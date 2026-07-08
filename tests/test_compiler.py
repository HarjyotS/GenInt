import pytest

from infinienv.generation.compiler import GenerationFailedError, generate_and_validate
from infinienv.llm.base import ProviderError
from infinienv.schema.scene_schema import SceneSpec


class _AlwaysBrokenProvider:
    """Every proposal fails validation (no goals) and repair never fixes it."""

    name = "always_broken"

    def generate_scene(self, prompt: str, seed: int) -> SceneSpec:
        return SceneSpec.model_validate(
            {
                "metadata": {"name": "x", "prompt": prompt},
                "grid": {"width": 4, "height": 4},
                "agent": {"x": 0, "y": 0},
                "objects": [],
                "walls": [],
                "goals": [],
            }
        )

    def repair_scene(self, prompt, scene, validation_errors, seed):
        return self.generate_scene(prompt, seed)


class _NeverParsesProvider:
    """Raises on the very first attempt, so there is no scene to hand to repair."""

    name = "never_parses"

    def generate_scene(self, prompt: str, seed: int) -> SceneSpec:
        raise ProviderError("model returned garbage")

    def repair_scene(self, prompt, scene, validation_errors, seed):
        raise AssertionError("should never be called")


def test_no_fallback_raises_instead_of_using_template():
    with pytest.raises(GenerationFailedError):
        generate_and_validate(_AlwaysBrokenProvider(), "test", 1, allow_fallback=False)


def test_fallback_still_succeeds_when_allowed():
    result = generate_and_validate(_AlwaysBrokenProvider(), "test", 1, allow_fallback=True)
    assert result.used_fallback
    assert result.validation.valid


def test_generation_failed_error_surfaces_root_cause_not_just_last_entry():
    # Regression test: the error used to only show the last history entry (a generic
    # "no parseable previous scene to repair" loop-exit note) and hid the real root
    # cause of the very first failure.
    with pytest.raises(GenerationFailedError) as excinfo:
        generate_and_validate(_NeverParsesProvider(), "test", 1, allow_fallback=False)
    message = str(excinfo.value)
    assert "model returned garbage" in message
