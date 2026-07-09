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


def test_undeclared_custom_object_type_fails():
    data = _base_scene()
    data["objects"][0]["type"] = "desk"  # not built-in, not declared in mechanics
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "UNSUPPORTED_OBJECT_TYPE" for e in result.errors)


def test_custom_object_type_colliding_with_builtin_fails():
    data = _base_scene()
    data["mechanics"] = {"custom_object_types": [{"id": "table"}], "custom_interactions": []}
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "MECHANICS_TYPE_COLLISION" for e in result.errors)


def test_interaction_trigger_action_colliding_with_builtin_fails():
    data = _base_scene()
    data["mechanics"] = {
        "custom_object_types": [{"id": "window"}],
        "custom_interactions": [
            {"id": "i1", "trigger_action": "pick_up", "target_type": "window", "effects": [{"op": "remove_object"}]}
        ],
    }
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "MECHANICS_ACTION_COLLISION" for e in result.errors)


def test_interaction_unknown_target_type_fails():
    data = _base_scene()
    data["mechanics"] = {
        "custom_object_types": [],
        "custom_interactions": [
            {"id": "i1", "trigger_action": "throw", "target_type": "window", "effects": [{"op": "remove_object"}]}
        ],
    }
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "MECHANICS_UNKNOWN_TYPE" for e in result.errors)


def test_interact_goal_referencing_undeclared_interaction_fails():
    data = _base_scene()
    data["goals"] = [{"id": "g1", "type": "interact", "interaction_id": "nonexistent", "target_id": "sink_1"}]
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert not result.valid
    assert any(e.code == "MECHANICS_UNKNOWN_INTERACTION" for e in result.errors)


def test_throw_object_through_window_end_to_end():
    """The user's own example: a window you can throw a held object out of."""
    data = _base_scene()
    data["objects"] = [
        {"id": "vase_1", "type": "vase", "x": 2, "y": 2, "portable": True},
        {"id": "window_1", "type": "window", "x": 5, "y": 5, "solid": False},
    ]
    data["mechanics"] = {
        "custom_object_types": [{"id": "vase", "description": "a fragile vase"}, {"id": "window"}],
        "custom_interactions": [
            {
                "id": "throw_through_window",
                "trigger_action": "throw",
                "target_type": "window",
                "must_hold_type": "vase",
                "effects": [{"op": "remove_held_object", "target": "held"}],
                "description": "Throws the held vase out the window.",
            }
        ],
    }
    data["goals"] = [
        {"id": "declutter", "type": "interact", "interaction_id": "throw_through_window", "target_id": "window_1"}
    ]
    scene = scene_spec_from_dict(data)
    result = validate_scene(scene)
    assert result.valid, result.errors


def _physics_scene(objects, goals, w=8, h=8, agent=(1, 3)):
    cells = set()
    for i in range(h):
        cells.add((0, i))
        cells.add((w - 1, i))
    for i in range(w):
        cells.add((i, 0))
        cells.add((i, h - 1))
    return {
        "version": "0.1",
        "seed": 1,
        "metadata": {"name": "t", "prompt": "p"},
        "grid": {"width": w, "height": h, "tile_size": 32},
        "agent": {"id": "agent", "x": agent[0], "y": agent[1]},
        "objects": objects,
        "walls": [{"x": x, "y": y} for x, y in sorted(cells)],
        "goals": goals,
    }


def test_valid_push_scene_passes():
    from infinienv.validation.validator import validate_scene_dict

    r = validate_scene_dict(
        _physics_scene(
            [
                {"id": "crate", "type": "box", "x": 3, "y": 3, "solid": True, "pushable": True},
                {"id": "plate", "type": "sink", "x": 5, "y": 3},
            ],
            [{"id": "g", "type": "push", "object_id": "crate", "target_id": "plate"}],
        )
    )
    assert r.valid, [e.code for e in r.errors]


def test_push_goal_on_non_pushable_object_is_rejected():
    from infinienv.validation.validator import validate_scene_dict

    r = validate_scene_dict(
        _physics_scene(
            [
                {"id": "crate", "type": "box", "x": 3, "y": 3, "solid": True},
                {"id": "plate", "type": "sink", "x": 5, "y": 3},
            ],
            [{"id": "g", "type": "push", "object_id": "crate", "target_id": "plate"}],
        )
    )
    assert not r.valid
    assert "PHYSICS_NOT_PUSHABLE" in [e.code for e in r.errors]


def test_push_goal_missing_target_reports_missing_goal_object():
    from infinienv.validation.validator import validate_scene_dict

    r = validate_scene_dict(
        _physics_scene(
            [{"id": "crate", "type": "box", "x": 3, "y": 3, "solid": True, "pushable": True}],
            [{"id": "g", "type": "push", "object_id": "crate", "target_id": "nope"}],
        )
    )
    assert not r.valid
    assert "MISSING_GOAL_OBJECT" in [e.code for e in r.errors]


def test_pushable_object_does_not_falsely_trip_unreachable_precheck():
    # a pushable crate walling off the only corridor to the plate must NOT be treated as a
    # permanent obstacle by the reachability pre-check -- it can be shoved aside.
    from infinienv.validation.validator import validate_scene_dict

    scene = _physics_scene(
        [
            {"id": "crate", "type": "box", "x": 4, "y": 3, "solid": True, "pushable": True},
            {"id": "plate", "type": "sink", "x": 6, "y": 3},
        ],
        [{"id": "g", "type": "push", "object_id": "crate", "target_id": "plate"}],
        w=8,
    )
    r = validate_scene_dict(scene)
    assert "UNREACHABLE_OBJECT" not in [e.code for e in r.errors]
