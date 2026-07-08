"""Static grid occupancy derived from a SceneSpec: bounds and solid-cell lookups."""

from __future__ import annotations

from infinienv.schema.scene_schema import SceneSpec

Coord = tuple[int, int]


class Grid:
    def __init__(self, scene: SceneSpec):
        self.width = scene.grid.width
        self.height = scene.grid.height
        self.tile_size = scene.grid.tile_size
        self._wall_cells: set[Coord] = {(w.x, w.y) for w in scene.walls}
        self._solid_cells: dict[Coord, str] = {}
        for obj in scene.objects:
            if obj.solid:
                self._solid_cells[(obj.x, obj.y)] = obj.id

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_wall(self, x: int, y: int) -> bool:
        return (x, y) in self._wall_cells

    def solid_object_at(self, x: int, y: int) -> str | None:
        return self._solid_cells.get((x, y))

    def is_blocked(self, x: int, y: int, *, unlocked_doors: frozenset[str] = frozenset()) -> bool:
        """True if a normal move cannot enter this cell (locked doors count as blocked)."""
        if not self.in_bounds(x, y):
            return True
        if self.is_wall(x, y):
            return True
        obj_id = self.solid_object_at(x, y)
        if obj_id is not None and obj_id not in unlocked_doors:
            return True
        return False

    def neighbors(self, x: int, y: int, *, unlocked_doors: frozenset[str] = frozenset()) -> list[Coord]:
        candidates = [(x, y + 1), (x, y - 1), (x - 1, y), (x + 1, y)]
        return [c for c in candidates if not self.is_blocked(c[0], c[1], unlocked_doors=unlocked_doors)]
