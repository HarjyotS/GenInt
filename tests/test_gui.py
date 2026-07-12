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
    assert _fake_run_sandbox_generation.last_assets_mode == "none"

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
