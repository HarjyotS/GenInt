from infinienv.generation.mutation import generate_mutations
from infinienv.generation.templates import kitchen_delivery
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
