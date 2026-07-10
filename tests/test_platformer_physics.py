"""Generic grounded-character physics for sandbox-authored simulations
(engine/platformer_physics.py)."""

import pytest

from infinienv.engine.platformer_physics import clamp_to_bounds, climb_step, integrate_grounded_2d


def test_integrate_grounded_2d_applies_gravity_and_moves_by_velocity():
    (x, y), (vx, vy), grounded = integrate_grounded_2d(
        (0.0, 0.0), (5.0, 0.0), gravity=10.0, dt=1.0, ground_y=1000.0
    )
    assert x == pytest.approx(5.0)
    assert vy == pytest.approx(10.0)
    assert y == pytest.approx(10.0)  # y moved by the post-gravity vy this step
    assert grounded is False


def test_integrate_grounded_2d_clamps_to_ground_and_zeroes_vertical_velocity():
    (x, y), (vx, vy), grounded = integrate_grounded_2d(
        (0.0, 95.0), (0.0, 50.0), gravity=10.0, dt=1.0, ground_y=100.0
    )
    assert y == pytest.approx(100.0)
    assert vy == pytest.approx(0.0)
    assert grounded is True


def test_integrate_grounded_2d_stays_grounded_when_already_at_ground_with_no_upward_velocity():
    (x, y), (vx, vy), grounded = integrate_grounded_2d(
        (0.0, 100.0), (3.0, 0.0), gravity=10.0, dt=0.1, ground_y=100.0
    )
    assert grounded is True
    assert y == pytest.approx(100.0)


def test_integrate_grounded_2d_clamps_to_world_bounds():
    (x, y), _, _ = integrate_grounded_2d(
        (995.0, 50.0),
        (100.0, 0.0),
        gravity=0.0,
        dt=1.0,
        ground_y=1000.0,
        bounds=(0.0, 0.0, 1000.0, 1000.0),
    )
    assert x == pytest.approx(1000.0)  # would have gone to 1095 without the clamp


def test_integrate_grounded_2d_without_bounds_does_not_clamp_x():
    (x, y), _, _ = integrate_grounded_2d(
        (995.0, 50.0), (100.0, 0.0), gravity=0.0, dt=1.0, ground_y=1000.0
    )
    assert x == pytest.approx(1095.0)


def test_integrate_grounded_2d_rejects_negative_dt():
    with pytest.raises(ValueError):
        integrate_grounded_2d((0.0, 0.0), (0.0, 0.0), gravity=10.0, dt=-1.0, ground_y=100.0)


def test_climb_step_moves_only_y_never_x():
    x0 = 500.0
    new_pos = climb_step((x0, 200.0), 50.0, 1.0, structure_bounds=(480.0, 0.0, 520.0, 400.0))
    assert new_pos[0] == x0
    assert new_pos[1] == pytest.approx(150.0)


def test_climb_step_clamps_to_the_structures_top():
    new_pos = climb_step((500.0, 10.0), 50.0, 1.0, structure_bounds=(480.0, 0.0, 520.0, 400.0))
    assert new_pos[1] == pytest.approx(0.0)


def test_climb_step_raises_when_x_is_outside_the_structure():
    with pytest.raises(ValueError, match="outside the structure"):
        climb_step((999.0, 200.0), 50.0, 1.0, structure_bounds=(480.0, 0.0, 520.0, 400.0))


def test_climb_step_rejects_negative_speed_or_dt():
    with pytest.raises(ValueError):
        climb_step((500.0, 200.0), -1.0, 1.0, structure_bounds=(480.0, 0.0, 520.0, 400.0))
    with pytest.raises(ValueError):
        climb_step((500.0, 200.0), 50.0, -1.0, structure_bounds=(480.0, 0.0, 520.0, 400.0))


def test_clamp_to_bounds_leaves_in_range_position_unchanged():
    assert clamp_to_bounds((50.0, 50.0), (0.0, 0.0, 100.0, 100.0)) == (50.0, 50.0)


def test_clamp_to_bounds_clamps_out_of_range_position():
    assert clamp_to_bounds((150.0, -20.0), (0.0, 0.0, 100.0, 100.0)) == (100.0, 0.0)
