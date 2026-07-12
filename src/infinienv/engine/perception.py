"""Generic perception / fog-of-war support: a closed *perception* model, the read-side twin of the
closed action space.

The closed action space says only declared actions may *change* state. This says: when a task limits
what the player can perceive ("can only see blocks in line of sight", fog of war, sonar-only), the
solver may only *read* what it has actually observed -- never the world's ground truth. A recurring
sandbox cheat is a solver that beelines to a ground-truth coordinate (e.g. `world.layout.diamond`)
while the render merely *draws* fog of war, so the limited-perception mechanic is cosmetic. The
`KnowledgeMap` here is the honest alternative: the author defines any perception rule they like (what
gets observed each step), feeds the observation into a `KnowledgeMap`, and the solver plans over the
`KnowledgeMap` alone. What it has never observed, it cannot act on.

Pure functions / plain data -- no dependency on `engine/state.py`, usable from any custom loop.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from infinienv.engine.vision import has_line_of_sight

Cell = tuple[int, int]


class KnowledgeMap:
    """The solver's accumulated belief about the world -- its fog-of-war memory. The solver plans
    over this, never the world's ground truth. `observe()` merges a fresh observation (whatever the
    author's perception rule exposed this step) into memory; `has_seen`/`find`/`known_cells` are the
    only honest ways to locate a target, because they can only return something already observed.
    """

    def __init__(self) -> None:
        self._known: dict[Cell, object] = {}  # cell -> most-recently-observed value
        self._seen: set[Cell] = set()  # every cell ever observed, even if now out of view

    def observe(self, observation: dict[Cell, object] | Iterable[Cell]) -> None:
        """Record what the perception rule exposed this step. Accepts either a `{cell: value}` map
        (e.g. the block type at each visible cell) or a bare iterable of cells (value defaults to
        `True`). Once observed, a cell stays remembered even after it leaves view."""
        if isinstance(observation, dict):
            items: Iterable[tuple[Cell, object]] = observation.items()
        else:
            items = ((cell, True) for cell in observation)
        for cell, value in items:
            self._known[cell] = value
            self._seen.add(cell)

    def has_seen(self, cell: Cell) -> bool:
        """True if `cell` was ever observed -- the honest precondition for the solver acting on it."""
        return cell in self._seen

    def get(self, cell: Cell, default: object = None) -> object:
        """The last observed value at `cell`, or `default` if it was never observed."""
        return self._known.get(cell, default)

    def known_cells(self) -> set[Cell]:
        """Every cell the solver has observed so far (a copy). The solver's navigable world."""
        return set(self._seen)

    def find(self, predicate: Callable[[object], bool]) -> list[Cell]:
        """All observed cells whose last-observed value satisfies `predicate` -- e.g.
        `find(lambda v: v == "diamond")` returns only diamonds the solver has actually seen, never
        one it hasn't discovered yet. This is how a solver locates a target without cheating."""
        return [cell for cell, value in self._known.items() if predicate(value)]

    def __contains__(self, cell: Cell) -> bool:
        return cell in self._seen

    def __len__(self) -> int:
        return len(self._seen)


def visible_cells(
    origin: Cell,
    radius: float,
    blockers: set[Cell],
    *,
    bounds: tuple[int, int] | None = None,
) -> set[Cell]:
    """One common perception rule: every cell within `radius` of `origin` that has a clear line of
    sight to it (not occluded by a `blockers` cell). Reuses `engine/vision.has_line_of_sight` for the
    raycast, treating cells as unit squares (tile_size=1, cell centers). `bounds`, if given as
    `(width, height)`, clamps the scan to the grid. Feed the result to `KnowledgeMap.observe`.

    This is just one rule -- an author can write any other (a facing cone, sound radius, sonar ping)
    and feed *its* output to the same `KnowledgeMap`; the point is the solver reads the map, not
    ground truth.
    """
    ox, oy = origin
    r = int(radius)
    x_lo, x_hi = ox - r, ox + r
    y_lo, y_hi = oy - r, oy + r
    if bounds is not None:
        width, height = bounds
        x_lo, x_hi = max(0, x_lo), min(width - 1, x_hi)
        y_lo, y_hi = max(0, y_lo), min(height - 1, y_hi)
    origin_center = (ox + 0.5, oy + 0.5)
    visible: set[Cell] = set()
    for cx in range(x_lo, x_hi + 1):
        for cy in range(y_lo, y_hi + 1):
            cell = (cx, cy)
            if (cx - ox) ** 2 + (cy - oy) ** 2 > radius * radius:
                continue
            # a blocker occludes cells behind it but is itself visible (you can see the wall you hit)
            if has_line_of_sight(origin_center, (cx + 0.5, cy + 0.5), blockers - {cell}, 1.0):
                visible.add(cell)
    return visible
