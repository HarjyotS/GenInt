"""End-to-end pipeline: generate -> validate/repair -> build -> solve -> render -> artifacts.

This is the single place that owns the full loop described in CLAUDE.md section 1.
Used by both `infinienv generate` and benchmark mode.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from infinienv.artifacts.report import build_report
from infinienv.artifacts.writer import resolve_out_dir, write_metrics, write_report, write_scene, write_validation
from infinienv.evaluation.metrics import compute_metrics
from infinienv.generation.compiler import GenerationResult, generate_and_validate
from infinienv.llm.base import SceneProvider
from infinienv.navigation.policy import SolveResult, solve_scene
from infinienv.render.image_export import save_render_png
from infinienv.render.replay_export import save_replay_gif

StageCallback = Callable[[str], None]


@dataclass
class RunResult:
    out_dir: str
    generation: GenerationResult
    solve: SolveResult
    metrics: dict
    paths: dict[str, str]


def run_generation(
    provider: SceneProvider,
    prompt: str,
    seed: int,
    out_dir: str,
    *,
    max_repair_attempts: int | None = None,
    on_stage: StageCallback | None = None,
) -> RunResult:
    def stage(msg: str) -> None:
        if on_stage:
            on_stage(msg)

    resolved_out = resolve_out_dir(out_dir)

    t0 = time.perf_counter()
    generation = generate_and_validate(provider, prompt, seed, max_repair_attempts=max_repair_attempts)
    generation_time = time.perf_counter() - t0
    if generation.repair_attempts == 0 and generation.validation.valid:
        stage("Generated initial SceneSpec (valid on first try)")
    elif generation.used_fallback:
        stage(f"Validation failed after {generation.repair_attempts} repair attempt(s); used template fallback")
    else:
        stage(f"Repair succeeded after {generation.repair_attempts} attempt(s)")

    stage("Built playable gridworld")

    t1 = time.perf_counter()
    solve = solve_scene(generation.scene)
    solve_time = time.perf_counter() - t1
    if solve.success:
        stage(f"Solver completed goal in {len(solve.actions)} actions")
    else:
        stage(f"Solver failed: {solve.error}")

    scene_path = write_scene(resolved_out, generation.scene)
    validation_payload = generation.validation.to_dict()
    validation_payload["repair_history"] = generation.history
    validation_path = write_validation(resolved_out, validation_payload)

    metrics = compute_metrics(
        provider_name=provider.name,
        seed=seed,
        generation=generation,
        solve=solve,
        generation_time_seconds=generation_time,
        solve_time_seconds=solve_time,
    )
    metrics_path = write_metrics(resolved_out, metrics)

    render_path = f"{resolved_out}/render.png"
    save_render_png(generation.scene, render_path, title=generation.scene.metadata.name)

    replay_path = f"{resolved_out}/replay.gif"
    save_replay_gif(generation.scene, solve.actions, replay_path)

    report_md = build_report(
        prompt=prompt,
        provider_name=provider.name,
        seed=seed,
        out_dir=resolved_out,
        generation=generation,
        solve=solve,
        metrics=metrics,
    )
    report_path = write_report(resolved_out, report_md)

    stage(
        "Wrote artifacts:\n"
        f"      - {scene_path}\n"
        f"      - {validation_path}\n"
        f"      - {metrics_path}\n"
        f"      - {render_path}\n"
        f"      - {replay_path}\n"
        f"      - {report_path}"
    )

    return RunResult(
        out_dir=resolved_out,
        generation=generation,
        solve=solve,
        metrics=metrics,
        paths={
            "scene": scene_path,
            "validation": validation_path,
            "metrics": metrics_path,
            "render": render_path,
            "replay": replay_path,
            "report": report_path,
        },
    )
