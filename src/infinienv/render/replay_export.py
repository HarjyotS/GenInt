"""Replays an action trace against the scene and exports it as an animated GIF."""

from __future__ import annotations

from PIL import Image

from infinienv.engine.actions import apply_action
from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState
from infinienv.render.image_export import render_scene_image
from infinienv.schema.scene_schema import SceneSpec


def _object_positions(state: GameState) -> dict[str, tuple[int, int] | None]:
    return {oid: (None if o.held else (o.x, o.y)) for oid, o in state.objects.items()}


def _slide_cells(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    """The intermediate cells strictly between `start` and `end` along their shared axis
    (empty for a one-cell or diagonal/zero move). Physics slides are always axis-aligned."""
    (sx, sy), (ex, ey) = start, end
    if sx == ex:
        step = 1 if ey > sy else -1
        return [(sx, y) for y in range(sy + step, ey, step)]
    if sy == ey:
        step = 1 if ex > sx else -1
        return [(x, sy) for x in range(sx + step, ex, step)]
    return []


def build_replay_frames(
    scene: SceneSpec, actions: list[dict], *, asset_paths: dict[str, str] | None = None
) -> list[Image.Image]:
    grid = Grid(scene)
    state = GameState.from_scene(scene)

    frames = [
        render_scene_image(
            scene,
            agent_pos=state.agent_pos(),
            inventory=list(state.inventory),
            object_positions=_object_positions(state),
            title="t=0 start",
            asset_paths=asset_paths,
        )
    ]
    for i, action in enumerate(actions, start=1):
        before = _object_positions(state)
        apply_action(state, grid, action, scene)
        after = _object_positions(state)

        # A slippery push moves an object several cells in a single action. Render the object
        # gliding through each intermediate cell (with the agent already at its final cell) so
        # the slide reads as smooth motion, not a teleport -- otherwise the replay would show a
        # multi-cell jump in one frame. Objects that moved only one cell need no interpolation.
        slides = {
            oid: _slide_cells(before[oid], after[oid])
            for oid in after
            if before.get(oid) is not None and after[oid] is not None and _slide_cells(before[oid], after[oid])
        }
        if slides:
            span = max(len(cells) for cells in slides.values())
            for k in range(span):
                mid = dict(after)
                for oid, cells in slides.items():
                    mid[oid] = cells[k] if k < len(cells) else after[oid]
                frames.append(
                    render_scene_image(
                        scene,
                        agent_pos=state.agent_pos(),
                        inventory=list(state.inventory),
                        object_positions=mid,
                        title=f"t={i} {action['action']} (slide)",
                        asset_paths=asset_paths,
                    )
                )

        frames.append(
            render_scene_image(
                scene,
                agent_pos=state.agent_pos(),
                inventory=list(state.inventory),
                object_positions=after,
                title=f"t={i} {action['action']}",
                asset_paths=asset_paths,
            )
        )
    return frames


def save_replay_gif(
    scene: SceneSpec,
    actions: list[dict],
    out_path: str,
    *,
    frame_duration_ms: int = 220,
    asset_paths: dict[str, str] | None = None,
) -> None:
    frames = build_replay_frames(scene, actions, asset_paths=asset_paths)
    if len(frames) == 1:
        frames.append(frames[0])
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=0,
    )
