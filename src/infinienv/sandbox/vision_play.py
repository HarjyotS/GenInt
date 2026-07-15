"""Faithful vision-play driver: a vision policy plays the REAL sandbox game.

This file is **copied verbatim into a sandbox workspace and run inside the sandbox** (see
`sandbox/vision_runner.py`). There it imports the agent-authored game's `make_env()` (its real
physics, rendering, and win condition) and drives it with a vision policy: each turn the policy
sees only the game's real rendered frame and picks one of the game's own declared actions, and the
game's own code decides reward/done/win. It writes `episode.gif` (the real frames the policy saw) +
`vision_metrics.json` (`vision_success` from the game's own `info["won"]` -- code-defined, never
from the pixels). The trusted process never imports the game code; it only reads those two files back.

Self-contained on purpose: it imports only stdlib + PIL + openai (all present in the sandbox's
interpreter), never this project's `llm/` stack, so it runs standalone in the workspace. The
`run_vision_episode` core takes the env + a controller callable as arguments, so it is unit-tested
in the trusted process with a fake env and a fake controller -- no game code, no network.
"""

from __future__ import annotations

import base64
import io
import json
import os

_PLAYER_SYSTEM = (
    "You are playing a 2D video game, controlling the character. You see the current rendered frame "
    "(and, when provided, the previous 1-2 frames so you can see how you just moved). Decide from the "
    "images alone. Find your character and the goal, and move to reduce the distance to the goal. "
    "This is often a MAZE: aisles/corridors between walls, shelves, or crates. Navigate it: "
    "(1) When a move is BLOCKED (a wall is that way), do NOT repeat it -- turn 90 degrees and go a "
    "different direction. You will be told which moves were just blocked; obey that. "
    "(2) Do not reverse back into the cell you just came from unless you have hit a dead end. "
    "(3) A reliable way out of a maze is to follow ONE wall consistently (keep it on the same side) "
    "until you reach the goal. "
    "(4) If you are told you are STUCK or going in circles, break the pattern: pick a direction you "
    "have NOT been trying. "
    "If this is a side-view platformer instead, be careful with jumps: only jump when there is a "
    "ledge or solid ground to LAND ON within reach, or to clear a gap or hazard directly in front of "
    "you -- never jump into an open gap or off a platform edge with nothing on the other side, which "
    "drops you into the void and loses the run. "
    "If the goal is to COLLECT / PICK UP / PLACE / DELIVER an item (not just reach a spot), moving is "
    "not enough: move next to or onto the item and use the INTERACT action (whichever of the allowed "
    "actions means use/pick up -- often 'e', 'space', 'interact', 'use', or 'pick') to grab it, carry "
    "it, then interact AGAIN at the destination to drop/place it. If you are told you are carrying "
    "nothing and just circling near the target, you probably still need to go pick the item up first. "
    "When a text MINIMAP and your coordinates are provided, USE THEM as the source of truth for"
    "navigation: it shows every wall (#), open floor (.), your position (A), and the goal (P). Work "
    "out the shortest path of cells from A to P that stays on '.' cells, and translate the first few "
    "steps of that path into moves. The frame is for seeing hazards/details; the minimap is for "
    "routing -- trust the minimap for where the walls are. "
    "You are NOT asked to react on every frame. Plan the next few moves toward the goal and reply "
    "with a SHORT ORDERED SEQUENCE of action words (in order, space-separated) -- executed one after "
    "another before you see a new frame. With a minimap you can commit to the whole next leg of the "
    "route; without one, plan fewer steps when uncertain. If a move is blocked, the rest of your "
    "sequence is dropped and you get a fresh frame with feedback, so committing is cheap. "
    "Reply with ONLY the action words (space-separated), no explanation, no punctuation, no numbering."
)
_JUDGE_SYSTEM = (
    "You judge whether a 2D game goal has been accomplished, looking ONLY at the final frame. "
    "Answer with a single word: YES or NO."
)


def _png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _idle_action(actions):
    """A safe do-nothing action from the game's own set (wait/noop/idle/stay), else the last one."""
    for idle in ("wait", "noop", "idle", "stay"):
        if idle in [str(a).lower() for a in actions]:
            return next(a for a in actions if str(a).lower() == idle)
    return actions[-1] if actions else "wait"


def _parse_actions(text, actions, limit=6):
    """Parse an ordered *plan* of the game's own actions from the model's reply (the moves it commits
    to before the next frame): whole-word matches in appearance order, capped at `limit`, immediate
    repeats kept. Falls back to `[idle]` so a garbled reply never stalls the loop."""
    import re

    low = (text or "").lower()
    # Longest action names first so a name that contains another (e.g. 'jumpright') isn't split.
    by_len = sorted({str(a) for a in actions}, key=len, reverse=True)
    if not by_len:
        return ["wait"]
    pattern = re.compile(r"\b(" + "|".join(re.escape(a.lower()) for a in by_len) + r")\b")
    canon = {str(a).lower(): a for a in actions}  # map back to the game's own casing
    plan = []
    for match in pattern.finditer(low):
        plan.append(canon[match.group(1)])
        if len(plan) >= max(1, int(limit)):
            break
    return plan or [_idle_action(actions)]


def _parse_action(text: str, actions) -> str:
    """The first action of the parsed plan (a single one) -- kept for the coercion/fallback path."""
    return _parse_actions(text, actions, limit=1)[0]


_POSITION_KEYS = ("position", "pos", "cell", "coord", "xy", "player", "location")


def _position(info) -> tuple | None:
    """Best-effort extract the player's position from the game's `info` dict, so we can tell when a
    move was blocked / when the player is looping. Looks for a common key holding a 2/3-number
    sequence; returns a hashable tuple, or None if the game doesn't expose one."""
    if not isinstance(info, dict):
        return None
    for key in _POSITION_KEYS:
        val = info.get(key)
        if isinstance(val, (list, tuple)) and 1 <= len(val) <= 3 and all(
            isinstance(n, (int, float)) for n in val
        ):
            return tuple(round(float(n), 3) for n in val)
    return None


_GOAL_ATTRS = ("goal", "package", "target", "exit", "objective", "destination", "goal_cell",
               "target_cell", "goal_pos")
_WALL_ATTRS = ("obstacle_cells", "walls", "blocked", "wall_cells", "solid_cells", "blocked_cells")
_WALK_ATTRS = ("walkable", "floor", "open_cells", "free_cells", "walkable_cells")
_POS_ATTRS = ("position", "pos", "player", "agent_pos", "player_cell", "player_pos")
_MINIMAP_MAX = 60  # if the grid is bigger than this per side it's probably pixel coords -> skip


def _cell(v):
    """Coerce a value (possibly a callable/property) to an integer (x, y) grid cell, else None."""
    if callable(v):
        try:
            v = v()
        except Exception:  # noqa: BLE001
            return None
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(n, (int, float)) for n in v):
        return (int(round(v[0])), int(round(v[1])))
    return None


def _cell_set(v):
    """Coerce an iterable of cells to a set of integer (x, y), else None."""
    try:
        out = {c for c in (_cell(x) for x in v) if c is not None}
    except TypeError:
        return None
    return out or None


def _first_attr(env, names, coerce):
    for n in names:
        if hasattr(env, n):
            got = coerce(getattr(env, n))
            if got:
                return got
    return None


def _minimap(env, info) -> str:
    """Best-effort ASCII minimap + coordinates for the vision policy to plan a route on -- the thing
    that lets it actually SOLVE a maze instead of guessing from one frame. Built from what a grid game
    exposes (position via `info`/`env`, a walls-or-walkable set, and a goal cell). Returns "" for a
    game that doesn't expose grid state (a continuous platformer, say) -- there the policy stays on
    pixels + feedback only. Deliberately conservative: only fires for a bounded integer grid."""
    pos = _cell(_position(info)) or _first_attr(env, _POS_ATTRS, _cell)
    if pos is None:
        return ""
    goal = _first_attr(env, _GOAL_ATTRS, _cell)
    walls = _first_attr(env, _WALL_ATTRS, _cell_set)
    walkable = _first_attr(env, _WALK_ATTRS, _cell_set)
    if walls is None and walkable is None:
        return ""  # no maze structure to draw -> pixels-only
    known = set()
    known |= walls or set()
    known |= walkable or set()
    known.add(pos)
    if goal:
        known.add(goal)
    w = _first_attr(env, ("width", "WIDTH", "cols"), lambda v: int(v) if isinstance(v, (int, float)) else None)
    h = _first_attr(env, ("height", "HEIGHT", "rows"), lambda v: int(v) if isinstance(v, (int, float)) else None)
    if not w:
        w = max(c[0] for c in known) + 1
    if not h:
        h = max(c[1] for c in known) + 1
    if w > _MINIMAP_MAX or h > _MINIMAP_MAX or w < 2 or h < 2:
        return ""  # too big / not a small grid -> almost certainly pixel coordinates
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            c = (x, y)
            if c == pos:
                row.append("A")
            elif goal and c == goal:
                row.append("P")
            elif walls is not None:
                row.append("#" if c in walls else ".")
            else:  # only walkable known: a cell is a wall if it's not walkable
                row.append("." if c in walkable else "#")
        rows.append("".join(row))
    header = f"You (A) are at {pos}."
    if goal:
        header += f" The goal (P) is at {goal}."
    return (header + "\nMap (#=wall, .=open floor, A=you, P=goal), row y=0 is the TOP:\n"
            + "\n".join(rows)
            + "\nPlan the shortest route of moves from A to P, stepping only on '.' cells. "
            "Directions: up/north/w decreases y; down/south/s increases y; left/a decreases x; "
            "right/d increases x -- use the matching action names for this game.")


def _still_frame(before, after) -> bool:
    """True if `after` is pixel-identical to `before` -- the weak last-resort 'did not move' signal
    for a game that exposes neither a moved/blocked flag nor a position (and has no live HUD).
    Defeated by any per-frame HUD, which is exactly why `_moved` prefers `info`. Best-effort."""
    try:
        from PIL import ImageChops

        a, b = before.convert("RGB"), after.convert("RGB")
        if a.size != b.size:
            return False
        return ImageChops.difference(a, b).getbbox() is None
    except Exception:  # noqa: BLE001 - best-effort, never fatal
        return False


def _moved(info, before_frame, after_frame, before_pos, after_pos) -> bool:
    """Did the last action actually change the game state? Prefer the game's own signals (robust to
    a live HUD, which a frame diff is not): an explicit `info['moved']`, else `not info['blocked']`,
    else a change in the extracted position; only if the game exposes none of those fall back to a
    whole-frame-identical check. cartel exposes moved+blocked, so this is reliable there."""
    if isinstance(info, dict):
        if "moved" in info:
            return bool(info["moved"])
        if "blocked" in info:
            return not bool(info["blocked"])
    if before_pos is not None or after_pos is not None:
        return before_pos != after_pos
    return not _still_frame(before_frame, after_frame)


_CARRY_KEYS = ("carried", "carrying", "holding", "held", "inventory", "hand", "hands")


def _carry_note(info) -> str:
    """A one-line 'you are carrying X / your hands are empty' note when the game exposes it -- the
    signal a deliver/pickup task needs so the policy knows whether it still has to go pick the item
    up. Best-effort over common info keys; "" when the game exposes nothing."""
    if not isinstance(info, dict):
        return ""
    for k in _CARRY_KEYS:
        if k in info:
            v = info[k]
            if not v:
                return "Your hands are EMPTY -- if the goal needs an item placed, go pick it up first (interact on it)."
            if isinstance(v, (list, tuple, set, dict)):
                v = ", ".join(str(x) for x in (v.keys() if isinstance(v, dict) else v)) or "something"
            return f"You are carrying: {v}. Take it to the target and interact to place it."
    return ""


def _feedback_text(recent, last_outcomes, looping: bool, actions, carry: str = "") -> str:
    """Build the short word-feedback handed to the pixel policy each look (it still decides from the
    frames -- this is the proprioceptive 'did I move / am I stuck / am I carrying' signal a real
    embodied policy gets). `recent` is a deque of (action, moved) over the last several env actions;
    `last_outcomes` is the just-executed plan's [(action, moved), ...]; `looping` flags
    no-net-progress / revisits; `carry` is the optional carry-state note."""
    lines: list[str] = []
    if last_outcomes:
        parts = [f"{a}({'moved' if m else 'BLOCKED'})" for a, m in last_outcomes]
        lines.append("Last plan result: " + " ".join(parts) + ".")
    if carry:
        lines.append(carry)
    # Actions blocked two+ times in the recent window -- tell the policy to stop trying them.
    blocked_counts: dict = {}
    for a, m in recent:
        if not m:
            blocked_counts[a] = blocked_counts.get(a, 0) + 1
    repeated = [a for a, c in blocked_counts.items() if c >= 2]
    if repeated:
        lines.append("These moves keep hitting a wall -- do NOT repeat them: " + ", ".join(repeated) + ".")
    if looping:
        lines.append("You are STUCK / oscillating over the same few cells and not winning. If moving "
                     "isn't working, you probably need to INTERACT (pick up / use / place an item), "
                     "not just move -- or head somewhere you have not been.")
    # Suggest directions not recently blocked (best-effort; the policy still chooses from the frame).
    untried = [a for a in actions if a not in blocked_counts]
    if (repeated or looping) and untried:
        lines.append("Options include: " + ", ".join(untried[:6]) + ".")
    return " ".join(lines)


def run_vision_episode(env, act, *, max_steps: int, hold: int = 1, plan_len: int = 1,
                       history: int = 0, on_step=None):
    """Drive `env` (a game env: `.actions`, `.reset()->frame`, `.step(a)->(frame,reward,done,info)`)
    with a controller `act(frames_png, decision, actions, feedback) -> plan`, where `frames_png` is
    `[current, prev, ...]` (the current frame plus up to `history` recent frames, so a pixel policy
    can see its own motion), `feedback` is a short word summary of what happened / whether it's stuck
    (built from the game's own `info`), and `plan` is an ordered list of up to `plan_len` actions (a
    bare string is coerced to a one-action plan).

    Each *decision* re-observes and gets a PLAN, then the driver executes it action-by-action (each
    HELD for `hold` simulation frames -- frame-skip / action-repeat, collecting every frame for a
    smooth gif). A plan is cut short (re-observe next decision) as soon as the game ends OR a move is
    BLOCKED -- detected from the game's own `info['moved']`/`info['blocked']`/position (robust to a
    live HUD, unlike a frame diff), so a stale plan never bashes a wall for the rest of its length,
    and the next look is told which move was blocked. `max_steps` bounds the TOTAL env actions, so
    vision calls are ~`max_steps / plan_len`.

    Pure loop (no network, no game import) so it's testable with a fake env + fake `act`.
    `vision_success` comes from the game's own `info['won']` (its code-defined win), not the pixels.

    `plan_len=1, history=0` reproduces the old one-action-per-decision behavior (plus feedback)."""
    from collections import deque

    actions = tuple(env.actions)
    hold = max(1, int(hold))
    plan_len = max(1, int(plan_len))
    history = max(0, int(history))
    frame = env.reset()
    frames = [frame]
    frame_hist: deque = deque([frame], maxlen=history + 1)  # newest first assembled below
    decisions: list[dict] = []
    total_reward = 0.0
    won = False
    done = False
    error: str | None = None
    sim_frames = 0
    steps_taken = 0  # total env actions across all plans -- bounded by max_steps
    blocked_steps = 0
    stuck_looks = 0
    recent: deque = deque(maxlen=8)  # (action, moved) over the last several env actions
    visited: deque = deque(maxlen=12)  # recent positions (when the game exposes one)
    pos: tuple | None = None  # player position, discovered from info once the game exposes one
    last_outcomes: list = []
    last_info: dict = {}
    had_minimap = False
    d = 0

    while steps_taken < max_steps and not done:
        d += 1
        # Detect looping: barely moved, or MOVING but oscillating -- a majority of recent cells are
        # revisits (a small cycle like up/up/down/down), so it goes in circles even though every move
        # "succeeds" (the exact failure of the kitchen deliver run: bouncing near the sink, never
        # picking the can up).
        moved_recent = sum(1 for _a, m in recent if m)
        looping = bool(recent) and (
            moved_recent == 0 or (len(visited) >= 6 and 2 * len(set(visited)) <= len(visited))
        )
        if looping:
            stuck_looks += 1
        feedback = _feedback_text(recent, last_outcomes, looping, actions, carry=_carry_note(last_info))
        # A text minimap + coordinates (when this game exposes grid state), so the policy can plan a
        # real route instead of guessing from one frame -- prepended to the feedback text.
        minimap = _minimap(env, last_info)
        if minimap:
            had_minimap = True
            feedback = minimap + ("\n\n" + feedback if feedback else "")
        # Current frame first, then recent history (so frames_png[0] is "now").
        obs = [_png_bytes(f) for f in list(frame_hist)[::-1]]
        try:
            plan = act(obs, d, actions, feedback)  # one occasional look (current + history frames)
        except Exception as exc:  # a policy/network hiccup shouldn't lose the whole episode
            error = f"policy error at decision {d}: {exc}"
            break
        if isinstance(plan, str):  # back-compat: a controller that returns a single action
            plan = [plan]
        plan = [a if a in actions else _parse_action(str(a), actions) for a in plan][:plan_len]
        if not plan:
            plan = [_idle_action(actions)]

        executed: list = []
        last_outcomes = []
        plan_reward = 0.0
        try:
            for action in plan:
                if steps_taken >= max_steps:
                    break
                before, before_pos = frame, pos
                # Two distinct signals over the hold-block (holding a direction can advance several
                # cells and only hit a wall on the LAST held frame):
                #   block_moved  = did the action move AT ALL during the hold -> the truthful "moved"
                #                  signal for the counter/feedback (reading only the last frame
                #                  over-counts blocks -- the bug the live cartel run exposed).
                #   step_moved   = did the FINAL held step move -> if not, this direction is exhausted
                #                  (ran into a wall), so re-observe. Frequent re-looks at each wall
                #                  gave the weak policy the best live result, so keep that cadence.
                block_moved = False
                step_moved = True
                prev_f, prev_p = frame, pos
                for _ in range(hold):  # hold this action while the simulation advances
                    frame, reward, done, info = env.step(action)
                    frames.append(frame)
                    sim_frames += 1
                    r = float(reward or 0.0)
                    total_reward += r
                    plan_reward += r
                    won = bool((info or {}).get("won", won))
                    cur_p = _position(info)
                    step_moved = r > 0 or _moved(info, prev_f, frame, prev_p, cur_p)
                    block_moved = block_moved or step_moved
                    prev_f, prev_p = frame, cur_p
                    if done:
                        break
                pos = _position(info)
                last_info = info or {}
                moved = block_moved
                executed.append(action)
                recent.append((action, moved))
                last_outcomes.append((action, moved))
                if pos is not None:
                    visited.append(pos)
                if not moved:
                    blocked_steps += 1
                steps_taken += 1
                frame_hist.append(frame)
                if done:
                    break
                if not step_moved:  # ran into a wall (this direction is done) -- re-observe with feedback
                    break
        except Exception as exc:  # a game-code error ends the episode honestly, not a crash
            error = f"game step error at decision {d}: {exc}"
            break

        decisions.append({"d": d, "plan": list(plan), "executed": executed,
                          "outcomes": [{"action": a, "moved": m} for a, m in last_outcomes],
                          "reward": plan_reward, "done": bool(done)})
        if on_step is not None:
            on_step(d, executed, plan_reward, bool(done), won)
        if done:
            break

    record = {
        "actions": list(actions),
        "decisions": decisions,
        "num_decisions": len(decisions),
        "sim_frames": sim_frames,
        "hold": hold,
        "plan_len": plan_len,
        "history": history,
        "env_steps": steps_taken,
        "had_minimap": had_minimap,  # was a text minimap available to the policy (a grid game)?
        "blocked_steps": blocked_steps,  # env actions that did NOT move -- should stay small now
        "stuck_looks": stuck_looks,
        # "steps"/"num_steps" kept as aliases (a decision == a policy step / vision call) for metrics.
        "steps": decisions,
        "num_steps": len(decisions),
        "total_reward": total_reward,
        "won": won,
        "done": done,
        "error": error,
    }
    return frames, record


def _save_episode_gif(frames, path: str, *, dt: float = 0.05, max_frames: int = 300,
                      min_frame_ms: float = 40.0) -> None:
    """Save the episode as a **real-time** gif that **ends when the game ended** (no padded hold).

    Each simulation frame represents `dt` seconds of game time, so the replay's total playback equals
    the real game duration (`len(frames) * dt`). If the sim produced many frames, subsample by a
    stride and stretch each shown frame to `stride*dt` -- the total stays real time while the frame
    count / per-frame duration stay sane (GIF renderers clamp very short durations)."""
    import math

    if not frames:
        return
    rgb = [f.convert("RGB") for f in frames]
    dt_ms = max(1.0, dt * 1000.0)
    # keep <= max_frames AND each displayed frame >= min_frame_ms, so the visible playback is
    # kept * (stride*dt) == len * dt == the real game duration.
    stride = max(1, math.ceil(len(rgb) / max_frames), math.ceil(min_frame_ms / dt_ms))
    kept = rgb[::stride]
    per_frame_ms = int(round(stride * dt_ms))
    # No final-frame padding: the replay finishes exactly when the game did.
    kept[0].save(path, save_all=True, append_images=kept[1:], duration=per_frame_ms, loop=0)


# ---- OpenAI vision calls (only reached when the driver runs for real, inside the sandbox) ----


def _vision_act(client, model, frames, goal, actions, step, feedback, plan_len=6):
    # frames = [current, prev, ...]; send the current frame plus recent history so the policy can
    # see how it just moved. feedback = the driver's word summary (blocked moves / stuck / suggestions).
    if isinstance(frames, (bytes, bytearray)):  # tolerate a single-frame caller
        frames = [frames]
    note = (feedback + "\n") if feedback else ""
    hist_note = " (the first image is NOW; the rest are the previous frames)" if len(frames) > 1 else ""
    user = (f"Goal: {goal}\nAllowed actions: {', '.join(actions)}\nTurn: {step}\n{note}"
            f"You see {len(frames)} frame(s){hist_note}. "
            f"Plan your next 1 to {plan_len} moves as a space-separated sequence of action words.")
    content = [{"type": "input_text", "text": user}]
    for fp in frames:
        b64 = base64.b64encode(fp).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
    resp = client.responses.create(
        model=model,
        instructions=_PLAYER_SYSTEM,
        input=[{"role": "user", "content": content}],
    )
    return _parse_actions(resp.output_text, actions, limit=plan_len)


def _vision_judge(client, model, frame_png, goal) -> bool:
    b64 = base64.b64encode(frame_png).decode("ascii")
    user = f"Goal: {goal}\nLooking only at this final frame, has the goal been accomplished? Answer YES or NO."
    resp = client.responses.create(
        model=model,
        instructions=_JUDGE_SYSTEM,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": user},
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
        ]}],
    )
    return "yes" in (resp.output_text or "").strip().lower()[:5]


def _config() -> dict:
    """Read the trusted-process-supplied config (written into the workspace), env-var fallback."""
    cfg = {}
    if os.path.exists("vision_config.json"):
        try:
            with open("vision_config.json") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            cfg = {}
    cfg.setdefault("goal", os.environ.get("VISION_GOAL", "reach the objective"))
    cfg.setdefault("model", os.environ.get("INFINIENV_VISION_MODEL", "gpt-5.6-terra"))
    # Total ENV actions executed across the episode (gameplay length). Vision calls are ~this/plan_len.
    cfg.setdefault("max_steps", int(os.environ.get("VISION_MAX_STEPS", "60")))
    # Frames the chosen action is HELD for between observations (frame-skip / action-repeat) -- the
    # policy grabs a frame occasionally, not every simulation frame.
    cfg.setdefault("hold", int(os.environ.get("VISION_HOLD", "6")))
    # Actions the policy plans per look (one vision call returns a sequence of up to this many).
    cfg.setdefault("plan_len", int(os.environ.get("VISION_PLAN_LEN", "6")))
    # Recent frames shown alongside the current one (so the policy can see its own motion / not loop).
    cfg.setdefault("history", int(os.environ.get("VISION_HISTORY", "2")))
    cfg.setdefault("judge", os.environ.get("VISION_JUDGE", "1") != "0")
    return cfg


def _resolve_dt(env, module=None, default: float = 0.05) -> float:
    """Seconds of game time per step(), for a real-time replay. Preferred path is the env exposing
    `dt` (or `fps`); as a best-effort fallback for worlds built before that contract, read the game
    module's timestep constant (`DT`/`dt`, or `FPS`/`fps`). Any bad/nonpositive value -> `default`.
    `module` is the imported `run_scene`; passed in so this is unit-testable."""
    def _pos(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    dt = _pos(getattr(env, "dt", None))
    if dt is not None:
        return dt
    fps = _pos(getattr(env, "fps", None))
    if fps is not None:
        return 1.0 / fps
    if module is not None:
        for name in ("DT", "dt", "TIMESTEP"):
            dt = _pos(getattr(module, name, None))
            if dt is not None:
                return dt
        for name in ("FPS", "fps"):
            fps = _pos(getattr(module, name, None))
            if fps is not None:
                return 1.0 / fps
    return default


def main() -> int:
    cfg = _config()
    # Import the agent-authored game's playable interface. Absent => this world predates the
    # make_env contract; fail loudly so the orchestrator can tell the user to regenerate it.
    try:
        from run_scene import make_env  # type: ignore
    except Exception as exc:
        print(f"NO_MAKE_ENV: run_scene.make_env is missing or broken: {exc}")
        return 3

    import run_scene  # type: ignore  # already importable (make_env came from it) -- for the dt fallback

    from openai import OpenAI

    client = OpenAI()
    env = make_env()
    model, goal = cfg["model"], cfg["goal"]
    max_steps, hold, plan_len = int(cfg["max_steps"]), int(cfg["hold"]), int(cfg["plan_len"])
    history = int(cfg.get("history", 2))

    def act(frames, step, actions, feedback):
        return _vision_act(client, model, frames, goal, actions, step, feedback, plan_len=plan_len)

    def on_step(d, executed, reward, done, won):
        flag = "  +reward" if reward else ""
        plan = " ".join(str(a) for a in executed) or "(none)"
        print(f"[look {d}] plan: {plan}{flag}{'  DONE' if done else ''}", flush=True)

    # Seconds of game time per simulation step -- so the replay can play in REAL time. Prefer
    # env.dt/env.fps (the contract); fall back to the game module's DT/FPS constant for worlds built
    # before that contract; else ~20fps default.
    dt = _resolve_dt(env, run_scene)

    frames, record = run_vision_episode(
        env, act, max_steps=max_steps, hold=hold, plan_len=plan_len, history=history, on_step=on_step
    )
    _save_episode_gif(frames, "episode.gif", dt=dt)

    vision_success = bool(record["won"])
    metrics = {
        "source": "vision_navigation",
        "faithful": True,
        "backend": "openai",
        "model": model,
        "goal_text": goal,
        # "decisions"/"steps" = policy looks (vision calls); each look planned up to plan_len actions,
        # each held `hold` frames. The game advanced sim_frames total across env_steps env actions.
        "decisions": record["num_decisions"],
        "steps": record["num_decisions"],
        "env_steps": record["env_steps"],
        # Env actions that did NOT move the player (walls). With blocked-move feedback this should be
        # a small fraction of env_steps -- a high ratio means the policy is stuck bashing walls.
        "blocked_steps": record["blocked_steps"],
        "stuck_looks": record["stuck_looks"],
        # True if this game exposed grid state so the policy got a text minimap to route on.
        "had_minimap": record["had_minimap"],
        "plan_len": plan_len,
        "history": history,
        "max_steps": max_steps,
        "sim_frames": record["sim_frames"],
        "hold": hold,
        # Real-time replay: episode.gif plays at the game's own speed and ends when the game did.
        "game_dt": dt,
        "real_time_seconds": round(record["sim_frames"] * dt, 2),
        "vision_success": vision_success,
        "total_reward": record["total_reward"],
        "episode_error": record["error"],
        "actions_available": record["actions"],
    }
    if cfg["judge"] and frames:
        try:
            judged = _vision_judge(client, model, _png_bytes(frames[-1]), goal)
            metrics["vlm_judge_success"] = judged
            metrics["judge_agrees_with_code"] = judged == vision_success
        except Exception as exc:
            metrics["vlm_judge_success"] = None
            metrics["judge_agrees_with_code"] = None
            metrics["judge_error"] = str(exc)

    with open("vision_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"wrote episode.gif, vision_metrics.json; vision_success={vision_success}", flush=True)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
