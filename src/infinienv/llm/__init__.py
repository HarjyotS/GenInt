"""Provider registry. Each provider is imported lazily so `mock` never requires optional deps."""

from __future__ import annotations

from infinienv.llm.base import ProviderError, SceneProvider

PROVIDER_NAMES = ("mock", "openai_agents", "openai_responses", "anthropic")


def get_provider(name: str) -> SceneProvider:
    if name == "mock":
        from infinienv.llm.providers.mock import MockProvider

        return MockProvider()
    if name == "openai_agents":
        from infinienv.llm.providers.openai_agents import OpenAIAgentsProvider

        return OpenAIAgentsProvider()
    if name == "openai_responses":
        from infinienv.llm.providers.openai_responses import OpenAIResponsesProvider

        return OpenAIResponsesProvider()
    if name == "anthropic":
        from infinienv.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider()
    raise ProviderError(f"unknown provider {name!r}. Supported: {', '.join(PROVIDER_NAMES)}")
