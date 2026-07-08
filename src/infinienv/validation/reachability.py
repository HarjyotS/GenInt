"""BFS reachability checks over the static grid, ignoring locked doors (worst case)."""

from __future__ import annotations

from collections import deque

from infinienv.engine.grid import Grid

Coord = tuple[int, int]


def reachable_set(grid: Grid, start: Coord, *, unlocked_doors: frozenset[str] = frozenset()) -> set[Coord]:
    """All cells reachable from `start` treating locked doors as blocked (conservative)."""
    seen = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nxt in grid.neighbors(*cur, unlocked_doors=unlocked_doors):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def is_reachable(grid: Grid, start: Coord, target: Coord, *, unlocked_doors: frozenset[str] = frozenset()) -> bool:
    if start == target:
        return True
    if grid.is_blocked(*target, unlocked_doors=unlocked_doors):
        # Still "reachable" in the practical sense if adjacent to a free cell.
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            adj = (target[0] + dx, target[1] + dy)
            if not grid.is_blocked(*adj, unlocked_doors=unlocked_doors) and adj in reachable_set(
                grid, start, unlocked_doors=unlocked_doors
            ):
                return True
        return False
    return target in reachable_set(grid, start, unlocked_doors=unlocked_doors)
