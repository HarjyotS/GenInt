"""Generic named-state and declarative gating for sandbox-authored simulations
(engine/puzzle_state.py)."""

import pytest

from infinienv.engine.puzzle_state import Gate, PuzzleState


def test_set_and_get_a_flag():
    state = PuzzleState()
    state.set("switch_pressed", True)
    assert state.get("switch_pressed") is True


def test_get_returns_default_when_unset():
    state = PuzzleState()
    assert state.get("gems") is None
    assert state.get("gems", 0) == 0


def test_increment_starts_from_zero_and_accumulates():
    state = PuzzleState()
    assert state.increment("gems") == 1
    assert state.increment("gems") == 2
    assert state.increment("gems", by=3) == 5


def test_increment_on_non_numeric_value_raises():
    state = PuzzleState()
    state.set("flag", True)
    with pytest.raises(TypeError):
        state.increment("flag")


def test_snapshot_is_a_plain_dict_copy():
    state = PuzzleState()
    state.set("a", True)
    state.increment("b")
    snap = state.snapshot()
    assert snap == {"a": True, "b": 1}
    snap["a"] = False
    assert state.get("a") is True  # snapshot is a copy, not a live view


def test_gate_open_when_numeric_threshold_met_exactly():
    state = PuzzleState()
    state.increment("gems", by=2)
    gate = Gate(requires={"gems": 2})
    assert gate.is_open(state) is True


def test_gate_closed_when_numeric_threshold_not_met():
    state = PuzzleState()
    state.increment("gems")
    gate = Gate(requires={"gems": 2})
    assert gate.is_open(state) is False
    assert gate.missing(state) == ["gems"]


def test_gate_open_when_numeric_threshold_exceeded():
    state = PuzzleState()
    state.increment("gems", by=5)
    gate = Gate(requires={"gems": 2})
    assert gate.is_open(state) is True


def test_gate_closed_by_default_for_unset_numeric_requirement():
    state = PuzzleState()
    gate = Gate(requires={"gems": 1})
    assert gate.is_open(state) is False


def test_gate_open_by_default_for_zero_numeric_requirement():
    state = PuzzleState()
    gate = Gate(requires={"gems": 0})
    assert gate.is_open(state) is True


def test_gate_boolean_requirement_true():
    state = PuzzleState()
    gate = Gate(requires={"switch_pressed": True})
    assert gate.is_open(state) is False
    state.set("switch_pressed", True)
    assert gate.is_open(state) is True


def test_gate_boolean_requirement_defaults_to_false_when_unset():
    state = PuzzleState()
    gate = Gate(requires={"door_locked": False})
    assert gate.is_open(state) is True  # unset -> False, matches the requirement


def test_gate_mixed_requirements_all_must_hold():
    state = PuzzleState()
    gate = Gate(requires={"gems": 2, "switch_pressed": True})
    state.increment("gems", by=2)
    assert gate.is_open(state) is False
    assert gate.missing(state) == ["switch_pressed"]
    state.set("switch_pressed", True)
    assert gate.is_open(state) is True
    assert gate.missing(state) == []


def test_gate_non_numeric_non_boolean_requirement_uses_equality():
    state = PuzzleState()
    gate = Gate(requires={"held_item": "key_1"})
    assert gate.is_open(state) is False
    state.set("held_item", "key_1")
    assert gate.is_open(state) is True
    state.set("held_item", "key_2")
    assert gate.is_open(state) is False
