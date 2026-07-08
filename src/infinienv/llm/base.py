"""Provider abstraction shared by every scene-generation backend."""

from __future__ import annotations

from typing import Protocol

from infinienv.schema.scene_schema import SceneSpec
from infinienv.validation.errors import ValidationIssue


class SceneProvider(Protocol):
    name: str

    def generate_scene(self, prompt: str, seed: int) -> SceneSpec:
        """Propose a SceneSpec for `prompt`. May be invalid; the caller validates it."""
        ...

    def repair_scene(
        self,
        prompt: str,
        scene: SceneSpec,
        validation_errors: list[ValidationIssue],
        seed: int,
    ) -> SceneSpec:
        """Return a corrected SceneSpec given the previous attempt and validator feedback."""
        ...


class ProviderError(Exception):
    """Raised when a provider cannot produce usable output (missing key, API failure, bad JSON)."""
