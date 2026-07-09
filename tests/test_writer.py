import os

import pytest

from infinienv.artifacts.writer import resolve_out_dir


def test_resolve_out_dir_creates_and_returns_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = resolve_out_dir("runs/demo")
    assert resolved == str(tmp_path / "runs" / "demo")
    assert os.path.isdir(resolved)


def test_resolve_out_dir_rejects_path_traversal_outside_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="refusing to write outside"):
        resolve_out_dir("../escaped")


def test_resolve_out_dir_allows_non_runs_path_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = resolve_out_dir("elsewhere")
    assert resolved == str(tmp_path / "elsewhere")


def test_resolve_out_dir_require_runs_dir_accepts_runs_subdirectory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = resolve_out_dir("runs/my_run", require_runs_dir=True)
    assert resolved == str(tmp_path / "runs" / "my_run")


def test_resolve_out_dir_require_runs_dir_accepts_runs_itself(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = resolve_out_dir("runs", require_runs_dir=True)
    assert resolved == str(tmp_path / "runs")


def test_resolve_out_dir_require_runs_dir_rejects_sibling_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="must write into the runs/ directory"):
        resolve_out_dir("pathy", require_runs_dir=True)


def test_resolve_out_dir_require_runs_dir_rejects_cwd_itself(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="must write into the runs/ directory"):
        resolve_out_dir(".", require_runs_dir=True)


def test_resolve_out_dir_require_runs_dir_rejects_a_runs_prefixed_sibling(tmp_path, monkeypatch):
    # "runs-backup" starts with "runs" as a string but is NOT a subdirectory of runs/ --
    # commonpath-based comparison must not be fooled by a shared string prefix.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="must write into the runs/ directory"):
        resolve_out_dir("runs-backup", require_runs_dir=True)
