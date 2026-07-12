"""Generic Sokoban-style crate/block pushing for sandbox-authored simulations.

The base engine already has push mechanics (`engine/physics.py::try_push`), but that mutates
`ObjectState` against a `Grid` -- unusable from a sandbox custom loop that tracks blocks as plain
`{id: (x, y)}` dicts. This is the dependency-free equivalent, the same relationship
`engine/grid_collision.py` has to the base engine's wall-blocking. It exists because "push a crate
onto a switch" is a common mechanic that's easy to get subtly wrong by hand: a block shoved
through a wall, two blocks occupying the same cell, a push that should have been blocked by a
second crate behind the first.

Pure functions on plain tuples/dicts/sets -- no dependency on `engine/state.py` or any other
InfiniEnv module.
"""

from __future__ import annotations


def block_at(blocks: dict[str, tuple[int, int]], cell: tuple[int, int]) -> str | None:
    """The id of the block currently occupying `cell`, or None."""
    for block_id, pos in blocks.items():
        if pos == cell:
            return block_id
    return None


def try_push_block(
    blocks: dict[str, tuple[int, int]],
    block_id: str,
    direction: tuple[int, int],
    blocked: set[tuple[int, int]],
    *,
    width: int | None = None,
    height: int | None = None,
) -> bool:
    """Push `block_id` one cell in `direction` (e.g. `(1, 0)` for right). Moves it (mutating
    `blocks` in place) only if the destination cell is free of walls (`blocked`), other blocks,
    and -- if `width`/`height` are given -- inside bounds. Returns whether it actually moved.

    A block is a real solid object: it cannot be shoved through a wall, off the grid, or into
    another block. This is the whole point of using this over just reassigning the block's
    coordinate -- collision applies to the block the same way it applies to the agent (principle
    2), rather than being something to remember to check by hand at each push site.
    """
    if block_id not in blocks:
        raise KeyError(f"no block {block_id!r} in blocks")
    dx, dy = direction
    x, y = blocks[block_id]
    dest = (x + dx, y + dy)
    if dest in blocked:
        return False
    if width is not None and not (0 <= dest[0] < width):
        return False
    if height is not None and not (0 <= dest[1] < height):
        return False
    if block_at(blocks, dest) is not None:
        return False
    blocks[block_id] = dest
    return True


def cell_is_free(
    cell: tuple[int, int],
    blocks: dict[str, tuple[int, int]],
    blocked: set[tuple[int, int]],
    *,
    width: int | None = None,
    height: int | None = None,
) -> bool:
    """True if `cell` is walkable: in bounds, not a wall, not occupied by a block. The same
    check the agent's own movement should use in a scene with pushable blocks, so a block
    genuinely obstructs the agent until pushed."""
    if cell in blocked:
        return False
    if width is not None and not (0 <= cell[0] < width):
        return False
    if height is not None and not (0 <= cell[1] < height):
        return False
    return block_at(blocks, cell) is None


def all_targets_satisfied(
    blocks: dict[str, tuple[int, int]], targets: dict[str, tuple[int, int]]
) -> bool:
    """True if every block named in `targets` is currently resting on its target cell -- the
    generic "all crates on their switches/plates" win check. `targets` is `{block_id: cell}`."""
    return all(blocks.get(block_id) == cell for block_id, cell in targets.items())
