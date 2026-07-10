"""Generic phase-driven animation helpers for sandbox-authored simulations (engine/animation.py)."""

import pytest

from infinienv.engine.animation import cycle_variant, oscillate, phase_of


def test_phase_of_starts_at_zero():
    assert phase_of(0.0, period=4.0) == pytest.approx(0.0)


def test_phase_of_wraps_a_full_period_back_to_zero():
    assert phase_of(4.0, period=4.0) == pytest.approx(0.0)


def test_phase_of_halfway_through_period():
    assert phase_of(2.0, period=4.0) == pytest.approx(0.5)


def test_phase_of_applies_offset():
    assert phase_of(0.0, period=4.0, offset=0.25) == pytest.approx(0.25)


def test_phase_of_rejects_nonpositive_period():
    with pytest.raises(ValueError):
        phase_of(0.0, period=0.0)


def test_oscillate_at_phase_zero_and_one_is_the_midpoint():
    assert oscillate(0.0, low=0.0, high=10.0) == pytest.approx(5.0)
    assert oscillate(1.0, low=0.0, high=10.0) == pytest.approx(5.0)


def test_oscillate_reaches_high_at_quarter_phase():
    assert oscillate(0.25, low=0.0, high=10.0) == pytest.approx(10.0)


def test_oscillate_reaches_low_at_three_quarter_phase():
    assert oscillate(0.75, low=0.0, high=10.0) == pytest.approx(0.0)


def test_cycle_variant_picks_the_first_bucket_at_phase_zero():
    assert cycle_variant(0.0, ["idle", "active", "cooldown"]) == "idle"


def test_cycle_variant_picks_the_middle_and_last_bucket():
    assert cycle_variant(0.34, ["idle", "active", "cooldown"]) == "active"
    assert cycle_variant(0.67, ["idle", "active", "cooldown"]) == "cooldown"


def test_cycle_variant_wraps_phase_greater_than_one():
    assert cycle_variant(1.0, ["a", "b"]) == cycle_variant(0.0, ["a", "b"])


def test_cycle_variant_rejects_empty_variants():
    with pytest.raises(ValueError):
        cycle_variant(0.5, [])
