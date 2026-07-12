"""Generic perception for sandbox-authored simulations (engine/vision.py)."""

from infinienv.engine.vision import can_see, has_line_of_sight, within_cone, within_range


def test_has_line_of_sight_clear():
    assert has_line_of_sight((0.0, 0.0), (100.0, 0.0), blocked=set(), tile_size=32.0) is True


def test_has_line_of_sight_blocked_by_a_wall():
    # a wall cell squarely on the horizontal sightline
    assert has_line_of_sight((0.0, 16.0), (160.0, 16.0), blocked={(2, 0)}, tile_size=32.0) is False


def test_within_range():
    assert within_range((0.0, 0.0), (3.0, 4.0), radius=5.0) is True  # dist exactly 5
    assert within_range((0.0, 0.0), (3.0, 4.0), radius=4.9) is False


def test_within_cone_straight_ahead():
    # facing +x, target directly ahead -> inside any positive cone
    assert within_cone((0.0, 0.0), (10.0, 0.0), facing=(1.0, 0.0), cone_degrees=90.0) is True


def test_within_cone_behind_is_excluded():
    # facing +x, target directly behind -> outside a 90-degree cone
    assert within_cone((0.0, 0.0), (-10.0, 0.0), facing=(1.0, 0.0), cone_degrees=90.0) is False


def test_within_cone_half_angle():
    # facing +x, target at 45 degrees off-axis: inside a 100-degree cone (half-angle 50),
    # outside a 60-degree cone (half-angle 30). Avoids an exact 45==45 float tie on purpose.
    assert within_cone((0.0, 0.0), (10.0, 10.0), facing=(1.0, 0.0), cone_degrees=100.0) is True
    assert within_cone((0.0, 0.0), (10.0, 10.0), facing=(1.0, 0.0), cone_degrees=60.0) is False


def test_can_see_omnidirectional_unlimited_is_just_line_of_sight():
    assert can_see((0.0, 0.0), (100.0, 0.0), blocked=set(), tile_size=32.0) is True
    assert can_see((0.0, 16.0), (160.0, 16.0), blocked={(2, 0)}, tile_size=32.0) is False


def test_can_see_respects_range():
    # clear sightline but target beyond radius
    assert can_see((0.0, 0.0), (100.0, 0.0), blocked=set(), tile_size=32.0, radius=50.0) is False
    assert can_see((0.0, 0.0), (40.0, 0.0), blocked=set(), tile_size=32.0, radius=50.0) is True


def test_can_see_respects_facing_cone():
    # target behind the guard, within range and clear LOS, but outside the facing cone
    assert (
        can_see(
            (100.0, 0.0),
            (0.0, 0.0),
            blocked=set(),
            tile_size=32.0,
            radius=200.0,
            facing=(1.0, 0.0),
            cone_degrees=90.0,
        )
        is False
    )
    # same but guard facing toward the target
    assert (
        can_see(
            (100.0, 0.0),
            (0.0, 0.0),
            blocked=set(),
            tile_size=32.0,
            radius=200.0,
            facing=(-1.0, 0.0),
            cone_degrees=90.0,
        )
        is True
    )
