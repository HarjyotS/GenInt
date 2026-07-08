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
├── scene.json          # structured SceneSpec ground truth
├── validation.json      # validator checks + repair history
├── metrics.json         # solvability, path length, success, timings
├── render.png           # static top-down visualization
├── replay.gif           # animated replay of the agent solving the task
└── report.md            # human-readable run summary
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
```

## Creativity: mutation + curriculum

`infinienv mutate` takes one valid scene and produces N *validated* variants (repositioned
objects, added obstacles, decoy objects, reversed spawn) — every mutation is re-run through
the full validator + solvability check before being kept, so "infinite generation" never means
"infinite garbage." `infinienv curriculum` emits an easy -> hard prompt suite (open-room pickup
-> obstacle -> cross-room delivery -> key/door -> decoy + long path) that feeds straight into
`infinienv benchmark`.

## Evaluation criteria mapping

| Challenge criterion | How this repo addresses it |
|---|---|
| Creativity | Mutation engine + curriculum generator produce infinite validated variants from one seed scene, not a single text-to-grid demo. |
| Clarity | Narrow P0 scope, one schema (`SceneSpec`) as the shared contract, `report.md` per run, this README. |
| Working output | `--provider mock` runs with zero setup; `pytest` covers schema/validator/reachability/solver/mock/CLI; `metrics.json` reports truthfully (`success` is only `true` if both validation and the solver actually succeeded). |

## Project layout

```text
src/infinienv/
├── cli.py                  # generate / validate / solve / play / benchmark / mutate / curriculum
├── schema/                 # SceneSpec (pydantic) + JSON schema
├── llm/                    # provider protocol + mock / openai_agents / openai_responses / anthropic
├── generation/             # compiler (generate->validate->repair->fallback), templates, mutation, curriculum
├── engine/                 # grid, mutable game state, action legality
├── validation/             # structured errors, reachability (BFS), solvability (full solve), validator
├── navigation/              # A*, symbolic task planner, top-level solve_scene
├── render/                  # render.png (Pillow) + replay.gif
├── evaluation/               # per-run metrics, end-to-end runner, benchmark aggregation
└── artifacts/                 # scene/validation/metrics JSON writers, report.md builder
tests/                          # pytest: schema, validator, reachability, solver, mock generation, CLI, mutation
examples/                        # example prompts + example scene.json files
```

## Limitations and roadmap

- **2D-first by design.** The `SceneSpec` schema is intentionally flat (grid `x`/`y`, not
  `position`/`rotation`/`scale`) so validation stays simple and testable; see CLAUDE.md section
  25 for the planned 3D field additions and exporter targets (Godot, Isaac Lab, MuJoCo, ...).
- **`anthropic` provider is implemented but untested against a live key** in this session (no
  `ANTHROPIC_API_KEY` was available) — it follows the same protocol and JSON-parsing path as
  `openai_responses`, and fails cleanly with a clear `ProviderError` if the key is missing.
  OpenAI Agents SDK remains the recommended/default runtime per the project brief.
- **Renderer is Pillow-only, not pygame.** pygame needs an SDL display context that isn't
  reliably available headless; Pillow produces the same `render.png`/`replay.gif` deliverables
  without that dependency risk.
- **Automatic key-door mutation** (from CLAUDE.md's mutation list) isn't implemented; the other
  four mutation strategies (reposition, add obstacle, add distractor, reverse start) are.
- Godot export, vLLM local-GPU provider, and diversity scoring are P2 stretch goals, not
  implemented in this MVP pass.

See `notes.md` for the running decision log from the build.
