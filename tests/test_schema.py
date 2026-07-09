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


def test_arbitrary_object_type_parses_at_schema_level():
    # `type` is a free string at the schema layer (not a Literal enum): a custom/LLM-declared
    # type like "window" must parse. Whether it's *allowed* (built-in or declared in
    # mechanics.custom_object_types) is validator.py's job -- see test_validator.py.
    data = _base_scene()
    data["objects"][0]["type"] = "window"
    scene = scene_spec_from_dict(data)
    assert scene.objects[0].type == "window"


def test_interact_goal_and_mechanics_parse():
    data = _base_scene()
    data["mechanics"] = {
        "custom_object_types": [{"id": "window", "description": "a window"}],
        "custom_interactions": [
            {
                "id": "throw_through_window",
                "trigger_action": "throw",
                "target_type": "window",
                "must_hold_type": "can",
                "effects": [{"op": "remove_held_object", "target": "held"}],
            }
        ],
    }
    data["goals"] = [{"id": "throw_it", "type": "interact", "interaction_id": "throw_through_window", "target_id": "sink_1"}]
    scene = scene_spec_from_dict(data)
    assert scene.mechanics.custom_object_types[0].id == "window"
    assert scene.mechanics.custom_interactions[0].effects[0].op == "remove_held_object"
    assert scene.goals[0].type == "interact"


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


def test_pushable_and_slippery_flags_and_push_goal_parse():
    from infinienv.schema.scene_schema import GOAL_TYPES, scene_spec_from_dict

    assert "push" in GOAL_TYPES
    scene = scene_spec_from_dict(
        {
            "metadata": {"name": "t"},
            "grid": {"width": 8, "height": 8},
            "agent": {"x": 1, "y": 1},
            "objects": [{"id": "crate", "type": "box", "x": 3, "y": 1, "solid": True, "pushable": True, "slippery": True}],
            "walls": [],
            "goals": [{"id": "g", "type": "push", "object_id": "crate", "target_id": "plate"}],
        }
    )
    assert scene.objects[0].pushable is True
    assert scene.objects[0].slippery is True
    assert scene.objects[0].__class__.__name__ == "SceneObject"
    goal = scene.goals[0]
    assert goal.type == "push"
    assert goal.object_id == "crate" and goal.target_id == "plate"


def test_object_flags_default_to_false():
    from infinienv.schema.scene_schema import scene_spec_from_dict

    scene = scene_spec_from_dict(
        {
            "metadata": {"name": "t"},
            "grid": {"width": 8, "height": 8},
            "agent": {"x": 1, "y": 1},
            "objects": [{"id": "can", "type": "can", "x": 3, "y": 1}],
            "walls": [],
            "goals": [{"id": "g", "type": "reach", "target_id": "can"}],
        }
    )
    assert scene.objects[0].pushable is False
    assert scene.objects[0].slippery is False
