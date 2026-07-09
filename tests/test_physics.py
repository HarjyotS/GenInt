"""Deterministic grid-physics: pushable objects and sliding (engine/physics.py + apply_action)."""

import pytest

from infinienv.engine.actions import ActionError, apply_action
from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState
from infinienv.engine.physics import cell_blocked, pushable_at, try_push
from infinienv.schema.scene_schema import scene_spec_from_dict


def _scene(objects, *, w=8, h=8, agent=(1, 3)):
    return scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": w, "height": h, "tile_size": 32},
            "agent": {"id": "agent", "x": agent[0], "y": agent[1]},
            "objects": objects,
            "walls": [{"x": w - 1, "y": y} for y in range(h)],  # a right wall to slide into
            "goals": [{"id": "g", "type": "reach", "target_id": "agent"}],
        }
    )


def _build(objects, **kw):
    scene = _scene(objects, **kw)
    return scene, Grid(scene), GameState.from_scene(scene)


def _pos(state, oid):
    return (state.objects[oid].x, state.objects[oid].y)


def test_moving_into_a_pushable_object_shoves_it_one_cell():
    scene, grid, state = _build([{"id": "crate", "type": "box", "x": 2, "y": 3, "solid": True, "pushable": True}])
    apply_action(state, grid, {"action": "move_right"}, scene)
    assert state.agent_pos() == (2, 3)
    assert _pos(state, "crate") == (3, 3)


def test_slippery_object_slides_until_it_hits_the_wall():
    scene, grid, state = _build(
        [{"id": "puck", "type": "box", "x": 2, "y": 3, "solid": True, "pushable": True, "slippery": True}]
    )
    apply_action(state, grid, {"action": "move_right"}, scene)
    # right wall is at x=7, so the puck stops at x=6
    assert _pos(state, "puck") == (6, 3)
    assert state.agent_pos() == (2, 3)


def test_push_blocked_by_a_solid_object_raises():
    scene, grid, state = _build(
        [
            {"id": "crate", "type": "box", "x": 2, "y": 3, "solid": True, "pushable": True},
            {"id": "rock", "type": "box", "x": 3, "y": 3, "solid": True},
        ]
    )
    with pytest.raises(ActionError, match="push blocked"):
        apply_action(state, grid, {"action": "move_right"}, scene)
    # nothing moved
    assert _pos(state, "crate") == (2, 3)
    assert state.agent_pos() == (1, 3)


def test_push_blocked_by_the_grid_boundary_raises():
    # crate flush against the right wall (x=6, wall at x=7) can't be pushed further right
    scene, grid, state = _build(
        [{"id": "crate", "type": "box", "x": 6, "y": 3, "solid": True, "pushable": True}], agent=(5, 3)
    )
    with pytest.raises(ActionError, match="push blocked"):
        apply_action(state, grid, {"action": "move_right"}, scene)


def test_agent_walks_through_a_cell_a_pushable_has_vacated():
    # the static Grid still records the crate's original cell as solid; live collision must not.
    scene, grid, state = _build([{"id": "crate", "type": "box", "x": 2, "y": 3, "solid": True, "pushable": True}])
    apply_action(state, grid, {"action": "move_right"}, scene)  # crate -> (3,3), agent -> (2,3)
    apply_action(state, grid, {"action": "move_right"}, scene)  # crate -> (4,3), agent -> (3,3)
    assert state.agent_pos() == (3, 3)
    assert _pos(state, "crate") == (4, 3)


def test_pushable_at_and_cell_blocked_reflect_live_positions():
    scene, grid, state = _build([{"id": "crate", "type": "box", "x": 2, "y": 3, "solid": True, "pushable": True}])
    assert pushable_at(state, 2, 3) is not None
    assert pushable_at(state, 5, 3) is None
    assert cell_blocked(state, grid, 2, 3) is True  # solid crate there
    assert cell_blocked(state, grid, 2, 3, ignore_id="crate") is False  # ignoring it, cell is free
    assert cell_blocked(state, grid, 7, 3) is True  # wall


def test_try_push_returns_false_when_immediately_blocked():
    scene, grid, state = _build(
        [{"id": "crate", "type": "box", "x": 6, "y": 3, "solid": True, "pushable": True}], agent=(5, 3)
    )
    moved = try_push(state, grid, state.objects["crate"], 1, 0)
    assert moved is False
    assert _pos(state, "crate") == (6, 3)


def test_non_pushable_solid_still_blocks_the_agent():
    scene, grid, state = _build([{"id": "rock", "type": "box", "x": 2, "y": 3, "solid": True}])
    with pytest.raises(ActionError, match="move blocked"):
        apply_action(state, grid, {"action": "move_right"}, scene)
