# InfiniEnv

**Infinite Environment Generation via an Agent Harness** — a 2D-first harness that turns
natural-language commands into playable, *verified* environments, then lets a **vision-based
policy** (one that sees only rendered frames) play them while a **code-defined** reward decides
whether it succeeded.

> A model proposes. The harness verifies. A pixel-policy proves.

The core bet, straight from the challenge brief: **code-defined objectives beat a VLM checking
pixels.** So generation is semantic (an LLM writes the world) but *truth* is deterministic Python
— schema validation, reachability, solvability, goal completion, and reward are all code, never
the model's say-so.

The headline demo (`navigate`) closes the exact loop the brief is about: a policy that observes
*rendered frames* and emits *controller actions* plays a generated world, and whether it *reached
the goal* is judged by `is_goal_complete` over game state — **not** by looking at the pixels. That
one split is the whole thesis, made runnable:

- **Post-training environments** — `InfiniEnv` is a Gymnasium-style `reset()/step()` env whose
  observation is a frame and whose action is a controller input; drop any frame-in/action-out
  policy into it.
- **Code-level objectives** — reward comes from `navigation/planner.py::is_goal_complete`, a pure
  function of state, so "the can is on the sink" is decided by code, reliably.
- **Reward from code, applied to pixels** — the `navigate` run also records a naive *VLM-judges-
  the-final-frame* verdict beside the code truth; where they disagree is a live demonstration of
  why the code signal is the trustworthy one to train a reward model on.

---

## Run it in 60 seconds

```bash
pip install -e ".[openai]"

# THE HEADLINE: a VISION policy (sees only rendered frames) plays a generated world, and a
# CODE-defined reward decides success. Writes episode.gif (the frames it saw), episode.json
# (its action + the code reward each step), and metrics.json (vision_success from code truth,
# plus a naive VLM-on-pixels verdict for contrast). Needs an OpenAI key (OPENAI_API_KEY/OP_KEY):
python -m infinienv navigate examples/vision_demo.json --out runs/vision_demo

# `generate` -- a model writes and runs a small game for your prompt, plays it, and an
# independent auditor checks it didn't fake the mechanic. The scene it declares is run
# through the real deterministic validator (bounds/ids enforced):
python -m infinienv generate \
  --prompt "Create a procedurally generated cave with spike hazards and glowing gems; pick a safe route, collect at least two gems, and reach the exit." \
  --seed 42 --assets generated --out runs/cave

# No API key? The deterministic tools run offline over any scene. `solve` renders a
# playable environment and a replay of the agent completing a code-defined objective:
python -m infinienv solve examples/kitchen_can.json --out runs/kitchen_can
```

No key at all? A committed example world lives in `examples/example_world/` (open `render.png` /
`replay.gif`, or launch the GUI and it shows up in the gallery).

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

(`solve <scene.json>` writes `replay.json` + `replay.gif` — the deterministic agent completing a
code-defined objective, no key needed.)

## Pipeline

```text
Text Prompt
  -> LLM writes the world            (a model proposes)
  -> SceneSpec JSON DSL  /  agent-authored game code (--sandbox)
  -> deterministic validator + independent audit    (the harness verifies)
  -> playable 2D gridworld
  -> A*/symbolic planner solves it, AND/OR
     a VISION policy plays it from rendered frames   (a pixel-policy proves)
  -> reward from is_goal_complete (code truth, not pixels)
  -> render.png + replay.gif / episode.gif + metrics.json
```

The most important design rule: **the validator wins**. The LLM may propose, repair, or mutate
a scene, but schema validation, object placement, collisions, reachability, pathfinding,
inventory transitions, goal completion, and reward are all deterministic code paths, covered by
`tests/`.

## The vision-policy loop (`navigate`)

`engine/env.py::InfiniEnv` is a Gymnasium-compatible env — `obs, info = env.reset()` /
`obs, reward, terminated, truncated, info = env.step(action)` — where the **observation is a
rendered frame** and the **action is a controller input** (`forward/back/left/right/interact/wait`,
the 2D subset of the brief's move-forward/…/mouse-ΔX/ΔY interface). Reward is code-defined:
`is_goal_complete(goal, state)` over the real `GameState`, +1 the step a goal first completes.

`navigate` runs a **stand-in vision policy** (a VLM — General Intuition's own policy isn't
available to us, so this proves the *interface and the reward loop*, not a competing policy)
through that env. The policy is handed *only the frame* plus the goal in words and returns a
controller action each step; the env decides success in code. It never sees `GameState`.

```bash
python -m infinienv navigate examples/vision_demo.json --out runs/vision_key
# --vision-backend openai|claude   --model ...   --max-steps N   --assets ...   --no-judge
```

`metrics.json` records `vision_success` (the pixel policy's outcome, **judged by code**) and,
for deliberate contrast, `vlm_judge_success` — a naive "does this final frame look done?" VLM
verdict — plus whether the two agreed. When they disagree, that's the brief's point made concrete:
the code signal is the one you'd trust to train a reward model on.

**Faithful play of a sandbox world.** A `--sandbox` world is a real game (often a side-view
platformer) whose physics/rendering/win live in the agent's own code, so playing it through the
top-down engine mis-renders and mis-plays it. Instead, a sandbox world is played **faithfully**: the
vision policy drives the *actual* game — its own frames, physics, and win condition — **inside the
sandbox** (the trusted process only reads back `episode.gif` + metrics, never runs the game code).
This works because every generated `run_scene.py` exposes a `make_env()` (reset/step over its real
frames + a code-defined reward). In the GUI, picking a sandbox world in Navigate routes here
automatically; on the CLI, `navigate <run_dir>` faithfully plays it while `navigate <scene.json>`
plays deterministically.

## Runtime providers

| Provider | Needs a key | Notes |
|---|---|---|
| `mock` | No | Deterministic templates (kitchen delivery, warehouse key/door, obstacle course), seeded by prompt keywords + `--seed`. Always valid and solvable by construction. |
| `openai_agents` | Yes (`OPENAI_API_KEY`) | Default runtime. `ScenePlannerAgent` / `RepairAgent` / `MutationAgent` built with the OpenAI Agents SDK, using non-strict structured output (`AgentOutputSchema(SceneSpec, strict_json_schema=False)` — SceneSpec's open-ended fields, like custom object properties, aren't strict-schema-compatible) plus `validate_scene`, `get_scene_schema`, `get_supported_mechanics`, and `get_known_mechanics` function tools. |
| `openai_responses` | Yes | Lower-level fallback: a single Responses API call with a non-strict JSON-schema `text.format`, no agent orchestration. |
| `anthropic` | Yes | Optional Claude provider (see Limitations). |

These providers drive the deterministic generation *tools* (`mutate`/`curriculum`/`benchmark`),
which propose a `SceneSpec` and validate/repair it with a full solvability guarantee. `generate`
itself is sandbox-only (a model writes and runs its own game code — see below); its repair loop
runs against the outer sanity check + audit, not an LLM repair agent. Keys are read from a
project-root `.env` (`OPENAI_API_KEY=sk-...`, or `OP_KEY=sk-...` — both work).

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

## Deterministic physics: pushable objects and sliding

Physics is a first-class, **deterministic** part of the engine — not something the model has to
reinvent. Objects can be `"pushable": true` (the agent shoves them one cell, Sokoban-style,
instead of being blocked) and `"slippery": true` (once shoved, they slide until they hit
something — ice-puck momentum), and a `push` goal is satisfied when a pushable object rests on its
target cell:

```json
{
  "objects": [
    {"id": "crate_1", "type": "box", "x": 4, "y": 4, "solid": true, "pushable": true},
    {"id": "switch_1", "type": "sink", "x": 7, "y": 4, "solid": false}
  ],
  "goals": [{"id": "shove", "type": "push", "object_id": "crate_1", "target_id": "switch_1"}]
}
```

This stays integer-grid and fully simulable, so the deterministic solver plans it (a joint
agent/box BFS) and the validator *verifies* it — the solvability guarantee holds. A slippery
object can only stop against an obstacle, so a mid-floor target for one is correctly reported
`UNSOLVABLE`, not hoped over. Continuous, force-based physics (pymunk-style) can't preserve that
guarantee and lives in `--sandbox` instead (below). Slides render as smooth gliding motion (the
replay inserts per-cell frames), so it looks good, especially with `--assets`. Live-verified
first-try against the real API on "push a heavy crate onto a floor switch, then reach the exit."
See `examples/push_slide_demo.json`.

## Sandbox agents: model-authored engine code, per-run isolated

The extended-mechanics system above is still a fixed, validated vocabulary -- the model composes
existing effect ops, it never writes code. `--sandbox` is the opposite, opt-in trade-off: the
model gets a real, isolated per-run copy of `schema/`/`engine/`/`navigation/`/`validation/`/
`render/`/`assets/` and may read, edit, or run anything in it -- including rewriting the engine
itself -- to build a mechanic the fixed vocabulary genuinely can't express (an adversarial NPC
that chases the agent, a custom win/lose condition). This gives up the validator-guaranteed
solvability check every other run has, and says so plainly: sandbox runs are labeled `"source":
"sandbox"` in `metrics.json`, carrying both the agent's own self-reported success and an
independent outer sanity check (re-parses `scene.json` against the real, unmodified schema;
confirms `render.png`/`replay.gif` are genuine, non-trivial, *animated* images) side by side. If
that outer check fails, the same agent gets the concrete failure fed back and a chance to repair
its own work in the same persistent workspace, up to `--max-repair-attempts` times (default 3) --
the harness keeps deciding pass/fail, the model just gets more chances against the same real
check. `--assets {local,generated,auto}` applies to sandbox runs the same as any other -- the
agent's reference `run_scene.py` resolves real sprites via the copied `assets/resolver.py` before
rendering.

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

`generate` is **sandbox-only** (a model writes and runs its own game code; needs an OpenAI key).
The deterministic tools below (`validate`/`solve`/`navigate`/`mutate`/`curriculum`/`benchmark`/
`export-dataset`) run the fixed-vocabulary validator + solver over any `scene.json` and work
offline (except `navigate`, which needs a vision key):

```bash
python -m infinienv generate --prompt "..." --seed 42 --out runs/demo        # sandbox (see above)
python -m infinienv navigate examples/vision_demo.json --out runs/vision    # pixel policy plays it
python -m infinienv validate examples/kitchen_can.json
python -m infinienv solve examples/kitchen_can.json --out runs/demo          # deterministic planner
python -m infinienv play examples/kitchen_can.json                           # interactive terminal play
python -m infinienv benchmark examples/prompts.txt --provider mock --out runs/benchmark
python -m infinienv mutate examples/kitchen_can.json --count 10 --out runs/mutations
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

A single local page with a **Generate ⟷ Navigate** mode toggle:

- **Generate**: type a prompt, pick the sandbox agent runtime/model and `--assets` mode, hit
  Generate, and watch the agent work — its decisions, code writes, shell commands, chosen assets,
  and the audit verdict stream in live (Server-Sent Events) as a structured activity view, followed
  by `render.png`/`replay.gif` inline and the metrics.
- **Navigate**: pick an example scene and a vision backend/model, hit Run — a **pixel-only vision
  policy** plays the world, its per-step controller actions stream as a play log, and the result
  shows the `episode.gif` plus verdict cards contrasting **code truth** (`vision_success`) against a
  **VLM-on-pixels** guess. This is the headline loop, runnable in the browser.

A "Recent runs" strip browses every past run under `runs/` (and the committed
`examples/example_world/`), badged by kind (sandbox ▣ / vision ◎), so you can revisit a result
without regenerating it.

This is a thin frontend on the exact same sandbox pipeline the CLI calls — no separate
implementation to keep in sync. `flask` is an optional dependency
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

Generation for every still-uncached type in a scene runs **concurrently** (a small bounded thread
pool, `INFINIENV_ASSET_CONCURRENCY`, default 4) rather than one type at a time, and defaults to
`quality="low"` (`INFINIENV_IMAGE_QUALITY` to override) since every sprite gets resized to 64x64
regardless of source quality — live-verified: 4 novel sprites in ~16s concurrently vs. the ~4x
that sequentially, with no visible loss of quality at 64x64.

### Sprite generation backend: OpenAI (default) or local diffusion

`generated`/`auto` mode's actual image-generation backend is controlled by
`INFINIENV_SPRITE_BACKEND` (`openai`, the default, or `diffusion`), independent of the `--assets`
mode value itself — both backends share the exact same contract, so nothing else about how sprites
are resolved/cached/manifested changes based on which one runs.

- `openai` (default) — the OpenAI Images API path. Real failure modes to know about: account rate
  limits (`gpt-image-1` is commonly limited to a handful of requests/minute) and content-moderation
  rejections for some character descriptions; either way the affected sprites fail cleanly and are
  recorded in `metrics.json`'s `asset_notes` field (the real per-type error, not silently
  swallowed) rather than pretending nothing went wrong.
- `diffusion` — a small local on-device model (`stabilityai/sd-turbo` by default,
  `INFINIENV_DIFFUSION_MODEL` to override), opt-in via `INFINIENV_SPRITE_BACKEND=diffusion`, no
  cloud call and no rate limit. Requires the optional `diffusion` extra: `pip install
  infinienv[diffusion]` (`torch`, `diffusers`, `transformers`, `rembg[cpu]`, `accelerate`).
  Auto-selects `cuda` → `mps` → `cpu`. **License note**: the default model, SD-Turbo, ships under
  the Stability AI Community License (free for research/personal/small-business use; a revenue
  threshold applies beyond that) — not as permissive as this project's other dependencies. Local
  pipelines have no request-time "transparent background" feature the way the OpenAI API does, so
  discrete objects go through a real background-removal model (`rembg`/U2Net) after generation.
  **Was briefly the default**, reverted after live-verified quality problems with character/hero
  sprites (a small, fast, weakly-prompt-adherent model isn't a great fit for narrative-heavy
  character descriptions) — still a good opt-in for textures/simple objects or when OpenAI's rate
  limit/moderation is the actual blocker. See `CLAUDE.md`'s asset pipeline section for the full,
  honest account of what was tried and what didn't work.

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
| Creativity | Text → verified worlds, plus model-authored game code (`--sandbox`), plus a mutation/curriculum/asset pipeline for infinite validated variants — and a Gymnasium-style **pixel-observation env** so a vision policy can actually play them. |
| Clarity | One schema (`SceneSpec`) as the shared contract; `navigate` runnable in one line; truthful `metrics.json` per run; this README leads with the loop. |
| Working output | `pytest` (390+ tests) covers the env, vision loop, validator, solver, assets, mutation, dataset export, sandbox; `metrics.json` never overclaims (`vision_success` comes from `is_goal_complete`, never from pixels). |

And the brief's three "why this matters" unlocks, made runnable:

| Brief's unlock | Where it lives |
|---|---|
| Post-training environments for a vision policy | `engine/env.py::InfiniEnv` — a Gymnasium `reset()/step()` env; observation = frame, action = controller input. Any frame-in/action-out policy drops in. |
| Code-level (not VLM-on-pixels) objectives | Reward = `navigation/planner.py::is_goal_complete`, a pure function of state; `navigate`'s `vision_success` is decided by it. |
| Reward from code, applied to pixels | `navigate` records a naive `vlm_judge_success` (a VLM reading the final frame) beside the code truth; disagreements show why the *code* signal is the reliable one to train a reward model on. |

## Project layout

```text
src/infinienv/
├── cli.py                  # generate / validate / solve / play / benchmark / mutate / curriculum / export-dataset
├── schema/                 # SceneSpec (pydantic) + JSON schema, incl. Mechanics/InteractGoal
├── llm/                    # provider protocol + mock / openai_agents / openai_responses / anthropic
├── generation/             # compiler (generate->validate->repair->fallback), templates, mutation,
│                           # curriculum, mechanics_cache (persist/reuse custom mechanics)
├── engine/                 # grid, game state, action legality, interactions.py, env.py (pixel-obs env)
├── validation/             # structured errors, reachability (BFS), solvability (full solve), validator
├── navigation/              # A*, symbolic planner, solve_scene, vision_policy.py (pixel->action stand-in)
├── render/                  # render.png (Pillow) + replay.gif, with optional sprite pasting
├── assets/                  # placeholder sprite generator, resolver, OpenAI Images generator, manifest
├── evaluation/               # per-run metrics, runner, benchmark, vision_runner.py (navigate command)
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
