"""Generic reactive behavior state machine for sandbox-authored NPCs (engine/agent_behavior.py)."""

from infinienv.engine.agent_behavior import BehaviorMachine


def _guard():
    m = BehaviorMachine("patrol")
    m.add_transition("patrol", "chase", when=lambda ctx: ctx["sees"])
    m.add_transition("chase", "patrol", when=lambda ctx: not ctx["sees"])
    return m


def test_starts_in_initial_state():
    assert BehaviorMachine("patrol").state == "patrol"


def test_transition_fires_when_condition_holds():
    m = _guard()
    assert m.update({"sees": True}) == "chase"
    assert m.state == "chase"


def test_no_transition_when_condition_false():
    m = _guard()
    assert m.update({"sees": False}) == "patrol"
    assert m.state == "patrol"


def test_transition_back():
    m = _guard()
    m.update({"sees": True})
    assert m.update({"sees": False}) == "patrol"


def test_only_transitions_from_the_current_state_apply():
    # while in "patrol", the chase->patrol transition must not fire even though its condition holds
    m = _guard()
    assert m.update({"sees": False}) == "patrol"  # stays put, chase->patrol not considered


def test_first_matching_transition_wins_in_registration_order():
    m = BehaviorMachine("idle")
    # both conditions true; the first-registered transition should win
    m.add_transition("idle", "flee", when=lambda ctx: True)
    m.add_transition("idle", "chase", when=lambda ctx: True)
    assert m.update({}) == "flee"


def test_only_one_transition_per_update():
    # idle -> alert -> chase both available, but a single update advances only one step
    m = BehaviorMachine("idle")
    m.add_transition("idle", "alert", when=lambda ctx: True)
    m.add_transition("alert", "chase", when=lambda ctx: True)
    assert m.update({}) == "alert"
    assert m.update({}) == "chase"


def test_states_property_lists_every_named_state():
    m = _guard()
    assert m.states == frozenset({"patrol", "chase"})
