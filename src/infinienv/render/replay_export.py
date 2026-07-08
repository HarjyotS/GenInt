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


def build_replay_frames(scene: SceneSpec, actions: list[dict]) -> list[Image.Image]:
    grid = Grid(scene)
    state = GameState.from_scene(scene)

    frames = [
        render_scene_image(
            scene,
            agent_pos=state.agent_pos(),
            inventory=list(state.inventory),
            object_positions=_object_positions(state),
            title=f"t=0 start",
        )
    ]
    for i, action in enumerate(actions, start=1):
        apply_action(state, grid, action)
        frames.append(
            render_scene_image(
                scene,
                agent_pos=state.agent_pos(),
                inventory=list(state.inventory),
                object_positions=_object_positions(state),
                title=f"t={i} {action['action']}",
            )
        )
    return frames


def save_replay_gif(scene: SceneSpec, actions: list[dict], out_path: str, *, frame_duration_ms: int = 220) -> None:
    frames = build_replay_frames(scene, actions)
    if len(frames) == 1:
        frames.append(frames[0])
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=0,
    )
