from infinienv.generation.mutation import STRATEGIES, generate_mutations
from infinienv.generation.templates import kitchen_delivery
from infinienv.llm.base import ProviderError
from infinienv.validation.validator import validate_scene


def test_mutations_are_valid_and_distinct():
    base = kitchen_delivery("kitchen task", seed=1)
    mutations = generate_mutations(base, count=6, seed=99)
    assert len(mutations) == 6
    names = {m.metadata.name for m in mutations}
    assert len(names) == len(mutations)
    for m in mutations:
        result = validate_scene(m)
        assert result.valid, result.errors


def test_theme_reskin_strategy_registered_and_always_valid():
    assert "theme_reskin" in STRATEGIES
    base = kitchen_delivery("kitchen task", seed=1)
    import random

    mutant = STRATEGIES["theme_reskin"](base, random.Random(0))
    assert mutant.metadata.theme != base.metadata.theme
    assert validate_scene(mutant).valid


class _FakeMutationProvider:
    """Always proposes a trivially-valid variant (theme swap) -- exercises the
    provider.propose_mutation duck-typed hook without hitting the real API."""

    name = "fake_mutation_provider"
    calls = 0

    def propose_mutation(self, scene, seed):
        self.calls += 1
        mutant = scene.model_copy(deep=True)
        mutant.metadata.theme = f"fake_theme_{seed}"
        return mutant


class _AlwaysFailsMutationProvider:
    name = "always_fails"

    def propose_mutation(self, scene, seed):
        raise ProviderError("simulated provider failure")


def test_llm_mutations_are_used_and_validated():
    base = kitchen_delivery("kitchen task", seed=1)
    provider = _FakeMutationProvider()
    mutations = generate_mutations(base, count=4, seed=5, provider=provider, llm_fraction=1.0)
    assert len(mutations) == 4
    assert provider.calls >= 4
    assert all(m.metadata.name.endswith("_llm_proposed") for m in mutations)


def test_llm_mutation_failure_never_crashes():
    base = kitchen_delivery("kitchen task", seed=1)
    # llm_fraction=1.0: every attempt tries the (always-failing) provider, never a
    # deterministic strategy -- should degrade to "produced nothing", not raise.
    mutations = generate_mutations(base, count=3, seed=5, provider=_AlwaysFailsMutationProvider(), llm_fraction=1.0)
    assert mutations == []


def test_llm_mutation_failure_still_reaches_count_via_deterministic_fallback():
    base = kitchen_delivery("kitchen task", seed=1)
    # llm_fraction=0.5: some attempts hit the (always-failing) provider and are
    # discarded, others use a deterministic strategy -- count should still be reached.
    mutations = generate_mutations(base, count=3, seed=5, provider=_AlwaysFailsMutationProvider(), llm_fraction=0.5)
    assert len(mutations) == 3
    for m in mutations:
        assert validate_scene(m).valid
