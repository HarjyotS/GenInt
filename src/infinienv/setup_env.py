"""Setup helpers: read/merge a project `.env`, and check the runtime is ready to run.

The interactive orchestration lives in `cli.py::cmd_setup`; the pure, testable pieces live here:
parsing/merging a `.env` (preserving every unrelated line and comment) and producing a readiness
checklist. Keeping these pure means `tests/test_setup_env.py` can cover them with no prompts, no
network, and no real filesystem beyond a tmp path.
"""

from __future__ import annotations

import importlib.util
import os
import shutil

# The keys the setup flow manages, in prompt order, with a one-line description shown to the user.
MANAGED_KEYS: dict[str, str] = {
    "OPENAI_API_KEY": (
        "OpenAI API key -- powers prompt refinement, the independent faithfulness audit, "
        "--assets sprite generation, and the `navigate` vision policy."
    ),
    "CL_KEY": (
        "Anthropic API key (OPTIONAL) -- only the `anthropic` provider uses it. The default "
        "Claude sandbox backend authenticates via the `claude` CLI login instead, so you can "
        "usually leave this blank."
    ),
}


def parse_env(text: str) -> dict[str, str]:
    """Parse simple `KEY=VALUE` lines from `.env` text, ignoring blanks and `#` comments."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        out[key.strip()] = value.strip()
    return out


def read_env(env_path: str) -> dict[str, str]:
    """Parsed key/value map of an existing `.env`, or `{}` if it doesn't exist."""
    if not os.path.exists(env_path):
        return {}
    with open(env_path) as f:
        return parse_env(f.read())


def merge_env_text(existing_text: str, updates: dict[str, str]) -> str:
    """Apply `updates` to `.env` text: replace an existing `KEY=` line in place, append a new key
    at the end, and leave every other line (comments, blank lines, unrelated keys) untouched.

    A blank/`None` value in `updates` is treated as "no change" and skipped, so passing an empty
    string for a key the user chose to keep never clobbers their existing value.
    """
    updates = {k: v for k, v in updates.items() if v}
    if not updates:
        return existing_text
    out_lines: list[str] = []
    seen: set[str] = set()
    for line in existing_text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in updates:
                out_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    text = "\n".join(out_lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


def write_env_keys(env_path: str, updates: dict[str, str]) -> list[str]:
    """Merge `updates` into the `.env` at `env_path` (creating it if needed), preserving all other
    content. Returns the list of keys actually written (blank values are skipped)."""
    existing = ""
    if os.path.exists(env_path):
        with open(env_path) as f:
            existing = f.read()
    new_text = merge_env_text(existing, updates)
    with open(env_path, "w") as f:
        f.write(new_text)
    return [k for k, v in updates.items() if v]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_environment(env: dict[str, str] | None = None) -> list[dict]:
    """A readiness checklist: one item per requirement, each `{name, ok, detail, fix}`.

    `env` defaults to the process environment; tests pass an explicit map. `ok` is what determines
    the ✓/✗ shown; `fix` is the exact command/step to resolve a ✗. Only the OpenAI key + the
    `claude` CLI depend on `env`/PATH; the package checks reflect what's importable in this
    interpreter."""
    env = os.environ if env is None else env
    has_openai = bool(env.get("OPENAI_API_KEY") or env.get("OP_KEY"))
    claude_cli = shutil.which("claude")
    return [
        {
            "name": "OpenAI API key",
            "ok": has_openai,
            "detail": "set" if has_openai else "missing -- refine/audit/assets/navigate are skipped or fail without it",
            "fix": "re-run `infinienv setup` and paste your key, or add OPENAI_API_KEY=... to .env",
        },
        {
            "name": "openai package",
            "ok": _module_available("openai"),
            "detail": "installed" if _module_available("openai") else "not installed",
            "fix": 'pip install -e ".[openai]"',
        },
        {
            "name": "Flask (web GUI)",
            "ok": _module_available("flask"),
            "detail": "installed" if _module_available("flask") else "not installed",
            "fix": 'pip install -e ".[gui]"',
        },
        {
            "name": "Claude Agent SDK (default sandbox backend)",
            "ok": _module_available("claude_agent_sdk"),
            "detail": "installed" if _module_available("claude_agent_sdk") else "not installed",
            "fix": 'pip install -e ".[claude]"',
        },
        {
            "name": "`claude` CLI on PATH (the Claude backend drives it)",
            "ok": bool(claude_cli),
            "detail": claude_cli or "not found",
            "fix": "npm install -g @anthropic-ai/claude-code, then run `claude login`",
        },
    ]
