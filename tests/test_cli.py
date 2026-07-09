import json
import os

from infinienv.cli import main


def test_generate_command_writes_expected_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_dir = "runs/demo"
    rc = main(
        [
            "generate",
            "--provider",
            "mock",
            "--prompt",
            "Create a kitchen delivery task",
            "--seed",
            "42",
            "--out",
            out_dir,
        ]
    )
    assert rc == 0
    for name in ("scene.json", "validation.json", "metrics.json", "render.png", "replay.gif", "report.md"):
        assert os.path.exists(os.path.join(out_dir, name)), name

    with open(os.path.join(out_dir, "metrics.json")) as f:
        metrics = json.load(f)
    assert metrics["success"] is True
    assert metrics["provider"] == "mock"


def test_generate_command_rejects_an_out_dir_outside_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(
        [
            "generate",
            "--provider",
            "mock",
            "--prompt",
            "Create a kitchen delivery task",
            "--seed",
            "42",
            "--out",
            "pathy",
        ]
    )
    assert rc == 1
    assert not os.path.exists("pathy")
    err = capsys.readouterr().out
    assert "runs/" in err


def test_generate_sandbox_command_rejects_an_out_dir_outside_runs(tmp_path, monkeypatch, capsys):
    # The out_dir check runs before the optional openai-agents SDK is ever imported inside
    # sandbox/runner.py, so this is testable regardless of whether that extra is installed.
    monkeypatch.chdir(tmp_path)
    rc = main(
        [
            "generate",
            "--sandbox",
            "--prompt",
            "make a game",
            "--seed",
            "1",
            "--out",
            "pathy",
        ]
    )
    assert rc == 1
    assert not os.path.exists("pathy")
    err = capsys.readouterr().out
    assert "runs/" in err


def test_validate_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["generate", "--provider", "mock", "--prompt", "kitchen task", "--seed", "1", "--out", "runs/demo"])
    rc = main(["validate", "runs/demo/scene.json"])
    assert rc == 0


def test_solve_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["generate", "--provider", "mock", "--prompt", "kitchen task", "--seed", "1", "--out", "runs/demo"])
    rc = main(["solve", "runs/demo/scene.json"])
    assert rc == 0


def test_generate_command_produces_a_physics_push_scene(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(
        [
            "generate",
            "--provider",
            "mock",
            "--prompt",
            "push the crate across the ice onto the target",
            "--seed",
            "3",
            "--out",
            "runs/physics",
        ]
    )
    assert rc == 0
    with open(os.path.join("runs/physics", "scene.json")) as f:
        scene = json.load(f)
    assert any(g["type"] == "push" for g in scene["goals"])
    assert any(o.get("pushable") for o in scene["objects"])
    with open(os.path.join("runs/physics", "metrics.json")) as f:
        assert json.load(f)["success"] is True
