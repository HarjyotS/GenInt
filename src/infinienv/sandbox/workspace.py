"""Builds the isolated per-run copy of the engine that a sandbox agent edits and executes.

The sandbox agent gets a fresh, real copy of schema/engine/navigation/validation/render/assets --
never the actual installed package -- so whatever it writes or breaks is confined to one run's
`runs/<id>/sandbox_workspace/` directory and can never affect another run or this repo's own
source. See CLAUDE.md's sandbox section and notes.md for the full design rationale and the
concrete trade-off this makes against the validator-wins guarantee everywhere else in this
project: this module extracts *only* the standard artifact files from the sandbox and never
imports or executes the sandboxed .py files in this process.

The copied files' own internal imports are rewritten from `infinienv.X` to bare `X` (see
`_rewrite_internal_imports`) so the copy is genuinely self-contained. Without this, since
`infinienv` is installed editable and importable from anywhere the same venv runs, a copied
module's `from infinienv.engine.grid import Grid`-style import would silently resolve to the
*real* installed package instead of the sandboxed copy sitting right next to it -- meaning an
agent's edit to e.g. `engine/grid.py` could be silently ignored by every other copied module that
still reaches for the real one. This was a real, previously-undetected gap between what this mode
promises ("edit anything, including the engine itself") and what actually ran.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile

_COPIED_PACKAGES = ("schema", "engine", "navigation", "validation", "render", "assets")
# Not a full package -- assets/generator_openai.py and assets/resolver.py depend only on
# ProviderError from llm.base, so only that one file is copied rather than pulling in the
# whole llm package (providers, prompts, heavy optional deps).
_PARTIAL_COPIES: tuple[tuple[str, str], ...] = (("llm/base.py", "llm/base.py"),)

_INTERNAL_IMPORT_RE = re.compile(r"^(\s*)(from|import)\s+infinienv\.", re.MULTILINE)

_RUN_SCENE_TEMPLATE = '''\
"""Reference entrypoint: validate, solve, and render scene.json in this directory.

You (the sandbox agent) own this file and everything else in this workspace. If the mechanic
you're implementing needs a different simulation loop than solve_scene() provides, edit
navigation/policy.py (or anything else here) and this script will pick up your changes, since
it imports the local copies below, not any installed package. You can also rewrite this script
entirely. The only hard requirement: by the time you finish, this directory must contain
scene.json, metrics.json, replay.json, render.png, and replay.gif.

TWO import-safety rules a rewrite MUST keep (so an external controller can play your game -- see
the make_env contract in your instructions):
  1. Expose a module-level `make_env()` returning a drivable episode of THIS game
     (env.actions / env.reset() -> frame / env.step(action) -> (frame, reward, done, info)).
  2. Keep all generation/self-play side effects under `if __name__ == "__main__":` so
     `from run_scene import make_env` never re-runs your whole script.

ASSETS_MODE (a plain-text file in this directory) controls sprite resolution, mirroring the
project's --assets modes. If it's anything other than "none", real sprites are resolved via
assets/resolver.py (generated through assets/generator_openai.py's OpenAI Images API call, or
the checked-in local placeholders in assets/base/, depending on the mode) and passed into the
renderer so render.png/replay.gif use real sprites instead of flat colored cells -- exactly like
the non-sandbox --assets flag. Sprites are cached in ./asset_cache for the rest of this run's
attempts (not shared with other runs).
"""
import json
import os
import sys

from schema.scene_schema import scene_spec_from_dict
from validation.validator import validate_scene
from navigation.policy import solve_scene
from render.image_export import save_render_png
from render.replay_export import save_replay_gif


def _load_scene():
    with open("scene.json") as f:
        return scene_spec_from_dict(json.load(f))


def make_env():
    """A drivable episode of THIS world so an external controller (e.g. a vision policy) can play
    it: env.actions / env.reset() -> PIL.Image / env.step(action) -> (PIL.Image, reward, done, info).
    This reference version wraps the deterministic InfiniEnv (a top-down grid env). If you REWRITE
    this file into a custom side-view game with your own physics + rendering, expose make_env() the
    same way over YOUR game -- return the real rendered frame, your own action set, and set
    info["won"] from your own code-defined win condition. Keep this at module level (import-safe).

    Render with the SAME real assets your replay uses: resolve ASSETS_MODE from the cached
    ./asset_cache and pass them into your renderer, so the frames a player sees match
    render.png/replay.gif -- NOT primitives. (If your own draw loop uses a SpriteBook, build it from
    these asset_paths here too.)"""
    from engine.env import InfiniEnv, CONTROLLER_ACTIONS

    scene = _load_scene()

    asset_paths = {}
    _mode = "none"
    if os.path.exists("ASSETS_MODE"):
        with open("ASSETS_MODE") as _f:
            _mode = _f.read().strip() or "none"
    if _mode != "none":
        from assets.resolver import resolve_assets

        _entries, _notes = resolve_assets(scene, _mode, os.path.abspath("asset_cache"))
        asset_paths = {t: e.path for t, e in _entries.items() if e.path}

    class _Episode:
        actions = CONTROLLER_ACTIONS
        dt = 0.2  # seconds of game time per step() -- lets the replay play in real time

        def __init__(self):
            self._env = InfiniEnv(scene, asset_paths=asset_paths)

        def reset(self):
            obs, _info = self._env.reset()
            return obs

        def step(self, action):
            obs, reward, terminated, truncated, info = self._env.step(action)
            return obs, reward, bool(terminated or truncated), {"won": bool(info.get("all_complete"))}

    return _Episode()


def main():
    scene = _load_scene()
    validation = validate_scene(scene)
    solve = solve_scene(scene)

    assets_mode = "none"
    if os.path.exists("ASSETS_MODE"):
        with open("ASSETS_MODE") as f:
            assets_mode = f.read().strip() or "none"

    asset_paths = {}
    asset_notes = []
    if assets_mode != "none":
        from assets.resolver import resolve_assets

        entries, asset_notes = resolve_assets(scene, assets_mode, os.path.abspath("asset_cache"))
        asset_paths = {t: e.path for t, e in entries.items() if e.path}
        # This script hands asset_paths straight to save_render_png/save_replay_gif, which paste every
        # resolved sprite for you. If you REWRITE this into a custom draw loop (continuous positions),
        # paste through engine.rendering.SpriteBook(asset_paths) and assert not book.unused_keys()
        # before finishing -- that catches the recurring bug of resolving nice sprites then drawing
        # primitives anyway, or asking for a key that doesn't match what was resolved.

    metrics = {
        "success": bool(validation.valid and solve.success),
        "source": "sandbox",
        "validation_passed": validation.valid,
        "solver_success": solve.success,
        "path_length": len(solve.actions),
        "num_objects": len(scene.objects),
        "num_goals": len(scene.goals),
    }
    # asset_notes carries resolve_assets()'s per-type failure reasons (e.g. a generation error) --
    # recorded even when empty is fine, but ALWAYS recorded: a sprite that silently fell back to a
    # flat colored cell with no trace of why is a real, previously-hit diagnostic dead end.
    metrics["asset_notes"] = asset_notes
    with open("metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    with open("replay.json", "w") as f:
        json.dump(
            {"actions": solve.actions, "trace": solve.trace, "success": solve.success},
            f,
            indent=2,
            default=str,
        )

    save_render_png(scene, "render.png", title=scene.metadata.name, asset_paths=asset_paths)
    save_replay_gif(scene, solve.actions, "replay.gif", asset_paths=asset_paths)

    print("wrote scene.json, metrics.json, replay.json, render.png, replay.gif")
    return 0 if metrics["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
'''

ARTIFACT_FILES: tuple[str, ...] = ("scene.json", "metrics.json", "replay.json", "render.png", "replay.gif")


def _rewrite_internal_imports(workspace_dir: str) -> None:
    """Rewrite `from/import infinienv.X` to `from/import X` in every copied .py file, so
    cross-references between copied modules resolve to the sandboxed copy sitting next to
    them, not the real installed package. See module docstring.
    """
    for root, _dirs, files in os.walk(workspace_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path) as f:
                content = f.read()
            rewritten = _INTERNAL_IMPORT_RE.sub(r"\1\2 ", content)
            if rewritten != content:
                with open(path, "w") as f:
                    f.write(rewritten)


def build_workspace_dir(out_dir: str, *, assets_mode: str = "none") -> str:
    """Create <out_dir>/sandbox_workspace/: a real, on-disk copy of the engine plus a
    reference run_scene.py entrypoint. Persisted (not a temp dir) so a reviewer can inspect
    exactly what the sandbox agent read, wrote, and ran -- the audit trail this mode needs
    in place of the solvability guarantee it gives up.
    """
    import infinienv

    package_root = os.path.dirname(infinienv.__file__)
    workspace_dir = os.path.join(out_dir, "sandbox_workspace")
    os.makedirs(workspace_dir, exist_ok=True)

    for name in _COPIED_PACKAGES:
        src = os.path.join(package_root, name)
        dst = os.path.join(workspace_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))

    for src_rel, dst_rel in _PARTIAL_COPIES:
        src = os.path.join(package_root, src_rel)
        dst = os.path.join(workspace_dir, dst_rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
            init_path = os.path.join(os.path.dirname(dst), "__init__.py")
            if not os.path.exists(init_path):
                open(init_path, "w").close()

    _rewrite_internal_imports(workspace_dir)

    with open(os.path.join(workspace_dir, "run_scene.py"), "w") as f:
        f.write(_RUN_SCENE_TEMPLATE)

    with open(os.path.join(workspace_dir, "ASSETS_MODE"), "w") as f:
        f.write(assets_mode)

    return workspace_dir


_PLAN_TOOL_TEMPLATE = '''\
#!/usr/bin/env python3
"""Your BUILD PLAN tool -- a live todo of the PARTS OF THE PROGRAM you are building, like a coding
agent's todo list. Drive it through this tool (not by hand-editing PLAN.json) so progress is tracked
and shown live to the reviewer.

  python plan.py show                 # print your build plan + what's left
  python plan.py add "<build task>"   # add a concrete part of the program to build (a progress point)
  python plan.py start <id>           # mark the task you are building NOW (shown as the current one)
  python plan.py done <id>            # mark a task built and actually working
  python plan.py note "<line>"        # append a line to MEMORY.md (your working memory)

This is SEPARATE from REQUIREMENTS.json (what the finished game must do, which you're audited
against). Your build tasks are the *work*, and together they must ADD UP to satisfy every
requirement. Make each task a real part of the program -- "tile world generation", "gravity + jump
physics", "gem pickup + counter", "exit gate logic", "HUD + win banner" -- NOT a restatement of a
requirement. Plan the steps first (add them all), then build, marking start/done as you go.
"""
import json
import sys

PLAN = "PLAN.json"
MEM = "MEMORY.md"


def _load():
    try:
        with open(PLAN) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def _save(items):
    with open(PLAN, "w") as f:
        json.dump(items, f, indent=2)


def _progress(items):
    done = sum(1 for it in items if it.get("status") == "done")
    print("PLAN_PROGRESS %d/%d done" % (done, len(items)))


def _set(item_id, status):
    items = _load()
    for it in items:
        if str(it.get("id")) == str(item_id):
            it["status"] = status
            _save(items)
            print("PLAN_UPDATE %s %s" % (item_id, status))
            _progress(items)
            return
    print("PLAN_ERROR: no task with id %r (run: python plan.py show)" % item_id)
    sys.exit(2)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "show":
        items = _load()
        for it in items:
            mark = {"done": "x", "doing": ">"}.get(it.get("status"), " ")
            print("[%s] %s: %s" % (mark, it.get("id"), it.get("task")))
        _progress(items)
        return
    cmd = args[0]
    if cmd == "add":
        items = _load()
        new_id = "t%d" % (len(items) + 1)
        items.append({"id": new_id, "task": args[1] if len(args) > 1 else "", "status": "pending"})
        _save(items)
        print("PLAN_ADD %s: %s" % (new_id, args[1] if len(args) > 1 else ""))
    elif cmd == "start":
        _set(args[1], "doing")
    elif cmd == "done":
        _set(args[1], "done")
    elif cmd == "note":
        line = args[1] if len(args) > 1 else ""
        with open(MEM, "a") as f:
            f.write("- " + line + "\\n")
        print("MEMORY_NOTE: " + line)
    else:
        print("PLAN_ERROR: unknown command %r" % cmd)
        sys.exit(2)


if __name__ == "__main__":
    main()
'''

_MEMORY_HEADER = (
    "# Build memory\n\n"
    "Your working notes for this build -- decisions, invariants, gotchas. Append with "
    "`python plan.py note \"...\"`. This persists across repair attempts, so a later attempt "
    "inherits what you learned.\n\n"
)


def seed_workspace(workspace_dir: str, requirements: list[dict]) -> None:
    """Seed the harness into the workspace (before hydration; lives in the sandbox FS + persists
    across repair attempts):

    - `REQUIREMENTS.json` -- the independently-derived acceptance criteria the run is audited against
      (SEPARATE from the build plan; the agent doesn't tick these -- the auditor verifies them).
    - `PLAN.json` -- the agent's live BUILD PLAN (progress points / parts of the program to build),
      **empty to start**: the agent fills it via plan.py, and its tasks must add up to satisfy every
      requirement. This is what the GUI's floating goals popup shows.
    - `MEMORY.md` + `plan.py` (the tool).

    Idempotent for the tool/memory; the two lists are only seeded when absent so a repair keeps the
    agent's prior plan progress."""
    req_path = os.path.join(workspace_dir, "REQUIREMENTS.json")
    if not os.path.exists(req_path):
        reqs = [
            {
                "id": str(it.get("id") or f"r{i}"),
                "requirement": str(it.get("requirement") or ""),
                "how_to_verify": str(it.get("how_to_verify") or ""),
            }
            for i, it in enumerate(requirements or [], start=1)
        ]
        with open(req_path, "w") as f:
            json.dump(reqs, f, indent=2)
    plan_path = os.path.join(workspace_dir, "PLAN.json")
    if not os.path.exists(plan_path):
        with open(plan_path, "w") as f:
            json.dump([], f)
    mem_path = os.path.join(workspace_dir, "MEMORY.md")
    if not os.path.exists(mem_path):
        with open(mem_path, "w") as f:
            f.write(_MEMORY_HEADER)
    with open(os.path.join(workspace_dir, "plan.py"), "w") as f:
        f.write(_PLAN_TOOL_TEMPLATE)


def _read_json_list(base: str, filename: str) -> list[dict]:
    for candidate in (os.path.join(base, filename), os.path.join(base, "sandbox_workspace", filename)):
        if os.path.exists(candidate):
            try:
                with open(candidate) as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
            except (OSError, json.JSONDecodeError):
                return []
    return []


def read_requirements(base: str) -> list[dict]:
    """The run's REQUIREMENTS.json (acceptance criteria) -- for the auditor, metrics, final report."""
    return _read_json_list(base, "REQUIREMENTS.json")


def read_plan(base: str) -> list[dict]:
    """The run's PLAN.json (the agent's build progress points) -- for metrics, the popup, repair."""
    return _read_json_list(base, "PLAN.json")


def open_plan_items(items: list[dict]) -> list[dict]:
    """The still-not-done build tasks (for repair re-injection)."""
    return [it for it in (items or []) if it.get("status") != "done"]


def tar_directory(path: str) -> io.BytesIO:
    """Package a directory into an in-memory tar for `session.hydrate_workspace()`.

    Required because the sandbox SDK's `snapshot=LocalSnapshotSpec(base_path=...)` parameter
    does not auto-hydrate on session creation in the installed SDK version (verified live,
    not assumed -- see notes.md) -- the workspace must be tarred and hydrated explicitly.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(path, arcname=".")
    buf.seek(0)
    return buf


async def sync_full_workspace(session, workspace_dir: str) -> None:
    """Overwrite the persisted `workspace_dir` on disk with the sandbox's actual final
    filesystem state (via `session.persist_workspace()`, the read-side counterpart of
    `hydrate_workspace()`).

    Without this, `workspace_dir` only ever holds the pre-run copy `build_workspace_dir`
    wrote -- `hydrate_workspace` extracts the tar into the sandbox backend's own temp
    directory, not back into `workspace_dir`, so any files the agent added or edited would
    otherwise never appear in the run's kept-for-audit workspace on the host.
    """
    archive = await session.persist_workspace()
    data = archive.read()
    buf = io.BytesIO(data) if isinstance(data, bytes) else data
    buf.seek(0)
    shutil.rmtree(workspace_dir, ignore_errors=True)
    os.makedirs(workspace_dir, exist_ok=True)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        tar.extractall(workspace_dir, filter="data")


async def extract_artifacts(session, out_dir: str) -> dict[str, str]:
    """Copy exactly the standard artifact files out of the sandbox session into the real run
    directory. Nothing else is pulled out, and the sandboxed .py files are never imported or
    executed by this process -- only these declared output files are trusted, and even they
    only get an outer schema sanity check (see `outer_sanity_check`), not a solvability
    guarantee.
    """
    os.makedirs(out_dir, exist_ok=True)
    paths: dict[str, str] = {}
    for name in ARTIFACT_FILES:
        try:
            handle = await session.read(name)
        except Exception:
            continue
        data = handle.read()
        dest = os.path.join(out_dir, name)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(dest, mode) as out:
            out.write(data)
        paths[name] = dest
    return paths


# Teleport detector: how many times larger than the 90th-percentile step a single-frame move
# must be to count as an egregious jump. Smooth run/jump motion has per-frame steps bounded by
# velocity, so no step exceeds a few x the p90; a `pos = target` snap is a 10-30x spike. 6x is
# conservative -- it catches real teleports (the observed case was ~15-30x) while leaving normal
# motion, and even a fast projectile, well under the bar.
_TELEPORT_STEP_FACTOR = 6.0
# Need enough steps for a p90 to be meaningful; below this the trace is too short to judge.
_TELEPORT_MIN_STEPS = 8

# Deterministic-validator error codes the sandbox outer check ENFORCES (fails + repairs). These are
# true regardless of the fixed vocabulary the sandbox escapes -- a duplicate id or an out-of-bounds
# coordinate is a real scene.json bug for any mechanics. Everything else the validator flags
# (solvability, reachability, mechanics consistency, undeclared types) is vocabulary-specific and is
# recorded, not enforced -- see outer_sanity_check / deterministic_validation_summary.
_ENFORCED_VALIDATION_CODES = frozenset({"OUT_OF_BOUNDS", "DUPLICATE_ID"})


def _positions_from_replay(data: object) -> list[tuple[float, float]] | None:
    """Best-effort extraction of the main entity's per-frame (x, y) series from a sandbox
    replay.json's parsed contents. Sandbox agents write their own replay shapes, so this tries the
    forms they actually produce and returns the first consistent series -- or None if nothing
    parses, in which case the teleport check is simply skipped (never fails on an unknown shape).
    """
    frames = None
    if isinstance(data, dict):
        for key in ("trace", "frames", "states"):
            if isinstance(data.get(key), list) and data[key]:
                frames = data[key]
                break
    elif isinstance(data, list):
        frames = data
    if not frames:
        return None

    def _xy(frame: object) -> tuple[float, float] | None:
        if not isinstance(frame, dict):
            return None
        # a nested main-entity dict with x/y
        for ent_key in ("hero", "agent", "player"):
            ent = frame.get(ent_key)
            if isinstance(ent, dict) and _is_num(ent.get("x")) and _is_num(ent.get("y")):
                return float(ent["x"]), float(ent["y"])
        # top-level x/y
        if _is_num(frame.get("x")) and _is_num(frame.get("y")):
            return float(frame["x"]), float(frame["y"])
        # a pos/position 2-sequence
        for pos_key in ("pos", "position"):
            p = frame.get(pos_key)
            if isinstance(p, (list, tuple)) and len(p) >= 2 and _is_num(p[0]) and _is_num(p[1]):
                return float(p[0]), float(p[1])
        return None

    positions = [xy for xy in (_xy(f) for f in frames) if xy is not None]
    # require the position to be present in (nearly) every frame -- a partial series means we
    # guessed the wrong shape, so don't judge it.
    if len(positions) < max(_TELEPORT_MIN_STEPS + 1, int(0.8 * len(frames))):
        return None
    return positions


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _teleport_frame(positions: list[tuple[float, float]]) -> tuple[int, float, float] | None:
    """The first single-frame step that is an egregious outlier (a teleport), as
    (frame_index, jump_distance, normal_p90_step), or None if the motion is smooth enough. Scale-
    free: uses the step distribution itself, so it works whether the trace is in pixels or tiles."""
    import math

    steps = [
        math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(positions, positions[1:])
    ]
    if len(steps) < _TELEPORT_MIN_STEPS:
        return None
    ordered = sorted(steps)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    if p90 <= 0:
        return None  # essentially motionless -- nothing to compare against
    threshold = _TELEPORT_STEP_FACTOR * p90
    for i, step in enumerate(steps):
        if step > threshold:
            return i, step, p90
    return None


def outer_sanity_check(out_dir: str) -> tuple[bool, str | None]:
    """Re-parse the sandbox's declared scene.json with the REAL, unmodified schema.

    This is deliberately NOT a solvability check -- that guarantee doesn't survive sandbox
    mode, and pretending otherwise would misrepresent the trade-off. It's a floor against a
    completely malformed or missing result being reported as a success: confirms scene.json
    at least parses against the real schema, that render.png/replay.gif are actually valid,
    non-trivial, *fully decodable* image files rather than a truncated/empty/corrupted write
    the sandbox never verified itself, and that replay.gif is a genuine multi-frame animation
    rather than a single static frame -- three real failure modes observed live (see
    notes.md): a sandbox run once self-reported success with a 43-byte, effectively-empty
    replay.gif; a later run self-reported success with a technically-valid but single-frame
    (non-animated) replay.gif; and a later run still self-reported success with a replay.gif
    that had a correct header/trailer and 59 well-formed frame descriptors (passing
    `Image.verify()` and an `n_frames` check) but malformed LZW-compressed pixel data in every
    frame, which `Image.verify()` does not catch because it validates container structure, not
    pixel data -- `ffmpeg`/a real per-frame `.load()` both fail on it with "LZW decode failed".

    It also applies one *heuristic* motion floor: it best-effort-parses replay.json and fails a
    run whose main entity makes an egregious single-frame position jump (a teleport). This modestly
    widens the check from "the artifacts are well-formed" to "the motion isn't physically absurd" --
    still not a semantic-correctness guarantee (it can't judge whether the game's *rules* are real,
    which is the boundary this mode exists at), just a floor against the specific, repeatedly-
    observed failure of an agent assigning a position straight to a target instead of moving there.
    Best-effort: if replay.json's shape isn't recognized, the motion floor is skipped, never a
    false failure.
    """
    from PIL import Image

    from infinienv.schema.scene_schema import scene_spec_from_dict

    from infinienv.validation.validator import validate_scene

    scene_path = os.path.join(out_dir, "scene.json")
    if not os.path.exists(scene_path):
        return False, "sandbox did not produce scene.json"
    try:
        with open(scene_path) as f:
            scene = scene_spec_from_dict(json.load(f))
    except Exception as exc:
        return False, f"sandbox's scene.json does not parse against the real schema: {exc}"

    # Run the REAL deterministic validator on the scene and ENFORCE its vocabulary-agnostic geometry
    # checks: a scene.json with an out-of-bounds coordinate or a duplicate id is a genuine bug no
    # matter what mechanics the agent's code implements, so those fail the outer check and feed the
    # repair loop. The fixed-vocabulary-dependent checks (solvability, reachability-via-fixed-actions,
    # mechanics consistency, undeclared custom types) are NOT enforced here -- a sandbox scene
    # legitimately escapes them (its real gameplay and win conditions live in run_scene.py, its
    # object types may be custom) -- they're recorded for transparency via
    # `deterministic_validation_summary`. Fixed-vocabulary solvability genuinely can't transfer to
    # arbitrary agent-authored code; the image checks below + the faithfulness audit + the agent's
    # own trace invariants stand in for it rather than a planner guarantee being pretended.
    geometry_errors = [e for e in validate_scene(scene).errors if e.code in _ENFORCED_VALIDATION_CODES]
    if geometry_errors:
        e = geometry_errors[0]
        return False, f"sandbox's scene.json fails the deterministic validator ({e.code}): {e.message}"

    for name, min_bytes in (("render.png", 100), ("replay.gif", 100)):
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            return False, f"sandbox did not produce {name}"
        if os.path.getsize(path) < min_bytes:
            return False, f"sandbox's {name} is only {os.path.getsize(path)} bytes -- likely truncated/empty"
        try:
            with Image.open(path) as img:
                img.verify()
        except Exception as exc:
            return False, f"sandbox's {name} is not a valid image: {exc}"
        # verify() only checks container structure, not that pixel data actually decodes (a
        # real failure mode: a GIF with a correct header/trailer and well-formed frame
        # descriptors but corrupted LZW-compressed pixel data passes verify() while being
        # completely unplayable). Re-open fresh (verify() leaves the image object unusable)
        # and force a real decode.
        try:
            with Image.open(path) as img:
                img.load()
        except Exception as exc:
            return False, f"sandbox's {name} has valid structure but corrupted pixel data: {exc}"

    # Re-open replay.gif fresh again to check it's an actual multi-frame animation, decoding
    # every individual frame -- n_frames alone only counts frame descriptors without decoding
    # them, which is exactly what let the LZW-corruption case above slip through a check that
    # only inspected frame 0.
    gif_path = os.path.join(out_dir, "replay.gif")
    try:
        with Image.open(gif_path) as gif:
            n_frames = getattr(gif, "n_frames", 1)
            if n_frames < 2:
                return False, f"sandbox's replay.gif has only {n_frames} frame(s) -- not an animated replay"
            for i in range(n_frames):
                gif.seek(i)
                gif.load()
    except Exception as exc:
        return False, f"sandbox's replay.gif could not be decoded frame-by-frame: {exc}"

    # Heuristic motion floor: fail an egregious single-frame position jump (a teleport). Best-
    # effort -- unparseable/short/unknown-shape traces are skipped, never failed.
    replay_path = os.path.join(out_dir, "replay.json")
    if os.path.exists(replay_path):
        try:
            with open(replay_path) as f:
                replay_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            replay_data = None
        if replay_data is not None:
            positions = _positions_from_replay(replay_data)
            if positions is not None:
                tp = _teleport_frame(positions)
                if tp is not None:
                    frame_i, jump, p90 = tp
                    return False, (
                        f"sandbox's replay.json shows a teleport: the main entity jumped "
                        f"{jump:.1f} between frames {frame_i} and {frame_i + 1} (normal steps ~{p90:.1f}) "
                        f"-- move via a capped per-frame velocity, don't assign position straight to a target"
                    )

    return True, None


def deterministic_validation_summary(out_dir: str) -> dict:
    """The full deterministic-validator verdict on the sandbox's scene.json, for `metrics.json`.

    Runs the real `validate_scene` and records what the fixed-vocabulary validator thinks of the
    scene: `valid`, the flagged error `codes`, and which of those the outer check actually
    `enforced` (the geometry subset). This is the concrete "sandbox has the validator checks":
    every vocabulary-agnostic geometry error is enforced (see `outer_sanity_check`), and the rest --
    including fixed-vocabulary solvability, which genuinely can't apply to agent-authored gameplay --
    is transparently recorded rather than pretended away."""
    from infinienv.schema.scene_schema import scene_spec_from_dict
    from infinienv.validation.validator import validate_scene

    scene_path = os.path.join(out_dir, "scene.json")
    try:
        with open(scene_path) as f:
            scene = scene_spec_from_dict(json.load(f))
    except Exception as exc:
        return {"ran": False, "note": f"scene.json unavailable: {exc}"}
    result = validate_scene(scene)
    return {
        "ran": True,
        "valid": result.valid,
        "errors": [e.code for e in result.errors],
        "enforced_codes": sorted(_ENFORCED_VALIDATION_CODES),
    }
