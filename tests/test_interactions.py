import pytest

from infinienv.engine.actions import ActionError, apply_action
from infinienv.engine.grid import Grid
from infinienv.engine.interactions import apply_custom_interaction
from infinienv.engine.state import GameState
from infinienv.navigation.policy import solve_scene
from infinienv.schema.scene_schema import scene_spec_from_dict


def _throw_scene(agent_pos=(0, 0), vase_pos=(2, 2), window_pos=(5, 5)) -> dict:
    return {
        "version": "0.1",
        "seed": 1,
        "metadata": {"name": "throw_test", "prompt": "throw a vase out a window"},
        "grid": {"width": 8, "height": 8, "tile_size": 32},
        "agent": {"id": "agent", "x": agent_pos[0], "y": agent_pos[1]},
        "objects": [
            {"id": "vase_1", "type": "vase", "x": vase_pos[0], "y": vase_pos[1], "portable": True},
            {"id": "window_1", "type": "window", "x": window_pos[0], "y": window_pos[1], "solid": False},
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


def test_solve_scene_throws_vase_out_window():
    scene = scene_spec_from_dict(_throw_scene())
    result = solve_scene(scene)
    assert result.success, result.error
    assert result.goal_results == [{"id": "declutter", "type": "interact", "success": True}]
    # the vase should be gone from the world entirely, not just moved
    assert "vase_1" not in result.final_state.objects
    assert "vase_1" not in result.final_state.inventory


def test_throw_without_holding_vase_raises():
    scene = scene_spec_from_dict(_throw_scene())
    grid = Grid(scene)
    state = GameState.from_scene(scene)
    state.agent_x, state.agent_y = scene.objects[1].x, scene.objects[1].y  # stand at the window, empty-handed
    with pytest.raises(ActionError):
        apply_custom_interaction(state, scene, {"action": "throw", "target_id": "window_1"})


def test_apply_action_routes_unknown_verb_to_custom_interaction():
    scene = scene_spec_from_dict(_throw_scene())
    grid = Grid(scene)
    state = GameState.from_scene(scene)
    state.agent_x, state.agent_y = scene.objects[0].x, scene.objects[0].y  # stand at the vase
    apply_action(state, grid, {"action": "pick_up", "object_id": "vase_1"}, scene)
    state.agent_x, state.agent_y = scene.objects[1].x, scene.objects[1].y  # walk to the window
    apply_action(state, grid, {"action": "throw", "target_id": "window_1"}, scene)
    assert "vase_1" not in state.objects
    assert ("throw_through_window", "window_1") in state.completed_interactions


def test_apply_action_without_scene_rejects_unknown_verb():
    scene = scene_spec_from_dict(_throw_scene())
    grid = Grid(scene)
    state = GameState.from_scene(scene)
    with pytest.raises(ActionError):
        apply_action(state, grid, {"action": "throw", "target_id": "window_1"})  # no scene passed
