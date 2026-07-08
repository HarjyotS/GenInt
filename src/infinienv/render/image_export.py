"""Static top-down PNG renderer for a SceneSpec / GameState snapshot."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from infinienv.schema.scene_schema import SceneSpec

CELL_PX = 28
LEGEND_W = 180
MARGIN = 10

COLORS: dict[str, tuple[int, int, int]] = {
    "wall": (70, 70, 70),
    "floor": (240, 240, 240),
    "table": (150, 111, 51),
    "can": (176, 176, 176),
    "box": (181, 136, 99),
    "key": (255, 200, 0),
    "door": (139, 69, 19),
    "package": (222, 184, 135),
    "sink": (100, 149, 237),
    "exit": (60, 200, 90),
    "hazard": (220, 20, 60),
    "distractor": (186, 85, 211),
    "agent": (30, 100, 240),
}
BACKGROUND = (255, 255, 255)
GRID_LINE = (220, 220, 220)


def _font() -> ImageFont.ImageFont:
    return ImageFont.load_default()


def _load_sprite(path: str, cache: dict[str, Image.Image]) -> Image.Image | None:
    if path in cache:
        return cache[path]
    try:
        sprite = Image.open(path).convert("RGBA").resize((CELL_PX, CELL_PX))
    except (FileNotFoundError, OSError):
        return None
    cache[path] = sprite
    return sprite


def render_scene_image(
    scene: SceneSpec,
    *,
    agent_pos: tuple[int, int] | None = None,
    inventory: list[str] | None = None,
    object_positions: dict[str, tuple[int, int] | None] | None = None,
    title: str | None = None,
    asset_paths: dict[str, str] | None = None,
) -> Image.Image:
    """Render one frame. `object_positions[id] = None` means the object is currently held.

    `asset_paths` optionally maps an object `type` (plus the special keys "wall" and
    "agent") to a sprite PNG path (see `assets/resolver.py`); types without an entry
    fall back to the flat colored-cell rendering, so this always degrades gracefully.
    """
    grid_w, grid_h = scene.grid.width, scene.grid.height
    inventory = inventory or []
    object_positions = object_positions or {obj.id: (obj.x, obj.y) for obj in scene.objects}
    agent_pos = agent_pos or (scene.agent.x, scene.agent.y)
    asset_paths = asset_paths or {}
    sprite_cache: dict[str, Image.Image] = {}

    img_w = grid_w * CELL_PX + LEGEND_W + MARGIN * 3
    img_h = max(grid_h * CELL_PX, 200) + MARGIN * 2 + (24 if title else 0)
    img = Image.new("RGB", (img_w, img_h), BACKGROUND)
    draw = ImageDraw.Draw(img)
    font = _font()

    top = MARGIN + (20 if title else 0)
    if title:
        draw.text((MARGIN, MARGIN), title, fill=(0, 0, 0), font=font)

    def cell_box(x: int, y: int) -> tuple[int, int, int, int]:
        x0 = MARGIN + x * CELL_PX
        y0 = top + y * CELL_PX
        return x0, y0, x0 + CELL_PX, y0 + CELL_PX

    def draw_sprite_or_fallback(box: tuple[int, int, int, int], asset_key: str, fallback) -> bool:
        path = asset_paths.get(asset_key)
        sprite = _load_sprite(path, sprite_cache) if path else None
        if sprite is None:
            fallback()
            return False
        img.paste(sprite, (box[0], box[1]), sprite)
        return True

    for gx in range(grid_w):
        for gy in range(grid_h):
            draw.rectangle(cell_box(gx, gy), fill=COLORS["floor"], outline=GRID_LINE)

    for wall in scene.walls:
        box = cell_box(wall.x, wall.y)
        draw_sprite_or_fallback(box, "wall", lambda box=box: draw.rectangle(box, fill=COLORS["wall"], outline=GRID_LINE))

    types_present: set[str] = set()
    for obj in scene.objects:
        pos = object_positions.get(obj.id)
        if pos is None:
            continue  # held by agent, drawn as part of the agent marker
        types_present.add(obj.type)
        box = cell_box(*pos)
        color = COLORS.get(obj.type, (128, 128, 128))
        label = obj.type[:1].upper()

        def fallback(box=box, color=color, label=label) -> None:
            draw.rectangle(box, fill=color, outline=(0, 0, 0))
            draw.text((box[0] + 3, box[1] + 3), label, fill=(255, 255, 255), font=font)

        draw_sprite_or_fallback(box, obj.type, fallback)

    agent_box = cell_box(*agent_pos)

    def agent_fallback(box=agent_box) -> None:
        draw.ellipse((box[0] + 3, box[1] + 3, box[2] - 3, box[3] - 3), fill=COLORS["agent"], outline=(0, 0, 0))

    draw_sprite_or_fallback(agent_box, "agent", agent_fallback)
    if inventory:
        draw.text((agent_box[0] + 3, agent_box[3] + 2), f"+{len(inventory)}", fill=(30, 100, 240), font=font)

    legend_x = MARGIN * 2 + grid_w * CELL_PX
    ly = top
    draw.text((legend_x, ly), "Legend", fill=(0, 0, 0), font=font)
    ly += 16
    draw.rectangle((legend_x, ly, legend_x + 14, ly + 14), fill=COLORS["agent"])
    draw.text((legend_x + 20, ly), "agent", fill=(0, 0, 0), font=font)
    ly += 18
    draw.rectangle((legend_x, ly, legend_x + 14, ly + 14), fill=COLORS["wall"])
    draw.text((legend_x + 20, ly), "wall", fill=(0, 0, 0), font=font)
    ly += 18
    for t in sorted(types_present):
        draw.rectangle((legend_x, ly, legend_x + 14, ly + 14), fill=COLORS.get(t, (128, 128, 128)))
        draw.text((legend_x + 20, ly), t, fill=(0, 0, 0), font=font)
        ly += 18
    if inventory:
        ly += 6
        draw.text((legend_x, ly), f"inventory: {', '.join(inventory)}", fill=(0, 0, 0), font=font)

    return img


def save_render_png(
    scene: SceneSpec, out_path: str, *, title: str | None = None, asset_paths: dict[str, str] | None = None
) -> None:
    img = render_scene_image(scene, title=title, asset_paths=asset_paths)
    img.save(out_path)
