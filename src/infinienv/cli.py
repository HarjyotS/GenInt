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
    total_stages = 6

    def on_stage(msg: str) -> None:
        stage_num[0] += 1
        print(f"[{stage_num[0]}/{total_stages}] {msg}")

    result = run_generation(
        provider,
        args.prompt,
        args.seed,
        args.out,
        max_repair_attempts=args.max_repair_attempts,
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
        write_json(out_dir, "replay.json", {"actions": result.actions, "trace": result.trace, "success": result.success})
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
    print(f"Playing {scene.metadata.name}. Commands: w/a/s/d move, p <id> pickup, o <id> drop, u <door> <key> unlock, q quit.")
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
                apply_action(state, grid, {"action": "move_up"})
            elif cmd == "s":
                apply_action(state, grid, {"action": "move_down"})
            elif cmd == "a":
                apply_action(state, grid, {"action": "move_left"})
            elif cmd == "d":
                apply_action(state, grid, {"action": "move_right"})
            elif cmd == "p" and rest:
                apply_action(state, grid, {"action": "pick_up", "object_id": rest[0]})
            elif cmd == "o" and rest:
                apply_action(state, grid, {"action": "drop", "object_id": rest[0]})
            elif cmd == "u" and len(rest) == 2:
                apply_action(state, grid, {"action": "unlock", "door_id": rest[0], "key_id": rest[1]})
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

    written = mutate_scene_file(args.scene_path, args.out, count=args.count, seed=args.seed)
    print(f"Wrote {len(written)} valid mutations to {args.out}")
    return 0


def cmd_curriculum(args: argparse.Namespace) -> int:
    from infinienv.generation.curriculum import write_curriculum

    path = write_curriculum(args.theme, args.out, levels=args.levels)
    print(f"Wrote curriculum to {path}")
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
    m.set_defaults(func=cmd_mutate)

    c = sub.add_parser("curriculum", help="Generate an easy-to-hard prompt suite for a theme.")
    c.add_argument("--theme", default="warehouse")
    c.add_argument("--levels", type=int, default=5)
    c.add_argument("--out", required=True)
    c.set_defaults(func=cmd_curriculum)

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
