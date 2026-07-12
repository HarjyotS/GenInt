"""Closed perception model: the solver may only read what it has observed (engine/perception.py)."""

from infinienv.engine.perception import KnowledgeMap, visible_cells


def test_knowledgemap_only_knows_what_it_observed():
    km = KnowledgeMap()
    assert not km.has_seen((3, 3))
    km.observe({(3, 3): "diamond", (3, 4): "stone"})
    assert km.has_seen((3, 3))
    assert km.get((3, 3)) == "diamond"
    assert km.get((9, 9)) is None  # never observed -> unknown, not ground truth
    assert (3, 3) in km and len(km) == 2


def test_knowledgemap_find_cannot_reach_an_unseen_target():
    km = KnowledgeMap()
    km.observe({(1, 1): "dirt", (2, 2): "diamond"})
    assert km.find(lambda v: v == "diamond") == [(2, 2)]  # a diamond it has actually seen
    assert km.find(lambda v: v == "gold") == []  # never observed -> can't beeline to it (the cheat)


def test_knowledgemap_accepts_bare_cells():
    km = KnowledgeMap()
    km.observe([(0, 0), (1, 0)])
    assert km.has_seen((0, 0)) and km.get((0, 0)) is True


def test_knowledgemap_remembers_after_leaving_view():
    km = KnowledgeMap()
    km.observe({(5, 5): "coal"})
    km.observe({(6, 5): "iron"})  # (5,5) not in this observation
    assert km.has_seen((5, 5))  # fog-of-war memory persists
    assert km.known_cells() == {(5, 5), (6, 5)}


def test_visible_cells_radius():
    vis = visible_cells((5, 5), 2, set())
    assert (5, 5) in vis and (5, 7) in vis and (7, 5) in vis  # within radius, open field
    assert (5, 8) not in vis  # outside radius


def test_visible_cells_occlusion():
    blockers = {(5, 6)}  # a wall directly north of the origin
    vis = visible_cells((5, 5), 3, blockers)
    assert (5, 6) in vis  # the wall itself is visible (you see what you bump into)
    assert (5, 7) not in vis and (5, 8) not in vis  # cells behind the wall are occluded


def test_visible_cells_respects_bounds():
    vis = visible_cells((0, 0), 3, set(), bounds=(4, 4))
    assert all(0 <= x < 4 and 0 <= y < 4 for x, y in vis)  # never scans off-grid
