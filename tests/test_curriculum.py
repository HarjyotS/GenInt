import os

from infinienv.generation.curriculum import build_curriculum, run_curriculum, write_curriculum
from infinienv.llm.providers.mock import MockProvider


def test_build_curriculum_is_easy_to_hard_and_theme_substituted():
    prompts = build_curriculum("warehouse", levels=5)
    assert len(prompts) == 5
    assert all("warehouse" in p for p in prompts)
    assert "key" in prompts[3].lower()  # level 4: key-door dependency


def test_write_curriculum_writes_prompt_list(tmp_path):
    out = tmp_path / "curriculum.txt"
    path = write_curriculum("kitchen", str(out), levels=3)
    content = out.read_text()
    assert path == str(out)
    assert content.count("# level") == 3


def test_run_curriculum_executes_each_level(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = run_curriculum("kitchen", "runs/curriculum", levels=2, provider=MockProvider(), seed=1)
    assert len(results) == 2
    for i, r in enumerate(results, start=1):
        assert r["level"] == i
        assert r["success"] is True
        level_dir = f"runs/curriculum/level_{i:02d}"
        assert os.path.exists(os.path.join(level_dir, "scene.json"))
        assert os.path.exists(os.path.join(level_dir, "metrics.json"))
    assert os.path.exists("runs/curriculum/prompts.txt")
