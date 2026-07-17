"""Tests for faithful vision-play (sandbox/vision_play.py + vision_runner.py).

Hermetic: the frame-loop core takes the env + controller as arguments, so it's tested with a fake
game env + a fake controller -- no game code, no network, no sandbox session. The reference
run_scene.py's make_env is also checked to be import-safe + drivable in a built workspace."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

from infinienv.sandbox.vision_play import _parse_action, _parse_actions, run_vision_episode

import pytest


class _MovingGame:
    """A game whose frame CHANGES with position (a lit pixel at x), so the stall detector only
    fires when the character is actually blocked. Optional `wall` caps how far right x can go."""

    actions = ("left", "right", "wait")

    def __init__(self, wall=None, win_at=5):
        self.x = 0
        self.wall = wall
        self.win_at = win_at

    def _frame(self):
        img = Image.new("RGB", (24, 16), (0, 0, 0))
        img.putpixel((min(self.x, 23), 0), (255, 255, 255))  # position-dependent -> frames differ
        return img

    def reset(self):
        self.x = 0
        return self._frame()

    def step(self, action):
        if action == "right":
            nxt = self.x + 1
            if self.wall is None or nxt <= self.wall:
                self.x = nxt
        elif action == "left":
            self.x = max(0, self.x - 1)
        won = self.x >= self.win_at
        return self._frame(), (1.0 if won else 0.0), won, {"won": won}


class _FakeGame:
    """A tiny side-view-ish game: reach x==3 by moving right; the win is code-defined via info."""

    actions = ("left", "right", "jump", "wait")

    def __init__(self):
        self.x = 0

    def _frame(self):
        return Image.new("RGB", (24, 16), (12, 24, 48))

    def reset(self):
        self.x = 0
        return self._frame()

    def step(self, action):
        if action == "right":
            self.x += 1
        elif action == "left":
            self.x = max(0, self.x - 1)
        won = self.x >= 3
        return self._frame(), (1.0 if won else 0.0), won, {"won": won}


def test_parse_action_matches_the_games_own_actions():
    acts = ("left", "right", "jump", "wait")
    assert _parse_action("right", acts) == "right"
    assert _parse_action("I will JUMP over it", acts) == "jump"
    assert _parse_action("nonsense here", acts) == "wait"  # idle fallback


def test_parse_actions_returns_a_plan_over_the_games_actions():
    acts = ("left", "right", "jump", "wait")
    assert _parse_actions("right right jump", acts) == ["right", "right", "jump"]
    assert _parse_actions("right right right", acts, limit=2) == ["right", "right"]
    assert _parse_actions("nothing", acts) == ["wait"]  # idle fallback, never empty


def test_plan_of_n_runs_n_steps_in_one_decision():
    # A plan of several actions is executed in a SINGLE vision call (one decision), so far fewer
    # model calls than env steps -- the whole point.
    plan_once = iter([["right", "right", "right"]])
    frames, rec = run_vision_episode(
        _MovingGame(win_at=3), lambda p, d, a, l: next(plan_once, ["wait"]),
        max_steps=10, plan_len=6,
    )
    assert rec["won"] is True
    assert rec["num_decisions"] == 1  # ONE look drove three actions
    assert rec["env_steps"] == 3
    assert len(frames) == 4  # reset + 3 sim frames


def test_a_single_string_plan_is_coerced_for_back_compat():
    # A controller that returns a bare action string (the old contract) still works.
    seq = iter(["right", "right", "right"])
    _frames, rec = run_vision_episode(
        _MovingGame(win_at=3), lambda p, d, a, l: next(seq, "wait"), max_steps=10, plan_len=6
    )
    assert rec["won"] is True
    assert rec["num_decisions"] == 3  # one action each -> three looks


def test_stall_cuts_a_plan_short_and_re_observes():
    # A plan that walks into a wall is aborted after the blocked move (frame stops changing), so the
    # rest of the plan doesn't bash the wall -- the next decision gets a fresh look.
    plans = iter([["right", "right", "right", "right"], ["wait"]])
    _frames, rec = run_vision_episode(
        _MovingGame(wall=2, win_at=99), lambda p, d, a, l: next(plans, ["wait"]),
        max_steps=20, plan_len=6,
    )
    first = rec["decisions"][0]
    # Executed right x3 (x: 0->1->2, then 2->2 blocked = the still frame), not all 4.
    assert first["executed"] == ["right", "right", "right"]
    assert rec["num_decisions"] >= 2  # it re-observed instead of finishing the stale plan


def test_run_vision_episode_win_is_from_the_games_own_info():
    seq = iter(["right", "right", "right"])
    frames, rec = run_vision_episode(_FakeGame(), lambda p, t, a, l: next(seq, "wait"), max_steps=10)
    assert rec["won"] is True  # code-defined win, from info["won"], not the pixels
    assert rec["num_steps"] == 3
    assert rec["total_reward"] == 1.0
    assert len(frames) == 4  # reset + 3 steps


def test_run_vision_episode_passes_png_frames_and_action_list():
    seen = {}

    def act(frames, step, actions, feedback):
        # frames is now a LIST of png bytes (current + history); check the current one.
        seen["is_list"] = isinstance(frames, list) and len(frames) >= 1
        seen["png_ok"] = frames[0][:8] == b"\x89PNG\r\n\x1a\n"
        seen["actions"] = actions
        return "wait"

    run_vision_episode(_FakeGame(), act, max_steps=1)
    assert seen["is_list"] is True
    assert seen["png_ok"] is True
    assert seen["actions"] == ("left", "right", "jump", "wait")


class _InfoGame:
    """A game with a WALL at x==2 and a live HUD (the frame changes EVERY step regardless of
    movement), reporting `info['moved']`. A frame diff would wrongly think a blocked move moved --
    the driver must use `info`. Win needs x>=5, past the wall, so `right` past x==2 is always blocked."""

    actions = ("left", "right", "wait")

    def __init__(self):
        self.x = 0
        self.t = 0

    def _frame(self):
        img = Image.new("RGB", (48, 16), (0, 0, 0))
        for i in range(self.t % 48):  # a HUD-like strip that changes every frame
            img.putpixel((i, 15), (255, 255, 255))
        img.putpixel((min(self.x, 47), 0), (0, 255, 0))
        return img

    def reset(self):
        self.x = 0
        self.t = 0
        return self._frame()

    def step(self, action):
        self.t += 1
        moved = False
        if action == "right" and self.x < 2:
            self.x += 1
            moved = True
        elif action == "left" and self.x > 0:
            self.x -= 1
            moved = True
        won = self.x >= 5
        return self._frame(), (0.0), won, {"won": won, "moved": moved}


def test_moved_prefers_info_over_frame_diff():
    from infinienv.sandbox.vision_play import _moved

    # info wins even when the frame changed (a HUD) -- moved:False means blocked.
    a = Image.new("RGB", (8, 8), (0, 0, 0))
    b = Image.new("RGB", (8, 8), (1, 1, 1))
    assert _moved({"moved": False}, a, b, None, None) is False
    assert _moved({"moved": True}, a, b, None, None) is True
    assert _moved({"blocked": True}, a, b, None, None) is False
    # position change when no moved/blocked flag.
    assert _moved({}, a, b, (1, 1), (1, 2)) is True
    assert _moved({}, a, b, (1, 1), (1, 1)) is False
    # last resort: identical frame -> not moved.
    assert _moved({}, a, a, None, None) is False


def test_blocked_move_aborts_plan_via_info_and_is_fed_back():
    seen = {"feedbacks": []}
    plans = iter([["right", "right", "right", "right"], ["wait"], ["wait"]])

    def act(frames, d, actions, feedback):
        seen["feedbacks"].append(feedback)
        return next(plans, ["wait"])

    _frames, rec = run_vision_episode(_InfoGame(), act, max_steps=20, plan_len=6)
    first = rec["decisions"][0]
    # right x2 moved (x:0->1->2), the 3rd right hit the wall (info moved:False) -> aborted, not 4.
    assert first["executed"] == ["right", "right", "right"]
    assert rec["blocked_steps"] >= 1
    # The SECOND look's feedback must name the blocked move (so the policy can turn).
    assert any("BLOCKED" in (f or "") for f in seen["feedbacks"][1:])


def test_hold_block_counts_as_moved_if_it_moved_at_all():
    # Holding a direction can advance several cells and only be blocked on the LAST held frame (a
    # corridor ending in a wall). That still moved -- it must NOT be counted as blocked or abort the
    # plan (the bug the live cartel run exposed: reading only the last held frame over-counted blocks).
    class _Corridor:
        actions = ("right", "wait")

        def __init__(self):
            self.x = 0

        def _f(self):
            img = Image.new("RGB", (16, 8), (0, 0, 0))
            img.putpixel((min(self.x, 15), 0), (0, 255, 0))
            return img

        def reset(self):
            self.x = 0
            return self._f()

        def step(self, a):
            moved = False
            if a == "right" and self.x < 3:  # wall at x==3
                self.x += 1
                moved = True
            return self._f(), 0.0, False, {"won": False, "moved": moved}

    # one decision, one 'right' held 6 frames: x 0->3 (moved) then blocked on the tail frames.
    _frames, rec = run_vision_episode(_Corridor(), lambda p, d, a, f: ["right"], max_steps=1, hold=6)
    assert rec["decisions"][0]["executed"] == ["right"]
    assert rec["blocked_steps"] == 0  # it moved 3 cells -- not a blocked step


class _GridGame:
    """A grid game that exposes maze state (position in info + walls/walkable/goal on the env), so
    the faithful driver can build a text minimap for the policy to route on."""

    actions = ("up", "down", "left", "right")
    # 4x3: solid border, single open cell (1,1) which is also the goal.
    obstacle_cells = {(0, 0), (1, 0), (2, 0), (3, 0), (0, 1), (2, 1), (3, 1),
                      (0, 2), (1, 2), (2, 2), (3, 2)}
    walkable = {(1, 1)}
    package = (1, 1)

    def __init__(self):
        self.position = (1, 1)

    def reset(self):
        self.position = (1, 1)
        return Image.new("RGB", (8, 8))

    def step(self, a):
        return Image.new("RGB", (8, 8)), 0.0, False, {"won": False, "moved": False, "position": self.position}


def test_minimap_builds_a_grid_from_env_state():
    from infinienv.sandbox.vision_play import _minimap

    mm = _minimap(_GridGame(), {"position": (1, 1)})
    assert "Map (" in mm and "A" in mm  # the agent marker is drawn
    assert "at (1, 1)" in mm
    # the open cell is '.', the border is '#'
    body = mm.splitlines()
    assert any(set(r) <= set("#") for r in body)  # a full-wall row exists


def test_carry_note_surfaces_holding_state():
    from infinienv.sandbox.vision_play import _carry_note

    # empty hands -> a hint to go pick the item up (the kitchen-deliver failure mode).
    assert "EMPTY" in _carry_note({"carried": None})
    assert "EMPTY" in _carry_note({"carrying": []})
    # holding something -> a hint to take it to the target and place it.
    assert "soda" in _carry_note({"carried": "soda"})
    assert "carrying" in _carry_note({"holding": ["soda"]}).lower()
    # no carry key -> nothing.
    assert _carry_note({"won": False}) == ""


def test_minimap_empty_for_a_game_without_grid_state():
    from infinienv.sandbox.vision_play import _minimap

    # _FakeGame exposes no position / walls / goal -> no minimap (pixels-only).
    assert _minimap(_FakeGame(), {"won": False}) == ""


def test_minimap_reaches_the_controller_as_feedback():
    seen = {}

    def act(frames, d, actions, feedback):
        seen["fb"] = feedback
        return ["up"]

    _frames, rec = run_vision_episode(_GridGame(), act, max_steps=1)
    assert rec["had_minimap"] is True
    assert "Map (" in seen["fb"]  # the policy was handed the routable minimap


def test_deterministic_minimap_marks_agent_and_goal():
    import json
    import os

    from infinienv.engine.env import InfiniEnv
    from infinienv.evaluation.vision_runner import _deterministic_minimap
    from infinienv.schema.scene_schema import scene_spec_from_dict

    example = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "kitchen_can.json")
    scene = scene_spec_from_dict(json.load(open(example)))
    env = InfiniEnv(scene)
    env.reset()
    mm = _deterministic_minimap(env, scene)
    assert "A" in mm and "P" in mm and "Map (" in mm
    # deliver goal: before pickup, the goal marker is the CAN (the fetch target), not the sink.
    can = scene_can_pos = next(o for o in scene.objects if o.type == "can")
    assert f"at ({can.x}, {can.y})" in mm


def test_history_frames_are_passed_to_the_controller():
    seen = {"lens": []}

    def act(frames, d, actions, feedback):
        seen["lens"].append(len(frames))
        return ["right"]

    run_vision_episode(_MovingGame(win_at=99), act, max_steps=5, history=2)
    # First look sees just the current frame; later looks accumulate up to 1+history=3 frames.
    assert seen["lens"][0] == 1
    assert max(seen["lens"]) >= 2


def test_run_vision_episode_coerces_an_illegal_action():
    # A policy that returns an out-of-set token still advances via _parse_action's fallback/match.
    seq = iter(["banana", "right", "right", "right"])
    _frames, rec = run_vision_episode(_FakeGame(), lambda p, t, a, l: next(seq, "wait"), max_steps=10)
    assert rec["won"] is True


def test_frame_skip_holds_the_action_and_grabs_frames_occasionally():
    # The policy should observe OCCASIONALLY: with hold=5, each decision advances the sim 5 frames
    # while holding the chosen action -- far fewer vision calls than sim frames.
    class _Runner(_FakeGame):
        def step(self, action):
            if action == "right":
                self.x += 1
            won = self.x >= 10
            return self._frame(), (1.0 if won else 0.0), won, {"won": won}

    calls = {"n": 0}

    def act(png, d, actions, last):
        calls["n"] += 1
        return "right"

    frames, rec = run_vision_episode(_Runner(), act, max_steps=60, hold=5)
    assert rec["won"] is True
    assert calls["n"] == 2  # 2 occasional grabs (5 held frames each) reach x>=10, not 10 grabs
    assert rec["num_decisions"] == 2
    assert rec["sim_frames"] == 10  # the game advanced 10 frames
    assert rec["hold"] == 5
    assert len(frames) == 11  # reset + 10 sim frames (all collected for the gif)


def test_resolve_dt_prefers_env_then_module_constant():
    from types import SimpleNamespace

    from infinienv.sandbox.vision_play import _resolve_dt

    # env.dt wins.
    assert _resolve_dt(SimpleNamespace(dt=0.02), SimpleNamespace(DT=1 / 30)) == 0.02
    # env.fps -> 1/fps.
    assert _resolve_dt(SimpleNamespace(fps=50), None) == 1 / 50
    # no env.dt/fps -> fall back to the game module's DT (this is the `absence` case: DT=1/30).
    assert _resolve_dt(SimpleNamespace(), SimpleNamespace(DT=1 / 30)) == 1 / 30
    # module FPS fallback -> 1/FPS.
    assert _resolve_dt(SimpleNamespace(), SimpleNamespace(FPS=60)) == 1 / 60
    # nothing usable -> default; bad values ignored.
    assert _resolve_dt(SimpleNamespace(dt="oops"), SimpleNamespace(DT=0)) == 0.05


def test_hold_default_1_is_the_old_one_decision_per_frame():
    # Backward compatibility: hold defaults to 1 (one decision per sim frame).
    seq = iter(["right", "right", "right"])
    _frames, rec = run_vision_episode(_FakeGame(), lambda p, d, a, l: next(seq, "wait"), max_steps=10)
    assert rec["hold"] == 1
    assert rec["num_decisions"] == 3
    assert rec["sim_frames"] == 3


def test_run_vision_episode_truncates_without_a_win():
    _frames, rec = run_vision_episode(_FakeGame(), lambda p, t, a, l: "wait", max_steps=3)
    assert rec["won"] is False
    assert rec["done"] is False
    assert rec["num_steps"] == 3


def test_run_vision_episode_survives_a_game_step_error():
    class _Broken(_FakeGame):
        def step(self, action):
            raise RuntimeError("boom")

    _frames, rec = run_vision_episode(_Broken(), lambda p, t, a, l: "right", max_steps=5)
    assert rec["won"] is False
    assert "game step error" in (rec["error"] or "")


def test_reference_make_env_is_import_safe_and_drivable(tmp_path):
    # The reference run_scene.py template must expose a module-level make_env() that imports WITHOUT
    # re-running generation, and returns a drivable env -- the make_env contract.
    from infinienv.sandbox.workspace import build_workspace_dir

    ws = build_workspace_dir(str(tmp_path))
    example = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "kitchen_can.json")
    shutil.copy(example, os.path.join(ws, "scene.json"))
    probe = (
        "from run_scene import make_env; e=make_env(); "
        "f=e.reset(); obs,r,d,info=e.step(e.actions[0]); "
        "print('OK', tuple(e.actions)[:2], f.size, d, sorted(info))"
    )
    res = subprocess.run([sys.executable, "-c", probe], cwd=ws, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("OK")
    # importing make_env must NOT have re-run generation (no artifact writes / no "wrote ..." print)
    assert "wrote scene.json" not in res.stdout
    assert not os.path.exists(os.path.join(ws, "metrics.json"))


def test_reference_make_env_resolves_real_assets(tmp_path):
    # The make_env contract requires rendering with the game's real assets (not primitives). The
    # reference make_env must resolve ASSETS_MODE from asset_cache and pass asset_paths to InfiniEnv.
    # `local` mode uses checked-in placeholder sprites -- no network.
    from infinienv.sandbox.workspace import build_workspace_dir

    ws = build_workspace_dir(str(tmp_path), assets_mode="local")
    example = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "kitchen_can.json")
    shutil.copy(example, os.path.join(ws, "scene.json"))
    probe = (
        "from run_scene import make_env; e=make_env(); "
        "print('APATHS', bool(e._env.asset_paths), 'AGENT', 'agent' in e._env.asset_paths)"
    )
    res = subprocess.run([sys.executable, "-c", probe], cwd=ws, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    # asset_paths is non-empty and includes the character ('agent') sprite -- not primitives.
    assert "APATHS True" in res.stdout
    assert "AGENT True" in res.stdout


def test_play_sandbox_world_rejects_a_non_sandbox_dir(tmp_path, monkeypatch):
    from infinienv.llm.base import ProviderError
    from infinienv.sandbox.vision_runner import play_sandbox_world

    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs" / "plain").mkdir(parents=True)
    with pytest.raises(ProviderError):
        play_sandbox_world("runs/plain", "runs/out")


# --- verify_playthrough (the played-through proof's checker): hermetic, faking _play_async so no
# sandbox session or vision call ever happens.


def _patch_play(monkeypatch, results):
    """Patch vision_runner._play_async to return/raise each entry of `results` in order."""
    import infinienv.sandbox.vision_runner as vr

    calls = {"n": 0}

    async def fake_play(run_dir, out_dir, **kw):
        idx = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        item = results[idx]
        if isinstance(item, Exception):
            raise item
        return dict(item)

    monkeypatch.setattr(vr, "_play_async", fake_play)
    return calls


def _run_verify(**kw):
    import asyncio

    from infinienv.sandbox.vision_runner import verify_playthrough

    return asyncio.run(verify_playthrough("runs/x", **kw))


def test_verify_playthrough_disabled_via_env(monkeypatch):
    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "0")
    res = _run_verify()
    assert res["attempted"] is False and res["won"] is None
    assert "disabled" in res["note"]


def test_verify_playthrough_skips_without_a_vision_key(monkeypatch):
    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    res = _run_verify()
    assert res["attempted"] is False
    assert "OPENAI_API_KEY" in res["note"]


def test_verify_playthrough_win_on_first_try(monkeypatch):
    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_play(monkeypatch, [{"vision_success": True, "env_steps": 30, "decisions": 5}])
    res = _run_verify(tries=2)
    assert res["attempted"] is True and res["won"] is True and res["tries"] == 1
    assert "30 env actions" in res["note"]


def test_verify_playthrough_stochastic_retry_then_win(monkeypatch):
    # The stand-in policy is stochastic: one loss must not fail the proof if a later try wins.
    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _patch_play(
        monkeypatch,
        [
            {"vision_success": False, "env_steps": 60, "decisions": 10, "blocked_steps": 40,
             "total_reward": 0.0, "had_minimap": False},
            {"vision_success": True, "env_steps": 25, "decisions": 4},
        ],
    )
    res = _run_verify(tries=2)
    assert calls["n"] == 2
    assert res["won"] is True and res["tries"] == 2


def test_verify_playthrough_persistent_loss_carries_evidence(monkeypatch):
    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_play(
        monkeypatch,
        [{"vision_success": False, "env_steps": 60, "decisions": 10, "blocked_steps": 41,
          "total_reward": 0.0, "episode_error": None, "had_minimap": False}],
    )
    res = _run_verify(tries=2)
    assert res["attempted"] is True and res["won"] is False and res["tries"] == 2
    assert "41" in res["evidence"]  # blocked count fed back as repair evidence
    assert "minimap" in res["evidence"]  # the no-grid-state hint is included


def test_is_grid_game_detects_exposed_cell_state():
    # A game exposing integer wall/walkable cells is turn-based: hold/action-repeat must not apply
    # (holding an action 6 frames lurched a knight 6 cells per decision in a live run).
    from infinienv.sandbox.vision_play import _is_grid_game

    class GridEnv:
        walls = {(0, 0), (1, 0)}

    class WalkableEnv:
        walkable = {(2, 2), (2, 3)}

    class ContinuousEnv:
        dt = 0.05  # a platformer exposing no cell sets

    assert _is_grid_game(GridEnv()) is True
    assert _is_grid_game(WalkableEnv()) is True
    assert _is_grid_game(ContinuousEnv()) is False


def test_verify_playthrough_instant_death_hints_safe_spawn(monkeypatch):
    # An episode that ends within a few actions with negative reward = the player died right at
    # spawn (observed live: a slime kill in 5 actions, twice, deterministically). The evidence must
    # name the safe-spawn fix.
    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_play(
        monkeypatch,
        [{"vision_success": False, "env_steps": 5, "decisions": 3, "blocked_steps": 1,
          "total_reward": -5.0, "episode_error": None, "had_minimap": True}],
    )
    res = _run_verify(tries=1)
    assert res["won"] is False
    assert "died" in res["evidence"] and "spawn" in res["evidence"]


def test_verify_playthrough_zero_reward_with_minimap_hints_dynamic_goal(monkeypatch):
    # A minimap was present yet no objective ever advanced: the evidence must teach the classic
    # cause -- a static final-goal marker in a multi-stage game routing the player into a locked
    # gate -- and the fix (env.goal = the CURRENT objective).
    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_play(
        monkeypatch,
        [{"vision_success": False, "env_steps": 60, "decisions": 31, "blocked_steps": 0,
          "total_reward": 0.0, "episode_error": None, "had_minimap": True}],
    )
    res = _run_verify(tries=1)
    assert res["won"] is False
    assert "CURRENT" in res["evidence"] and "env.goal" in res["evidence"]


def test_verify_playthrough_missing_make_env_is_a_defect_not_a_skip(monkeypatch):
    from infinienv.llm.base import ProviderError

    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_play(monkeypatch, [ProviderError("run_scene.make_env is missing -- regenerate")])
    res = _run_verify(tries=2)
    # a world with no playable interface is the generating agent's defect: it must FAIL and repair
    assert res["attempted"] is True and res["won"] is False
    assert "make_env" in res["evidence"]


def test_verify_playthrough_infra_failure_is_a_skip_not_a_loss(monkeypatch):
    from infinienv.llm.base import ProviderError

    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_play(
        monkeypatch,
        [ProviderError("The 'openai-agents' package (with sandbox support) is not installed.")],
    )
    res = _run_verify(tries=2)
    assert res["attempted"] is False and res["won"] is None
    assert "could not run" in res["note"]


def test_verify_playthrough_game_crash_under_play_is_a_defect(monkeypatch):
    from infinienv.llm.base import ProviderError

    monkeypatch.setenv("INFINIENV_SANDBOX_PLAYTHROUGH", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _patch_play(
        monkeypatch,
        [ProviderError("the vision-play driver did not finish (exit 1): KeyError: 'plank'")],
    )
    res = _run_verify(tries=2)
    assert res["attempted"] is True and res["won"] is False
    assert "env.step must survive" in res["evidence"]


def test_tar_workspace_uses_the_real_folder_and_injects_driver(tmp_path):
    # The hydration tar is built from the run's real sandbox_workspace/ (no host-side copy), with
    # the driver + config injected in-memory, __pycache__/.pyc excluded, and the folder left clean.
    import tarfile

    from infinienv.sandbox.vision_runner import _tar_workspace_with

    ws = tmp_path / "sandbox_workspace"
    (ws / "engine" / "__pycache__").mkdir(parents=True)
    (ws / "run_scene.py").write_text("def make_env(): ...")
    (ws / "engine" / "__pycache__" / "x.pyc").write_bytes(b"stale")

    buf = _tar_workspace_with(str(ws), {"vision_play.py": b"# driver", "vision_config.json": b"{}"})
    names = tarfile.open(fileobj=buf).getnames()
    assert any(n.endswith("run_scene.py") for n in names)  # real workspace file
    assert "./vision_play.py" in names and "./vision_config.json" in names  # injected
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)  # excluded
    # the real folder was NOT modified (driver/config exist only inside the tar)
    assert not (ws / "vision_play.py").exists()
