"""Symbolic task planner: expands a Goal into a primitive action sequence.

The LLM plans task semantics (which goals exist and in what order). Everything
below this layer -- pathfinding, pickup/drop/unlock legality, goal completion --
is deterministic code, per the project's core design rule.
"""

from __future__ import annotations

from infinienv.engine.actions import apply_action
from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState
from infinienv.navigation.astar import find_path, path_to_moves


class PlanError(Exception):
    """Raised when a goal cannot be planned/solved from the current state."""


def _emit(grid: Grid, state: GameState, out: list[dict], action: dict) -> None:
    """Append `action` to `out` and apply it to `state` so subsequent planning sees the effect."""
    apply_action(state, grid, action)
    out.append(action)


def _path_moves_to(grid: Grid, state: GameState, out: list[dict], target: tuple[int, int]) -> None:
    start = state.agent_pos()
    unlocked = frozenset(state.unlocked_doors)
    path = find_path(grid, start, target, unlocked_doors=unlocked)
    if path is None:
        # target cell itself may be unenterable (e.g. a solid object); try adjacent cells.
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            candidate = (target[0] + dx, target[1] + dy)
            path = find_path(grid, start, candidate, unlocked_doors=unlocked)
            if path is not None:
                break
    if path is None:
        raise PlanError(f"no path to {target}")
    for move in path_to_moves(path):
        _emit(grid, state, out, {"action": move})


def _ensure_holding(grid: Grid, state: GameState, out: list[dict], object_id: str) -> None:
    if object_id in state.inventory:
        return
    obj = state.objects.get(object_id)
    if obj is None:
        raise PlanError(f"unknown object {object_id!r}")
    if not obj.portable:
        raise PlanError(f"object {object_id!r} is not portable")
    _path_moves_to(grid, state, out, (obj.x, obj.y))
    _emit(grid, state, out, {"action": "pick_up", "object_id": object_id})


def _plan_reach(grid: Grid, state: GameState, out: list[dict], target_id: str) -> None:
    target_pos = state.object_pos(target_id)
    if target_pos is None:
        raise PlanError(f"reach target {target_id!r} has no position")
    _path_moves_to(grid, state, out, target_pos)


def _plan_pickup(grid: Grid, state: GameState, out: list[dict], object_id: str) -> None:
    _ensure_holding(grid, state, out, object_id)


def _plan_deliver(grid: Grid, state: GameState, out: list[dict], object_id: str, target_id: str) -> None:
    _ensure_holding(grid, state, out, object_id)
    target_pos = state.object_pos(target_id)
    if target_pos is None:
        raise PlanError(f"deliver target {target_id!r} has no position")
    _path_moves_to(grid, state, out, target_pos)
    _emit(grid, state, out, {"action": "drop", "object_id": object_id})


def _plan_unlock(grid: Grid, state: GameState, out: list[dict], door_id: str) -> None:
    door = state.objects.get(door_id)
    if door is None:
        raise PlanError(f"unknown door {door_id!r}")
    if door_id in state.unlocked_doors:
        return
    if door.key_id is None:
        raise PlanError(f"door {door_id!r} has no key_id")
    _ensure_holding(grid, state, out, door.key_id)
    _path_moves_to(grid, state, out, (door.x, door.y))
    _emit(grid, state, out, {"action": "unlock", "door_id": door_id, "key_id": door.key_id})


def plan_goal(goal, grid: Grid, state: GameState) -> list[dict]:
    """Return primitive actions to satisfy `goal`, applying them to `state` as they are planned."""
    out: list[dict] = []
    kind = goal.type
    if kind == "reach":
        _plan_reach(grid, state, out, goal.target_id)
    elif kind == "pickup":
        _plan_pickup(grid, state, out, goal.object_id)
    elif kind == "deliver":
        _plan_deliver(grid, state, out, goal.object_id, goal.target_id)
    elif kind == "unlock":
        _plan_unlock(grid, state, out, goal.door_id)
    elif kind == "sequence":
        for sub in goal.subgoals:
            out.extend(plan_goal(sub, grid, state))
    else:
        raise PlanError(f"unsupported goal type {kind!r}")
    return out


def is_goal_complete(goal, state: GameState) -> bool:
    kind = goal.type
    if kind == "reach":
        pos = state.object_pos(goal.target_id)
        return pos is not None and state.agent_pos() == pos
    if kind == "pickup":
        return goal.object_id in state.inventory
    if kind == "deliver":
        obj = state.objects.get(goal.object_id)
        target = state.objects.get(goal.target_id)
        if obj is None or target is None or obj.held:
            return False
        return (obj.x, obj.y) == (target.x, target.y)
    if kind == "unlock":
        return goal.door_id in state.unlocked_doors
    if kind == "sequence":
        return all(is_goal_complete(sub, state) for sub in goal.subgoals)
    raise PlanError(f"unsupported goal type {kind!r}")
