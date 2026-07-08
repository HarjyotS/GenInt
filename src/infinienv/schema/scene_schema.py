"""SceneSpec: the typed contract between AI generation and the deterministic engine."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, model_validator

OBJECT_TYPES: set[str] = {
    "wall",
    "floor",
    "table",
    "can",
    "box",
    "key",
    "door",
    "package",
    "sink",
    "exit",
    "hazard",
    "distractor",
}

ACTION_TYPES: set[str] = {
    "move_up",
    "move_down",
    "move_left",
    "move_right",
    "pick_up",
    "drop",
    "unlock",
    "wait",
}

GOAL_TYPES: set[str] = {"reach", "pickup", "deliver", "unlock", "sequence"}


class SceneMetadata(BaseModel):
    name: str
    prompt: str = ""
    theme: str = "generic"


class Grid(BaseModel):
    width: int = Field(gt=0, le=256)
    height: int = Field(gt=0, le=256)
    tile_size: int = Field(default=32, gt=0)


class AgentSpec(BaseModel):
    id: str = "agent"
    x: int
    y: int
    inventory: list[str] = Field(default_factory=list)


class SceneObject(BaseModel):
    id: str
    type: str
    x: int
    y: int
    solid: bool = False
    portable: bool = False
    locked: bool = False
    key_id: str | None = None

    @model_validator(mode="after")
    def _check_type(self) -> "SceneObject":
        if self.type not in OBJECT_TYPES:
            raise ValueError(f"unsupported object type: {self.type!r}")
        return self


class WallCell(BaseModel):
    x: int
    y: int


class ReachGoal(BaseModel):
    id: str
    type: Literal["reach"] = "reach"
    target_id: str


class PickupGoal(BaseModel):
    id: str
    type: Literal["pickup"] = "pickup"
    object_id: str


class DeliverGoal(BaseModel):
    id: str
    type: Literal["deliver"] = "deliver"
    object_id: str
    target_id: str


class UnlockGoal(BaseModel):
    id: str
    type: Literal["unlock"] = "unlock"
    door_id: str


class SequenceGoal(BaseModel):
    id: str
    type: Literal["sequence"] = "sequence"
    subgoals: list["GoalUnion"]


GoalUnion = Annotated[
    Union[ReachGoal, PickupGoal, DeliverGoal, UnlockGoal, SequenceGoal],
    Field(discriminator="type"),
]
SequenceGoal.model_rebuild()

Goal = Union[ReachGoal, PickupGoal, DeliverGoal, UnlockGoal, SequenceGoal]


class SceneSpec(BaseModel):
    version: str = "0.1"
    seed: int = 0
    metadata: SceneMetadata
    grid: Grid
    agent: AgentSpec
    objects: list[SceneObject] = Field(default_factory=list)
    walls: list[WallCell] = Field(default_factory=list)
    goals: list[GoalUnion]

    def object_by_id(self, object_id: str) -> SceneObject | None:
        for obj in self.objects:
            if obj.id == object_id:
                return obj
        return None

    def all_ids(self) -> list[str]:
        ids = [self.agent.id]
        ids.extend(obj.id for obj in self.objects)
        return ids


def scene_spec_from_dict(data: dict) -> SceneSpec:
    """Parse and validate a raw dict into a SceneSpec, raising pydantic.ValidationError on failure."""
    return SceneSpec.model_validate(data)


def scene_spec_json_schema() -> dict:
    return SceneSpec.model_json_schema()
