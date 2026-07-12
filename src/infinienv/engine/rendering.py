"""Generic sprite-placement helpers so a drawn character sits where the physics says it is.

A recurring bug across platformer runs: the character floats above the ground. Two causes, both
about the render disagreeing with the simulation -- (1) the physics clamps a grounded entity to one
ground line but the surface is *drawn* at a different one (two different numbers for "the ground"),
and (2) the sprite is pasted *center*-anchored (top-left = center - size/2), so a standing
character's feet land half a sprite-height below its center point and hover over the ground. These
helpers fix (2) structurally -- feet-anchoring is correct-by-construction -- so the only thing left
to keep straight is using a single shared ground constant for both physics and drawing (a
discipline, since the agent writes both).

Pure functions on plain numbers -- no PIL/engine dependency, so they compose with any draw loop.
Returns integer top-left pixel coordinates ready to hand to `PIL.Image.paste`.
"""

from __future__ import annotations


def feet_anchor(center_x: float, ground_y_px: float, size: float) -> tuple[int, int]:
    """Top-left paste corner for a square sprite of side `size` so its **bottom edge sits exactly
    on `ground_y_px`** and it's horizontally centered on `center_x`. Use this for a grounded
    character instead of a center-anchored paste (`center - size/2` on both axes), which floats a
    standing sprite by half its height above wherever its center is.
    """
    return (round(center_x - size / 2), round(ground_y_px - size))


def feet_anchor_rect(center_x: float, ground_y_px: float, width: float, height: float) -> tuple[int, int]:
    """Feet-anchor for a non-square sprite (a character is usually taller than wide): bottom edge
    on `ground_y_px`, horizontally centered on `center_x`."""
    return (round(center_x - width / 2), round(ground_y_px - height))


class SpriteBook:
    """A thin wrapper over the `{key: path}` map `assets.resolver.resolve_assets` returns, so a
    custom draw loop pastes generated sprites *by their real resolved key* -- and can prove, in one
    line, that it didn't silently leave any behind.

    Two recurring, user-reported rendering bugs this exists to make mechanically detectable:

    - **"Resolve then ignore"** -- a run generates a full `asset_cache/` of nice sprites and then
      draws flat primitives anyway, using none of them. `unused_keys()` names exactly the sprites a
      draw loop never pasted, so a self-check can `assert not book.unused_keys()` and fail loudly.
    - **Key mismatch** -- the draw loop asks for a key that doesn't match what was resolved (e.g.
      `"hero"` when the player object resolved as `"agent"`), so that entity silently falls back to
      a primitive. The wrong key just returns `False` (caller draws its fallback), *and* the real
      key (`"agent"`) shows up in `unused_keys()` -- so the same assertion catches this too.

    Paste at a consistent, layout-appropriate `size` tied to your tile/grid (e.g. `TILE`, or a small
    multiple for a large structure), not an arbitrary per-entity pixel size -- inconsistent
    rescaling is what makes a render read worse than one that keeps sprites at natural tile sizes.

    PIL is imported lazily inside `paste` so importing this module stays cheap for callers (e.g. the
    physics/self-check helpers) that never draw anything.
    """

    def __init__(self, asset_paths: dict[str, str] | None):
        # Keep only keys that actually resolved to a path -- a None/empty path is "no sprite for
        # this key" (paste falls back), so it must never count toward unused_keys() (which means
        # "resolved sprite the draw loop ignored"). resolve_assets already filters, but be robust.
        self._paths = {k: p for k, p in (asset_paths or {}).items() if p}
        self._cache: dict[tuple[str, int], object] = {}
        self._used: set[str] = set()

    def has(self, key: str) -> bool:
        """True if a sprite path was resolved for `key` (so `paste` will actually paste it)."""
        return bool(self._paths.get(key))

    def paste(self, img, key: str, cx: float, cy: float, size: int, *, anchor: str = "center") -> bool:
        """Paste the sprite for `key` onto `img`, resized square to `size`, and record `key` as
        used. `anchor="center"` (default) centers it on `(cx, cy)`; `anchor="feet"` treats `cy` as
        the ground line and sits the sprite's bottom edge on it (via `feet_anchor`) -- one call for a
        grounded character. Returns `False` (nothing pasted) when no sprite resolved for `key`, so
        the caller's per-key primitive fallback still runs exactly as before.
        """
        path = self._paths.get(key)
        if not path:
            return False
        from PIL import Image

        cache_key = (path, int(size))
        sprite = self._cache.get(cache_key)
        if sprite is None:
            sprite = Image.open(path).convert("RGBA").resize((int(size), int(size)))
            self._cache[cache_key] = sprite
        if anchor == "feet":
            top_left = feet_anchor(cx, cy, size)
        else:
            top_left = (int(cx - size / 2), int(cy - size / 2))
        img.paste(sprite, top_left, sprite)
        self._used.add(key)
        return True

    def paste_column(self, img, key: str, cx: float, y_top: float, y_bottom: float, tile: int) -> int:
        """Draw a multi-cell vertical structure -- a ladder between two floors, a pipe, a wall column
        -- as **one contiguous run of tiles that meets both endpoints**. Pastes the `key` sprite at
        every cell center from `y_top` to `y_bottom` inclusive, stepping by `tile`, centered on `cx`
        (pixel coordinates, so it's agnostic to a run's own grid/HUD offset). Returns the number of
        tiles pasted (0 if no sprite resolved for `key`, so a per-key primitive fallback still runs),
        and records `key` as used.

        This makes "a ladder spans contiguously from its lower floor to its upper floor, touching
        both" correct by construction: pass the two floors' pixel rows and every cell between them is
        filled -- there's no `range(top+1, bottom)` trim or `% 2` "rung" gap to silently detach the
        ladder from the floors it connects (a real, user-reported "why are the ladders separated"
        bug). `y_top`/`y_bottom` may be given in either order.
        """
        if not self._paths.get(key):
            return 0
        top, bottom = (y_top, y_bottom) if y_top <= y_bottom else (y_bottom, y_top)
        count = 0
        cy = top
        # Inclusive of both ends; a tiny epsilon guards float accumulation on the last cell.
        while cy <= bottom + 1e-6:
            self.paste(img, key, cx, cy, tile)
            count += 1
            cy += tile
        return count

    def unused_keys(self) -> list[str]:
        """Resolved sprite keys that `paste` was never (successfully) called with -- i.e. generated
        art the draw loop ignored. Empty is the invariant a self-check should assert; a non-empty
        result is either the "resolve then ignore" bug or a key mismatch (see the class docstring).
        """
        return sorted(k for k in self._paths if k not in self._used)
