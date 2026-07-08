from infinienv.engine.grid import Grid
from infinienv.schema.scene_schema import scene_spec_from_dict
from infinienv.validation.reachability import is_reachable


def _scene(walls):
    return scene_spec_from_dict(
        {
            "version": "0.1",
            "seed": 1,
            "metadata": {"name": "t", "prompt": "p"},
            "grid": {"width": 6, "height": 6, "tile_size": 32},
            "agent": {"id": "agent", "x": 0, "y": 0},
            "objects": [{"id": "target", "type": "exit", "x": 4, "y": 4}],
            "walls": walls,
            "goals": [{"id": "reach", "type": "reach", "target_id": "target"}],
        }
    )


def test_reachable_target_passes():
    scene = _scene([])
    grid = Grid(scene)
    assert is_reachable(grid, (0, 0), (4, 4))


def test_sealed_target_fails():
    # Wall off the entire target cell's neighborhood.
    walls = [{"x": 3, "y": y} for y in range(6)]
    scene = _scene(walls)
    grid = Grid(scene)
    assert not is_reachable(grid, (0, 0), (4, 4))
