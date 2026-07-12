"""A second sandbox backend that drives the agent with Anthropic's **Claude Agent SDK**
(`claude-agent-sdk`) instead of the OpenAI Agents SDK. Selected via
`INFINIENV_SANDBOX_BACKEND=claude`; the default backend remains `openai`
(`sandbox/runner.py`). Everything a reviewer sees is the same -- the same isolated
`sandbox_workspace/` copy of the engine, the same five artifacts, the same outer sanity check,
the same repair loop, the same `metrics.json` shape -- only the agent runtime differs, so the two
are genuinely interchangeable rather than parallel forks of the whole pipeline.

The execution models differ in one structural way, which drives the differences below:

- The OpenAI backend (`sandbox/runner.py`) copies the workspace into a *separate ephemeral
  filesystem* (`hydrate_workspace`), runs the agent's shell/file tools there under a macOS
  Seatbelt profile, then syncs that filesystem *back* onto disk (`sync_full_workspace`) and
  extracts the five artifacts from it. The isolation boundary is a real, OS-enforced separate FS.
- The Claude Agent SDK (Claude Code as a library) runs its built-in Read/Write/Edit/Bash tools
  *directly on a working directory* (`cwd`). So here `cwd` **is** `sandbox_workspace/` on disk --
  the agent edits it in place, there's no tar hydrate/sync round trip, and "extract artifacts"
  is a plain copy of the five files out of that directory. Isolation is by working-directory
  convention on a throwaway copied workspace (never this repo's real source), **not** the
  OpenAI backend's Seatbelt confinement -- an honest, disclosed weakening, consistent with
  section 11's standing posture that sandbox mode is a disclosed trade-off, not a hidden one.
  The outer (trusted) process still never imports or executes the agent-written `.py` files;
  it only ever reads back the five artifact files, exactly as the OpenAI backend does.

Auth: the Claude Agent SDK spawns the `claude` CLI, which reads `ANTHROPIC_API_KEY`. `_load_dotenv`
maps `CL_KEY` -> `ANTHROPIC_API_KEY` (mirroring `OP_KEY` -> `OPENAI_API_KEY`); this module also
does that mapping defensively so it works regardless of entry point.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Callable

from infinienv.artifacts.writer import resolve_out_dir
from infinienv.llm.base import ProviderError
from infinienv.sandbox.runner import (
    _MAX_NARRATION_CHARS,
    _interpreter_briefing,
    _load_prompt,
    _repair_message,
    _truncate,
)
from infinienv.sandbox.auditor import audit_run
from infinienv.sandbox.workspace import (
    ARTIFACT_FILES,
    build_workspace_dir,
    deterministic_validation_summary,
    outer_sanity_check,
)

DEFAULT_SANDBOX_CLAUDE_MODEL = "claude-sonnet-5"

# Claude Code tool names -> how to narrate them, mirroring runner.py's `_describe_*` for the
# OpenAI backend (announce the intent -- a command, or the files touched -- never a diff).
_EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _describe_claude_message(message: object, *, stage: Callable[[str], None]) -> None:
    """Turn one Claude Agent SDK stream message into zero or more `on_stage` narration lines.
    Duck-typed against the message/block shapes (no isinstance against SDK classes, wrapped so a
    future shape change degrades to silence rather than crashing) -- same best-effort discipline
    as runner.py's `_describe_stream_event`.
    """
    try:
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            return
        for block in content:
            line = _describe_block(block)
            if line:
                stage(line)
    except Exception:
        return


def _describe_block(block: object) -> str | None:
    text = getattr(block, "text", None)
    if isinstance(text, str) and text.strip():
        return f"Agent: {_truncate(text, 300)}"
    thinking = getattr(block, "thinking", None)
    if isinstance(thinking, str) and thinking.strip():
        return f"Thinking: {_truncate(thinking, _MAX_NARRATION_CHARS)}"
    name = getattr(block, "name", None)
    if name is not None and getattr(block, "input", None) is not None:
        args = getattr(block, "input", None)
        args = args if isinstance(args, dict) else {}
        if name == "Bash":
            cmd = str(args.get("command") or "").strip()
            return f"$ {_truncate(cmd, _MAX_NARRATION_CHARS)}" if cmd else "Running a shell command..."
        if name in _EDIT_TOOLS:
            path = str(args.get("file_path") or args.get("notebook_path") or "").strip()
            return f"Editing: {path}" if path else "Editing files..."
        if name in ("Read", "Glob", "Grep", "TodoWrite"):
            return None  # too noisy to surface; the agent's own text covers intent
        return f"Calling tool: {name}"
    # A failed tool result is worth surfacing; a successful one is not (its intent was announced).
    if getattr(block, "is_error", False):
        result = getattr(block, "content", None)
        detail = ""
        if isinstance(result, str):
            detail = result
        elif isinstance(result, list):
            detail = " ".join(
                str(part.get("text", "")) for part in result if isinstance(part, dict)
            )
        detail = detail.strip()
        return f"  tool failed{': ' + _truncate(detail, _MAX_NARRATION_CHARS) if detail else ''}"
    return None


def _copy_artifacts_from_dir(workspace_dir: str, out_dir: str) -> dict[str, str]:
    """Copy the five standard artifacts out of the in-place workspace (`cwd`) into `out_dir`.
    The Claude Agent backend's equivalent of the OpenAI backend's `extract_artifacts(session, ...)`
    -- here the agent wrote directly to `workspace_dir` on disk, so it's a plain file copy, and the
    outer process still only ever reads these five named files, never the agent's code.
    """
    paths: dict[str, str] = {}
    for name in ARTIFACT_FILES:
        src = os.path.join(workspace_dir, name)
        if os.path.isfile(src):
            dst = os.path.join(out_dir, name)
            if os.path.abspath(src) != os.path.abspath(dst):
                shutil.copy(src, dst)
            paths[name] = dst
    return paths


async def _run_async(
    prompt: str,
    seed: int,
    out_dir: str,
    *,
    model: str,
    max_turns: int,
    max_repair_attempts: int,
    assets_mode: str = "none",
    require_runs_dir: bool = False,
    refine_prompt: bool = True,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    def stage(msg: str) -> None:
        if on_stage is not None:
            on_stage(msg)

    out_dir = resolve_out_dir(out_dir, require_runs_dir=require_runs_dir)

    # Best-effort prompt enrichment, identical to the OpenAI backend (see sandbox/prompt_refiner.py).
    original_prompt = prompt
    refine_note: str | None = "prompt refinement disabled"
    prompt_was_refined = False
    if refine_prompt:
        from infinienv.sandbox.prompt_refiner import refine_prompt as _refine

        stage("Refining prompt into a detailed spec...")
        refine_result = _refine(prompt)
        prompt = refine_result.refined
        refine_note = refine_result.note
        prompt_was_refined = refine_result.used_refinement
        if prompt_was_refined:
            stage(f"Refined prompt handed to the agent:\n{prompt}")
        else:
            stage(f"Prompt not refined ({refine_note}); using the original.")

    try:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    except ImportError as exc:
        raise ProviderError(
            "The 'claude-agent-sdk' package is not installed. "
            "Install it with `pip install infinienv[claude]` (and ensure the `claude` CLI is on PATH)."
        ) from exc

    # Auth is deliberately left to the `claude` CLI the Claude Agent SDK spawns: it uses the
    # user's claude.ai login (the normal case here) unless ANTHROPIC_API_KEY is set, in which case
    # it prefers that. We intentionally do NOT set ANTHROPIC_API_KEY from CL_KEY -- doing so forces
    # the CLI onto the API-key account and away from the login, which broke real runs when that
    # account was out of credit (see cli._load_dotenv and CLAUDE.md section 11). So we don't
    # require a key here; if there's genuinely no auth at all, the SDK surfaces that as a normal
    # run_error below rather than a hard precondition failure.

    # Same shared model cache as the OpenAI backend, so a first-time diffusion/rembg download is
    # reused across runs and backends instead of re-downloaded (see generator_diffusion.py).
    from infinienv.assets.generator_diffusion import model_cache_dir

    os.environ.setdefault("INFINIENV_MODEL_CACHE_DIR", model_cache_dir())

    stage("Preparing sandbox workspace (copy of schema/engine/navigation/validation/render/assets)...")
    workspace_dir = build_workspace_dir(out_dir, assets_mode=assets_mode)

    options = ClaudeAgentOptions(
        cwd=workspace_dir,
        model=model,
        max_turns=max_turns,
        # Append our sandbox rules to Claude Code's own coding-agent preset, rather than replacing
        # it -- keeps the built-in file/bash tool competence and adds this task's constraints.
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": _load_prompt("sandbox_agent.md"),
        },
        # Autonomous run: no human is available to approve each tool call.
        permission_mode="bypassPermissions",
        # Load none of the host's project/user settings or CLAUDE.md -- this repo's own CLAUDE.md
        # would otherwise be picked up by walking up from cwd and confuse the sandbox agent with
        # instructions meant for the outer project, not the isolated task.
        setting_sources=[],
    )

    agent_summary: str | None = None
    run_error: str | None = None
    artifact_paths: dict[str, str] = {}
    sane = False
    sanity_error: str | None = None
    audited = False
    audit_passed = True
    audit_findings: str | None = None
    audit_note: str | None = None
    missing: list[str] = list(ARTIFACT_FILES)
    repair_history: list[dict] = []
    message = f"Seed: {seed}\nTask: {prompt}\nAssets mode: {assets_mode}\n{_interpreter_briefing()}"

    for attempt in range(max_repair_attempts + 1):
        stage(
            f"Running Claude sandbox agent (attempt {attempt + 1}/{max_repair_attempts + 1})..."
            if attempt == 0
            else f"Repairing against the outer sanity check (attempt {attempt + 1}/{max_repair_attempts + 1})..."
        )
        agent_summary = None
        run_error = None
        try:
            async for msg in query(prompt=message, options=options):
                _describe_claude_message(msg, stage=stage)
                if isinstance(msg, ResultMessage):
                    result = getattr(msg, "result", None)
                    if isinstance(result, str) and result.strip():
                        agent_summary = result
                    if getattr(msg, "is_error", False) and run_error is None:
                        run_error = f"agent result reported an error (subtype={getattr(msg, 'subtype', None)})"
        except Exception as exc:
            # Same posture as the OpenAI backend: a conversation that doesn't finish cleanly (turn
            # budget, transport error) still leaves whatever files it produced on disk -- extract
            # and sanity-check them, record the failure as a repair attempt, never crash.
            run_error = str(exc)

        # The agent wrote directly into workspace_dir (cwd); copy the five artifacts to out_dir.
        # Persistent across attempts (built once before the loop) so a repair attempt genuinely
        # inspects and fixes its own prior files -- fresh conversation, persistent filesystem,
        # exactly as the OpenAI backend does.
        artifact_paths = _copy_artifacts_from_dir(workspace_dir, out_dir)
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

        # Independent faithfulness audit once the artifacts are mechanically sound -- identical to
        # the OpenAI backend (see sandbox/runner.py); audited=False (no key/disabled/error) never
        # blocks. Cross-model by construction here: the author is Claude, the auditor is OpenAI.
        audited = False
        audit_passed = True
        audit_findings = None
        audit_note = None
        if run_error is None and sane and not missing:
            stage("Independent reviewer auditing the run for faithfulness to the spec...")
            audit = audit_run(out_dir, prompt)
            audited, audit_passed = audit.audited, audit.passed
            audit_findings, audit_note = audit.findings, audit.note
            if audited and not audit_passed:
                stage("Auditor: the run fakes a required mechanic rather than implementing it.")
            elif audited:
                stage("Auditor: the run faithfully implements the spec.")
            else:
                stage(f"Auditor: skipped, run NOT independently audited ({audit_note}).")

        repair_history.append(
            {
                "attempt": attempt,
                "run_error": run_error,
                "outer_sanity_passed": sane,
                "outer_sanity_error": sanity_error,
                "audited": audited,
                "audit_passed": audit_passed,
                "audit_findings": audit_findings,
                "audit_note": audit_note,
                "missing_artifacts": missing,
            }
        )

        if run_error is None and sane and not missing and audit_passed:
            if audited:
                stage(f"Attempt {attempt + 1} passed the outer sanity check and the faithfulness audit.")
            else:
                stage(f"Attempt {attempt + 1} passed the outer sanity check (audit skipped, not verified).")
            break
        fail_reason = audit_findings if (audited and not audit_passed) else sanity_error
        if attempt >= max_repair_attempts:
            stage(f"Attempt {attempt + 1} failed and the repair budget is exhausted: {fail_reason}")
            break
        stage(f"Attempt {attempt + 1} failed a check, repairing: {fail_reason}")
        if audited and not audit_passed:
            message = _repair_message(run_error=None, sanity_error=None, audit_findings=audit_findings)
        else:
            message = _repair_message(run_error=run_error, sanity_error=sanity_error)

    success = not missing and sane and run_error is None and audit_passed

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
            "provider": "claude_agent_sandbox",
            "model": model,
            "seed": seed,
            "success": success,
            "sandbox_self_reported_success": metrics.get("success"),
            "outer_sanity_passed": sane,
            "outer_sanity_error": sanity_error,
            "audited": audited,
            "audit_passed": audit_passed,
            "audit_findings": audit_findings,
            "audit_note": audit_note,
            "deterministic_validation": deterministic_validation_summary(out_dir),
            "missing_artifacts": missing,
            "repair_attempts": len(repair_history) - 1,
            "repair_history": repair_history,
            "original_prompt": original_prompt,
            "refined_prompt": prompt,
            "prompt_refined": prompt_was_refined,
            "prompt_refine_note": refine_note,
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
