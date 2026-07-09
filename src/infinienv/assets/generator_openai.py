"""Optional sprite generation via the OpenAI Images API.

Not part of the P0 loop: `--assets generated`/`--assets auto` opt into this. If it's
unavailable (no key, no package, API error), callers must fall back cleanly and say
so -- never silently claim a generated asset was used when it wasn't.
"""

from __future__ import annotations

import base64
import os

from infinienv.llm.base import ProviderError

SPRITE_PROMPT_TEMPLATE = (
    "Top-down 2D game sprite of: {desc}. Style: clean pixel art, readable at 32x32, "
    "centered object, no text, no labels, isolated object on a transparent background, "
    "suitable for a tile-based game. The object should fill nearly the entire frame "
    "edge-to-edge with minimal empty margin -- no small icon floating in a large blank "
    "canvas; it needs to read clearly when scaled down and tiled edge-to-edge with other "
    "sprites."
)

# Some types aren't "an object sitting on a tile" -- they ARE the tile's surface
# material (wall, floor). Those need a seamless, full-bleed texture, not a
# cropped/isolated object on a transparent background, or they render with visible
# padding around a "chunk" floating in the cell instead of a continuous surface.
TEXTURE_TILE_TYPES: set[str] = {"wall", "floor"}

TEXTURE_PROMPT_TEMPLATE = (
    "Top-down 2D game texture tile of: {desc}. Style: seamless tileable texture, "
    "pixel art, no text, no labels. The texture must cover the ENTIRE square canvas "
    "edge-to-edge with zero border, zero margin, and zero transparency -- an opaque "
    "surface filling 100% of the frame, not an isolated object floating in empty "
    "space. It will be tiled edge-to-edge with identical copies to form a continuous "
    "surface, so any border or vignette would show as a visible seam."
)

OBJECT_DESCRIPTIONS: dict[str, str] = {
    "agent": "a small friendly robot character",
    "wall": "a stone brick wall",
    "floor": "plain wood flooring planks",
    "table": "a wooden table",
    "can": "a metal soda can",
    "box": "a wooden crate",
    "key": "a brass key",
    "door": "a wooden door",
    "package": "a cardboard shipping package",
    "sink": "a kitchen sink",
    "exit": "a green exit sign",
    "hazard": "a red hazard warning sign",
    "distractor": "a purple decorative gem",
}


def _crop_to_content(img, *, pad_ratio: float = 0.03):
    """Crop tightly to the non-transparent pixels, then pad to a square.

    Even with a transparent background, the model tends to draw the object with
    margin inside the 1024x1024 canvas; without this, sprites look small and
    sparse once resized down to tile size instead of filling the cell.
    """
    from PIL import Image

    rgba = img.convert("RGBA")
    alpha = rgba.split()[3]
    bbox = alpha.getbbox()
    if bbox is None:
        return rgba
    w, h = rgba.size
    pad = int(max(w, h) * pad_ratio)
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
    cropped = rgba.crop((x0, y0, x1, y1))
    cw, ch = cropped.size
    side = max(cw, ch)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(cropped, ((side - cw) // 2, (side - ch) // 2), cropped)
    return square


def generate_sprite(
    object_type: str,
    cache_dir: str,
    *,
    model: str | None = None,
    quality: str | None = None,
    description: str | None = None,
) -> str:
    """Generate (or reuse a cached) sprite for `object_type`. Returns the PNG path.

    `description`, if given, overrides the generic `OBJECT_DESCRIPTIONS`/bare-type-name prompt
    basis -- callers should pass a scene-specific description when one is available (a declared
    `mechanics.custom_object_types` entry's own description, or the scene's prompt for the
    player character) rather than letting an unrelated custom type or "a small friendly robot
    character" (the generic default for "agent") drive what gets generated. See
    `resolver.py::resolve_assets`, which is the only real caller and always supplies this.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise ProviderError("OPENAI_API_KEY is not set; cannot generate sprites")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ProviderError("The 'openai' package is not installed.") from exc

    # gpt-image-1 (not gpt-image-2): per OpenAI's own docs, gpt-image-2 explicitly does
    # NOT support background="transparent" ("Requests with background: transparent
    # aren't supported for this model"); gpt-image-1/1.5/1-mini do. Overridable via
    # INFINIENV_IMAGE_MODEL, but transparency will silently stop working on gpt-image-2.
    model = model or os.environ.get("INFINIENV_IMAGE_MODEL", "gpt-image-1")
    # gpt-image-1's generation latency scales heavily with `quality` (low/medium/high/auto);
    # every sprite gets resized down to 64x64 immediately after (see below), so paying for
    # "auto" (which resolves to a slow, high-effort render) buys nothing visible. Default to
    # "low" -- overridable via INFINIENV_IMAGE_QUALITY for anyone who wants higher-fidelity
    # source images (e.g. if the render resolution is ever raised well above 64px).
    quality = quality or os.environ.get("INFINIENV_IMAGE_QUALITY", "low")
    desc = description or OBJECT_DESCRIPTIONS.get(object_type, object_type.replace("_", " "))
    is_texture = object_type in TEXTURE_TILE_TYPES
    prompt = (TEXTURE_PROMPT_TEMPLATE if is_texture else SPRITE_PROMPT_TEMPLATE).format(desc=desc)

    client = OpenAI()
    try:
        response = client.images.generate(
            model=model,
            prompt=prompt,
            size="1024x1024",
            quality=quality,
            background="opaque" if is_texture else "transparent",
            output_format="png",
            n=1,
        )
    except Exception as exc:
        raise ProviderError(f"image generation failed for {object_type!r}: {exc}") from exc

    b64 = response.data[0].b64_json
    if not b64:
        raise ProviderError(f"image generation returned no data for {object_type!r}")
    raw = base64.b64decode(b64)

    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{object_type}.png")
    with open(path, "wb") as f:
        f.write(raw)

    from PIL import Image

    img = Image.open(path)
    # Texture tiles are meant to fill the whole canvas already (opaque, full-bleed
    # prompt) -- cropping to content would be a no-op at best and risks clipping a
    # legitimately busy edge-to-edge pattern at worst, so skip it for those.
    img = img if is_texture else _crop_to_content(img)
    img = img.convert("RGBA").resize((64, 64))
    img.save(path)
    return path
