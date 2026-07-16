"""Tests for the Claude Agent SDK sandbox backend (`sandbox/claude_runner.py`) and the
`INFINIENV_SANDBOX_BACKEND` dispatch in `sandbox/runner.py`. All hermetic -- no `claude` CLI,
no network, no API key. The SDK message/block shapes are duck-typed by the narration code, so
plain stand-in objects exercise it exactly as the real SDK types would.
"""

import os
from types import SimpleNamespace

import pytest

from infinienv.llm.base import ProviderError
from infinienv.sandbox import claude_runner
from infinienv.sandbox.runner import run_sandbox_generation


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    # No real keys leak into these tests: the Claude backend needs ANTHROPIC_API_KEY/CL_KEY, and
    # the shared prompt refiner needs OPENAI_API_KEY -- delete all so nothing makes a live call.
    for var in ("ANTHROPIC_API_KEY", "CL_KEY", "OPENAI_API_KEY", "OP_KEY"):
        monkeypatch.delenv(var, raising=False)


class _Block(SimpleNamespace):
    """A duck-typed stand-in for a Claude Agent SDK content block."""


def test_describe_block_bash_is_a_shell_line():
    assert (
        claude_runner._describe_block(_Block(name="Bash", input={"command": "python run_scene.py"}))
        == "$ python run_scene.py"
    )


def test_describe_block_edit_tools_show_only_the_path():
    assert (
        claude_runner._describe_block(_Block(name="Write", input={"file_path": "run_scene.py"}))
        == "Editing: run_scene.py"
    )
    assert (
        claude_runner._describe_block(_Block(name="Edit", input={"file_path": "engine/x.py"}))
        == "Editing: engine/x.py"
    )


def test_describe_block_text_and_thinking():
    assert claude_runner._describe_block(_Block(text="building the scene")) == "Agent: building the scene"
    assert (
        claude_runner._describe_block(_Block(thinking="climb only on the ladder"))
        == "Thinking: climb only on the ladder"
    )


def test_describe_block_read_and_grep_stay_silent():
    assert claude_runner._describe_block(_Block(name="Read", input={"file_path": "x"})) is None
    assert claude_runner._describe_block(_Block(name="Grep", input={"pattern": "x"})) is None


def test_describe_block_surfaces_only_failed_tool_results():
    assert claude_runner._describe_block(_Block(is_error=False, content="ok")) is None
    line = claude_runner._describe_block(_Block(is_error=True, content="Traceback: boom"))
    assert line is not None and "tool failed" in line and "boom" in line


def test_describe_message_emits_a_line_per_block_and_never_raises():
    seen: list[str] = []
    msg = SimpleNamespace(content=[_Block(text="hi"), _Block(name="Bash", input={"command": "ls"})])
    claude_runner._describe_claude_message(msg, stage=seen.append)
    assert seen == ["Agent: hi", "$ ls"]

    # A message with no `content` list, or a malformed block, degrades to silence, not a crash.
    claude_runner._describe_claude_message(SimpleNamespace(), stage=seen.append)
    claude_runner._describe_claude_message(SimpleNamespace(content="not-a-list"), stage=seen.append)
    assert seen == ["Agent: hi", "$ ls"]


def test_copy_artifacts_from_dir_copies_present_and_skips_missing(tmp_path):
    ws = tmp_path / "sandbox_workspace"
    out = tmp_path / "out"
    ws.mkdir()
    out.mkdir()
    (ws / "scene.json").write_text("{}")
    (ws / "render.png").write_bytes(b"\x89PNG")
    # metrics.json / replay.json / replay.gif deliberately absent

    paths = claude_runner._copy_artifacts_from_dir(str(ws), str(out))

    assert set(paths) == {"scene.json", "render.png"}
    assert (out / "scene.json").read_text() == "{}"
    assert (out / "render.png").read_bytes() == b"\x89PNG"
    assert not (out / "metrics.json").exists()


def test_backend_dispatch_routes_to_claude_with_a_claude_model(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(prompt, seed, out_dir, *, model, **kwargs):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["kwargs"] = kwargs
        return {"success": True, "routed": "claude"}

    monkeypatch.setenv("INFINIENV_SANDBOX_BACKEND", "claude")
    monkeypatch.setattr(claude_runner, "_run_async", fake_run)

    result = run_sandbox_generation("make a maze", 3, str(tmp_path / "run"), refine_prompt=False)

    assert result == {"success": True, "routed": "claude"}
    # No explicit model or INFINIENV_SANDBOX_MODEL -> the Claude default, not the OpenAI gpt default.
    assert captured["model"] == claude_runner.DEFAULT_SANDBOX_CLAUDE_MODEL
    assert captured["prompt"] == "make a maze"
    assert captured["kwargs"]["refine_prompt"] is False


def test_backend_dispatch_default_is_claude(monkeypatch, tmp_path):
    # With no INFINIENV_SANDBOX_BACKEND set, the default runtime is now the Claude Agent SDK.
    captured = {}

    async def fake_claude_run(prompt, seed, out_dir, *, model, **kwargs):
        captured["model"] = model
        return {"routed": "claude"}

    monkeypatch.delenv("INFINIENV_SANDBOX_BACKEND", raising=False)
    monkeypatch.delenv("INFINIENV_SANDBOX_MODEL", raising=False)
    monkeypatch.setattr(claude_runner, "_run_async", fake_claude_run)

    result = run_sandbox_generation("make a maze", 1, str(tmp_path / "run"), refine_prompt=False)

    assert result == {"routed": "claude"}
    assert captured["model"] == claude_runner.DEFAULT_SANDBOX_CLAUDE_MODEL  # the Claude default


def test_backend_dispatch_openai_is_still_reachable_explicitly(monkeypatch, tmp_path):
    from infinienv.sandbox import runner as runner_mod

    captured = {}

    async def fake_openai_run(prompt, seed, out_dir, *, model, **kwargs):
        captured["model"] = model
        return {"routed": "openai"}

    monkeypatch.setenv("INFINIENV_SANDBOX_BACKEND", "openai")
    monkeypatch.delenv("INFINIENV_SANDBOX_MODEL", raising=False)
    monkeypatch.setattr(runner_mod, "_run_async", fake_openai_run)

    result = run_sandbox_generation("make a maze", 1, str(tmp_path / "run"), refine_prompt=False)

    assert result == {"routed": "openai"}
    assert captured["model"] == runner_mod.DEFAULT_SANDBOX_MODEL  # the gpt default


def test_claude_backend_does_not_require_a_key_and_never_sets_anthropic_api_key(monkeypatch, tmp_path):
    # The backend must NOT hard-require ANTHROPIC_API_KEY/CL_KEY -- it relies on the `claude` CLI's
    # own auth (the user's claude.ai login). With no key set it should proceed to run (we stub the
    # SDK's query so no real CLI spawns), never raise a missing-key ProviderError, and never set
    # ANTHROPIC_API_KEY itself (which would hijack the CLI's login).
    pytest.importorskip("claude_agent_sdk")
    import asyncio

    import claude_agent_sdk

    async def fake_query(*, prompt, options, **_):
        # An async generator that yields nothing -> the agent produced no artifacts; the run ends
        # cleanly with success False, exercising the no-key path without a live call.
        return
        yield  # pragma: no cover -- makes this an async generator

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.chdir(tmp_path)  # resolve_out_dir requires the out_dir under cwd

    result = asyncio.run(
        claude_runner._run_async(
            "make a maze",
            1,
            "run",
            model="claude-sonnet-5",
            max_turns=1,
            max_repair_attempts=0,
            refine_prompt=False,
        )
    )

    assert result["success"] is False  # no artifacts, but no crash and no missing-key ProviderError
    assert "ANTHROPIC_API_KEY" not in os.environ  # the backend never sets it
