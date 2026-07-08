"""Default runtime provider: OpenAI Agents SDK.

The agent proposes SceneSpec JSON and may call deterministic tools (schema lookup,
mechanics lookup, validation) but never executes arbitrary code and never writes
project files directly -- this module owns parsing, and the caller (generation.compiler)
owns validation, retries, and artifact writing.
"""

from __future__ import annotations

import json
import os
from importlib import resources

from infinienv.llm.base import ProviderError
from infinienv.schema.scene_schema import (
    ACTION_TYPES,
    GOAL_TYPES,
    OBJECT_TYPES,
    SceneSpec,
    scene_spec_from_dict,
    scene_spec_json_schema,
)
from infinienv.validation.errors import ValidationIssue


def _load_prompt(filename: str) -> str:
    return resources.files("infinienv.llm.prompts").joinpath(filename).read_text()


def _extract_json(output) -> dict:
    if isinstance(output, dict):
        return output
    if not isinstance(output, str):
        raise ProviderError(f"unexpected agent output type: {type(output)!r}")
    text = output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"model did not return valid JSON: {exc}. Output was: {text[:500]!r}") from exc


class OpenAIAgentsProvider:
    name = "openai_agents"

    def __init__(self, model: str | None = None):
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError(
                "OPENAI_API_KEY is not set. Export it (or set OP_KEY / put it in .env) or use --provider mock."
            )
        try:
            import agents  # noqa: F401
        except ImportError as exc:
            raise ProviderError(
                "The 'openai-agents' package is not installed. Install it with `pip install infinienv[openai]`."
            ) from exc
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4.1")
        self.max_turns = int(os.environ.get("LLM_MAX_TURNS", "8"))

    # -- shared function tools -------------------------------------------------

    def _tools(self):
        from agents import function_tool

        @function_tool
        def get_scene_schema() -> dict:
            """Return the current SceneSpec JSON schema."""
            return scene_spec_json_schema()

        @function_tool
        def get_supported_mechanics() -> dict:
            """Return supported object types, action types, and goal types."""
            return {
                "object_types": sorted(OBJECT_TYPES),
                "action_types": sorted(ACTION_TYPES),
                "goal_types": sorted(GOAL_TYPES),
            }

        @function_tool(strict_mode=False)
        def validate_scene_tool(scene_spec: dict) -> dict:
            """Validate schema, geometry, reachability, and solvability for a candidate scene."""
            from infinienv.validation.validator import validate_scene_dict

            return validate_scene_dict(scene_spec).to_dict()

        return [get_scene_schema, get_supported_mechanics, validate_scene_tool]

    def _run(self, agent, user_message: str) -> SceneSpec:
        from agents import Runner

        try:
            result = Runner.run_sync(agent, user_message, max_turns=self.max_turns)
        except Exception as exc:  # SDK/network/auth failures should surface as ProviderError
            raise ProviderError(f"OpenAI Agents SDK run failed: {exc}") from exc
        output = result.final_output
        # output_type=SceneSpec asks the SDK for structured output matching our schema
        # directly, so final_output is usually already a parsed SceneSpec instance.
        if isinstance(output, SceneSpec):
            return output
        return scene_spec_from_dict(_extract_json(output))

    # -- provider protocol -------------------------------------------------

    def generate_scene(self, prompt: str, seed: int) -> SceneSpec:
        from agents import Agent

        agent = Agent(
            name="ScenePlannerAgent",
            instructions=_load_prompt("scene_planner.md"),
            tools=self._tools(),
            model=self.model,
            output_type=SceneSpec,
        )
        user_message = f"Seed: {seed}\nTask: {prompt}\n\nReturn only SceneSpec JSON."
        return self._run(agent, user_message)

    def repair_scene(
        self,
        prompt: str,
        scene: SceneSpec,
        validation_errors: list[ValidationIssue],
        seed: int,
    ) -> SceneSpec:
        from agents import Agent

        agent = Agent(
            name="RepairAgent",
            instructions=_load_prompt("repair_agent.md"),
            tools=self._tools(),
            model=self.model,
            output_type=SceneSpec,
        )
        errors_text = "\n".join(f"- {e.code}: {e.message}" for e in validation_errors)
        user_message = (
            f"Original task: {prompt}\n"
            f"Seed: {seed}\n"
            f"Previous SceneSpec:\n{json.dumps(scene.model_dump())}\n\n"
            f"Validator errors:\n{errors_text}\n\n"
            "Return only the repaired SceneSpec JSON."
        )
        return self._run(agent, user_message)

    def propose_mutation(self, scene: SceneSpec, seed: int) -> SceneSpec:
        """Optional capability (duck-typed, not part of SceneProvider): a creative,
        LLM-proposed variant of `scene`. Callers (generation.mutation) must still validate
        the result the same as any deterministic mutation -- this only proposes."""
        from agents import Agent

        agent = Agent(
            name="MutationAgent",
            instructions=_load_prompt("mutation_agent.md"),
            tools=self._tools(),
            model=self.model,
            output_type=SceneSpec,
        )
        user_message = f"Seed: {seed}\nBase SceneSpec:\n{json.dumps(scene.model_dump())}\n\nReturn only the mutated SceneSpec JSON."
        return self._run(agent, user_message)
