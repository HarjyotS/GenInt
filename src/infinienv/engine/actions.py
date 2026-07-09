"""Deterministic action application: move / pick_up / drop / unlock / wait / custom interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState

if TYPE_CHECKING:
    from infinienv.schema.scene_schema import SceneSpec

MOVE_DELTAS = {
    "move_up": (0, -1),
    "move_down": (0, 1),
    "move_left": (-1, 0),
    "move_right": (1, 0),
}


class ActionError(Exception):
    """Raised when an action is illegal given the current state."""


def apply_action(state: GameState, grid: Grid, action: dict, scene: "SceneSpec | None" = None) -> GameState:
    """Apply one action in place and return the mutated state. Raises ActionError if illegal."""
    kind = action.get("action")

    if kind in MOVE_DELTAS:
        from infinienv.engine.physics import cell_blocked, pushable_at, try_push

        dx, dy = MOVE_DELTAS[kind]
        nx, ny = state.agent_x + dx, state.agent_y + dy
        # Moving into a pushable object shoves it (one cell, or sliding until blocked if it's
        # slippery) instead of being blocked. Collision is computed from live state, not the
        # static grid, so it stays correct after objects have moved -- see engine/physics.py.
        pushable = pushable_at(state, nx, ny)
        if pushable is not None:
            if not try_push(state, grid, pushable, dx, dy):
                raise ActionError(f"push blocked: {pushable.id!r} cannot move ({dx}, {dy})")
            state.agent_x, state.agent_y = nx, ny  # the object vacated (nx, ny)
            return state
        if cell_blocked(state, grid, nx, ny):
            raise ActionError(f"move blocked at ({nx}, {ny})")
        state.agent_x, state.agent_y = nx, ny
        return state

    if kind == "pick_up":
        object_id = action["object_id"]
        obj = state.objects.get(object_id)
        if obj is None:
            raise ActionError(f"unknown object {object_id!r}")
        if not obj.portable:
            raise ActionError(f"object {object_id!r} is not portable")
        if obj.held:
            raise ActionError(f"object {object_id!r} is already held")
        if not state.is_adjacent_or_same(obj.x, obj.y):
            raise ActionError(f"agent is not adjacent to {object_id!r}")
        obj.held = True
        state.inventory.append(object_id)
        return state

    if kind == "drop":
        object_id = action["object_id"]
        obj = state.objects.get(object_id)
        if obj is None:
            raise ActionError(f"unknown object {object_id!r}")
        if not obj.held:
            raise ActionError(f"object {object_id!r} is not held")
        obj.held = False
        obj.x, obj.y = state.agent_x, state.agent_y
        state.inventory.remove(object_id)
        return state

    if kind == "unlock":
        door_id = action["door_id"]
        key_id = action["key_id"]
        door = state.objects.get(door_id)
        if door is None:
            raise ActionError(f"unknown door {door_id!r}")
        if key_id not in state.inventory:
            raise ActionError(f"key {key_id!r} not held")
        if not state.is_adjacent_or_same(door.x, door.y):
            raise ActionError(f"agent is not adjacent to door {door_id!r}")
        door.locked = False
        state.unlocked_doors.add(door_id)
        return state

    if kind == "wait":
        return state

    if scene is not None and scene.mechanics.custom_interactions:
        from infinienv.engine.interactions import apply_custom_interaction  # local: avoids a circular import

        return apply_custom_interaction(state, scene, action)

    raise ActionError(f"unsupported action {kind!r}")
