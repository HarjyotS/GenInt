"""Deterministic A* pathfinding over the static grid."""

from __future__ import annotations

import heapq

from infinienv.engine.grid import Grid

Coord = tuple[int, int]


def _heuristic(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def find_path(
    grid: Grid,
    start: Coord,
    goal: Coord,
    *,
    unlocked_doors: frozenset[str] = frozenset(),
) -> list[Coord] | None:
    """Return a list of coords from start to goal inclusive, or None if unreachable."""
    if start == goal:
        return [start]
    if grid.is_blocked(*goal, unlocked_doors=unlocked_doors):
        return None

    frontier: list[tuple[int, int, Coord]] = [(0, 0, start)]
    came_from: dict[Coord, Coord] = {}
    cost_so_far: dict[Coord, int] = {start: 0}
    counter = 0

    while frontier:
        _, _, current = heapq.heappop(frontier)
        if current == goal:
            break
        for nxt in grid.neighbors(*current, unlocked_doors=unlocked_doors):
            new_cost = cost_so_far[current] + 1
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost
                priority = new_cost + _heuristic(nxt, goal)
                counter += 1
                heapq.heappush(frontier, (priority, counter, nxt))
                came_from[nxt] = current

    if goal not in cost_so_far:
        return None

    path = [goal]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def path_to_moves(path: list[Coord]) -> list[str]:
    moves = []
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        dx, dy = x1 - x0, y1 - y0
        if (dx, dy) == (0, -1):
            moves.append("move_up")
        elif (dx, dy) == (0, 1):
            moves.append("move_down")
        elif (dx, dy) == (-1, 0):
            moves.append("move_left")
        elif (dx, dy) == (1, 0):
            moves.append("move_right")
        else:
            raise ValueError(f"non-adjacent path step {(x0, y0)} -> {(x1, y1)}")
    return moves
