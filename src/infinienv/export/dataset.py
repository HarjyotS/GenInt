"""Exports a directory of executed InfiniEnv runs into a JSONL dataset (PATHWAY.md section 13).

Each row's `programmatic_reward` comes from the real per-goal `goal_results` recorded by
`navigation.policy.solve_scene` (via replay.json), not just a copy of overall `success` --
so a scene with 4 delivery goals where 3 succeeded shows that, not a flat 0/1.
"""

from __future__ import annotations

import json
import os


def _read_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _programmatic_reward(replay: dict | None, metrics: dict) -> dict:
    if replay and replay.get("goal_results"):
        reward = {g["id"]: int(bool(g["success"])) for g in replay["goal_results"]}
        reward["total"] = sum(reward.values())
        return reward
    # No replay.json (e.g. an older or hand-authored run dir): fall back to a single
    # overall-success signal rather than guessing at per-goal detail we don't have.
    ok = int(bool(metrics.get("success")))
    return {"overall": ok, "total": ok}


def iter_run_dirs(root: str):
    for name in sorted(os.listdir(root)):
        run_dir = os.path.join(root, name)
        if not os.path.isdir(run_dir):
            continue
        if os.path.exists(os.path.join(run_dir, "scene.json")) and os.path.exists(os.path.join(run_dir, "metrics.json")):
            yield name, run_dir


def _rel_if_exists(run_dir: str, root: str, filename: str) -> str | None:
    path = os.path.join(run_dir, filename)
    return os.path.relpath(path, root) if os.path.exists(path) else None


def build_dataset_rows(root: str) -> list[dict]:
    rows: list[dict] = []
    for name, run_dir in iter_run_dirs(root):
        scene = _read_json(os.path.join(run_dir, "scene.json"))
        metrics = _read_json(os.path.join(run_dir, "metrics.json"))
        replay_json_path = os.path.join(run_dir, "replay.json")
        replay = _read_json(replay_json_path) if os.path.exists(replay_json_path) else None

        scene_name = scene.get("metadata", {}).get("name", "")
        # `name` (the run directory, e.g. "level_01") is always unique within one export;
        # the scene's own metadata.name often is not (templates/mutations reuse it), so
        # combine both instead of risking duplicate dataset ids.
        row_id = f"{name}__{scene_name}" if scene_name else name

        rows.append(
            {
                "id": row_id,
                "prompt": scene.get("metadata", {}).get("prompt", ""),
                "scene_path": _rel_if_exists(run_dir, root, "scene.json"),
                "asset_manifest_path": _rel_if_exists(run_dir, root, "asset_manifest.json"),
                "replay_path": _rel_if_exists(run_dir, root, "replay.json"),
                "gif_path": _rel_if_exists(run_dir, root, "replay.gif"),
                "success": bool(metrics.get("success")),
                "path_length": metrics.get("path_length"),
                "goal": [{"id": g.get("id"), "type": g.get("type")} for g in scene.get("goals", [])],
                "programmatic_reward": _programmatic_reward(replay, metrics),
            }
        )
    return rows


def export_dataset(root: str, out_path: str) -> int:
    rows = build_dataset_rows(root)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return len(rows)
