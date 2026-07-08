"""Orchestrates generate -> validate -> repair -> fallback. The LLM proposes; this owns retries."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from pydantic import ValidationError as PydanticValidationError

from infinienv.generation.templates import generate_from_template
from infinienv.llm.base import ProviderError, SceneProvider
from infinienv.schema.scene_schema import SceneSpec
from infinienv.validation.errors import ValidationIssue, ValidationResult
from infinienv.validation.validator import validate_scene

# Providers parse the model's raw output into a SceneSpec themselves, so a
# malformed response surfaces here as a pydantic error, not just ProviderError.
_PROVIDER_FAILURE_EXCEPTIONS = (ProviderError, PydanticValidationError)


def _schema_failure_result(exc: Exception) -> ValidationResult:
    return ValidationResult(valid=False, errors=[ValidationIssue("GENERATION_FAILED", str(exc))])


def _describe_attempt(entry: dict) -> str:
    label = f"attempt {entry.get('attempt')} ({entry.get('stage')})"
    if entry.get("error"):
        return f"{label}: {entry['error']}"
    if entry.get("errors"):
        return f"{label}: " + "; ".join(f"{e['code']}: {e['message']}" for e in entry["errors"])
    return f"{label}: valid={entry.get('valid')}"


class GenerationFailedError(ProviderError):
    """Raised instead of silently falling back to a template when allow_fallback=False."""

    def __init__(self, prompt: str, history: list[dict]):
        self.prompt = prompt
        self.history = history
        # Show every attempt, not just the last -- the last entry is often just a
        # loop-exit note ("no parseable previous scene to repair"), while the actual
        # root cause (e.g. the real pydantic/API error) is earlier in the history.
        trail = "\n  ".join(_describe_attempt(e) for e in history)
        super().__init__(
            f"generation failed for prompt {prompt!r} and fallback is disabled (--no-fallback):\n  {trail}"
        )


DEFAULT_MAX_REPAIR_ATTEMPTS = int(os.environ.get("MAX_REPAIR_ATTEMPTS", "3"))


@dataclass
class GenerationResult:
    scene: SceneSpec
    validation: ValidationResult
    repair_attempts: int
    used_fallback: bool
    history: list[dict] = field(default_factory=list)


def generate_and_validate(
    provider: SceneProvider,
    prompt: str,
    seed: int,
    *,
    max_repair_attempts: int | None = None,
    allow_fallback: bool = True,
) -> GenerationResult:
    max_attempts = DEFAULT_MAX_REPAIR_ATTEMPTS if max_repair_attempts is None else max_repair_attempts

    scene: SceneSpec | None = None
    try:
        scene = provider.generate_scene(prompt, seed)
        validation = validate_scene(scene)
        history = [
            {"attempt": 0, "stage": "initial", "valid": validation.valid, "errors": [e.to_dict() for e in validation.errors]}
        ]
    except _PROVIDER_FAILURE_EXCEPTIONS as exc:
        validation = _schema_failure_result(exc)
        history = [{"attempt": 0, "stage": "initial", "valid": False, "error": str(exc)}]

    attempts = 0
    while not validation.valid and attempts < max_attempts:
        attempts += 1
        if scene is None:
            # The model never produced anything parseable; there is nothing to
            # hand the RepairAgent, so stop the loop and fall back to a template.
            history.append(
                {"attempt": attempts, "stage": "repair", "error": "no parseable previous scene to repair"}
            )
            break
        try:
            scene = provider.repair_scene(prompt, scene, validation.errors, seed)
            validation = validate_scene(scene)
            history.append(
                {
                    "attempt": attempts,
                    "stage": "repair",
                    "valid": validation.valid,
                    "errors": [e.to_dict() for e in validation.errors],
                }
            )
        except _PROVIDER_FAILURE_EXCEPTIONS as exc:
            history.append({"attempt": attempts, "stage": "repair", "error": str(exc)})
            # keep the last valid `scene`/`validation` and try repairing again next iteration

    if not validation.valid and not allow_fallback:
        raise GenerationFailedError(prompt, history)

    used_fallback = False
    if not validation.valid:
        used_fallback = True
        scene = generate_from_template(prompt, seed)
        validation = validate_scene(scene)
        history.append(
            {
                "attempt": "fallback",
                "stage": "template_fallback",
                "valid": validation.valid,
                "errors": [e.to_dict() for e in validation.errors],
            }
        )

    return GenerationResult(
        scene=scene,
        validation=validation,
        repair_attempts=attempts,
        used_fallback=used_fallback,
        history=history,
    )
