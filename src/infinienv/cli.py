"""InfiniEnv command-line interface. See README.md for full usage."""

from __future__ import annotations

import argparse
import json
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


def cmd_generate(args: argparse.Namespace) -> int:
    from infinienv.evaluation.runner import run_generation

    provider = get_provider(args.provider)
    print(f"InfiniEnv run: {args.out}")
    print(f"Prompt: {args.prompt}")
    print(f"Provider: {args.provider}")
    print(f"Seed: {args.seed}")
    print()

    stage_num = [0]
    total_stages = 6 if args.assets == "none" else 7

    def on_stage(msg: str) -> None:
        stage_num[0] += 1
        print(f"[{stage_num[0]}/{total_stages}] {msg}")

    result = run_generation(
        provider,
        args.prompt,
        args.seed,
        args.out,
        max_repair_attempts=args.max_repair_attempts,
        allow_fallback=not args.no_fallback,
        assets_mode=args.assets,
        on_stage=on_stage,
    )
    print()
    ok = result.metrics["success"]
    print("Result: SUCCESS" if ok else "Result: FAILED (see report.md)")
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

    g = sub.add_parser("generate", help="Generate, validate, build, and solve a scene from a prompt.")
    g.add_argument("--prompt", required=True)
    g.add_argument("--provider", default="mock", choices=["mock", "openai_agents", "openai_responses", "anthropic"])
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--out", required=True)
    g.add_argument("--max-repair-attempts", type=int, default=None, dest="max_repair_attempts")
    g.add_argument(
        "--no-fallback",
        action="store_true",
        help="Error out instead of silently falling back to the template generator when the "
        "provider can't produce a valid scene within the repair budget.",
    )
    g.add_argument(
        "--assets",
        default="none",
        choices=["none", "local", "generated", "auto"],
        help="none: flat colored cells (default). local: checked-in placeholder sprites, no key "
        "needed. generated: OpenAI image generation only, no silent fallback. auto: generated "
        "then local placeholder fallback.",
    )
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("validate", help="Validate a scene.json file.")
    v.add_argument("scene_path")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("solve", help="Run the deterministic solver against a scene.json file.")
    s.add_argument("scene_path")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_solve)

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
    gu.add_argument("--port", type=int, default=5050)
    gu.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser tab.")
    gu.set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
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
