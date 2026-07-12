"""Generic grid pathfinding for sandbox-authored simulations (engine/pathfinding.py)."""

from infinienv.engine.pathfinding import find_path, next_step_toward


def test_find_path_straight_line_no_walls():
    path = find_path((0, 0), (3, 0), blocked=set(), width=5, height=5)
    assert path == [(0, 0), (1, 0), (2, 0), (3, 0)]


def test_find_path_start_equals_goal():
    assert find_path((2, 2), (2, 2), blocked=set(), width=5, height=5) == [(2, 2)]


def test_find_path_routes_around_a_wall():
    # a vertical wall at x=2 from y=0..2, gap at y=3 -- path must detour down and around
    blocked = {(2, 0), (2, 1), (2, 2)}
    path = find_path((0, 0), (4, 0), blocked, width=5, height=5)
    assert path is not None
    assert path[0] == (0, 0) and path[-1] == (4, 0)
    assert all(cell not in blocked for cell in path)
    # every consecutive step is 4-connected (no diagonal jumps)
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        assert abs(x1 - x0) + abs(y1 - y0) == 1


def test_find_path_returns_none_when_goal_walled_off():
    # goal fully enclosed by walls
    blocked = {(3, 3), (5, 3), (4, 2), (4, 4)}
    path = find_path((0, 0), (4, 3), blocked, width=6, height=6)
    assert path is None


def test_find_path_returns_none_when_goal_is_a_wall():
    assert find_path((0, 0), (2, 2), blocked={(2, 2)}, width=5, height=5) is None


def test_find_path_is_shortest():
    # open grid: manhattan distance from (0,0) to (3,2) is 5, path has 6 cells inclusive
    path = find_path((0, 0), (3, 2), blocked=set(), width=6, height=6)
    assert path is not None
    assert len(path) == 6


def test_next_step_toward_returns_first_step():
    step = next_step_toward((0, 0), (3, 0), blocked=set(), width=5, height=5)
    assert step == (1, 0)


def test_next_step_toward_returns_start_when_unreachable():
    step = next_step_toward((0, 0), (2, 2), blocked={(2, 2)}, width=5, height=5)
    assert step == (0, 0)


def test_next_step_toward_returns_start_when_already_there():
    assert next_step_toward((2, 2), (2, 2), blocked=set(), width=5, height=5) == (2, 2)
