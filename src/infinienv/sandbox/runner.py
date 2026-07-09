"""Orchestrates a sandboxed generation end to end: prepare an isolated workspace copy, run a
SandboxAgent against it, extract artifacts, run the outer sanity check -- and, if that check
fails, feed the concrete failure back to the *same* agent (same persistent sandbox filesystem)
and let it repair its own work, up to a bounded number of attempts. This mirrors
`generation/compiler.py`'s repair loop for the non-sandbox path: the harness, not the model,
decides whether a result is acceptable, and a failure is a reason to retry with real feedback,
not a reason to silently give up after one shot.

See `sandbox/workspace.py` for the isolation mechanics and CLAUDE.md's sandbox section for what
guarantee this mode does and doesn't provide relative to the rest of the project.
"""

from __future__ import annotations

import asyncio
import json
import os
from importlib import resources
from typing import Callable

from infinienv.llm.base import ProviderError
from infinienv.sandbox.workspace import (
    ARTIFACT_FILES,
    build_workspace_dir,
    extract_artifacts,
    outer_sanity_check,
    sync_full_workspace,
    tar_directory,
)

DEFAULT_SANDBOX_MODEL = "gpt-5.5"
DEFAULT_SANDBOX_MAX_REPAIR_ATTEMPTS = int(os.environ.get("INFINIENV_SANDBOX_MAX_REPAIR_ATTEMPTS", "2"))


def _load_prompt(filename: str) -> str:
    return resources.files("infinienv.llm.prompts").joinpath(filename).read_text()


def _repair_message(*, run_error: str | None, sanity_error: str | None) -> str:
    if run_error is not None:
        return (
            f"Your previous attempt in this same workspace did not finish cleanly: {run_error}. "
            "Any files you already produced are still on disk -- inspect them (ls, cat), pick up "
            "from where you left off, and finish producing valid scene.json/metrics.json/"
            "replay.json/render.png/replay.gif."
        )
    return (
        "A previous attempt in this same workspace did not pass an independent outer check "
        f"using the real, unmodified schema: {sanity_error}. Your existing files from that "
        "attempt are still on disk -- inspect them, fix the specific problem described, and "
        "re-run to produce all five artifact files again."
    )


async def _run_async(
    prompt: str,
    seed: int,
    out_dir: str,
    *,
    model: str,
    max_turns: int,
    max_repair_attempts: int,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    def stage(msg: str) -> None:
        if on_stage is not None:
            on_stage(msg)

    try:
        from agents import Runner
        from agents.run import RunConfig
        from agents.sandbox import SandboxAgent, SandboxRunConfig
        from agents.sandbox.capabilities import Filesystem, Shell
        from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
    except ImportError as exc:
        raise ProviderError(
            "The 'openai-agents' package (with sandbox support) is not installed. "
            "Install it with `pip install infinienv[openai]`."
        ) from exc

    stage("Preparing isolated sandbox workspace (copy of schema/engine/navigation/validation/render)...")
    workspace_dir = build_workspace_dir(out_dir)

    client = UnixLocalSandboxClient()
    session = await client.create()
    await session.start()
    # snapshot=LocalSnapshotSpec(...) does not auto-hydrate on session creation in the
    # installed SDK version (verified live, not assumed) -- hydrate explicitly from a tar.
    await session.hydrate_workspace(tar_directory(workspace_dir))

    agent = SandboxAgent(
        name="SandboxMechanicAgent",
        instructions=_load_prompt("sandbox_agent.md"),
        model=model,
        capabilities=[Filesystem(), Shell()],
    )
    run_config = RunConfig(sandbox=SandboxRunConfig(client=client, session=session))

    agent_summary: str | None = None
    run_error: str | None = None
    artifact_paths: dict[str, str] = {}
    sane = False
    sanity_error: str | None = None
    missing: list[str] = list(ARTIFACT_FILES)
    repair_history: list[dict] = []
    message = f"Seed: {seed}\nTask: {prompt}"

    try:
        for attempt in range(max_repair_attempts + 1):
            stage(
                f"Running sandbox agent (attempt {attempt + 1}/{max_repair_attempts + 1})..."
                if attempt == 0
                else f"Repairing against the outer sanity check (attempt {attempt + 1}/{max_repair_attempts + 1})..."
            )
            try:
                result = await Runner.run(agent, message, run_config=run_config, max_turns=max_turns)
                agent_summary = result.final_output
                run_error = None
            except Exception as exc:
                # Deliberately not re-raised: the agent conversation not finishing cleanly (e.g.
                # hitting max_turns) doesn't mean nothing was produced -- whatever partial
                # artifacts and workspace state exist are still worth extracting, syncing, and
                # running through the outer sanity check below, rather than discarding all of
                # that. The failure becomes a repair-loop attempt like any other, not a crash.
                agent_summary = None
                run_error = str(exc)

            # Same session across attempts -- the sandbox filesystem persists (unlike the
            # agent's conversation memory, which is fresh each Runner.run call), so a repaired
            # attempt can genuinely inspect and fix its own prior files, not start from zero.
            artifact_paths = await extract_artifacts(session, out_dir)
            # Sync the sandbox's real final filesystem state back onto disk so the kept
            # workspace_dir reflects what the agent actually wrote/edited, not just the
            # pre-run copy build_workspace_dir produced -- this is the audit trail this mode
            # substitutes for a solvability guarantee, so it has to be the real thing.
            try:
                await sync_full_workspace(session, workspace_dir)
            except Exception:
                pass

            missing = [name for name in ARTIFACT_FILES if name not in artifact_paths]
            if run_error is None and "scene.json" in artifact_paths:
                sane, sanity_error = outer_sanity_check(out_dir)
            else:
                sane = False
                sanity_error = (
                    f"agent run did not finish cleanly: {run_error}"
                    if run_error is not None
                    else "scene.json missing"
                )

            repair_history.append(
                {
                    "attempt": attempt,
                    "run_error": run_error,
                    "outer_sanity_passed": sane,
                    "outer_sanity_error": sanity_error,
                    "missing_artifacts": missing,
                }
            )

            if run_error is None and sane and not missing:
                stage(f"Attempt {attempt + 1} passed the outer sanity check.")
                break
            if attempt >= max_repair_attempts:
                stage(f"Attempt {attempt + 1} failed and the repair budget is exhausted: {sanity_error}")
                break
            stage(f"Attempt {attempt + 1} failed an outer check, repairing: {sanity_error}")
            message = _repair_message(run_error=run_error, sanity_error=sanity_error)
    finally:
        await session.aclose()

    success = not missing and sane and run_error is None

    # Merge the outer verdict into the sandbox's own metrics.json (rather than overwriting
    # it) so both the sandbox's self-report and the outer, real-schema-based check are
    # visible in one place -- the audit trail this mode substitutes for a real guarantee.
    metrics_path = os.path.join(out_dir, "metrics.json")
    metrics: dict = {}
    if os.path.exists(metrics_path):
        try:
            with open(metrics_path) as f:
                metrics = json.load(f)
        except (OSError, json.JSONDecodeError):
            metrics = {}
    metrics.update(
        {
            "source": "sandbox",
            "provider": "openai_agents_sandbox",
            "seed": seed,
            "success": success,
            "sandbox_self_reported_success": metrics.get("success"),
            "outer_sanity_passed": sane,
            "outer_sanity_error": sanity_error,
            "missing_artifacts": missing,
            "repair_attempts": len(repair_history) - 1,
            "repair_history": repair_history,
        }
    )
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    return {
        "success": success,
        "agent_summary": agent_summary,
        "run_error": run_error,
        "artifact_paths": artifact_paths,
        "workspace_dir": workspace_dir,
        "metrics": metrics,
        "repair_attempts": len(repair_history) - 1,
    }


def run_sandbox_generation(
    prompt: str,
    seed: int,
    out_dir: str,
    *,
    model: str | None = None,
    max_turns: int = 40,
    max_repair_attempts: int | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Sync entrypoint: run a sandboxed generation end to end, repairing via the same agent
    against the real outer sanity check until it passes or the repair budget runs out. See
    module docstring. `on_stage`, if given, is called with a short progress message at each
    attempt boundary -- mirrors `evaluation.runner.run_generation`'s `on_stage` so the CLI and
    GUI can show live progress the same way for both paths.
    """
    model = model or os.environ.get("INFINIENV_SANDBOX_MODEL", DEFAULT_SANDBOX_MODEL)
    attempts = (
        DEFAULT_SANDBOX_MAX_REPAIR_ATTEMPTS if max_repair_attempts is None else max_repair_attempts
    )
    return asyncio.run(
        _run_async(
            prompt,
            seed,
            out_dir,
            model=model,
            max_turns=max_turns,
            max_repair_attempts=attempts,
            on_stage=on_stage,
        )
    )
