"""Persists user/LLM-defined custom object types and interactions for reuse.

Mirrors the asset cache's "generate once, reuse forever" pattern (assets/resolver.py):
once a scene defines a "window" object type and a "throw_through_window" interaction,
future ScenePlannerAgent runs can call get_known_mechanics() and reuse that exact
definition instead of reinventing a slightly different one each time.
"""

from __future__ import annotations

import json
import os

from infinienv.schema.scene_schema import SceneSpec

DEFAULT_CACHE_PATH = os.path.join(os.getcwd(), ".infinienv_mechanics_cache.json")


def _load_raw(path: str) -> dict:
    if not os.path.exists(path):
        return {"object_types": {}, "interactions": {}}
    with open(path) as f:
        return json.load(f)


def load_mechanics_library(path: str = DEFAULT_CACHE_PATH) -> dict:
    """Returns {"object_types": [...], "interactions": [...]}, ready to hand to the
    model (e.g. via a get_known_mechanics tool)."""
    raw = _load_raw(path)
    return {
        "object_types": list(raw.get("object_types", {}).values()),
        "interactions": list(raw.get("interactions", {}).values()),
    }


def remember_scene_mechanics(scene: SceneSpec, path: str = DEFAULT_CACHE_PATH) -> int:
    """Add any new custom object types/interactions from `scene` into the shared cache.

    Existing entries are never overwritten (first definition wins) so a cached
    mechanic's behavior stays stable once established. Returns how many new entries
    were added.
    """
    if not scene.mechanics.custom_object_types and not scene.mechanics.custom_interactions:
        return 0

    raw = _load_raw(path)
    object_types = raw.setdefault("object_types", {})
    interactions = raw.setdefault("interactions", {})
    added = 0

    for t in scene.mechanics.custom_object_types:
        if t.id not in object_types:
            object_types[t.id] = t.model_dump()
            added += 1
    for i in scene.mechanics.custom_interactions:
        if i.id not in interactions:
            interactions[i.id] = i.model_dump()
            added += 1

    if added:
        out_dir = os.path.dirname(path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump(raw, f, indent=2)
    return added
