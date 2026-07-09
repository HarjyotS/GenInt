"""Writes run artifacts to disk. All file writes are confined to the given output directory."""

from __future__ import annotations

import json
import os

from infinienv.schema.scene_schema import SceneSpec


def resolve_out_dir(out_dir: str, *, require_runs_dir: bool = False) -> str:
    """Resolve and create the output directory, rejecting path traversal outside the cwd tree.

    `require_runs_dir`, when set, additionally requires the resolved path to be `runs/` itself
    or a subdirectory of it -- the CLI's `generate` command enforces this (every generation
    belongs in the standard `runs/` tree, per CLAUDE.md's artifact layout), while the GUI
    deliberately does not, since a reviewer may legitimately want a run written somewhere else
    (see `gui/app.py`, which calls this with the default `False`).
    """
    abs_out = os.path.abspath(out_dir)
    abs_cwd = os.path.abspath(os.getcwd())
    if os.path.commonpath([abs_out, abs_cwd]) != abs_cwd:
        raise ValueError(f"refusing to write outside the working directory: {out_dir!r}")
    if require_runs_dir:
        abs_runs = os.path.join(abs_cwd, "runs")
        if os.path.commonpath([abs_out, abs_runs]) != abs_runs:
            raise ValueError(
                f"generations must write into the runs/ directory (got {out_dir!r}) -- "
                "pass e.g. --out runs/my_run"
            )
    os.makedirs(abs_out, exist_ok=True)
    return abs_out


def write_json(out_dir: str, filename: str, data: dict) -> str:
    path = os.path.join(out_dir, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return path


def write_scene(out_dir: str, scene: SceneSpec) -> str:
    return write_json(out_dir, "scene.json", scene.model_dump())


def write_validation(out_dir: str, validation_payload: dict) -> str:
    return write_json(out_dir, "validation.json", validation_payload)


def write_metrics(out_dir: str, metrics: dict) -> str:
    return write_json(out_dir, "metrics.json", metrics)


def write_report(out_dir: str, report_markdown: str) -> str:
    path = os.path.join(out_dir, "report.md")
    with open(path, "w") as f:
        f.write(report_markdown)
    return path
