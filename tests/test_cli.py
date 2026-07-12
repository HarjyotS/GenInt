import json
import os

from infinienv.cli import _load_dotenv, main


def test_load_dotenv_does_not_promote_cl_key_to_anthropic_api_key(monkeypatch):
    # Deliberately NOT mapped: setting ANTHROPIC_API_KEY would hijack the `claude` CLI's auth away
    # from the user's claude.ai login (see cli._load_dotenv). CL_KEY stays under its own name;
    # ANTHROPIC_API_KEY must remain unset by _load_dotenv.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CL_KEY", "sk-ant-sentinel")
    _load_dotenv()
    assert os.environ.get("ANTHROPIC_API_KEY") is None


_EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "kitchen_can.json")
_PUSH_EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "push_slide_demo.json")


def test_generate_command_rejects_an_out_dir_outside_runs(tmp_path, monkeypatch, capsys):
    # `generate` is sandbox-only now; the out_dir check runs before the optional openai-agents SDK
    # is imported inside sandbox/runner.py, so this is testable without that extra installed.
    monkeypatch.chdir(tmp_path)
    rc = main(["generate", "--prompt", "make a game", "--seed", "1", "--out", "pathy"])
    assert rc == 1
    assert not os.path.exists("pathy")
    assert "runs/" in capsys.readouterr().out


def test_validate_command(tmp_path):
    # Uses a committed example scene rather than generating one (generate is sandbox-only / needs a key).
    rc = main(["validate", _EXAMPLE])
    assert rc == 0


def test_solve_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["solve", _EXAMPLE, "--out", "runs/solved"])
    assert rc == 0


def test_solve_command_on_a_push_physics_scene(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open(_PUSH_EXAMPLE) as f:
        scene = json.load(f)
    assert any(g["type"] == "push" for g in scene["goals"])
    assert any(o.get("pushable") for o in scene["objects"])
    rc = main(["solve", _PUSH_EXAMPLE, "--out", "runs/push"])
    assert rc == 0  # the deterministic solver still handles push/slide physics on a scene


def test_generate_is_sandbox_only(tmp_path, monkeypatch):
    # Sandbox is the ONE generate mode: a plain `generate` routes to the sandbox runner.
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_sandbox(prompt, seed, out_dir, **kwargs):
        captured["called"] = True
        return {
            "success": True, "agent_summary": None, "run_error": None, "repair_attempts": 0,
            "artifact_paths": {}, "workspace_dir": out_dir, "metrics": {"source": "sandbox", "success": True},
        }

    # cmd_generate imports run_sandbox_generation lazily from sandbox.runner inside the function.
    import infinienv.sandbox.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_sandbox_generation", fake_sandbox)

    rc = main(["generate", "--prompt", "make a game", "--seed", "1", "--out", "runs/def"])
    assert captured.get("called") is True and rc == 0
    # the legacy --sandbox flag is still accepted (no-op) for backward compat
    captured.clear()
    rc = main(["generate", "--sandbox", "--prompt", "make a game", "--seed", "1", "--out", "runs/def2"])
    assert captured.get("called") is True and rc == 0
