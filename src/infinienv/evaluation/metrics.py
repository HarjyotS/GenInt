"""Computes the metrics.json payload for a single run."""

from __future__ import annotations

from infinienv.generation.compiler import GenerationResult
from infinienv.navigation.policy import SolveResult
from infinienv.schema.scene_schema import SceneSpec


def compute_metrics(
    *,
    provider_name: str,
    seed: int,
    generation: GenerationResult,
    solve: SolveResult,
    generation_time_seconds: float,
    solve_time_seconds: float,
) -> dict:
    scene: SceneSpec = generation.scene
    success = bool(generation.validation.valid and solve.success)
    return {
        "success": success,
        "provider": provider_name,
        "seed": seed,
        "repair_attempts": generation.repair_attempts,
        "used_fallback": generation.used_fallback,
        "validation_passed": generation.validation.valid,
        "solver_success": solve.success,
        "path_length": len(solve.actions),
        "num_objects": len(scene.objects),
        "num_walls": len(scene.walls),
        "num_goals": len(scene.goals),
        "generation_time_seconds": round(generation_time_seconds, 4),
        "solve_time_seconds": round(solve_time_seconds, 4),
    }
