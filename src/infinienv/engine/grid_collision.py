"""Generic grid-wall collision for continuous-position sandbox-authored simulations.

A live-caught, concrete bug motivated this: a cave-navigation scene moved its agent along a
hand-authored list of waypoints, interpolating in a straight line between each pair of
consecutive grid-cell centers, without ever checking the move against the scene's own `walls` --
one waypoint was itself a wall cell, and two consecutive-waypoint segments cut diagonally through
a wall corner where *both* adjacent cells were blocked. The agent visibly "phased through" solid
rock. This is the same root cause `sandbox_agent.md`'s design principles already name generally
(state changing outside any declared, rule-checked action) applied to grid navigation specifically
-- a pre-planned route interpolated blindly is exactly the "animation, not simulation" anti-
pattern the "Simulate, don't animate" guidance exists to prevent, just for movement instead of
outcomes.

All functions are pure (no side effects); `blocked` is any `set`/`frozenset` of `(x, y)` integer
grid-cell tuples (build one trivially from a scene's `walls` list: `{(w["x"], w["y"]) for w in
scene["walls"]}`) -- no dependency on `engine/state.py`/`engine/grid.py` or any other InfiniEnv
module, so this works from any custom simulation loop regardless of how it represents the rest of
its state.
"""

from __future__ import annotations

import math


def cell_of(pos: tuple[float, float], tile_size: float) -> tuple[int, int]:
    """The integer grid cell a continuous pixel/world position falls in."""
    return (int(pos[0] // tile_size), int(pos[1] // tile_size))


def segment_blocked(
    p0: tuple[float, float],
    p1: tuple[float, float],
    blocked: set[tuple[int, int]],
    tile_size: float,
) -> bool:
    """True if the straight-line move from `p0` to `p1` passes through any cell in `blocked`.

    Sampled at a sub-tile resolution (finer than one grid cell) rather than checked only at the
    endpoints -- endpoint-only checking is exactly what let the motivating bug's diagonal moves
    cut through a wall corner neither endpoint was inside.
    """
    dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    if dist == 0:
        return cell_of(p0, tile_size) in blocked
    steps = max(1, int(dist / (tile_size / 4)) + 1)
    for i in range(steps + 1):
        t = i / steps
        p = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
        if cell_of(p, tile_size) in blocked:
            return True
    return False


def move_with_collision(
    pos: tuple[float, float],
    target: tuple[float, float],
    speed: float,
    dt: float,
    blocked: set[tuple[int, int]],
    tile_size: float,
) -> tuple[float, float]:
    """Step `pos` toward `target` at a capped `speed` (mirrors `motion_patterns.pursue`'s shape),
    but stop at the current position instead of moving if the step would cross a `blocked` cell --
    a drop-in replacement for hand-rolled waypoint interpolation that structurally cannot move
    through a wall, since every candidate step is checked before it's taken rather than trusted
    because "the route was planned to avoid walls."
    """
    if speed < 0:
        raise ValueError("speed must be non-negative")
    if dt < 0:
        raise ValueError("dt must be non-negative")
    dx = target[0] - pos[0]
    dy = target[1] - pos[1]
    dist = math.hypot(dx, dy)
    if dist == 0:
        return pos
    step = min(speed * dt, dist)
    candidate = (pos[0] + dx / dist * step, pos[1] + dy / dist * step)
    if segment_blocked(pos, candidate, blocked, tile_size):
        return pos
    return candidate
