"""Tests for the stand-in vision policy (navigation/vision_policy.py).

Hermetic -- a fake `responder` replaces the network entirely, so no key/package is needed.
Locks in: robust action parsing, that the frame is passed to the model as PNG bytes with the
goal text, the last-action feedback note, the naive final-frame judge, and that an unknown
backend / a failing judge degrade cleanly."""

from infinienv.llm.base import ProviderError
from infinienv.navigation.vision_policy import (
    VisionPolicy,
    _build_responder,
    _parse_action,
    _parse_actions,
)

import pytest


def test_parse_action_is_robust():
    assert _parse_action("forward") == "forward"
    assert _parse_action("I will go LEFT now") == "left"
    assert _parse_action("interact!") == "interact"
    # Whole-word matching: 'left' inside 'leftover' must NOT match (that's the point).
    assert _parse_action("leftover pizza, no token here") == "wait"
    assert _parse_action("hmm, not sure") == "wait"  # nothing matches -> safe no-op
    assert _parse_action("") == "wait"


def test_parse_actions_returns_an_ordered_plan():
    # A plan is the tokens in the order the model wrote them, immediate repeats kept.
    assert _parse_actions("right right forward interact") == ["right", "right", "forward", "interact"]
    # Order follows appearance, not CONTROLLER_ACTIONS order.
    assert _parse_actions("forward left back")[:3] == ["forward", "left", "back"]
    # Capped at limit.
    assert _parse_actions("right right right right", limit=2) == ["right", "right"]
    # Nothing matches -> a safe single-action plan, never empty.
    assert _parse_actions("no tokens here") == ["wait"]
    assert _parse_actions("") == ["wait"]
    # Whole-word only: 'leftover' doesn't contribute a 'left'.
    assert _parse_actions("leftover pizza") == ["wait"]


def test_act_passes_frames_and_goal_and_returns_a_plan():
    seen = {}

    def fake(system, user, images):
        seen["system"] = system
        seen["user"] = user
        seen["images"] = images
        return "right right forward"

    pol = VisionPolicy(responder=fake)
    plan, raw = pol.act([b"\x89PNGnow", b"\x89PNGprev"], "deliver the can", step=1)
    assert plan == ["right", "right", "forward"]  # a plan, not one action
    assert raw == "right right forward"
    # The responder receives a LIST of frames (current + history), not a single image.
    assert isinstance(seen["images"], list) and len(seen["images"]) == 2
    assert all(isinstance(i, (bytes, bytearray)) for i in seen["images"])
    assert "deliver the can" in seen["user"]
    assert "Turn: 1" in seen["user"]
    assert "interact" in seen["system"]


def test_act_coerces_a_single_frame_to_a_list():
    seen = {}

    def fake(system, user, images):
        seen["images"] = images
        return "right"

    VisionPolicy(responder=fake).act(b"onlyframe", "goal", step=1)
    assert seen["images"] == [b"onlyframe"]


def test_act_respects_max_actions():
    pol = VisionPolicy(responder=lambda s, u, i: "right right right right")
    plan, _ = pol.act([b"img"], "goal", step=1, max_actions=2)
    assert plan == ["right", "right"]


def test_act_includes_feedback_in_the_user_text():
    captured = {}

    def fake(system, user, images):
        captured["user"] = user
        return "forward"

    pol = VisionPolicy(responder=fake)
    pol.act([b"img"], "goal", step=3, feedback="right(BLOCKED). You are STUCK.")
    assert "BLOCKED" in captured["user"]
    assert "STUCK" in captured["user"]


def test_build_feedback_reports_blocked_stuck_and_suggestions():
    from infinienv.navigation.vision_policy import build_feedback

    actions = ("forward", "back", "left", "right", "interact", "wait")
    # Repeated blocked action -> "do NOT repeat" + a suggestion of untried directions.
    recent = [("right", False), ("right", False), ("forward", True)]
    last = [("right", False)]
    fb = build_feedback(recent, last, looping=False, actions=actions)
    assert "Last plan result: right(BLOCKED)." in fb
    assert "do NOT repeat" in fb and "right" in fb
    assert "Options include:" in fb and "back" in fb
    # Looping flag surfaces the stuck warning + the interact hint (for pickup/deliver tasks).
    fb2 = build_feedback([("forward", True)], [("forward", True)], looping=True, actions=actions)
    assert "oscillating" in fb2 and "INTERACT" in fb2
    # No history -> empty feedback (nothing to say on the first look).
    assert build_feedback([], [], looping=False, actions=actions) == ""


def test_judge_final_frame_parses_verdict():
    pol_yes = VisionPolicy(responder=lambda s, u, i: "YES")
    verdict, raw = pol_yes.judge_final_frame(b"img", "goal")
    assert verdict is True and raw == "YES"

    pol_no = VisionPolicy(responder=lambda s, u, i: "no, not done")
    verdict, _ = pol_no.judge_final_frame(b"img", "goal")
    assert verdict is False


def test_judge_is_best_effort_on_provider_error():
    def boom(system, user, image_png):
        raise ProviderError("no key")

    pol = VisionPolicy(responder=boom)
    verdict, raw = pol.judge_final_frame(b"img", "goal")
    assert verdict is False
    assert "judge unavailable" in raw


def test_constructing_policy_needs_no_key():
    # The responder is built lazily, so constructing a policy never touches a key.
    pol = VisionPolicy(backend="openai")
    assert pol.backend == "openai"
    assert pol.model  # a default model is chosen


def test_unknown_backend_raises_provider_error():
    with pytest.raises(ProviderError):
        _build_responder("bogus", "some-model")
