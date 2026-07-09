"""Replay rendering, including smooth interpolation of multi-cell slides."""

from PIL import Image

from infinienv.navigation.policy import solve_scene
from infinienv.render.replay_export import _slide_cells, build_replay_frames, save_replay_gif
from infinienv.schema.scene_schema import scene_spec_from_dict


def test_slide_cells_returns_intermediate_cells_along_an_axis():
    assert _slide_cells((2, 3), (6, 3)) == [(3, 3), (4, 3), (5, 3)]
    assert _slide_cells((3, 6), (3, 2)) == [(3, 5), (3, 4), (3, 3)]
    assert _slide_cells((2, 3), (3, 3)) == []  # one cell -> no interpolation
    assert _slide_cells((2, 3), (2, 3)) == []  # no move


def _slide_scene():
    cells = set()
    for i in range(8):
        cells.add((0, i))
        cells.add((9, i))
    for i in range(10):
        cells.add((i, 0))
        cells.add((i, 7))
    return scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "slide", "prompt": "p"},
            "grid": {"width": 10, "height": 8, "tile_size": 32},
            "agent": {"id": "agent", "x": 1, "y": 3},
            "objects": [
                {"id": "puck", "type": "box", "x": 3, "y": 3, "solid": True, "pushable": True, "slippery": True},
                {"id": "goal", "type": "exit", "x": 8, "y": 3, "solid": False},
            ],
            "walls": [{"x": x, "y": y} for x, y in sorted(cells)],
            "goals": [{"id": "g", "type": "push", "object_id": "puck", "target_id": "goal"}],
        }
    )


def test_slippery_slide_inserts_interpolation_frames():
    scene = _slide_scene()
    result = solve_scene(scene)
    assert result.success, result.error
    frames = build_replay_frames(scene, result.actions)
    # a plain per-action replay would be len(actions)+1 frames; the multi-cell slide adds more.
    assert len(frames) > len(result.actions) + 1
    assert all(isinstance(f, Image.Image) for f in frames)


def test_save_replay_gif_writes_a_multi_frame_animation(tmp_path):
    scene = _slide_scene()
    result = solve_scene(scene)
    out = tmp_path / "replay.gif"
    save_replay_gif(scene, result.actions, str(out))
    with Image.open(out) as gif:
        assert gif.n_frames > 2
        for i in range(gif.n_frames):  # every frame's pixel data must actually decode
            gif.seek(i)
            gif.load()
