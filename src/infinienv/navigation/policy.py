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


def solve_scene(scene: SceneSpec) -> SolveResult:
    grid = Grid(scene)
    state = GameState.from_scene(scene)
    actions: list[dict] = []
    trace: list[dict] = [{"t": 0, "action": None, "position": list(state.agent_pos())}]

    try:
        for goal in scene.goals:
            goal_actions = plan_goal(goal, grid, state)
            for act in goal_actions:
                actions.append(act)
                trace.append(
                    {
                        "t": len(actions),
                        "action": act["action"],
                        "position": list(state.agent_pos()),
                        "inventory": list(state.inventory),
                    }
                )
            if not is_goal_complete(goal, state):
                return SolveResult(
                    success=False,
                    actions=actions,
                    trace=trace,
                    error=f"goal {goal.id!r} not satisfied after planning",
                    final_state=state,
                )
    except PlanError as exc:
        return SolveResult(success=False, actions=actions, trace=trace, error=str(exc), final_state=state)

    success = all(is_goal_complete(g, state) for g in scene.goals)
    trace.append({"t": len(actions) + 1, "success": success})
    return SolveResult(success=success, actions=actions, trace=trace, final_state=state, error=None if success else "unsatisfied goals")
