"""Tests for the Gymnasium-compatible pixel-observation env (engine/env.py).

Hermetic -- no network. The env renders frames and computes reward in code, so it's
fully deterministic. These lock in that (a) reward is the *code-defined* goal signal,
fired exactly when a goal completes, (b) an illegal move is a no-op not a crash, and
(c) the `interact` button resolves to the right concrete engine action."""

from PIL import Image

from infinienv.engine.env import CONTROLLER_ACTIONS, InfiniEnv, frame_to_png_bytes
from infinienv.schema.scene_schema import scene_spec_from_dict

import pytest


def _pickup_scene():
    return scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "pick up the can"},
            "grid": {"width": 5, "height": 3, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [{"id": "can_1", "type": "can", "x": 1, "y": 0, "portable": True}],
            "walls": [],
            "goals": [{"id": "pick", "type": "pickup", "object_id": "can_1"}],
        }
    )


def _deliver_scene():
    return scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "deliver the can to the sink"},
            "grid": {"width": 5, "height": 3, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [
                {"id": "can_1", "type": "can", "x": 1, "y": 0, "portable": True},
                {"id": "sink_1", "type": "sink", "x": 2, "y": 0, "solid": False},
            ],
            "walls": [],
            "goals": [{"id": "deliver", "type": "deliver", "object_id": "can_1", "target_id": "sink_1"}],
        }
    )


def test_reset_returns_a_frame_and_goal_info():
    env = InfiniEnv(_pickup_scene())
    obs, info = env.reset()
    assert isinstance(obs, Image.Image)
    assert info["goals"] == {"pick": False}
    assert info["all_complete"] is False
    assert len(env.frames) == 1


def test_move_actions_change_position():
    env = InfiniEnv(_pickup_scene())
    env.reset()
    obs, reward, terminated, truncated, info = env.step("right")
    assert env.state.agent_pos() == (1, 0)
    assert info["action_legal"] is True
    assert info["resolved_action"] == "move_right"


def test_illegal_move_is_a_noop_not_a_crash():
    env = InfiniEnv(_pickup_scene())
    env.reset()
    # Moving left from x=0 goes out of bounds -> blocked, but must not raise.
    obs, reward, terminated, truncated, info = env.step("left")
    assert env.state.agent_pos() == (0, 0)  # unchanged
    assert info["action_legal"] is False
    assert reward == 0.0


def test_reward_fires_exactly_when_the_goal_completes():
    env = InfiniEnv(_pickup_scene())
    env.reset()
    _, r1, term1, _, _ = env.step("right")  # onto the can, not yet picked up
    assert r1 == 0.0 and term1 is False
    _, r2, term2, _, info = env.step("interact")  # pick it up -> goal completes
    assert r2 == 1.0
    assert term2 is True
    assert info["goals"] == {"pick": True}


def test_reward_is_not_paid_twice_for_the_same_goal():
    env = InfiniEnv(_pickup_scene())
    env.reset()
    env.step("right")
    _, r_first, _, _, _ = env.step("interact")
    assert r_first == 1.0
    # Any further action must not re-award the already-completed goal.
    _, r_again, _, _, _ = env.step("wait")
    assert r_again == 0.0


def test_interact_resolves_pickup_then_drop_for_deliver():
    env = InfiniEnv(_deliver_scene())
    env.reset()
    env.step("right")  # (1,0), on the can
    _, _, _, _, info = env.step("interact")  # pick up can
    assert "can_1" in env.state.inventory
    assert info["resolved_action"] == "pick_up can_1"
    env.step("right")  # (2,0), on the sink
    _, reward, terminated, _, info = env.step("interact")  # drop on sink -> deliver
    assert info["resolved_action"] == "drop can_1"
    assert reward == 1.0
    assert terminated is True


def test_truncates_at_max_steps_without_completion():
    env = InfiniEnv(_pickup_scene(), max_steps=2)
    env.reset()
    env.step("wait")
    _, _, terminated, truncated, _ = env.step("wait")
    assert terminated is False
    assert truncated is True


def test_unknown_action_raises():
    env = InfiniEnv(_pickup_scene())
    env.reset()
    with pytest.raises(ValueError):
        env.step("jump")


def test_step_before_reset_raises():
    env = InfiniEnv(_pickup_scene())
    with pytest.raises(RuntimeError):
        env.step("wait")


def test_frame_to_png_bytes_is_real_png():
    env = InfiniEnv(_pickup_scene())
    obs, _ = env.reset()
    data = frame_to_png_bytes(obs)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_controller_actions_are_the_documented_set():
    assert CONTROLLER_ACTIONS == ("forward", "back", "left", "right", "interact", "wait")
