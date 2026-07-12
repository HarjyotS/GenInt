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


def generate_terrain_profile(
    width: int,
    *,
    seed: int,
    base_row: int,
    min_row: int,
    max_row: int,
    max_step: int = 2,
    flat_bias: float = 0.6,
) -> list[int]:
    """A seeded *side-view* ground-height profile: one ground row per column, left to right, for a
    platformer whose terrain is a continuous uneven surface (not a top-down region -- use
    `generate_organic_region` for that). Deterministic for a given `seed`; a different seed produces
    a genuinely different profile, which is the whole point -- "procedurally generated uneven
    terrain" is something to generate here, not hand-list as constants.

    Each column either stays level (probability `flat_bias`, producing real walkable ledges) or
    steps up/down by 1..`max_step` rows, clamped to `[min_row, max_row]`. Row indices grow downward
    (screen convention), so a smaller row is higher ground.
    """
    if width < 1:
        raise ValueError("width must be at least 1")
    if not (min_row <= base_row <= max_row):
        raise ValueError("require min_row <= base_row <= max_row")
    if max_step < 1:
        raise ValueError("max_step must be at least 1")
    if not (0.0 <= flat_bias <= 1.0):
        raise ValueError("flat_bias must be in [0, 1]")

    rng = random.Random(seed)
    heights = [base_row]
    for _ in range(width - 1):
        row = heights[-1]
        if rng.random() >= flat_bias:
            step = rng.randint(1, max_step) * (1 if rng.random() < 0.5 else -1)
            row = max(min_row, min(max_row, row + step))
        heights.append(row)
    return heights


def carve_gaps(
    width: int,
    *,
    seed: int,
    count: int,
    min_width: int = 1,
    max_width: int = 2,
    margin: int = 2,
) -> set[int]:
    """A seeded set of pit columns (fatal-fall gaps) for a side-view level. Gaps are kept at least
    `margin` columns away from both ends, so the spawn and exit stay on solid ground. Deterministic
    for a given `seed`. Returns the set of gap column indices (may be fewer than `count` if the
    interior is too small to place them without overlap)."""
    if count < 0:
        raise ValueError("count must be non-negative")
    if not (1 <= min_width <= max_width):
        raise ValueError("require 1 <= min_width <= max_width")
    if margin < 0:
        raise ValueError("margin must be non-negative")

    rng = random.Random(seed)
    gaps: set[int] = set()
    lo, hi = margin, width - 1 - margin
    attempts = 0
    while _distinct_gap_count(gaps) < count and attempts < count * 20:
        attempts += 1
        if hi - lo < 1:
            break
        gap_w = rng.randint(min_width, max_width)
        left = rng.randint(lo, hi)
        cells = {left + i for i in range(gap_w) if left + i <= hi}
        # keep a solid column between distinct gaps so they read as separate pits
        if any((c - 1) in gaps or (c + 1) in gaps or c in gaps for c in cells):
            continue
        gaps |= cells
        if _distinct_gap_count(gaps) >= count:
            break
    return gaps


def _distinct_gap_count(gaps: set[int]) -> int:
    """Number of contiguous runs in a set of columns (how many separate pits `gaps` represents)."""
    return sum(1 for c in gaps if (c - 1) not in gaps)


def generate_platform_layout(
    width: int,
    height: int,
    *,
    seed: int,
    rows: int,
    platform_span: tuple[int, int] = (4, 10),
    gap_span: tuple[int, int] = (2, 5),
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    """A seeded *discrete* side-view level: horizontal platforms stacked on several vertical levels,
    joined by ladders. Returns `(platforms, ladders)` in the tuple shapes sandbox platformers use --
    each platform is `(left_col, row, right_col)` and each ladder is `(col, top_row, bottom_row)`.

    `rows` vertical levels are spaced across `[0, height)`. On each level, platforms of width
    `platform_span` are laid left to right separated by gaps of width `gap_span`. Every adjacent pair
    of occupied levels is joined by at least one ladder whose column lies on a platform of *both*
    levels, so the layout is **vertically connected by construction** -- while the horizontal gaps and
    multiple platforms per row give genuinely different routes. Deterministic for a given `seed`; a
    different seed yields a different layout. This is what "procedurally generated platforms with
    multiple paths" should call instead of hand-listing platform constants.
    """
    if width < platform_span[1] or height < 2:
        raise ValueError("grid too small for the requested layout")
    if rows < 2:
        raise ValueError("rows must be at least 2 for a connected multi-level layout")
    if not (1 <= platform_span[0] <= platform_span[1]):
        raise ValueError("invalid platform_span")
    if not (1 <= gap_span[0] <= gap_span[1]):
        raise ValueError("invalid gap_span")

    rng = random.Random(seed)
    row_ys = [round(i * (height - 1) / (rows - 1)) for i in range(rows)]
    platforms: list[tuple[int, int, int]] = []
    per_row: list[list[tuple[int, int]]] = []  # (left, right) spans, by row index
    for row_y in row_ys:
        spans: list[tuple[int, int]] = []
        x = rng.randint(0, max(0, gap_span[1] - 1))
        while x < width:
            span_w = rng.randint(*platform_span)
            right = min(width - 1, x + span_w - 1)
            if right - x >= platform_span[0] - 1:
                spans.append((x, right))
                platforms.append((x, row_y, right))
            x = right + 1 + rng.randint(*gap_span)
        if not spans:  # guarantee at least one platform on every level
            left = rng.randint(0, max(0, width - platform_span[0]))
            right = min(width - 1, left + platform_span[0] - 1)
            spans.append((left, right))
            platforms.append((left, row_y, right))
        per_row.append(spans)

    ladders: list[tuple[int, int, int]] = []
    for i in range(rows - 1):
        top_y, bottom_y = row_ys[i], row_ys[i + 1]
        # find a column covered by a platform on BOTH adjacent levels (guaranteed connectivity)
        shared = [
            rng.randint(max(a[0], b[0]), min(a[1], b[1]))
            for a in per_row[i]
            for b in per_row[i + 1]
            if max(a[0], b[0]) <= min(a[1], b[1])
        ]
        if shared:
            col = rng.choice(shared)
        else:
            # no overlap: extend a top platform to reach under a bottom one so the ladder is real
            a, b = per_row[i][0], per_row[i + 1][0]
            col = b[0] if b[0] <= a[1] else a[0]
            new_right = max(a[1], col)
            platforms = [(a[0], top_y, new_right) if p == (a[0], top_y, a[1]) else p for p in platforms]
            per_row[i][0] = (a[0], new_right)
        ladders.append((col, top_y, bottom_y))
    return platforms, ladders


def scatter_on_supports(
    supports: list[tuple[int, int, int]],
    *,
    seed: int,
    count: int,
    spacing: int,
    avoid: frozenset[int] = frozenset(),
) -> list[tuple[int, int]]:
    """Seeded placement of items (gems, hazards) onto given support spans -- either the platforms from
    `generate_platform_layout` (`(left, row, right)`) or synthesized ground spans. Returns up to
    `count` `(col, row)` positions **sitting on a support**, at least `spacing` columns apart, never
    on an `avoid` column (a pit) and never on the first/last support column (keep spawn/exit clear).
    Deterministic for a given `seed`. Fewer than `count` positions are returned if the supports can't
    fit them at the requested spacing."""
    if count < 0:
        raise ValueError("count must be non-negative")
    if spacing < 1:
        raise ValueError("spacing must be at least 1")

    candidates: list[tuple[int, int]] = []
    for left, row, right in supports:
        for col in range(left, right + 1):
            if col not in avoid:
                candidates.append((col, row))
    if not candidates:
        return []
    reserved = {candidates[0][0], candidates[-1][0]}  # spawn/exit ends
    rng = random.Random(seed)
    rng.shuffle(candidates)
    placed: list[tuple[int, int]] = []
    for col, row in candidates:
        if len(placed) >= count:
            break
        if col in reserved:
            continue
        if all(abs(col - pc) >= spacing for pc, _ in placed):
            placed.append((col, row))
    return placed


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
