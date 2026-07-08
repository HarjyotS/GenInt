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
    "centered object, no text, no labels, plain light background, isolated object, "
    "suitable for a tile-based game."
)

OBJECT_DESCRIPTIONS: dict[str, str] = {
    "agent": "a small friendly robot character",
    "wall": "a stone brick wall tile",
    "floor": "a plain wood floor tile",
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


def generate_sprite(object_type: str, cache_dir: str, *, model: str | None = None) -> str:
    """Generate (or reuse a cached) sprite for `object_type`. Returns the PNG path."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise ProviderError("OPENAI_API_KEY is not set; cannot generate sprites")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ProviderError("The 'openai' package is not installed.") from exc

    # NOTE: PATHWAY.md names "gpt-image-2"; the currently-released OpenAI image model
    # is "gpt-image-1", so that's the default here. Overridable via INFINIENV_IMAGE_MODEL
    # in case a newer model name becomes available.
    model = model or os.environ.get("INFINIENV_IMAGE_MODEL", "gpt-image-1")
    desc = OBJECT_DESCRIPTIONS.get(object_type, object_type.replace("_", " "))
    prompt = SPRITE_PROMPT_TEMPLATE.format(desc=desc)

    client = OpenAI()
    try:
        response = client.images.generate(model=model, prompt=prompt, size="1024x1024", n=1)
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

    from PIL import Image  # normalize to a small consistent tile size

    img = Image.open(path).convert("RGBA").resize((64, 64))
    img.save(path)
    return path
