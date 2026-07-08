"""Optional Claude provider. Direct Messages API call, no agent orchestration.

Kept to the same provider protocol as the OpenAI paths so the benchmark can compare
providers without touching engine code, per CLAUDE.md section 11. Not the default
runtime -- OpenAI Agents SDK is -- so this intentionally stays simple: one prompt
asking for strict JSON, parsed the same way as the openai_responses fallback.
"""

from __future__ import annotations

import json
import os

from infinienv.llm.base import ProviderError
from infinienv.llm.providers.openai_agents import _extract_json, _load_prompt
from infinienv.schema.scene_schema import SceneSpec, scene_spec_from_dict
from infinienv.validation.errors import ValidationIssue


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set. Export it or use --provider mock for a no-key demo."
            )
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "The 'anthropic' package is not installed. Install it with `pip install infinienv[anthropic]`."
            ) from exc
        self.model = model or os.environ.get("LLM_MODEL", "claude-sonnet-5")
        self.client = anthropic.Anthropic()

    def _call(self, instructions: str, user_message: str) -> dict:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=instructions,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            raise ProviderError(f"Anthropic Messages API call failed: {exc}") from exc
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        return _extract_json(text)

    def generate_scene(self, prompt: str, seed: int) -> SceneSpec:
        instructions = _load_prompt("scene_planner.md")
        user_message = f"Seed: {seed}\nTask: {prompt}\n\nReturn only SceneSpec JSON."
        return scene_spec_from_dict(self._call(instructions, user_message))

    def repair_scene(
        self,
        prompt: str,
        scene: SceneSpec,
        validation_errors: list[ValidationIssue],
        seed: int,
    ) -> SceneSpec:
        instructions = _load_prompt("repair_agent.md")
        errors_text = "\n".join(f"- {e.code}: {e.message}" for e in validation_errors)
        user_message = (
            f"Original task: {prompt}\n"
            f"Seed: {seed}\n"
            f"Previous SceneSpec:\n{json.dumps(scene.model_dump())}\n\n"
            f"Validator errors:\n{errors_text}\n\n"
            "Return only the repaired SceneSpec JSON."
        )
        return scene_spec_from_dict(self._call(instructions, user_message))
