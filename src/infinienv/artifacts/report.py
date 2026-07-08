"""Builds report.md: a short human-readable run summary for reviewers."""

from __future__ import annotations

from infinienv.generation.compiler import GenerationResult
from infinienv.navigation.policy import SolveResult


def build_report(
    *,
    prompt: str,
    provider_name: str,
    seed: int,
    out_dir: str,
    generation: GenerationResult,
    solve: SolveResult,
    metrics: dict,
) -> str:
    scene = generation.scene
    lines: list[str] = []
    lines.append(f"# InfiniEnv run report: {scene.metadata.name}")
    lines.append("")
    lines.append(f"**Prompt:** {prompt}")
    lines.append(f"**Provider:** {provider_name}  ")
    lines.append(f"**Seed:** {seed}  ")
    lines.append(f"**Output directory:** `{out_dir}`")
    lines.append("")

    lines.append("## Generation")
    lines.append("")
    lines.append(f"- Repair attempts: {generation.repair_attempts}")
    lines.append(f"- Fell back to template generator: {generation.used_fallback}")
    lines.append(f"- Final validation: {'PASSED' if generation.validation.valid else 'FAILED'}")
    if not generation.validation.valid:
        for err in generation.validation.errors:
            lines.append(f"  - `{err.code}`: {err.message}")
    lines.append("")

    lines.append("## Solver")
    lines.append("")
    lines.append(f"- Goal completion: {'SUCCESS' if solve.success else 'FAILED'}")
    lines.append(f"- Actions taken: {len(solve.actions)}")
    if not solve.success and solve.error:
        lines.append(f"- Error: {solve.error}")
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    for key, value in metrics.items():
        lines.append(f"- **{key}**: {value}")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    lines.append("- `scene.json` — structured SceneSpec ground truth")
    lines.append("- `validation.json` — validator checks and repair history")
    lines.append("- `metrics.json` — solvability, path length, success, timings")
    lines.append("- `render.png` — static visualization of the environment")
    lines.append("- `replay.gif` — replay of the agent solving the task")
    lines.append("")

    lines.append("## Claim")
    lines.append("")
    if metrics.get("success"):
        lines.append("This run **succeeded**: metrics.json reports `success: true`.")
    else:
        lines.append("This run **did not succeed**. See the generation/solver sections above for why.")
    lines.append("")

    return "\n".join(lines)
