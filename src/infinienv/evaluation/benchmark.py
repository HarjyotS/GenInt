"""Batch evaluation over a prompt file. Aggregates the per-run metrics from evaluation.runner."""

from __future__ import annotations

import json
import os

from infinienv.artifacts.writer import resolve_out_dir
from infinienv.evaluation.runner import run_generation
from infinienv.llm.base import SceneProvider


def load_prompts(prompts_path: str) -> list[str]:
    prompts = []
    with open(prompts_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            prompts.append(line)
    return prompts


def run_benchmark(provider: SceneProvider, prompts_path: str, out_dir: str, *, seed: int = 42) -> dict:
    prompts = load_prompts(prompts_path)
    resolved_out = resolve_out_dir(out_dir)

    per_run: list[dict] = []
    for i, prompt in enumerate(prompts):
        run_out = os.path.join(resolved_out, f"prompt_{i:03d}")
        try:
            result = run_generation(provider, prompt, seed + i, run_out)
            per_run.append({"index": i, "prompt": prompt, **result.metrics})
        except Exception as exc:
            per_run.append({"index": i, "prompt": prompt, "success": False, "crashed": True, "error": str(exc)})

    n = len(per_run) or 1
    valid_first_try = sum(1 for r in per_run if r.get("repair_attempts") == 0 and r.get("validation_passed"))
    valid_after_repair = sum(
        1 for r in per_run if r.get("repair_attempts", 0) > 0 and r.get("validation_passed") and not r.get("used_fallback")
    )
    failed_after_repair = sum(1 for r in per_run if r.get("used_fallback") or not r.get("validation_passed"))
    solved = sum(1 for r in per_run if r.get("solver_success"))
    repair_attempts = [r.get("repair_attempts", 0) for r in per_run if "repair_attempts" in r]
    path_lengths = [r.get("path_length", 0) for r in per_run if r.get("solver_success")]
    gen_times = [r.get("generation_time_seconds", 0) for r in per_run if "generation_time_seconds" in r]

    summary = {
        "num_prompts": len(per_run),
        "provider": provider.name,
        "seed": seed,
        "valid_on_first_try": valid_first_try,
        "valid_after_repair": valid_after_repair,
        "failed_after_repair": failed_after_repair,
        "solved_successfully": solved,
        "avg_repair_attempts": round(sum(repair_attempts) / n, 3),
        "avg_path_length": round(sum(path_lengths) / len(path_lengths), 3) if path_lengths else None,
        "avg_generation_time_seconds": round(sum(gen_times) / n, 4),
        "runs": per_run,
    }

    with open(os.path.join(resolved_out, "benchmark_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    return summary
