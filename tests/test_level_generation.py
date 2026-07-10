"""Generic procedural terrain generation for sandbox-authored simulations
(engine/level_generation.py)."""

import pytest

from infinienv.engine.level_generation import generate_organic_region, region_is_connected


def test_generate_organic_region_is_deterministic_for_a_given_seed():
    a = generate_organic_region(20, 20, (0, 0), steps=200, seed=7)
    b = generate_organic_region(20, 20, (0, 0), steps=200, seed=7)
    assert a == b


def test_generate_organic_region_different_seeds_usually_differ():
    a = generate_organic_region(20, 20, (0, 0), steps=200, seed=1)
    b = generate_organic_region(20, 20, (0, 0), steps=200, seed=2)
    assert a != b


def test_generate_organic_region_includes_start():
    region = generate_organic_region(20, 20, (3, 4), steps=50, seed=1)
    assert (3, 4) in region


def test_generate_organic_region_stays_within_bounds():
    region = generate_organic_region(10, 10, (0, 0), steps=500, seed=1)
    assert all(0 <= x < 10 and 0 <= y < 10 for x, y in region)


def test_generate_organic_region_is_always_connected_by_construction():
    region = generate_organic_region(25, 25, (12, 12), steps=400, seed=3)
    assert region_is_connected(region, (12, 12))


def test_generate_organic_region_produces_more_cells_with_more_steps():
    small = generate_organic_region(30, 30, (0, 0), steps=20, seed=1)
    large = generate_organic_region(30, 30, (0, 0), steps=300, seed=1)
    assert len(large) >= len(small)


def test_generate_organic_region_zero_branch_chance_can_still_branch_only_at_start():
    # With branch_chance=0, only the initial single walker ever moves -- still a valid, connected,
    # single-corridor-style region (branching is optional, not assumed).
    region = generate_organic_region(20, 20, (0, 0), steps=50, seed=1, branch_chance=0.0)
    assert region_is_connected(region, (0, 0))


def test_generate_organic_region_rejects_bad_parameters():
    with pytest.raises(ValueError):
        generate_organic_region(10, 10, (0, 0), steps=-1, seed=1)
    with pytest.raises(ValueError):
        generate_organic_region(10, 10, (0, 0), steps=10, seed=1, branch_chance=1.5)
    with pytest.raises(ValueError):
        generate_organic_region(10, 10, (0, 0), steps=10, seed=1, max_walkers=0)
    with pytest.raises(ValueError):
        generate_organic_region(10, 10, (99, 99), steps=10, seed=1)


def test_region_is_connected_true_for_a_simple_line():
    region = {(0, 0), (1, 0), (2, 0)}
    assert region_is_connected(region, (0, 0)) is True


def test_region_is_connected_false_for_a_disconnected_island():
    region = {(0, 0), (1, 0), (5, 5)}  # (5,5) is not reachable from (0,0)
    assert region_is_connected(region, (0, 0)) is False


def test_region_is_connected_false_when_start_not_in_region():
    assert region_is_connected({(1, 1)}, (0, 0)) is False
