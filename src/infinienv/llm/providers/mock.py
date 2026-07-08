"""Deterministic, no-API-key provider. Always produces a valid, solvable scene."""

from __future__ import annotations

from infinienv.generation.templates import generate_from_template
from infinienv.schema.scene_schema import SceneSpec
from infinienv.validation.errors import ValidationIssue


class MockProvider:
    name = "mock"

    def generate_scene(self, prompt: str, seed: int) -> SceneSpec:
        return generate_from_template(prompt, seed)

    def repair_scene(
        self,
        prompt: str,
        scene: SceneSpec,
        validation_errors: list[ValidationIssue],
        seed: int,
    ) -> SceneSpec:
        # The mock provider's templates are valid by construction, so "repair" just
        # regenerates deterministically from a derived seed to avoid infinite loops
        # on a bad hand-authored input.
        return generate_from_template(prompt, seed + 1)
