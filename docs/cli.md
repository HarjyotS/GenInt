# CLI reference

[← back to README](../README.md) · [How it works](overview.md)

`generate` is **sandbox-only** (a model writes and runs its own game code; runs on the Claude Agent
SDK by default). The deterministic tools (`validate`/`solve`/`navigate`/`mutate`/`curriculum`/
`benchmark`/`export-dataset`) run the fixed-vocabulary validator + solver over any `scene.json` and
work offline (except `navigate`, which needs a vision key).

```bash
python -m infinienv setup                                                 # guided .env + readiness check
python -m infinienv generate --prompt "..." --seed 42 --out runs/demo   # sandbox agent (Claude SDK)
python -m infinienv navigate examples/vision_demo.json --out runs/vis    # pixel policy plays it
python -m infinienv validate examples/kitchen_can.json
python -m infinienv solve examples/kitchen_can.json --out runs/demo       # deterministic planner
python -m infinienv play examples/kitchen_can.json                        # interactive terminal play
python -m infinienv benchmark examples/prompts.txt --provider mock --out runs/benchmark
python -m infinienv mutate examples/kitchen_can.json --count 10 --out runs/mutations
python -m infinienv curriculum --theme warehouse --levels 5 --out examples/curriculum_warehouse.txt
python -m infinienv curriculum --theme warehouse --levels 5 --run --provider mock --out runs/curriculum
python -m infinienv export-dataset runs/curriculum --out runs/curriculum/dataset.jsonl
python -m infinienv gui                                                    # local web GUI
```

### First-run setup

`infinienv setup` is a guided flow: it prompts for your API keys (hidden input), merges them into a
project `.env` (preserving any other lines), and prints a readiness checklist covering the OpenAI
key, the `openai`/`flask`/`claude-agent-sdk` packages, and the `claude` CLI — each with the exact
`fix` command if it's missing. Scriptable too: `infinienv setup --no-input --openai-key sk-... [--anthropic-key ...] [--env-path PATH]`.

### No-key quickstart

The deterministic tools run offline over any scene. `solve` renders a playable environment and a
replay of the agent completing a code-defined objective:

```bash
python -m infinienv solve examples/kitchen_can.json --out runs/kitchen_can
```

A committed example world lives in `examples/example_world/` (open `render.png` / `replay.gif`, or
launch the GUI and it shows up in the gallery).

## Run artifacts

A `navigate` run writes:

```text
runs/<run_id>/
├── episode.gif    # the exact frames the pixel-only policy saw, in order
├── episode.json   # per step: controller action chosen from pixels + the code-computed reward
└── metrics.json   # vision_success (from is_goal_complete), + vlm_judge_success for contrast
```

A `generate` (sandbox) run writes:

```text
runs/<run_id>/
├── scene.json           # the static SceneSpec layout (run through the real validator)
├── metrics.json         # source, success, outer_sanity_*, audit_*, deterministic_validation, ...
├── replay.json          # the agent's own trace + declared rules
├── render.png           # a rendered frame of the world
├── replay.gif           # animated replay of the agent playing its game
└── sandbox_workspace/   # the exact code the agent wrote and ran (the audit trail)
```

## Runtime providers

These drive the deterministic generation *tools* (`mutate`/`curriculum`/`benchmark`), which propose
a `SceneSpec` and validate/repair it with a full solvability guarantee. (`generate` itself is
sandbox-only — see [How it works](overview.md#sandbox-agents-model-authored-engine-code-per-run-isolated).)

| Provider | Needs a key | Notes |
|---|---|---|
| `mock` | No | Deterministic templates (kitchen delivery, warehouse key/door, obstacle course), seeded by prompt keywords + `--seed`. Always valid and solvable by construction. |
| `openai_agents` | Yes (`OPENAI_API_KEY`) | `ScenePlannerAgent` / `RepairAgent` / `MutationAgent` built with the OpenAI Agents SDK, using non-strict structured output plus `validate_scene`, `get_scene_schema`, `get_supported_mechanics`, and `get_known_mechanics` function tools. |
| `openai_responses` | Yes | Lower-level fallback: a single Responses API call with a non-strict JSON-schema `text.format`. |
| `anthropic` | Yes | Optional Claude provider (Messages API; reads `CL_KEY`/`ANTHROPIC_API_KEY`). |

Keys are read from a project-root `.env` (`OPENAI_API_KEY=sk-...`, or `OP_KEY=sk-...` — both work).
The LLM never executes code or writes files directly on these paths: it only emits `SceneSpec` JSON
and may call the read-only/validate-only tools above.

## Extended mechanics: model-defined object types and interactions

The base schema (see [CLAUDE.md](../CLAUDE.md) section 4) is a small, fixed, validator-enforced
vocabulary — deliberately, so solvability stays guaranteed. But a scene can declare its own
`mechanics`:

- **`custom_object_types`**: new object types (e.g. `"window"`) beyond the base vocabulary. Must be
  declared before any object uses them, or validation rejects the object.
- **`custom_interactions`**: a new verb (`trigger_action`, e.g. `"throw"`) usable against a
  `target_type`, with an optional `must_hold_type` precondition and an ordered list of **effects** —
  `remove_held_object`, `drop_held_object_at_target`, `remove_object`, `unlock_target`,
  `set_object_property`, `teleport_agent`. A fixed, safe, declarative vocabulary interpreted by
  `engine/interactions.py`, never executed code.
- A goal `{"type": "interact", "interaction_id": ..., "target_id": ...}` is satisfied once that
  interaction has actually been performed — planned and executed exactly like `unlock`/`deliver`.

`examples/throw_vase_demo.json` is a hand-authored, always-valid instance (throw a vase out a
window); run it through `solve` or `validate`. New object types with no built-in sprite render as a
labeled gray cell automatically. Validated custom mechanics are cached in
`.infinienv_mechanics_cache.json` and offered back to the model so a definition is reused, not
reinvented per scene.

## Deterministic physics: pushable objects and sliding

Physics is a first-class, **deterministic** part of the engine. Objects can be `"pushable": true`
(the agent shoves them one cell, Sokoban-style) and `"slippery": true` (once shoved, they slide
until they hit something), and a `push` goal is satisfied when a pushable object rests on its target
cell. This stays integer-grid and fully simulable, so the deterministic solver plans it (a joint
agent/box BFS) and the validator *verifies* it — the solvability guarantee holds. Continuous,
force-based physics can't preserve that guarantee and lives in `--sandbox` instead. See
`examples/push_slide_demo.json`.

## Asset pipeline

`--assets {none,local,generated,auto}` on `generate` (default **`auto`**):

- `none` — flat colored cells, no sprites (zero dependencies).
- `local` — checked-in placeholder sprites (`src/infinienv/assets/base/*.png`, no key/network).
- `generated` — real sprites from the OpenAI Images API (`gpt-image-1`); no fallback on failure.
- `auto` — the smart default: OpenAI-generate only the types that benefit (characters, creatures,
  novel props), draw the simple structural tiles locally, reuse a similar cached sprite when one
  exists, and fall back to a local placeholder on failure. Far fewer image calls than `generated`.

Sprites are cached by object **type** in `.infinienv_asset_cache/`, shared across runs. Generation
runs concurrently (`INFINIENV_ASSET_CONCURRENCY`, default 4) at `quality="low"`. Image requests are
**anonymised** before the API call (named characters/brands → neutral archetypes, an original-design
clause appended) and **retried** on a rate limit (backoff) or a moderation rejection (retry once
with a generic description) so they don't just fail — see [CLAUDE.md](../CLAUDE.md) section 9 for the
full account. `asset_manifest.json` records exactly where each sprite came from.

A second backend, local on-device diffusion (`stabilityai/sd-turbo`), is available via
`INFINIENV_SPRITE_BACKEND=diffusion` (`pip install -e ".[diffusion]"`) — no cloud call, no rate
limit; better for textures/simple objects than narrative-heavy characters.

## Creativity: mutation + curriculum + dataset export

- **`mutate`** takes one valid scene and produces N *validated* variants — five deterministic
  strategies (reposition, add obstacle, add distractor, reverse start, theme reskin) plus an
  optional LLM-proposed strategy (`--provider openai_agents --llm-fraction 0.5`). Every candidate
  goes through the full validator + solvability check before being kept.
- **`curriculum`** emits an easy→hard prompt suite (open-room pickup → obstacle → cross-room
  delivery → key/door → decoy + long path). Add `--run` to execute every level into
  `<out>/level_NN/`.
- **`export-dataset <runs_dir> --out dataset.jsonl`** scans executed run folders and emits one JSONL
  row per run with `programmatic_reward` — a real per-goal completion signal
  (`{"deliver_package": 1, "unlock_door": 1, "total": 2}`), sourced from `SolveResult.goal_results`,
  not a single flattened success bit.
