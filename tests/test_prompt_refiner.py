"""Best-effort prompt enrichment before the sandbox handoff (sandbox/prompt_refiner.py)."""

from types import SimpleNamespace

from infinienv.sandbox.prompt_refiner import refine_prompt


def _install_fake_openai(monkeypatch, *, output_text="ENRICHED SPEC", capture=None):
    class FakeResponses:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            return SimpleNamespace(output_text=output_text)

    class FakeClient:
        def __init__(self, *a, **kw):
            self.responses = FakeResponses()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")


def test_refine_prompt_returns_enriched_text(monkeypatch):
    _install_fake_openai(monkeypatch, output_text="A detailed ninja platformer spec.")
    result = refine_prompt("a ninja platformer")
    assert result.used_refinement is True
    assert result.refined == "A detailed ninja platformer spec."
    assert result.original == "a ninja platformer"
    assert result.note is None


def test_refine_prompt_passes_prompt_as_input_and_uses_the_refiner_instructions(monkeypatch):
    capture: dict = {}
    _install_fake_openai(monkeypatch, capture=capture)
    refine_prompt("a cave game")
    assert capture["input"] == "a cave game"
    # the system prompt is the refiner's, not the scene planner's
    assert "improved game spec" in capture["instructions"].lower() or "build spec" in capture["instructions"].lower()


def test_refine_prompt_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OP_KEY", raising=False)
    result = refine_prompt("a cave game")
    assert result.used_refinement is False
    assert result.refined == "a cave game"
    assert "OPENAI_API_KEY" in result.note


def test_refine_prompt_falls_back_on_client_error(monkeypatch):
    class FakeResponses:
        def create(self, **kwargs):
            raise RuntimeError("boom")

    class FakeClient:
        def __init__(self, *a, **kw):
            self.responses = FakeResponses()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")

    result = refine_prompt("a cave game")
    assert result.used_refinement is False
    assert result.refined == "a cave game"
    assert "failed" in result.note


def test_refine_prompt_falls_back_on_empty_output(monkeypatch):
    _install_fake_openai(monkeypatch, output_text="   ")
    result = refine_prompt("a cave game")
    assert result.used_refinement is False
    assert result.refined == "a cave game"
    assert "empty" in result.note


def test_refine_prompt_respects_model_env_override(monkeypatch):
    capture: dict = {}
    _install_fake_openai(monkeypatch, capture=capture)
    monkeypatch.setenv("INFINIENV_REFINER_MODEL", "some-cheaper-model")
    refine_prompt("a cave game")
    assert capture["model"] == "some-cheaper-model"


def test_refine_prompt_model_kwarg_overrides_env(monkeypatch):
    capture: dict = {}
    _install_fake_openai(monkeypatch, capture=capture)
    monkeypatch.setenv("INFINIENV_REFINER_MODEL", "env-model")
    refine_prompt("a cave game", model="explicit-model")
    assert capture["model"] == "explicit-model"
