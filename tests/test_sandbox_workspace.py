import asyncio
import json
import os
import tarfile

from infinienv.sandbox.workspace import (
    ARTIFACT_FILES,
    _positions_from_replay,
    _teleport_frame,
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
    assert os.path.isdir(os.path.join(workspace, "assets"))
    assert os.path.exists(os.path.join(workspace, "run_scene.py"))
    assert os.path.exists(os.path.join(workspace, "ASSETS_MODE"))
    # the generic, reusable sandbox-facing primitives (action registry, motion patterns,
    # animation helpers) are plain files under engine/, so the existing shutil.copytree("engine")
    # picks them up automatically -- assert they actually land in a built workspace.
    assert os.path.exists(os.path.join(workspace, "engine", "action_registry.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "motion_patterns.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "animation.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "platformer_physics.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "grid_collision.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "level_generation.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "puzzle_state.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "pushables.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "pathfinding.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "vision.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "agent_behavior.py"))
    assert os.path.exists(os.path.join(workspace, "engine", "rendering.py"))
    # nothing from the installed package's cli/generation/gui should leak in, and only the
    # one file assets/*.py actually needs (ProviderError) is copied from llm/, not the
    # whole package (providers, prompts, heavy optional deps).
    assert not os.path.exists(os.path.join(workspace, "cli.py"))
    assert not os.path.exists(os.path.join(workspace, "generation"))
    assert os.path.exists(os.path.join(workspace, "llm", "base.py"))
    assert not os.path.exists(os.path.join(workspace, "llm", "providers"))


def test_build_workspace_dir_default_assets_mode_is_none(tmp_path):
    workspace = build_workspace_dir(str(tmp_path))
    with open(os.path.join(workspace, "ASSETS_MODE")) as f:
        assert f.read().strip() == "none"


def test_build_workspace_dir_writes_requested_assets_mode(tmp_path):
    workspace = build_workspace_dir(str(tmp_path), assets_mode="generated")
    with open(os.path.join(workspace, "ASSETS_MODE")) as f:
        assert f.read().strip() == "generated"


def test_build_workspace_dir_rewrites_internal_infinienv_imports(tmp_path):
    # Regression test for a real bug: infinienv is installed editable, so a copied module's
    # `from infinienv.engine.grid import Grid`-style import would otherwise silently resolve
    # to the *real* installed package instead of the sandboxed copy sitting next to it.
    import re

    import_line = re.compile(r"^\s*(from|import)\s+infinienv\.", re.MULTILINE)
    workspace = build_workspace_dir(str(tmp_path))
    for root, _dirs, files in os.walk(workspace):
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name)) as f:
                content = f.read()
            assert not import_line.search(content), f"{name} still imports from the real installed package"


def test_build_workspace_dir_copy_is_actually_self_contained(tmp_path):
    # The real regression check: run a copied module's code from a subprocess whose cwd is
    # the workspace (matching how the sandbox actually executes it) and confirm the classes
    # it uses really are the sandboxed copies, not the real installed package.
    import subprocess
    import sys

    workspace = build_workspace_dir(str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", "from engine.grid import Grid; import engine.grid as m; print(m.__file__)"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == os.path.join(workspace, "engine", "grid.py")


def test_reference_run_scene_records_asset_notes_in_metrics(tmp_path):
    # Regression test: resolve_assets() returns (entries, notes), and the reference run_scene.py
    # template used to discard `notes` entirely -- a sprite that silently failed to generate had
    # no trace of why anywhere. ASSETS_MODE=none exercises the template's default asset_notes=[]
    # path without needing network access; the field's presence is what regressed, not its
    # contents for any particular mode.
    import subprocess
    import sys

    workspace = build_workspace_dir(str(tmp_path), assets_mode="none")
    scene = {
        "version": "0.1",
        "seed": 1,
        "metadata": {"name": "t", "prompt": "p"},
        "grid": {"width": 4, "height": 4, "tile_size": 32},
        "agent": {"id": "agent", "x": 0, "y": 0},
        "objects": [{"id": "spot", "type": "exit", "x": 1, "y": 0}],
        "walls": [],
        "goals": [{"id": "g", "type": "reach", "target_id": "spot"}],
    }
    with open(os.path.join(workspace, "scene.json"), "w") as f:
        json.dump(scene, f)

    result = subprocess.run(
        [sys.executable, "run_scene.py"], cwd=workspace, capture_output=True, text=True
    )
    assert result.returncode == 0, (result.stdout, result.stderr)

    with open(os.path.join(workspace, "metrics.json")) as f:
        metrics = json.load(f)
    assert metrics["asset_notes"] == []


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


def _smooth_trace(steps=20):
    # a hero walking right ~3px/frame then a small jump arc -- all steps bounded, no teleport
    frames = []
    x, y, vy = 0.0, 100.0, 0.0
    for i in range(steps):
        x += 3.0
        if i == 8:
            vy = -9.0
        vy += 1.5
        y = min(100.0, y + vy)
        frames.append({"hero": {"x": x, "y": y}})
    return {"trace": frames}


def test_positions_from_replay_extracts_nested_hero_series():
    positions = _positions_from_replay(_smooth_trace(12))
    assert positions is not None
    assert len(positions) == 12
    assert positions[0] == (3.0, positions[0][1])


def test_positions_from_replay_handles_top_level_and_pos_shapes():
    assert _positions_from_replay({"frames": [{"x": i, "y": 0} for i in range(10)]}) is not None
    assert _positions_from_replay({"states": [{"pos": [i, 2 * i]} for i in range(10)]}) is not None


def test_positions_from_replay_returns_none_for_unknown_shape():
    assert _positions_from_replay({"something_else": 1}) is None
    assert _positions_from_replay({"trace": [{"score": 5}, {"score": 6}]}) is None


def test_teleport_frame_none_for_smooth_motion():
    positions = _positions_from_replay(_smooth_trace(20))
    assert _teleport_frame(positions) is None


def test_teleport_frame_flags_an_egregious_jump():
    # smooth ~3px steps, then one 90px snap (a `pos = target` teleport)
    positions = [(float(i * 3), 0.0) for i in range(15)]
    positions.append((positions[-1][0] + 90.0, 0.0))
    positions += [(positions[-1][0] + 3.0 * i, 0.0) for i in range(1, 6)]
    tp = _teleport_frame(positions)
    assert tp is not None
    frame_i, jump, p90 = tp
    assert jump > 80
    assert p90 < 10


def test_outer_sanity_check_fails_for_a_teleporting_replay(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)
    _write_real_images(out_dir)
    frames = [{"hero": {"x": float(i * 3), "y": 0.0}} for i in range(15)]
    frames.append({"hero": {"x": frames[-1]["hero"]["x"] + 120.0, "y": 0.0}})  # teleport
    frames += [{"hero": {"x": frames[-1]["hero"]["x"] + 3.0 * i, "y": 0.0}} for i in range(1, 6)]
    (out_dir / "replay.json").write_text(json.dumps({"trace": frames}))

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False
    assert "teleport" in error


def test_outer_sanity_check_passes_for_a_smooth_replay(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)
    _write_real_images(out_dir)
    (out_dir / "replay.json").write_text(json.dumps(_smooth_trace(30)))

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is True
    assert error is None


def test_outer_sanity_check_skips_motion_floor_for_unparseable_replay(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)
    _write_real_images(out_dir)
    (out_dir / "replay.json").write_text(json.dumps({"rules": ["x"], "no_positions": True}))

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is True  # unknown shape -> motion floor skipped, not failed
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


# --- the deterministic validator runs on the sandbox scene (geometry enforced, rest recorded) ---


def test_outer_sanity_check_enforces_out_of_bounds(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    scene = _write_valid_scene(out_dir)
    scene["agent"]["x"] = 99  # outside the 4x4 grid
    (out_dir / "scene.json").write_text(json.dumps(scene))
    _write_real_images(out_dir)

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False and "OUT_OF_BOUNDS" in error


def test_outer_sanity_check_enforces_duplicate_id(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    scene = _write_valid_scene(out_dir)
    scene["objects"] = [{"id": "agent", "type": "box", "x": 2, "y": 2}]  # id collides with the agent
    (out_dir / "scene.json").write_text(json.dumps(scene))
    _write_real_images(out_dir)

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False and "DUPLICATE_ID" in error


def test_outer_sanity_check_does_not_enforce_vocabulary_specific_errors(tmp_path):
    # A scene with an undeclared custom object type is invalid to the deterministic validator
    # (UNSUPPORTED_OBJECT_TYPE) but legitimate for the sandbox (its code handles the type). The outer
    # check must NOT fail it -- geometry is fine -- it's recorded, not enforced.
    from infinienv.sandbox.workspace import deterministic_validation_summary

    out_dir = tmp_path / "run"
    out_dir.mkdir()
    scene = _write_valid_scene(out_dir)
    scene["objects"] = [{"id": "w1", "type": "widget", "x": 2, "y": 2}]  # 'widget' is not a built-in type
    (out_dir / "scene.json").write_text(json.dumps(scene))
    _write_real_images(out_dir)

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is True and error is None  # geometry is fine; vocabulary error not enforced

    summary = deterministic_validation_summary(str(out_dir))
    assert summary["ran"] is True and summary["valid"] is False
    assert "UNSUPPORTED_OBJECT_TYPE" in summary["errors"]  # recorded for transparency
    assert summary["enforced_codes"] == ["DUPLICATE_ID", "OUT_OF_BOUNDS"]


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


def test_outer_sanity_check_fails_for_single_frame_replay_gif(tmp_path):
    # Regression test: a real sandbox run once self-reported success with a replay.gif that
    # was a technically-valid, correctly-sized image -- but only one static frame, showing
    # nothing happening. See notes.md.
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)
    from PIL import Image

    Image.new("RGB", (64, 64), (255, 0, 0)).save(out_dir / "render.png")
    Image.new("RGB", (64, 64), (255, 0, 0)).save(out_dir / "replay.gif")  # single frame, no animation

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False
    assert "replay.gif" in error
    assert "frame" in error


def _make_lzw_corrupted_gif() -> bytes:
    """Build a 2-frame animated GIF, then XOR every image sub-block's *payload* bytes (never
    the length-prefix bytes, the block terminator, or any header) so the container structure --
    and therefore `Image.verify()` and `n_frames` -- stays fully intact, but the LZW-compressed
    pixel data is garbage. Reproduces a real bug: PIL's `verify()` validates GIF container
    structure, not that pixel data actually decodes.
    """
    import io

    from PIL import Image

    buf = io.BytesIO()
    frame1 = Image.new("RGB", (16, 16), (255, 0, 0))
    frame2 = Image.new("RGB", (16, 16), (0, 255, 0))
    frame1.save(buf, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    data = bytearray(buf.getvalue())

    pos = 6  # skip "GIF87a"/"GIF89a"
    lsd_packed = data[pos + 4]
    pos += 7  # logical screen descriptor
    if lsd_packed & 0x80:
        pos += 3 * (2 ** ((lsd_packed & 0x07) + 1))  # global color table

    while pos < len(data):
        b = data[pos]
        if b == 0x21:  # extension block: introducer + label, then length-prefixed sub-blocks
            pos += 2
            while data[pos] != 0x00:
                pos += 1 + data[pos]
            pos += 1
        elif b == 0x2C:  # image descriptor: introducer(1) + left/top/w/h(8) + packed(1)
            img_packed = data[pos + 9]
            pos += 10
            if img_packed & 0x80:
                pos += 3 * (2 ** ((img_packed & 0x07) + 1))  # local color table
            pos += 1  # LZW minimum code size byte
            while data[pos] != 0x00:  # length-prefixed image data sub-blocks
                block_len = data[pos]
                start = pos + 1
                for j in range(start, start + block_len):
                    data[j] ^= 0xFF  # corrupt payload only, never the length byte
                pos = start + block_len
            pos += 1  # block terminator
        else:  # trailer (0x3B) or anything else: stop walking, structure beyond isn't ours
            break

    return bytes(data)


def test_outer_sanity_check_fails_for_lzw_corrupted_replay_gif(tmp_path):
    # Regression test for a real bug found from a user report ("gui_1783609484 run failed
    # replay"): a sandbox run self-reported success with a replay.gif that had a correct
    # header/trailer and 59 well-formed frame descriptors -- passing both `Image.verify()` and
    # the frame-count check -- but malformed LZW-compressed pixel data in every frame.
    # `Image.verify()` only validates GIF container structure, not that pixel data actually
    # decodes, so this slipped through until outer_sanity_check started forcing a real
    # frame-by-frame `.load()`. See notes.md.
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    _write_valid_scene(out_dir)
    from PIL import Image

    Image.new("RGB", (64, 64), (255, 0, 0)).save(out_dir / "render.png")
    (out_dir / "replay.gif").write_bytes(_make_lzw_corrupted_gif())

    from PIL import Image as PILImage

    with PILImage.open(out_dir / "replay.gif") as sanity:
        assert sanity.n_frames == 2  # structurally still a 2-frame animation
        sanity.verify()  # and verify() still passes -- the corruption is only in pixel data

    ok, error = outer_sanity_check(str(out_dir))
    assert ok is False
    assert "replay.gif" in error
    assert "corrupted" in error.lower() or "decod" in error.lower()
