"""Generic perception (line of sight, range, vision cone) for sandbox-authored simulations.

This is what makes a reactive NPC actually *reactive*: "the guard chases you on sight" needs a
real check that the guard can see the player -- an unobstructed sightline, within range, optionally
within a facing cone -- not a bare distance check that "sees" through solid walls. It's the
perception half of a reactive agent (`engine/agent_behavior.py` is the decision half, `pursue`/
`pathfinding` the movement half).

`has_line_of_sight` reuses `engine/grid_collision.py::segment_blocked` rather than re-implementing
a raycast -- a clear sightline is exactly "the straight segment between two points crosses no wall
cell." Pure functions on plain tuples/sets.
"""

from __future__ import annotations

import math

from infinienv.engine.grid_collision import segment_blocked


def has_line_of_sight(
    observer: tuple[float, float],
    target: tuple[float, float],
    blocked: set[tuple[int, int]],
    tile_size: float,
) -> bool:
    """True if the straight line from `observer` to `target` (both in the same pixel/world units)
    crosses no wall cell in `blocked`. Positions are continuous; `blocked` is grid cells."""
    return not segment_blocked(observer, target, blocked, tile_size)


def within_range(a: tuple[float, float], b: tuple[float, float], radius: float) -> bool:
    """True if `b` is within `radius` of `a` (Euclidean)."""
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= radius


def within_cone(
    observer: tuple[float, float],
    target: tuple[float, float],
    facing: tuple[float, float],
    cone_degrees: float,
) -> bool:
    """True if `target` lies within a `cone_degrees`-wide cone centered on the `facing` direction
    from `observer` (i.e. the angle between `facing` and the observer->target direction is at most
    half the cone width). A zero-length `facing` or `target == observer` counts as visible (no
    meaningful direction to exclude)."""
    dx, dy = target[0] - observer[0], target[1] - observer[1]
    target_dist = math.hypot(dx, dy)
    facing_len = math.hypot(facing[0], facing[1])
    if target_dist == 0 or facing_len == 0:
        return True
    dot = (dx * facing[0] + dy * facing[1]) / (target_dist * facing_len)
    dot = max(-1.0, min(1.0, dot))
    angle = math.degrees(math.acos(dot))
    return angle <= cone_degrees / 2.0


def can_see(
    observer: tuple[float, float],
    target: tuple[float, float],
    blocked: set[tuple[int, int]],
    tile_size: float,
    *,
    radius: float | None = None,
    facing: tuple[float, float] | None = None,
    cone_degrees: float | None = None,
) -> bool:
    """The single "does this observer notice the target right now" predicate a reactive NPC's
    perception check needs: an unobstructed line of sight, AND (if `radius` given) within range,
    AND (if `facing`/`cone_degrees` given) within the facing cone. Compose only what the task
    describes -- a 360-degree guard with unlimited sight is just `can_see(o, t, walls, tile)`; a
    directional guard with a view distance passes all of `radius`/`facing`/`cone_degrees`.
    """
    if radius is not None and not within_range(observer, target, radius):
        return False
    if facing is not None and cone_degrees is not None and not within_cone(observer, target, facing, cone_degrees):
        return False
    return has_line_of_sight(observer, target, blocked, tile_size)
