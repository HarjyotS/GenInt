"""Deterministic action application: move / pick_up / drop / unlock / wait."""

from __future__ import annotations

from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState

MOVE_DELTAS = {
    "move_up": (0, -1),
    "move_down": (0, 1),
    "move_left": (-1, 0),
    "move_right": (1, 0),
}


class ActionError(Exception):
    """Raised when an action is illegal given the current state."""


def apply_action(state: GameState, grid: Grid, action: dict) -> GameState:
    """Apply one action in place and return the mutated state. Raises ActionError if illegal."""
    kind = action.get("action")

    if kind in MOVE_DELTAS:
        dx, dy = MOVE_DELTAS[kind]
        nx, ny = state.agent_x + dx, state.agent_y + dy
        if grid.is_blocked(nx, ny, unlocked_doors=frozenset(state.unlocked_doors)):
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

    raise ActionError(f"unsupported action {kind!r}")
