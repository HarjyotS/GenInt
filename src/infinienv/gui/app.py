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
        job.events.put({"type": "stage", "message": msg})

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
) -> None:
    from infinienv.sandbox.runner import run_sandbox_generation

    def on_stage(msg: str) -> None:
        job.events.put({"type": "stage", "message": msg})

    try:
        result = run_sandbox_generation(
            prompt,
            seed,
            out_dir,
            max_repair_attempts=max_repair_attempts,
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
            }
        )
    except ProviderError as exc:
        job.events.put({"type": "error", "message": str(exc)})
    except Exception as exc:  # a GUI must never crash the server on a bad run; surface it instead
        job.events.put({"type": "error", "message": f"unexpected error: {exc}"})
    finally:
        job.done = True


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

        out_dir = (data.get("out") or "").strip() or f"runs/gui_{int(time.time())}"
        sandbox = bool(data.get("sandbox"))

        job = Job()
        job_id = uuid.uuid4().hex
        with _jobs_lock:
            _jobs[job_id] = job

        if sandbox:
            # --sandbox ignores provider/assets/no_fallback -- same as the CLI (see CLAUDE.md
            # section 11) -- so those fields are neither read nor validated here.
            thread = threading.Thread(
                target=_run_sandbox_job,
                kwargs=dict(
                    job=job,
                    prompt=prompt,
                    seed=seed,
                    out_dir=out_dir,
                    max_repair_attempts=max_repair_attempts,
                ),
                daemon=True,
            )
        else:
            provider_name = data.get("provider") or "mock"
            if provider_name not in PROVIDER_NAMES:
                return jsonify({"error": f"unknown provider {provider_name!r}"}), 400

            assets_mode = data.get("assets") or "none"
            if assets_mode not in ("none", "local", "generated", "auto"):
                return jsonify({"error": f"unknown assets mode {assets_mode!r}"}), 400

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
        runs_dir = os.path.abspath(os.path.join(os.getcwd(), "runs"))
        if not os.path.isdir(runs_dir):
            return jsonify({"runs": []})
        entries = []
        for name in sorted(os.listdir(runs_dir), reverse=True):
            run_path = os.path.join(runs_dir, name)
            scene_path = os.path.join(run_path, "scene.json")
            if not os.path.isfile(scene_path):
                continue
            metrics_path = os.path.join(run_path, "metrics.json")
            success = None
            sandbox = False
            if os.path.isfile(metrics_path):
                try:
                    with open(metrics_path) as f:
                        run_metrics = json.load(f)
                    success = run_metrics.get("success")
                    sandbox = run_metrics.get("source") == "sandbox"
                except (OSError, json.JSONDecodeError):
                    pass
            entries.append(
                {
                    "name": name,
                    "success": success,
                    "sandbox": sandbox,
                    "render_url": f"/artifact/runs/{name}/render.png"
                    if os.path.isfile(os.path.join(run_path, "render.png"))
                    else None,
                    "replay_url": f"/artifact/runs/{name}/replay.gif"
                    if os.path.isfile(os.path.join(run_path, "replay.gif"))
                    else None,
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
