"""Curriculum generator: easy -> hard natural-language prompt suites for a theme.

Per CLAUDE.md section 16.C. Output is a plain prompts.txt (one prompt per line),
the same format `infinienv benchmark` consumes, so a generated curriculum can be
benchmarked immediately.
"""

from __future__ import annotations

import os

LEVEL_TEMPLATES = [
    # Level 1: open room pickup
    "Create a {theme} with an open room where the agent picks up a single item and delivers it to a nearby target.",
    # Level 2: pickup behind obstacle
    "Create a {theme} where the agent must navigate around a piece of furniture to reach and pick up an item.",
    # Level 3: delivery across rooms
    "Create a {theme} with two connected rooms where the agent picks up an item in the first room and delivers it to a target in the second room.",
    # Level 4: key-door dependency
    "Create a {theme} where the agent must find a key, unlock a locked door, and deliver a package to the exit on the other side.",
    # Level 5: decoy object and longer path
    "Create a large {theme} with a decoy object that looks useful but is not part of the task, and a longer path the agent must take to find a key, unlock a door, and deliver a package to a far exit.",
]


def build_curriculum(theme: str, levels: int) -> list[str]:
    levels = max(1, min(levels, len(LEVEL_TEMPLATES)))
    return [LEVEL_TEMPLATES[i].format(theme=theme) for i in range(levels)]


def write_curriculum(theme: str, out_path: str, *, levels: int = 5) -> str:
    prompts = build_curriculum(theme, levels)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w") as f:
        for i, prompt in enumerate(prompts, start=1):
            f.write(f"# level {i}\n{prompt}\n")
    return out_path
