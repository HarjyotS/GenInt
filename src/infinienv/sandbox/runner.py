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
from infinienv.sandbox.auditor import audit_run
from infinienv.sandbox.workspace import (
    ARTIFACT_FILES,
    build_workspace_dir,
    deterministic_validation_summary,
    extract_artifacts,
    outer_sanity_check,
    sync_full_workspace,
    tar_directory,
)

DEFAULT_SANDBOX_MODEL = "gpt-5.6-terra"
DEFAULT_SANDBOX_MAX_REPAIR_ATTEMPTS = int(os.environ.get("INFINIENV_SANDBOX_MAX_REPAIR_ATTEMPTS", "3"))

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


# Narration blocks shown in the live feed (command output + edit diffs). Both backends emit these
# via on_stage with a recognizable prefix the GUI classifier maps to a kind; the GUI renders them as
# a collapsible mono output block / a colored +/- diff. Kept compact so the feed stays readable.
_OUTPUT_MAX_LINES = 14
_DIFF_MAX_LINES = 40

# Invisible sentinel prefix for LIVE partial-model-output deltas (streamed thinking/text as the model
# generates, so a slow turn shows progress instead of dead air). `⁣` is an invisible separator
# (valid in JSON/SSE, renders as nothing); the GUI classifier maps it to kind `live` and strips it,
# accumulating the deltas into one in-place "live" bubble.
LIVE_PREFIX = "⁣LIVE⁣"


def _output_block(text: str, *, max_lines: int = _OUTPUT_MAX_LINES) -> str | None:
    """A trimmed command/tool output tagged `Output:` for the GUI to render as a collapsible block.
    Returns None for empty output (nothing worth showing)."""
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return None
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more lines)"]
    return "Output:\n" + "\n".join(lines)


def _make_diff(old: str, new: str, *, max_lines: int = _DIFF_MAX_LINES) -> str:
    """A compact unified diff (no `---`/`+++` file headers) of old->new, so an edit shows its actual
    change. Empty string when there's no textual difference."""
    import difflib

    lines = [
        ln
        for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="")
        if not (ln.startswith("--- ") or ln.startswith("+++ "))
    ]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more diff lines)"]
    return "\n".join(lines)


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
        header = f"Editing: {', '.join(ops)}" if ops else "Editing files..."
        # Include the actual change (the patch's +/- hunk lines, minus the `*** ...` file headers the
        # `ops` list already covers), so the feed shows the diff, rendered colorized by the GUI.
        body = [ln for ln in patch_text.splitlines() if not ln.startswith("*** ")]
        while body and not body[-1].strip():
            body.pop()
        while body and not body[0].strip():
            body.pop(0)
        if len(body) > _DIFF_MAX_LINES:
            body = body[:_DIFF_MAX_LINES] + [f"… ({len(body) - _DIFF_MAX_LINES} more diff lines)"]
        return f"{header}\n" + "\n".join(body) if body else header
    if name == "view_image":
        return "Viewing an image it produced..."
    if name:
        return f"Calling tool: {name}"
    return None


def _describe_tool_output(item: object) -> str | None:
    """A tool result -> a narration line. Surfaces a shell command's output (both success, as a
    collapsible block, and failure, as a first/last-line summary) so the feed shows what each step
    produced. Non-exec results (apply_patch/view_image) stay silent -- their intent/diff was already
    announced by `_describe_tool_called`.
    """
    output = getattr(item, "output", None)
    if not isinstance(output, str):
        return None
    # Surface the TODO harness's structured progress lines even on a SUCCESSFUL command, so the GUI
    # build-plan popup gets clean, id-accurate updates from plan.py's OUTPUT (PLAN_ADD/PLAN_UPDATE/
    # PLAN_PROGRESS) rather than parsing fragile shell command lines (which break on `&&` chaining
    # or quoting -- a real bug that showed the whole spec + a shell fragment as one popup item).
    body = output.split("Output:", 1)[-1]
    todo_lines = [
        ln.strip()
        for ln in body.splitlines()
        if ln.strip().startswith(("PLAN_ADD", "PLAN_UPDATE", "PLAN_PROGRESS", "MEMORY_NOTE"))
    ]
    if todo_lines:
        return "\n".join(todo_lines)
    match = re.search(r"Process exited with code (-?\d+)", output)
    if match is None:
        return None  # not a shell/exec output (e.g. an apply_patch/view_image result) -- stay silent
    if match.group(1) == "0":
        # A successful command: surface its output too (truncated), so the feed shows what each step
        # produced, not just the command. Rendered as a compact collapsible block by the GUI.
        return _output_block(output.split("Output:", 1)[-1])
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


def _todo_brief() -> str:
    """The instruction (shared by both backends): read the requirements, then author + work a build
    plan (progress points) via plan.py that adds up to meet them."""
    py = sys.executable
    return (
        "\n\nYour workspace has two things. REQUIREMENTS.json lists what the finished game MUST do -- "
        "the acceptance criteria you will be independently audited against (you don't edit it). "
        "PLAN.json is your own live BUILD PLAN: the concrete PARTS OF THE PROGRAM to build, which you "
        "author and work like a coding agent's todo list, and which must ADD UP to satisfy every "
        "requirement. Drive the plan ONLY through the plan.py tool, run with this exact interpreter: "
        f"first read the requirements (`cat REQUIREMENTS.json`), then plan the build -- add each part "
        f"with `{py} plan.py add \"<build task>\"` (e.g. \"tile world generation\", \"jump/gravity "
        "physics\", \"gem pickup + counter\", \"exit gate\", \"HUD + win banner\") until the plan "
        f"covers every requirement. Then build: `{py} plan.py start <id>` when you begin a task and "
        f"`{py} plan.py done <id>` when it's built and working. Keep notes with `{py} plan.py note "
        "\"...\"`. Do NOT finish until every plan task is done AND every requirement is genuinely met."
    )


def _todo_reminder(open_todo: list[dict] | None) -> str:
    """A short 'these build tasks are still open' preface for a repair message, so the agent resumes
    with its unfinished plan in context (its PLAN.json/MEMORY.md are still on disk)."""
    if not open_todo:
        return ""
    lines = "\n".join(f"  - [{it.get('id')}] {it.get('task')}" for it in open_todo[:20])
    return (
        "Your PLAN.json still has unfinished build tasks (not done yet):\n"
        f"{lines}\nYour PLAN.json, REQUIREMENTS.json and MEMORY.md are still on disk -- keep driving "
        "the plan with plan.py, finish these, and make sure every requirement is met.\n\n"
    )


def _repair_message(
    *,
    run_error: str | None,
    sanity_error: str | None,
    audit_findings: str | None = None,
    open_todo: list[dict] | None = None,
) -> str:
    prefix = _todo_reminder(open_todo)
    if run_error is not None:
        # Point the agent at the concrete fix for a known crash-inducing tool misuse: calling the
        # `view_image` tool with an absolute path fails with "manifest path must be relative" and
        # crashes the whole attempt. The raw error alone wasn't enough for the agent to self-correct
        # (it repeated the mistake on repair in a real run), so spell out the fix.
        hint = ""
        if "view_image" in run_error and "must be relative" in run_error:
            hint = (
                " The crash was a `view_image` call with an ABSOLUTE path -- that tool needs a "
                "workspace-RELATIVE path. Write your review frames into the workspace and call it "
                'like `view_image("review_start.png")`, never with an absolute /private/var/... path.'
            )
        return prefix + (
            f"Your previous attempt in this same workspace did not finish cleanly: {run_error}.{hint} "
            "Any files you already produced are still on disk -- inspect them (ls, cat), pick up "
            "from where you left off, and finish producing valid scene.json/metrics.json/"
            "replay.json/render.png/replay.gif."
        )
    if sanity_error is not None:
        return prefix + (
            "A previous attempt in this same workspace did not pass an independent outer check "
            f"using the real, unmodified schema: {sanity_error}. Your existing files from that "
            "attempt are still on disk -- inspect them, fix the specific problem described, and "
            "re-run to produce all five artifact files again."
        )
    return prefix + (
        "Your previous attempt in this same workspace produced valid artifacts but an independent "
        "reviewer found that it does not faithfully implement the spec -- it fakes a requirement "
        "rather than really doing it:\n"
        f"{audit_findings}\n"
        "Your existing files are still on disk -- inspect them and fix the simulation logic so the "
        "flagged requirement is genuinely implemented (not just made to look right in the render), "
        "then re-run to produce all five artifacts again. Do not paper over it by editing the "
        "reported success value or a threshold."
    )


# A model-API rate limit (esp. OpenAI's tokens-per-minute cap) is usually transient -- the error
# even carries a "try again in Xms" hint -- so it should be waited out and retried, NOT treated as a
# fatal run failure that burns a repair attempt (which just re-hits the still-saturated limit). These
# retries are separate from and do not consume the outer repair budget.
_MAX_RATE_LIMIT_RETRIES = int(os.environ.get("INFINIENV_SANDBOX_RATE_LIMIT_RETRIES", "6"))


def _is_rate_limit_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "rate limit" in s or "rate_limit" in s or "tokens per min" in s or " tpm" in s or "429" in s


def _rate_limit_backoff_seconds(message: str, attempt: int) -> float:
    """How long to wait before retrying a rate-limited model call. Prefer the API's own
    'try again in Xs/Xms' hint (floored to a couple seconds so a sub-second hint still lets a
    per-minute window meaningfully drain), else exponential backoff; capped at 30s."""
    import re

    m = re.search(r"try again in ([\d.]+)\s*(ms|s)\b", message, re.IGNORECASE)
    if m:
        secs = float(m.group(1)) / 1000.0 if m.group(2).lower() == "ms" else float(m.group(1))
        return max(2.0, min(secs + 1.0, 30.0))
    return min(2.0 * (2**attempt), 30.0)


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

    # Best-effort prompt enrichment: expand the user's raw prompt into a fuller, buildable spec
    # before handing it to the agent. Never fatal -- degrades to the original prompt on any
    # failure (see sandbox/prompt_refiner.py). The original and the refined text are both recorded
    # in metrics.json below for transparency.
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

    # Derive an independent requirements checklist from the (refined) prompt and seed it into the
    # workspace as the agent's live TODO (+ MEMORY.md + the todo.py tool). This makes fidelity to the
    # prompt a per-item, tracked contract the agent works through via tool calls, the auditor
    # enforces, and the run's metrics/GUI surface. Best-effort: no key/failure -> empty checklist,
    # the agent self-derives its TODO (see sandbox_agent.md / seed_todo).
    from infinienv.sandbox.checklist import build_checklist
    from infinienv.sandbox.workspace import open_plan_items, read_plan, read_requirements, seed_workspace

    stage("Deriving the requirements from the prompt...")
    checklist_result = build_checklist(prompt)
    # REQUIREMENTS.json = the acceptance criteria (the auditor's contract). PLAN.json starts empty --
    # the agent authors its own build plan (the parts of the program to build) that must add up to
    # meet these requirements; that plan is what the GUI's live goals popup shows.
    seed_workspace(workspace_dir, checklist_result.items)
    if checklist_result.items:
        stage(f"Requirements derived ({len(checklist_result.items)}); the agent will plan a build to meet them.")
        for _it in checklist_result.items:
            stage(f"REQ_SEED {_it['id']}: {_it['requirement']}")
    else:
        stage(f"Requirements not pre-derived ({checklist_result.note}); the agent will derive them itself.")

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
    audited = False
    audit_passed = True
    audit_findings: str | None = None
    audit_note: str | None = None
    missing: list[str] = list(ARTIFACT_FILES)
    repair_history: list[dict] = []
    playable_env: bool | None = None
    message = f"Seed: {seed}\nTask: {prompt}\nAssets mode: {assets_mode}\n{_interpreter_briefing()}{_todo_brief()}"

    try:
        for attempt in range(max_repair_attempts + 1):
            stage(
                f"Running sandbox agent (attempt {attempt + 1}/{max_repair_attempts + 1})..."
                if attempt == 0
                else f"Repairing against the outer sanity check (attempt {attempt + 1}/{max_repair_attempts + 1})..."
            )
            agent_summary = None
            run_error = None
            for rl in range(_MAX_RATE_LIMIT_RETRIES + 1):
                try:
                    streamed = Runner.run_streamed(agent, message, run_config=run_config, max_turns=max_turns)
                    async for event in streamed.stream_events():
                        narration = _describe_stream_event(event)
                        if narration:
                            stage(narration)
                    agent_summary = streamed.final_output
                    run_error = None
                    break
                except Exception as exc:
                    # A transient model-API rate limit is waited out and retried WITHOUT consuming a
                    # repair attempt (retrying immediately just re-hits the still-saturated limit).
                    if _is_rate_limit_error(exc) and rl < _MAX_RATE_LIMIT_RETRIES:
                        wait = _rate_limit_backoff_seconds(str(exc), rl)
                        stage(
                            f"Model API rate limit hit; waiting {wait:.0f}s and retrying "
                            "(not counted as a repair attempt)..."
                        )
                        await asyncio.sleep(wait)
                        continue
                    # Any other failure (or an exhausted rate-limit budget) is deliberately not
                    # re-raised: the agent not finishing cleanly (e.g. hitting max_turns) doesn't mean
                    # nothing was produced -- whatever partial artifacts/workspace state exist are
                    # still worth extracting, syncing, and sanity-checking. It becomes a repair-loop
                    # attempt like any other, not a crash.
                    agent_summary = None
                    run_error = str(exc)
                    break

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

            # The acceptance criteria (auditor's contract) + the agent's build plan (as it left it
            # this attempt), read from the synced workspace -- for the audit, repair re-injection,
            # and metrics.
            requirements = read_requirements(workspace_dir)
            plan = read_plan(workspace_dir)
            open_todo = open_plan_items(plan)

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

            # Only worth the independent semantic audit once the artifacts are mechanically sound;
            # a malformed run already needs repair for a more basic reason. audited=False means the
            # auditor couldn't run (no key/disabled/error) -- never blocks, audit_passed stays True.
            audited = False
            audit_passed = True
            audit_findings = None
            audit_note = None
            if run_error is None and sane and not missing:
                stage("Independent reviewer auditing the run for faithfulness to the spec...")
                audit = audit_run(out_dir, prompt, checklist=requirements)
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
                message = _repair_message(
                    run_error=None, sanity_error=None, audit_findings=audit_findings, open_todo=open_todo
                )
            else:
                message = _repair_message(
                    run_error=run_error, sanity_error=sanity_error, open_todo=open_todo
                )

        # The make_env contract: can an external controller (a vision policy) drive this game?
        # Cheap smoke check in the same live session -- records the fact in metrics rather than
        # discovering a missing/broken interface only at faithful-play time. Best-effort.
        playable_env = None
        if run_error is None and "scene.json" in artifact_paths:
            try:
                probe = await session.exec(
                    sys.executable,
                    "-c",
                    "from run_scene import make_env; e=make_env(); e.reset(); e.step(e.actions[0])",
                    timeout=120,
                )
                playable_env = bool(probe.ok)
            except Exception:
                playable_env = None
    finally:
        await session.aclose()

    success = not missing and sane and run_error is None and audit_passed

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
            # The acceptance criteria (auditor's contract) and the agent's build plan (its progress
            # points). Separate: requirements = "true to the prompt"; build_plan = the parts the agent
            # built to meet them (shown live in the GUI's goals popup).
            "requirements": read_requirements(workspace_dir),
            "requirements_note": checklist_result.note,
            "build_plan": read_plan(workspace_dir),
            # The real deterministic validator's verdict on the sandbox scene (geometry enforced by
            # the outer check, the rest recorded -- see workspace.deterministic_validation_summary).
            "deterministic_validation": deterministic_validation_summary(out_dir),
            # Whether run_scene.make_env() is importable + drivable -- i.e. whether a vision policy
            # can faithfully play this world (see sandbox/vision_runner.py). None => not checked.
            "playable_env": playable_env,
            "missing_artifacts": missing,
            "repair_attempts": len(repair_history) - 1,
            "repair_history": repair_history,
            # Prompt-enrichment provenance: what the user typed vs. what the agent was actually
            # given, so a reviewer always sees the exact handoff (see sandbox/prompt_refiner.py).
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
    refine_prompt: bool = True,
    backend: str | None = None,
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
    `artifacts/writer.py::resolve_out_dir`). `refine_prompt` (default on) runs a best-effort LLM
    pass to expand the raw prompt into a fuller spec before handing it to the agent -- see
    `sandbox/prompt_refiner.py`; degrades to the original prompt on any failure.

    The agent runtime is selected by `backend`: `openai` (OpenAI Agents SDK, this module) or
    `claude` (Anthropic's Claude Agent SDK, `sandbox/claude_runner.py`). When `backend` is None
    (the default), it falls back to `INFINIENV_SANDBOX_BACKEND`, then to `claude` (the default
    runtime) -- so the CLI/GUI can pass an explicit per-run choice while the env var stays the
    default for anyone who doesn't.
    Selecting the runtime this way (rather than a new `--sandbox` sub-mode) keeps `--sandbox`'s
    meaning stable: the two backends are interchangeable -- same workspace copy, same five
    artifacts, same outer sanity check and repair loop, same `metrics.json` shape (only `provider`/`model`
    differs).
    """
    attempts = (
        DEFAULT_SANDBOX_MAX_REPAIR_ATTEMPTS if max_repair_attempts is None else max_repair_attempts
    )
    backend = (backend or os.environ.get("INFINIENV_SANDBOX_BACKEND", "claude")).strip().lower()
    if backend == "claude":
        from infinienv.sandbox import claude_runner

        model = model or os.environ.get(
            "INFINIENV_SANDBOX_MODEL", claude_runner.DEFAULT_SANDBOX_CLAUDE_MODEL
        )
        return asyncio.run(
            claude_runner._run_async(
                prompt,
                seed,
                out_dir,
                model=model,
                max_turns=max_turns,
                max_repair_attempts=attempts,
                assets_mode=assets_mode,
                require_runs_dir=require_runs_dir,
                refine_prompt=refine_prompt,
                on_stage=on_stage,
            )
        )

    model = model or os.environ.get("INFINIENV_SANDBOX_MODEL", DEFAULT_SANDBOX_MODEL)
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
            refine_prompt=refine_prompt,
            on_stage=on_stage,
        )
    )
