"""Generic, reusable grounded-character physics for sandbox-authored simulations.

`engine/motion_patterns.py`/`engine/animation.py` generalized *hazard/NPC* motion so it doesn't
get hand-rolled from scratch (and subtly wrong) every run. This module is the same idea for
*player-character locomotion physics* -- gravity, ground contact, climbing, and world/screen
bounds are needed by nearly every platformer-style sandbox scene, and hand-rolling them fresh
each time is exactly where real bugs have been found live: a character's horizontal run velocity
left applied (never zeroed) during a vertical "climb" branch, drifting it off the side of the
structure it was supposedly climbing; a climb condition gated only by a lower x-bound with no
upper bound tied to the structure's actual extent, letting the character keep "climbing" (rising)
while floating in open air well past the structure; and no world-bounds clamp anywhere, letting
the character run off the edge of the screen entirely. See CLAUDE.md's asset pipeline / sandbox
section for the live-verified account this was built from.

All functions are pure (no side effects) and operate on plain floats/tuples -- no dependency on
`engine/state.py` or any other InfiniEnv module, so they're usable from any custom simulation
loop, grid-based or continuous.
"""

from __future__ import annotations


def integrate_grounded_2d(
    pos: tuple[float, float],
    vel: tuple[float, float],
    *,
    gravity: float,
    dt: float,
    ground_y: float,
    bounds: tuple[float, float, float, float] | None = None,
) -> tuple[tuple[float, float], tuple[float, float], bool]:
    """One physics step for a grounded 2D character: apply gravity, integrate position, clamp
    to the ground, and (if `bounds` is given) clamp to world/screen bounds. Returns `(new_pos,
    new_vel, grounded)`.

    This does not decide what `vel` should be this frame -- that's the caller's action/control
    logic (e.g. "run" sets `vx`, "jump" sets `vy` while grounded). What it *does* do is the part
    that's easy to get subtly wrong by hand: `bounds`, if given, is `(min_x, min_y, max_x,
    max_y)` and is clamped silently every call regardless of which action fired this frame --
    world/screen bounds are a rule like gravity or collision that must apply unconditionally
    (see `sandbox_agent.md` principle 2), not something to remember to check only in specific
    branches.
    """
    if dt < 0:
        raise ValueError("dt must be non-negative")
    x, y = pos
    vx, vy = vel
    vy += gravity * dt
    x += vx * dt
    y += vy * dt
    grounded = False
    if y >= ground_y:
        y = ground_y
        vy = 0.0
        grounded = True
    if bounds is not None:
        min_x, min_y, max_x, max_y = bounds
        x = max(min_x, min(max_x, x))
        y = max(min_y, min(max_y, y))
    return (x, y), (vx, vy), grounded


def climb_step(
    pos: tuple[float, float],
    climb_speed: float,
    dt: float,
    *,
    structure_bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Move purely vertically along a climbable structure -- `x` is never read for anything but
    the bounds check below, and is never modified. That's what makes this safe to use instead of
    hand-integrating `y` during a climb branch: there is no code path here through which a
    horizontal run velocity could also apply during the same frame, because this function has no
    way to touch `x`.

    `structure_bounds` is `(min_x, min_y, max_x, max_y)` -- the structure's actual physical
    extent. Raises `ValueError` if `pos`'s `x` is outside `[min_x, max_x]`: unlike world/screen
    bounds (a normal edge to stop at), being asked to climb while not actually on the structure
    is an illegal *control-logic* state, so this is structurally unable to do it rather than
    silently keeping the character airborne beside the structure -- the same "no declared action
    for that" philosophy as `engine/action_registry.py`'s `UnknownActionError`.
    """
    if climb_speed < 0:
        raise ValueError("climb_speed must be non-negative")
    if dt < 0:
        raise ValueError("dt must be non-negative")
    x, y = pos
    min_x, min_y, max_x, max_y = structure_bounds
    if not (min_x <= x <= max_x):
        raise ValueError(f"climb_step: x={x} is outside the structure's x-range [{min_x}, {max_x}]")
    new_y = max(min_y, y - climb_speed * dt)
    return (x, new_y)


def clamp_to_bounds(
    pos: tuple[float, float], bounds: tuple[float, float, float, float]
) -> tuple[float, float]:
    """Clamp `pos` into `(min_x, min_y, max_x, max_y)`. The standalone form of
    `integrate_grounded_2d`'s bounds clamp -- usable every frame regardless of which action fired,
    for a control loop that isn't otherwise going through `integrate_grounded_2d` (e.g. an action
    that only moves horizontally, or a non-gravity character)."""
    x, y = pos
    min_x, min_y, max_x, max_y = bounds
    return (max(min_x, min(max_x, x)), max(min_y, min(max_y, y)))
