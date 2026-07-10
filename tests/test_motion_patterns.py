"""Generic reusable motion-pattern functions for sandbox-authored simulations
(engine/motion_patterns.py)."""

import math

import pytest

from infinienv.engine.motion_patterns import patrol, pulse_cycle, pursue


def test_patrol_returns_base_at_phase_zero():
    assert patrol(0.0, base=10.0, amplitude=3.0, period=4.0) == pytest.approx(10.0)


def test_patrol_reaches_the_amplitude_extremes():
    # a quarter period in: sin(pi/2) == 1
    assert patrol(1.0, base=10.0, amplitude=3.0, period=4.0) == pytest.approx(13.0)
    # three-quarters period in: sin(3pi/2) == -1
    assert patrol(3.0, base=10.0, amplitude=3.0, period=4.0) == pytest.approx(7.0)


def test_patrol_is_periodic():
    v1 = patrol(1.3, base=5.0, amplitude=2.0, period=4.0)
    v2 = patrol(1.3 + 4.0, base=5.0, amplitude=2.0, period=4.0)
    assert v1 == pytest.approx(v2)


def test_patrol_rejects_nonpositive_period():
    with pytest.raises(ValueError):
        patrol(0.0, base=0.0, amplitude=1.0, period=0.0)


def test_pulse_cycle_starts_at_zero_extent():
    assert pulse_cycle(0.0, period=4.0) == pytest.approx(0.0)


def test_pulse_cycle_ramps_up_then_holds_then_ramps_down_then_idles():
    period = 4.0
    # rise band is [0, 0.25) of the cycle -- halfway through rise
    assert pulse_cycle(period * 0.125, period, rise=0.25, hold=0.25, fall=0.25) == pytest.approx(0.5)
    # hold band is [0.25, 0.5)
    assert pulse_cycle(period * 0.375, period, rise=0.25, hold=0.25, fall=0.25) == pytest.approx(1.0)
    # fall band is [0.5, 0.75) -- halfway through fall
    assert pulse_cycle(period * 0.625, period, rise=0.25, hold=0.25, fall=0.25) == pytest.approx(0.5)
    # idle band is [0.75, 1.0)
    assert pulse_cycle(period * 0.9, period, rise=0.25, hold=0.25, fall=0.25) == pytest.approx(0.0)


def test_pulse_cycle_stays_within_unit_range_across_a_full_period():
    period = 3.0
    for i in range(60):
        t = i * period / 60.0
        extent = pulse_cycle(t, period, rise=0.2, hold=0.3, fall=0.3)
        assert 0.0 <= extent <= 1.0


def test_pulse_cycle_rejects_bad_parameters():
    with pytest.raises(ValueError):
        pulse_cycle(0.0, period=0.0)
    with pytest.raises(ValueError):
        pulse_cycle(0.0, period=1.0, rise=-0.1)
    with pytest.raises(ValueError):
        pulse_cycle(0.0, period=1.0, rise=0.6, hold=0.6, fall=0.2)


def test_pursue_steps_toward_target_at_capped_speed():
    new_pos = pursue((0.0, 0.0), (10.0, 0.0), speed=5.0, dt=1.0)
    assert new_pos == pytest.approx((5.0, 0.0))


def test_pursue_snaps_to_target_instead_of_overshooting():
    new_pos = pursue((0.0, 0.0), (3.0, 0.0), speed=5.0, dt=1.0)
    assert new_pos == (3.0, 0.0)


def test_pursue_handles_already_at_target():
    assert pursue((2.0, 2.0), (2.0, 2.0), speed=5.0, dt=1.0) == (2.0, 2.0)


def test_pursue_moves_along_the_correct_diagonal_direction():
    new_pos = pursue((0.0, 0.0), (10.0, 10.0), speed=math.sqrt(2), dt=1.0)
    assert new_pos == pytest.approx((1.0, 1.0))


def test_pursue_rejects_negative_speed_or_dt():
    with pytest.raises(ValueError):
        pursue((0.0, 0.0), (1.0, 0.0), speed=-1.0, dt=1.0)
    with pytest.raises(ValueError):
        pursue((0.0, 0.0), (1.0, 0.0), speed=1.0, dt=-1.0)
