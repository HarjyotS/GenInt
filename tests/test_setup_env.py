import os

from infinienv.cli import main
from infinienv.setup_env import (
    check_environment,
    merge_env_text,
    parse_env,
    read_env,
    write_env_keys,
)


def test_parse_env_ignores_comments_and_blanks():
    text = "# a comment\n\nOPENAI_API_KEY=sk-123\n  CL_KEY = tok  \nNOTAKEY\n"
    assert parse_env(text) == {"OPENAI_API_KEY": "sk-123", "CL_KEY": "tok"}


def test_merge_env_text_replaces_in_place_and_preserves_other_lines():
    existing = "# my keys\nOPENAI_API_KEY=old\nOTHER=keep\n"
    merged = merge_env_text(existing, {"OPENAI_API_KEY": "new"})
    assert "OPENAI_API_KEY=new" in merged
    assert "OPENAI_API_KEY=old" not in merged
    assert "# my keys" in merged  # comment preserved
    assert "OTHER=keep" in merged  # unrelated key preserved


def test_merge_env_text_appends_new_key():
    merged = merge_env_text("OTHER=keep\n", {"CL_KEY": "tok"})
    assert "OTHER=keep" in merged
    assert "CL_KEY=tok" in merged


def test_merge_env_text_blank_value_is_no_change():
    existing = "OPENAI_API_KEY=keepme\n"
    # An empty value means "user kept the current one" -> must not clobber it.
    assert merge_env_text(existing, {"OPENAI_API_KEY": ""}) == existing


def test_write_env_keys_creates_and_merges(tmp_path):
    env_path = str(tmp_path / ".env")
    written = write_env_keys(env_path, {"OPENAI_API_KEY": "sk-abc"})
    assert written == ["OPENAI_API_KEY"]
    assert read_env(env_path)["OPENAI_API_KEY"] == "sk-abc"
    # a second write updates in place, keeps the first, adds the second
    write_env_keys(env_path, {"CL_KEY": "tok"})
    env = read_env(env_path)
    assert env["OPENAI_API_KEY"] == "sk-abc" and env["CL_KEY"] == "tok"


def test_check_environment_reflects_openai_key_and_op_key_fallback():
    names = {c["name"]: c for c in check_environment({})}
    assert names["OpenAI API key"]["ok"] is False
    assert check_environment({"OPENAI_API_KEY": "sk-x"})[0]["ok"] is True
    # OP_KEY is an accepted alias for the OpenAI key.
    assert next(c for c in check_environment({"OP_KEY": "sk-x"}) if c["name"] == "OpenAI API key")["ok"] is True


def test_check_environment_has_all_expected_items():
    names = [c["name"] for c in check_environment({})]
    assert any("OpenAI API key" in n for n in names)
    assert any("Flask" in n for n in names)
    assert any("Claude Agent SDK" in n for n in names)
    assert any("claude` CLI" in n for n in names)


def test_cli_setup_non_interactive_writes_env(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_path = str(tmp_path / ".env")
    rc = main(["setup", "--no-input", "--openai-key", "sk-cli", "--env-path", env_path])
    assert rc == 0
    assert read_env(env_path)["OPENAI_API_KEY"] == "sk-cli"
    out = capsys.readouterr().out
    assert "Readiness check" in out
    assert "OpenAI API key" in out


def test_gui_port_defaults_to_PORT_env(monkeypatch):
    # A PaaS host injects the port to bind as $PORT; `infinienv gui` should default to it so a
    # deployed run binds correctly with no extra flag (see docs/deploy.md + the Dockerfile CMD).
    from infinienv.cli import build_parser

    monkeypatch.setenv("PORT", "8080")
    args = build_parser().parse_args(["gui"])
    assert args.port == 8080

    monkeypatch.delenv("PORT", raising=False)
    assert build_parser().parse_args(["gui"]).port == 5050
