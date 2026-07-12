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
            for name in sorted(os.listdir(base_dir), reverse=True):
                run_path = os.path.join(base_dir, name)
                if not os.path.isfile(os.path.join(run_path, "scene.json")):
                    continue
                success, sandbox = None, False
                metrics_path = os.path.join(run_path, "metrics.json")
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
                        "name": name if base == "runs" else f"example: {name}",
                        "success": success,
                        "sandbox": sandbox,
                        "render_url": f"/artifact/{base}/{name}/render.png"
                        if os.path.isfile(os.path.join(run_path, "render.png"))
                        else None,
                        "replay_url": f"/artifact/{base}/{name}/replay.gif"
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
