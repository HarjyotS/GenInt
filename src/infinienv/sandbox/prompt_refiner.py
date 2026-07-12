"""Best-effort enrichment of a user's raw prompt into a fuller game spec before the sandbox handoff.

A one-line prompt leaves the expected feature set implicit ("mario-style" without saying jump/
gravity/pipes; "procedural cave" without saying branching/uneven), and the sandbox agent tends to
under-build against it. This runs a single semantic LLM call to expand the prompt into a concrete,
buildable spec (win/lose conditions, expected mechanics and how they behave, level structure,
visual style) that's handed to the agent instead of the bare prompt. It's pure semantic generation
-- it improves an instruction, runs no code, touches no validator-wins guarantee (and only ever
runs on the already-disclosed sandbox path). See `llm/prompts/prompt_refiner.md` for the system
prompt and CLAUDE.md's sandbox section for the design.

Deliberately best-effort and never fatal, the same posture as sandbox live narration: if there's
no key, the `openai` package is missing, the call fails, or the model returns nothing usable, the
run proceeds on the original prompt with the reason recorded -- refinement is an enhancement layer,
not a dependency.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources

# Default to the same model the sandbox agent uses, overridable independently -- prompt refinement
# is a cheap text task, so a smaller/faster model can be substituted here without touching the
# agent's model.
_DEFAULT_REFINER_MODEL = "gpt-5.6-sol"


@dataclass
class RefineResult:
    """What the refiner produced. `refined` is always safe to hand to the agent -- it equals
    `original` when refinement was skipped or failed. `note` explains why it was skipped (None on
    success), so a run's metrics can record exactly what happened."""

    original: str
    refined: str
    used_refinement: bool
    note: str | None = None


def _load_refiner_prompt() -> str:
    return resources.files("infinienv.llm.prompts").joinpath("prompt_refiner.md").read_text()


def refine_prompt(prompt: str, *, model: str | None = None) -> RefineResult:
    """Expand `prompt` into a fuller, intent-preserving game spec via one LLM call. Never raises:
    on any failure (no key, missing package, API error, empty output) it returns the original
    prompt unchanged with `used_refinement=False` and a `note` describing why."""
    if not os.environ.get("OPENAI_API_KEY"):
        return RefineResult(prompt, prompt, False, "no OPENAI_API_KEY; used original prompt")
    try:
        from openai import OpenAI
    except ImportError:
        return RefineResult(prompt, prompt, False, "openai package not installed; used original prompt")

    model = model or os.environ.get("INFINIENV_REFINER_MODEL", _DEFAULT_REFINER_MODEL)
    try:
        client = OpenAI()
        response = client.responses.create(
            model=model,
            instructions=_load_refiner_prompt(),
            input=prompt,
        )
        refined = (response.output_text or "").strip()
    except Exception as exc:  # best-effort: any API/SDK failure degrades to the original prompt
        return RefineResult(prompt, prompt, False, f"refinement call failed ({exc}); used original prompt")

    if not refined:
        return RefineResult(prompt, prompt, False, "refiner returned empty output; used original prompt")
    return RefineResult(prompt, refined, True, None)
