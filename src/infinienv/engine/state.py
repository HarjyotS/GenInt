"""Mutable runtime game state: agent position, inventory, object positions, door locks."""

from __future__ import annotations

from dataclasses import dataclass, field

from infinienv.schema.scene_schema import SceneObject, SceneSpec


@dataclass
class ObjectState:
    id: str
    type: str
    x: int
    y: int
    solid: bool
    portable: bool
    locked: bool
    key_id: str | None
    held: bool = False

    @classmethod
    def from_spec(cls, obj: SceneObject) -> "ObjectState":
        return cls(
            id=obj.id,
            type=obj.type,
            x=obj.x,
            y=obj.y,
            solid=obj.solid,
            portable=obj.portable,
            locked=obj.locked,
            key_id=obj.key_id,
        )


@dataclass
class GameState:
    agent_x: int
    agent_y: int
    inventory: list[str] = field(default_factory=list)
    objects: dict[str, ObjectState] = field(default_factory=dict)
    unlocked_doors: set[str] = field(default_factory=set)

    @classmethod
    def from_scene(cls, scene: SceneSpec) -> "GameState":
        objects = {obj.id: ObjectState.from_spec(obj) for obj in scene.objects}
        return cls(
            agent_x=scene.agent.x,
            agent_y=scene.agent.y,
            inventory=list(scene.agent.inventory),
            objects=objects,
        )

    def agent_pos(self) -> tuple[int, int]:
        return (self.agent_x, self.agent_y)

    def object_pos(self, object_id: str) -> tuple[int, int] | None:
        obj = self.objects.get(object_id)
        if obj is None or obj.held:
            return None
        return (obj.x, obj.y)

    def is_adjacent_or_same(self, x: int, y: int) -> bool:
        return abs(x - self.agent_x) + abs(y - self.agent_y) <= 1
