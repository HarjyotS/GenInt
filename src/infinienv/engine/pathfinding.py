"""Generic grid pathfinding for sandbox-authored simulations.

The base engine's A* (`navigation/astar.py::find_path`) needs a full `Grid` object
(`grid.is_blocked`/`grid.neighbors`) -- unusable from a sandbox custom loop that only has a plain
set of wall cells (the cave-navigation run hand-rolled its own `deque` BFS because of exactly
this). This is the dependency-free equivalent: shortest-path over a plain wall-cell set. Its main
job is letting a chasing NPC (or a player-router) navigate *around* walls in a maze -- straight-
line stepping (`motion_patterns.pursue`) walks into walls, which is the wall-phasing class of bug
this session already had to fix once.

Pure functions on plain tuples/sets -- no dependency on `engine/grid.py` or any other InfiniEnv
module. 4-connected grid (no diagonals), BFS (uniform cost), so the returned path is a genuine
shortest path in cells.
"""

from __future__ import annotations

from collections import deque

_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def find_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    blocked: set[tuple[int, int]],
    width: int,
    height: int,
) -> list[tuple[int, int]] | None:
    """Shortest 4-connected path from `start` to `goal` (inclusive of both), avoiding `blocked`
    cells and staying within `[0, width) x [0, height)`. Returns the list of cells, or None if
    `goal` is unreachable. `start` itself being blocked is tolerated (an entity standing on a
    just-closed cell can still path out); `goal` being blocked returns None.
    """
    if start == goal:
        return [start]
    if goal in blocked:
        return None
    frontier: deque[tuple[int, int]] = deque([start])
    came_from: dict[tuple[int, int], tuple[int, int]] = {start: start}
    while frontier:
        current = frontier.popleft()
        if current == goal:
            break
        cx, cy = current
        for dx, dy in _STEPS:
            nxt = (cx + dx, cy + dy)
            if nxt in came_from:
                continue
            if not (0 <= nxt[0] < width and 0 <= nxt[1] < height):
                continue
            if nxt in blocked:
                continue
            came_from[nxt] = current
            frontier.append(nxt)
    if goal not in came_from:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def next_step_toward(
    start: tuple[int, int],
    goal: tuple[int, int],
    blocked: set[tuple[int, int]],
    width: int,
    height: int,
) -> tuple[int, int]:
    """The single next cell to move to along the shortest path from `start` toward `goal` -- the
    per-frame convenience a chasing NPC calls each step. Returns `start` unchanged if `goal` is
    unreachable or already reached (so a caller can treat "didn't move" as "no path / arrived"
    without special-casing None)."""
    path = find_path(start, goal, blocked, width, height)
    if path is None or len(path) < 2:
        return start
    return path[1]
