"""A Gymnasium-compatible pixel-observation environment over a SceneSpec.

This is the bridge to a vision-based policy. General Intuition's policy observes
*rendered frames* and emits *controller actions* (move forward/back/left/right,
mouse deltas); this env exposes exactly that shape for a 2D scene: observations
are rendered frames (what a camera-mounted policy would see), actions are a small
discrete controller set.

Crucially, the reward is still computed in **code** from `GameState` via
`navigation.planner.is_goal_complete` -- never from the pixels. So the project's
core rule (CLAUDE.md section 2: "Use AI for semantic generation, deterministic
code for truth") is preserved: only the *policy that plays* becomes pixel-based;
whether it *succeeded* is decided by deterministic code, exactly as the brief's
"code-level objectives beat a VLM on pixels" thesis wants.

The API mirrors Gymnasium's without a hard dependency on it:
    obs, info = env.reset()
    obs, reward, terminated, truncated, info = env.step(action)
`obs` is a PIL.Image (the frame); `env.frames` accumulates every observed frame so
a caller can save the episode as a GIF.
"""

from __future__ import annotations

from PIL import Image

from infinienv.engine.actions import ActionError, apply_action
from infinienv.engine.grid import Grid
from infinienv.engine.state import GameState
from infinienv.navigation.planner import is_goal_complete
from infinienv.render.image_export import render_scene_image
from infinienv.schema.scene_schema import SceneSpec

# The 2D subset of the policy's controller interface. Forward/back/left/right map to the
# four grid moves; `interact` is a single "use" button the env resolves to whatever engine
# action is legal here (pick up, drop, unlock, or a custom interaction); `wait` is a no-op.
# 2D top-down has no mouse deltaX/deltaY (a 3D look-direction concept) -- the interface is
# shaped so a 6-DoF controller slots in when the schema goes 3D (CLAUDE.md section 18).
CONTROLLER_ACTIONS: tuple[str, ...] = ("forward", "back", "left", "right", "interact", "wait")

_MOVE = {
    "forward": "move_up",
    "back": "move_down",
    "left": "move_left",
    "right": "move_right",
}


def _object_positions(state: GameState) -> dict[str, tuple[int, int] | None]:
    """Live object positions for rendering; None means the object is currently held.

    Kept identical to render.replay_export's helper so a frame from this env and a frame
    from a replay are drawn the same way."""
    return {oid: (None if o.held else (o.x, o.y)) for oid, o in state.objects.items()}


def _describe(action: dict) -> str:
    kind = action["action"]
    for key in ("object_id", "door_id", "target_id"):
        if key in action:
            return f"{kind} {action[key]}"
    return kind


def frame_to_png_bytes(img: Image.Image) -> bytes:
    """Encode a rendered frame as PNG bytes (what a vision policy is handed)."""
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class InfiniEnv:
    """A single-scene episode. Reward is code-defined; observations are rendered frames."""

    metadata = {"render_modes": ["rgb_array"]}
    controller_actions = CONTROLLER_ACTIONS

    def __init__(
        self,
        scene: SceneSpec,
        *,
        max_steps: int | None = None,
        asset_paths: dict[str, str] | None = None,
    ) -> None:
        self.scene = scene
        self.grid = Grid(scene)
        self.asset_paths = asset_paths or {}
        self.max_steps = max_steps if max_steps is not None else self._default_max_steps()
        self._state: GameState | None = None
        self._steps = 0
        self._rewarded: set[str] = set()
        self.frames: list[Image.Image] = []

    def _default_max_steps(self) -> int:
        w, h = self.scene.grid.width, self.scene.grid.height
        # Enough budget to cross the grid several times per goal, bounded so a wandering
        # pixel policy can't run forever.
        return max(40, (w + h) * 3 * max(1, len(self.scene.goals)))

    @property
    def state(self) -> GameState:
        if self._state is None:
            raise RuntimeError("call reset() before accessing state")
        return self._state

    def _goal_completion(self) -> dict[str, bool]:
        return {g.id: is_goal_complete(g, self.state) for g in self.scene.goals}

    def _render(self, title: str) -> Image.Image:
        return render_scene_image(
            self.scene,
            agent_pos=self.state.agent_pos(),
            inventory=list(self.state.inventory),
            object_positions=_object_positions(self.state),
            title=title,
            asset_paths=self.asset_paths,
        )

    def _info(self, *, resolved: str | None, legal: bool, error: str | None) -> dict:
        goals = self._goal_completion()
        return {
            "steps": self._steps,
            "resolved_action": resolved,
            "action_legal": legal,
            "error": error,
            "goals": goals,
            "all_complete": bool(goals) and all(goals.values()),
        }

    def reset(self) -> tuple[Image.Image, dict]:
        self._state = GameState.from_scene(self.scene)
        self._steps = 0
        # A goal that is already satisfied at spawn should not later pay out a reward.
        self._rewarded = {gid for gid, done in self._goal_completion().items() if done}
        self.frames = []
        obs = self._render("t=0 start")
        self.frames.append(obs)
        return obs, self._info(resolved="reset", legal=True, error=None)

    def _resolve_interact(self) -> dict | None:
        """Translate the generic `interact` button into whatever concrete engine action is
        legal in the current state. Deterministic code -- the policy only decides *when* to
        press interact, not what object ids exist (it can't; it only sees pixels)."""
        st = self.state
        held_types = {st.objects[i].type for i in st.inventory if i in st.objects}

        # 1. Unlock an adjacent locked door we hold the key for.
        for oid, o in st.objects.items():
            if o.locked and o.key_id and o.key_id in st.inventory and st.is_adjacent_or_same(o.x, o.y):
                return {"action": "unlock", "door_id": oid, "key_id": o.key_id}

        # 2. Perform a custom interaction we satisfy the precondition for, against an adjacent target.
        for inter in self.scene.mechanics.custom_interactions:
            if inter.must_hold_type and inter.must_hold_type not in held_types:
                continue
            for oid, o in st.objects.items():
                if o.type == inter.target_type and not o.held and st.is_adjacent_or_same(o.x, o.y):
                    return {"action": inter.trigger_action, "target_id": oid}

        # 3. Pick up an adjacent portable object we aren't already holding.
        for oid, o in st.objects.items():
            if o.portable and not o.held and st.is_adjacent_or_same(o.x, o.y):
                return {"action": "pick_up", "object_id": oid}

        # 4. Otherwise, drop a held object at the current cell (this is how `deliver`/`push`-style
        #    "put the thing on the target" is accomplished: stand on the target, press interact).
        if st.inventory:
            return {"action": "drop", "object_id": st.inventory[-1]}

        return None

    def step(self, action: str) -> tuple[Image.Image, float, bool, bool, dict]:
        if self._state is None:
            raise RuntimeError("call reset() before step()")
        if action not in CONTROLLER_ACTIONS:
            raise ValueError(f"unknown controller action {action!r}; expected one of {CONTROLLER_ACTIONS}")

        self._steps += 1
        legal = True
        error: str | None = None

        if action in _MOVE:
            resolved = {"action": _MOVE[action]}
        elif action == "interact":
            resolved = self._resolve_interact()
        else:  # wait
            resolved = {"action": "wait"}

        if resolved is None:
            # `interact` with nothing to act on: a harmless no-op, recorded honestly.
            resolved_desc = "interact (nothing to interact with)"
        else:
            try:
                apply_action(self._state, self.grid, resolved, self.scene)
                resolved_desc = _describe(resolved)
            except ActionError as exc:
                # An illegal move (into a wall, etc.) is a no-op, not a crash -- exactly what a
                # controller pressing "forward" into a wall does. Recorded in info, not rewarded.
                legal = False
                error = str(exc)
                resolved_desc = f"{_describe(resolved)} (blocked)"

        # Reward = number of goals that *newly* completed this step (code-defined truth).
        completion = self._goal_completion()
        newly = [gid for gid, done in completion.items() if done and gid not in self._rewarded]
        self._rewarded.update(newly)
        reward = float(len(newly))

        terminated = bool(completion) and all(completion.values())
        truncated = (not terminated) and self._steps >= self.max_steps

        obs = self._render(f"t={self._steps} {action}")
        self.frames.append(obs)
        info = self._info(resolved=resolved_desc, legal=legal, error=error)
        return obs, reward, terminated, truncated, info
