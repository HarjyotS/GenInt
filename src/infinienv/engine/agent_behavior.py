"""Generic reactive behavior state machine for sandbox-authored NPCs.

The difference between a "moving hazard" (a fixed patrol/oscillation) and a real NPC is that an
NPC *reacts*: it patrols, notices the player, switches to chase, gives up and returns, flees when
threatened. Hand-rolled, that decision logic collapses into an ad-hoc if-chain that tends to get
stuck in one state ("always chase" or "never leaves patrol") -- the same "collapsed to the
simplest thing" failure this whole design keeps addressing. `BehaviorMachine` makes the reactive
structure a declared state graph you can see and test: named states and transitions with explicit
conditions, evaluated deterministically.

This is deliberately a *pure* FSM -- it imports nothing from `motion_patterns`/`vision`/
`pathfinding`. The caller supplies transition conditions as predicates over a context dict (e.g.
`lambda ctx: can_see(...)`) and does the per-state movement itself (e.g. `pursue` in "chase",
`patrol` in "patrol"). Keeping the decision structure separate from perception and movement is
what lets each be tested on its own and composed freely.
"""

from __future__ import annotations

from typing import Callable


class BehaviorMachine:
    """A named-state machine with condition-gated transitions.

    Usage:
        guard = BehaviorMachine("patrol")
        guard.add_transition("patrol", "chase", when=lambda ctx: ctx["sees_player"])
        guard.add_transition("chase", "patrol", when=lambda ctx: not ctx["sees_player"])
        ...
        state = guard.update({"sees_player": can_see(...)})   # -> "chase" or "patrol"
        if state == "chase":
            npc_pos = pursue(npc_pos, player_pos, speed, dt)
    """

    def __init__(self, initial: str) -> None:
        self.state = initial
        self._transitions: dict[str, list[tuple[str, Callable[[dict], bool]]]] = {}
        self._states: set[str] = {initial}

    def add_transition(self, from_state: str, to_state: str, *, when: Callable[[dict], bool]) -> None:
        """Register a transition from `from_state` to `to_state`, taken when `when(context)` is
        true. Transitions are evaluated in registration order; the first matching one from the
        current state wins (so register more specific/urgent transitions first)."""
        self._transitions.setdefault(from_state, []).append((to_state, when))
        self._states.add(from_state)
        self._states.add(to_state)

    def update(self, context: dict) -> str:
        """Evaluate transitions out of the current state against `context`, move to the first
        whose condition holds, and return the (possibly unchanged) current state. Only one
        transition fires per call -- the machine doesn't chain through multiple states in a
        single update, so behavior is predictable frame to frame."""
        for to_state, condition in self._transitions.get(self.state, ()):
            if condition(context):
                self.state = to_state
                break
        return self.state

    @property
    def states(self) -> frozenset[str]:
        """Every state named in the machine (initial plus every transition endpoint)."""
        return frozenset(self._states)
