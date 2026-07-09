import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from infinienv.sandbox.runner import (
    _describe_stream_event,
    _interpreter_briefing,
    _repair_message,
    run_sandbox_generation,
)
from infinienv.sandbox.workspace import tar_directory


def test_repair_message_describes_run_error_distinctly_from_sanity_error():
    msg = _repair_message(run_error="boom", sanity_error=None)
    assert "did not finish cleanly" in msg
    assert "boom" in msg

    msg = _repair_message(run_error=None, sanity_error="scene.json does not parse")
    assert "did not pass an independent outer check" in msg
    assert "scene.json does not parse" in msg


def test_interpreter_briefing_names_the_harness_interpreter_and_forbids_hunting():
    import sys

    briefing = _interpreter_briefing()
    assert sys.executable in briefing
    assert "-S" in briefing
    assert "PYTHONHOME" in briefing
    assert "PYTHONPATH" in briefing
    assert "PYTHONNOUSERSITE" in briefing


def test_interpreter_briefing_reports_real_pymunk_availability():
    import importlib

    briefing = _interpreter_briefing()
    if importlib.util.find_spec("pymunk") is not None:
        assert "pymunk is installed and importable" in briefing
    else:
        assert "pymunk is NOT installed" in briefing


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


_created_fake_clients: list = []


class _FakeSandboxClient:
    def __init__(self, *args, **kwargs):
        self.session = _FakeSandboxSession()
        self.last_create_manifest = None
        _created_fake_clients.append(self)

    async def create(self, manifest=None):
        self.last_create_manifest = manifest
        return self.session


class _FakeStreamedResult:
    """Stands in for `agents.result.RunResultStreaming`: yields a fixed sequence of fake
    stream events (for narration tests), then awaits the wrapped coroutine (an existing
    `fake_run(agent, message, *, run_config, max_turns)`-shaped async function that performs
    the test's real side effects and returns `SimpleNamespace(final_output=...)`) to populate
    `final_output`, mirroring how `Runner.run_streamed` behaves for real.
    """

    def __init__(self, coro, events=()):
        self._coro = coro
        self._events = list(events)
        self.final_output = None

    async def stream_events(self):
        for event in self._events:
            yield event
        result = await self._coro
        self.final_output = result.final_output


def _streamed(fake_run, events=()):
    """Adapts a test's `fake_run` coroutine function (previously wired to `Runner.run`) into
    a `Runner.run_streamed` replacement, so existing test bodies don't need to change -- only
    which SDK entrypoint they're patched onto.
    """

    def run_streamed(agent, message, *, run_config, max_turns):
        return _FakeStreamedResult(
            fake_run(agent, message, run_config=run_config, max_turns=max_turns), events=events
        )

    return run_streamed


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
def patched_sdk(tmp_path, monkeypatch):
    try:
        from agents import Runner
        from agents.sandbox.sandboxes import unix_local
    except ImportError:
        pytest.skip("openai-agents sandbox support not installed")

    # sandbox/runner.py now runs every out_dir through resolve_out_dir(), which requires the
    # resolved path to be under cwd (same convention test_cli.py already uses for the
    # non-sandbox path) -- chdir into tmp_path so tests' `str(tmp_path / "run")` out_dirs
    # satisfy that check.
    monkeypatch.chdir(tmp_path)

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

    with patch.object(Runner, "run_streamed", _streamed(fake_run)):
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

    with patch.object(Runner, "run_streamed", _streamed(fake_run)):
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

    with patch.object(Runner, "run_streamed", _streamed(fake_run)):
        result = run_sandbox_generation(
            "make a game", 1, str(tmp_path / "run"), max_repair_attempts=2
        )

    assert len(attempts) == 1
    assert result["success"] is True
    assert result["repair_attempts"] == 0


def test_session_is_created_with_a_read_only_grant_for_the_harness_python_prefix(tmp_path, patched_sdk):
    # Regression test for a real bug found live: on macOS, exec_command runs every shell
    # command through a Seatbelt (sandbox-exec) profile that denies reading anything under
    # the real filesystem outside the ephemeral workspace root except a narrow allowlist --
    # which doesn't cover a project-local venv's own lib/site-packages, crashing the
    # interpreter during its own startup (`Fatal Python error: init_import_site`, root cause a
    # PermissionError reading pyvenv.cfg) regardless of which interpreter the agent is told to
    # use. Fixed by granting read-only access to sys.prefix via the session's Manifest. See
    # notes.md for the full diagnosis, including a from-scratch repro against the SDK's real
    # profile generator.
    import sys

    Runner = patched_sdk
    _created_fake_clients.clear()

    async def fake_run(agent, message, *, run_config, max_turns):
        session = run_config.sandbox.session
        _write_good_attempt(session.files)
        return SimpleNamespace(final_output="summary")

    with patch.object(Runner, "run_streamed", _streamed(fake_run)):
        run_sandbox_generation("make a game", 1, str(tmp_path / "run"), max_repair_attempts=0)

    assert len(_created_fake_clients) == 1
    manifest = _created_fake_clients[0].last_create_manifest
    assert manifest is not None
    grants = manifest.extra_path_grants
    assert len(grants) == 1
    assert grants[0].path == sys.prefix
    assert grants[0].read_only is True


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
        patch.object(Runner, "run_streamed", _streamed(fake_run)),
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

    with patch.object(Runner, "run_streamed", _streamed(fake_run)):
        run_sandbox_generation("make a game", 1, str(tmp_path / "run"), max_repair_attempts=2)

    assert "Assets mode: none" in attempts[0]


def test_initial_message_tells_the_agent_which_python_interpreter_to_use(tmp_path, patched_sdk):
    # Regression test for a real, user-reported issue: without this, the agent burns turns
    # hunting through `which -a python`, other interpreters, and `-S` (which disables
    # site-packages on any interpreter), never finding pymunk even though the harness's own
    # interpreter -- which the sandbox's shell commands actually inherit -- has it the whole
    # time. See notes.md.
    import sys

    Runner = patched_sdk
    attempts: list[str] = []

    async def fake_run(agent, message, *, run_config, max_turns):
        attempts.append(message)
        session = run_config.sandbox.session
        _write_good_attempt(session.files)
        return SimpleNamespace(final_output="summary")

    with patch.object(Runner, "run_streamed", _streamed(fake_run)):
        run_sandbox_generation("make a game", 1, str(tmp_path / "run"), max_repair_attempts=2)

    assert f"Python interpreter: {sys.executable}" in attempts[0]
    assert "-S" in attempts[0]
    assert "PYTHONNOUSERSITE" in attempts[0]


def _fake_event(name: str, item) -> SimpleNamespace:
    return SimpleNamespace(type="run_item_stream_event", name=name, item=item)


class TestDescribeStreamEvent:
    """Unit coverage for the narration layer itself -- pure functions over duck-typed
    SimpleNamespace stand-ins for real SDK item shapes, so these don't need the optional
    `agents` package installed at all (see the module docstring on _describe_stream_event)."""

    def test_exec_command_call_shows_the_shell_command(self):
        item = SimpleNamespace(raw_item=SimpleNamespace(name="exec_command", arguments=json.dumps({"cmd": "python run_scene.py"})))
        assert _describe_stream_event(_fake_event("tool_called", item)) == "$ python run_scene.py"

    def test_exec_command_call_with_unparseable_arguments_falls_back_to_generic_message(self):
        item = SimpleNamespace(raw_item=SimpleNamespace(name="exec_command", arguments="not json"))
        assert _describe_stream_event(_fake_event("tool_called", item)) == "Running a shell command..."

    def test_apply_patch_call_lists_touched_files_without_diff_content(self):
        patch_text = (
            "*** Begin Patch\n"
            "*** Update File: navigation/policy.py\n"
            "@@\n-old\n+new\n"
            "*** Add File: engine/npc.py\n"
            "+class NPC: ...\n"
            "*** End Patch"
        )
        item = SimpleNamespace(raw_item=SimpleNamespace(name="apply_patch", input=patch_text))
        msg = _describe_stream_event(_fake_event("tool_called", item))
        assert msg == "Editing: edit navigation/policy.py, add engine/npc.py"
        # never surface the actual hunk/diff content
        assert "-old" not in msg
        assert "+new" not in msg
        assert "class NPC" not in msg

    def test_unknown_tool_call_gets_a_generic_message(self):
        item = SimpleNamespace(raw_item=SimpleNamespace(name="view_image"))
        assert _describe_stream_event(_fake_event("tool_called", item)) == "Viewing an image it produced..."

    def test_failed_shell_command_output_is_surfaced(self):
        output = (
            "Chunk ID: abc123\nWall time: 0.5000 seconds\n"
            "Process exited with code 1\nOutput:\nTraceback (most recent call last):\nValueError: boom"
        )
        item = SimpleNamespace(output=output)
        msg = _describe_stream_event(_fake_event("tool_output", item))
        assert msg is not None
        assert "exit 1" in msg
        assert "Traceback" in msg

    def test_failed_output_shows_last_line_not_just_a_leading_warning(self):
        # Regression test for a real, live-observed problem: `perl`'s locale warning is benign
        # (perl still exits 0 on its own) and always prints first, so showing only the first
        # output line hid the actual failure reason -- observed live sending an agent into a
        # long, unproductive trial-and-error loop chasing the wrong cause. The real error is
        # usually the last line for shell tools/tracebacks, so it must be included too.
        output = (
            "Chunk ID: abc123\nWall time: 0.1000 seconds\n"
            "Process exited with code 1\nOutput:\n"
            "perl: warning: Setting locale failed.\n"
            "syntax error at -e line 1, near \"s/foo\"\n"
        )
        item = SimpleNamespace(output=output)
        msg = _describe_stream_event(_fake_event("tool_output", item))
        assert msg is not None
        assert "exit 1" in msg
        assert "locale" in msg  # still shows the first line...
        assert "syntax error" in msg  # ...but not at the expense of the real error

    def test_failed_output_with_a_single_line_is_shown_as_is(self):
        output = "Chunk ID: abc\nWall time: 0.1 seconds\nProcess exited with code 2\nOutput:\ncommand not found\n"
        item = SimpleNamespace(output=output)
        msg = _describe_stream_event(_fake_event("tool_output", item))
        assert msg == "  command failed (exit 2): command not found"

    def test_successful_shell_command_output_stays_silent(self):
        output = "Chunk ID: abc123\nWall time: 0.5000 seconds\nProcess exited with code 0\nOutput:\nok"
        item = SimpleNamespace(output=output)
        assert _describe_stream_event(_fake_event("tool_output", item)) is None

    def test_apply_patch_output_stays_silent_to_avoid_duplicating_the_call_announcement(self):
        item = SimpleNamespace(output="Updated navigation/policy.py")
        assert _describe_stream_event(_fake_event("tool_output", item)) is None

    def test_reasoning_summary_is_surfaced(self):
        item = SimpleNamespace(raw_item=SimpleNamespace(summary=[SimpleNamespace(text="I'll add a chase NPC using pymunk.")]))
        msg = _describe_stream_event(_fake_event("reasoning_item_created", item))
        assert msg == "Thinking: I'll add a chase NPC using pymunk."

    def test_empty_reasoning_summary_stays_silent(self):
        item = SimpleNamespace(raw_item=SimpleNamespace(summary=[]))
        assert _describe_stream_event(_fake_event("reasoning_item_created", item)) is None

    def test_message_output_is_surfaced(self):
        item = SimpleNamespace(raw_item=SimpleNamespace(content=[SimpleNamespace(text="Implemented the chase mechanic.")]))
        msg = _describe_stream_event(_fake_event("message_output_created", item))
        assert msg == "Agent: Implemented the chase mechanic."

    def test_non_run_item_stream_events_are_ignored(self):
        event = SimpleNamespace(type="agent_updated_stream_event", new_agent=SimpleNamespace(name="X"))
        assert _describe_stream_event(event) is None

    def test_malformed_item_does_not_raise(self):
        # a future SDK shape change shouldn't crash the run -- narration is best-effort.
        assert _describe_stream_event(_fake_event("tool_called", object())) is None


def test_sandbox_run_streams_agent_narration_through_on_stage(tmp_path, patched_sdk):
    Runner = patched_sdk
    stages: list[str] = []

    async def fake_run(agent, message, *, run_config, max_turns):
        session = run_config.sandbox.session
        _write_good_attempt(session.files)
        return SimpleNamespace(final_output="summary")

    events = [
        _fake_event(
            "tool_called",
            SimpleNamespace(raw_item=SimpleNamespace(name="exec_command", arguments=json.dumps({"cmd": "ls"}))),
        ),
        _fake_event(
            "tool_called",
            SimpleNamespace(
                raw_item=SimpleNamespace(
                    name="apply_patch",
                    input="*** Begin Patch\n*** Update File: navigation/policy.py\n@@\n-a\n+b\n*** End Patch",
                )
            ),
        ),
        _fake_event(
            "reasoning_item_created",
            SimpleNamespace(raw_item=SimpleNamespace(summary=[SimpleNamespace(text="Adding a chase NPC.")])),
        ),
    ]

    with patch.object(Runner, "run_streamed", _streamed(fake_run, events=events)):
        run_sandbox_generation(
            "make a game", 1, str(tmp_path / "run"), max_repair_attempts=0, on_stage=stages.append
        )

    assert "$ ls" in stages
    assert "Editing: edit navigation/policy.py" in stages
    assert "Thinking: Adding a chase NPC." in stages
    # never a diff line
    assert not any("-a" in s or "+b" in s for s in stages)
