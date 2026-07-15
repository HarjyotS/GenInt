"""Decompose a (refined) sandbox prompt into an explicit, individually-verifiable requirements
checklist -- the source of the agent's TODO.

Following agent-harness best practice ("the job of a harness is to provide context to the model at
every step; a todo list tracks progress across a long run"), this turns the spec into concrete
pass/fail items so the run's fidelity to the prompt becomes an enforced, per-item contract rather
than a vibe. Crucially it is derived **independently of the builder** (a separate LLM pass, like the
prompt refiner), so the agent can't silently drop a hard requirement it doesn't want to build -- the
item is on its TODO regardless, and both its own self-check and the auditor hold it to each one.

Best-effort / never fatal, the same posture as `prompt_refiner.py` and `auditor.py`: no key, missing
`openai`, an API error, or unparseable output all yield an empty checklist (the agent then
self-derives its TODO from the prompt per `sandbox_agent.md`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from importlib import resources

# A cheap text task; default to the same tier as the refiner, overridable independently.
_DEFAULT_CHECKLIST_MODEL = "gpt-5.6-sol"
_MAX_ITEMS = 24


@dataclass
class ChecklistResult:
    """`items` is always safe to use -- empty when the pass was skipped/failed (`used=False`), in
    which case the agent self-derives its TODO from the prompt. `note` explains a skip (None on
    success)."""

    items: list[dict] = field(default_factory=list)
    used: bool = False
    note: str | None = None


def _load_prompt() -> str:
    return resources.files("infinienv.llm.prompts").joinpath("checklist_generator.md").read_text()


def _parse_items(raw: str) -> list[dict]:
    """Parse the model's reply into a list of {id, requirement, how_to_verify}. Tolerant of code
    fences / surrounding prose; returns [] if nothing usable parses."""
    text = (raw or "").strip()
    if "```" in text:  # strip a ```json ... ``` fence if present
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
    if not text.startswith("["):  # find the first JSON array in the text
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    items: list[dict] = []
    for i, entry in enumerate(data[:_MAX_ITEMS], start=1):
        if not isinstance(entry, dict):
            continue
        requirement = str(entry.get("requirement") or entry.get("item") or "").strip()
        if not requirement:
            continue
        items.append(
            {
                "id": str(entry.get("id") or f"r{i}").strip(),
                "requirement": requirement,
                "how_to_verify": str(entry.get("how_to_verify") or "").strip(),
            }
        )
    return items


def build_checklist(refined_prompt: str, *, model: str | None = None) -> ChecklistResult:
    """Turn `refined_prompt` into an itemized requirements checklist via one LLM call. Never raises;
    any failure returns an empty checklist with a `note`."""
    if not os.environ.get("OPENAI_API_KEY"):
        return ChecklistResult([], False, "no OPENAI_API_KEY; agent will self-derive its TODO")
    try:
        from openai import OpenAI
    except ImportError:
        return ChecklistResult([], False, "openai package not installed; agent will self-derive its TODO")

    model = model or os.environ.get("INFINIENV_CHECKLIST_MODEL", _DEFAULT_CHECKLIST_MODEL)
    try:
        client = OpenAI()
        response = client.responses.create(
            model=model,
            instructions=_load_prompt(),
            input=refined_prompt,
        )
        items = _parse_items(response.output_text or "")
    except Exception as exc:  # best-effort: any API/SDK failure -> empty checklist
        return ChecklistResult([], False, f"checklist call failed ({exc}); agent will self-derive its TODO")

    if not items:
        return ChecklistResult([], False, "checklist generator returned no usable items")
    return ChecklistResult(items, True, None)
