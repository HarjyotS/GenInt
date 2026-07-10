"""Generic, reusable motion-pattern functions for sandbox-authored simulations.

Sandbox-mode agents (see `llm/prompts/sandbox_agent.md`) hand-roll NPC/hazard motion from scratch
every run with no shared vocabulary, which tends to collapse onto whichever pattern is easiest to
type (usually a horizontal sine patrol) regardless of what the task actually describes. These
three functions are deliberately generic -- none is named after or tuned to any specific
creature or genre -- so a wide range of described behaviors ("emerges from a gap," "chases when
close," "patrols back and forth") can be composed from a shared, tested building block instead of
reinvented, and so an agent skimming this module sees more than one pattern to choose from.

All functions are pure (no side effects) and operate on plain floats/tuples -- no dependency on
`engine/state.py` or any other InfiniEnv module, so they're usable from any custom simulation
loop, grid-based or continuous.
"""

from __future__ import annotations

import math


def patrol(t: float, base: float, amplitude: float, period: float, phase: float = 0.0) -> float:
    """A 1D sinusoidal oscillation around `base`, ranging over `[base - amplitude, base +
    amplitude]` with the given `period` (in the same time units as `t`). Apply once per axis --
    call twice with different `phase`/`period` values for a 2D patrol path.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    return base + amplitude * math.sin(2 * math.pi * (t / period) + phase)


def pulse_cycle(
    t: float,
    period: float,
    phase: float = 0.0,
    rise: float = 0.25,
    hold: float = 0.25,
    fall: float = 0.25,
) -> float:
    """A generic rise/hold/fall/idle rhythm, returning an `extent` in `[0, 1]`: 0 at the start of
    the cycle, ramping linearly to 1 over the `rise` fraction of `period`, holding at 1 for the
    `hold` fraction, ramping back to 0 over the `fall` fraction, then idle at 0 for whatever
    fraction remains. Applicable to anything that emerges and retracts on a timer -- a trap
    springing, a turret extending, a drawbridge lowering, a creature popping out of a gap -- the
    caller decides what "extent" means for its own entity (a vertical offset, an opening angle,
    an opacity), this only computes the timing curve.

    `rise + hold + fall` must not exceed 1.0 (the remainder is idle time at extent 0).
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if rise < 0 or hold < 0 or fall < 0:
        raise ValueError("rise/hold/fall must be non-negative")
    if rise + hold + fall > 1.0:
        raise ValueError("rise + hold + fall must not exceed 1.0")
    cycle = ((t / period) + phase) % 1.0
    if cycle < rise:
        return cycle / rise if rise > 0 else 1.0
    cycle -= rise
    if cycle < hold:
        return 1.0
    cycle -= hold
    if cycle < fall:
        return 1.0 - (cycle / fall if fall > 0 else 1.0)
    return 0.0


def pursue(
    pos: tuple[float, float], target_pos: tuple[float, float], speed: float, dt: float
) -> tuple[float, float]:
    """Step `pos` toward `target_pos` at a capped `speed` (distance/second), snapping exactly
    onto `target_pos` instead of overshooting when the remaining distance is less than one step
    covers. Applicable to any chase/pursuit behavior -- a hazard closing on the agent, an NPC
    escorting a target, a projectile homing in.
    """
    if speed < 0:
        raise ValueError("speed must be non-negative")
    if dt < 0:
        raise ValueError("dt must be non-negative")
    dx = target_pos[0] - pos[0]
    dy = target_pos[1] - pos[1]
    dist = math.hypot(dx, dy)
    step = speed * dt
    if dist <= step or dist == 0.0:
        return target_pos
    return (pos[0] + dx / dist * step, pos[1] + dy / dist * step)
