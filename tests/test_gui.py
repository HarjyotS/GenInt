import json

import pytest

from infinienv.gui.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_page_lists_providers(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"mock" in res.data
    assert b"openai_agents" in res.data


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
