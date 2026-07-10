"""Generic phase-driven animation helpers for sandbox-authored simulations.

A game where every entity is a single fixed shape or sprite that only ever translates is missing
real animation, however correct its physics is -- a drawn mouth, leg, or sprite pose should vary
with the entity's own internal clock, not just its position. These functions are the generic,
reusable form of that idea: they know nothing about what's being animated (a mouth angle, a leg
offset, a sprite variant key), only how to turn elapsed time into a repeating phase and how to
turn a phase into either a smoothly-varying number or a choice among named states. Pair
`cycle_variant()` with `assets/resolver.py`'s `variant_types()`/`variant_descriptions()` and
`resolve_assets(..., extra_types=..., extra_descriptions=...)` to drive an actual sprite swap by
phase; use `oscillate()` to drive a procedurally-drawn overlay instead when no sprite is needed.
"""

from __future__ import annotations

import math
from typing import Sequence


def phase_of(t: float, period: float, offset: float = 0.0) -> float:
    """Normalize elapsed time `t` into a repeating `[0, 1)` phase with the given `period`."""
    if period <= 0:
        raise ValueError("period must be positive")
    return ((t / period) + offset) % 1.0


def oscillate(phase: float, low: float, high: float) -> float:
    """Sweep a value between `low` and `high` as `phase` (expected in `[0, 1)`, but any float
    works) advances through a full sine cycle -- `phase=0` and `phase=1` both return the
    midpoint, `phase=0.25` returns `high`, `phase=0.75` returns `low`. Use for any single
    continuously-varying drawn parameter: a mouth-opening angle, a leg offset, a squash-and-
    stretch scale.
    """
    t = (math.sin(2 * math.pi * phase) + 1.0) / 2.0
    return low + t * (high - low)


def cycle_variant(phase: float, variants: Sequence[str]) -> str:
    """Split `[0, 1)` into `len(variants)` even buckets and return which named variant is
    "active" for the given `phase` (expected in `[0, 1)`; wrapped via modulo otherwise). The
    generic form of "which sprite frame/pose should I show right now" -- this function has no
    idea what the variant names mean, it only picks one.
    """
    if not variants:
        raise ValueError("variants must be non-empty")
    wrapped = phase % 1.0
    index = min(int(wrapped * len(variants)), len(variants) - 1)
    return variants[index]
