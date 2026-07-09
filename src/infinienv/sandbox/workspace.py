"""Builds the isolated per-run copy of the engine that a sandbox agent edits and executes.

The sandbox agent gets a fresh, real copy of schema/engine/navigation/validation/render --
never the actual installed package -- so whatever it writes or breaks is confined to one run's
`runs/<id>/sandbox_workspace/` directory and can never affect another run or this repo's own
source. See CLAUDE.md's sandbox section and notes.md for the full design rationale and the
concrete trade-off this makes against the validator-wins guarantee everywhere else in this
project: this module extracts *only* the standard artifact files from the sandbox and never
imports or executes the sandboxed .py files in this process.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile

_COPIED_PACKAGES = ("schema", "engine", "navigation", "validation", "render")

_RUN_SCENE_TEMPLATE = '''\
"""Reference entrypoint: validate, solve, and render scene.json in this directory.

You (the sandbox agent) own this file and everything else in this workspace. If the mechanic
you're implementing needs a different simulation loop than solve_scene() provides, edit
navigation/policy.py (or anything else here) and this script will pick up your changes, since
it imports the local copies below, not any installed package. You can also rewrite this script
entirely. The only hard requirement: by the time you finish, this directory must contain
scene.json, metrics.json, replay.json, render.png, and replay.gif.
"""
import json
import sys

from schema.scene_schema import scene_spec_from_dict
from validation.validator import validate_scene
from navigation.policy import solve_scene
from render.image_export import save_render_png
from render.replay_export import save_replay_gif

with open("scene.json") as f:
    scene = scene_spec_from_dict(json.load(f))

validation = validate_scene(scene)
solve = solve_scene(scene)

metrics = {
    "success": bool(validation.valid and solve.success),
    "source": "sandbox",
    "validation_passed": validation.valid,
    "solver_success": solve.success,
    "path_length": len(solve.actions),
    "num_objects": len(scene.objects),
    "num_goals": len(scene.goals),
}
with open("metrics.json", "w") as f:
    json.dump(metrics, f, indent=2)

with open("replay.json", "w") as f:
    json.dump(
        {"actions": solve.actions, "trace": solve.trace, "success": solve.success},
        f,
        indent=2,
        default=str,
    )

save_render_png(scene, "render.png", title=scene.metadata.name)
save_replay_gif(scene, solve.actions, "replay.gif")

print("wrote scene.json, metrics.json, replay.json, render.png, replay.gif")
sys.exit(0 if metrics["success"] else 1)
'''

ARTIFACT_FILES: tuple[str, ...] = ("scene.json", "metrics.json", "replay.json", "render.png", "replay.gif")


def build_workspace_dir(out_dir: str) -> str:
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

    with open(os.path.join(workspace_dir, "run_scene.py"), "w") as f:
        f.write(_RUN_SCENE_TEMPLATE)

    return workspace_dir


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


def outer_sanity_check(out_dir: str) -> tuple[bool, str | None]:
    """Re-parse the sandbox's declared scene.json with the REAL, unmodified schema.

    This is deliberately NOT a solvability check -- that guarantee doesn't survive sandbox
    mode, and pretending otherwise would misrepresent the trade-off. It's a floor against a
    completely malformed or missing result being reported as a success: confirms scene.json
    at least parses against the real schema, that render.png/replay.gif are actually valid,
    non-trivial image files rather than a truncated/empty write the sandbox never verified
    itself, and that replay.gif is a genuine multi-frame animation rather than a single static
    frame -- both real failure modes observed live (see notes.md): a sandbox run once
    self-reported success with a 43-byte, effectively-empty replay.gif, and a later run
    self-reported success with a technically-valid but single-frame (non-animated) replay.gif.
    """
    from PIL import Image

    from infinienv.schema.scene_schema import scene_spec_from_dict

    scene_path = os.path.join(out_dir, "scene.json")
    if not os.path.exists(scene_path):
        return False, "sandbox did not produce scene.json"
    try:
        with open(scene_path) as f:
            scene_spec_from_dict(json.load(f))
    except Exception as exc:
        return False, f"sandbox's scene.json does not parse against the real schema: {exc}"

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

    # img.verify() above leaves the image object unusable for anything further, so re-open
    # replay.gif fresh to check it's an actual multi-frame animation -- a real failure mode
    # observed live: a technically-valid, correctly-sized GIF that was just one static frame,
    # which passes every check above but shows nothing happening (same shape of bug as the
    # truncated-GIF case this function already guards against -- see notes.md).
    gif_path = os.path.join(out_dir, "replay.gif")
    try:
        with Image.open(gif_path) as gif:
            n_frames = getattr(gif, "n_frames", 1)
    except Exception as exc:
        return False, f"sandbox's replay.gif could not be re-opened to check frame count: {exc}"
    if n_frames < 2:
        return False, f"sandbox's replay.gif has only {n_frames} frame(s) -- not an animated replay"

    return True, None
