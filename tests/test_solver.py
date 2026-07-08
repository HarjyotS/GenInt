from infinienv.navigation.policy import solve_scene
from infinienv.schema.scene_schema import scene_spec_from_dict


def test_pickup_task_succeeds():
    scene = scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": 6, "height": 6, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [{"id": "can_1", "type": "can", "x": 3, "y": 3, "portable": True}],
            "walls": [],
            "goals": [{"id": "pick", "type": "pickup", "object_id": "can_1"}],
        }
    )
    result = solve_scene(scene)
    assert result.success, result.error
    assert "can_1" in result.final_state.inventory


def test_deliver_task_succeeds():
    scene = scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": 8, "height": 8, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [
                {"id": "can_1", "type": "can", "x": 2, "y": 2, "portable": True},
                {"id": "sink_1", "type": "sink", "x": 6, "y": 6, "solid": False},
            ],
            "walls": [],
            "goals": [{"id": "deliver", "type": "deliver", "object_id": "can_1", "target_id": "sink_1"}],
        }
    )
    result = solve_scene(scene)
    assert result.success, result.error


def test_locked_door_task_succeeds():
    scene = scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": 8, "height": 8, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [
                {"id": "key_1", "type": "key", "x": 1, "y": 1, "portable": True},
                {"id": "door_1", "type": "door", "x": 4, "y": 0, "solid": True, "locked": True, "key_id": "key_1"},
                {"id": "package_1", "type": "package", "x": 6, "y": 0, "portable": True},
                {"id": "exit_1", "type": "exit", "x": 7, "y": 0, "solid": False},
            ],
            "walls": [{"x": 4, "y": y} for y in range(1, 8)],
            "goals": [
                {"id": "unlock", "type": "unlock", "door_id": "door_1"},
                {"id": "deliver", "type": "deliver", "object_id": "package_1", "target_id": "exit_1"},
            ],
        }
    )
    result = solve_scene(scene)
    assert result.success, result.error
    assert "door_1" in result.final_state.unlocked_doors


def test_impossible_task_fails_cleanly():
    scene = scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": 8, "height": 8, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [{"id": "can_1", "type": "can", "x": 6, "y": 6, "portable": True}],
            "walls": [{"x": 3, "y": y} for y in range(8)],
            "goals": [{"id": "pick", "type": "pickup", "object_id": "can_1"}],
        }
    )
    result = solve_scene(scene)
    assert not result.success
    assert result.error
