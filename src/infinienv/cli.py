"""InfiniEnv command-line interface. See README.md for full usage."""

from __future__ import annotations

import argparse
import json
import os
import sys

from infinienv.llm import get_provider
from infinienv.llm.base import ProviderError


def _load_dotenv() -> None:
    import os

    try:
        from dotenv import load_dotenv

        # override=True: .env is the source of truth here, since a stale
        # OPENAI_API_KEY from a parent shell would otherwise silently win.
        load_dotenv(override=True)
    except ImportError:
        pass
    # Some setups export the OpenAI key under OP_KEY instead of OPENAI_API_KEY.
    if os.environ.get("OP_KEY"):
        os.environ["OPENAI_API_KEY"] = os.environ["OP_KEY"]
    # We deliberately do NOT promote CL_KEY (InfiniEnv's own name for the Anthropic key) to
    # ANTHROPIC_API_KEY. Setting ANTHROPIC_API_KEY hijacks the `claude` CLI's auth: the Claude
    # Agent SDK sandbox backend spawns that CLI, and if ANTHROPIC_API_KEY is set the CLI uses it
    # in preference to the user's claude.ai login -- which broke real runs when that API account
    # ran out of credit while the login itself was fine. CL_KEY stays under its own name so code
    # that explicitly wants the raw key (the `anthropic` provider) can read it, without polluting
    # the global env var the CLI reads. See CLAUDE.md section 11's auth note.


def cmd_setup(args: argparse.Namespace) -> int:
    """Interactive first-run setup: collect API keys into a project `.env`, then report what's
    ready. Run once (`infinienv setup`) and the GUI / CLI pick up the keys automatically."""
    import getpass

    from infinienv.setup_env import MANAGED_KEYS, check_environment, read_env, write_env_keys

    env_path = args.env_path or os.path.join(os.getcwd(), ".env")
    existing = read_env(env_path)

    # Keys can come from flags (scriptable / non-interactive) or the interactive prompt below.
    updates: dict[str, str] = {}
    if args.openai_key:
        updates["OPENAI_API_KEY"] = args.openai_key
    if args.anthropic_key:
        updates["CL_KEY"] = args.anthropic_key

    print("InfiniEnv setup")
    print(f"Writing keys to: {env_path}")
    print()

    interactive = (not args.no_input) and sys.stdin.isatty()
    if interactive:
        print("Paste each key and press Enter. Input is hidden. Press Enter alone to keep the")
        print("current value (or skip an optional one).")
        print()
        for key, description in MANAGED_KEYS.items():
            if key in updates:  # already supplied via a flag
                continue
            have = existing.get(key) or (os.environ.get("OP_KEY") if key == "OPENAI_API_KEY" else None)
            status = "currently set" if have else "not set"
            print(f"{key} ({status})")
            print(f"  {description}")
            try:
                value = getpass.getpass("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSetup cancelled.")
                return 1
            if value:
                updates[key] = value
            print()
    elif not updates:
        # Non-interactive and no flags: don't hang on input(); just show status + how to set keys.
        print("Non-interactive shell and no --openai-key/--anthropic-key given -- skipping prompts.")
        print("Run `infinienv setup` in a terminal, or pass keys as flags. Current status below.")
        print()

    written = write_env_keys(env_path, updates) if updates else []
    if written:
        print(f"Saved to .env: {', '.join(written)}")
        print()

    # Reflect the just-written keys so the checklist is accurate within this process too.
    merged_env = {**os.environ, **read_env(env_path)}
    print("Readiness check:")
    all_ok = True
    for item in check_environment(merged_env):
        mark = "OK " if item["ok"] else "-- "
        print(f"  [{mark}] {item['name']}: {item['detail']}")
        if not item["ok"]:
            all_ok = False
            print(f"          fix: {item['fix']}")
    print()
    if all_ok:
        print("All set. Launch the app:  python -m infinienv gui")
    else:
        print("Some items above need attention (see each 'fix'). The GUI still runs with whatever")
        print("is ready; missing pieces just disable the features that need them.")
        print("Next:  python -m infinienv gui")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    # Sandbox is the only generate mode. The deterministic pipeline stays available for the other
    # commands (validate/solve/mutate/curriculum/benchmark/export-dataset), just not here.
    return _cmd_generate_sandbox(args)


def _cmd_generate_sandbox(args: argparse.Namespace) -> int:
    from infinienv.sandbox.runner import run_sandbox_generation

    print(f"InfiniEnv SANDBOX run: {args.out}")
    print(f"Prompt: {args.prompt}")
    print(
        "Note: sandbox mode lets the agent write and run its own code in an isolated per-run\n"
        "workspace copy. Unlike every other run, this trades away the validator-guaranteed\n"
        "solvability check -- see metrics.json's outer_sanity_* fields and CLAUDE.md. If the\n"
        "first attempt fails an independent outer check, the same agent gets the concrete\n"
        "failure and a chance to repair its own work, up to a bounded number of attempts."
    )
    print()

    def on_stage(msg: str) -> None:
        # LIVE partial-model-output deltas are for the GUI's live bubble; they'd flood the terminal
        # (hundreds of token fragments per turn), so skip them here -- the CLI still shows the
        # finalized Agent:/Thinking: lines and every command/edit.
        if msg.startswith("⁣LIVE⁣"):
            return
        print(f"[sandbox] {msg}")

    try:
        result = run_sandbox_generation(
            args.prompt,
            args.seed,
            args.out,
            max_repair_attempts=args.max_repair_attempts,
            assets_mode=args.assets,
            require_runs_dir=True,
            refine_prompt=args.refine_prompt,
            on_stage=on_stage,
        )
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    print()
    if result["repair_attempts"]:
        print(f"Repaired over {result['repair_attempts']} additional attempt(s) after the first.")
        print()
    if result["run_error"]:
        print(f"Sandbox agent run did not finish cleanly: {result['run_error']}")
        print("(partial artifacts, if any, were still extracted and sanity-checked below)")
        print()
    if result["agent_summary"]:
        print("Sandbox agent summary:")
        print(result["agent_summary"])
        print()
    print(f"Sandbox workspace kept at: {result['workspace_dir']}")
    print("Wrote artifacts:")
    for path in result["artifact_paths"].values():
        print(f"      - {path}")
    print()
    ok = result["success"]
    print("Result: SUCCESS" if ok else "Result: FAILED (see metrics.json outer_sanity_error)")
    return 0 if ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    from infinienv.validation.validator import validate_scene_dict

    with open(args.scene_path) as f:
        data = json.load(f)
    result = validate_scene_dict(data)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.valid else 1


def cmd_solve(args: argparse.Namespace) -> int:
    from infinienv.artifacts.writer import resolve_out_dir, write_json
    from infinienv.navigation.policy import solve_scene
    from infinienv.render.replay_export import save_replay_gif
    from infinienv.schema.scene_schema import scene_spec_from_dict

    with open(args.scene_path) as f:
        scene = scene_spec_from_dict(json.load(f))

    result = solve_scene(scene)
    print(f"Solver: {'SUCCESS' if result.success else 'FAILED'} ({len(result.actions)} actions)")
    if not result.success:
        print(f"Error: {result.error}")

    if args.out:
        out_dir = resolve_out_dir(args.out)
        write_json(
            out_dir,
            "replay.json",
            {
                "actions": result.actions,
                "trace": result.trace,
                "success": result.success,
                "goal_results": result.goal_results,
            },
        )
        save_replay_gif(scene, result.actions, f"{out_dir}/replay.gif")
        print(f"Wrote {out_dir}/replay.json and {out_dir}/replay.gif")

    return 0 if result.success else 1


def cmd_navigate(args: argparse.Namespace) -> int:
    """Play a world with a VISION policy (sees only rendered frames), scored by code.

    If the target is a sandbox run (a directory with sandbox_workspace/, or its scene.json),
    the vision policy FAITHFULLY plays the real sandbox game (its own side-view frames + physics +
    win) inside the sandbox. Otherwise a scene.json is played through the deterministic top-down env."""
    target = args.scene_path
    # Detect a sandbox run: a dir with sandbox_workspace/, or a scene.json whose dir has one.
    run_dir = None
    if os.path.isdir(target) and os.path.isdir(os.path.join(target, "sandbox_workspace")):
        run_dir = target
    elif target.endswith(".json") and os.path.isdir(os.path.join(os.path.dirname(target), "sandbox_workspace")):
        run_dir = os.path.dirname(target)

    if run_dir is not None:
        from infinienv.sandbox.vision_runner import play_sandbox_world

        metrics = play_sandbox_world(
            run_dir,
            args.out,
            backend=args.vision_backend,
            model=args.model,
            max_steps=args.max_steps or 60,
            judge=not args.no_judge,
            on_stage=print,
        )
        print(
            f"Result: {'SUCCESS' if metrics['vision_success'] else 'FAILED'} "
            f"(vision policy played the REAL sandbox game, judged by the game's own win)"
        )
        return 0 if metrics["vision_success"] else 1

    from infinienv.evaluation.vision_runner import run_navigation
    from infinienv.schema.scene_schema import scene_spec_from_dict

    with open(target) as f:
        scene = scene_spec_from_dict(json.load(f))

    metrics = run_navigation(
        scene,
        args.out,
        backend=args.vision_backend,
        model=args.model,
        max_steps=args.max_steps,
        assets_mode=args.assets,
        judge=not args.no_judge,
        on_stage=print,
    )
    print(
        f"Result: {'SUCCESS' if metrics['vision_success'] else 'FAILED'} "
        f"(pixel-only policy, judged by code-defined reward)"
    )
    return 0 if metrics["vision_success"] else 1


def cmd_play(args: argparse.Namespace) -> int:
    import json as _json

    from infinienv.engine.actions import ActionError, apply_action
    from infinienv.engine.grid import Grid
    from infinienv.engine.state import GameState
    from infinienv.navigation.planner import is_goal_complete
    from infinienv.schema.scene_schema import scene_spec_from_dict

    with open(args.scene_path) as f:
        scene = scene_spec_from_dict(_json.load(f))

    grid = Grid(scene)
    state = GameState.from_scene(scene)
    interactions_help = ""
    if scene.mechanics.custom_interactions:
        verbs = ", ".join(sorted({i.trigger_action for i in scene.mechanics.custom_interactions}))
        interactions_help = f" This scene also defines: {verbs} <target_id>."
    print(
        f"Playing {scene.metadata.name}. Commands: w/a/s/d move, p <id> pickup, o <id> drop, "
        f"u <door> <key> unlock, q quit.{interactions_help}"
    )
    custom_verbs = {i.trigger_action for i in scene.mechanics.custom_interactions}
    while True:
        print(f"agent=({state.agent_x},{state.agent_y}) inventory={state.inventory}")
        if all(is_goal_complete(g, state) for g in scene.goals):
            print("All goals complete!")
            return 0
        try:
            raw = input("> ").strip().split()
        except EOFError:
            return 1
        if not raw:
            continue
        cmd, *rest = raw
        try:
            if cmd == "q":
                return 1
            if cmd == "w":
                apply_action(state, grid, {"action": "move_up"}, scene)
            elif cmd == "s":
                apply_action(state, grid, {"action": "move_down"}, scene)
            elif cmd == "a":
                apply_action(state, grid, {"action": "move_left"}, scene)
            elif cmd == "d":
                apply_action(state, grid, {"action": "move_right"}, scene)
            elif cmd == "p" and rest:
                apply_action(state, grid, {"action": "pick_up", "object_id": rest[0]}, scene)
            elif cmd == "o" and rest:
                apply_action(state, grid, {"action": "drop", "object_id": rest[0]}, scene)
            elif cmd == "u" and len(rest) == 2:
                apply_action(state, grid, {"action": "unlock", "door_id": rest[0], "key_id": rest[1]}, scene)
            elif cmd in custom_verbs and rest:
                apply_action(state, grid, {"action": cmd, "target_id": rest[0]}, scene)
            else:
                print("unrecognized command")
        except ActionError as exc:
            print(f"illegal action: {exc}")


def cmd_benchmark(args: argparse.Namespace) -> int:
    from infinienv.evaluation.benchmark import run_benchmark

    provider = get_provider(args.provider)
    summary = run_benchmark(provider, args.prompts_path, args.out, seed=args.seed)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_mutate(args: argparse.Namespace) -> int:
    from infinienv.generation.mutation import mutate_scene_file

    provider = get_provider(args.provider) if args.llm_fraction > 0 else None
    written = mutate_scene_file(
        args.scene_path, args.out, count=args.count, seed=args.seed, provider=provider, llm_fraction=args.llm_fraction
    )
    print(f"Wrote {len(written)} valid mutations to {args.out}")
    return 0


def cmd_curriculum(args: argparse.Namespace) -> int:
    from infinienv.generation.curriculum import run_curriculum, write_curriculum

    if not args.run:
        path = write_curriculum(args.theme, args.out, levels=args.levels)
        print(f"Wrote curriculum prompt list to {path}")
        return 0

    provider = get_provider(args.provider)

    def on_level(i: int, total: int, result) -> None:
        status = "SUCCESS" if result.metrics["success"] else "FAILED"
        print(f"[level {i}/{total}] {status} -> {result.out_dir}")

    results = run_curriculum(args.theme, args.out, levels=args.levels, provider=provider, seed=args.seed, on_level=on_level)
    solved = sum(1 for r in results if r["success"])
    print(f"Ran {len(results)} level(s) into {args.out}: {solved}/{len(results)} succeeded")
    return 0 if solved == len(results) else 1


def cmd_export_dataset(args: argparse.Namespace) -> int:
    from infinienv.export.dataset import export_dataset

    count = export_dataset(args.runs_dir, args.out)
    print(f"Wrote {count} row(s) to {args.out}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from infinienv.gui.app import launch

    launch(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="infinienv", description="InfiniEnv: infinite environment generation via an agent harness.")
    sub = parser.add_subparsers(dest="command", required=True)

    st = sub.add_parser(
        "setup",
        help="First-run setup: interactively save your API keys to a project .env and check that "
        "everything needed to run the GUI (with the Claude sandbox backend) is installed.",
    )
    st.add_argument("--env-path", default=None, dest="env_path", help="Path to the .env file (default: ./.env).")
    st.add_argument("--openai-key", default=None, dest="openai_key", help="Set OPENAI_API_KEY non-interactively.")
    st.add_argument(
        "--anthropic-key", default=None, dest="anthropic_key",
        help="Set CL_KEY (Anthropic key for the optional `anthropic` provider) non-interactively.",
    )
    st.add_argument(
        "--no-input", action="store_true", dest="no_input",
        help="Don't prompt; just apply any --*-key flags and print the readiness check.",
    )
    st.set_defaults(func=cmd_setup)

    g = sub.add_parser(
        "generate",
        help="Generate a playable scene from a prompt. Runs the sandbox agent (the one and only "
        "generate mode): a model writes and runs its own engine code in an isolated per-run "
        "workspace copy, repairing against an outer check + an independent audit.",
    )
    g.add_argument("--prompt", required=True)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument(
        "--out",
        required=True,
        help="Output directory -- must be runs/ or a subdirectory of it (e.g. runs/my_run). "
        "Every generate run's artifacts live under runs/ by convention; the GUI is the one "
        "place that doesn't enforce this, since a reviewer may legitimately want a run "
        "written elsewhere.",
    )
    g.add_argument(
        "--max-repair-attempts",
        type=int,
        default=None,
        dest="max_repair_attempts",
        help="Repair attempts where the same sandbox agent gets the concrete outer-check/audit "
        "failure and a chance to fix its own work in the same persistent workspace (default 2).",
    )
    g.add_argument(
        "--assets",
        default="auto",
        choices=["none", "local", "generated", "auto"],
        help="auto (default): OpenAI-generate the types that benefit (characters/creatures/props), "
        "draw the simple structural tiles locally, and reuse a similar cached sprite when one exists "
        "-- far fewer image calls. none: flat colored cells. local: checked-in placeholder sprites, "
        "no key needed. generated: OpenAI image generation for everything, no fallback. Resolved "
        "inside the sandbox workspace via a copy of assets/resolver.py.",
    )
    g.add_argument(
        "--no-refine-prompt",
        dest="refine_prompt",
        action="store_false",
        help="Skip the best-effort LLM step that expands your prompt into a fuller build spec "
        "before handing it to the agent. On by default; the original and refined prompts are "
        "both recorded in metrics.json.",
    )
    # Accepted no-op: sandbox is the only generate mode now, but keep the flag so existing
    # `generate --sandbox ...` invocations and scripts don't break.
    g.add_argument("--sandbox", action="store_true", help=argparse.SUPPRESS)
    # Sandbox is the ONE generate mode. The deterministic engine stays as the substrate (the sandbox
    # copies schema/engine/navigation/validation/render/assets into every workspace, and its outer
    # check runs the real validator on the scene), and the validate/solve/mutate/curriculum/
    # benchmark/export-dataset tools still use it -- but `generate` no longer has a non-sandbox path.
    g.set_defaults(func=cmd_generate, refine_prompt=True)

    v = sub.add_parser("validate", help="Validate a scene.json file.")
    v.add_argument("scene_path")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("solve", help="Run the deterministic solver against a scene.json file.")
    s.add_argument("scene_path")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_solve)

    n = sub.add_parser(
        "navigate",
        help="Play a scene with a stand-in VISION policy (sees only rendered frames), scored by "
        "code-defined reward. Writes episode.gif/episode.json/metrics.json.",
    )
    n.add_argument("scene_path")
    n.add_argument("--out", required=True)
    n.add_argument("--vision-backend", default="openai", choices=["openai", "claude"], dest="vision_backend")
    n.add_argument("--model", default=None, help="Override the vision model (default per backend).")
    n.add_argument("--max-steps", type=int, default=None, dest="max_steps")
    n.add_argument("--assets", default="none", choices=["none", "local", "generated", "auto"])
    n.add_argument(
        "--no-judge",
        action="store_true",
        dest="no_judge",
        help="Skip the naive VLM-on-pixels judge of the final frame (the code-vs-pixels contrast).",
    )
    n.set_defaults(func=cmd_navigate)

    p = sub.add_parser("play", help="Interactively play a scene.json file in the terminal.")
    p.add_argument("scene_path")
    p.set_defaults(func=cmd_play)

    b = sub.add_parser("benchmark", help="Run generation over a prompt file and aggregate metrics.")
    b.add_argument("prompts_path")
    b.add_argument("--provider", default="mock", choices=["mock", "openai_agents", "openai_responses", "anthropic"])
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--out", required=True)
    b.set_defaults(func=cmd_benchmark)

    m = sub.add_parser("mutate", help="Generate solvable variants of a valid scene.")
    m.add_argument("scene_path")
    m.add_argument("--count", type=int, default=10)
    m.add_argument("--seed", type=int, default=42)
    m.add_argument("--out", required=True)
    m.add_argument("--provider", default="openai_agents", choices=["mock", "openai_agents", "openai_responses", "anthropic"])
    m.add_argument(
        "--llm-fraction",
        type=float,
        default=0.0,
        dest="llm_fraction",
        help="Fraction (0-1) of mutation attempts that ask an LLM (MutationAgent) for a creative "
        "variant instead of a deterministic strategy. 0 (default) is fully deterministic/offline.",
    )
    m.set_defaults(func=cmd_mutate)

    c = sub.add_parser("curriculum", help="Generate an easy-to-hard prompt suite for a theme.")
    c.add_argument("--theme", default="warehouse")
    c.add_argument("--levels", type=int, default=5)
    c.add_argument("--out", required=True)
    c.add_argument(
        "--run",
        action="store_true",
        help="Also execute each level (generate/validate/solve/render) into <out-dir>/level_NN/, "
        "not just write the prompt list.",
    )
    c.add_argument("--provider", default="mock", choices=["mock", "openai_agents", "openai_responses", "anthropic"])
    c.add_argument("--seed", type=int, default=42)
    c.set_defaults(func=cmd_curriculum)

    ed = sub.add_parser("export-dataset", help="Export a directory of executed runs to a JSONL dataset.")
    ed.add_argument("runs_dir", help="Directory containing run subdirectories (each with scene.json + metrics.json).")
    ed.add_argument("--out", required=True)
    ed.set_defaults(func=cmd_export_dataset)

    gu = sub.add_parser("gui", help="Launch the local web GUI (requires `pip install infinienv[gui]`).")
    gu.add_argument("--host", default="127.0.0.1")
    # Most PaaS hosts (Render/Fly/Railway/…) inject the port to bind as $PORT. Default to it so a
    # deployed `infinienv gui` binds the right port with no extra flag; falls back to 5050 locally.
    gu.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5050)))
    gu.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser tab.")
    gu.set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Python fully buffers stdout (instead of flushing per line) whenever it isn't a live
    # terminal -- e.g. redirected to a file/pipe, exactly the case when a run is kicked off in
    # the background for later inspection. Without this, every `on_stage`/progress print for a
    # long-running `generate`/`--sandbox` command sits in the buffer and only appears once the
    # process exits, making a run look silent/stuck while it's actually making real progress.
    # Reconfigure once here so every command's output streams live regardless of destination.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass  # stdout doesn't support reconfigure (e.g. captured by a test runner) -- harmless

    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ProviderError as exc:
        print(f"Provider error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
