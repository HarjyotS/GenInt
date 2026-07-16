"""Resolves each object type present in a scene to a sprite, per `--assets` mode.

Modes (matches PATHWAY.md section 8):
  none      -> no sprites; renderer keeps drawing flat colored cells.
  local     -> only the checked-in placeholders in assets/base/.
  generated -> only OpenAI-generated sprites (Images API); no silent fallback.
  auto      -> the SMART default: OpenAI-generate only the types that benefit from it (characters,
               creatures, novel/custom props) and draw the simple structural tiles locally
               (SIMPLE_LOCAL_TYPES), with a local-placeholder fallback if a generation fails. This is
               far faster than generating everything (fewer OpenAI calls, so it doesn't crawl through
               the 5-images/min rate limit) while still giving real art for the hero and creatures.
               `generated` still generates everything with no fallback.
"""

from __future__ import annotations

import difflib
import os

from infinienv.assets.manifest import AssetEntry
from infinienv.assets.placeholder_gen import base_assets_dir
from infinienv.llm.base import ProviderError
from infinienv.schema.scene_schema import SceneSpec

ASSET_MODES = ("none", "local", "generated", "auto")

# In `auto` mode, these structural/primitive types resolve to their checked-in local placeholder with
# NO image-generation call: a flat drawn tile is adequate for them, they're placed in nearly every
# cell, and they're exactly the types that otherwise burn the image API's 5-images/min rate limit.
# Generation is reserved for the types that actually benefit -- characters (agent) and novel/custom
# types (creatures, plants, props). `generated` mode still generates everything.
SIMPLE_LOCAL_TYPES = frozenset({"wall", "floor", "box", "door", "exit", "key", "hazard", "distractor"})


def scene_asset_types(scene: SceneSpec) -> list[str]:
    types = {"agent"}
    if scene.walls:
        types.add("wall")
    types.update(obj.type for obj in scene.objects)
    return sorted(types)


def variant_types(base: str, states: list[str]) -> list[str]:
    """Canonical type-string naming for one conceptual entity's animation/costume variants,
    e.g. `variant_types("hazard", ["idle", "active"]) -> ["hazard__idle", "hazard__active"]`.
    A variant type need not correspond to any placed `SceneObject` -- pass the result as
    `extra_types` to `resolve_assets()` to have each one generated/cached like any other type,
    even though `scene_asset_types()` itself would never discover it (it only scans placed
    objects). Centralizing the naming convention here means callers don't have to invent and
    consistently apply one themselves.
    """
    return [f"{base}__{state}" for state in states]


def variant_descriptions(base_description: str, base: str, states: list[str]) -> dict[str, str]:
    """One description per variant type (see `variant_types()`), each derived from a single
    shared `base_description` plus a state-specific qualifier, so all variants of one entity
    stay visually consistent instead of being described independently."""
    return {
        f"{base}__{state}": f"{base_description} -- specifically its {state.replace('_', ' ')} state"
        for state in states
    }


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

# Which generate_sprite() implementation actually runs "generated"/"auto" mode -- both share the
# exact same contract (object_type, cache_dir, *, model=, quality=, description=) -> path, so
# resolve_assets()/generate_many() don't need to know which one ran, only resolve_assets()'s
# AssetEntry.note records that for provenance. "openai" (the default) calls the real OpenAI
# Images API. "diffusion" runs a local on-device model instead -- no cloud call, no account rate
# limit, no external content-moderation gate -- and was briefly the default after two real OpenAI
# failure modes (a rate limit, then a moderation rejection of a character description; see
# notes.md). Reverted back to "openai" as the default after live-verified quality problems with
# the local model's character/hero sprites (a user-reported "this is shit" on the actual rendered
# output, not a hypothetical) -- see notes.md for the full account. "diffusion" remains fully
# available as an explicit opt-in (`INFINIENV_SPRITE_BACKEND=diffusion`) for scenes/types it
# handles well (textures, simple objects) or when OpenAI's rate limit/moderation is the blocker.
# Requires the optional `diffusion` extra (`pip install infinienv[diffusion]`) -- a missing extra
# fails with a clear ProviderError at generation time, the same as any other missing-dependency
# case in this project.
SPRITE_BACKENDS = ("openai", "diffusion")


def _select_sprite_generator():
    backend = os.environ.get("INFINIENV_SPRITE_BACKEND", "openai")
    if backend == "openai":
        from infinienv.assets.generator_openai import generate_sprite
    elif backend == "diffusion":
        from infinienv.assets.generator_diffusion import generate_sprite
    else:
        raise ValueError(f"unknown INFINIENV_SPRITE_BACKEND {backend!r}; expected one of {SPRITE_BACKENDS}")
    return backend, generate_sprite


def resolve_assets(
    scene: SceneSpec,
    mode: str,
    cache_dir: str,
    *,
    extra_types: list[str] | None = None,
    extra_descriptions: dict[str, str] | None = None,
) -> tuple[dict[str, AssetEntry], list[str]]:
    """Resolve a sprite per asset type needed by `scene`, per `--assets` mode.

    `extra_types`/`extra_descriptions` let a caller request sprites for types that don't
    correspond to any placed `SceneObject` -- e.g. animation/costume variants built with
    `variant_types()`/`variant_descriptions()` above -- merged into the same resolution pass
    (cache-hit check, concurrent generation, local/auto fallback) as every scene-derived type,
    with no separate code path. Both default to `None` and are fully backward compatible with
    every existing call site.
    """
    if mode not in ASSET_MODES:
        raise ValueError(f"unknown asset mode {mode!r}; expected one of {ASSET_MODES}")

    types = sorted(set(scene_asset_types(scene)) | set(extra_types or ()))
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

    # `auto`: draw the simple structural tiles locally (no API call), and only generate the types
    # that benefit from it (characters, creatures, novel props). Big speed win over generating all.
    if mode == "auto":
        still: list[str] = []
        for t in pending:
            local_path = os.path.join(local_dir, f"{t}.png")
            if t in SIMPLE_LOCAL_TYPES and os.path.exists(local_path):
                manifest[t] = AssetEntry(t, "local", local_path, note="auto: simple type drawn locally")
                _progress(f"{t}: simple tile, drawn locally")
            else:
                still.append(t)
        pending = still

    # Reuse a SIMILAR sprite we already have in the cache (e.g. a scene with "wolf" and "gray_wolf",
    # or a repair attempt reusing attempt 1's sprites) instead of generating a near-duplicate.
    pending = _reuse_similar_cached(pending, cache_dir, manifest, notes)
    # Dedup similar types WITHIN this batch: generate one representative per cluster, copy it to the
    # rest -- so a scene with several near-identical types makes one image call, not many.
    pending, reuse_map = _dedup_similar_pending(pending, notes)

    descriptions = {**_scene_descriptions(scene), **(extra_descriptions or {})}
    backend, generate_sprite_fn = _select_sprite_generator()
    if pending:
        _progress(f"generating {len(pending)} sprite(s) via {backend}: {', '.join(pending)}")
    generated_paths, generation_errors = _generate_many(pending, cache_dir, descriptions, generate_sprite_fn)

    def _fallback(t: str) -> None:
        exc = generation_errors.get(t, "generation unavailable")
        notes.append(f"image generation unavailable for {t!r}: {exc}")
        local_path = os.path.join(local_dir, f"{t}.png")
        if mode == "generated":
            manifest[t] = AssetEntry(t, "none", None, note="generation failed and fallback not requested")
        elif os.path.exists(local_path):
            manifest[t] = AssetEntry(t, "local", local_path, note="fallback: generated unavailable")
        else:
            manifest[t] = AssetEntry(t, "none", None, note="no asset available")

    for t in pending:
        if t in generated_paths:
            manifest[t] = AssetEntry(t, "generated", generated_paths[t], note=f"backend: {backend}")
            _progress(f"{t}: generated ({backend})")
        else:
            _progress(f"{t}: generation failed")
            _fallback(t)

    # Give each deduped type its representative's generated sprite (or fall back if the rep failed).
    import shutil

    for dup, rep in reuse_map.items():
        rep_path = generated_paths.get(rep)
        if rep_path and os.path.exists(rep_path):
            dst = os.path.join(cache_dir, f"{dup}.png")
            try:
                shutil.copyfile(rep_path, dst)
                manifest[dup] = AssetEntry(dup, "generated", dst, note=f"reused similar '{rep}'")
                _progress(f"{dup}: reused similar '{rep}'")
                continue
            except OSError:
                pass
        _fallback(dup)

    return manifest, notes


def _progress(msg: str) -> None:
    """Print an asset-resolution progress line. Inside a sandbox run this is captured as the
    command's output and shown live in the GUI feed, so a reviewer sees each sprite as it's
    generated/reused rather than a silent multi-minute pause."""
    print(f"[assets] {msg}", flush=True)


def _norm_type(s: str) -> tuple[str, frozenset]:
    import re

    text = re.sub(r"[_-]+", " ", s.lower()).strip()
    return text, frozenset(text.split())


def _similarity(a: str, b: str) -> float:
    """0..1 similarity between two sprite type names -- string ratio, boosted when one's word set is
    a subset of the other's (e.g. 'wolf' within 'gray wolf', 'acorn' within 'golden acorn')."""
    na, ta = _norm_type(a)
    nb, tb = _norm_type(b)
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    if ta and tb and (ta <= tb or tb <= ta):
        ratio = max(ratio, 0.85)
    return ratio


_SIMILARITY_THRESHOLD = 0.8


def _reuse_similar_cached(pending, cache_dir, manifest, notes):
    """For each pending type, if a similar sprite already exists in the cache, copy it and skip
    generation. Returns the still-pending types."""
    if not pending or not os.path.isdir(cache_dir):
        return pending
    import shutil

    available = {
        os.path.splitext(f)[0]: os.path.join(cache_dir, f)
        for f in os.listdir(cache_dir)
        if f.lower().endswith(".png")
    }
    if not available:
        return pending
    still = []
    for t in pending:
        best = max((c for c in available if c != t), key=lambda c: _similarity(t, c), default=None)
        if best is not None and _similarity(t, best) >= _SIMILARITY_THRESHOLD:
            dst = os.path.join(cache_dir, f"{t}.png")
            try:
                shutil.copyfile(available[best], dst)
            except OSError:
                still.append(t)
                continue
            manifest[t] = AssetEntry(t, "generated", dst, note=f"reused similar cached '{best}'")
            notes.append(f"reused similar cached sprite '{best}' for {t!r}")
            _progress(f"{t}: reused similar cached '{best}'")
            available[t] = dst
        else:
            still.append(t)
    return still


def _dedup_similar_pending(pending, notes):
    """Cluster near-identical pending types; keep one representative per cluster to generate and map
    the rest to it. Returns (representatives_to_generate, {dup_type: representative_type})."""
    reps: list[str] = []
    reuse_map: dict[str, str] = {}
    for t in pending:
        match = next((r for r in reps if _similarity(t, r) >= _SIMILARITY_THRESHOLD), None)
        if match is None:
            reps.append(t)
        else:
            reuse_map[t] = match
            notes.append(f"'{t}' will reuse the generated sprite for similar type '{match}'")
    return reps, reuse_map


def _generate_many(
    types: list[str],
    cache_dir: str,
    descriptions: dict[str, str] | None = None,
    generate_sprite_fn=None,
) -> tuple[dict[str, str], dict[str, ProviderError]]:
    """Generate sprites for every type in `types` concurrently, via `generate_sprite_fn`
    (defaults to whatever `_select_sprite_generator()` currently resolves to if not given
    explicitly). Returns (type -> path for successes, type -> the raised ProviderError for
    failures) -- never raises itself, so one type's generation failure doesn't take down the
    others already in flight."""
    if not types:
        return {}, {}

    from concurrent.futures import ThreadPoolExecutor

    if generate_sprite_fn is None:
        _backend, generate_sprite_fn = _select_sprite_generator()

    descriptions = descriptions or {}
    max_workers = min(len(types), int(os.environ.get("INFINIENV_ASSET_CONCURRENCY", str(DEFAULT_ASSET_CONCURRENCY))))
    paths: dict[str, str] = {}
    errors: dict[str, ProviderError] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_type = {
            pool.submit(generate_sprite_fn, t, cache_dir, description=descriptions.get(t)): t for t in types
        }
        for future in future_to_type:
            t = future_to_type[future]
            try:
                paths[t] = future.result()
            except ProviderError as exc:
                errors[t] = exc
    return paths, errors
