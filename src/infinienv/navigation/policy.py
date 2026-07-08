"""Top-level solver: plans and executes every goal in a scene, producing a full action trace."""

from __future__ import annotations

from dataclasses import dataclass, field

from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState
from infinienv.navigation.planner import PlanError, is_goal_complete, plan_goal
from infinienv.schema.scene_schema import SceneSpec


@dataclass
class SolveResult:
    success: bool
    actions: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    error: str | None = None
    final_state: GameState | None = None
    # Per top-level goal: {"id", "type", "success"} -- the real signal behind
    # dataset export's programmatic_reward, not just a copy of overall `success`.
    goal_results: list[dict] = field(default_factory=list)


def solve_scene(scene: SceneSpec) -> SolveResult:
    grid = Grid(scene)
    state = GameState.from_scene(scene)
    actions: list[dict] = []
    trace: list[dict] = [{"t": 0, "action": None, "position": list(state.agent_pos())}]
    goal_results: list[dict] = []

    try:
        for goal in scene.goals:
            # `trace` is populated incrementally *inside* plan_goal (by _emit, at the
            # moment each action is actually applied) -- not rebuilt here afterward,
            # since by the time plan_goal returns, `state` already reflects the goal's
            # *final* step, not each intermediate one.
            goal_actions = plan_goal(goal, grid, state, scene, trace)
            actions.extend(goal_actions)
            goal_done = is_goal_complete(goal, state)
            goal_results.append({"id": goal.id, "type": goal.type, "success": goal_done})
            if not goal_done:
                return SolveResult(
                    success=False,
                    actions=actions,
                    trace=trace,
                    error=f"goal {goal.id!r} not satisfied after planning",
                    final_state=state,
                    goal_results=goal_results,
                )
    except PlanError as exc:
        # Mark every goal not yet recorded as not (yet) achieved, so goal_results always
        # covers every top-level goal even when planning itself raised mid-scene.
        recorded = {g["id"] for g in goal_results}
        for goal in scene.goals:
            if goal.id not in recorded:
                goal_results.append({"id": goal.id, "type": goal.type, "success": False})
        return SolveResult(
            success=False, actions=actions, trace=trace, error=str(exc), final_state=state, goal_results=goal_results
        )

    success = all(g["success"] for g in goal_results)
    trace.append({"t": len(actions) + 1, "success": success})
    return SolveResult(
        success=success,
        actions=actions,
        trace=trace,
        final_state=state,
        goal_results=goal_results,
        error=None if success else "unsatisfied goals",
    )
