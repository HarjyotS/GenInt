from pydantic import ValidationError as PydanticValidationError
import pytest

from infinienv.schema.scene_schema import scene_spec_from_dict


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


def test_valid_scene_parses():
    scene = scene_spec_from_dict(_base_scene())
    assert scene.metadata.name == "t"
    assert scene.grid.width == 8
    assert len(scene.objects) == 2
    assert scene.goals[0].type == "deliver"


def test_missing_required_field_fails():
    data = _base_scene()
    del data["agent"]
    with pytest.raises(PydanticValidationError):
        scene_spec_from_dict(data)


def test_duplicate_ids_allowed_at_schema_level_but_caught_by_validator():
    # Pydantic doesn't enforce uniqueness across the object list; that's validator.py's job.
    data = _base_scene()
    data["objects"].append({"id": "can_1", "type": "box", "x": 3, "y": 3, "solid": True})
    scene = scene_spec_from_dict(data)
    assert sum(1 for o in scene.objects if o.id == "can_1") == 2


def test_unsupported_object_type_fails():
    data = _base_scene()
    data["objects"][0]["type"] = "spaceship"
    with pytest.raises(PydanticValidationError):
        scene_spec_from_dict(data)


def test_object_type_is_an_enum_in_the_json_schema():
    # This is what makes structured-output generation refuse unsupported types (e.g. "desk",
    # "sofa") at sampling time, instead of only rejecting them after the fact.
    from infinienv.schema.scene_schema import OBJECT_TYPE_VALUES, scene_spec_json_schema

    schema = scene_spec_json_schema()
    object_type_schema = schema["$defs"]["SceneObject"]["properties"]["type"]
    assert set(object_type_schema["enum"]) == set(OBJECT_TYPE_VALUES)


def test_sequence_goal_nests():
    data = _base_scene()
    data["goals"] = [
        {
            "id": "seq",
            "type": "sequence",
            "subgoals": [
                {"id": "pick", "type": "pickup", "object_id": "can_1"},
                {"id": "deliver", "type": "deliver", "object_id": "can_1", "target_id": "sink_1"},
            ],
        }
    ]
    scene = scene_spec_from_dict(data)
    assert scene.goals[0].type == "sequence"
    assert len(scene.goals[0].subgoals) == 2
