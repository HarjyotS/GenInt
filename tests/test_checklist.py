"""Tests for the independent requirements-checklist generator (sandbox/checklist.py).

Hermetic: a fake OpenAI client replaces the network. Locks in parsing (fences/prose/missing ids) and
the best-effort skips (no key / package / API error / empty output all yield an empty checklist, so
the agent self-derives its TODO)."""

import json
from types import SimpleNamespace

from infinienv.sandbox.checklist import ChecklistResult, _parse_items, build_checklist


def _install_fake_openai(monkeypatch, *, output_text, capture=None):
    class FakeResponses:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            return SimpleNamespace(output_text=output_text)

    class FakeClient:
        def __init__(self, *a, **k):
            self.responses = FakeResponses()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")


def test_parse_items_handles_fences_prose_and_missing_ids():
    raw = '```json\n[{"id":"r1","requirement":"jump works","how_to_verify":"y changes"},{"requirement":"break placed blocks"}]\n```'
    items = _parse_items(raw)
    assert [i["id"] for i in items] == ["r1", "r2"]  # missing id auto-assigned
    assert items[1]["requirement"] == "break placed blocks"
    assert _parse_items("prose then [{\"requirement\":\"collect 2 gems\"}] trailing") == [
        {"id": "r1", "requirement": "collect 2 gems", "how_to_verify": ""}
    ]
    assert _parse_items("not json") == []


def test_build_checklist_returns_items(monkeypatch):
    out = json.dumps([
        {"id": "r1", "requirement": "every placeable block can be broken", "how_to_verify": "place+mine each"},
        {"id": "r2", "requirement": "mine one diamond to win", "how_to_verify": "win requires diamond"},
    ])
    _install_fake_openai(monkeypatch, output_text=out)
    res = build_checklist("a minecraft-lite spec")
    assert res.used is True and res.note is None
    assert len(res.items) == 2
    assert res.items[0]["requirement"].startswith("every placeable")


def test_build_checklist_passes_prompt_and_uses_generator_instructions(monkeypatch):
    capture = {}
    _install_fake_openai(monkeypatch, output_text='[{"requirement":"x"}]', capture=capture)
    build_checklist("THE REFINED PROMPT")
    assert capture["input"] == "THE REFINED PROMPT"
    assert "checklist" in capture["instructions"].lower()


def test_build_checklist_skips_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OP_KEY", raising=False)
    res = build_checklist("spec")
    assert res == ChecklistResult([], False, res.note)
    assert res.items == [] and res.used is False and "OPENAI_API_KEY" in res.note


def test_build_checklist_best_effort_on_error_and_empty(monkeypatch):
    class BoomResponses:
        def create(self, **k):
            raise RuntimeError("boom")

    class BoomClient:
        def __init__(self, *a, **k):
            self.responses = BoomResponses()

    import openai

    monkeypatch.setattr(openai, "OpenAI", BoomClient)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    assert build_checklist("spec").items == []  # API error -> empty, never raises

    _install_fake_openai(monkeypatch, output_text="[]")
    res = build_checklist("spec")
    assert res.items == [] and res.used is False  # no usable items
