"""Generic procedural terrain generation for sandbox-authored simulations.

A live-caught, concrete gap motivated this: a scene whose own prompt asked for "a procedurally
generated cave with uneven terrain... multiple possible paths" instead shipped a single,
hand-authored, mostly-linear corridor of specific grid cells -- there was nothing procedural
about it, and no real branching, because hand-listing cells one by one under time pressure
naturally collapses onto the simplest shape that technically connects a start to an end. This
gives sandbox agents an actual generator instead, so "procedurally generated, uneven, branching"
is something to call, not something to hand-author cell by cell.

Pure functions operating on plain tuples/sets -- no dependency on `engine/state.py` or any other
InfiniEnv module, usable from any custom simulation loop.
"""

from __future__ import annotations

import random
from collections import deque


def generate_organic_region(
    width: int,
    height: int,
    start: tuple[int, int],
    *,
    steps: int,
    seed: int,
    branch_chance: float = 0.15,
    max_walkers: int = 4,
) -> set[tuple[int, int]]:
    """Carve a connected, irregularly-shaped region of floor cells via a branching random walk
    (a "drunkard's walk" cave-generation algorithm), returning the set of `(x, y)` floor cells.
    Deterministic for a given `seed`.

    Starting from `start`, a walker repeatedly steps to a random in-bounds neighboring cell
    (4-directional) and marks it as floor; each step has a `branch_chance` probability of
    spawning an additional independent walker at the current position (capped at `max_walkers`
    concurrent walkers), so the carved region naturally develops multiple diverging paths rather
    than a single corridor -- real branching is an emergent property of the algorithm, not
    something to design cell by cell. Every carved cell is connected to `start` by construction
    (a walker only ever steps from an already-carved cell), so the result never needs a separate
    connectivity fix-up.
    """
    if steps < 0:
        raise ValueError("steps must be non-negative")
    if not (0.0 <= branch_chance <= 1.0):
        raise ValueError("branch_chance must be in [0, 1]")
    if max_walkers < 1:
        raise ValueError("max_walkers must be at least 1")
    if not (0 <= start[0] < width and 0 <= start[1] < height):
        raise ValueError(f"start {start} is outside the {width}x{height} grid")

    rng = random.Random(seed)
    region: set[tuple[int, int]] = {start}
    walkers = [start]
    deltas = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    for _ in range(steps):
        if not walkers:
            break
        idx = rng.randrange(len(walkers))
        x, y = walkers[idx]
        dx, dy = deltas[rng.randrange(len(deltas))]
        nx, ny = x + dx, y + dy
        if 0 <= nx < width and 0 <= ny < height:
            region.add((nx, ny))
            walkers[idx] = (nx, ny)
            if len(walkers) < max_walkers and rng.random() < branch_chance:
                walkers.append((nx, ny))

    return region


def region_is_connected(region: set[tuple[int, int]], start: tuple[int, int]) -> bool:
    """True if every cell in `region` is reachable from `start` via 4-directional steps within
    `region`. Generically useful for verifying *any* hand-authored or generated level's
    connectivity before shipping it -- the motivating bug's route also included a waypoint that
    was never a floor cell at all, which this would have caught immediately (`start` or any
    later checked cell not even being in `region` fails loudly rather than being discovered as a
    visual glitch).
    """
    if start not in region:
        return False
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (x + dx, y + dy)
            if neighbor in region and neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen == region
