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
| `openai_agents` | Yes (`OPENAI_API_KEY`) | Default runtime. `ScenePlannerAgent` / `RepairAgent` / `MutationAgent` built with the OpenAI Agents SDK, using non-strict structured output (`AgentOutputSchema(SceneSpec, strict_json_schema=False)` — SceneSpec's open-ended fields, like custom object properties, aren't strict-schema-compatible) plus `validate_scene`, `get_scene_schema`, `get_supported_mechanics`, and `get_known_mechanics` function tools. |
| `openai_responses` | Yes | Lower-level fallback: a single Responses API call with a non-strict JSON-schema `text.format`, no agent orchestration. |
| `anthropic` | Yes | Optional Claude provider (see Limitations). |

By default, if the provider can't produce a valid scene within `MAX_REPAIR_ATTEMPTS`, `generate`
silently falls back to the deterministic template generator so the pipeline still produces a
successful run (useful for unattended/reviewer runs). Pass `--no-fallback` to disable that and
have the command error out instead — useful while iterating on prompts/providers, so a real
generation failure is loud instead of quietly masked by the fallback.

If `OPENAI_API_KEY` isn't set, either use `--provider mock` or drop the key in a project-root
`.env` file (`OPENAI_API_KEY=sk-...`, or `OP_KEY=sk-...` — both are read).

The LLM never executes code or writes files directly: it only emits `SceneSpec` JSON and may
call the read-only/validate-only tools above. All file writes, retries, rendering, and scoring
are owned by this repo's Python code (`generation/`, `evaluation/`, `artifacts/`).

## Extended mechanics: model-defined object types and interactions

The base schema (see CLAUDE.md section 4) is a small, fixed, validator-enforced vocabulary --
deliberately, so solvability stays guaranteed rather than hoped-for. But not every task fits
`table`/`can`/`sink`/`deliver`. So a scene can declare its own `mechanics`:

- **`custom_object_types`**: new object types (e.g. `"window"`) beyond the base vocabulary. Must
  be declared before any object uses them, or validation rejects the object as unsupported.
- **`custom_interactions`**: a new verb (`trigger_action`, e.g. `"throw"`) usable against a
  `target_type`, with an optional `must_hold_type` precondition and an ordered list of
  **effects** -- `remove_held_object`, `drop_held_object_at_target`, `remove_object`,
  `unlock_target`, `set_object_property`, `teleport_agent`. This is a fixed, safe, declarative
  vocabulary interpreted by `engine/interactions.py`, never executed code (see CLAUDE.md section
  5 for the full design rationale and why this doesn't violate "the LLM is never the source of
  truth").
- A goal `{"type": "interact", "interaction_id": ..., "target_id": ...}` is satisfied once that
  interaction has actually been performed -- planned (path to target, pick up a matching object
  first if needed) and executed exactly like `unlock`/`deliver`.

```bash
python -m infinienv generate \
  --provider openai_agents \
  --prompt "Create a small apartment with a window. The agent must pick up a vase from a table and throw it out the window." \
  --seed 3 --out runs/throw_vase --no-fallback
```

Verified live against the real API, valid on the first attempt: the model declared `vase` and
`window` as custom object types and a `throw_through_window` interaction
(`must_hold_type: "vase"`, effect `remove_held_object`) entirely on its own from that prompt --
no hand-authored example in the codebase for this exact scenario beyond the prompt's worked
example. A second, unrelated prompt ("flip a switch to unlock a vault door") produced a genuinely
different mechanic (verb `"flip"`, effects `set_object_property` + `unlock_target` against an
explicit target id) — confirming this generalizes rather than just echoing one canned example.

New object types with no built-in color/sprite render as a labeled gray cell automatically (see
`render/image_export.py`'s fallback) — no per-type hardcoding needed for the renderer to stay
correct.

**Mechanics get cached, not reinvented per scene.** `generation/mechanics_cache.py` persists
every new custom object type/interaction from a validated scene into
`.infinienv_mechanics_cache.json` (gitignored, project-local, same treatment as
`.infinienv_asset_cache/`). The `get_known_mechanics` tool exposes that cache back to the model,
and the prompt instructs it to reuse an existing definition verbatim before inventing a new one
— so "window" keeps meaning the same thing across a session instead of drifting scene to scene.

## Sandbox agents: model-authored engine code, per-run isolated

The extended-mechanics system above is still a fixed, validated vocabulary -- the model composes
existing effect ops, it never writes code. `--sandbox` is the opposite, opt-in trade-off: the
model gets a real, isolated per-run copy of `schema/`/`engine/`/`navigation/`/`validation/`/
`render/` and may read, edit, or run anything in it -- including rewriting the engine itself -- to
build a mechanic the fixed vocabulary genuinely can't express (an adversarial NPC that chases the
agent, a custom win/lose condition). This gives up the validator-guaranteed solvability check
every other run has, and says so plainly: sandbox runs are labeled `"source": "sandbox"` in
`metrics.json`, carrying both the agent's own self-reported success and an independent outer
sanity check (re-parses `scene.json` against the real, unmodified schema; confirms `render.png`/
`replay.gif` are genuine, non-trivial, *animated* images) side by side. If that outer check fails,
the same agent gets the concrete failure fed back and a chance to repair its own work in the same
persistent workspace, up to `--max-repair-attempts` times (default 2) -- the harness keeps
deciding pass/fail, the model just gets more chances against the same real check.

```bash
python -m infinienv generate --sandbox --seed 2 --out runs/chase_demo --prompt \
  "Create a game where the agent must grab a friend and deliver them to a sink. A girl and a boy \
  NPC chase the agent; touching the agent before delivery fails the run."
```

Live-verified: the agent reused the real `SceneSpec` schema (`mechanics.custom_object_types` for
the NPC types, a `sequence` goal for open-door/grab-friend/deliver-friend) and extended
`navigation/policy.py` in place with real chase-stepping logic dispatched off the scene's declared
custom object types -- confirmed by diffing the run's kept `sandbox_workspace/` against this
repo's actual source, not by trusting the agent's summary. The outer process never imports or
executes that sandboxed code itself; it only ever reads back the same five standard artifact files
every other run produces. See CLAUDE.md section 11 for the full design, the isolation boundary,
and what the outer sanity check does and doesn't guarantee.

## CLI

```bash
python -m infinienv generate --prompt "..." --provider mock --seed 42 --out runs/demo
python -m infinienv generate --sandbox --prompt "..." --seed 42 --out runs/demo   # see above
python -m infinienv validate runs/demo/scene.json
python -m infinienv solve runs/demo/scene.json --out runs/demo
python -m infinienv play runs/demo/scene.json          # interactive terminal play
python -m infinienv benchmark examples/prompts.txt --provider mock --out runs/benchmark
python -m infinienv mutate runs/demo/scene.json --count 10 --out runs/mutations
python -m infinienv curriculum --theme warehouse --levels 5 --out examples/curriculum_warehouse.txt
python -m infinienv curriculum --theme warehouse --levels 5 --run --provider mock --out runs/curriculum
python -m infinienv export-dataset runs/curriculum --out runs/curriculum/dataset.jsonl
python -m infinienv gui                                # local web GUI, see below
```

## Web GUI

```bash
pip install -e ".[gui]"
python -m infinienv gui   # opens http://127.0.0.1:5050
```

A single local page: type a prompt, toggle every `generate` setting (provider, seed, `--assets`
mode, `--no-fallback`, max repair attempts, output directory), hit Generate, and watch it work —
stage-by-stage progress streams in live (Server-Sent Events) exactly as it happens, the same
`[n/total] ...` messages the CLI prints, followed by `render.png`/`replay.gif` inline, the metrics
table, and the full `scene.json`. A "Recent runs" strip on the left browses every past run under
`runs/` (from the CLI or the GUI) so you can revisit an old result without regenerating it.

This is a thin frontend on the exact same `evaluation.runner.run_generation` pipeline the CLI
calls — no separate implementation to keep in sync. `flask` is an optional dependency
(`pip install infinienv[gui]`); nothing else in the project needs it, and `python -m infinienv
gui` gives a clear install hint if it's missing.

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
| Creativity | Mutation engine (5 deterministic strategies + LLM-proposed) + curriculum generator + asset pipeline + model-definable object types/interactions produce infinite validated, visually and mechanically distinct variants from one seed scene, not a single text-to-grid demo. |
| Clarity | Narrow P0 scope, one schema (`SceneSpec`) as the shared contract, `report.md` per run, this README. |
| Working output | `--provider mock` runs with zero setup; `pytest` covers schema/validator/reachability/solver/mock/CLI/assets/mutation/curriculum/dataset export/mechanics; `metrics.json` reports truthfully (`success` is only `true` if both validation and the solver actually succeeded). |

## Project layout

```text
src/infinienv/
├── cli.py                  # generate / validate / solve / play / benchmark / mutate / curriculum / export-dataset
├── schema/                 # SceneSpec (pydantic) + JSON schema, incl. Mechanics/InteractGoal
├── llm/                    # provider protocol + mock / openai_agents / openai_responses / anthropic
├── generation/             # compiler (generate->validate->repair->fallback), templates, mutation,
│                           # curriculum, mechanics_cache (persist/reuse custom mechanics)
├── engine/                 # grid, mutable game state, action legality, interactions.py (effect interpreter)
├── validation/             # structured errors, reachability (BFS), solvability (full solve), validator
├── navigation/              # A*, symbolic task planner (incl. interact goals), top-level solve_scene
├── render/                  # render.png (Pillow) + replay.gif, with optional sprite pasting
├── assets/                  # placeholder sprite generator, resolver, OpenAI Images generator, manifest
├── evaluation/               # per-run metrics, end-to-end runner, benchmark aggregation
├── export/                    # dataset.py: runs directory -> JSONL with programmatic_reward
├── sandbox/                    # --sandbox mode: isolated per-run workspace, agent orchestration,
│                                # artifact extraction, outer sanity check (see README above)
└── artifacts/                 # scene/validation/metrics JSON writers, report.md builder
tests/                          # pytest: schema, validator, reachability, solver, mock generation, CLI,
                                # mutation, assets, curriculum, dataset export, interactions, mechanics cache
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
- **2D-first by design.** See CLAUDE.md section 17 for the planned 3D field additions and
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
