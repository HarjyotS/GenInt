"""Deterministic SceneSpec validation: the source of truth over any LLM output.

Runs schema parsing, geometry/collision checks, reachability, and full
solvability (via the deterministic planner) and returns a structured
ValidationResult. Never trusts model output.
"""

from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError

from infinienv.engine.grid import Grid
from infinienv.schema.scene_schema import ACTION_TYPES, OBJECT_TYPES, SceneSpec, scene_spec_from_dict
from infinienv.validation.errors import ValidationIssue, ValidationResult
from infinienv.validation.reachability import is_reachable
from infinienv.validation.solvability import check_solvability


def validate_scene_dict(data: dict) -> ValidationResult:
    """Validate a raw dict (e.g. straight from an LLM). Handles schema-parse failures cleanly."""
    try:
        scene = scene_spec_from_dict(data)
    except PydanticValidationError as exc:
        issues = [
            ValidationIssue(
                code="SCHEMA_ERROR",
                message=err["msg"],
                object_id=".".join(str(p) for p in err["loc"]) or None,
                severity="error",
            )
            for err in exc.errors()
        ]
        return ValidationResult(valid=False, errors=issues)
    return validate_scene(scene)


def _iter_goal_refs(goal) -> list[str]:
    kind = goal.type
    if kind == "reach":
        return [goal.target_id]
    if kind == "pickup":
        return [goal.object_id]
    if kind == "deliver":
        return [goal.object_id, goal.target_id]
    if kind == "unlock":
        return [goal.door_id]
    if kind == "interact":
        return [goal.target_id]
    if kind == "sequence":
        refs: list[str] = []
        for sub in goal.subgoals:
            refs.extend(_iter_goal_refs(sub))
        return refs
    return []


def _flatten_goals(goals) -> list:
    flat = []
    for g in goals:
        if g.type == "sequence":
            flat.extend(_flatten_goals(g.subgoals))
        else:
            flat.append(g)
    return flat


def validate_scene(scene: SceneSpec) -> ValidationResult:
    issues: list[ValidationIssue] = []

    # -- duplicate IDs --
    ids = scene.all_ids()
    seen: set[str] = set()
    for oid in ids:
        if oid in seen:
            issues.append(ValidationIssue("DUPLICATE_ID", f"Duplicate object id {oid!r}.", oid))
        seen.add(oid)

    # -- mechanics: custom object types and interactions must be internally consistent
    # before we trust anything that references them. See CLAUDE.md section 28.
    custom_type_ids = {t.id for t in scene.mechanics.custom_object_types}
    for t in scene.mechanics.custom_object_types:
        if t.id in OBJECT_TYPES:
            issues.append(
                ValidationIssue(
                    "MECHANICS_TYPE_COLLISION",
                    f"Custom object type {t.id!r} collides with a built-in type.",
                    t.id,
                )
            )
    known_types = OBJECT_TYPES | custom_type_ids

    interaction_ids: set[str] = set()
    for interaction in scene.mechanics.custom_interactions:
        if interaction.id in interaction_ids:
            issues.append(
                ValidationIssue("DUPLICATE_ID", f"Duplicate interaction id {interaction.id!r}.", interaction.id)
            )
        interaction_ids.add(interaction.id)
        if interaction.trigger_action in ACTION_TYPES:
            issues.append(
                ValidationIssue(
                    "MECHANICS_ACTION_COLLISION",
                    f"Interaction {interaction.id!r} trigger_action {interaction.trigger_action!r} "
                    "collides with a built-in action.",
                    interaction.id,
                )
            )
        if interaction.target_type not in known_types:
            issues.append(
                ValidationIssue(
                    "MECHANICS_UNKNOWN_TYPE",
                    f"Interaction {interaction.id!r} target_type {interaction.target_type!r} is not a "
                    "known or declared object type.",
                    interaction.id,
                )
            )
        if interaction.must_hold_type is not None and interaction.must_hold_type not in known_types:
            issues.append(
                ValidationIssue(
                    "MECHANICS_UNKNOWN_TYPE",
                    f"Interaction {interaction.id!r} must_hold_type {interaction.must_hold_type!r} is not "
                    "a known or declared object type.",
                    interaction.id,
                )
            )
        if not interaction.effects:
            issues.append(
                ValidationIssue(
                    "MECHANICS_NO_EFFECTS",
                    f"Interaction {interaction.id!r} has no effects; it would do nothing.",
                    interaction.id,
                )
            )

    for obj in scene.objects:
        if obj.type not in known_types:
            issues.append(
                ValidationIssue(
                    "UNSUPPORTED_OBJECT_TYPE",
                    f"Object {obj.id!r} has type {obj.type!r}, which is neither a built-in type nor "
                    "declared in mechanics.custom_object_types.",
                    obj.id,
                )
            )

    for goal in _flatten_goals(scene.goals):
        if goal.type == "interact" and goal.interaction_id not in interaction_ids:
            issues.append(
                ValidationIssue(
                    "MECHANICS_UNKNOWN_INTERACTION",
                    f"Goal {goal.id!r} references undeclared interaction {goal.interaction_id!r}.",
                    goal.interaction_id,
                )
            )

    # -- agent exists exactly once & is in bounds --
    grid_w, grid_h = scene.grid.width, scene.grid.height
    if not (0 <= scene.agent.x < grid_w and 0 <= scene.agent.y < grid_h):
        issues.append(
            ValidationIssue(
                "OUT_OF_BOUNDS",
                f"Agent spawn ({scene.agent.x}, {scene.agent.y}) is outside the {grid_w}x{grid_h} grid.",
                scene.agent.id,
            )
        )

    # -- object bounds & unsupported types (type already enforced by schema, checked defensively) --
    for obj in scene.objects:
        if not (0 <= obj.x < grid_w and 0 <= obj.y < grid_h):
            issues.append(
                ValidationIssue(
                    "OUT_OF_BOUNDS",
                    f"Object {obj.id!r} at ({obj.x}, {obj.y}) is outside the {grid_w}x{grid_h} grid.",
                    obj.id,
                )
            )

    for wall in scene.walls:
        if not (0 <= wall.x < grid_w and 0 <= wall.y < grid_h):
            issues.append(
                ValidationIssue(
                    "OUT_OF_BOUNDS",
                    f"Wall at ({wall.x}, {wall.y}) is outside the {grid_w}x{grid_h} grid.",
                )
            )

    # -- illegal overlaps: two solid things (solid objects, walls, agent) on one cell --
    occupied: dict[tuple[int, int], list[str]] = {}
    occupied.setdefault((scene.agent.x, scene.agent.y), []).append(scene.agent.id)
    for wall in scene.walls:
        occupied.setdefault((wall.x, wall.y), []).append("__wall__")
    for obj in scene.objects:
        if obj.solid:
            occupied.setdefault((obj.x, obj.y), []).append(obj.id)
    for pos, occupants in occupied.items():
        if len(occupants) > 1:
            issues.append(
                ValidationIssue(
                    "ILLEGAL_OVERLAP",
                    f"Cell {pos} has overlapping solid occupants: {occupants}.",
                    occupants[0],
                )
            )

    # -- goal reference integrity --
    known_ids = set(ids)
    flat_goals = _flatten_goals(scene.goals)
    if not flat_goals:
        issues.append(ValidationIssue("NO_GOALS", "Scene has no goals."))
    for goal in scene.goals:
        for ref in _iter_goal_refs(goal):
            if ref not in known_ids:
                issues.append(
                    ValidationIssue(
                        "MISSING_GOAL_OBJECT",
                        f"Goal {goal.id!r} references unknown object/target {ref!r}.",
                        ref,
                    )
                )

    ids_seen_dup_free = len(issues) == 0
    if not ids_seen_dup_free:
        # geometry is broken enough that reachability/solvability would be meaningless/crash-prone
        return ValidationResult(valid=False, errors=issues)

    # -- reachability: every referenced object must be reachable from spawn.
    # Doors are treated as optimistically unlocked here: this is a cheap "is it walled off
    # entirely" pre-check, not a full key/lock/order simulation (that's solvability, below).
    grid = Grid(scene)
    start = (scene.agent.x, scene.agent.y)
    door_ids = frozenset(o.id for o in scene.objects if o.type == "door")
    for goal in flat_goals:
        for ref in _iter_goal_refs(goal):
            if ref == scene.agent.id:
                continue
            obj = scene.object_by_id(ref)
            if obj is None:
                continue
            if not is_reachable(grid, start, (obj.x, obj.y), unlocked_doors=door_ids):
                issues.append(
                    ValidationIssue(
                        "UNREACHABLE_OBJECT",
                        f"Object {ref!r} cannot be reached from the agent spawn.",
                        ref,
                    )
                )

    if issues:
        return ValidationResult(valid=False, errors=issues)

    # -- solvability: the deterministic planner must be able to complete every goal --
    result = check_solvability(scene)
    if not result.success:
        issues.append(
            ValidationIssue(
                "UNSOLVABLE",
                f"Deterministic planner could not complete the scene's goals: {result.error}",
            )
        )
        return ValidationResult(valid=False, errors=issues)

    return ValidationResult(valid=True, errors=[])
