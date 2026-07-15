"""Independent faithfulness auditor (sandbox/auditor.py). Hermetic -- no network, no real key: the
OpenAI client is faked so we exercise the payload-building, verdict-parsing, and best-effort skip
paths without a live call."""

import json

import pytest

from infinienv.sandbox import auditor
from infinienv.sandbox.auditor import _parse_verdict, audit_run


def _make_run_dir(tmp_path, code="print('hi')", rules=None):
    out = tmp_path / "run"
    ws = out / "sandbox_workspace"
    ws.mkdir(parents=True)
    (ws / "run_scene.py").write_text(code)
    (out / "replay.json").write_text(json.dumps({"trace": [{"x": 1, "won": True}]}))
    metrics = {"success": True}
    if rules is not None:
        metrics["rules"] = rules
    (out / "metrics.json").write_text(json.dumps(metrics))
    return str(out)


def _install_fake_openai(monkeypatch, output_text, capture=None):
    import openai

    class _Resp:
        output_text = None

    class _Responses:
        def create(self, **kwargs):
            if capture is not None:
                capture.update(kwargs)
            r = _Resp()
            r.output_text = output_text
            return r

    class _Client:
        def __init__(self, *a, **k):
            self.responses = _Responses()

    monkeypatch.setattr(openai, "OpenAI", _Client)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("INFINIENV_SANDBOX_AUDIT", raising=False)
    monkeypatch.delenv("INFINIENV_SANDBOX_AUDITOR_MODEL", raising=False)


# --- verdict parsing --------------------------------------------------------------------------


def test_parse_verdict_pass():
    assert _parse_verdict('{"verdict": "PASS", "findings": []}') == (True, None)


def test_parse_verdict_fail_collects_findings():
    passed, findings = _parse_verdict('{"verdict": "FAIL", "findings": ["a cheat", "another"]}')
    assert passed is False and "a cheat" in findings and "another" in findings


def test_parse_verdict_tolerates_code_fence_and_prose():
    passed, _ = _parse_verdict('Here is my review:\n```json\n{"verdict":"PASS","findings":[]}\n```')
    assert passed is True


def test_parse_verdict_none_on_garbage():
    assert _parse_verdict("no json here at all") is None
    assert _parse_verdict('{"no_verdict_key": 1}') is None


# --- audit_run end to end (faked client) ------------------------------------------------------


def test_audit_pass(tmp_path, monkeypatch):
    _install_fake_openai(monkeypatch, '{"verdict": "PASS", "findings": []}')
    result = audit_run(_make_run_dir(tmp_path), "make a maze")
    assert result.audited is True and result.passed is True and result.findings is None


def test_audit_fail_surfaces_findings(tmp_path, monkeypatch):
    _install_fake_openai(
        monkeypatch,
        '{"verdict": "FAIL", "findings": ["solver beelines to game.layout.diamond under fog of war"]}',
    )
    result = audit_run(_make_run_dir(tmp_path), "fog of war, only see line of sight")
    assert result.audited is True and result.passed is False
    assert "game.layout.diamond" in result.findings


def test_audit_sends_prompt_code_and_rules(tmp_path, monkeypatch):
    capture: dict = {}
    _install_fake_openai(monkeypatch, '{"verdict": "PASS", "findings": []}', capture=capture)
    code = "def policy(): return 'unique-marker-xyz'"
    out = _make_run_dir(tmp_path, code=code, rules=["two gems gate the exit"])
    audit_run(out, "collect two gems then exit")
    sent = capture["input"]
    assert "collect two gems then exit" in sent  # the spec
    assert "unique-marker-xyz" in sent  # the actual code
    assert "two gems gate the exit" in sent  # the declared rules block


def test_audit_skipped_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = audit_run(_make_run_dir(tmp_path), "make a maze")
    assert result.audited is False and result.passed is True and "OPENAI_API_KEY" in result.note


def test_audit_disabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("INFINIENV_SANDBOX_AUDIT", "0")
    result = audit_run(_make_run_dir(tmp_path), "make a maze")
    assert result.audited is False and result.passed is True and "disabled" in result.note


def test_audit_unparseable_output_does_not_fail_the_run(tmp_path, monkeypatch):
    _install_fake_openai(monkeypatch, "I think it's fine, no strong opinion.")
    result = audit_run(_make_run_dir(tmp_path), "make a maze")
    assert result.audited is False and result.passed is True  # couldn't audit != failed


def test_audit_skipped_when_no_code(tmp_path, monkeypatch):
    _install_fake_openai(monkeypatch, '{"verdict": "PASS", "findings": []}')
    out = tmp_path / "run"
    (out / "sandbox_workspace").mkdir(parents=True)  # no run_scene.py
    (out / "replay.json").write_text("{}")
    result = audit_run(str(out), "make a maze")
    assert result.audited is False and result.passed is True


def test_audit_call_failure_degrades_gracefully(tmp_path, monkeypatch):
    import openai

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("api exploded")

    monkeypatch.setattr(openai, "OpenAI", _Boom)
    result = audit_run(_make_run_dir(tmp_path), "make a maze")
    assert result.audited is False and result.passed is True and "failed" in result.note


def test_auditor_review_input_includes_the_checklist():
    from infinienv.sandbox.auditor import _build_review_input, _format_checklist

    checklist = [
        {"id": "r1", "requirement": "break any placed block", "status": "done", "verified_by": "place+mine each"},
        {"id": "r2", "requirement": "mine a diamond", "status": "pending", "verified_by": None},
    ]
    fmt = _format_checklist(checklist)
    assert "[x] (r1) break any placed block" in fmt
    assert "[ ] (r2) mine a diamond" in fmt
    review = _build_review_input("the spec", "code", None, None, checklist)
    assert "requirements checklist" in review.lower()
    assert "break any placed block" in review
    # no checklist -> a clear placeholder, never a crash
    assert "no requirements checklist" in _format_checklist(None).lower()
