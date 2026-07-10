"""Generic named-state and declarative gating for sandbox-authored simulations.

Every sandbox run in this session so far (a Mario-style rescue, a cave, a factory floor, a
submarine cave) has produced *static navigation* -- walk through a space, avoid or reach things --
with the win condition collapsing to whatever's simplest to check (a single position, a bare item
count), never real state-dependent puzzle logic: a locked exit that only opens once several
conditions are jointly satisfied, an ordered sequence of sub-objectives, a switch that gates a
door. The base (non-sandbox) engine's schema already models locks/keys and ordered `sequence`
goals, but that machinery is wired through `GameState`/`solve_scene()`, which no sandbox run
observed this session actually uses -- every one writes its own custom continuous-position
simulation loop instead. This module is the sandbox-facing equivalent: a small, dependency-free
primitive for "has X happened yet" and "is this gate open," so expressing joint/sequenced
preconditions is a couple of function calls instead of something to invent from scratch under
time pressure (which is exactly what tends to collapse onto the simplest possible check).

Pure, dependency-free classes -- no reliance on `engine/state.py` or any other InfiniEnv module,
usable from any custom simulation loop.
"""

from __future__ import annotations


class PuzzleState:
    """A named flag/counter store: the generic form of "has X happened yet" (gems collected, a
    switch pressed, a key held, an earlier sub-objective completed). Values can be anything --
    booleans for one-off flags, numbers for counters -- `Gate` interprets them based on what a
    given requirement's threshold looks like (see below).
    """

    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def set(self, name: str, value: object = True) -> None:
        self._values[name] = value

    def increment(self, name: str, by: int | float = 1) -> int | float:
        current = self._values.get(name, 0)
        # bool is a subclass of int in Python (True + 1 == 2) -- exclude it explicitly, since
        # incrementing a boolean flag is almost always a mistake, not an intentional counter.
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise TypeError(f"cannot increment {name!r}: current value {current!r} is not numeric")
        self._values[name] = current + by
        return self._values[name]

    def get(self, name: str, default: object = None) -> object:
        return self._values.get(name, default)

    def snapshot(self) -> dict[str, object]:
        """A plain-dict copy of every flag/counter set so far -- useful for a self-review script
        or for writing the full puzzle state into `replay.json`."""
        return dict(self._values)


class Gate:
    """A declarative, named precondition over a `PuzzleState`: `Gate(requires={"gems": 2,
    "switch_pressed": True})` reads as "needs at least 2 gems AND the switch pressed." A numeric
    threshold is satisfied by `>=` (so it composes naturally with `PuzzleState.increment`); a
    boolean threshold is satisfied by equality; anything else is compared by equality too. An
    unset flag defaults to `0` for a numeric requirement and `False` for a boolean one, so a gate
    starts closed by default rather than needing every flag pre-initialized.

    Keeping a gate's requirements in one declared place (rather than scattered across several
    if-conditions in a win check) is what makes the dependency structure something you can see
    and test directly, instead of something that's easy to under-specify -- the same reasoning as
    `engine/action_registry.py`'s closed action dispatch, applied to preconditions instead of
    actions.
    """

    def __init__(self, requires: dict[str, object]) -> None:
        self.requires = dict(requires)

    def is_open(self, state: PuzzleState) -> bool:
        return not self.missing(state)

    def missing(self, state: PuzzleState) -> list[str]:
        """Which requirement names aren't yet met -- empty when the gate is open. Useful both for
        a HUD/status message and for a self-review check ("was this gate ever actually closed
        before it opened").
        """
        unmet = []
        for name, threshold in self.requires.items():
            value = state.get(name)
            if isinstance(threshold, bool):
                if bool(value) != threshold:
                    unmet.append(name)
            elif isinstance(threshold, (int, float)):
                numeric_value = value if isinstance(value, (int, float)) else 0
                if numeric_value < threshold:
                    unmet.append(name)
            else:
                if value != threshold:
                    unmet.append(name)
        return unmet
