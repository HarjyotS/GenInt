"""Resolves each object type present in a scene to a sprite, per `--assets` mode.

Modes (matches PATHWAY.md section 8):
  none      -> no sprites; renderer keeps drawing flat colored cells.
  local     -> only the checked-in placeholders in assets/base/.
  generated -> only OpenAI-generated sprites (Images API); no silent fallback.
  auto      -> generated if available, else local placeholder, with a note either way.
"""

from __future__ import annotations

import os

from infinienv.assets.manifest import AssetEntry
from infinienv.assets.placeholder_gen import base_assets_dir
from infinienv.llm.base import ProviderError
from infinienv.schema.scene_schema import SceneSpec

ASSET_MODES = ("none", "local", "generated", "auto")


def scene_asset_types(scene: SceneSpec) -> list[str]:
    types = {"agent"}
    if scene.walls:
        types.add("wall")
    types.update(obj.type for obj in scene.objects)
    return sorted(types)


_AGENT_DESCRIPTION_MAX_CHARS = 220


def _scene_descriptions(scene: SceneSpec) -> dict[str, str]:
    """A best-effort {asset_type: description} map sourced from the scene itself, so sprite
    generation asks for what THIS scene actually needs instead of a generic, possibly-wrong
    default. Two sources:

    - `mechanics.custom_object_types`: the model already writes a description for every custom
      type it declares (e.g. "a green-clothed Italian rescuer", "a smooth-moving turtle hazard")
      -- reuse it verbatim instead of falling back to the bare type name.
    - The player character (asset key "agent", not itself a declared object type -- it's the
      top-level `SceneSpec.agent`, not a `SceneObject`): `OBJECT_DESCRIPTIONS["agent"]` is a
      generic "a small friendly robot character" that's wrong for most scenes with a specific
      protagonist (an Italian plumber-style rescuer, a knight, etc.). The scene's own `prompt`
      almost always describes the intended player character better than any generic default, so
      use it -- this was a real, user-reported quality gap: every sandbox run's hand-drawn or
      generated hero looked generic/wrong because nothing ever told the sprite generator what
      the scene actually wanted the player to look like.
    """
    descriptions: dict[str, str] = {
        t.id: t.description for t in scene.mechanics.custom_object_types if t.description
    }
    prompt = (scene.metadata.prompt or "").strip()
    if prompt:
        descriptions.setdefault(
            "agent",
            f"the player character described here, drawn as a single clear character sprite "
            f"(not a scene or other objects): {prompt[:_AGENT_DESCRIPTION_MAX_CHARS]}",
        )
    return descriptions


# Sprite generation calls are independent, I/O-bound (network) requests to the OpenAI Images
# API -- running them one at a time serializes their full latency (N types == N x per-image
# latency). A small bounded thread pool overlaps them instead, so wall-clock time is closer to
# the single slowest call. Bounded (not "one thread per type") to stay polite to API rate limits
# on scenes with many novel object types. Overridable via INFINIENV_ASSET_CONCURRENCY.
DEFAULT_ASSET_CONCURRENCY = 4


def resolve_assets(scene: SceneSpec, mode: str, cache_dir: str) -> tuple[dict[str, AssetEntry], list[str]]:
    if mode not in ASSET_MODES:
        raise ValueError(f"unknown asset mode {mode!r}; expected one of {ASSET_MODES}")

    types = scene_asset_types(scene)
    manifest: dict[str, AssetEntry] = {}
    notes: list[str] = []

    if mode == "none":
        for t in types:
            manifest[t] = AssetEntry(t, "none", None)
        return manifest, notes

    local_dir = base_assets_dir()

    if mode == "local":
        for t in types:
            local_path = os.path.join(local_dir, f"{t}.png")
            manifest[t] = (
                AssetEntry(t, "local", local_path)
                if os.path.exists(local_path)
                else AssetEntry(t, "none", None, note="no local placeholder for this type")
            )
        return manifest, notes

    # mode in ("generated", "auto") -- resolve cache hits synchronously (cheap, local
    # filesystem check), then generate every remaining type concurrently.
    pending: list[str] = []
    for t in types:
        cached_path = os.path.join(cache_dir, f"{t}.png")
        if os.path.exists(cached_path):
            manifest[t] = AssetEntry(t, "generated", cached_path, note="cache hit")
        else:
            pending.append(t)

    generated_paths, generation_errors = _generate_many(pending, cache_dir, _scene_descriptions(scene))

    for t in pending:
        if t in generated_paths:
            manifest[t] = AssetEntry(t, "generated", generated_paths[t])
            continue
        exc = generation_errors[t]
        notes.append(f"image generation unavailable for {t!r}: {exc}")
        local_path = os.path.join(local_dir, f"{t}.png")
        has_local = os.path.exists(local_path)
        if mode == "generated":
            manifest[t] = AssetEntry(t, "none", None, note="generation failed and fallback not requested")
        elif has_local:
            manifest[t] = AssetEntry(t, "local", local_path, note="fallback: generated unavailable")
        else:
            manifest[t] = AssetEntry(t, "none", None, note="no asset available")

    return manifest, notes


def _generate_many(
    types: list[str], cache_dir: str, descriptions: dict[str, str] | None = None
) -> tuple[dict[str, str], dict[str, ProviderError]]:
    """Generate sprites for every type in `types` concurrently. Returns (type -> path for
    successes, type -> the raised ProviderError for failures) -- never raises itself, so one
    type's generation failure doesn't take down the others already in flight."""
    if not types:
        return {}, {}

    from concurrent.futures import ThreadPoolExecutor

    from infinienv.assets.generator_openai import generate_sprite

    descriptions = descriptions or {}
    max_workers = min(len(types), int(os.environ.get("INFINIENV_ASSET_CONCURRENCY", str(DEFAULT_ASSET_CONCURRENCY))))
    paths: dict[str, str] = {}
    errors: dict[str, ProviderError] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_type = {
            pool.submit(generate_sprite, t, cache_dir, description=descriptions.get(t)): t for t in types
        }
        for future in future_to_type:
            t = future_to_type[future]
            try:
                paths[t] = future.result()
            except ProviderError as exc:
                errors[t] = exc
    return paths, errors
