"""Local web GUI for InfiniEnv.

One page: type a prompt, toggle every `generate` setting, watch live stage
progress (SSE), see render.png/replay.gif inline. Runs the exact same
`evaluation.runner.run_generation` pipeline as the CLI -- this is a frontend
on top of it, not a second implementation.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid

from infinienv.llm import PROVIDER_NAMES, get_provider
from infinienv.llm.base import ProviderError

_jobs: dict[str, "Job"] = {}
_jobs_lock = threading.Lock()

# Live human-play sessions: a server-side InfiniEnv per session, driven by the browser's keypresses
# (the human is just another controller over the same deterministic engine `navigate` uses). Kept in
# memory (a local single-user GUI); capped + oldest-evicted so a long session never leaks envs.
_play_sessions: dict[str, dict] = {}
_play_lock = threading.Lock()
_PLAY_SESSION_CAP = 24
_PLAY_MAX_STEPS = 2000  # generous, so a human is never truncated mid-play

# Selectable sandbox-agent models per backend, first entry is the default. Kept in sync with the
# frontend picker in templates/index.html (SANDBOX_MODELS). Passing an unlisted string is rejected
# rather than forwarded to the API. The OpenAI variants (terra/sol/luna) and the Claude tiers are
# what's actually available on this account (see notes.md); a run may still override via the
# INFINIENV_SANDBOX_MODEL env var, which these override in turn when the frontend sends one.
SANDBOX_MODELS: dict[str, tuple[str, ...]] = {
    "openai": ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.5", "gpt-5.5-pro"),
    "claude": ("claude-sonnet-5", "claude-opus-4-8", "claude-fable-5"),
}


def _classify_stage(msg: str) -> str:
    """Tag a live narration line with a `kind` so the frontend can render it structured/pretty
    instead of as raw scrolling text. Keys off the stable narration prefixes emitted by
    sandbox/runner.py's `_describe_stream_event` and the repair-loop `stage()` calls (documented in
    CLAUDE.md section 11). An unrecognized line falls through to `status`, so this never breaks a run
    -- worst case a line is shown in the generic style."""
    s = (msg or "").strip()
    low = s.lower()
    # TODO harness: the seeded requirements, the agent's plan.py tool calls, memory notes.
    if s.startswith(("REQ_SEED", "PLAN_ADD", "PLAN_UPDATE", "PLAN_PROGRESS", "MEMORY_NOTE")) or (
        "plan.py" in low and (s.startswith("$ ") or s.startswith("Calling tool"))
    ):
        return "todo"
    if "requirements derived" in low or "deriving the requirements" in low or "plan a build" in low:
        return "todo"
    if s.startswith(("$ ", "Running a shell command", "Calling tool:")):
        return "command"
    if s.startswith("Editing"):
        return "edit"
    if s.startswith("Viewing an image"):
        return "image"
    if s.startswith("Thinking:"):
        return "decision"
    if s.startswith("Agent:"):
        return "agent"
    if s.startswith("Auditor:") or "auditing the run" in low:
        return "audit"
    if s.startswith(("Refining prompt", "Refined prompt")):
        return "refine"
    if "isolated sandbox workspace" in low:
        return "workspace"
    if s.startswith(("Running sandbox agent", "Running Claude sandbox agent", "Repairing", "Attempt ")):
        return "attempt"
    # navigate (vision-policy) narration -- run_navigation's + vision_runner's on_stage lines.
    if s.startswith("Goal (given"):  # "...to the pixel policy:" / "...to the vision policy:"
        return "goal"
    import re as _re

    if _re.match(r"^\[\d+/\d+\]", s):  # a per-step play line: "[3/30] right -> move_right" or "[3/30] jump"
        return "step"
    if s.startswith(("Naive VLM-on-pixels verdict:", "A vision policy is playing")):
        return "judge" if s.startswith("Naive") else "status"
    if "command failed" in low or "tool failed" in low or low.startswith("error"):
        return "error"
    return "status"


def _sandbox_assets_summary(out_dir: str) -> dict:
    """What assets the run actually resolved: each generated/resolved sprite in the sandbox's
    `asset_cache/` (served via the path-safe /artifact route) plus any `asset_notes` (e.g. a
    rate-limit fallback). Powers the frontend's assets panel."""
    items: list[dict] = []
    cache = os.path.join(out_dir, "sandbox_workspace", "asset_cache")
    if os.path.isdir(cache):
        for name in sorted(os.listdir(cache)):
            if name.lower().endswith(".png"):
                rel = os.path.relpath(os.path.join(cache, name), os.getcwd())
                items.append({"type": name[:-4], "url": "/artifact/" + rel})
    notes: list = []
    metrics_path = os.path.join(out_dir, "metrics.json")
    if os.path.isfile(metrics_path):
        try:
            with open(metrics_path) as f:
                notes = json.load(f).get("asset_notes") or []
        except (OSError, json.JSONDecodeError):
            pass
    return {"items": items, "notes": notes}


class Job:
    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.done = False


def _run_job(
    job: Job,
    *,
    provider_name: str,
    prompt: str,
    seed: int,
    out_dir: str,
    max_repair_attempts: int | None,
    allow_fallback: bool,
    assets_mode: str,
) -> None:
    from infinienv.evaluation.runner import run_generation

    def on_stage(msg: str) -> None:
        job.events.put({"type": "stage", "kind": _classify_stage(msg), "message": msg})

    try:
        provider = get_provider(provider_name)
        result = run_generation(
            provider,
            prompt,
            seed,
            out_dir,
            max_repair_attempts=max_repair_attempts,
            allow_fallback=allow_fallback,
            assets_mode=assets_mode,
            on_stage=on_stage,
        )
        job.events.put(
            {
                "type": "done",
                "success": result.metrics["success"],
                "metrics": result.metrics,
                "out_dir": os.path.relpath(result.out_dir, os.getcwd()),
                "scene": result.generation.scene.model_dump(),
                "validation_errors": [e.to_dict() for e in result.generation.validation.errors],
            }
        )
    except ProviderError as exc:
        job.events.put({"type": "error", "message": str(exc)})
    except Exception as exc:  # a GUI must never crash the server on a bad run; surface it instead
        job.events.put({"type": "error", "message": f"unexpected error: {exc}"})
    finally:
        job.done = True


def _run_sandbox_job(
    job: Job,
    *,
    prompt: str,
    seed: int,
    out_dir: str,
    max_repair_attempts: int | None,
    assets_mode: str,
    refine_prompt: bool,
    backend: str,
    model: str | None,
) -> None:
    from infinienv.sandbox.runner import run_sandbox_generation

    def on_stage(msg: str) -> None:
        job.events.put({"type": "stage", "kind": _classify_stage(msg), "message": msg})

    try:
        result = run_sandbox_generation(
            prompt,
            seed,
            out_dir,
            max_repair_attempts=max_repair_attempts,
            assets_mode=assets_mode,
            refine_prompt=refine_prompt,
            backend=backend,
            model=model,
            on_stage=on_stage,
        )
        # No SceneSpec/validation-errors payload here (unlike the non-sandbox path): a
        # sandbox scene.json is only checked for basic schema well-formedness, not
        # re-validated/re-solved by this process -- see outer_sanity_* in metrics below.
        job.events.put(
            {
                "type": "done",
                "success": result["success"],
                "metrics": result["metrics"],
                "out_dir": os.path.relpath(out_dir, os.getcwd()),
                "sandbox": True,
                "agent_summary": result["agent_summary"],
                "run_error": result["run_error"],
                "repair_attempts": result["repair_attempts"],
                "workspace_dir": os.path.relpath(result["workspace_dir"], os.getcwd()),
                "assets": _sandbox_assets_summary(out_dir),
            }
        )
    except ProviderError as exc:
        job.events.put({"type": "error", "message": str(exc)})
    except Exception as exc:  # a GUI must never crash the server on a bad run; surface it instead
        job.events.put({"type": "error", "message": f"unexpected error: {exc}"})
    finally:
        job.done = True


def _run_navigate_job(
    job: Job,
    *,
    scene_path: str,
    backend: str,
    model: str | None,
    max_steps: int | None,
    assets_mode: str,
    judge: bool,
    out_dir: str,
) -> None:
    """The vision-policy loop as a GUI job: a pixel-only policy plays a scene, scored by code.
    Mirrors _run_sandbox_job -- reuses evaluation.vision_runner.run_navigation and its on_stage."""
    from infinienv.evaluation.vision_runner import run_navigation
    from infinienv.schema.scene_schema import scene_spec_from_dict

    def on_stage(msg: str) -> None:
        job.events.put({"type": "stage", "kind": _classify_stage(msg), "message": msg})

    try:
        with open(scene_path) as f:
            scene = scene_spec_from_dict(json.load(f))
        metrics = run_navigation(
            scene,
            out_dir,
            backend=backend,
            model=model,
            max_steps=max_steps,
            assets_mode=assets_mode,
            judge=judge,
            on_stage=on_stage,
        )
        rel_out = os.path.relpath(out_dir, os.getcwd())
        job.events.put(
            {
                "type": "done",
                "mode": "navigate",
                # Success is the CODE-defined verdict (is_goal_complete), never the pixels.
                "success": bool(metrics.get("vision_success")),
                "metrics": metrics,
                "out_dir": rel_out,
                "episode_url": f"/artifact/{rel_out}/episode.gif",
            }
        )
    except ProviderError as exc:
        job.events.put({"type": "error", "message": str(exc)})
    except Exception as exc:  # a GUI must never crash the server on a bad run; surface it instead
        job.events.put({"type": "error", "message": f"unexpected error: {exc}"})
    finally:
        job.done = True


def _run_faithful_play_job(
    job: Job,
    *,
    run_dir: str,
    backend: str,
    model: str | None,
    max_steps: int | None,
    judge: bool,
    out_dir: str,
) -> None:
    """Faithful vision-play as a GUI job: a vision policy plays the REAL sandbox game (its own
    side-view frames + physics + win) inside the sandbox. Reuses sandbox.vision_runner."""
    from infinienv.sandbox.vision_runner import play_sandbox_world

    def on_stage(msg: str) -> None:
        job.events.put({"type": "stage", "kind": _classify_stage(msg), "message": msg})

    try:
        metrics = play_sandbox_world(
            run_dir,
            out_dir,
            backend=backend,
            model=model,
            max_steps=max_steps or 60,
            judge=judge,
            on_stage=on_stage,
        )
        rel_out = os.path.relpath(out_dir, os.getcwd())
        job.events.put(
            {
                "type": "done",
                "mode": "navigate",
                "faithful": True,  # the REAL game was played, not a top-down reinterpretation
                "success": bool(metrics.get("vision_success")),
                "metrics": metrics,
                "out_dir": rel_out,
                "episode_url": f"/artifact/{rel_out}/episode.gif",
            }
        )
    except ProviderError as exc:
        job.events.put({"type": "error", "message": str(exc)})
    except Exception as exc:
        job.events.put({"type": "error", "message": f"unexpected error: {exc}"})
    finally:
        job.done = True


def _faithful_run_dir(scene_path: str) -> str | None:
    """If `scene_path` belongs to a sandbox run (its dir has sandbox_workspace/), return the run
    dir so navigate can faithfully play the REAL game instead of a top-down reinterpretation."""
    if not scene_path.startswith("runs/"):
        return None
    run_dir = os.path.dirname(scene_path)  # runs/<name>/scene.json -> runs/<name>
    ws = os.path.join(os.getcwd(), run_dir, "sandbox_workspace")
    metrics_path = os.path.join(os.getcwd(), run_dir, "metrics.json")
    if not os.path.isdir(ws):
        return None
    try:
        with open(metrics_path) as f:
            if json.load(f).get("source") == "sandbox":
                return run_dir
    except (OSError, json.JSONDecodeError):
        pass
    return None


_SCENE_RUN_CAP = 40  # cap the runs listed in the navigate picker so the dropdown stays usable


def _run_dirs_newest_first(base_dir: str) -> list[str]:
    """Run subdirectory names under `base_dir`, newest first by **modification time** -- NOT by name.
    A named run (e.g. `absence`) must surface by recency, not alphabetically; sorting by name only
    happened to look right for `gui_<timestamp>` names and buried everything else."""
    entries: list[tuple[float, str]] = []
    for name in os.listdir(base_dir):
        path = os.path.join(base_dir, name)
        if os.path.isdir(path):
            try:
                entries.append((os.path.getmtime(path), name))
            except OSError:
                continue
    return [name for _mtime, name in sorted(entries, reverse=True)]


def _run_asset_paths(scene_path: str, scene) -> dict[str, str]:
    """For a generated run's scene, the sprites that run ALREADY autogenerated (in its
    `sandbox_workspace/asset_cache/`, keyed by `<type>.png`) — so Play renders the world with its own
    generated art, no new image-API calls. Empty for an example scene or a run without a cache."""
    parts = scene_path.replace("\\", "/").split("/")
    if len(parts) < 3 or parts[0] != "runs":
        return {}
    cache = os.path.abspath(os.path.join(os.getcwd(), "runs", parts[1], "sandbox_workspace", "asset_cache"))
    if not os.path.isdir(cache):
        return {}
    from infinienv.assets.resolver import scene_asset_types

    paths: dict[str, str] = {}
    for t in scene_asset_types(scene):
        p = os.path.join(cache, f"{t}.png")
        if os.path.isfile(p):
            paths[t] = p
    return paths


def _play_frame_b64(env) -> str:
    """The env's current frame as a base64 data-URI PNG for an <img> src."""
    import base64

    from infinienv.engine.env import frame_to_png_bytes

    png = frame_to_png_bytes(env.frames[-1])
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _play_state(env, info: dict, *, done: bool, won: bool) -> dict:
    """The JSON payload the browser needs to render the current play state after start/step."""
    return {
        "frame": _play_frame_b64(env),
        "goals": info["goals"],
        "steps": info["steps"],
        "max_steps": env.max_steps,
        "resolved": info.get("resolved_action"),
        "legal": info.get("action_legal", True),
        "done": done,
        "won": won,
    }


def _list_scenes() -> list[dict]:
    """Selectable scenes for the navigate picker.

    Two sources: the committed `examples/` worlds (curated, always guaranteed-playable), and any
    generated run's `scene.json` under `runs/` -- **including sandbox worlds**. A sandbox scene.json
    loads through the real schema and uses the fixed-vocabulary goals, so the deterministic env can
    play it; navigate plays that *deterministic interpretation* of the declared scene (static layout
    + fixed goals), scored by code, not the agent's own custom-coded physics (which the trusted
    process never runs -- the isolation invariant). Paths are enumerated here and validated against
    this list on use, so the client can't request an arbitrary path."""
    scenes: list[dict] = []
    examples_dir = os.path.abspath(os.path.join(os.getcwd(), "examples"))
    if os.path.isdir(examples_dir):
        for name in sorted(os.listdir(examples_dir)):
            full = os.path.join(examples_dir, name)
            if name.endswith(".json"):
                scenes.append({"label": f"example: {name}", "path": f"examples/{name}"})
            elif os.path.isfile(os.path.join(full, "scene.json")):
                scenes.append({"label": f"example: {name}", "path": f"examples/{name}/scene.json"})
    runs_dir = os.path.abspath(os.path.join(os.getcwd(), "runs"))
    if os.path.isdir(runs_dir):
        count = 0
        for name in _run_dirs_newest_first(runs_dir):  # newest first by mtime
            if count >= _SCENE_RUN_CAP:
                break
            if os.path.isfile(os.path.join(runs_dir, name, "scene.json")):
                scenes.append({"label": f"run: {name}", "path": f"runs/{name}/scene.json"})
                count += 1
    return scenes


def create_app():
    from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template("index.html", providers=PROVIDER_NAMES)

    @app.post("/api/generate")
    def api_generate():
        data = request.get_json(force=True) or {}
        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400

        try:
            seed = int(data.get("seed") or 42)
        except (TypeError, ValueError):
            return jsonify({"error": "seed must be an integer"}), 400

        raw_repair = data.get("max_repair_attempts")
        max_repair_attempts = None
        if raw_repair not in (None, ""):
            try:
                max_repair_attempts = int(raw_repair)
            except (TypeError, ValueError):
                return jsonify({"error": "max_repair_attempts must be an integer"}), 400

        # Defaults under runs/, but -- unlike the CLI -- doesn't require it: neither run_job
        # below passes require_runs_dir, so a reviewer can point the GUI at any writable
        # directory under cwd. The CLI's `generate` command enforces runs/ (see cli.py);
        # deliberately not mirrored here.
        out_dir = (data.get("out") or "").strip() or f"runs/gui_{int(time.time())}"
        sandbox = bool(data.get("sandbox"))

        assets_mode = data.get("assets") or "none"
        if assets_mode not in ("none", "local", "generated", "auto"):
            return jsonify({"error": f"unknown assets mode {assets_mode!r}"}), 400

        # Prompt enrichment defaults on for sandbox runs; the form sends refine_prompt=false only
        # when the user unchecks it. Absent key -> default True.
        refine_prompt = data.get("refine_prompt", True) is not False

        # Which sandbox agent runtime to use (only meaningful when sandbox is checked). Absent ->
        # None, so run_sandbox_generation falls back to INFINIENV_SANDBOX_BACKEND, then "openai".
        sandbox_backend = data.get("sandbox_backend") or None
        if sandbox_backend not in (None, "openai", "claude"):
            return jsonify({"error": f"unknown sandbox backend {sandbox_backend!r}"}), 400

        # Which model the sandbox agent runs as. Absent -> None, so run_sandbox_generation falls
        # back to INFINIENV_SANDBOX_MODEL then the backend default. A provided model must be one of
        # the backend's known-good options (see SANDBOX_MODELS) -- don't forward arbitrary strings.
        sandbox_model = data.get("sandbox_model") or None
        if sandbox_model is not None:
            effective_backend = sandbox_backend or "openai"
            if sandbox_model not in SANDBOX_MODELS.get(effective_backend, ()):
                return jsonify(
                    {"error": f"unknown sandbox model {sandbox_model!r} for backend {effective_backend!r}"}
                ), 400

        job = Job()
        job_id = uuid.uuid4().hex
        with _jobs_lock:
            _jobs[job_id] = job

        if sandbox:
            # --sandbox ignores provider/no_fallback -- same as the CLI (see CLAUDE.md section
            # 11) -- but --assets applies the same as any other run, so it's still threaded
            # through here.
            thread = threading.Thread(
                target=_run_sandbox_job,
                kwargs=dict(
                    job=job,
                    prompt=prompt,
                    seed=seed,
                    out_dir=out_dir,
                    max_repair_attempts=max_repair_attempts,
                    assets_mode=assets_mode,
                    refine_prompt=refine_prompt,
                    backend=sandbox_backend,
                    model=sandbox_model,
                ),
                daemon=True,
            )
        else:
            provider_name = data.get("provider") or "mock"
            if provider_name not in PROVIDER_NAMES:
                return jsonify({"error": f"unknown provider {provider_name!r}"}), 400

            no_fallback = bool(data.get("no_fallback"))

            thread = threading.Thread(
                target=_run_job,
                kwargs=dict(
                    job=job,
                    provider_name=provider_name,
                    prompt=prompt,
                    seed=seed,
                    out_dir=out_dir,
                    max_repair_attempts=max_repair_attempts,
                    allow_fallback=not no_fallback,
                    assets_mode=assets_mode,
                ),
                daemon=True,
            )
        thread.start()
        return jsonify({"job_id": job_id}), 202

    @app.get("/api/scenes")
    def api_scenes():
        return jsonify({"scenes": _list_scenes()})

    @app.post("/api/navigate")
    def api_navigate():
        data = request.get_json(force=True) or {}

        # The scene must be one we offered (path-safety: no arbitrary path from the client).
        scene = (data.get("scene") or "").strip()
        allowed = {s["path"] for s in _list_scenes()}
        if scene not in allowed:
            return jsonify({"error": "unknown or missing scene"}), 400

        backend = data.get("vision_backend") or "openai"
        if backend not in ("openai", "claude"):
            return jsonify({"error": f"unknown vision backend {backend!r}"}), 400

        model = data.get("model") or None
        if model is not None and model not in SANDBOX_MODELS.get(backend, ()):
            return jsonify({"error": f"unknown model {model!r} for backend {backend!r}"}), 400

        max_steps = None
        raw_steps = data.get("max_steps")
        if raw_steps not in (None, ""):
            try:
                max_steps = int(raw_steps)
            except (TypeError, ValueError):
                return jsonify({"error": "max_steps must be an integer"}), 400

        assets_mode = data.get("assets") or "none"
        if assets_mode not in ("none", "local", "generated", "auto"):
            return jsonify({"error": f"unknown assets mode {assets_mode!r}"}), 400

        judge = data.get("judge", True) is not False
        out_dir = (data.get("out") or "").strip() or f"runs/nav_{int(time.time())}"

        job = Job()
        job_id = uuid.uuid4().hex
        with _jobs_lock:
            _jobs[job_id] = job

        # A sandbox world (a side-view platformer whose real game lives in its own code) is played
        # FAITHFULLY -- a vision policy drives its real game inside the sandbox -- not through the
        # deterministic top-down engine, which would mis-render + mis-play it. Faithful play only
        # supports the OpenAI vision backend (it runs inside the sandbox); Claude falls through to
        # the deterministic path, which is only coherent for a top-down scene anyway.
        faithful_run_dir = _faithful_run_dir(scene) if backend == "openai" else None
        if faithful_run_dir is not None:
            thread = threading.Thread(
                target=_run_faithful_play_job,
                kwargs=dict(
                    job=job, run_dir=faithful_run_dir, backend=backend, model=model,
                    max_steps=max_steps, judge=judge, out_dir=out_dir,
                ),
                daemon=True,
            )
        else:
            thread = threading.Thread(
                target=_run_navigate_job,
                kwargs=dict(
                    job=job, scene_path=scene, backend=backend, model=model,
                    max_steps=max_steps, assets_mode=assets_mode, judge=judge, out_dir=out_dir,
                ),
                daemon=True,
            )
        thread.start()
        return jsonify({"job_id": job_id}), 202

    @app.post("/api/play/start")
    def api_play_start():
        """Start a human-play session: build an InfiniEnv for the chosen scene and return the first
        frame + goal, so the browser can drive it with the keyboard (the human as the controller)."""
        data = request.get_json(force=True) or {}
        scene_path = (data.get("scene") or "").strip()
        allowed = {s["path"] for s in _list_scenes()}
        if scene_path not in allowed:  # path-safety: only a scene we offered
            return jsonify({"error": "unknown or missing scene"}), 400

        # "world" (default) = render with THIS run's already-autogenerated sprites (no new API calls);
        # none/local/generated/auto go through the normal repo-cache resolution.
        assets_mode = data.get("assets") or "world"
        if assets_mode not in ("world", "none", "local", "generated", "auto"):
            return jsonify({"error": f"unknown assets mode {assets_mode!r}"}), 400

        from infinienv.engine.env import CONTROLLER_ACTIONS, InfiniEnv
        from infinienv.evaluation.vision_runner import _goal_text, _resolve_asset_paths
        from infinienv.schema.scene_schema import scene_spec_from_dict

        full = os.path.abspath(os.path.join(os.getcwd(), scene_path))
        try:
            with open(full) as f:
                scene = scene_spec_from_dict(json.load(f))
        except Exception as exc:  # noqa: BLE001 - a bad scene file is a client-visible 400, not a 500
            return jsonify({"error": f"could not load scene: {exc}"}), 400

        if assets_mode == "world":
            asset_paths = _run_asset_paths(scene_path, scene)  # the run's own generated sprites, else flat
        else:
            asset_paths = _resolve_asset_paths(scene, assets_mode, None)
        env = InfiniEnv(scene, max_steps=_PLAY_MAX_STEPS, asset_paths=asset_paths)
        _obs, info = env.reset()

        sid = uuid.uuid4().hex
        with _play_lock:
            if len(_play_sessions) >= _PLAY_SESSION_CAP:  # evict the oldest so envs never pile up
                oldest = min(_play_sessions, key=lambda k: _play_sessions[k]["created"])
                _play_sessions.pop(oldest, None)
            _play_sessions[sid] = {"env": env, "created": time.time()}

        payload = _play_state(env, info, done=False, won=False)
        payload["session_id"] = sid
        payload["goal"] = _goal_text(scene)
        payload["actions"] = list(CONTROLLER_ACTIONS)
        return jsonify(payload)

    @app.post("/api/play/step")
    def api_play_step():
        """Apply one controller action to a play session and return the new frame + goal state.
        Win/termination is CODE-defined (is_goal_complete over GameState), never from pixels."""
        data = request.get_json(force=True) or {}
        sid = (data.get("session_id") or "").strip()
        action = (data.get("action") or "").strip()
        with _play_lock:
            sess = _play_sessions.get(sid)
        if sess is None:
            return jsonify({"error": "unknown or expired session"}), 404

        from infinienv.engine.env import CONTROLLER_ACTIONS

        if action not in CONTROLLER_ACTIONS:
            return jsonify({"error": f"unknown action {action!r}"}), 400

        env = sess["env"]
        _obs, reward, terminated, truncated, info = env.step(action)
        done = bool(terminated or truncated)
        won = bool(info["all_complete"])
        if done:  # episode over -> free the env
            with _play_lock:
                _play_sessions.pop(sid, None)

        payload = _play_state(env, info, done=done, won=won)
        payload["reward"] = reward
        return jsonify(payload)

    @app.get("/api/stream/<job_id>")
    def api_stream(job_id: str):
        with _jobs_lock:
            job = _jobs.get(job_id)
        if job is None:
            abort(404)

        def generate_events():
            while True:
                try:
                    event = job.events.get(timeout=1.0)
                except queue.Empty:
                    if job.done:
                        return
                    yield ": keep-alive\n\n"  # SSE comment line, keeps the connection open
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    with _jobs_lock:
                        _jobs.pop(job_id, None)
                    return

        return Response(generate_events(), mimetype="text/event-stream")

    @app.get("/artifact/<path:filepath>")
    def artifact(filepath: str):
        base = os.path.abspath(os.getcwd())
        full = os.path.abspath(os.path.join(base, filepath))
        if os.path.commonpath([full, base]) != base:
            abort(403)
        directory, name = os.path.split(full)
        if not os.path.exists(full):
            abort(404)
        return send_from_directory(directory, name)

    @app.get("/api/runs")
    def api_runs():
        entries = []
        # Scan examples/ (committed example worlds) first so a no-key reviewer always sees a real
        # generated world in the gallery, then runs/ (live runs). Examples lead so the 50-item cap
        # can't hide them on a machine with many runs.
        for base in ("examples", "runs"):
            base_dir = os.path.abspath(os.path.join(os.getcwd(), base))
            if not os.path.isdir(base_dir):
                continue
            # runs newest-first by mtime (so a recent named run isn't buried); examples by name.
            names = _run_dirs_newest_first(base_dir) if base == "runs" else sorted(os.listdir(base_dir), reverse=True)
            for name in names:
                run_path = os.path.join(base_dir, name)
                has_scene = os.path.isfile(os.path.join(run_path, "scene.json"))
                # A navigate (vision) run writes episode.gif but no scene.json/render.png -- still
                # show it in the gallery, using episode.gif as its thumbnail.
                has_episode = os.path.isfile(os.path.join(run_path, "episode.gif"))
                if not (has_scene or has_episode):
                    continue
                success, sandbox, vision = None, False, False
                metrics_path = os.path.join(run_path, "metrics.json")
                if os.path.isfile(metrics_path):
                    try:
                        with open(metrics_path) as f:
                            run_metrics = json.load(f)
                        source = run_metrics.get("source")
                        sandbox = source == "sandbox"
                        vision = source == "vision_navigation"
                        # A vision run's code-defined verdict is vision_success, not success.
                        success = run_metrics.get("vision_success") if vision else run_metrics.get("success")
                    except (OSError, json.JSONDecodeError):
                        pass
                episode_url = f"/artifact/{base}/{name}/episode.gif" if has_episode else None
                entries.append(
                    {
                        "name": name if base == "runs" else f"example: {name}",
                        "success": success,
                        "sandbox": sandbox,
                        "vision": vision,
                        # For a vision run, episode.gif is both the still and the replay.
                        "render_url": (f"/artifact/{base}/{name}/render.png"
                                       if os.path.isfile(os.path.join(run_path, "render.png"))
                                       else episode_url),
                        "replay_url": (f"/artifact/{base}/{name}/replay.gif"
                                       if os.path.isfile(os.path.join(run_path, "replay.gif"))
                                       else episode_url),
                    }
                )
        return jsonify({"runs": entries[:50]})

    return app


def launch(host: str = "127.0.0.1", port: int = 5050, *, open_browser: bool = True) -> None:
    try:
        import flask  # noqa: F401
    except ImportError as exc:
        raise ProviderError(
            "The 'flask' package is not installed. Install it with `pip install infinienv[gui]`."
        ) from exc

    app = create_app()
    if open_browser:
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"InfiniEnv GUI running at http://{host}:{port} (Ctrl+C to stop)")
    app.run(host=host, port=port, threaded=True)
