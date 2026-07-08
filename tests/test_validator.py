from infinienv.schema.scene_schema import scene_spec_from_dict
from infinienv.validation.validator import validate_scene


def _base_scene() -> dict:
    return {
        "version": "0.1",
        "seed": 1,
        "metadata": {"name": "t", "prompt": "p", "theme": "kitchen"},
        "grid": {"width": 8, "height": 8, "tile_size": 32},
        "agent": {"id": "agent", "x": 1, "y": 1, "inventory": []},
        "objects": [
            {"id": "can_1", "type": "can", "x": 2, "y": 2, "portable": True},
            {"id": "sink_1", "type": "sink", "x": 5, "y": 5, "solid": False},
        ],
        "walls": [],
        "goals": [{"id": "deliver", "type": "deliver", "object_id": "can_1", "target_id": "sink_1"}],
    }


def test_valid_scene_passes():
    scene = scene_spec_from_dict(_base_scene())
    result = validate_scene(scene)
    assert result.valid, result.errors


def test_out_of_bounds_object_fails():
    data = _base_scene()
    data["objects"][0]["x"] = 100
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "OUT_OF_BOUNDS" for e in result.errors)


def test_overlapping_solid_objects_fail():
    data = _base_scene()
    data["objects"][0]["solid"] = True
    data["objects"].append({"id": "box_1", "type": "box", "x": 2, "y": 2, "solid": True})
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "ILLEGAL_OVERLAP" for e in result.errors)


def test_missing_goal_target_fails():
    data = _base_scene()
    data["goals"] = [{"id": "deliver", "type": "deliver", "object_id": "can_1", "target_id": "nonexistent"}]
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "MISSING_GOAL_OBJECT" for e in result.errors)


def test_duplicate_id_fails():
    data = _base_scene()
    data["objects"].append({"id": "can_1", "type": "box", "x": 3, "y": 3, "solid": True})
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "DUPLICATE_ID" for e in result.errors)


def test_no_goals_fails():
    data = _base_scene()
    data["goals"] = []
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "NO_GOALS" for e in result.errors)
