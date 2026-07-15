"""Independent faithfulness auditor for sandbox runs -- the systemic anti-cheese layer.

Sandbox mode has no external semantic validator by design (CLAUDE.md section 11): the outer sanity
check verifies only that the artifacts are well-formed (images load, the GIF animates, no teleport),
not that the game's rules are *real*. The only other enforcement is the agent's own self-review --
and the author grading itself is the structural weakness behind every "looks right but fakes the
requirement" cheat this project has hit (cosmetic fog-of-war over a solver that reads ground truth;
"procedural" that's a hardcoded layout; smooth motion with no physics).

This adds an *independent* reviewer: after the outer check passes, a fresh LLM instance -- no shared
context with the author, no incentive to pass -- reads the synced `run_scene.py` (as text, never
executed) plus the trace and the refined prompt, and adversarially hunts for requirements that were
faked rather than implemented. Its verdict feeds the same repair loop the outer check does. It's a
probabilistic reviewer, not a guarantee (it can miss or false-positive), but it separates the author
from the grader and generalizes across cheese categories without a new hand-written check for each.

Deliberately best-effort and never fatal, the same posture as the prompt refiner: no key, missing
package, API error, unparseable output, or a disable flag all yield `audited=False, passed=True` (a
run is never failed because the auditor couldn't run). Runs the OpenAI Responses API directly (it
only reads and judges -- no tools), so it is cross-model whenever the author backend is Claude, and
independent-context even when it's OpenAI. `INFINIENV_SANDBOX_AUDIT=0` disables it;
`INFINIENV_SANDBOX_AUDITOR_MODEL` overrides the model.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources

_DEFAULT_AUDITOR_MODEL = "gpt-5.6-terra"
_MAX_CODE_CHARS = 60000
_MAX_TRACE_CHARS = 6000


@dataclass
class AuditResult:
    """The auditor's verdict. `audited` is False when the auditor couldn't run (no key, disabled,
    error) -- in which case `passed` is True (never block a run on the auditor's own failure) and
    `note` says why. When `audited` is True, `passed` is the real verdict and `findings` lists the
    concrete cheats to feed back to the author on a FAIL."""

    audited: bool
    passed: bool
    findings: str | None = None
    note: str | None = None


def _load_auditor_prompt() -> str:
    return resources.files("infinienv.llm.prompts").joinpath("sandbox_auditor.md").read_text()


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def _truncate_middle(text: str, limit: int) -> str:
    """Keep the head and tail of `text` (so both the initial and final/win state of a trace survive),
    dropping the middle when it's over `limit`."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} chars truncated] ...\n{text[-half:]}"


def _declared_rules(out_dir: str) -> str | None:
    """The agent's declared `rules` block from metrics.json / replay.json, if it wrote one -- the
    auditor's coverage target. Best-effort: any shape or absence is fine."""
    for name in ("metrics.json", "replay.json"):
        raw = _read_text(os.path.join(out_dir, name))
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        rules = data.get("rules") if isinstance(data, dict) else None
        if rules:
            return json.dumps(rules, indent=2)[:4000]
    return None


def _format_checklist(checklist: list[dict] | None) -> str:
    if not checklist:
        return "(no requirements checklist was derived)"
    lines = []
    for it in checklist:
        status = it.get("status", "?")
        vb = it.get("verified_by")
        lines.append(
            f"- [{'x' if status == 'done' else ' '}] ({it.get('id')}) {it.get('requirement')}"
            + (f"  -- author says verified by: {vb}" if vb else "")
        )
    return "\n".join(lines)


def _build_review_input(
    refined_prompt: str, code: str, trace: str | None, rules: str | None, checklist: list[dict] | None
) -> str:
    parts = [
        "## What the game was supposed to be (the spec handed to the author)\n",
        refined_prompt.strip(),
        "\n\n## The requirements checklist (the author's TODO -- every item must be genuinely done)\n",
        "Each item below is a requirement of the spec. Verify BOTH: (a) the checklist is complete "
        "(no requirement of the spec is missing from it), and (b) EACH item the author marked done is "
        "genuinely implemented in the code below -- not faked, not a hollow `verified_by`. A missing "
        "requirement, or a `done` item that the code doesn't really do, is a FAIL naming that item.\n",
        _format_checklist(checklist),
        "\n\n## The author's declared rules (its own claimed invariants), if any\n",
        rules or "(none declared)",
        "\n\n## The code the author actually wrote (run_scene.py)\n```python\n",
        _truncate_middle(code, _MAX_CODE_CHARS),
        "\n```\n\n## A sample of the recorded trace (replay.json)\n```json\n",
        _truncate_middle(trace, _MAX_TRACE_CHARS) if trace else "(no trace available)",
        "\n```\n",
    ]
    return "".join(parts)


def _parse_verdict(raw: str) -> tuple[bool, str | None] | None:
    """Parse the auditor's reply into `(passed, findings)`. Expects JSON
    `{"verdict": "PASS"|"FAIL", "findings": [...]}`, tolerating code fences / surrounding prose.
    Returns None if nothing parseable (caller treats that as "couldn't audit", not a failure)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or "verdict" not in data:
        return None
    passed = str(data["verdict"]).strip().upper() == "PASS"
    findings_raw = data.get("findings") or []
    if isinstance(findings_raw, str):
        findings_list = [findings_raw]
    elif isinstance(findings_raw, list):
        findings_list = [str(f).strip() for f in findings_raw if str(f).strip()]
    else:
        findings_list = []
    findings = "\n".join(f"- {f}" for f in findings_list) if findings_list else None
    return passed, findings


def audit_run(
    out_dir: str, refined_prompt: str, *, model: str | None = None, checklist: list[dict] | None = None
) -> AuditResult:
    """Audit a completed sandbox run for faithfulness to `refined_prompt`. `checklist` is the agent's
    TODO (the per-item requirements + their final status) -- the auditor checks completeness + that
    each `done` item is genuinely implemented. Never raises; on any inability to run, returns
    `audited=False, passed=True` with a `note`."""
    if os.environ.get("INFINIENV_SANDBOX_AUDIT", "1").strip() == "0":
        return AuditResult(False, True, note="auditor disabled via INFINIENV_SANDBOX_AUDIT=0")
    if not os.environ.get("OPENAI_API_KEY"):
        return AuditResult(False, True, note="no OPENAI_API_KEY; audit skipped")
    try:
        from openai import OpenAI
    except ImportError:
        return AuditResult(False, True, note="openai package not installed; audit skipped")

    code = _read_text(os.path.join(out_dir, "sandbox_workspace", "run_scene.py"))
    if not code:
        return AuditResult(False, True, note="no run_scene.py to audit; skipped")
    trace = _read_text(os.path.join(out_dir, "replay.json"))
    rules = _declared_rules(out_dir)
    review_input = _build_review_input(refined_prompt, code, trace, rules, checklist)

    model = model or os.environ.get("INFINIENV_SANDBOX_AUDITOR_MODEL", _DEFAULT_AUDITOR_MODEL)
    instructions = _load_auditor_prompt()
    # A skipped audit silently lets a cheat ship (best-effort by design), so don't give up on the
    # first transient hiccup -- retry the call once before skipping. Verified live: a real run once
    # skipped its audit on a transient error and passed a cheating run; a working auditor catches it.
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            client = OpenAI()
            response = client.responses.create(model=model, instructions=instructions, input=review_input)
            raw = (response.output_text or "").strip()
        except Exception as exc:  # best-effort: any API/SDK failure means "couldn't audit", not "failed"
            last_exc = exc
            continue
        verdict = _parse_verdict(raw)
        if verdict is None:
            return AuditResult(False, True, note="auditor returned unparseable output; skipped")
        passed, findings = verdict
        return AuditResult(True, passed, findings=findings)
    return AuditResult(False, True, note=f"audit call failed after retry ({last_exc}); skipped")
