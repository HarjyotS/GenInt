"""SceneSpec: the typed contract between AI generation and the deterministic engine."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

OBJECT_TYPE_VALUES: tuple[str, ...] = (
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
)
OBJECT_TYPES: set[str] = set(OBJECT_TYPE_VALUES)

# A Literal (not a plain `str` + runtime check) so the JSON schema itself carries an
# enum constraint -- under structured-output / strict mode, this stops the model from
# ever sampling an unsupported type (e.g. "desk", "sofa") instead of only rejecting it
# after the fact once validation/parsing has already failed.
ObjectType = Literal[OBJECT_TYPE_VALUES]

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

GOAL_TYPES: set[str] = {"reach", "pickup", "deliver", "unlock", "sequence", "interact", "push"}

# Fixed, safe vocabulary of effect primitives a custom interaction can compose.
# Deliberately NOT arbitrary code: every op is interpreted by a small fixed
# dispatcher in engine/interactions.py, never eval/exec'd. See CLAUDE.md section 28.
EFFECT_OP_VALUES: tuple[str, ...] = (
    "remove_held_object",
    "drop_held_object_at_target",
    "remove_object",
    "unlock_target",
    "set_object_property",
    "teleport_agent",
)
EffectOp = Literal[EFFECT_OP_VALUES]


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
    # Free string, not the ObjectType Literal: a custom (LLM/user-declared) type is
    # allowed here, but only if it's declared in scene.mechanics.custom_object_types --
    # that cross-field check happens in validation/validator.py, not at parse time,
    # since pydantic field validators can't see sibling fields on the parent model.
    type: str
    x: int
    y: int
    solid: bool = False
    portable: bool = False
    locked: bool = False
    key_id: str | None = None
    # Deterministic grid-physics flags (see engine/physics.py). `pushable`: the agent can
    # shove this object one cell by moving into it (Sokoban-style), instead of being blocked
    # by it. `slippery`: a pushable object that, once shoved, keeps sliding in the push
    # direction until the next cell is blocked (ice-puck momentum). Both stay integer-grid and
    # fully simulable, so the validator's solvability guarantee still holds -- unlike the
    # continuous physics that only --sandbox mode allows.
    pushable: bool = False
    slippery: bool = False
    properties: dict[str, bool | str | int] = Field(default_factory=dict)


class WallCell(BaseModel):
    x: int
    y: int


class CustomObjectType(BaseModel):
    """Declares a new object type beyond OBJECT_TYPE_VALUES, e.g. "window"."""

    id: str
    description: str = ""


class InteractionEffect(BaseModel):
    """One declarative effect. `target` is "target" (the interaction's target object),
    "held" (the object the actor is holding, if `must_hold_type` matched one), or an
    explicit object id. Interpreted by engine/interactions.py -- never executed code."""

    op: EffectOp
    target: str = "target"
    property_name: str | None = None
    property_value: bool | str | int | None = None
    x: int | None = None
    y: int | None = None


class CustomInteraction(BaseModel):
    """Declares a new verb (e.g. "throw") usable against objects of `target_type`,
    with a fixed, ordered list of effects. This is how "a window you can throw
    things out of" becomes real, checkable behavior instead of just flavor text."""

    id: str
    trigger_action: str
    target_type: str
    must_hold_type: str | None = None
    effects: list[InteractionEffect] = Field(default_factory=list)
    description: str = ""


class Mechanics(BaseModel):
    custom_object_types: list[CustomObjectType] = Field(default_factory=list)
    custom_interactions: list[CustomInteraction] = Field(default_factory=list)


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


class InteractGoal(BaseModel):
    """Completed once `interaction_id` has been performed against `target_id` --
    e.g. "throw the vase through window_1" (see CustomInteraction)."""

    id: str
    type: Literal["interact"] = "interact"
    interaction_id: str
    target_id: str


class PushGoal(BaseModel):
    """Completed once the pushable `object_id` has been shoved onto `target_id`'s cell --
    e.g. "push the crate onto the pressure plate". Distinct from `deliver`: the agent shoves
    the object across the floor rather than carrying it, so it works for heavy/non-portable
    objects, and for slippery ones the object slides until it stops (see engine/physics.py)."""

    id: str
    type: Literal["push"] = "push"
    object_id: str
    target_id: str


class SequenceGoal(BaseModel):
    id: str
    type: Literal["sequence"] = "sequence"
    subgoals: list["GoalUnion"]


GoalUnion = Annotated[
    Union[ReachGoal, PickupGoal, DeliverGoal, UnlockGoal, InteractGoal, PushGoal, SequenceGoal],
    Field(discriminator="type"),
]
SequenceGoal.model_rebuild()

Goal = Union[ReachGoal, PickupGoal, DeliverGoal, UnlockGoal, InteractGoal, PushGoal, SequenceGoal]


class SceneSpec(BaseModel):
    version: str = "0.1"
    seed: int = 0
    metadata: SceneMetadata
    grid: Grid
    agent: AgentSpec
    objects: list[SceneObject] = Field(default_factory=list)
    walls: list[WallCell] = Field(default_factory=list)
    goals: list[GoalUnion]
    mechanics: Mechanics = Field(default_factory=Mechanics)

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
