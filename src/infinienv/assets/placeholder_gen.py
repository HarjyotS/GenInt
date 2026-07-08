"""Generates the checked-in local placeholder sprite set (`assets/base/*.png`).

These are the `--assets local` / `--assets none`-fallback tier: simple, distinct,
readable-at-32px icons per supported object type, drawn with Pillow shapes (no
external art, no network). Re-run this module (`python -m infinienv.assets.placeholder_gen`)
if `schema.scene_schema.OBJECT_TYPE_VALUES` ever changes.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

SIZE = 64
TRANSPARENT = (0, 0, 0, 0)


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (SIZE, SIZE), TRANSPARENT)
    return img, ImageDraw.Draw(img)


def _agent() -> Image.Image:
    img, d = _canvas()
    d.ellipse((6, 6, 58, 58), fill=(30, 100, 240, 255), outline=(10, 40, 120, 255), width=3)
    d.ellipse((22, 24, 30, 32), fill=(255, 255, 255, 255))
    d.ellipse((34, 24, 42, 32), fill=(255, 255, 255, 255))
    return img


def _wall() -> Image.Image:
    img, d = _canvas()
    d.rectangle((2, 2, 62, 62), fill=(70, 70, 70, 255))
    for y in (18, 34, 50):
        offset = 16 if (y // 16) % 2 else 0
        d.line((2, y, 62, y), fill=(50, 50, 50, 255), width=2)
        for x in range(-16 + offset, 64, 32):
            d.line((x, y - 16, x, y), fill=(50, 50, 50, 255), width=2)
    return img


def _floor() -> Image.Image:
    img, d = _canvas()
    d.rectangle((2, 2, 62, 62), fill=(240, 240, 240, 255), outline=(220, 220, 220, 255))
    return img


def _table() -> Image.Image:
    img, d = _canvas()
    d.rounded_rectangle((6, 14, 58, 34), radius=4, fill=(150, 111, 51, 255), outline=(100, 74, 34, 255))
    for x in (10, 50):
        d.rectangle((x, 34, x + 4, 56), fill=(100, 74, 34, 255))
    return img


def _can() -> Image.Image:
    img, d = _canvas()
    d.rounded_rectangle((22, 10, 42, 54), radius=6, fill=(190, 190, 190, 255), outline=(120, 120, 120, 255))
    d.line((22, 20, 42, 20), fill=(120, 120, 120, 255), width=2)
    d.line((22, 44, 42, 44), fill=(120, 120, 120, 255), width=2)
    return img


def _box() -> Image.Image:
    img, d = _canvas()
    d.rectangle((10, 10, 54, 54), fill=(181, 136, 99, 255), outline=(120, 88, 60, 255), width=2)
    d.line((10, 32, 54, 32), fill=(120, 88, 60, 255), width=2)
    d.line((32, 10, 32, 54), fill=(120, 88, 60, 255), width=2)
    return img


def _key() -> Image.Image:
    img, d = _canvas()
    d.ellipse((8, 22, 26, 40), outline=(255, 200, 0, 255), width=5)
    d.line((24, 31, 54, 31), fill=(255, 200, 0, 255), width=5)
    d.line((46, 31, 46, 40), fill=(255, 200, 0, 255), width=5)
    d.line((54, 31, 54, 40), fill=(255, 200, 0, 255), width=5)
    return img


def _door() -> Image.Image:
    img, d = _canvas()
    d.rectangle((14, 6, 50, 58), fill=(139, 69, 19, 255), outline=(90, 45, 12, 255), width=2)
    d.ellipse((38, 30, 44, 36), fill=(255, 215, 0, 255))
    return img


def _package() -> Image.Image:
    img, d = _canvas()
    d.rectangle((10, 14, 54, 50), fill=(222, 184, 135, 255), outline=(150, 120, 80, 255), width=2)
    d.line((10, 32, 54, 32), fill=(150, 120, 80, 255), width=4)
    d.line((32, 14, 32, 50), fill=(150, 120, 80, 255), width=4)
    return img


def _sink() -> Image.Image:
    img, d = _canvas()
    d.rounded_rectangle((8, 20, 56, 50), radius=8, fill=(100, 149, 237, 255), outline=(60, 100, 180, 255), width=2)
    d.ellipse((28, 30, 36, 38), fill=(30, 60, 120, 255))
    return img


def _exit() -> Image.Image:
    img, d = _canvas()
    d.rectangle((4, 4, 60, 60), fill=(60, 200, 90, 255), outline=(30, 140, 60, 255), width=2)
    d.polygon([(20, 32), (40, 18), (40, 27), (48, 27), (48, 37), (40, 37), (40, 46)], fill=(255, 255, 255, 255))
    return img


def _hazard() -> Image.Image:
    img, d = _canvas()
    d.polygon([(32, 6), (58, 56), (6, 56)], fill=(220, 20, 60, 255), outline=(140, 10, 35, 255))
    d.rectangle((29, 24, 35, 40), fill=(255, 255, 255, 255))
    d.rectangle((29, 44, 35, 50), fill=(255, 255, 255, 255))
    return img


def _distractor() -> Image.Image:
    img, d = _canvas()
    d.polygon([(32, 4), (60, 32), (32, 60), (4, 32)], fill=(186, 85, 211, 255), outline=(120, 50, 140, 255))
    return img


BUILDERS = {
    "agent": _agent,
    "wall": _wall,
    "floor": _floor,
    "table": _table,
    "can": _can,
    "box": _box,
    "key": _key,
    "door": _door,
    "package": _package,
    "sink": _sink,
    "exit": _exit,
    "hazard": _hazard,
    "distractor": _distractor,
}


def base_assets_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "base")


def generate_all_placeholders(out_dir: str | None = None) -> list[str]:
    out_dir = out_dir or base_assets_dir()
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, builder in BUILDERS.items():
        path = os.path.join(out_dir, f"{name}.png")
        builder().save(path)
        written.append(path)
    return written


if __name__ == "__main__":
    for path in generate_all_placeholders():
        print("wrote", path)
