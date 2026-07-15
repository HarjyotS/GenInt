"""A stand-in vision-based policy: sees only the rendered frame, emits a controller action.

General Intuition's own vision policy isn't available to us, so this uses a VLM (OpenAI by
default, Claude optional) as a **stand-in** to demonstrate the *interface and the code-reward
loop* -- not to compete with their policy. The policy is handed the current frame (pixels)
plus the task's goal in words (the brief's "specific goals and rewards") and returns one of
`engine.env.CONTROLLER_ACTIONS`. It never sees `GameState`; whether it succeeded is decided by
code (`InfiniEnv`'s reward), which is the whole point.

Network access is isolated behind a `responder` callable `(system, user_text, image_png) -> str`
so the loop is fully testable without an API key: tests inject a fake responder. Missing keys /
packages surface as `ProviderError`, matching every other provider in this project.
"""

from __future__ import annotations

import base64
import os
from typing import Callable

from infinienv.engine.env import CONTROLLER_ACTIONS
from infinienv.llm.base import ProviderError
from infinienv.llm.providers.openai_agents import _load_prompt

DEFAULT_OPENAI_VISION_MODEL = "gpt-5.6-terra"
DEFAULT_CLAUDE_VISION_MODEL = "claude-sonnet-5"

# How many actions a single vision call may plan ahead. The policy sees one frame and returns an
# ordered plan of up to this many controller actions; the driver executes them in order (re-looking
# early if a move is blocked) before asking again. This is what makes vision play cost ~PLAN_LEN
# fewer model calls per episode than one-keystroke-per-call, for the same gameplay length.
PLAN_LEN = int(os.environ.get("INFINIENV_VISION_PLAN_LEN", "6"))

# The type of `responder`: given the system prompt, the user text, and one-or-more frames as PNG
# bytes (the current frame plus any recent history), return the model's raw text reply.
Responder = Callable[[str, str, "list[bytes]"], str]


def _parse_actions(raw: str, *, limit: int = PLAN_LEN) -> list[str]:
    """Extract an ordered *plan* of controller actions from the model's reply (the moves it wants to
    commit to before seeing the next frame). Whole-word matches, in the order they appear, capped at
    `limit`; immediate repeats are kept (`right right` is a legal two-step plan). Returns `['wait']`
    (a safe no-op) if nothing matches, so a garbled reply never stalls the loop."""
    import re

    text = (raw or "").lower()
    plan: list[str] = []
    # Scan token occurrences in appearance order (not CONTROLLER_ACTIONS order), so the plan follows
    # the sequence the model actually wrote.
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in CONTROLLER_ACTIONS) + r")\b")
    for match in pattern.finditer(text):
        plan.append(match.group(1))
        if len(plan) >= max(1, limit):
            break
    return plan or ["wait"]


def _parse_action(raw: str) -> str:
    """The first action of the parsed plan -- a single controller token, else 'wait'. Kept for
    callers/tests that want just one action."""
    return _parse_actions(raw, limit=1)[0]


def _as_image_list(images) -> list[bytes]:
    """Normalize a responder's image argument to a list of PNG-bytes (tolerating a single frame)."""
    if isinstance(images, (bytes, bytearray)):
        return [bytes(images)]
    return [bytes(i) for i in images]


def build_feedback(recent, last_outcomes, looping: bool, actions) -> str:
    """The short word-feedback handed to the pixel policy each look (proprioceptive 'did I move / am
    I stuck' signal a real embodied policy gets -- it still decides from the frames). `recent` is a
    deque/list of (action, moved) over the last several actions; `last_outcomes` is the just-executed
    plan's [(action, moved), ...]; `looping` flags no-net-progress / revisits. Mirrors the sandbox
    driver's `_feedback_text` (kept separate because that file is copied standalone into a sandbox)."""
    lines: list[str] = []
    if last_outcomes:
        parts = [f"{a}({'moved' if m else 'BLOCKED'})" for a, m in last_outcomes]
        lines.append("Last plan result: " + " ".join(parts) + ".")
    blocked_counts: dict = {}
    for a, m in recent:
        if not m:
            blocked_counts[a] = blocked_counts.get(a, 0) + 1
    repeated = [a for a, c in blocked_counts.items() if c >= 2]
    if repeated:
        lines.append("These moves keep hitting a wall -- do NOT repeat them: " + ", ".join(repeated) + ".")
    if looping:
        lines.append("You are STUCK / oscillating over the same few cells and not winning. If moving "
                     "isn't working, you probably need to INTERACT (pick up / use / place an item), "
                     "not just move -- or head somewhere you have not been.")
    untried = [a for a in actions if a not in blocked_counts]
    if (repeated or looping) and untried:
        lines.append("Options include: " + ", ".join(untried[:6]) + ".")
    return " ".join(lines)


def _openai_responder(model: str) -> Responder:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise ProviderError(
            "The 'openai' package is not installed. Install it with `pip install infinienv[openai]`."
        ) from exc
    if not os.environ.get("OPENAI_API_KEY"):
        raise ProviderError(
            "No OpenAI key set. Put OPENAI_API_KEY (or OP_KEY) in .env, or use --vision-backend claude."
        )
    client = OpenAI()

    def respond(system: str, user_text: str, images: list[bytes]) -> str:
        content = [{"type": "input_text", "text": user_text}]
        for img in _as_image_list(images):
            b64 = base64.b64encode(img).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
        try:
            response = client.responses.create(
                model=model,
                instructions=system,
                input=[{"role": "user", "content": content}],
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a ProviderError with context
            raise ProviderError(f"OpenAI vision call failed: {exc}") from exc
        return response.output_text

    return respond


def _claude_responder(model: str) -> Responder:
    api_key = os.environ.get("CL_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderError(
            "No Anthropic key set. Put it in .env as CL_KEY (InfiniEnv's name for it) or export "
            "ANTHROPIC_API_KEY, or use --vision-backend openai."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise ProviderError(
            "The 'anthropic' package is not installed. Install it with `pip install infinienv[anthropic]`."
        ) from exc
    client = anthropic.Anthropic(api_key=api_key)

    def respond(system: str, user_text: str, images: list[bytes]) -> str:
        content: list = [{"type": "text", "text": user_text}]
        for img in _as_image_list(images):
            b64 = base64.b64encode(img).decode("ascii")
            content.append(
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}
            )
        try:
            response = client.messages.create(
                model=model,
                max_tokens=64,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Anthropic vision call failed: {exc}") from exc
        return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")

    return respond


def _build_responder(backend: str, model: str) -> Responder:
    if backend == "openai":
        return _openai_responder(model)
    if backend == "claude":
        return _claude_responder(model)
    raise ProviderError(f"unknown vision backend {backend!r}; expected 'openai' or 'claude'")


class VisionPolicy:
    """Picks a controller action from a rendered frame and a goal description."""

    def __init__(
        self,
        *,
        backend: str | None = None,
        model: str | None = None,
        responder: Responder | None = None,
    ) -> None:
        self.backend = backend or os.environ.get("INFINIENV_VISION_BACKEND", "openai")
        default_model = DEFAULT_OPENAI_VISION_MODEL if self.backend == "openai" else DEFAULT_CLAUDE_VISION_MODEL
        self.model = model or os.environ.get("INFINIENV_VISION_MODEL", default_model)
        self.system = _load_prompt("vision_navigator.md")
        # Built lazily so constructing a VisionPolicy never requires a key (only calling it does),
        # and so tests can inject a fake responder with no network at all.
        self._responder = responder

    def _respond(self, system: str, user_text: str, images) -> str:
        if self._responder is None:
            self._responder = _build_responder(self.backend, self.model)
        return self._responder(system, user_text, images)

    def act(
        self,
        frames_png,
        goal_text: str,
        *,
        step: int,
        feedback: str = "",
        max_actions: int = PLAN_LEN,
    ) -> tuple[list[str], str]:
        """Return (plan, raw_model_reply): an ordered list of up to `max_actions` controller actions
        to execute before the next look. `frames_png` is the current frame plus any recent history
        (`[current, prev, ...]`, or a single frame); `feedback` is the driver's word summary (blocked
        moves / stuck warning). Never returns an empty plan (falls back to ['wait'])."""
        frames = _as_image_list(frames_png)
        lines = [f"Goal: {goal_text}", f"Turn: {step}"]
        if feedback:
            lines.append(feedback)
        if len(frames) > 1:
            lines.append(f"You see {len(frames)} frames (the first is NOW, the rest are previous).")
        lines.append(f"Plan your next 1 to {max_actions} moves.")
        raw = self._respond(self.system, "\n".join(lines), frames)
        return _parse_actions(raw, limit=max_actions), raw

    def judge_final_frame(self, frame_png: bytes, goal_text: str) -> tuple[bool, str]:
        """Ask the VLM to judge, *from the final frame alone*, whether the goal was accomplished.

        This is the deliberate contrast to `InfiniEnv`'s code-defined reward: it's the "use a VLM
        on pixels to check whether something happened" approach the brief calls out as *less
        reliable* than code-level objectives. When it disagrees with the code truth, that
        disagreement is the demonstration. Best-effort -- any failure returns (False, reason)."""
        system = (
            "You judge whether a 2D game goal has been accomplished, looking ONLY at the final frame. "
            "The character is the blue circle / 'agent' sprite. Answer with a single word: YES or NO."
        )
        user = (
            f"Goal: {goal_text}\n"
            "Looking only at this final frame, has the goal been accomplished? Answer YES or NO."
        )
        try:
            raw = self._respond(system, user, frame_png)
        except ProviderError as exc:
            return False, f"judge unavailable: {exc}"
        verdict = "yes" in (raw or "").strip().lower()[:5]
        return verdict, raw
