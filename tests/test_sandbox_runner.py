import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from infinienv.sandbox.runner import _repair_message, run_sandbox_generation
from infinienv.sandbox.workspace import tar_directory


def test_repair_message_describes_run_error_distinctly_from_sanity_error():
    msg = _repair_message(run_error="boom", sanity_error=None)
    assert "did not finish cleanly" in msg
    assert "boom" in msg

    msg = _repair_message(run_error=None, sanity_error="scene.json does not parse")
    assert "did not pass an independent outer check" in msg
    assert "scene.json does not parse" in msg


def _valid_scene_json() -> str:
    return json.dumps(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": 4, "height": 4, "tile_size": 32},
            "agent": {"id": "agent", "x": 1, "y": 1},
            "objects": [],
            "walls": [],
            "goals": [{"id": "g", "type": "reach", "target_id": "agent"}],
        }
    )


def _real_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _real_gif_bytes() -> bytes:
    buf = io.BytesIO()
    frame1 = Image.new("RGB", (64, 64), (255, 0, 0))
    frame2 = Image.new("RGB", (64, 64), (0, 255, 0))
    frame1.save(buf, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    return buf.getvalue()


class _FakeReadHandle:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class _FakeSandboxSession:
    def __init__(self):
        self.files: dict[str, bytes | str] = {}

    async def start(self):
        pass

    async def hydrate_workspace(self, data):
        pass

    async def aclose(self):
        pass

    async def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return _FakeReadHandle(self.files[path])

    async def persist_workspace(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            buf = tar_directory(d)
        return _FakeReadHandle(buf.read())


class _FakeSandboxClient:
    def __init__(self, *args, **kwargs):
        self.session = _FakeSandboxSession()

    async def create(self):
        return self.session


def _write_bad_attempt(files: dict) -> None:
    files["scene.json"] = json.dumps({"not": "a valid scene"})
    files["metrics.json"] = json.dumps({"success": True})
    files["replay.json"] = json.dumps({"actions": []})
    files["render.png"] = _real_png_bytes()
    files["replay.gif"] = _real_gif_bytes()


def _write_good_attempt(files: dict) -> None:
    files["scene.json"] = _valid_scene_json()
    files["metrics.json"] = json.dumps({"success": True})
    files["replay.json"] = json.dumps({"actions": []})
    files["render.png"] = _real_png_bytes()
    files["replay.gif"] = _real_gif_bytes()


@pytest.fixture
def patched_sdk():
    try:
        from agents import Runner
        from agents.sandbox.sandboxes import unix_local
    except ImportError:
        pytest.skip("openai-agents sandbox support not installed")

    with (
        patch("agents.sandbox.SandboxAgent", lambda **kwargs: SimpleNamespace(**kwargs)),
        patch("agents.sandbox.SandboxRunConfig", lambda **kwargs: SimpleNamespace(**kwargs)),
        patch("agents.run.RunConfig", lambda **kwargs: SimpleNamespace(**kwargs)),
        patch("agents.sandbox.capabilities.Filesystem", lambda: SimpleNamespace()),
        patch("agents.sandbox.capabilities.Shell", lambda: SimpleNamespace()),
        patch.object(unix_local, "UnixLocalSandboxClient", _FakeSandboxClient),
    ):
        yield Runner


def test_repair_loop_retries_and_succeeds_after_a_bad_first_attempt(tmp_path, patched_sdk):
    Runner = patched_sdk
    attempts: list[str] = []

    async def fake_run(agent, message, *, run_config, max_turns):
        attempts.append(message)
        session = run_config.sandbox.session
        if len(attempts) == 1:
            _write_bad_attempt(session.files)
        else:
            _write_good_attempt(session.files)
        return SimpleNamespace(final_output=f"attempt {len(attempts)} summary")

    with patch.object(Runner, "run", AsyncMock(side_effect=fake_run)):
        result = run_sandbox_generation(
            "make a game", 1, str(tmp_path / "run"), max_repair_attempts=2
        )

    assert len(attempts) == 2
    assert "did not pass an independent outer check" in attempts[1]
    assert result["success"] is True
    assert result["repair_attempts"] == 1
    assert result["metrics"]["repair_history"][0]["outer_sanity_passed"] is False
    assert result["metrics"]["repair_history"][1]["outer_sanity_passed"] is True


def test_repair_loop_gives_up_honestly_after_budget_exhausted(tmp_path, patched_sdk):
    Runner = patched_sdk
    attempts: list[str] = []

    async def fake_run(agent, message, *, run_config, max_turns):
        attempts.append(message)
        session = run_config.sandbox.session
        _write_bad_attempt(session.files)
        return SimpleNamespace(final_output=f"attempt {len(attempts)} summary")

    with patch.object(Runner, "run", AsyncMock(side_effect=fake_run)):
        result = run_sandbox_generation(
            "make a game", 1, str(tmp_path / "run"), max_repair_attempts=1
        )

    assert len(attempts) == 2  # initial + 1 repair attempt, budget exhausted
    assert result["success"] is False
    assert result["repair_attempts"] == 1
    assert result["metrics"]["outer_sanity_passed"] is False


def test_repair_loop_succeeds_immediately_without_using_the_repair_budget(tmp_path, patched_sdk):
    Runner = patched_sdk
    attempts: list[str] = []

    async def fake_run(agent, message, *, run_config, max_turns):
        attempts.append(message)
        session = run_config.sandbox.session
        _write_good_attempt(session.files)
        return SimpleNamespace(final_output="summary")

    with patch.object(Runner, "run", AsyncMock(side_effect=fake_run)):
        result = run_sandbox_generation(
            "make a game", 1, str(tmp_path / "run"), max_repair_attempts=2
        )

    assert len(attempts) == 1
    assert result["success"] is True
    assert result["repair_attempts"] == 0


def test_assets_mode_threads_through_to_workspace_and_agent_message(tmp_path, patched_sdk):
    Runner = patched_sdk
    attempts: list[str] = []

    async def fake_run(agent, message, *, run_config, max_turns):
        attempts.append(message)
        session = run_config.sandbox.session
        _write_good_attempt(session.files)
        return SimpleNamespace(final_output="summary")

    out_dir = str(tmp_path / "run")

    # persist_workspace's fake implementation always tars an unrelated empty temp dir (it has
    # no access to the real workspace_dir), so sync_full_workspace would wipe ASSETS_MODE off
    # disk afterward -- inspect the pre-run workspace, written by build_workspace_dir, before
    # that happens rather than asserting on post-sync disk state.
    written_assets_mode: list[str] = []
    real_build_workspace_dir = __import__(
        "infinienv.sandbox.runner", fromlist=["build_workspace_dir"]
    ).build_workspace_dir

    def spy_build_workspace_dir(out_dir, *, assets_mode="none"):
        workspace_dir = real_build_workspace_dir(out_dir, assets_mode=assets_mode)
        with open(f"{workspace_dir}/ASSETS_MODE") as f:
            written_assets_mode.append(f.read())
        return workspace_dir

    with (
        patch.object(Runner, "run", AsyncMock(side_effect=fake_run)),
        patch("infinienv.sandbox.runner.build_workspace_dir", spy_build_workspace_dir),
    ):
        result = run_sandbox_generation(
            "make a game", 1, out_dir, max_repair_attempts=2, assets_mode="local"
        )

    assert result["success"] is True
    assert "Assets mode: local" in attempts[0]
    assert written_assets_mode == ["local"]


def test_assets_mode_defaults_to_none(tmp_path, patched_sdk):
    Runner = patched_sdk
    attempts: list[str] = []

    async def fake_run(agent, message, *, run_config, max_turns):
        attempts.append(message)
        session = run_config.sandbox.session
        _write_good_attempt(session.files)
        return SimpleNamespace(final_output="summary")

    with patch.object(Runner, "run", AsyncMock(side_effect=fake_run)):
        run_sandbox_generation("make a game", 1, str(tmp_path / "run"), max_repair_attempts=2)

    assert "Assets mode: none" in attempts[0]
