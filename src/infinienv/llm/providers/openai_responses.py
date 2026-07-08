"""Lower-level fallback provider: a direct OpenAI Responses API call, no agent orchestration."""

from __future__ import annotations

import json
import os

from infinienv.llm.base import ProviderError
from infinienv.llm.providers.openai_agents import _extract_json, _load_prompt
from infinienv.schema.scene_schema import SceneSpec, scene_spec_from_dict, scene_spec_json_schema
from infinienv.validation.errors import ValidationIssue


def _strict_scene_schema() -> dict:
    # Reuses the Agents SDK's strict-schema converter (all-required + no bare
    # additionalProperties) so the Responses API's structured-output mode accepts it.
    from agents.strict_schema import ensure_strict_json_schema

    return ensure_strict_json_schema(scene_spec_json_schema())


class OpenAIResponsesProvider:
    name = "openai_responses"

    def __init__(self, model: str | None = None):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError(
                "OPENAI_API_KEY is not set. Export it (or set OP_KEY / put it in .env) or use --provider mock."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError(
                "The 'openai' package is not installed. Install it with `pip install infinienv[openai]`."
            ) from exc
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4.1")
        self.client = OpenAI()

    def _call(self, instructions: str, user_message: str) -> dict:
        text_config = None
        try:
            text_config = {
                "format": {"type": "json_schema", "name": "SceneSpec", "schema": _strict_scene_schema(), "strict": True}
            }
        except ImportError:
            pass  # openai-agents not installed; fall back to prompt-only JSON extraction below.

        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=user_message,
                **({"text": text_config} if text_config else {}),
            )
        except Exception as exc:
            raise ProviderError(f"OpenAI Responses API call failed: {exc}") from exc
        return _extract_json(response.output_text)

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
