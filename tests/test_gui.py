import json

import pytest

from infinienv.gui.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_page_is_sandbox_only(client):
    # Generate is sandbox-only: no provider select / sandbox toggle in the form, but the agent
    # runtime + model pickers are present.
    res = client.get("/")
    assert res.status_code == 200
    assert b'id="provider"' not in res.data and b'id="sandbox"' not in res.data
    assert b'id="sandbox_backend"' in res.data and b'id="sandbox_model"' in res.data


def test_generate_requires_prompt(client):
    res = client.post("/api/generate", json={"prompt": "  "})
    assert res.status_code == 400
    assert "prompt" in res.get_json()["error"]


def test_generate_rejects_unknown_provider(client):
    res = client.post("/api/generate", json={"prompt": "a kitchen task", "provider": "nope"})
    assert res.status_code == 400


def _consume_sse(response) -> list[dict]:
    """Parse a Flask test-client streamed SSE response into a list of event dicts."""
    events = []
    buf = ""
    for chunk in response.response:
        buf += chunk.decode() if isinstance(chunk, bytes) else chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            if not raw.strip() or raw.startswith(":"):
                continue
            lines = raw.splitlines()
            data_line = next((line[len("data: ") :] for line in lines if line.startswith("data: ")), None)
            if data_line:
                events.append(json.loads(data_line))
    return events


def test_full_generate_flow_with_mock_provider_streams_stage_and_done_events(client):
    res = client.post("/api/generate", json={"prompt": "a kitchen delivery task", "provider": "mock", "seed": 1})
    assert res.status_code == 202
    job_id = res.get_json()["job_id"]

    stream = client.get(f"/api/stream/{job_id}")
    events = _consume_sse(stream)

    stage_events = [e for e in events if e["type"] == "stage"]
    done_events = [e for e in events if e["type"] == "done"]
    assert len(stage_events) >= 3
    assert len(done_events) == 1
    done = done_events[0]
    assert done["success"] is True
    assert "scene" in done and "metadata" in done["scene"]
    assert done["out_dir"].startswith("runs/gui_")

    # the artifact route should now serve the real render.png that run produced
    render_res = client.get(f"/artifact/{done['out_dir']}/render.png")
    assert render_res.status_code == 200
    assert render_res.mimetype == "image/png"


def test_classify_stage_maps_narration_prefixes():
    from infinienv.gui.app import _classify_stage

    cases = {
        "$ ls -la": "command",
        "Calling tool: view_image": "command",
        "Editing: edit run_scene.py, add engine/x.py": "edit",
        "Output:\nfile_a\nfile_b": "output",
        "⁣LIVE⁣planning the tile world": "live",
        "Thinking: I'll gate the climb on being on the ladder": "decision",
        "Agent: building the cave": "agent",
        "Auditor: the run faithfully implements the spec.": "audit",
        "Independent reviewer auditing the run for faithfulness to the spec...": "audit",
        "Running sandbox agent (attempt 1/3)...": "attempt",
        "Repairing against the outer sanity check (attempt 2/3)...": "attempt",
        "Refined prompt handed to the agent:\n...": "refine",
        "Preparing isolated sandbox workspace (copy of ...)...": "workspace",
        "  command failed (exit 1): boom": "error",
        "Viewing an image it produced...": "image",
        # TODO harness: requirements + build-plan (plan.py) narration.
        "REQ_SEED r1: the player can jump": "todo",
        "PLAN_ADD t1: gravity + jump physics": "todo",
        "$ /venv/bin/python plan.py start t2": "todo",
        "Requirements derived (18); the agent will plan a build to meet them.": "todo",
        "$ /venv/bin/python run_scene.py": "command",
        "something unrecognized": "status",
    }
    for msg, kind in cases.items():
        assert _classify_stage(msg) == kind, msg


def test_stage_events_carry_a_kind(client):
    res = client.post("/api/generate", json={"prompt": "a kitchen delivery task", "provider": "mock", "seed": 5})
    job_id = res.get_json()["job_id"]
    events = _consume_sse(client.get(f"/api/stream/{job_id}"))
    stage_events = [e for e in events if e["type"] == "stage"]
    assert stage_events and all("kind" in e for e in stage_events)


def test_artifact_route_blocks_path_traversal(client):
    res = client.get("/artifact/../../etc/passwd")
    assert res.status_code in (403, 404)


def test_runs_listing_reflects_completed_run(client):
    res = client.post("/api/generate", json={"prompt": "a kitchen delivery task", "provider": "mock", "seed": 2})
    job_id = res.get_json()["job_id"]
    _consume_sse(client.get(f"/api/stream/{job_id}"))

    runs = client.get("/api/runs").get_json()["runs"]
    assert len(runs) >= 1
    assert runs[0]["success"] is True


def _fake_run_sandbox_generation(
    prompt, seed, out_dir, *, max_repair_attempts=None, assets_mode="none", backend=None, model=None, on_stage=None, **_
):
    import os

    _fake_run_sandbox_generation.last_assets_mode = assets_mode
    _fake_run_sandbox_generation.last_backend = backend
    _fake_run_sandbox_generation.last_model = model

    if on_stage is not None:
        on_stage("Running sandbox agent (attempt 1/1)...")
        on_stage("Attempt 1 passed the outer sanity check.")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "scene.json"), "w") as f:
        json.dump({"version": "0.1", "metadata": {"name": "fake"}}, f)
    metrics = {
        "source": "sandbox",
        "success": True,
        "sandbox_self_reported_success": True,
        "outer_sanity_passed": True,
        "outer_sanity_error": None,
        "missing_artifacts": [],
        "repair_attempts": 0,
    }
    with open(os.path.join(out_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f)
    with open(os.path.join(out_dir, "render.png"), "wb") as f:
        f.write(b"\x89PNG-fake")
    with open(os.path.join(out_dir, "replay.gif"), "wb") as f:
        f.write(b"GIF89a-fake")
    return {
        "success": True,
        "agent_summary": "built a fake sandbox scene",
        "run_error": None,
        "artifact_paths": {},
        "workspace_dir": os.path.join(out_dir, "sandbox_workspace"),
        "metrics": metrics,
        "repair_attempts": 0,
    }


def test_sandbox_generate_flow_streams_stage_and_done_events(client, monkeypatch):
    import infinienv.sandbox.runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner, "run_sandbox_generation", _fake_run_sandbox_generation)

    res = client.post("/api/generate", json={"prompt": "a chase task", "sandbox": True, "seed": 3})
    assert res.status_code == 202
    job_id = res.get_json()["job_id"]

    events = _consume_sse(client.get(f"/api/stream/{job_id}"))
    stage_events = [e for e in events if e["type"] == "stage"]
    done_events = [e for e in events if e["type"] == "done"]
    assert len(stage_events) == 2
    assert len(done_events) == 1
    done = done_events[0]
    assert done["success"] is True
    assert done["sandbox"] is True
    assert done["agent_summary"] == "built a fake sandbox scene"
    assert done["metrics"]["outer_sanity_passed"] is True
    assert "scene" not in done  # sandbox path doesn't re-validate/re-parse a SceneSpec itself
    assert _fake_run_sandbox_generation.last_assets_mode == "auto"  # auto is the default now

    runs = client.get("/api/runs").get_json()["runs"]
    assert any(r["sandbox"] is True for r in runs)


def test_sandbox_generate_flow_threads_assets_mode_through(client, monkeypatch):
    import infinienv.sandbox.runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner, "run_sandbox_generation", _fake_run_sandbox_generation)

    res = client.post(
        "/api/generate",
        json={"prompt": "a chase task", "sandbox": True, "seed": 3, "assets": "local"},
    )
    assert res.status_code == 202
    job_id = res.get_json()["job_id"]
    _consume_sse(client.get(f"/api/stream/{job_id}"))

    assert _fake_run_sandbox_generation.last_assets_mode == "local"


def test_sandbox_generate_flow_threads_backend_choice_through(client, monkeypatch):
    import infinienv.sandbox.runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner, "run_sandbox_generation", _fake_run_sandbox_generation)

    res = client.post(
        "/api/generate",
        json={"prompt": "a chase task", "sandbox": True, "seed": 3, "sandbox_backend": "claude"},
    )
    assert res.status_code == 202
    _consume_sse(client.get(f"/api/stream/{res.get_json()['job_id']}"))
    assert _fake_run_sandbox_generation.last_backend == "claude"


def test_sandbox_generate_rejects_unknown_backend(client):
    res = client.post(
        "/api/generate",
        json={"prompt": "a chase task", "sandbox": True, "sandbox_backend": "bogus"},
    )
    assert res.status_code == 400
    assert "backend" in res.get_json()["error"]


def test_sandbox_done_payload_includes_assets_summary(client, monkeypatch):
    import infinienv.sandbox.runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner, "run_sandbox_generation", _fake_run_sandbox_generation)
    res = client.post("/api/generate", json={"prompt": "a maze", "sandbox": True, "seed": 4})
    job_id = res.get_json()["job_id"]
    events = _consume_sse(client.get(f"/api/stream/{job_id}"))
    done = next(e for e in events if e["type"] == "done")
    assert "assets" in done and "items" in done["assets"] and "notes" in done["assets"]


def test_sandbox_generate_flow_threads_model_choice_through(client, monkeypatch):
    import infinienv.sandbox.runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner, "run_sandbox_generation", _fake_run_sandbox_generation)

    res = client.post(
        "/api/generate",
        json={"prompt": "a chase task", "sandbox": True, "seed": 3,
              "sandbox_backend": "openai", "sandbox_model": "gpt-5.6-sol"},
    )
    assert res.status_code == 202
    _consume_sse(client.get(f"/api/stream/{res.get_json()['job_id']}"))
    assert _fake_run_sandbox_generation.last_model == "gpt-5.6-sol"


def test_sandbox_generate_rejects_model_not_valid_for_backend(client):
    # a Claude model with the OpenAI backend is rejected before any run starts
    res = client.post(
        "/api/generate",
        json={"prompt": "a chase task", "sandbox": True,
              "sandbox_backend": "openai", "sandbox_model": "claude-opus-4-8"},
    )
    assert res.status_code == 400
    assert "model" in res.get_json()["error"]


def test_sandbox_generate_accepts_claude_model_with_claude_backend(client, monkeypatch):
    import infinienv.sandbox.runner as sandbox_runner

    monkeypatch.setattr(sandbox_runner, "run_sandbox_generation", _fake_run_sandbox_generation)

    res = client.post(
        "/api/generate",
        json={"prompt": "a chase task", "sandbox": True, "seed": 1,
              "sandbox_backend": "claude", "sandbox_model": "claude-opus-4-8"},
    )
    assert res.status_code == 202
    _consume_sse(client.get(f"/api/stream/{res.get_json()['job_id']}"))
    assert _fake_run_sandbox_generation.last_model == "claude-opus-4-8"


# ---- navigate (vision-policy) mode ----

import os


def _write_example_scene(tmp_path) -> str:
    """A trivial pickup scene under examples/ so the navigate flow has something to play."""
    ex_dir = tmp_path / "examples"
    ex_dir.mkdir(exist_ok=True)
    scene = {
        "version": "0.1", "seed": 1, "metadata": {"name": "mini", "prompt": "pick up the can"},
        "grid": {"width": 4, "height": 2, "tile_size": 32},
        "agent": {"id": "agent", "x": 0, "y": 0},
        "objects": [{"id": "can_1", "type": "can", "x": 1, "y": 0, "portable": True}],
        "walls": [], "goals": [{"id": "pick", "type": "pickup", "object_id": "can_1"}],
    }
    (ex_dir / "mini.json").write_text(json.dumps(scene))
    return "examples/mini.json"


class _FakeVisionPolicy:
    backend = "openai"
    model = "fake-vision"

    def __init__(self, *a, **k):
        self._plan = iter(["right", "interact"])

    def act(self, frame, goal, **kw):
        return [next(self._plan, "wait")], "fake"  # a (one-action) plan, per the new contract

    def judge_final_frame(self, frame, goal):
        return True, "YES"


def test_index_page_has_both_modes(client):
    res = client.get("/")
    assert b'data-mode="generate"' in res.data and b'data-mode="navigate"' in res.data
    assert b'id="scene"' in res.data and b'id="vision_backend"' in res.data


def test_scenes_endpoint_lists_examples(client, tmp_path):
    _write_example_scene(tmp_path)
    res = client.get("/api/scenes")
    assert res.status_code == 200
    paths = [s["path"] for s in res.get_json()["scenes"]]
    assert "examples/mini.json" in paths


def test_scenes_endpoint_includes_generated_runs(client, tmp_path):
    # A generated (incl. sandbox) run's scene.json must be selectable for navigate.
    _write_example_scene(tmp_path)
    run = tmp_path / "runs" / "gui_sandbox_world"
    run.mkdir(parents=True)
    (run / "scene.json").write_text(json.dumps({
        "version": "0.1", "seed": 1, "metadata": {"name": "w"},
        "grid": {"width": 3, "height": 2, "tile_size": 32},
        "agent": {"id": "agent", "x": 0, "y": 0},
        "objects": [{"id": "exit", "type": "exit", "x": 2, "y": 0, "solid": False}],
        "walls": [], "goals": [{"id": "g", "type": "reach", "target_id": "exit"}],
    }))
    res = client.get("/api/scenes")
    entries = {s["path"]: s["label"] for s in res.get_json()["scenes"]}
    assert "runs/gui_sandbox_world/scene.json" in entries
    assert entries["runs/gui_sandbox_world/scene.json"].startswith("run:")


def test_scenes_picker_orders_runs_by_mtime_not_name(client, tmp_path):
    # Regression: a recently-created named run (e.g. "absence") must surface by recency, not
    # alphabetically -- the picker used to sort by name and bury/cut it under the run cap.
    import os
    import time

    runs = tmp_path / "runs"
    scene = json.dumps({
        "version": "0.1", "seed": 1, "metadata": {"name": "w"},
        "grid": {"width": 3, "height": 2, "tile_size": 32}, "agent": {"id": "agent", "x": 0, "y": 0},
        "objects": [{"id": "exit", "type": "exit", "x": 2, "y": 0, "solid": False}],
        "walls": [], "goals": [{"id": "g", "type": "reach", "target_id": "exit"}],
    })
    # "zeta_old" is created first (older), "absence" second (newer) -- name order would put "zeta"
    # first (reverse-alpha), so a name sort hides the newer "absence".
    (runs / "zeta_old").mkdir(parents=True)
    (runs / "zeta_old" / "scene.json").write_text(scene)
    time.sleep(0.02)
    (runs / "absence").mkdir(parents=True)
    (runs / "absence" / "scene.json").write_text(scene)
    os.utime(runs / "absence", None)  # ensure absence is newest

    paths = [s["path"] for s in client.get("/api/scenes").get_json()["scenes"]]
    assert "runs/absence/scene.json" in paths
    # newest-first: absence (newer) before zeta_old (older)
    assert paths.index("runs/absence/scene.json") < paths.index("runs/zeta_old/scene.json")


def test_navigate_rejects_unknown_scene(client, tmp_path):
    _write_example_scene(tmp_path)
    res = client.post("/api/navigate", json={"scene": "runs/../secret.json"})
    assert res.status_code == 400


def test_navigate_flow_streams_stage_and_done_events(client, tmp_path, monkeypatch):
    scene_path = _write_example_scene(tmp_path)
    import infinienv.evaluation.vision_runner as vr
    monkeypatch.setattr(vr, "VisionPolicy", _FakeVisionPolicy)

    res = client.post("/api/navigate", json={"scene": scene_path, "vision_backend": "openai"})
    assert res.status_code == 202
    job_id = res.get_json()["job_id"]

    events = _consume_sse(client.get(f"/api/stream/{job_id}"))
    done = next(e for e in events if e["type"] == "done")
    assert done["mode"] == "navigate"
    # Success is the CODE-defined verdict; the faked policy solves the pickup.
    assert done["success"] is True
    assert done["metrics"]["source"] == "vision_navigation"
    assert done["metrics"]["vision_success"] is True
    assert done["metrics"]["vlm_judge_success"] is True
    assert done["out_dir"].startswith("runs/nav_")

    # episode.gif was written and is served
    gif = client.get(f"/artifact/{done['out_dir']}/episode.gif")
    assert gif.status_code == 200


def test_navigate_routes_a_sandbox_world_to_faithful_play(client, tmp_path, monkeypatch):
    # A sandbox world (dir with sandbox_workspace/, metrics source==sandbox) must be played
    # FAITHFULLY (its real game inside the sandbox), not through the deterministic top-down env.
    run = tmp_path / "runs" / "gui_sbx"
    (run / "sandbox_workspace").mkdir(parents=True)
    (run / "scene.json").write_text(json.dumps({
        "version": "0.1", "seed": 1, "metadata": {"name": "w", "prompt": "a platformer"},
        "grid": {"width": 3, "height": 2, "tile_size": 32}, "agent": {"id": "agent", "x": 0, "y": 0},
        "objects": [{"id": "exit", "type": "exit", "x": 2, "y": 0, "solid": False}],
        "walls": [], "goals": [{"id": "g", "type": "reach", "target_id": "exit"}],
    }))
    (run / "metrics.json").write_text(json.dumps({"source": "sandbox", "success": True}))

    captured = {}

    def fake_play(run_dir, out_dir, **kw):
        captured["run_dir"] = run_dir
        return {"source": "vision_navigation", "faithful": True, "vision_success": True,
                "steps": 5, "max_steps": 60, "model": "gpt-5.6-terra", "backend": "openai"}

    import infinienv.sandbox.vision_runner as vr
    monkeypatch.setattr(vr, "play_sandbox_world", fake_play)

    res = client.post("/api/navigate", json={"scene": "runs/gui_sbx/scene.json", "vision_backend": "openai"})
    assert res.status_code == 202
    events = _consume_sse(client.get(f"/api/stream/{res.get_json()['job_id']}"))
    done = next(e for e in events if e["type"] == "done")
    assert captured["run_dir"] == "runs/gui_sbx"  # routed to faithful play on the run dir
    assert done["faithful"] is True
    assert done["success"] is True
    assert done["metrics"]["vision_success"] is True


# ---- play (human keyboard) mode ----


def test_index_has_play_mode(client):
    res = client.get("/")
    assert b'data-mode="play"' in res.data
    assert b'id="play_scene"' in res.data and b'id="play-stage"' in res.data


def test_play_start_returns_frame_goal_and_session(client, tmp_path):
    scene = _write_example_scene(tmp_path)
    res = client.post("/api/play/start", json={"scene": scene})
    assert res.status_code == 200
    d = res.get_json()
    assert d["session_id"]
    assert d["frame"].startswith("data:image/png;base64,")
    assert "forward" in d["actions"] and "interact" in d["actions"]
    assert d["steps"] == 0 and d["won"] is False and d["done"] is False
    assert d["goal"]  # a natural-language goal is provided


def test_play_step_moves_and_wins_by_code(client, tmp_path):
    # agent at (0,0), can at (1,0): move right onto it, interact to pick it up -> pickup goal complete.
    scene = _write_example_scene(tmp_path)
    sid = client.post("/api/play/start", json={"scene": scene}).get_json()["session_id"]

    d = client.post("/api/play/step", json={"session_id": sid, "action": "right"}).get_json()
    assert d["steps"] == 1 and d["resolved"] == "move_right" and d["won"] is False

    d = client.post("/api/play/step", json={"session_id": sid, "action": "interact"}).get_json()
    # Win is CODE-judged (is_goal_complete), never pixels.
    assert d["won"] is True and d["done"] is True


def test_play_rejects_unknown_scene_session_and_action(client, tmp_path):
    scene = _write_example_scene(tmp_path)
    assert client.post("/api/play/start", json={"scene": "runs/../secret.json"}).status_code == 400
    assert client.post("/api/play/step", json={"session_id": "nope", "action": "right"}).status_code == 404
    sid = client.post("/api/play/start", json={"scene": scene}).get_json()["session_id"]
    assert client.post("/api/play/step", json={"session_id": sid, "action": "fly"}).status_code == 400


def test_play_world_assets_use_the_runs_generated_sprites(client, tmp_path):
    # A generated run keeps its sprites in runs/<name>/sandbox_workspace/asset_cache/<type>.png.
    # Play's default `world` assets mode must render with THOSE (no re-generation), not flat cells.
    from PIL import Image

    from infinienv.gui import app as A

    run = tmp_path / "runs" / "mygame"
    cache = run / "sandbox_workspace" / "asset_cache"
    cache.mkdir(parents=True)
    scene = {
        "version": "0.1", "seed": 1, "metadata": {"name": "g", "prompt": "get the can"},
        "grid": {"width": 4, "height": 2, "tile_size": 32},
        "agent": {"id": "agent", "x": 0, "y": 0},
        "objects": [{"id": "can_1", "type": "can", "x": 1, "y": 0, "portable": True}],
        "walls": [], "goals": [{"id": "pick", "type": "pickup", "object_id": "can_1"}],
    }
    (run / "scene.json").write_text(json.dumps(scene))
    for t in ("agent", "can"):  # the run's autogenerated sprites
        Image.new("RGBA", (16, 16), (10, 200, 90, 255)).save(cache / f"{t}.png")

    A._play_sessions.clear()
    res = client.post("/api/play/start", json={"scene": "runs/mygame/scene.json"})  # default assets=world
    assert res.status_code == 200
    env = list(A._play_sessions.values())[-1]["env"]
    # the env resolved the run's OWN sprites (paths under that run's asset_cache), not the repo cache
    assert env.asset_paths.get("can", "").endswith("mygame/sandbox_workspace/asset_cache/can.png")
    assert env.asset_paths.get("agent", "").endswith("mygame/sandbox_workspace/asset_cache/agent.png")


def test_play_session_ends_after_win(client, tmp_path):
    # After the episode ends the session is freed, so a further step 404s (client restarts a new one).
    scene = _write_example_scene(tmp_path)
    sid = client.post("/api/play/start", json={"scene": scene}).get_json()["session_id"]
    client.post("/api/play/step", json={"session_id": sid, "action": "right"})
    client.post("/api/play/step", json={"session_id": sid, "action": "interact"})  # wins -> frees session
    assert client.post("/api/play/step", json={"session_id": sid, "action": "wait"}).status_code == 404


def test_runs_listing_includes_a_vision_run(client, tmp_path):
    out = tmp_path / "runs" / "nav_demo"
    out.mkdir(parents=True)
    (out / "episode.gif").write_bytes(b"GIF89a-fake")
    (out / "metrics.json").write_text(json.dumps({"source": "vision_navigation", "vision_success": True}))

    res = client.get("/api/runs")
    names = {r["name"]: r for r in res.get_json()["runs"]}
    assert "nav_demo" in names
    assert names["nav_demo"]["vision"] is True
    assert names["nav_demo"]["success"] is True
    assert names["nav_demo"]["replay_url"].endswith("/episode.gif")


def _consume_sse_raw(response) -> list[dict]:
    """Parse SSE into a list of {id, event, data} records (data parsed as JSON). Unlike
    _consume_sse this keeps the SSE event NAME and id, so a test can tell a `failed` event from a
    `done` one and assert the id-based resume contract."""
    out: list[dict] = []
    buf = ""
    for chunk in response.response:
        buf += chunk.decode() if isinstance(chunk, bytes) else chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            if not raw.strip() or raw.lstrip().startswith(":"):
                continue
            rec: dict = {}
            for line in raw.splitlines():
                if line.startswith("id: "):
                    rec["id"] = line[len("id: "):]
                elif line.startswith("event: "):
                    rec["event"] = line[len("event: "):]
                elif line.startswith("data: "):
                    rec["data"] = json.loads(line[len("data: "):])
            if "data" in rec:
                out.append(rec)
    return out


def test_stream_is_resumable_from_last_event_id(client):
    """A dropped stream must be able to reconnect and replay from where it left off, so a long run
    isn't reported as failed on a network blip. The server retains the finished job and honors the
    SSE Last-Event-ID header the browser resends."""
    res = client.post("/api/generate", json={"prompt": "a kitchen delivery task", "provider": "mock", "seed": 1})
    job_id = res.get_json()["job_id"]

    first = _consume_sse_raw(client.get(f"/api/stream/{job_id}"))
    assert first, "expected at least one event"
    assert all("id" in r for r in first), "every event must carry an id for resume"
    assert first[-1]["event"] == "done"
    total = len(first)

    # Reconnect as the browser would after a drop after seeing event 0: resume at index 1.
    resumed = _consume_sse_raw(
        client.get(f"/api/stream/{job_id}", headers={"Last-Event-ID": "0"})
    )
    assert [int(r["id"]) for r in resumed] == list(range(1, total)), "resume must replay only newer events"
    assert resumed[-1]["event"] == "done", "the terminal event is still delivered on reconnect"


def test_result_endpoint_recovers_a_finished_run(client):
    """When the stream can't be re-established, the client polls /api/result; a finished run returns
    its terminal payload so a completed run is never shown as a failure."""
    res = client.post("/api/generate", json={"prompt": "a kitchen delivery task", "provider": "mock", "seed": 1})
    job_id = res.get_json()["job_id"]
    _consume_sse(client.get(f"/api/stream/{job_id}"))  # run to completion

    result = client.get(f"/api/result/{job_id}")
    assert result.status_code == 200
    body = result.get_json()
    assert body["status"] == "finished"
    assert body["result"]["type"] == "done" and body["result"]["success"] is True

    # An unknown/evicted job tells the client to stop polling.
    unknown = client.get("/api/result/deadbeef")
    assert unknown.status_code == 404
    assert unknown.get_json()["status"] == "unknown"


def test_run_failure_is_sent_on_failed_channel_not_error(client, monkeypatch):
    """A genuine run failure must arrive on the `failed` SSE event, NOT `error` -- the browser also
    dispatches native transport errors (a dropped connection) as `error`, and the frontend must tell
    a real failure apart from a transient disconnect. The payload still carries type=='error'."""
    from infinienv.llm.base import ProviderError

    def boom(*a, **k):
        raise ProviderError("no API key")

    monkeypatch.setattr("infinienv.evaluation.runner.run_generation", boom)

    res = client.post("/api/generate", json={"prompt": "a kitchen task", "provider": "mock", "seed": 1})
    job_id = res.get_json()["job_id"]
    events = _consume_sse_raw(client.get(f"/api/stream/{job_id}"))

    terminal = events[-1]
    assert terminal["event"] == "failed", "a real failure uses the `failed` channel, not `error`"
    assert terminal["data"]["type"] == "error"
    assert "no API key" in terminal["data"]["message"]
    # And it's recoverable via the result endpoint too.
    assert client.get(f"/api/result/{job_id}").get_json()["result"]["type"] == "error"


# --- Access control + rate limiting (public-deploy hardening) ---------------------------------

def test_public_bind_and_password_required_helpers():
    from infinienv.gui.app import _password_required_message, _public_bind

    assert _public_bind("0.0.0.0") is True
    assert _public_bind("192.168.1.5") is True
    assert _public_bind("127.0.0.1") is False
    assert _public_bind("localhost") is False
    # A public bind with no password is refused; localhost or a set password is fine.
    assert _password_required_message("0.0.0.0", None) is not None
    assert _password_required_message("0.0.0.0", "pw") is None
    assert _password_required_message("127.0.0.1", None) is None


def test_password_gate_blocks_without_and_allows_with_credentials(monkeypatch, tmp_path):
    import base64

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INFINIENV_GUI_PASSWORD", "hunter2")
    app = create_app()
    c = app.test_client()

    # No credentials -> 401 with a Basic challenge (browser shows a login prompt).
    r = c.get("/")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate", "").startswith("Basic")

    # Correct password (any username) -> 200.
    ok = base64.b64encode(b"anyuser:hunter2").decode()
    assert c.get("/", headers={"Authorization": f"Basic {ok}"}).status_code == 200

    # Wrong password -> 401.
    bad = base64.b64encode(b"anyuser:nope").decode()
    assert c.get("/", headers={"Authorization": f"Basic {bad}"}).status_code == 401


def test_no_password_env_leaves_gui_open(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INFINIENV_GUI_PASSWORD", raising=False)
    app = create_app()
    assert app.test_client().get("/").status_code == 200


def test_rate_limit_returns_429(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INFINIENV_GUI_PASSWORD", raising=False)
    monkeypatch.setenv("INFINIENV_GUI_RATE_LIMIT", "1")
    monkeypatch.setenv("INFINIENV_GUI_MAX_CONCURRENT", "100")  # isolate the RATE path from concurrency
    app = create_app()  # note: NOT TESTING -> limits are active
    c = app.test_client()
    body = {"prompt": "a kitchen task", "provider": "mock", "sandbox": False, "seed": 1}

    assert c.post("/api/generate", json=body).status_code == 202
    r2 = c.post("/api/generate", json=body)
    assert r2.status_code == 429
    assert "error" in r2.get_json()


def test_concurrency_cap_returns_429(monkeypatch, tmp_path):
    from infinienv.gui import app as gui_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INFINIENV_GUI_PASSWORD", raising=False)
    monkeypatch.setenv("INFINIENV_GUI_MAX_CONCURRENT", "1")
    monkeypatch.setenv("INFINIENV_GUI_RATE_LIMIT", "1000")  # isolate the CONCURRENCY path
    app = gui_app.create_app()
    c = app.test_client()

    # Simulate one already-running job (Job.done defaults False -> counts as active).
    gui_app._jobs["fake-active"] = gui_app.Job()
    try:
        r = c.post("/api/generate", json={"prompt": "x", "provider": "mock", "sandbox": False, "seed": 1})
        assert r.status_code == 429
        assert "in progress" in r.get_json()["error"]
    finally:
        gui_app._jobs.pop("fake-active", None)
