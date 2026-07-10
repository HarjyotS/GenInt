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
import re
import sys
from importlib import resources
from typing import Callable

from infinienv.artifacts.writer import resolve_out_dir
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

_PATCH_OP_RE = re.compile(r"^\*\*\* (Add File|Delete File|Update File): (.+)$", re.MULTILINE)
_PATCH_VERB = {"Add File": "add", "Delete File": "delete", "Update File": "edit"}
_MAX_NARRATION_CHARS = 200


def _load_prompt(filename: str) -> str:
    return resources.files("infinienv.llm.prompts").joinpath(filename).read_text()


def _interpreter_briefing() -> str:
    """A concrete, environment-accurate fact for the agent: exactly which Python interpreter
    its shell commands inherit (`UnixLocalSandboxClient` passes `env = os.environ.copy()` from
    this harness process to every `exec_command` subprocess, so it's the same interpreter,
    same installed packages, same everything), and whether `pymunk` is importable in it --
    checked at runtime rather than assumed, since the `physics` extra is optional.

    Without this, the agent has no way to know which of potentially several Python
    installations on the host actually has this project's dependencies, and (observed live --
    see notes.md) will burn turns hunting through `which -a python`, other interpreters, and
    `-S` (which disables site-packages on any interpreter) before giving up on `pymunk`, even
    though the correct interpreter had it importable the whole time.

    Critically, this tells the agent to use the *absolute path*, not a bare `python`/`python3`
    name, and explains why: every `exec_command` call the SDK makes runs the command through
    `sh -lc "<command>"` (`agents/sandbox/session/base_sandbox_session.py::_prepare_exec_command`)
    -- a login shell, which on macOS re-runs `/usr/libexec/path_helper` on *every single
    command*, silently reordering `PATH` so a bare `python`/`python3` can resolve to a
    completely different, dependency-less interpreter than the one described here, even though
    the environment variables themselves (including a correctly activated `VIRTUAL_ENV`/`PATH`)
    were faithfully inherited. Confirmed live and reproduced directly against
    `asyncio.create_subprocess_exec("sh", "-lc", ...)` -- the exact call the SDK makes -- with
    the absolute interpreter path, unmodified environment: works every time. An absolute path
    is not subject to this PATH reordering at all, so it's the fix, not a workaround.
    """
    try:
        import pymunk  # noqa: F401

        pymunk_note = "pymunk is installed and importable in it"
    except ImportError:
        pymunk_note = "pymunk is NOT installed in it -- if a mechanic needs real physics, implement your own"
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401

        diffusion_note = (
            "torch/diffusers are installed and importable in it; model weights are cached in a "
            "shared, project-level directory reused across every run (sandboxed or not), so "
            "generation may take a while the first time a given model/type needs downloading but "
            "is fast afterward -- if a generation call seems slow, that's a one-time download, "
            "not a hang; let it finish rather than interrupting and disabling asset generation"
        )
    except ImportError:
        diffusion_note = (
            "torch/diffusers are NOT installed in it -- assets.resolver's local diffusion sprite "
            "backend (INFINIENV_SPRITE_BACKEND=diffusion) will raise a clear ProviderError if selected"
        )
    return (
        f"Python interpreter: {sys.executable} ({pymunk_note}; {diffusion_note}).\n"
        f"ALWAYS invoke it by this exact absolute path, e.g. `{sys.executable} run_scene.py` -- "
        "never a bare `python`/`python3` name. Your shell commands run as `sh -lc \"...\"` (a login "
        "shell), which on this kind of host re-runs PATH-rewriting logic on *every single command*, "
        "so a bare interpreter name can silently resolve to a different, dependency-less Python "
        "even though your environment variables are otherwise inherited correctly. The absolute "
        "path above bypasses that entirely and is the one interpreter guaranteed to have this "
        "project's dependencies (pymunk included, if noted above).\n"
        "Do not pass -S. Do not set or clear PYTHONHOME/PYTHONPATH/PYTHONNOUSERSITE for any "
        "reason -- even `PYTHONHOME=` (empty) is a real, broken override, not a no-op, and will "
        "itself produce a 'Fatal Python error: init_import_site' crash on any interpreter. If a "
        "command fails with that error or a missing-module error, the fix is almost always "
        "\"I used a bare python/python3 name or touched one of those env vars\" -- re-run with "
        "the exact absolute path above and no env changes. Do not go looking for a different "
        "python/python3 on the system; none of the others have this project's dependencies."
    )


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_json_object(raw: object) -> dict:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _describe_tool_called(item: object) -> str | None:
    """A `tool_called` stream item -> a short line describing what the sandbox agent is about
    to do: the shell command it's running, or the files it's about to touch. Never a diff --
    for `apply_patch` this only lists file paths and add/edit/delete, parsed from the patch's
    own `*** Add/Update/Delete File:` headers, not the hunk content.
    """
    raw = getattr(item, "raw_item", None)
    name = raw.get("name") if isinstance(raw, dict) else getattr(raw, "name", None)
    if name == "exec_command":
        args = _safe_json_object(raw.get("arguments") if isinstance(raw, dict) else getattr(raw, "arguments", None))
        cmd = str(args.get("cmd") or "").strip()
        return f"$ {_truncate(cmd, _MAX_NARRATION_CHARS)}" if cmd else "Running a shell command..."
    if name == "apply_patch":
        patch_text = (raw.get("input") if isinstance(raw, dict) else getattr(raw, "input", None)) or ""
        ops = [
            f"{_PATCH_VERB.get(verb, verb.lower())} {path.strip()}"
            for verb, path in _PATCH_OP_RE.findall(patch_text)
        ]
        return f"Editing: {', '.join(ops)}" if ops else "Editing files..."
    if name == "view_image":
        return "Viewing an image it produced..."
    if name:
        return f"Calling tool: {name}"
    return None


def _describe_tool_output(item: object) -> str | None:
    """Only surface tool output when it's actually informative -- a shell command that failed.
    Successful commands and every `apply_patch` result stay silent: the intent was already
    announced by `_describe_tool_called`, and this project never surfaces diffs.
    """
    output = getattr(item, "output", None)
    if not isinstance(output, str):
        return None
    match = re.search(r"Process exited with code (-?\d+)", output)
    if not match or match.group(1) == "0":
        return None
    tail = output.split("Output:", 1)[-1].strip()
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    if not lines:
        detail = ""
    elif len(lines) == 1 or lines[0] == lines[-1]:
        detail = lines[0]
    else:
        # A leading line is often an incidental warning (e.g. a locale warning from `perl`,
        # printed before anything else) rather than the actual failure -- observed live to
        # mislead an agent into a long trial-and-error loop chasing the wrong cause. The real
        # error/exception summary is usually the *last* line for shell errors, Python
        # tracebacks, and most CLI tools, so show both instead of just the first.
        detail = f"{lines[0]} ... {lines[-1]}"
    suffix = f": {_truncate(detail, _MAX_NARRATION_CHARS)}" if detail else ""
    return f"  command failed (exit {match.group(1)}){suffix}"


def _describe_reasoning(item: object) -> str | None:
    raw = getattr(item, "raw_item", None)
    summary = (raw.get("summary") if isinstance(raw, dict) else getattr(raw, "summary", None)) or []
    texts = [t for t in (s.get("text") if isinstance(s, dict) else getattr(s, "text", None) for s in summary) if t]
    text = " ".join(texts).strip()
    return f"Thinking: {_truncate(text, _MAX_NARRATION_CHARS)}" if text else None


def _describe_message(item: object) -> str | None:
    raw = getattr(item, "raw_item", None)
    content = (raw.get("content") if isinstance(raw, dict) else getattr(raw, "content", None)) or []
    texts = [t for t in (c.get("text") if isinstance(c, dict) else getattr(c, "text", None) for c in content) if t]
    text = " ".join(texts).strip()
    return f"Agent: {_truncate(text, 300)}" if text else None


_STREAM_ITEM_DESCRIBERS: dict[str, Callable[[object], str | None]] = {
    "tool_called": _describe_tool_called,
    "tool_output": _describe_tool_output,
    "reasoning_item_created": _describe_reasoning,
    "message_output_created": _describe_message,
}


def _describe_stream_event(event: object) -> str | None:
    """One agent SDK stream event -> a short narration line for `on_stage`, or None to stay
    silent. Deliberately duck-typed against the event/item shapes (no `agents` import at
    module scope, no isinstance checks against SDK classes) so this is testable without the
    optional SDK installed, matching this project's lazy-import discipline for optional/heavy
    dependencies -- and so it degrades gracefully rather than crashing if a future SDK version
    changes an item's internal shape. `on_stage` narration is best-effort commentary on top of
    a real run, never something a run's correctness depends on.
    """
    if getattr(event, "type", None) != "run_item_stream_event":
        return None
    describer = _STREAM_ITEM_DESCRIBERS.get(getattr(event, "name", None))
    if describer is None:
        return None
    try:
        return describer(getattr(event, "item", None))
    except Exception:
        return None


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
    assets_mode: str = "none",
    require_runs_dir: bool = False,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    def stage(msg: str) -> None:
        if on_stage is not None:
            on_stage(msg)

    out_dir = resolve_out_dir(out_dir, require_runs_dir=require_runs_dir)

    try:
        from agents import Runner
        from agents.run import RunConfig
        from agents.sandbox import SandboxAgent, SandboxRunConfig
        from agents.sandbox.capabilities import Filesystem, Shell
        from agents.sandbox.manifest import Manifest
        from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
        from agents.sandbox.workspace_paths import SandboxPathGrant
    except ImportError as exc:
        raise ProviderError(
            "The 'openai-agents' package (with sandbox support) is not installed. "
            "Install it with `pip install infinienv[openai]`."
        ) from exc

    stage("Preparing isolated sandbox workspace (copy of schema/engine/navigation/validation/render/assets)...")
    workspace_dir = build_workspace_dir(out_dir, assets_mode=assets_mode)

    # Real, live-caught bug (see notes.md): inside a sandboxed run, HOME resolves within that
    # one attempt's ephemeral workspace filesystem, not the host's real home directory -- so the
    # local diffusion backend's HF_HOME/U2NET_HOME defaults (see generator_diffusion.py) would
    # otherwise land inside the sandbox and re-download multi-GB model weights from scratch on
    # every single run. Set the same env var the outer process uses so it's inherited into the
    # sandboxed subprocess's environment (UnixLocalSandboxClient passes env = os.environ.copy()
    # to every exec_command), and grant that exact host path read-write so a download that
    # already happened (by this run or any earlier one) is reused instead of repeated.
    from infinienv.assets.generator_diffusion import model_cache_dir

    os.environ.setdefault("INFINIENV_MODEL_CACHE_DIR", model_cache_dir())

    client = UnixLocalSandboxClient()
    # Grant read-only access to this harness's own Python prefix (sys.prefix -- e.g. a project
    # .venv). Without this, on macOS, exec_command runs every shell command through a
    # sandbox-exec (Seatbelt) profile that denies reading anything under the real filesystem
    # outside the ephemeral workspace root except a narrow, hand-picked allowlist
    # (/opt/homebrew, /usr/local, /Library/Frameworks, the literal executable's own containing
    # directory) -- which does NOT cover a project-local venv's own lib/site-packages, even
    # though the venv's *binary* is reachable. Confirmed live: this crashes the interpreter
    # itself during startup (`Fatal Python error: init_import_site`, root cause
    # `PermissionError: Operation not permitted: '<venv>/pyvenv.cfg'`), not just an import
    # error inside user code -- so no amount of prompt engineering about *which* interpreter
    # to use can fix it; the interpreter it's told to use has to actually be able to read its
    # own files. See notes.md for the full diagnosis and a from-scratch repro against the
    # SDK's real Seatbelt profile generator that isolated this exact grant as the fix.
    manifest = Manifest(
        extra_path_grants=(
            SandboxPathGrant(
                path=sys.prefix,
                read_only=True,
                description="Python interpreter and installed packages (incl. pymunk if the physics extra is installed)",
            ),
            SandboxPathGrant(
                path=os.environ["INFINIENV_MODEL_CACHE_DIR"],
                read_only=False,
                description="Shared, project-level cache for local diffusion/background-removal model "
                "weights -- read-write so a first-time download persists across runs instead of "
                "re-downloading multi-GB weights inside each sandboxed run's ephemeral filesystem",
            ),
        )
    )
    session = await client.create(manifest=manifest)
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
    message = f"Seed: {seed}\nTask: {prompt}\nAssets mode: {assets_mode}\n{_interpreter_briefing()}"

    try:
        for attempt in range(max_repair_attempts + 1):
            stage(
                f"Running sandbox agent (attempt {attempt + 1}/{max_repair_attempts + 1})..."
                if attempt == 0
                else f"Repairing against the outer sanity check (attempt {attempt + 1}/{max_repair_attempts + 1})..."
            )
            try:
                streamed = Runner.run_streamed(agent, message, run_config=run_config, max_turns=max_turns)
                async for event in streamed.stream_events():
                    narration = _describe_stream_event(event)
                    if narration:
                        stage(narration)
                agent_summary = streamed.final_output
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
    max_turns: int = 60,
    max_repair_attempts: int | None = None,
    assets_mode: str = "none",
    require_runs_dir: bool = False,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Sync entrypoint: run a sandboxed generation end to end, repairing via the same agent
    against the real outer sanity check until it passes or the repair budget runs out. See
    module docstring. `on_stage`, if given, is called with a short progress message at each
    attempt boundary -- mirrors `evaluation.runner.run_generation`'s `on_stage` so the CLI and
    GUI can show live progress the same way for both paths. `assets_mode` mirrors the non-sandbox
    `--assets` flag ({none,local,generated,auto}) -- passed through to the workspace so the
    agent's reference run_scene.py resolves real sprites instead of flat colored cells.
    `require_runs_dir`, when set, requires `out_dir` to resolve under `runs/` (raises
    `ValueError` otherwise) -- the CLI sets this, the GUI deliberately doesn't (see
    `artifacts/writer.py::resolve_out_dir`).
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
            assets_mode=assets_mode,
            require_runs_dir=require_runs_dir,
            on_stage=on_stage,
        )
    )
