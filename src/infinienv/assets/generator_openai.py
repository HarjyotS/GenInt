"""Optional sprite generation via the OpenAI Images API.

Not part of the P0 loop: `--assets generated`/`--assets auto` opt into this. If it's
unavailable (no key, no package, API error), callers must fall back cleanly and say
so -- never silently claim a generated asset was used when it wasn't.
"""

from __future__ import annotations

import base64
import os
import re
import time

from infinienv.llm.base import ProviderError

SPRITE_PROMPT_TEMPLATE = (
    "Retro 16-bit platformer game sprite of: {desc}. Style: BLOCKY pixel art built from "
    "chunky, clearly visible square pixels, a small flat color palette, a bold dark outline, "
    "and flat cel shading -- absolutely NO smooth gradients, NO photorealism, NO 3D render, "
    "NO soft or airbrushed shading, NO anti-aliased curves. Readable at 32x32, centered, "
    "no text, no labels, isolated object on a transparent background, suitable for a "
    "tile-based game whose world is made of square blocks. The object should fill nearly the "
    "entire frame edge-to-edge with minimal empty margin -- no small icon floating in a large "
    "blank canvas -- and must read clearly when scaled down and sit seamlessly next to other "
    "blocky sprites and square tiles in the same 16-bit style."
)

# Some types aren't "an object sitting on a tile" -- they ARE the tile's surface
# material (wall, floor). Those need a seamless, full-bleed texture, not a
# cropped/isolated object on a transparent background, or they render with visible
# padding around a "chunk" floating in the cell instead of a continuous surface.
TEXTURE_TILE_TYPES: set[str] = {"wall", "floor"}

TEXTURE_PROMPT_TEMPLATE = (
    "Retro 16-bit platformer game texture tile of: {desc}. Style: seamless tileable "
    "BLOCKY pixel art built from chunky, clearly visible square pixels, a small flat "
    "color palette, and flat shading -- absolutely NO smooth gradients, NO photorealism, "
    "NO soft shading. No text, no labels. The texture must cover the ENTIRE square canvas "
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


# --- Request anonymisation ---------------------------------------------------------------------
# The Images API applies OUTPUT moderation: it rejects a generated sprite (400 moderation_blocked)
# when the image reads as a real person or a copyrighted/trademarked character. A scene prompt like
# "an Italian man in green rescues Princess Peach" drove exactly that failure on the agent sprite
# (moderation_blocked on agent__walk/climb). So every description is anonymised before it reaches the
# API -- named characters/brands become neutral archetypes, nationality tags are dropped -- and the
# prompt gets an appended clause demanding an ORIGINAL, non-branded design. This is deliberately NOT
# an exhaustive IP database: the generic-reframe clause does the heavy lifting, a moderation
# rejection additionally retries once with a fully generic description (see generate_sprite), and
# this list just scrubs the specific, recurring triggers.
_NATIONALITY_WORDS = (
    "italian japanese american chinese korean mexican french german spanish russian british "
    "english irish indian egyptian greek roman nordic scandinavian"
).split()

_IP_REPLACEMENTS = {
    "donkey kong": "a large ape", "pac-man": "a round eater", "pacman": "a round eater",
    "spider-man": "a masked hero", "spiderman": "a masked hero", "princess peach": "a royal",
    "mario": "a plumber-style hero", "luigi": "a plumber-style hero", "peach": "a royal",
    "bowser": "a spiked-shell boss", "sonic": "a speedy creature", "pikachu": "an electric creature",
    "pokemon": "a small creature", "pokémon": "a small creature", "zelda": "an adventurer",
    "link": "a green-clad adventurer", "kirby": "a round pink creature", "yoshi": "a friendly dinosaur",
    "goomba": "a walking mushroom", "koopa": "a shelled creature", "minecraft": "blocky",
    "steve": "a blocky miner", "batman": "a caped hero", "superman": "a caped hero", "nintendo": "",
    "sega": "", "disney": "cartoon",
}

_ORIGINAL_DESIGN_CLAUSE = (
    " Depict an ORIGINAL, generic design of no particular identity -- do NOT depict any real person, "
    "celebrity, brand, logo, mascot, or any named/copyrighted/trademarked character from any existing "
    "game, film, or franchise."
)

_GENERIC_SPRITE_DESC = "a simple original game character, a plain generic figure of no particular identity"
_GENERIC_TEXTURE_DESC = "a plain generic surface material"

# On a 429 (this account's Images limit is 5/min) the request is retried after a wait; on an output
# moderation rejection it's retried once with a fully generic description. Overridable via env.
_IMAGE_MAX_RETRIES = int(os.environ.get("INFINIENV_IMAGE_MAX_RETRIES", "4"))


def _anonymize_description(desc: str) -> str:
    """Strip identity/IP signals from a sprite description so output moderation doesn't reject the
    result. Best-effort and general (not an exhaustive IP list); pairs with the appended
    original-design clause and the moderation retry in generate_sprite."""
    text = f" {desc} "
    for term in sorted(_IP_REPLACEMENTS, key=len, reverse=True):  # longest first ("donkey kong" > "kong")
        text = re.sub(rf"(?i)\b{re.escape(term)}\b", _IP_REPLACEMENTS[term], text)
    text = re.sub(r"(?i)\b(" + "|".join(_NATIONALITY_WORDS) + r")\b", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.-")
    return text or desc


def _rate_limit_sleep(message: str, attempt: int) -> float:
    """Seconds to wait before retrying a 429: prefer the server's own 'try again in Ns' hint, else
    back off from a ~12s base (the 5/min limit resets on roughly that cadence), capped at 30s."""
    m = re.search(r"try again in ([\d.]+)\s*s", message, re.IGNORECASE)
    if m:
        return min(float(m.group(1)) + 1.0, 30.0)
    return min(12.0 * (attempt + 1), 30.0)


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
    raw_desc = description or OBJECT_DESCRIPTIONS.get(object_type, object_type.replace("_", " "))
    is_texture = object_type in TEXTURE_TILE_TYPES
    template = TEXTURE_PROMPT_TEMPLATE if is_texture else SPRITE_PROMPT_TEMPLATE
    # Anonymise the request so the API's output moderation doesn't reject the sprite.
    desc = _anonymize_description(raw_desc)

    client = OpenAI()
    response = None
    generic_retry_used = False
    last_exc: Exception | None = None
    for attempt in range(_IMAGE_MAX_RETRIES + 1):
        prompt = template.format(desc=desc) + _ORIGINAL_DESIGN_CLAUSE
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
            break
        except Exception as exc:  # classify by message; retry the recoverable kinds, else give up
            last_exc = exc
            msg = str(exc).lower()
            # Rate limit (this account: 5 images/min) -- wait and retry the same request.
            if ("rate_limit" in msg or "429" in msg) and attempt < _IMAGE_MAX_RETRIES:
                time.sleep(_rate_limit_sleep(str(exc), attempt))
                continue
            # Output moderation flagged the generated image as IP/a real person -- retry ONCE with a
            # fully generic description, the last resort before falling back to a local placeholder.
            if ("moderation" in msg or "safety system" in msg) and not generic_retry_used:
                desc = _GENERIC_TEXTURE_DESC if is_texture else _GENERIC_SPRITE_DESC
                generic_retry_used = True
                continue
            raise ProviderError(f"image generation failed for {object_type!r}: {exc}") from exc
    if response is None:
        raise ProviderError(f"image generation failed for {object_type!r}: {last_exc}")

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
