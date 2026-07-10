"""Generic grid-wall collision for sandbox-authored simulations (engine/grid_collision.py)."""

import pytest

from infinienv.engine.grid_collision import cell_of, move_with_collision, segment_blocked


def test_cell_of_maps_position_to_grid_cell():
    assert cell_of((0.0, 0.0), 32.0) == (0, 0)
    assert cell_of((31.9, 31.9), 32.0) == (0, 0)
    assert cell_of((32.0, 64.0), 32.0) == (1, 2)


def test_segment_blocked_false_when_no_walls_in_the_way():
    assert segment_blocked((0.0, 0.0), (100.0, 0.0), blocked=set(), tile_size=32.0) is False


def test_segment_blocked_true_when_endpoint_is_a_wall_cell():
    blocked = {(3, 0)}
    assert segment_blocked((0.0, 0.0), (100.0, 0.0), blocked, tile_size=32.0) is True


def test_segment_blocked_catches_a_diagonal_cut_through_a_wall_corner():
    # The exact motivating bug: moving from cell (6,7) to (7,6) center-to-center in a straight
    # line cuts through the shared corner region even though neither endpoint cell is blocked.
    blocked = {(6, 6), (7, 7)}
    p0 = (6.5 * 32, 7.5 * 32)
    p1 = (7.5 * 32, 6.5 * 32)
    assert segment_blocked(p0, p1, blocked, tile_size=32.0) is True


def test_segment_blocked_false_for_a_clean_diagonal():
    # Neither the two endpoint cells nor the two corner cells the diagonal passes near are
    # blocked -- the move should be allowed.
    blocked = {(0, 5), (5, 0)}  # far away from this diagonal, not (6,6)/(7,7)
    p0 = (6.5 * 32, 7.5 * 32)
    p1 = (7.5 * 32, 6.5 * 32)
    assert segment_blocked(p0, p1, blocked, tile_size=32.0) is False


def test_move_with_collision_moves_freely_when_unblocked():
    new_pos = move_with_collision((0.0, 0.0), (100.0, 0.0), speed=50.0, dt=1.0, blocked=set(), tile_size=32.0)
    assert new_pos == pytest.approx((50.0, 0.0))


def test_move_with_collision_stops_before_a_wall_instead_of_moving_through_it():
    blocked = {(2, 0)}  # a wall cell directly in the path
    pos = (0.0, 0.0)
    for _ in range(20):
        new_pos = move_with_collision(pos, (200.0, 0.0), speed=50.0, dt=1.0, blocked=blocked, tile_size=32.0)
        if new_pos == pos:
            break
        pos = new_pos
    assert cell_of(pos, 32.0) != (2, 0)
    assert pos[0] < 2 * 32.0


def test_move_with_collision_snaps_to_target_when_within_one_step():
    new_pos = move_with_collision((0.0, 0.0), (5.0, 0.0), speed=50.0, dt=1.0, blocked=set(), tile_size=32.0)
    assert new_pos == (5.0, 0.0)


def test_move_with_collision_rejects_negative_speed_or_dt():
    with pytest.raises(ValueError):
        move_with_collision((0.0, 0.0), (10.0, 0.0), speed=-1.0, dt=1.0, blocked=set(), tile_size=32.0)
    with pytest.raises(ValueError):
        move_with_collision((0.0, 0.0), (10.0, 0.0), speed=1.0, dt=-1.0, blocked=set(), tile_size=32.0)
