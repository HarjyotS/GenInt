"""Generic closed-action dispatcher for sandbox-authored simulations (engine/action_registry.py)."""

import pytest

from infinienv.engine.action_registry import ActionSpace, UnknownActionError


def test_register_and_dispatch_calls_the_registered_function():
    actions = ActionSpace()
    calls = []
    actions.register("jump", lambda hero: calls.append(("jump", hero)))
    actions.dispatch("jump", "hero_1")
    assert calls == [("jump", "hero_1")]


def test_dispatch_returns_the_function_result():
    actions = ActionSpace()
    actions.register("double", lambda x: x * 2)
    assert actions.dispatch("double", 21) == 42


def test_decorator_registration_works():
    actions = ActionSpace()

    @actions.action("climb")
    def climb(hero):
        return f"{hero} climbs"

    assert actions.dispatch("climb", "knight") == "knight climbs"
    assert climb("knight") == "knight climbs"  # decorator returns the original function


def test_dispatch_unknown_action_raises():
    actions = ActionSpace()
    actions.register("walk", lambda: None)
    with pytest.raises(UnknownActionError, match="fly"):
        actions.dispatch("fly")


def test_registering_the_same_name_twice_raises():
    actions = ActionSpace()
    actions.register("walk", lambda: None)
    with pytest.raises(ValueError, match="walk"):
        actions.register("walk", lambda: None)


def test_names_reflects_registered_actions():
    actions = ActionSpace()
    actions.register("walk", lambda: None)
    actions.register("jump", lambda: None)
    assert actions.names == frozenset({"walk", "jump"})


def test_names_is_empty_for_a_fresh_registry():
    assert ActionSpace().names == frozenset()
