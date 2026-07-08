import json

from infinienv.evaluation.runner import run_generation
from infinienv.export.dataset import build_dataset_rows, export_dataset
from infinienv.llm.providers.mock import MockProvider


def test_export_dataset_reads_per_goal_programmatic_reward(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = MockProvider()
    run_generation(provider, "Create a kitchen delivery task", 1, "runs/level_01")
    run_generation(provider, "Create a warehouse key task", 2, "runs/level_02")

    rows = build_dataset_rows("runs")
    assert len(rows) == 2
    ids = {r["id"] for r in rows}
    assert ids == {"level_01__kitchen_can_delivery", "level_02__warehouse_key_delivery"}

    for row in rows:
        assert row["success"] is True
        assert row["programmatic_reward"]["total"] >= 1
        # every top-level goal id should appear as a key with value 1 (all succeeded)
        for g in row["goal"]:
            assert row["programmatic_reward"][g["id"]] == 1

    count = export_dataset("runs", str(tmp_path / "dataset.jsonl"))
    assert count == 2
    lines = (tmp_path / "dataset.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # must be valid JSON per line
