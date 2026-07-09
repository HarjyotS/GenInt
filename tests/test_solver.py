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


def test_trace_records_incremental_state_not_final_state_repeated():
    # Regression test: trace entries used to be built *after* plan_goal had already
    # mutated `state` all the way to the goal's end, so every entry past t=0 showed
    # the same final position/inventory instead of the real step-by-step values.
    scene = scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": 6, "height": 6, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [{"id": "can_1", "type": "can", "x": 3, "y": 0, "portable": True}],
            "walls": [],
            "goals": [{"id": "pick", "type": "pickup", "object_id": "can_1"}],
        }
    )
    result = solve_scene(scene)
    assert result.success, result.error
    positions = [tuple(t["position"]) for t in result.trace if "position" in t]
    # t=0 start, then 3 rightward moves, then pick_up: positions must actually
    # progress, not repeat the final one for every step.
    assert positions == [(0, 0), (1, 0), (2, 0), (3, 0), (3, 0)]
    inventories = [t["inventory"] for t in result.trace if "inventory" in t]
    assert inventories == [[], [], [], ["can_1"]]


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


def test_push_goal_solved_and_reported_in_goal_results():
    scene = scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "push", "prompt": "shove the crate onto the plate"},
            "grid": {"width": 8, "height": 8, "tile_size": 32},
            "agent": {"id": "agent", "x": 1, "y": 3},
            "objects": [
                {"id": "crate_1", "type": "box", "x": 3, "y": 3, "solid": True, "pushable": True},
                {"id": "plate_1", "type": "sink", "x": 5, "y": 3, "solid": False},
            ],
            "walls": [],
            "goals": [{"id": "push", "type": "push", "object_id": "crate_1", "target_id": "plate_1"}],
        }
    )
    result = solve_scene(scene)
    assert result.success, result.error
    crate = result.final_state.objects["crate_1"]
    assert (crate.x, crate.y) == (5, 3)
    assert result.goal_results == [{"id": "push", "type": "push", "success": True}]


def test_slippery_push_to_a_mid_floor_target_is_unsolvable():
    # a slippery object can only come to rest against an obstacle, so a mid-floor target
    # for it is genuinely impossible -- the deterministic solver must report that, not hang.
    scene = scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "push", "prompt": "p"},
            "grid": {"width": 10, "height": 8, "tile_size": 32},
            "agent": {"id": "agent", "x": 1, "y": 3},
            "objects": [
                {"id": "puck_1", "type": "box", "x": 3, "y": 3, "solid": True, "pushable": True, "slippery": True},
                {"id": "plate_1", "type": "sink", "x": 5, "y": 3, "solid": False},
            ],
            "walls": [{"x": 9, "y": y} for y in range(8)],
            "goals": [{"id": "push", "type": "push", "object_id": "puck_1", "target_id": "plate_1"}],
        }
    )
    result = solve_scene(scene)
    assert not result.success
    assert result.error
