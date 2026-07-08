"""Mutation engine: given one valid scene, generate many valid variants.

This is the highest-leverage "creativity" feature per CLAUDE.md section 16.B --
it demonstrates infinite environment generation from a single seed scene, not
just a one-off text-to-grid demo. Every mutation is re-validated (including a
full solvability run) before being kept; invalid candidates are discarded.
"""

from __future__ import annotations

import json
import random

from pydantic import ValidationError as PydanticValidationError

from infinienv.artifacts.writer import resolve_out_dir, write_json
from infinienv.llm.base import ProviderError
from infinienv.schema.scene_schema import SceneObject, SceneSpec, scene_spec_from_dict
from infinienv.validation.validator import validate_scene

MAX_ATTEMPTS_PER_MUTATION = 8

THEME_ROTATION = ("kitchen", "warehouse", "office", "convenience_store", "obstacle_course")


def _occupied_cells(scene: SceneSpec) -> set[tuple[int, int]]:
    cells = {(scene.agent.x, scene.agent.y)}
    cells.update((w.x, w.y) for w in scene.walls)
    cells.update((o.x, o.y) for o in scene.objects)
    return cells


def _free_cell(rng: random.Random, scene: SceneSpec, taken: set[tuple[int, int]]) -> tuple[int, int] | None:
    candidates = [
        (x, y)
        for x in range(1, scene.grid.width - 1)
        for y in range(1, scene.grid.height - 1)
        if (x, y) not in taken
    ]
    if not candidates:
        return None
    return rng.choice(candidates)


def mutate_reposition_objects(scene: SceneSpec, rng: random.Random) -> SceneSpec:
    """Same goal, different layout: move every non-solid, non-wall object to a fresh cell."""
    mutant = scene.model_copy(deep=True)
    taken = {(w.x, w.y) for w in mutant.walls}
    taken.add((mutant.agent.x, mutant.agent.y))
    for obj in mutant.objects:
        if obj.solid and obj.type not in ("table", "box", "door"):
            taken.add((obj.x, obj.y))
            continue
        cell = _free_cell(rng, mutant, taken)
        if cell is None:
            continue
        obj.x, obj.y = cell
        taken.add(cell)
    return mutant


def mutate_add_obstacle(scene: SceneSpec, rng: random.Random) -> SceneSpec:
    """Same task, extra obstacles: drop 1-2 solid boxes into open floor."""
    mutant = scene.model_copy(deep=True)
    taken = _occupied_cells(mutant)
    next_idx = sum(1 for o in mutant.objects if o.id.startswith("mut_box_"))
    for _ in range(rng.choice([1, 2])):
        cell = _free_cell(rng, mutant, taken)
        if cell is None:
            break
        obj_id = f"mut_box_{next_idx}"
        next_idx += 1
        mutant.objects.append(SceneObject(id=obj_id, type="box", x=cell[0], y=cell[1], solid=True))
        taken.add(cell)
    return mutant


def mutate_add_distractor(scene: SceneSpec, rng: random.Random) -> SceneSpec:
    """Same task, decoy objects: add a portable distractor irrelevant to any goal."""
    mutant = scene.model_copy(deep=True)
    taken = _occupied_cells(mutant)
    cell = _free_cell(rng, mutant, taken)
    if cell is None:
        return mutant
    next_idx = sum(1 for o in mutant.objects if o.id.startswith("mut_distractor_"))
    mutant.objects.append(
        SceneObject(id=f"mut_distractor_{next_idx}", type="distractor", x=cell[0], y=cell[1], portable=True)
    )
    return mutant


def mutate_reverse_start(scene: SceneSpec, rng: random.Random) -> SceneSpec:
    """Same task, reversed start: mirror the agent spawn across the grid."""
    mutant = scene.model_copy(deep=True)
    mirrored = (mutant.grid.width - 1 - mutant.agent.x, mutant.grid.height - 1 - mutant.agent.y)
    taken = _occupied_cells(mutant) - {(mutant.agent.x, mutant.agent.y)}
    if mirrored in taken or mirrored[0] <= 0 or mirrored[1] <= 0:
        cell = _free_cell(rng, mutant, taken)
        if cell is None:
            return mutant
        mirrored = cell
    mutant.agent.x, mutant.agent.y = mirrored
    return mutant


def mutate_theme_reskin(scene: SceneSpec, rng: random.Random) -> SceneSpec:
    """Same symbolic task, different theme (PATHWAY.md section 11): swaps `metadata.theme`.

    Object *types* stay within the fixed, validator-enforced vocabulary (kitchen/warehouse/
    office/etc. don't get distinct object sets in this schema by design -- see notes.md), so
    this is a metadata-level reskin, not a full art/vocabulary swap. Always valid: it only
    touches labels, never geometry or goals.
    """
    mutant = scene.model_copy(deep=True)
    choices = [t for t in THEME_ROTATION if t != mutant.metadata.theme] or list(THEME_ROTATION)
    mutant.metadata.theme = rng.choice(choices)
    return mutant


STRATEGIES = {
    "reposition": mutate_reposition_objects,
    "add_obstacle": mutate_add_obstacle,
    "add_distractor": mutate_add_distractor,
    "reverse_start": mutate_reverse_start,
    "theme_reskin": mutate_theme_reskin,
}


def _try_llm_mutation(scene: SceneSpec, provider, seed: int) -> SceneSpec | None:
    propose = getattr(provider, "propose_mutation", None)
    if propose is None:
        return None
    try:
        return propose(scene, seed)
    except (ProviderError, PydanticValidationError):
        return None  # treat like any other rejected candidate; caller just retries


def generate_mutations(scene: SceneSpec, count: int, seed: int, *, provider=None, llm_fraction: float = 0.0) -> list[SceneSpec]:
    """Return up to `count` distinct, validated mutations of `scene`.

    If `provider` implements `propose_mutation` (currently only OpenAIAgentsProvider),
    `llm_fraction` of attempts ask it for a creative variant instead of using a
    deterministic strategy; every candidate -- LLM-proposed or deterministic -- goes
    through the same full validate_scene() (schema + geometry + solvability) before
    being kept, per CLAUDE.md section 16.B.
    """
    rng = random.Random(seed)
    strategy_names = list(STRATEGIES.keys())
    results: list[SceneSpec] = []
    idx = 0
    tries = 0
    max_total_tries = count * MAX_ATTEMPTS_PER_MUTATION
    while len(results) < count and tries < max_total_tries:
        tries += 1
        use_llm = provider is not None and llm_fraction > 0 and rng.random() < llm_fraction
        if use_llm:
            strategy_name = "llm_proposed"
            candidate = _try_llm_mutation(scene, provider, seed + tries)
            if candidate is None:
                continue
        else:
            strategy_name = strategy_names[idx % len(strategy_names)]
            idx += 1
            candidate = STRATEGIES[strategy_name](scene, rng)
        candidate.metadata.name = f"{scene.metadata.name}_mut{len(results)}_{strategy_name}"
        candidate.seed = seed + len(results) + 1
        if validate_scene(candidate).valid:
            results.append(candidate)
    return results


def mutate_scene_file(
    scene_path: str, out_dir: str, *, count: int, seed: int, provider=None, llm_fraction: float = 0.0
) -> list[str]:
    with open(scene_path) as f:
        scene = scene_spec_from_dict(json.load(f))
    mutations = generate_mutations(scene, count, seed, provider=provider, llm_fraction=llm_fraction)
    resolved_out = resolve_out_dir(out_dir)
    written = []
    for i, mutant in enumerate(mutations):
        path = write_json(resolved_out, f"mutation_{i:03d}.json", mutant.model_dump())
        written.append(path)
    return written
