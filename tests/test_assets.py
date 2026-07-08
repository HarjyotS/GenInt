import os

from infinienv.assets.resolver import resolve_assets, scene_asset_types
from infinienv.generation.templates import kitchen_delivery


def test_scene_asset_types_includes_agent_and_wall():
    scene = kitchen_delivery("kitchen task", seed=1)
    types = scene_asset_types(scene)
    assert "agent" in types
    assert "wall" in types
    assert "table" in types
    assert "can" in types
    assert "sink" in types


def test_resolve_assets_none_mode_returns_no_paths():
    scene = kitchen_delivery("kitchen task", seed=1)
    entries, notes = resolve_assets(scene, "none", "/tmp/unused")
    assert notes == []
    assert all(e.source == "none" and e.path is None for e in entries.values())


def test_resolve_assets_local_mode_uses_checked_in_placeholders(tmp_path):
    scene = kitchen_delivery("kitchen task", seed=1)
    entries, notes = resolve_assets(scene, "local", str(tmp_path))
    for t, entry in entries.items():
        assert entry.source == "local", (t, entry)
        assert os.path.exists(entry.path)
