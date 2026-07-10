"""A small, generic closed-action dispatcher for sandbox-authored simulations.

InfiniEnv's own deterministic engine enforces "state may only change through a small, declared
set of actions" via a hand-written `if`/`elif` chain in `engine/actions.py` that raises on an
unrecognized verb. Sandbox-mode agents (see `llm/prompts/sandbox_agent.md`) author their own
physics/rules with nothing external checking them, so that same discipline has to be upheld by
the agent itself, every run, from memory. `ActionSpace` makes it structural instead: register
every legal action once, then `dispatch()` is the only route decision logic can use to apply one
-- an unregistered name raises loudly rather than silently doing nothing or being bypassed by a
stray direct state assignment elsewhere in the file.
"""

from __future__ import annotations

from typing import Callable


class UnknownActionError(Exception):
    """Raised by `ActionSpace.dispatch()` for a name that was never registered."""


class ActionSpace:
    """A registry of the only functions allowed to mutate simulation state.

    Usage:
        actions = ActionSpace()

        @actions.action("jump")
        def jump(hero):
            if hero.grounded:
                hero.vy = -JUMP_SPEED
                hero.grounded = False

        actions.dispatch("jump", hero)   # calls jump(hero)
        actions.dispatch("fly", hero)    # raises UnknownActionError -- "fly" was never declared
    """

    def __init__(self) -> None:
        self._actions: dict[str, Callable] = {}

    def action(self, name: str) -> Callable[[Callable], Callable]:
        """Decorator form: `@actions.action("jump")` registers the decorated function."""

        def decorator(fn: Callable) -> Callable:
            self.register(name, fn)
            return fn

        return decorator

    def register(self, name: str, fn: Callable) -> None:
        if name in self._actions:
            raise ValueError(f"action {name!r} is already registered")
        self._actions[name] = fn

    def dispatch(self, name: str, *args, **kwargs):
        """Call the action registered as `name`, or raise `UnknownActionError` if none was."""
        try:
            fn = self._actions[name]
        except KeyError:
            raise UnknownActionError(
                f"{name!r} is not a registered action; only {sorted(self._actions)} are allowed"
            ) from None
        return fn(*args, **kwargs)

    @property
    def names(self) -> frozenset[str]:
        """The frozen set of currently-registered action names -- useful for a self-check
        that every action recorded in a trace belongs to this set."""
        return frozenset(self._actions)
