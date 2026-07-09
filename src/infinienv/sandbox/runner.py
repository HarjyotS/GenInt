"""Orchestrates a sandboxed generation end to end: prepare an isolated workspace copy, run a
SandboxAgent against it, extract artifacts, run the outer sanity check.

See `sandbox/workspace.py` for the isolation mechanics and CLAUDE.md's sandbox section for what
guarantee this mode does and doesn't provide relative to the rest of the project.
"""

from __future__ import annotations

import asyncio
import json
import os
from importlib import resources

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


def _load_prompt(filename: str) -> str:
    return resources.files("infinienv.llm.prompts").joinpath(filename).read_text()


async def _run_async(prompt: str, seed: int, out_dir: str, *, model: str, max_turns: int) -> dict:
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

    workspace_dir = build_workspace_dir(out_dir)

    client = UnixLocalSandboxClient()
    session = await client.create()
    await session.start()
    # snapshot=LocalSnapshotSpec(...) does not auto-hydrate on session creation in the
    # installed SDK version (verified live, not assumed) -- hydrate explicitly from a tar.
    await session.hydrate_workspace(tar_directory(workspace_dir))

    agent_summary: str | None = None
    run_error: str | None = None
    try:
        agent = SandboxAgent(
            name="SandboxMechanicAgent",
            instructions=_load_prompt("sandbox_agent.md"),
            model=model,
            capabilities=[Filesystem(), Shell()],
        )
        run_config = RunConfig(sandbox=SandboxRunConfig(client=client, session=session))
        user_message = f"Seed: {seed}\nTask: {prompt}"
        try:
            result = await Runner.run(agent, user_message, run_config=run_config, max_turns=max_turns)
            agent_summary = result.final_output
        except Exception as exc:
            # Deliberately not re-raised: the agent conversation not finishing cleanly (e.g.
            # hitting max_turns) doesn't mean nothing was produced -- whatever partial
            # artifacts and workspace state exist are still worth extracting, syncing, and
            # running through the outer sanity check/metrics write below, rather than
            # discarding all of that in favor of a bare one-line error at the CLI's top
            # level. The error is instead surfaced honestly as a failed outer verdict.
            run_error = str(exc)
        finally:
            artifact_paths = await extract_artifacts(session, out_dir)
            # Sync the sandbox's real final filesystem state back onto disk so the kept
            # workspace_dir reflects what the agent actually wrote/edited, not just the
            # pre-run copy build_workspace_dir produced -- this is the audit trail this mode
            # substitutes for a solvability guarantee, so it has to be the real thing.
            try:
                await sync_full_workspace(session, workspace_dir)
            except Exception:
                pass
    finally:
        await session.aclose()

    missing = [name for name in ARTIFACT_FILES if name not in artifact_paths]
    if "scene.json" in artifact_paths:
        sane, sanity_error = outer_sanity_check(out_dir)
    else:
        sane, sanity_error = False, "scene.json missing"
    if run_error is not None:
        sane = False
        sanity_error = f"agent run did not finish cleanly: {run_error}" + (
            f" (outer sanity check also failed: {sanity_error})" if sanity_error else ""
        )

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
    }


def run_sandbox_generation(
    prompt: str, seed: int, out_dir: str, *, model: str | None = None, max_turns: int = 40
) -> dict:
    """Sync entrypoint: run a sandboxed generation end to end. See module docstring."""
    model = model or os.environ.get("INFINIENV_SANDBOX_MODEL", DEFAULT_SANDBOX_MODEL)
    return asyncio.run(_run_async(prompt, seed, out_dir, model=model, max_turns=max_turns))
