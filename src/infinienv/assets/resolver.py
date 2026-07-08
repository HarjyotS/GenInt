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

    for t in types:
        local_path = os.path.join(local_dir, f"{t}.png")
        has_local = os.path.exists(local_path)

        if mode == "local":
            manifest[t] = (
                AssetEntry(t, "local", local_path)
                if has_local
                else AssetEntry(t, "none", None, note="no local placeholder for this type")
            )
            continue

        # mode in ("generated", "auto")
        cached_path = os.path.join(cache_dir, f"{t}.png")
        if os.path.exists(cached_path):
            manifest[t] = AssetEntry(t, "generated", cached_path, note="cache hit")
            continue

        try:
            from infinienv.assets.generator_openai import generate_sprite

            path = generate_sprite(t, cache_dir)
            manifest[t] = AssetEntry(t, "generated", path)
            continue
        except ProviderError as exc:
            notes.append(f"image generation unavailable for {t!r}: {exc}")

        if mode == "generated":
            manifest[t] = AssetEntry(t, "none", None, note="generation failed and fallback not requested")
        elif has_local:
            manifest[t] = AssetEntry(t, "local", local_path, note="fallback: generated unavailable")
        else:
            manifest[t] = AssetEntry(t, "none", None, note="no asset available")

    return manifest, notes
