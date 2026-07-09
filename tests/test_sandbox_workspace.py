import asyncio
import json
import os
import tarfile

from infinienv.sandbox.workspace import (
    ARTIFACT_FILES,
    build_workspace_dir,
    extract_artifacts,
    outer_sanity_check,
    sync_full_workspace,
    tar_directory,
)


def test_build_workspace_dir_copies_engine_and_reference_runner(tmp_path):
    workspace = build_workspace_dir(str(tmp_path))
    assert os.path.isdir(os.path.join(workspace, "schema"))
    assert os.path.isdir(os.path.join(workspace, "engine"))
    assert os.path.isdir(os.path.join(workspace, "navigation"))
    assert os.path.isdir(os.path.join(workspace, "validation"))
    assert os.path.isdir(os.path.join(workspace, "render"))
    assert os.path.exists(os.path.join(workspace, "run_scene.py"))
    # nothing from the installed package's cli/llm/generation/assets/gui should leak in --
    # the sandbox gets exactly the engine surface it needs, not the whole installation.
    assert not os.path.exists(os.path.join(workspace, "cli.py"))
    assert not os.path.exists(os.path.join(workspace, "llm"))


def test_build_workspace_dir_excludes_pycache(tmp_path):
    workspace = build_workspace_dir(str(tmp_path))
    for root, dirs, _files in os.walk(workspace):
        assert "__pycache__" not in dirs


def test_tar_directory_round_trips_file_contents(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "marker.txt").write_text("hello sandbox")

    buf = tar_directory(str(src))
    with tarfile.open(fileobj=buf, mode="r") as tar:
        member = tar.extractfile("./marker.txt")
        assert member is not None
        assert member.read() == b"hello sandbox"


class _FakePersistHandle:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class _FakePersistSession:
    def __init__(self, tar_bytes: bytes):
        self._tar_bytes = tar_bytes

    async def persist_workspace(self):
        return _FakePersistHandle(self._tar_bytes)


def test_sync_full_workspace_replaces_stale_copy_with_agent_edits(tmp_path):
    # The pre-run workspace copy (what build_workspace_dir wrote).
    workspace_dir = tmp_path / "sandbox_workspace"
    workspace_dir.mkdir()
    (workspace_dir / "run_scene.py").write_text("# original template")
    stale_dir = workspace_dir / "stale_subdir"
    stale_dir.mkdir()
    (stale_dir / "old.txt").write_text("should be gone after sync")

    # What the sandbox agent actually produced by the end of the run.
    agent_tree = tmp_path / "agent_final_state"
    agent_tree.mkdir()
    (agent_tree / "run_scene.py").write_text("# agent-edited entrypoint")
    (agent_tree / "navigation").mkdir()
    (agent_tree / "navigation" / "chase.py").write_text("# new chase module")

    buf = tar_directory(str(agent_tree))
    session = _FakePersistSession(buf.read())

    asyncio.run(sync_full_workspace(session, str(workspace_dir)))

    assert (workspace_dir / "run_scene.py").read_text() == "# agent-edited entrypoint"
    assert (workspace_dir / "navigation" / "chase.py").exists()
    assert not (workspace_dir / "stale_subdir").exists()


class _FakeReadHandle:
    def __init__(self, data: bytes | str):
        self._data = data

    def read(self):
        return self._data


class _FakeSession:
    def __init__(self, files: dict[str, bytes | str]):
        self._files = files

    async def read(self, name: str):
        if name not in self._files:
            raise FileNotFoundError(name)
        return _FakeReadHandle(self._files[name])


def test_extract_artifacts_pulls_only_the_standard_files(tmp_path):
    files = {
        "scene.json": json.dumps({"a": 1}),
        "metrics.json": json.dumps({"success": True}),
        "replay.json": json.dumps({"actions": []}),
        "render.png": b"\x89PNG-fake-bytes",
        "replay.gif": b"GIF89a-fake-bytes",
        "not_a_real_artifact.py": "print('should not be extracted')",
    }
    session = _FakeSession(files)
    out_dir = str(tmp_path / "run")

    paths = asyncio.run(extract_artifacts(session, out_dir))

    assert set(paths.keys()) == set(ARTIFACT_FILES)
    assert not os.path.exists(os.path.join(out_dir, "not_a_real_artifact.py"))
    with open(os.path.join(out_dir, "scene.json")) as f:
        assert json.load(f) == {"a": 1}
    with open(os.path.join(out_dir, "render.png"), "rb") as f:
        assert f.read() == b"\x89PNG-fake-bytes"


def test_extract_artifacts_tolerates_missing_files(tmp_path):
    session = _FakeSession({"scene.json": "{}"})
    paths = asyncio.run(extract_artifacts(session, str(tmp_path / "run")))
    assert paths == {"scene.json": os.path.join(str(tmp_path / "run"), "scene.json")}


def _write_valid_scene(out_dir) -> dict:
    scene = {
        "version": "0.1",
        "seed": 1,
        "metadata": {"name": "t", "prompt": "p"},
        "grid": {"width": 4, "height": 4, "tile_size": 32},
        "agent": {"id": "agent", "x": 1, "y": 1},
        "objects": [],
        "walls": [],
        "goals": [{"id": "g", "type": "reach", "target_id": "agent"}],
    }
    (out_dir / "scene.json").write_text(json.dumps(scene))
    return scene


def _write_real_images(out_dir) -> None:
    from PIL import Image

    Image.new("RGB", (64, 64), (255, 0, 0)).save(out_dir / "render.png")
    frame1 = Image.new("RGB", (64, 64), (255, 0, 0))
    frame2 = Image.new("RGB", (64, 64), (0, 255, 0))
    frame1.save(out_dir / "replay.gif", save_all=True, append_images=[frame2], duration=100, loop=0)


def test_outer_sanity_check_passes_for_valid_scene_and_real_images(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)
    _write_real_images(out_dir)

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is True
    assert error is None


def test_outer_sanity_check_fails_for_malformed_scene(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    (out_dir / "scene.json").write_text(json.dumps({"not": "a valid scene"}))

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False
    assert error is not None


def test_outer_sanity_check_fails_when_scene_json_missing(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False
    assert "did not produce" in error


def test_outer_sanity_check_fails_for_truncated_replay_gif(tmp_path):
    # Regression test: a real sandbox run once self-reported success with a 43-byte,
    # effectively-empty replay.gif that it never verified itself. See notes.md.
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)
    from PIL import Image

    Image.new("RGB", (64, 64), (255, 0, 0)).save(out_dir / "render.png")
    (out_dir / "replay.gif").write_bytes(b"GIF89a")  # header only, no real frame data

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False
    assert "replay.gif" in error


def test_outer_sanity_check_fails_when_render_png_missing(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False
    assert "render.png" in error
