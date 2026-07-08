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
