from infinienv.generation.mechanics_cache import load_mechanics_library, remember_scene_mechanics
from infinienv.schema.scene_schema import scene_spec_from_dict


def _scene_with_mechanics() -> dict:
    return {
        "version": "0.1",
        "seed": 1,
        "metadata": {"name": "t", "prompt": "p"},
        "grid": {"width": 8, "height": 8, "tile_size": 32},
        "agent": {"id": "agent", "x": 1, "y": 1},
        "objects": [
            {"id": "vase_1", "type": "vase", "x": 2, "y": 2, "portable": True},
            {"id": "window_1", "type": "window", "x": 5, "y": 5, "solid": False},
        ],
        "walls": [],
        "mechanics": {
            "custom_object_types": [{"id": "vase"}, {"id": "window"}],
            "custom_interactions": [
                {
                    "id": "throw_through_window",
                    "trigger_action": "throw",
                    "target_type": "window",
                    "must_hold_type": "vase",
                    "effects": [{"op": "remove_held_object", "target": "held"}],
                }
            ],
        },
        "goals": [
            {"id": "declutter", "type": "interact", "interaction_id": "throw_through_window", "target_id": "window_1"}
        ],
    }


def test_remember_and_reload_mechanics(tmp_path):
    cache_path = str(tmp_path / "mechanics.json")
    scene = scene_spec_from_dict(_scene_with_mechanics())

    added = remember_scene_mechanics(scene, cache_path)
    assert added == 3  # 2 object types + 1 interaction

    library = load_mechanics_library(cache_path)
    assert {t["id"] for t in library["object_types"]} == {"vase", "window"}
    assert {i["id"] for i in library["interactions"]} == {"throw_through_window"}


def test_remember_does_not_duplicate_or_overwrite(tmp_path):
    cache_path = str(tmp_path / "mechanics.json")
    scene = scene_spec_from_dict(_scene_with_mechanics())

    first = remember_scene_mechanics(scene, cache_path)
    second = remember_scene_mechanics(scene, cache_path)  # same scene again
    assert first == 3
    assert second == 0  # nothing new; already cached

    library = load_mechanics_library(cache_path)
    assert len(library["object_types"]) == 2
    assert len(library["interactions"]) == 1


def test_empty_mechanics_library_when_no_cache_file(tmp_path):
    library = load_mechanics_library(str(tmp_path / "does_not_exist.json"))
    assert library == {"object_types": [], "interactions": []}
