"""Deterministic grid-physics: pushable objects and sliding (momentum) on the integer grid.

This is a real, tested physics primitive -- NOT continuous/force-based simulation. Everything
here operates on integer cells and mutates GameState deterministically, so the validator's
solvability guarantee still holds (the solver in navigation/planner.py plans and the validator
verifies pushes exactly like any other goal). Continuous physics, which the A* solver cannot
verify, stays confined to --sandbox mode; see CLAUDE.md.

Two behaviours, both driven by SceneObject flags:
- `pushable`: the agent shoves the object one cell by moving into it, instead of being blocked.
- `slippery`: a pushable object that, once shoved, keeps sliding in the push direction until the
  next cell is blocked (ice-puck momentum) -- still integer cells, just several per push.

Collision here is computed from LIVE state (current object positions), not the static Grid: the
Grid records only the initial solid layout, so once a pushable object moves it would be stale.
Walls and bounds still come from the Grid (those never move). For a scene with no pushable
objects this yields exactly the same blocking decisions the old static check did, so existing
(non-physics) scenes are unaffected.
"""

from __future__ import annotations

from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState, ObjectState


def solid_blocker_at(state: GameState, x: int, y: int, *, ignore_id: str | None = None) -> ObjectState | None:
    """The object currently occupying (x, y) that blocks movement, or None. Live: uses each
    object's *current* position, so it stays correct as pushable objects move. An unlocked door
    (id in state.unlocked_doors) does not block, matching Grid.is_blocked's door handling."""
    for obj in state.objects.values():
        if obj.held or obj.id == ignore_id:
            continue
        if obj.x == x and obj.y == y and obj.solid and obj.id not in state.unlocked_doors:
            return obj
    return None


def cell_blocked(state: GameState, grid: Grid, x: int, y: int, *, ignore_id: str | None = None) -> bool:
    """Is (x, y) blocked for a moving entity right now? Out of bounds or a wall (static, from the
    Grid) or occupied by a live solid object (from state)."""
    if not grid.in_bounds(x, y):
        return True
    if grid.is_wall(x, y):
        return True
    return solid_blocker_at(state, x, y, ignore_id=ignore_id) is not None


def pushable_at(state: GameState, x: int, y: int) -> ObjectState | None:
    """A non-held pushable object currently at (x, y), or None."""
    for obj in state.objects.values():
        if not obj.held and obj.pushable and obj.x == x and obj.y == y:
            return obj
    return None


def try_push(state: GameState, grid: Grid, obj: ObjectState, dx: int, dy: int) -> bool:
    """Push `obj` in direction (dx, dy). A non-slippery object moves one cell if the destination
    is free; a slippery one keeps sliding until the next cell is blocked. Mutates `obj` in place.
    Returns True if it moved at least one cell, False if it was immediately blocked (couldn't move
    at all). Collision ignores `obj` itself but respects walls, bounds, and every other live solid
    object."""
    steps = 0
    while True:
        tx, ty = obj.x + dx, obj.y + dy
        if cell_blocked(state, grid, tx, ty, ignore_id=obj.id):
            break
        obj.x, obj.y = tx, ty
        steps += 1
        if not obj.slippery:
            break
    return steps > 0
