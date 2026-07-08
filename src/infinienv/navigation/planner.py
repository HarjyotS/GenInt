"""Symbolic task planner: expands a Goal into a primitive action sequence.

The LLM plans task semantics (which goals exist and in what order). Everything
below this layer -- pathfinding, pickup/drop/unlock legality, goal completion --
is deterministic code, per the project's core design rule.
"""

from __future__ import annotations

from infinienv.engine.actions import ActionError, apply_action
from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState
from infinienv.navigation.astar import find_path, path_to_moves
from infinienv.schema.scene_schema import SceneSpec


class PlanError(Exception):
    """Raised when a goal cannot be planned/solved from the current state."""


def _emit(
    grid: Grid,
    state: GameState,
    out: list[dict],
    action: dict,
    scene: SceneSpec | None = None,
    trace: list[dict] | None = None,
) -> None:
    """Apply `action` to `state`, append it to `out`, and (if given) record a trace
    entry for the state *at this exact step* -- not reconstructed after the fact,
    since `state` keeps mutating as planning continues past this point."""
    try:
        apply_action(state, grid, action, scene)
    except ActionError as exc:
        raise PlanError(str(exc)) from exc
    out.append(action)
    if trace is not None:
        trace.append(
            {
                "t": len(trace),
                "action": action["action"],
                "position": list(state.agent_pos()),
                "inventory": list(state.inventory),
            }
        )


def _path_moves_to(
    grid: Grid, state: GameState, out: list[dict], target: tuple[int, int], trace: list[dict] | None = None
) -> None:
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
        _emit(grid, state, out, {"action": move}, trace=trace)


def _ensure_holding(
    grid: Grid, state: GameState, out: list[dict], object_id: str, trace: list[dict] | None = None
) -> None:
    if object_id in state.inventory:
        return
    obj = state.objects.get(object_id)
    if obj is None:
        raise PlanError(f"unknown object {object_id!r}")
    if not obj.portable:
        raise PlanError(f"object {object_id!r} is not portable")
    _path_moves_to(grid, state, out, (obj.x, obj.y), trace=trace)
    _emit(grid, state, out, {"action": "pick_up", "object_id": object_id}, trace=trace)


def _ensure_holding_type(
    grid: Grid, state: GameState, out: list[dict], scene: SceneSpec, object_type: str, trace: list[dict] | None = None
) -> None:
    if any(oid in state.objects and state.objects[oid].type == object_type for oid in state.inventory):
        return
    candidate = next((o for o in scene.objects if o.type == object_type and o.portable), None)
    if candidate is None:
        raise PlanError(f"no portable object of type {object_type!r} available")
    _ensure_holding(grid, state, out, candidate.id, trace=trace)


def _plan_reach(grid: Grid, state: GameState, out: list[dict], target_id: str, trace: list[dict] | None = None) -> None:
    target_pos = state.object_pos(target_id)
    if target_pos is None:
        raise PlanError(f"reach target {target_id!r} has no position")
    _path_moves_to(grid, state, out, target_pos, trace=trace)


def _plan_pickup(grid: Grid, state: GameState, out: list[dict], object_id: str, trace: list[dict] | None = None) -> None:
    _ensure_holding(grid, state, out, object_id, trace=trace)


def _plan_deliver(
    grid: Grid, state: GameState, out: list[dict], object_id: str, target_id: str, trace: list[dict] | None = None
) -> None:
    _ensure_holding(grid, state, out, object_id, trace=trace)
    target_pos = state.object_pos(target_id)
    if target_pos is None:
        raise PlanError(f"deliver target {target_id!r} has no position")
    _path_moves_to(grid, state, out, target_pos, trace=trace)
    _emit(grid, state, out, {"action": "drop", "object_id": object_id}, trace=trace)


def _plan_unlock(grid: Grid, state: GameState, out: list[dict], door_id: str, trace: list[dict] | None = None) -> None:
    door = state.objects.get(door_id)
    if door is None:
        raise PlanError(f"unknown door {door_id!r}")
    if door_id in state.unlocked_doors:
        return
    if door.key_id is None:
        raise PlanError(f"door {door_id!r} has no key_id")
    _ensure_holding(grid, state, out, door.key_id, trace=trace)
    _path_moves_to(grid, state, out, (door.x, door.y), trace=trace)
    _emit(grid, state, out, {"action": "unlock", "door_id": door_id, "key_id": door.key_id}, trace=trace)


def _plan_interact(
    grid: Grid,
    state: GameState,
    out: list[dict],
    scene: SceneSpec,
    interaction_id: str,
    target_id: str,
    trace: list[dict] | None = None,
) -> None:
    interaction = next((i for i in scene.mechanics.custom_interactions if i.id == interaction_id), None)
    if interaction is None:
        raise PlanError(f"unknown interaction {interaction_id!r}")
    if (interaction_id, target_id) in state.completed_interactions:
        return
    target = state.objects.get(target_id)
    if target is None:
        raise PlanError(f"unknown interaction target {target_id!r}")
    if interaction.must_hold_type:
        _ensure_holding_type(grid, state, out, scene, interaction.must_hold_type, trace=trace)
    _path_moves_to(grid, state, out, (target.x, target.y), trace=trace)
    _emit(grid, state, out, {"action": interaction.trigger_action, "target_id": target_id}, scene, trace=trace)


def plan_goal(goal, grid: Grid, state: GameState, scene: SceneSpec | None = None, trace: list[dict] | None = None) -> list[dict]:
    """Return primitive actions to satisfy `goal`, applying them to `state` as they are planned.

    If `trace` is given, each `_emit`'d action appends its own step snapshot to it
    immediately -- so trace entries always reflect the state at that exact step, not
    whatever `state` happens to be by the time the caller gets around to look at it.
    """
    out: list[dict] = []
    kind = goal.type
    if kind == "reach":
        _plan_reach(grid, state, out, goal.target_id, trace=trace)
    elif kind == "pickup":
        _plan_pickup(grid, state, out, goal.object_id, trace=trace)
    elif kind == "deliver":
        _plan_deliver(grid, state, out, goal.object_id, goal.target_id, trace=trace)
    elif kind == "unlock":
        _plan_unlock(grid, state, out, goal.door_id, trace=trace)
    elif kind == "interact":
        if scene is None:
            raise PlanError("interact goals require the scene (for mechanics.custom_interactions)")
        _plan_interact(grid, state, out, scene, goal.interaction_id, goal.target_id, trace=trace)
    elif kind == "sequence":
        for sub in goal.subgoals:
            out.extend(plan_goal(sub, grid, state, scene, trace=trace))
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
    if kind == "interact":
        return (goal.interaction_id, goal.target_id) in state.completed_interactions
    if kind == "sequence":
        return all(is_goal_complete(sub, state) for sub in goal.subgoals)
    raise PlanError(f"unsupported goal type {kind!r}")
