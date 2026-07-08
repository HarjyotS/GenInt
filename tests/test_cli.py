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
