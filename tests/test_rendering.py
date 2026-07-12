"""Generic feet-anchored sprite placement for sandbox-authored simulations (engine/rendering.py)."""

from PIL import Image

from infinienv.engine.rendering import SpriteBook, feet_anchor, feet_anchor_rect


def test_feet_anchor_puts_the_bottom_edge_on_the_ground_line():
    # a 40px sprite centered at x=100 with feet on ground_y=300
    left, top = feet_anchor(100, 300, 40)
    assert left == 80  # 100 - 40/2
    assert top == 260  # 300 - 40
    assert top + 40 == 300  # bottom edge lands exactly on the ground line


def test_feet_anchor_does_not_float_the_sprite():
    # the whole point: a grounded sprite's bottom == ground line, never center == ground line
    # (which would float it by size/2)
    size = 64
    ground = 500
    _, top = feet_anchor(0, ground, size)
    assert top + size == ground  # feet on ground, not top+size/2


def test_feet_anchor_rect_taller_than_wide():
    left, top = feet_anchor_rect(100, 300, width=30, height=50)
    assert left == 85  # 100 - 30/2
    assert top == 250  # 300 - 50
    assert top + 50 == 300


def test_feet_anchor_rounds_to_ints():
    left, top = feet_anchor(100.4, 299.6, 41)
    assert isinstance(left, int) and isinstance(top, int)


def _make_sprite(tmp_path, name, color=(200, 30, 30, 255)):
    path = tmp_path / f"{name}.png"
    Image.new("RGBA", (8, 8), color).save(path)
    return str(path)


def test_spritebook_pastes_present_key_and_records_it_as_used(tmp_path):
    book = SpriteBook({"agent": _make_sprite(tmp_path, "agent")})
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    assert book.has("agent")
    assert book.paste(img, "agent", 32, 32, 16) is True
    assert book.unused_keys() == []  # the only resolved key got pasted


def test_spritebook_missing_key_returns_false_and_pastes_nothing(tmp_path):
    book = SpriteBook({"agent": _make_sprite(tmp_path, "agent")})
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    # A wrong/absent key (the 'hero' vs 'agent' mismatch) does not paste -> caller draws its fallback
    assert book.has("hero") is False
    assert book.paste(img, "hero", 32, 32, 16) is False
    # ...and the real resolved key is still flagged as never used, so a self-check catches the bug.
    assert book.unused_keys() == ["agent"]


def test_spritebook_unused_keys_reports_ignored_sprites(tmp_path):
    # The "resolve then ignore" bug: several sprites resolved, only one pasted.
    book = SpriteBook(
        {k: _make_sprite(tmp_path, k) for k in ("agent", "coin", "pipe", "tower")}
    )
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    book.paste(img, "agent", 32, 32, 16)
    assert book.unused_keys() == ["coin", "pipe", "tower"]


def test_spritebook_caches_the_resized_sprite(tmp_path):
    book = SpriteBook({"agent": _make_sprite(tmp_path, "agent")})
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    book.paste(img, "agent", 10, 10, 16)
    book.paste(img, "agent", 40, 40, 16)  # same key+size -> served from cache
    assert list(book._cache.keys()) == [(book._paths["agent"], 16)]


def test_spritebook_feet_anchor_sits_bottom_edge_on_ground_line(tmp_path):
    book = SpriteBook({"hero": _make_sprite(tmp_path, "hero", color=(0, 200, 0, 255))})
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    # feet anchor: cy is the ground line; a 16px sprite's opaque bottom row lands on ground-1.
    ground = 40
    assert book.paste(img, "hero", 32, ground, 16, anchor="feet") is True
    px = img.load()
    assert px[32, ground - 1][3] == 255  # bottom row of the sprite is on the ground line
    assert px[32, ground][3] == 0  # nothing pasted below it


def test_spritebook_handles_none_asset_paths():
    book = SpriteBook(None)
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    assert book.paste(img, "anything", 4, 4, 4) is False
    assert book.unused_keys() == []


def test_paste_column_fills_every_cell_inclusive_of_both_ends(tmp_path):
    book = SpriteBook({"ladder": _make_sprite(tmp_path, "ladder")})
    img = Image.new("RGBA", (64, 200), (0, 0, 0, 0))
    tile = 16
    # a ladder from the top floor (y=32) to the bottom floor (y=96): 5 cells, both ends inclusive
    n = book.paste_column(img, "ladder", 32, y_top=32, y_bottom=96, tile=tile)
    assert n == 5  # 32, 48, 64, 80, 96 -- no gaps, both floor rows covered
    px = img.load()
    assert px[32, 32][3] == 255  # top floor cell drawn
    assert px[32, 96][3] == 255  # bottom floor cell drawn
    assert px[32, 64][3] == 255  # a middle cell drawn (not a % 2 gap)
    assert book.unused_keys() == []  # the ladder key was used


def test_paste_column_accepts_endpoints_in_either_order(tmp_path):
    book = SpriteBook({"ladder": _make_sprite(tmp_path, "ladder")})
    img = Image.new("RGBA", (64, 200), (0, 0, 0, 0))
    assert book.paste_column(img, "ladder", 32, y_top=96, y_bottom=32, tile=16) == 5


def test_paste_column_missing_key_draws_nothing(tmp_path):
    book = SpriteBook({"ladder": _make_sprite(tmp_path, "ladder")})
    img = Image.new("RGBA", (64, 200), (0, 0, 0, 0))
    assert book.paste_column(img, "pipe", 32, y_top=32, y_bottom=96, tile=16) == 0  # no sprite -> fallback
    assert book.unused_keys() == ["ladder"]  # 'ladder' still never pasted


def test_spritebook_unresolved_key_never_counts_as_unused(tmp_path):
    # A key whose path is None/empty means "no sprite resolved" -> it must not fail the
    # unused_keys() invariant (that's for resolved sprites the draw loop ignored).
    book = SpriteBook({"agent": _make_sprite(tmp_path, "agent"), "ghost": None})
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    assert book.has("ghost") is False
    book.paste(img, "agent", 32, 32, 16)
    assert book.unused_keys() == []  # 'ghost' had no path, so it isn't an ignored sprite
