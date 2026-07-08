"""Solvability check: can the deterministic planner complete every goal in the scene?"""

from __future__ import annotations

from infinienv.navigation.policy import SolveResult, solve_scene
from infinienv.schema.scene_schema import SceneSpec


def check_solvability(scene: SceneSpec) -> SolveResult:
    try:
        return solve_scene(scene)
    except Exception as exc:  # planner/engine bugs should surface as a failed solve, not a crash
        return SolveResult(success=False, error=f"solver crashed: {exc}")
