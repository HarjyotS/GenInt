"""Generic Sokoban-style crate/block pushing for sandbox-authored simulations
(engine/pushables.py)."""

import pytest

from infinienv.engine.pushables import all_targets_satisfied, block_at, cell_is_free, try_push_block


def test_block_at_finds_and_misses():
    blocks = {"crate": (3, 4)}
    assert block_at(blocks, (3, 4)) == "crate"
    assert block_at(blocks, (0, 0)) is None


def test_try_push_block_moves_into_a_free_cell():
    blocks = {"crate": (2, 2)}
    moved = try_push_block(blocks, "crate", (1, 0), blocked=set())
    assert moved is True
    assert blocks["crate"] == (3, 2)


def test_try_push_block_blocked_by_a_wall():
    blocks = {"crate": (2, 2)}
    moved = try_push_block(blocks, "crate", (1, 0), blocked={(3, 2)})
    assert moved is False
    assert blocks["crate"] == (2, 2)  # unchanged


def test_try_push_block_blocked_by_another_block():
    blocks = {"crate": (2, 2), "boulder": (3, 2)}
    moved = try_push_block(blocks, "crate", (1, 0), blocked=set())
    assert moved is False
    assert blocks["crate"] == (2, 2)


def test_try_push_block_blocked_by_bounds():
    blocks = {"crate": (4, 2)}
    moved = try_push_block(blocks, "crate", (1, 0), blocked=set(), width=5, height=5)
    assert moved is False  # would go to x=5, out of [0,5)
    assert blocks["crate"] == (4, 2)


def test_try_push_block_unknown_id_raises():
    with pytest.raises(KeyError):
        try_push_block({"crate": (0, 0)}, "ghost", (1, 0), blocked=set())


def test_cell_is_free_accounts_for_walls_blocks_and_bounds():
    blocks = {"crate": (3, 2)}
    blocked = {(1, 1)}
    assert cell_is_free((0, 0), blocks, blocked, width=5, height=5) is True
    assert cell_is_free((1, 1), blocks, blocked) is False  # wall
    assert cell_is_free((3, 2), blocks, blocked) is False  # block
    assert cell_is_free((9, 9), blocks, blocked, width=5, height=5) is False  # out of bounds


def test_all_targets_satisfied():
    targets = {"crate": (5, 5), "boulder": (6, 6)}
    assert all_targets_satisfied({"crate": (5, 5), "boulder": (6, 6)}, targets) is True
    assert all_targets_satisfied({"crate": (5, 5), "boulder": (0, 0)}, targets) is False
    assert all_targets_satisfied({"crate": (5, 5)}, targets) is False  # boulder missing
