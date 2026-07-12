"""Generic procedural terrain generation for sandbox-authored simulations
(engine/level_generation.py)."""

import pytest

from infinienv.engine.level_generation import (
    carve_gaps,
    generate_organic_region,
    generate_platform_layout,
    generate_terrain_profile,
    region_is_connected,
    scatter_on_supports,
)


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


# --- side-view seeded generators (the anti-cheese capability) ---------------------------------


def test_terrain_profile_deterministic_and_varies_by_seed():
    a = generate_terrain_profile(40, seed=1, base_row=10, min_row=4, max_row=14)
    assert a == generate_terrain_profile(40, seed=1, base_row=10, min_row=4, max_row=14)  # deterministic
    b = generate_terrain_profile(40, seed=2, base_row=10, min_row=4, max_row=14)
    assert a != b  # the anti-cheese property: a different seed is a different level


def test_terrain_profile_respects_bounds_and_max_step():
    heights = generate_terrain_profile(60, seed=5, base_row=10, min_row=4, max_row=14, max_step=2)
    assert len(heights) == 60
    assert all(4 <= h <= 14 for h in heights)
    assert all(abs(b - a) <= 2 for a, b in zip(heights, heights[1:]))  # never jumps more than max_step


def test_carve_gaps_deterministic_avoids_margins_and_varies():
    g1 = carve_gaps(40, seed=1, count=3, margin=3)
    assert g1 == carve_gaps(40, seed=1, count=3, margin=3)
    assert all(3 <= c <= 40 - 1 - 3 for c in g1)  # never within the margin of either end
    assert carve_gaps(40, seed=1, count=3) != carve_gaps(40, seed=9, count=3)


def test_platform_layout_shapes_deterministic_and_varies():
    p1, l1 = generate_platform_layout(60, 22, seed=1, rows=5)
    p2, l2 = generate_platform_layout(60, 22, seed=1, rows=5)
    assert (p1, l1) == (p2, l2)  # deterministic
    assert all(len(p) == 3 and p[0] <= p[2] for p in p1)  # (left, row, right)
    assert all(len(l) == 3 and l[1] < l[2] for l in l1)  # (col, top_row, bottom_row)
    p3, _ = generate_platform_layout(60, 22, seed=2, rows=5)
    assert p1 != p3  # different seed -> different layout


def test_platform_layout_every_adjacent_level_is_ladder_connected():
    platforms, ladders = generate_platform_layout(60, 22, seed=7, rows=5)
    rows_used = sorted({row for _, row, _ in platforms})
    # each ladder's endpoints land on a platform of both the top and bottom level it joins
    for col, top, bottom in ladders:
        assert any(l <= col <= r and row == top for l, row, r in platforms)
        assert any(l <= col <= r and row == bottom for l, row, r in platforms)
    # every adjacent pair of occupied levels has at least one ladder joining them (connectivity)
    joined = {(t, b) for _, t, b in ladders}
    for a, b in zip(rows_used, rows_used[1:]):
        assert (a, b) in joined


def test_scatter_on_supports_spacing_avoids_pits_and_ends():
    supports = [(0, 10, 20)]  # one platform spanning cols 0..20 on row 10
    pits = frozenset({5, 6})
    placed = scatter_on_supports(supports, seed=3, count=4, spacing=3, avoid=pits)
    cols = [c for c, _ in placed]
    assert all(r == 10 for _, r in placed)  # sits on the support row
    assert all(c not in pits for c in cols)  # never on a pit column
    assert 0 not in cols and 20 not in cols  # spawn/exit ends kept clear
    assert all(abs(a - b) >= 3 for i, a in enumerate(cols) for b in cols[i + 1:])  # spacing respected
    assert scatter_on_supports(supports, seed=3, count=4, spacing=3, avoid=pits) == placed  # deterministic


def test_side_view_generators_reject_bad_parameters():
    with pytest.raises(ValueError):
        generate_terrain_profile(0, seed=1, base_row=5, min_row=0, max_row=10)
    with pytest.raises(ValueError):
        generate_terrain_profile(10, seed=1, base_row=99, min_row=0, max_row=10)  # base outside range
    with pytest.raises(ValueError):
        generate_platform_layout(60, 22, seed=1, rows=1)  # need >= 2 levels to connect
