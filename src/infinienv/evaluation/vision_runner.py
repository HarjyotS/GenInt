"""Runs a stand-in vision policy through `InfiniEnv` and writes the episode artifacts.

Parallel to `evaluation/runner.py` (the deterministic path). The headline result is the
demonstration this whole feature exists for: a **pixel-only** policy attempting a **code-defined**
objective. It writes:
- `episode.gif`  -- the exact frames the policy saw, in order.
- `episode.json` -- per step: the controller action chosen *from pixels*, the *code-computed*
  reward, and goal state.
- `metrics.json` -- `vision_success` (did the pixel policy complete the goal, judged by
  `is_goal_complete` -- never by pixels), steps, per-goal reward, and, as a deliberate contrast,
  a naive `vlm_judge_success` (a VLM's guess from the final frame) plus whether it agreed with
  the code truth. A disagreement is the concrete illustration of the brief's point that
  code-level objectives beat a VLM checking pixels.

Reward and success stay code-defined (CLAUDE.md section 2). Only the *player* is pixel-based.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from PIL import Image

from infinienv.artifacts.writer import resolve_out_dir, write_json
from infinienv.engine.env import CONTROLLER_ACTIONS, InfiniEnv, frame_to_png_bytes
from infinienv.navigation.vision_policy import PLAN_LEN, VisionPolicy, build_feedback
from infinienv.schema.scene_schema import SceneSpec

OnStage = Callable[[str], None]


def _goal_text(scene: SceneSpec) -> str:
    """A natural-language goal for the policy (the brief's 'specific goals'). Prefer the original
    task prompt; fall back to a summary built from the scene's structured goals."""
    prompt = (scene.metadata.prompt or "").strip()
    if prompt:
        return prompt
    return _summarize_goals(scene)


def _summarize_goals(scene: SceneSpec) -> str:
    parts: list[str] = []
    for g in scene.goals:
        t = g.type
        if t == "reach":
            parts.append(f"reach {g.target_id}")
        elif t == "pickup":
            parts.append(f"pick up {g.object_id}")
        elif t == "deliver":
            parts.append(f"deliver {g.object_id} to {g.target_id}")
        elif t == "unlock":
            parts.append(f"unlock {g.door_id}")
        elif t == "push":
            parts.append(f"push {g.object_id} onto {g.target_id}")
        elif t == "interact":
            parts.append(f"perform {g.interaction_id} on {g.target_id}")
        else:
            parts.append(t)
    return "; then ".join(parts) if parts else "complete the level"


def _deterministic_minimap(env, scene) -> str:
    """A text minimap + coordinates for the pixel policy to route on (so it actually reaches the goal
    instead of guessing from one frame). Built from the deterministic engine's own truth: `env.grid`
    (walls / solid objects), the live agent cell, and the current goal's target cell. Returns "" for
    an implausibly large grid (kept as a guard, though fixed-vocabulary scenes are small)."""
    grid = env.grid
    st = env.state
    w, h = grid.width, grid.height
    if w > 60 or h > 60 or w < 2 or h < 2:
        return ""
    agent = st.agent_pos()
    unlocked = getattr(st, "unlocked_doors", frozenset())
    # Head for the current SUB-target of the first not-yet-complete goal: for a deliver, that's the
    # object to fetch until it's held, then the drop target -- so the map points where to go NOW.
    goal_cell = None
    completion = env._goal_completion()  # noqa: SLF001 - harness-side; only words reach the policy
    for g in scene.goals:
        if completion.get(g.id):
            continue
        gtype = getattr(g, "type", None)
        oid = getattr(g, "object_id", None)
        obj = st.objects.get(oid) if oid else None
        if gtype == "deliver" and obj is not None and not obj.held:
            tid = oid  # go grab the object first
        else:
            tid = getattr(g, "target_id", None) or oid or getattr(g, "door_id", None)
        if not tid or tid == st_agent_id(st):
            continue
        c = st.object_pos(tid)
        if c is not None:
            goal_cell = c
            break
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            c = (x, y)
            if c == agent:
                row.append("A")
            elif goal_cell and c == goal_cell:
                row.append("P")
            elif grid.is_blocked(x, y, unlocked_doors=frozenset(unlocked)):
                row.append("#")
            else:
                row.append(".")
        rows.append("".join(row))
    header = f"You (A) are at {agent}."
    if goal_cell:
        header += f" The goal (P) is at {goal_cell}."
    return (header + "\nMap (#=wall, .=open floor, A=you, P=goal), row y=0 is the TOP:\n"
            + "\n".join(rows)
            + "\nPlan the shortest route of moves from A to P, stepping only on '.' cells. "
            "forward=up (y-1), back=down (y+1), left=x-1, right=x+1.")


def st_agent_id(state) -> str:
    return getattr(state, "agent_id", "agent")


def _save_gif(frames: list[Image.Image], path: str, *, duration_ms: int = 260) -> None:
    if not frames:
        return
    # Hold the final frame a beat longer so the outcome is readable.
    seq = frames + [frames[-1]] * 3
    seq[0].save(path, save_all=True, append_images=seq[1:], duration=duration_ms, loop=0)


def _resolve_asset_paths(scene: SceneSpec, assets_mode: str, cache_dir: str | None) -> dict[str, str]:
    if assets_mode == "none":
        return {}
    from infinienv.assets.resolver import resolve_assets

    cache = os.path.abspath(cache_dir or os.path.join(os.getcwd(), ".infinienv_asset_cache"))
    entries, _notes = resolve_assets(scene, assets_mode, cache)
    return {t: e.path for t, e in entries.items() if e.path}


def run_navigation(
    scene: SceneSpec,
    out_dir: str,
    *,
    policy: VisionPolicy | None = None,
    backend: str = "openai",
    model: str | None = None,
    max_steps: int | None = None,
    plan_len: int = PLAN_LEN,
    history: int = 2,
    assets_mode: str = "none",
    asset_cache_dir: str | None = None,
    judge: bool = True,
    on_stage: OnStage | None = None,
) -> dict:
    """Play `scene` with a vision policy; write episode.gif/episode.json/metrics.json; return metrics.

    The policy plays in short PLANS, not one action per look: each vision call returns up to
    `plan_len` actions, executed in order until the level ends or a move is blocked (then we
    re-observe). So the model is called ~`plan_len`x fewer times than one-keystroke-per-call for the
    same number of env steps."""
    stage = on_stage or (lambda _m: None)
    out = resolve_out_dir(out_dir)
    policy = policy or VisionPolicy(backend=backend, model=model)

    asset_paths = _resolve_asset_paths(scene, assets_mode, asset_cache_dir)
    goal = _goal_text(scene)
    stage(f"Goal (given to the pixel policy): {goal}")

    from collections import deque

    env = InfiniEnv(scene, max_steps=max_steps, asset_paths=asset_paths)
    obs, info = env.reset()

    steps: list[dict] = []
    total_reward = 0.0
    terminated = truncated = False
    decisions = 0  # number of vision (model) calls -- each plans a short sequence of actions
    blocked_steps = 0
    recent: deque = deque(maxlen=8)  # (action, moved) over the last several env actions
    visited: deque = deque(maxlen=12)  # recent agent cells, for loop detection
    frame_hist: deque = deque([obs], maxlen=history + 1)  # newest appended; sent newest-first
    last_outcomes: list = []
    started = time.time()

    while not (terminated or truncated):
        decisions += 1
        step_no = env._steps + 1  # noqa: SLF001 - the next step index, for the prompt
        moved_recent = sum(1 for _a, m in recent if m)
        # Oscillating (moving but revisiting a small cycle) counts as looping, not just no-movement.
        looping = bool(recent) and (
            moved_recent == 0 or (len(visited) >= 6 and 2 * len(set(visited)) <= len(visited))
        )
        feedback = build_feedback(recent, last_outcomes, looping, CONTROLLER_ACTIONS)
        minimap = _deterministic_minimap(env, scene)  # a route-able text map so it actually solves
        if minimap:
            feedback = minimap + ("\n\n" + feedback if feedback else "")
        obs_pngs = [frame_to_png_bytes(f) for f in list(frame_hist)[::-1]]  # current first, then history
        plan, raw = policy.act(obs_pngs, goal, step=step_no, feedback=feedback, max_actions=plan_len)
        stage(f"[look {decisions}] plan: {' '.join(plan)}" + (f"  ({feedback})" if feedback else ""))
        last_outcomes = []
        for action in plan:  # execute the planned moves in order, re-looking early if blocked
            before_pos = env.state.agent_pos()
            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            after_pos = env.state.agent_pos()
            legal = info["action_legal"]
            moved = legal and after_pos != before_pos  # a real position change (not wait/interact)
            recent.append((action, moved))
            last_outcomes.append((action, moved))
            visited.append(after_pos)
            frame_hist.append(obs)
            if not legal:
                blocked_steps += 1
            steps.append(
                {
                    "t": info["steps"],
                    "decision": decisions,
                    "action": action,
                    "resolved": info["resolved_action"],
                    "legal": legal,
                    "moved": moved,
                    "reward": reward,
                    "goals": info["goals"],
                    "model_reply": raw.strip()[:120] if raw else "",
                }
            )
            flag = "  +reward" if reward else ("  (blocked)" if not legal else "")
            stage(f"[{info['steps']}/{env.max_steps}] {action} -> {info['resolved_action']}{flag}")
            # Stop the plan and re-observe on level end or a blocked move (the frame the rest of the
            # plan assumed is now stale -- a fresh look with feedback beats bashing the wall).
            if terminated or truncated or not legal:
                break

    vision_success = bool(info["all_complete"])
    elapsed = round(time.time() - started, 2)
    stage(
        f"Result: {'SUCCESS' if vision_success else 'did not complete'} in {info['steps']} steps "
        f"across {decisions} vision calls (code-judged)."
    )

    gif_path = os.path.join(out, "episode.gif")
    _save_gif(env.frames, gif_path)

    # The deliberate contrast: a naive VLM verdict from the final frame alone.
    vlm_judge_success: bool | None = None
    judge_raw: str | None = None
    judge_agrees: bool | None = None
    if judge:
        try:
            vlm_judge_success, judge_raw = policy.judge_final_frame(frame_to_png_bytes(env.frames[-1]), goal)
            judge_agrees = vlm_judge_success == vision_success
            stage(
                f"Naive VLM-on-pixels verdict: {'goal done' if vlm_judge_success else 'goal not done'} "
                f"({'agrees with' if judge_agrees else 'DISAGREES with'} the code truth)."
            )
        except Exception as exc:  # noqa: BLE001 - the judge is best-effort commentary, never fatal
            judge_raw = f"judge skipped: {exc}"

    write_json(
        out,
        "episode.json",
        {"goal_text": goal, "success": vision_success, "total_reward": total_reward, "steps": steps},
    )
    metrics = {
        "source": "vision_navigation",
        "backend": policy.backend,
        "model": policy.model,
        "goal_text": goal,
        "steps": info["steps"],
        # Model calls: the policy planned a short sequence each look, so this is well below `steps`.
        "decisions": decisions,
        "plan_len": plan_len,
        "history": history,
        # Blocked (illegal) env actions -- should stay small now that blocked moves are fed back.
        "blocked_steps": blocked_steps,
        "max_steps": env.max_steps,
        "total_reward": total_reward,
        # Code-defined truth: did the pixel-only policy complete the objective? Judged by
        # is_goal_complete over GameState, NOT by looking at the rendered pixels.
        "vision_success": vision_success,
        "goal_results": info["goals"],
        "truncated": truncated,
        # The contrast (best-effort): a VLM's guess from the final frame, and whether it agreed.
        "vlm_judge_success": vlm_judge_success,
        "judge_agrees_with_code": judge_agrees,
        "judge_raw": judge_raw,
        "elapsed_seconds": elapsed,
    }
    write_json(out, "metrics.json", metrics)
    stage(f"Wrote {out}/episode.gif, episode.json, metrics.json")
    return metrics
