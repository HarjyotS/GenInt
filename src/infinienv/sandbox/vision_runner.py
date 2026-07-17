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
    variation: int = 0,
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
         "hold": hold, "plan_len": plan_len, "history": history, "variation": variation}
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


# --- The played-through proof (§11): after a sandbox attempt passes the sanity check and the
# audit, an EXTERNAL vision policy must actually beat the game before the run may claim success.
# Win is judged by the game's own code (info["won"]) -- code truth, never the generating agent's
# say-so. Opt-out via INFINIENV_SANDBOX_PLAYTHROUGH=0; tries via INFINIENV_PLAYTHROUGH_TRIES.

PLAYTHROUGH_ENV = "INFINIENV_SANDBOX_PLAYTHROUGH"

# Substrings in a failure that mean the CHECKER couldn't run (auth/install/infrastructure), as
# opposed to the game being genuinely unbeatable/broken under external play. Mirrors the auditor's
# posture: a run is never failed because the checker couldn't run.
_INFRA_MARKERS = ("api_key", "openai_api_key", "authentication", "not installed", "no auth")


def playthrough_enabled() -> bool:
    return os.environ.get(PLAYTHROUGH_ENV, "1").strip().lower() not in ("0", "off", "false", "no")


def playthrough_tries() -> int:
    try:
        return max(1, int(os.environ.get("INFINIENV_PLAYTHROUGH_TRIES", "2")))
    except ValueError:
        return 2


def _playthrough_evidence(metrics: dict) -> str:
    """A concrete, repair-actionable summary of a lost episode, built from the driver's own
    vision_metrics.json -- what the repair prompt hands back to the generating agent."""
    parts = [
        f"the policy played {metrics.get('env_steps')} env actions over "
        f"{metrics.get('decisions')} looks and did not reach the game's win condition",
        f"{metrics.get('blocked_steps')} of those actions were blocked (didn't move the player)",
        f"total reward {metrics.get('total_reward')}",
    ]
    if metrics.get("episode_error"):
        parts.append(f"the game errored during play: {metrics['episode_error']}")
    env_steps, reward = metrics.get("env_steps"), metrics.get("total_reward")
    if isinstance(env_steps, int) and env_steps <= 10 and isinstance(reward, (int, float)) and reward < 0:
        # The episode ended almost immediately with a penalty: the player died within moments of
        # spawning -- a hazard is lethal right at the start, which no ordinary player survives.
        parts.append(
            "the game ended after only a few actions with a NEGATIVE reward -- the player died "
            "almost immediately after spawning; make sure no hazard can reach or kill an ordinary "
            "player within the first several actions from spawn (safe spawn area, hazard "
            "routes/timing that give a new player room to react)"
        )
    if metrics.get("had_minimap") is False:
        parts.append(
            "the game exposed NO grid state (walls/walkable/position/goal), so the policy had no "
            "minimap to route on -- expose those via env attributes / step info per the make_env "
            "contract"
        )
    elif not metrics.get("total_reward"):
        # It had a minimap yet never advanced a single objective: the classic cause is a STATIC
        # final-goal marker in a multi-stage game (the map routes the player into a locked
        # gate/exit it can't pass yet). The fix is a dynamic env.goal = the current objective.
        parts.append(
            "despite a minimap, no objective ever advanced -- if the game is multi-stage "
            "(keys/gems/switches before an exit), make the exposed env.goal point at the CURRENT "
            "objective (the next key/gem/switch) and only at the gate/exit once it's unlocked, "
            "and remove an opened gate from the walls set"
        )
    return "; ".join(str(p) for p in parts)


async def verify_playthrough(
    out_dir: str,
    *,
    on_stage: Callable[[str], None] | None = None,
    tries: int | None = None,
    max_steps: int | None = None,
) -> dict:
    """Run the played-through proof against the sandbox run in `out_dir` (its synced
    `sandbox_workspace/`), giving the stochastic stand-in policy up to `tries` episodes to win.

    Returns `{attempted, won, tries, note, evidence}`:
    - `attempted=True, won=True`   -> proof passed (episode.gif + vision_metrics.json kept).
    - `attempted=True, won=False`  -> the game ran but couldn't be beaten / broke under external
      play / has no make_env -- a genuine defect; `evidence` feeds the repair loop.
    - `attempted=False`            -> the checker itself couldn't run (disabled, no key, missing
      SDK); never fails the run, `note` records why.
    """
    def stage(msg: str) -> None:
        if on_stage is not None:
            on_stage(msg)

    if not playthrough_enabled():
        return {
            "attempted": False, "won": None, "tries": 0,
            "note": f"disabled via {PLAYTHROUGH_ENV}", "evidence": None,
        }
    if not os.environ.get("OPENAI_API_KEY"):
        return {
            "attempted": False, "won": None, "tries": 0,
            "note": "playthrough checker could not run: no OPENAI_API_KEY for the vision policy",
            "evidence": None,
        }
    tries = playthrough_tries() if tries is None else max(1, tries)
    if max_steps is None:
        # 100, not the driver's usual 60: the proof demands a WIN, not a speedrun -- an ordinary
        # player explores, so the episode budget needs headroom over the ~40-60-action direct path
        # the prompt asks worlds to have (a live two-key dungeon's direct path alone was ~50).
        try:
            max_steps = max(20, int(os.environ.get("INFINIENV_PLAYTHROUGH_MAX_STEPS", "100")))
        except ValueError:
            max_steps = 100
    last_evidence: str | None = None
    for t in range(tries):
        stage(f"Playthrough proof: a vision policy is playing this world (try {t + 1}/{tries})...")
        try:
            metrics = await _play_async(
                out_dir, out_dir, model=os.environ.get("INFINIENV_VISION_MODEL", "gpt-5.6-terra"),
                max_steps=max_steps, hold=6, plan_len=6, history=2, judge=False, on_stage=on_stage,
                variation=t,  # retries nudge the policy off the previous try's losing route
            )
        except ProviderError as exc:
            msg = str(exc)
            low = msg.lower()
            if "make_env" in low:
                # Not infra: the world lacks the playable interface -- the generating agent's
                # defect, and concretely repairable.
                return {
                    "attempted": True, "won": False, "tries": t + 1, "note": msg,
                    "evidence": (
                        "run_scene.py does not expose a working module-level make_env() -- an "
                        "external policy cannot play this world at all. Implement the make_env "
                        f"contract (env.actions / reset / step with info['won']): {msg}"
                    ),
                }
            if any(m in low for m in _INFRA_MARKERS):
                return {
                    "attempted": False, "won": None, "tries": t + 1,
                    "note": f"playthrough checker could not run: {msg}", "evidence": None,
                }
            # The driver started but the game broke under external play -- a real defect.
            return {
                "attempted": True, "won": False, "tries": t + 1, "note": msg,
                "evidence": (
                    "the game crashed or failed while an external policy was driving it through "
                    f"make_env(): {msg}. env.step must survive any action from env.actions."
                ),
            }
        except Exception as exc:  # session/SDK infrastructure failure -- checker couldn't run
            return {
                "attempted": False, "won": None, "tries": t + 1,
                "note": f"playthrough checker could not run: {exc}", "evidence": None,
            }
        if metrics.get("vision_success"):
            return {
                "attempted": True, "won": True, "tries": t + 1,
                "note": (
                    f"an external vision policy beat the game on try {t + 1}/{tries} "
                    f"({metrics.get('env_steps')} env actions, {metrics.get('decisions')} looks)"
                ),
                "evidence": None,
            }
        last_evidence = _playthrough_evidence(metrics)
        stage(f"Playthrough try {t + 1}/{tries} did not win: {last_evidence}")
    return {
        "attempted": True, "won": False, "tries": tries,
        "note": f"an external vision policy could not beat the game in {tries} tries",
        "evidence": last_evidence or "the policy never reached the game's win condition",
    }


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
