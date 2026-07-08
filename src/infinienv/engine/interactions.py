"""Interprets custom interactions and their declarative effects.

This is the safe alternative to letting the model author real behavior as code:
a CustomInteraction is a fixed precondition (adjacency, optionally holding an
object of a given type) plus an ordered list of effects, each one of a small,
fixed vocabulary (EFFECT_OP_VALUES) implemented below. Nothing here ever calls
eval/exec or imports anything the model named -- see CLAUDE.md section 28.
"""

from __future__ import annotations

from infinienv.engine.actions import ActionError
from infinienv.engine.state import GameState
from infinienv.schema.scene_schema import CustomInteraction, InteractionEffect, SceneSpec


def find_matching_interaction(scene: SceneSpec, trigger_action: str, target_type: str) -> CustomInteraction | None:
    for interaction in scene.mechanics.custom_interactions:
        if interaction.trigger_action == trigger_action and interaction.target_type == target_type:
            return interaction
    return None


def _resolve_object_id(ref: str, *, target_id: str, held_id: str | None) -> str | None:
    if ref == "target":
        return target_id
    if ref == "held":
        return held_id
    return ref  # an explicit object id


def _apply_effect(effect: InteractionEffect, state: GameState, *, target_id: str, held_id: str | None) -> None:
    if effect.op == "remove_held_object":
        if held_id is None:
            raise ActionError("remove_held_object effect requires a held object")
        if held_id in state.inventory:
            state.inventory.remove(held_id)
        state.objects.pop(held_id, None)
        return

    if effect.op == "drop_held_object_at_target":
        if held_id is None:
            raise ActionError("drop_held_object_at_target effect requires a held object")
        target = state.objects[target_id]
        held = state.objects[held_id]
        held.held = False
        held.x, held.y = target.x, target.y
        if held_id in state.inventory:
            state.inventory.remove(held_id)
        return

    if effect.op == "remove_object":
        obj_id = _resolve_object_id(effect.target, target_id=target_id, held_id=held_id)
        if obj_id is None:
            return
        if obj_id in state.inventory:
            state.inventory.remove(obj_id)
        state.objects.pop(obj_id, None)
        return

    if effect.op == "unlock_target":
        target = state.objects[target_id]
        target.locked = False
        state.unlocked_doors.add(target_id)
        return

    if effect.op == "set_object_property":
        obj_id = _resolve_object_id(effect.target, target_id=target_id, held_id=held_id)
        obj = state.objects.get(obj_id) if obj_id else None
        if obj is not None and effect.property_name is not None:
            obj.properties[effect.property_name] = effect.property_value
        return

    if effect.op == "teleport_agent":
        if effect.x is None or effect.y is None:
            raise ActionError("teleport_agent effect requires x and y")
        state.agent_x, state.agent_y = effect.x, effect.y
        return

    raise ActionError(f"unsupported effect op {effect.op!r}")


def apply_custom_interaction(state: GameState, scene: SceneSpec, action: dict) -> GameState:
    """Apply a `{"action": <trigger_action>, "target_id": ...}` custom interaction."""
    target_id = action.get("target_id")
    if not target_id:
        raise ActionError("custom interaction action requires target_id")
    target = state.objects.get(target_id)
    if target is None:
        raise ActionError(f"unknown interaction target {target_id!r}")

    interaction = find_matching_interaction(scene, action["action"], target.type)
    if interaction is None:
        raise ActionError(f"no interaction {action['action']!r} defined for target type {target.type!r}")

    if not state.is_adjacent_or_same(target.x, target.y):
        raise ActionError(f"agent is not adjacent to {target_id!r}")

    held_id = None
    if interaction.must_hold_type:
        held_id = next(
            (oid for oid in state.inventory if oid in state.objects and state.objects[oid].type == interaction.must_hold_type),
            None,
        )
        if held_id is None:
            raise ActionError(f"interaction {interaction.id!r} requires holding a {interaction.must_hold_type!r}")

    for effect in interaction.effects:
        _apply_effect(effect, state, target_id=target_id, held_id=held_id)

    state.completed_interactions.add((interaction.id, target_id))
    return state
