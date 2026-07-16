# How InfiniEnv works

[← back to README](../README.md)

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
the goal* is judged by `is_goal_complete` over game state — **not** by looking at the pixels.

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

## Sandbox agents: model-authored engine code, per-run isolated

`generate` is **sandbox-only**: the model gets a real, isolated per-run copy of `schema/`/`engine/`/
`navigation/`/`validation/`/`render/`/`assets/` and may read, edit, or run anything in it —
including rewriting the engine itself — to build a mechanic a fixed vocabulary genuinely can't
express (an adversarial NPC that chases the agent, a custom win/lose condition). This gives up the
validator-guaranteed solvability check every other run has, and says so plainly: sandbox runs are
labeled `"source": "sandbox"` in `metrics.json`, carrying the agent's own self-reported success, an
independent **outer sanity check** (re-parses `scene.json` against the real schema; confirms
`render.png`/`replay.gif` are genuine, non-trivial, *animated* images; runs the deterministic
validator's vocabulary-agnostic geometry checks), and an independent **faithfulness audit** (a
separate LLM reads the agent's code as text — never executes it — and flags a faked mechanic) side
by side. If a check fails, the same agent gets the concrete failure fed back and repairs its own
work in the same persistent workspace, up to `--max-repair-attempts` times. The outer process never
imports or executes the sandboxed code; it only ever reads back the five standard artifact files.

The sandbox agent runs on the **Claude Agent SDK by default** (`INFINIENV_SANDBOX_BACKEND=claude`,
model `claude-haiku-4-5`), or the OpenAI Agents SDK (`=openai`). Both are interchangeable — same
workspace, artifacts, checks, and `metrics.json` shape.

See [CLAUDE.md](../CLAUDE.md) section 11 for the full design, the isolation boundary, and exactly
what the outer sanity check and the auditor do and don't guarantee.

## Evaluation-criteria mapping

| Challenge criterion | How this repo addresses it |
|---|---|
| Creativity | Text → verified worlds, plus model-authored game code (`--sandbox`), plus a mutation/curriculum/asset pipeline for infinite validated variants — and a Gymnasium-style **pixel-observation env** so a vision policy can actually play them. |
| Clarity | One schema (`SceneSpec`) as the shared contract; the GUI runs the whole loop in the browser; truthful `metrics.json` per run. |
| Working output | `pytest` (450+ tests) covers the env, vision loop, validator, solver, assets, mutation, dataset export, sandbox; `metrics.json` never overclaims (`vision_success` comes from `is_goal_complete`, never from pixels). |

And the brief's three "why this matters" unlocks, made runnable:

| Brief's unlock | Where it lives |
|---|---|
| Post-training environments for a vision policy | `engine/env.py::InfiniEnv` — a Gymnasium `reset()/step()` env; observation = frame, action = controller input. Any frame-in/action-out policy drops in. |
| Code-level (not VLM-on-pixels) objectives | Reward = `navigation/planner.py::is_goal_complete`, a pure function of state; `navigate`'s `vision_success` is decided by it. |
| Reward from code, applied to pixels | `navigate` records a naive `vlm_judge_success` (a VLM reading the final frame) beside the code truth; disagreements show why the *code* signal is the reliable one to train a reward model on. |

## Project layout

```text
src/infinienv/
├── cli.py                  # setup / generate / validate / solve / play / navigate / benchmark / mutate / curriculum / export-dataset / gui
├── setup_env.py            # guided .env writer + readiness checklist (the `setup` command)
├── schema/                 # SceneSpec (pydantic) + JSON schema, incl. Mechanics/InteractGoal
├── llm/                    # provider protocol + mock / openai_agents / openai_responses / anthropic + prompts
├── generation/             # compiler (generate->validate->repair->fallback), templates, mutation, curriculum, mechanics_cache
├── engine/                 # grid, game state, action legality, interactions, physics, env.py (pixel-obs env) + reusable game-dev primitives
├── validation/             # structured errors, reachability (BFS), solvability (full solve), validator
├── navigation/             # A*, symbolic planner, solve_scene, vision_policy.py (pixel->action stand-in)
├── render/                 # render.png (Pillow) + replay.gif, with optional sprite pasting
├── assets/                 # placeholder sprites, resolver, OpenAI Images + local diffusion generators, manifest
├── evaluation/             # per-run metrics, runner, benchmark, vision_runner.py (navigate command)
├── export/                 # dataset.py: runs directory -> JSONL with programmatic_reward
├── sandbox/                # --sandbox mode: isolated workspace, OpenAI/Claude runners, outer sanity check, auditor, faithful vision-play
├── gui/                    # Flask app + single-page frontend (SSE-streamed live activity)
└── artifacts/              # scene/validation/metrics JSON writers, report.md builder
tests/                      # pytest across every module above
examples/                   # example prompts + example scene.json files + a committed example run
```

## Limitations and roadmap

`PATHWAY.md` sketches a considerably larger version of this project. The following were deliberately
*not* done, to avoid rewriting a working system for redundant gain (see `notes.md` for the full
reasoning):

- **No package rename/restructure**, **no `SceneSpec` v0.2 migration**, **no typer/rich CLI
  rewrite**, **no pygame-ce renderer** — the current layout, schema, argparse CLI, and Pillow
  renderer already separate the same concerns and are real-API-verified.
- **2D-first by design.** See CLAUDE.md section 18 for the planned 3D field additions and exporter
  targets (Godot, Isaac Lab, MuJoCo, …).
- **The vision policy is a stand-in.** General Intuition's own policy isn't available to us, so
  `navigate` proves the *interface and the code-reward loop*, not a competing policy; a weak VLM
  won't reliably solve a deep maze from single frames.

See [notes.md](../notes.md) for the running decision log from the build.
