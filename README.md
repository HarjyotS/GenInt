# InfiniEnv

**Infinite Environment Generation via an Agent Harness** — a 2D-first agent harness that
converts natural-language commands into playable, verified environments.

> A model proposes. The harness verifies. The agent proves.

A language model (OpenAI Agents SDK by default) compiles a text prompt into a typed
`SceneSpec` JSON world. Deterministic Python code validates it, repairs it via LLM feedback
if invalid, builds a playable gridworld, solves it with an A*/symbolic planner, and emits
replayable proof (`render.png`, `replay.gif`, `metrics.json`) that the objective was completed.
The LLM is never the source of truth — schema validation, reachability, solvability, and goal
completion are all deterministic and testable.

---

## Run it in 60 seconds

```bash
pip install -e .

# No API key required:
python -m infinienv generate \
  --provider mock \
  --prompt "Create a kitchen where the agent picks up a can from the table and drops it in the sink." \
  --seed 42 \
  --out runs/kitchen_can

# With an OpenAI key (OPENAI_API_KEY or OP_KEY in .env):
python -m infinienv generate \
  --provider openai_agents \
  --prompt "Create a warehouse where the agent must find a key, unlock a door, pick up a package, and deliver it to the exit." \
  --seed 7 \
  --out runs/warehouse_key
```

Either command prints stage-by-stage progress and writes:

```text
runs/<run_id>/
├── scene.json           # structured SceneSpec ground truth
├── validation.json      # validator checks + repair history
├── metrics.json         # solvability, path length, success, timings
├── replay.json          # action trace + per-goal completion (goal_results)
├── render.png           # static top-down visualization
├── replay.gif           # animated replay of the agent solving the task
├── report.md            # human-readable run summary
├── asset_plan.json      # (only with --assets != none) requested sprite types
└── asset_manifest.json  # (only with --assets != none) resolved sprite source per type
```

## Pipeline

```text
Text Prompt
  -> OpenAI Agents SDK ScenePlannerAgent  (LLM proposes)
  -> SceneSpec JSON DSL
  -> deterministic validator (schema, bounds, collisions, reachability, solvability)
  -> RepairAgent loop if invalid (max 3 attempts, then template fallback)
  -> playable 2D gridworld
  -> A* + symbolic task planner solves the goals   (harness proves)
  -> replay.gif + metrics.json + report.md
```

The most important design rule: **the validator wins**. The LLM may propose, repair, or mutate
a scene, but schema validation, object placement, collisions, reachability, pathfinding,
inventory transitions, goal completion, and scoring are all deterministic code paths, covered
by `tests/`.

## Runtime providers

| Provider | Needs a key | Notes |
|---|---|---|
| `mock` | No | Deterministic templates (kitchen delivery, warehouse key/door, obstacle course), seeded by prompt keywords + `--seed`. Always valid and solvable by construction. |
| `openai_agents` | Yes (`OPENAI_API_KEY`) | Default runtime. `ScenePlannerAgent` / `RepairAgent` built with the OpenAI Agents SDK, using structured output (`output_type=SceneSpec`) plus `validate_scene`, `get_scene_schema`, and `get_supported_mechanics` function tools. |
| `openai_responses` | Yes | Lower-level fallback: a single Responses API call with a strict JSON-schema `text.format`, no agent orchestration. |
| `anthropic` | Yes | Optional Claude provider (see Limitations). |

By default, if the provider can't produce a valid scene within `MAX_REPAIR_ATTEMPTS`, `generate`
silently falls back to the deterministic template generator so the pipeline still produces a
successful run (useful for unattended/reviewer runs). Pass `--no-fallback` to disable that and
have the command error out instead — useful while iterating on prompts/providers, so a real
generation failure is loud instead of quietly masked by the fallback.

If `OPENAI_API_KEY` isn't set, either use `--provider mock` or drop the key in a project-root
`.env` file (`OPENAI_API_KEY=sk-...`, or `OP_KEY=sk-...` — both are read).

The LLM never executes code or writes files directly: it only emits `SceneSpec` JSON and may
call the three read-only/validate-only tools above. All file writes, retries, rendering, and
scoring are owned by this repo's Python code (`generation/`, `evaluation/`, `artifacts/`).

## CLI

```bash
python -m infinienv generate --prompt "..." --provider mock --seed 42 --out runs/demo
python -m infinienv validate runs/demo/scene.json
python -m infinienv solve runs/demo/scene.json --out runs/demo
python -m infinienv play runs/demo/scene.json          # interactive terminal play
python -m infinienv benchmark examples/prompts.txt --provider mock --out runs/benchmark
python -m infinienv mutate runs/demo/scene.json --count 10 --out runs/mutations
python -m infinienv curriculum --theme warehouse --levels 5 --out examples/curriculum_warehouse.txt
python -m infinienv curriculum --theme warehouse --levels 5 --run --provider mock --out runs/curriculum
python -m infinienv export-dataset runs/curriculum --out runs/curriculum/dataset.jsonl
```

## Asset pipeline

`--assets {none,local,generated,auto}` on `generate` (default `none`, which keeps the original
flat-colored-cell rendering exactly as before):

- `none` — flat colored cells, no sprites (original/default behavior, zero dependencies).
- `local` — the checked-in placeholder sprites in `src/infinienv/assets/base/*.png`
  (`src/infinienv/assets/placeholder_gen.py`, simple Pillow-drawn icons, no key/network needed).
- `generated` — real sprites from the OpenAI Images API (`gpt-image-1`; `INFINIENV_IMAGE_MODEL`
  to override) only; no silent fallback if generation fails.
- `auto` — generated, falling back to the local placeholder (with a note in
  `asset_manifest.json`) if generation is unavailable.

Sprites are cached by object **type** (not per-scene) in `.infinienv_asset_cache/` at the repo
root, shared across every run — generating a "table" sprite once means every future scene with a
table reuses it instead of calling the API again; `--assets auto`/`generated` only ever calls out
for types not already cached. `asset_manifest.json` records exactly where each sprite came from
(`local` / `generated` / `none`) so a run never silently claims a generated asset that wasn't
actually generated.

## Creativity: mutation + curriculum + dataset export

`infinienv mutate` takes one valid scene and produces N *validated* variants — five deterministic
strategies (reposition objects, add obstacle, add distractor, reverse start, theme reskin) plus
an optional LLM-proposed strategy (`--provider openai_agents --llm-fraction 0.5`: a `MutationAgent`
proposes creative layout changes while preserving the goal structure). Every candidate, LLM or
deterministic, goes through the same full validator + solvability check before being kept, so
"infinite generation" never means "infinite garbage."

`infinienv curriculum` emits an easy -> hard prompt suite (open-room pickup -> obstacle ->
cross-room delivery -> key/door -> decoy + long path). Add `--run` to actually execute every
level (generate/validate/solve/render) into `<out>/level_NN/`, not just write the prompt list.

`infinienv export-dataset <runs_dir> --out dataset.jsonl` scans a directory of executed run
folders (anything with `scene.json` + `metrics.json` — curriculum level dirs, benchmark
`prompt_NNN/` dirs, etc.) and emits one JSONL row per run with `programmatic_reward`: a real
per-goal completion signal (`{"deliver_package": 1, "unlock_door": 1, "total": 2}`), sourced from
`SolveResult.goal_results` in `replay.json`, not a single flattened success bit.

## Evaluation criteria mapping

| Challenge criterion | How this repo addresses it |
|---|---|
| Creativity | Mutation engine (5 deterministic strategies + LLM-proposed) + curriculum generator + asset pipeline produce infinite validated, visually distinct variants from one seed scene, not a single text-to-grid demo. |
| Clarity | Narrow P0 scope, one schema (`SceneSpec`) as the shared contract, `report.md` per run, this README. |
| Working output | `--provider mock` runs with zero setup; `pytest` covers schema/validator/reachability/solver/mock/CLI/assets/mutation/curriculum/dataset export; `metrics.json` reports truthfully (`success` is only `true` if both validation and the solver actually succeeded). |

## Project layout

```text
src/infinienv/
├── cli.py                  # generate / validate / solve / play / benchmark / mutate / curriculum / export-dataset
├── schema/                 # SceneSpec (pydantic) + JSON schema
├── llm/                    # provider protocol + mock / openai_agents / openai_responses / anthropic
├── generation/             # compiler (generate->validate->repair->fallback), templates, mutation, curriculum
├── engine/                 # grid, mutable game state, action legality
├── validation/             # structured errors, reachability (BFS), solvability (full solve), validator
├── navigation/              # A*, symbolic task planner, top-level solve_scene
├── render/                  # render.png (Pillow) + replay.gif, with optional sprite pasting
├── assets/                  # placeholder sprite generator, resolver, OpenAI Images generator, manifest
├── evaluation/               # per-run metrics, end-to-end runner, benchmark aggregation
├── export/                    # dataset.py: runs directory -> JSONL with programmatic_reward
└── artifacts/                 # scene/validation/metrics JSON writers, report.md builder
tests/                          # pytest: schema, validator, reachability, solver, mock generation, CLI,
                                # mutation, assets, curriculum, dataset export
examples/                        # example prompts + example scene.json files
```

## Limitations and roadmap

`PATHWAY.md` sketches a considerably larger version of this project (renamed package layout,
pygame-ce rendering, a typer/rich CLI, and an incompatible v0.2 `SceneSpec` with `tiles`/
`goal.steps`). The asset pipeline, dataset export, curriculum execution, and LLM-driven mutations
above were built as additions on top of the existing, real-API-verified architecture; the
following were deliberately *not* done, to avoid rewriting a working system for redundant gain
(see `notes.md` for the full reasoning):

- **No package rename/restructure** (`agents/`/`providers/`/`core/`/`export/` split) — the
  current `schema/llm/generation/engine/validation/navigation/render/evaluation/artifacts`
  layout already separates the same concerns and matches CLAUDE.md's suggested structure.
- **No `SceneSpec` v0.2 migration** (`tiles`, `goal: {type: sequence, steps: [...]}`,
  `position: [x, y]`) — the current schema already expresses the same mechanics (ordered
  top-level goals, `locked`/`key_id` for doors, `solid` for blocking) and a breaking schema
  change would invalidate every real-API-verified scene and test in this repo.
- **No typer/rich CLI rewrite** — the existing argparse CLI is tested and already gives
  reviewer-friendly stage-by-stage output; not worth the churn for a cosmetic framework swap.
- **No pygame-ce renderer** — pygame needs an SDL display context that's a real risk headless;
  Pillow already produces `render.png`/`replay.gif` reliably, and now optionally with real
  sprites via the asset pipeline above.
- **2D-first by design.** See CLAUDE.md section 25 for the planned 3D field additions and
  exporter targets (Godot, Isaac Lab, MuJoCo, ...).
- **`anthropic` provider is implemented but untested against a live key** in this session (no
  `ANTHROPIC_API_KEY` was available) — it follows the same protocol and JSON-parsing path as
  `openai_responses`, and fails cleanly with a clear `ProviderError` if the key is missing.
  OpenAI Agents SDK remains the recommended/default runtime per the project brief.
- **`theme_reskin` mutation is metadata-only.** The object-type vocabulary is a fixed,
  validator-enforced enum (by design, for schema simplicity), so a reskin changes
  `metadata.theme`/name, not a distinct per-theme object/art set.
- **Automatic key-door-dependency mutation** (from CLAUDE.md's mutation list) isn't implemented
  as a deterministic strategy; the LLM-proposed mutation path can add one opportunistically.
- Godot export, vLLM local-GPU provider, and diversity scoring are P2 stretch goals, not
  implemented in this MVP pass.

See `notes.md` for the running decision log from the build.
