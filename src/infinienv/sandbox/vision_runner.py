"""Faithful vision-play orchestrator: run a vision policy against the REAL sandbox game.

Given an already-generated sandbox run (whose `sandbox_workspace/run_scene.py` exposes `make_env()`
-- the make_env contract, see `sandbox/workspace.py` and `sandbox_agent.md`), this hydrates a fresh
sandbox from a copy of that workspace, drops in the self-contained `vision_play.py` driver, and runs
it **inside the sandbox**. The driver drives the game's own env with a vision policy and writes
`episode.gif` + `vision_metrics.json`; this trusted process only ever reads those two files back --
it never imports or executes the agent-authored game code (the §11 isolation invariant).

Reuses `sandbox/runner.py`'s session mechanics and the exact `Manifest` grants that make the
interpreter + outbound network + OpenAI key work inside the sandbox (asset generation already calls
the OpenAI Images API from inside a sandbox run, so a vision call from inside is the same path).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tarfile
import time
from typing import Callable

from infinienv.artifacts.writer import resolve_out_dir
from infinienv.llm.base import ProviderError

_VISION_ARTIFACTS = ("episode.gif", "vision_metrics.json")


def _tar_workspace_with(workspace_src: str, extra_files: dict[str, bytes]) -> io.BytesIO:
    """Package `workspace_src` (the run's real sandbox_workspace/) into an in-memory tar for
    `session.hydrate_workspace()`, injecting `extra_files` (the trusted vision-play driver + config)
    as in-memory members. No host-side copy of the workspace, and the kept sandbox_workspace/ is
    never modified -- the driver+config exist only inside the sandbox's hydrated filesystem. Skips
    __pycache__/.pyc so stale bytecode can't shadow the copied sources."""
    def _keep(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = ti.name.split("/")
        return None if ("__pycache__" in parts or ti.name.endswith(".pyc")) else ti

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(workspace_src, arcname=".", filter=_keep)
        for name, data in extra_files.items():
            info = tarfile.TarInfo(name="./" + name)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def _goal_text(run_dir: str) -> str:
    """The natural-language goal handed to the policy: the world's original prompt."""
    scene_path = os.path.join(run_dir, "scene.json")
    try:
        with open(scene_path) as f:
            meta = json.load(f).get("metadata", {})
        return (meta.get("prompt") or meta.get("name") or "reach the objective").strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return "reach the objective"


def _valid_animation(path: str) -> tuple[bool, str | None]:
    """episode.gif must be a real, multi-frame, fully decodable animation (mirrors the gif floor in
    workspace.outer_sanity_check) -- a floor against a malformed/empty/single-frame result."""
    from PIL import Image

    if not os.path.exists(path):
        return False, "no episode.gif was produced (the vision-play driver may have failed)"
    if os.path.getsize(path) < 100:
        return False, f"episode.gif is only {os.path.getsize(path)} bytes -- likely truncated/empty"
    try:
        with Image.open(path) as gif:
            n = getattr(gif, "n_frames", 1)
            if n < 2:
                return False, f"episode.gif has only {n} frame(s) -- not an animated episode"
            for i in range(n):
                gif.seek(i)
                gif.load()
    except Exception as exc:
        return False, f"episode.gif could not be decoded frame-by-frame: {exc}"
    return True, None


async def _read_file(session, name: str, dest: str) -> bool:
    try:
        handle = await session.read(name)
    except Exception:
        return False
    data = handle.read()
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(dest, mode) as out:
        out.write(data)
    return True


async def _play_async(
    run_dir: str,
    out_dir: str,
    *,
    model: str,
    max_steps: int,
    hold: int,
    plan_len: int,
    history: int,
    judge: bool,
    on_stage: Callable[[str], None] | None,
) -> dict:
    def stage(msg: str) -> None:
        if on_stage is not None:
            on_stage(msg)

    workspace_src = os.path.join(run_dir, "sandbox_workspace")
    if not os.path.isdir(workspace_src):
        raise ProviderError(
            f"{run_dir!r} has no sandbox_workspace/ -- faithful vision-play only works on a sandbox "
            "run. Generate a world with --sandbox first."
        )
    if not os.path.isfile(os.path.join(workspace_src, "run_scene.py")):
        raise ProviderError(f"{workspace_src!r} has no run_scene.py -- not a usable sandbox workspace.")

    out_dir = resolve_out_dir(out_dir)
    goal = _goal_text(run_dir)
    stage(f"Goal (given to the vision policy): {goal}")

    # Hydrate the sandbox straight from the run's real sandbox_workspace/ (no host-side copy),
    # injecting the trusted driver + config as in-memory tar members. Keeps asset_cache/ so a
    # make_env() that resolves assets reuses the already-generated sprites (no re-generation).
    driver_src = os.path.join(os.path.dirname(__file__), "vision_play.py")
    with open(driver_src, "rb") as f:
        driver_bytes = f.read()
    config_bytes = json.dumps(
        {"goal": goal, "model": model, "max_steps": max_steps, "judge": judge,
         "hold": hold, "plan_len": plan_len, "history": history}
    ).encode("utf-8")
    workspace_tar = _tar_workspace_with(
        workspace_src, {"vision_play.py": driver_bytes, "vision_config.json": config_bytes}
    )

    try:
        from agents.sandbox.manifest import Manifest
        from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
        from agents.sandbox.workspace_paths import SandboxPathGrant
    except ImportError as exc:
        raise ProviderError(
            "The 'openai-agents' package (with sandbox support) is not installed. "
            "Install it with `pip install infinienv[openai]`."
        ) from exc

    from infinienv.assets.generator_diffusion import model_cache_dir

    os.environ.setdefault("INFINIENV_MODEL_CACHE_DIR", model_cache_dir())
    # Same grants as sandbox/runner.py: the interpreter (+ installed packages) must be readable
    # inside the Seatbelt confinement, and the model cache read-write. See runner.py for the
    # full diagnosis of why sys.prefix must be granted.
    manifest = Manifest(
        extra_path_grants=(
            SandboxPathGrant(path=sys.prefix, read_only=True, description="Python interpreter + packages"),
            SandboxPathGrant(
                path=os.environ["INFINIENV_MODEL_CACHE_DIR"], read_only=False,
                description="shared local model-weights cache",
            ),
        )
    )
    client = UnixLocalSandboxClient()
    session = await client.create(manifest=manifest)
    await session.start()
    await session.hydrate_workspace(workspace_tar)

    stage("A vision policy is playing the real game inside the sandbox...")
    try:
        result = await session.exec(f"{sys.executable} vision_play.py", timeout=1200)
    finally:
        # extract before closing, whatever happened
        got_gif = await _read_file(session, "episode.gif", os.path.join(out_dir, "episode.gif"))
        got_metrics = await _read_file(session, "vision_metrics.json", os.path.join(out_dir, "vision_metrics.json"))
        await session.aclose()

    stdout = (result.stdout or b"").decode("utf-8", "replace")
    stderr = (result.stderr or b"").decode("utf-8", "replace")
    for line in stdout.splitlines():
        if line.strip():
            stage(line.rstrip())

    if result.exit_code == 3 or "NO_MAKE_ENV" in stdout:
        raise ProviderError(
            "This world wasn't built with a playable interface (run_scene.make_env is missing). "
            "It predates faithful vision-play -- regenerate the world with --sandbox to get one."
        )
    if not got_metrics:
        tail = (stderr.strip().splitlines() or ["(no stderr)"])[-1]
        raise ProviderError(f"the vision-play driver did not finish (exit {result.exit_code}): {tail}")

    with open(os.path.join(out_dir, "vision_metrics.json")) as f:
        metrics = json.load(f)

    ok, gif_err = _valid_animation(os.path.join(out_dir, "episode.gif")) if got_gif else (False, "no episode.gif")
    metrics["episode_gif_ok"] = ok
    metrics["episode_gif_error"] = gif_err
    metrics["run_dir"] = os.path.relpath(run_dir, os.getcwd())
    with open(os.path.join(out_dir, "vision_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)

    verdict = "SUCCESS" if metrics.get("vision_success") else "did not reach the goal"
    stage(f"Result: {verdict} in {metrics.get('steps')} steps (code-judged by the game's own win).")
    return metrics


def play_sandbox_world(
    run_dir: str,
    out_dir: str,
    *,
    backend: str = "openai",
    model: str | None = None,
    max_steps: int = 60,
    hold: int = 6,
    plan_len: int = 6,
    history: int = 2,
    judge: bool = True,
    on_stage: Callable[[str], None] | None = None,
) -> dict:
    """Sync entrypoint: a vision policy faithfully plays the real sandbox game in `run_dir`, writing
    `episode.gif` + `vision_metrics.json` into `out_dir`. The policy observes OCCASIONALLY and plays
    in short PLANS: each look returns a sequence of up to `plan_len` actions (executed in order, each
    HELD for `hold` simulation frames -- frame-skip / action-repeat), and `max_steps` bounds the
    TOTAL env actions, so the model is called ~`max_steps / plan_len` times, not once per frame.
    `backend` is accepted for parity with the deterministic navigate path; the in-sandbox driver uses
    OpenAI vision (the sandbox's proven network path). Returns the metrics dict."""
    if backend not in ("openai",):
        # The driver runs inside the sandbox and uses OpenAI vision; Claude-in-sandbox isn't wired.
        raise ProviderError(
            f"faithful vision-play supports the 'openai' vision backend inside the sandbox, not {backend!r}."
        )
    model = model or os.environ.get("INFINIENV_VISION_MODEL", "gpt-5.6-terra")
    return asyncio.run(
        _play_async(run_dir, out_dir, model=model, max_steps=max_steps, hold=hold,
                    plan_len=plan_len, history=history, judge=judge, on_stage=on_stage)
    )
