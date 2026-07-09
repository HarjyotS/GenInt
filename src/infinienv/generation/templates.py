"""Deterministic, always-solvable-by-construction scene templates.

These back the `mock` provider (no API key required) and the final fallback
step of the repair loop. Every template is parameterized by a seed so the
same (template, seed) pair always yields the same scene.
"""

from __future__ import annotations

import random

from infinienv.schema.scene_schema import (
    AgentSpec,
    DeliverGoal,
    Grid,
    PushGoal,
    ReachGoal,
    SceneMetadata,
    SceneObject,
    SceneSpec,
    UnlockGoal,
    WallCell,
)

DEFAULT_W, DEFAULT_H = 16, 12


def _border_walls(width: int, height: int) -> list[WallCell]:
    cells: set[tuple[int, int]] = set()
    for x in range(width):
        cells.add((x, 0))
        cells.add((x, height - 1))
    for y in range(height):
        cells.add((0, y))
        cells.add((width - 1, y))
    return [WallCell(x=x, y=y) for x, y in sorted(cells)]


def _free_cell(rng: random.Random, x_range: range, y_range: range, taken: set[tuple[int, int]]) -> tuple[int, int]:
    while True:
        x, y = rng.choice(list(x_range)), rng.choice(list(y_range))
        if (x, y) not in taken:
            taken.add((x, y))
            return x, y


def kitchen_delivery(prompt: str, seed: int, *, theme: str = "kitchen") -> SceneSpec:
    """Open room: agent picks up a portable item and delivers it to a target."""
    rng = random.Random(seed)
    width, height = DEFAULT_W, DEFAULT_H
    taken: set[tuple[int, int]] = set()

    agent_x, agent_y = _free_cell(rng, range(1, 4), range(1, height - 1), taken)
    table_x, table_y = _free_cell(rng, range(4, width - 4), range(2, height - 2), taken)
    item_x, item_y = table_x, max(1, table_y - 1)
    taken.add((item_x, item_y))
    sink_x, sink_y = _free_cell(rng, range(width - 4, width - 1), range(1, height - 1), taken)

    scene = SceneSpec(
        seed=seed,
        metadata=SceneMetadata(name="kitchen_can_delivery", prompt=prompt, theme=theme),
        grid=Grid(width=width, height=height, tile_size=32),
        agent=AgentSpec(id="agent", x=agent_x, y=agent_y),
        objects=[
            SceneObject(id="table_1", type="table", x=table_x, y=table_y, solid=True),
            SceneObject(id="can_1", type="can", x=item_x, y=item_y, portable=True),
            SceneObject(id="sink_1", type="sink", x=sink_x, y=sink_y, solid=False),
        ],
        walls=_border_walls(width, height),
        goals=[DeliverGoal(id="deliver_can_to_sink", object_id="can_1", target_id="sink_1")],
    )
    return scene


def warehouse_key_door(prompt: str, seed: int, *, theme: str = "warehouse") -> SceneSpec:
    """Two rooms split by a wall with a locked door; key in room 1, package+exit in room 2."""
    rng = random.Random(seed)
    width, height = DEFAULT_W, DEFAULT_H
    divider_x = width // 2
    door_y = rng.randint(2, height - 3)

    walls = _border_walls(width, height)
    for y in range(1, height - 1):
        if y != door_y:
            walls.append(WallCell(x=divider_x, y=y))

    taken: set[tuple[int, int]] = {(divider_x, door_y)}
    agent_x, agent_y = _free_cell(rng, range(1, divider_x - 1), range(1, height - 1), taken)
    key_x, key_y = _free_cell(rng, range(1, divider_x - 1), range(1, height - 1), taken)
    package_x, package_y = _free_cell(rng, range(divider_x + 1, width - 1), range(1, height - 1), taken)
    exit_x, exit_y = _free_cell(rng, range(divider_x + 1, width - 1), range(1, height - 1), taken)

    scene = SceneSpec(
        seed=seed,
        metadata=SceneMetadata(name="warehouse_key_delivery", prompt=prompt, theme=theme),
        grid=Grid(width=width, height=height, tile_size=32),
        agent=AgentSpec(id="agent", x=agent_x, y=agent_y),
        objects=[
            SceneObject(id="key_1", type="key", x=key_x, y=key_y, portable=True),
            SceneObject(id="door_1", type="door", x=divider_x, y=door_y, solid=True, locked=True, key_id="key_1"),
            SceneObject(id="package_1", type="package", x=package_x, y=package_y, portable=True),
            SceneObject(id="exit_1", type="exit", x=exit_x, y=exit_y, solid=False),
        ],
        walls=walls,
        goals=[
            UnlockGoal(id="unlock_door", door_id="door_1"),
            DeliverGoal(id="deliver_package", object_id="package_1", target_id="exit_1"),
        ],
    )
    return scene


def obstacle_course(prompt: str, seed: int, *, theme: str = "obstacle_course") -> SceneSpec:
    """Open room with scattered solid obstacles and a reach goal, plus a decoy distractor."""
    rng = random.Random(seed)
    width, height = DEFAULT_W, DEFAULT_H
    taken: set[tuple[int, int]] = set()

    agent_x, agent_y = _free_cell(rng, range(1, 3), range(1, height - 1), taken)
    exit_x, exit_y = _free_cell(rng, range(width - 3, width - 1), range(1, height - 1), taken)

    objects = [SceneObject(id="exit_1", type="exit", x=exit_x, y=exit_y, solid=False)]

    # Scatter a handful of solid obstacles that never fully seal a straight corridor.
    for i in range(4):
        ox, oy = _free_cell(rng, range(3, width - 3), range(2, height - 2), taken)
        objects.append(SceneObject(id=f"box_{i}", type="box", x=ox, y=oy, solid=True))

    dx, dy = _free_cell(rng, range(2, width - 2), range(1, height - 1), taken)
    objects.append(SceneObject(id="distractor_1", type="distractor", x=dx, y=dy, portable=True))

    scene = SceneSpec(
        seed=seed,
        metadata=SceneMetadata(name="obstacle_course_reach", prompt=prompt, theme=theme),
        grid=Grid(width=width, height=height, tile_size=32),
        agent=AgentSpec(id="agent", x=agent_x, y=agent_y),
        objects=objects,
        walls=_border_walls(width, height),
        goals=[ReachGoal(id="reach_exit", target_id="exit_1")],
    )
    return scene


def push_slide_puzzle(prompt: str, seed: int, *, theme: str = "ice") -> SceneSpec:
    """Deterministic grid-physics puzzle: the agent shoves a slippery puck that slides across
    the floor until it hits the right wall, landing on a target plate. Always solvable by
    construction -- the plate sits in the last interior cell of the puck's row, so a single push
    slides the puck exactly onto it. Exercises the push + slide vocabulary (engine/physics.py)."""
    rng = random.Random(seed)
    width, height = DEFAULT_W, DEFAULT_H
    row = rng.randint(2, height - 3)

    puck_x = 4
    plate_x = width - 2  # last interior cell before the right border wall at (width-1, row)

    scene = SceneSpec(
        seed=seed,
        metadata=SceneMetadata(name="push_slide_puzzle", prompt=prompt, theme=theme),
        grid=Grid(width=width, height=height, tile_size=32),
        agent=AgentSpec(id="agent", x=2, y=row),
        objects=[
            SceneObject(id="puck_1", type="box", x=puck_x, y=row, solid=True, pushable=True, slippery=True),
            SceneObject(id="goal_plate_1", type="sink", x=plate_x, y=row, solid=False),
        ],
        walls=_border_walls(width, height),
        goals=[PushGoal(id="push_puck_to_plate", object_id="puck_1", target_id="goal_plate_1")],
    )
    return scene


TEMPLATES = {
    "kitchen": kitchen_delivery,
    "warehouse": warehouse_key_door,
    "obstacle_course": obstacle_course,
    "push": push_slide_puzzle,
}


def pick_template_name(prompt: str) -> str:
    p = prompt.lower()
    if any(kw in p for kw in ("push", "shove", "crate", "boulder", "block", "slide", "slippery", "ice", "momentum", "physics")):
        return "push"
    if any(kw in p for kw in ("key", "door", "lock", "unlock")):
        return "warehouse"
    if any(kw in p for kw in ("maze", "obstacle", "hazard", "navigate", "avoid")):
        return "obstacle_course"
    return "kitchen"


def generate_from_template(prompt: str, seed: int) -> SceneSpec:
    name = pick_template_name(prompt)
    return TEMPLATES[name](prompt, seed)
