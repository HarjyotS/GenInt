# How InfiniEnv works

[← back to README](../README.md)

**Infinite Environment Generation via an Agent Harness** — a 2D-first harness that turns
natural-language commands into playable, *verified* environments, then lets a **vision-based
policy** (one that sees only rendered frames) play them while a **code-defined** reward decides
whether it succeeded.

> An agent builds. The harness verifies. A player other than the author must win.

The core bet, straight from the challenge brief: **code-defined objectives beat a VLM checking
pixels.** An agent builds each world in real game code — that's what makes the environments rich —
but whether a run *succeeded* is never the building model's say-so and never a VLM eyeballing
pixels. Every generated world must clear a **layered verification stack**: deterministic geometry
validation, artifact/motion sanity floors, an independent requirements audit, and finally the
**played-through proof** — a separate pixel-policy actually plays the game and must *beat* it,
win judged by the game's own code, before the run may claim success.

## Pipeline

```text
Text Prompt
  -> prompt refined into a build spec + derived requirements checklist
  -> an AGENT writes and runs the game's real code       (an agent builds)
     in an isolated per-run workspace copy of the engine
  -> layer 1: deterministic geometry validation           (the harness verifies)
  -> layer 2: artifact + motion sanity floors
  -> layer 3: independent faithfulness audit (a separate LLM, code read as text)
  -> layer 4: PLAYED-THROUGH PROOF -- an external vision  (a player other than
     policy plays the real game from rendered frames       the author must win)
     and must WIN, judged by the game's own code
  -> any failed layer -> concrete feedback -> the same agent repairs
  -> render.png + replay.gif + episode.gif (the winning playthrough) + metrics.json
```

The most important design rule: **verification wins**. The building model never grades its own
work: geometry is checked by the real deterministic validator, faithfulness by an auditor with no
shared context, and playability by an external policy that only sees frames — with every verdict
recorded in `metrics.json`. The fixed-vocabulary tools (`validate`/`solve`/`mutate`/`curriculum`/
`benchmark`/`export-dataset`) go further still: pure deterministic code with an analytic
solvability guarantee, covered by `tests/`.

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

## The sandbox baseline: an agent authors the engine code, the harness verifies it four ways

`generate` runs the **sandbox agent** — the baseline and only generation mode, because it makes
the best environments: the model gets a real, isolated per-run copy of `schema/`/`engine/`/
`navigation/`/`validation/`/`render/`/`assets/` and may read, edit, or run anything in it —
including rewriting the engine itself — to build a mechanic a fixed vocabulary genuinely can't
express (an adversarial NPC that chases the agent, gravity and jump arcs, a custom win/lose
condition). The verification requirements apply to it in full. A run may only claim `success`
after clearing four independent layers, none of which the agent can skip or weaken:

1. **Deterministic geometry validation** — the real validator runs on the generated `scene.json`,
   enforcing the vocabulary-agnostic geometry codes and recording the full verdict.
2. **Artifact + motion sanity floors** — `render.png`/`replay.gif` must be genuine, decodable,
   *animated* images, and the replay trace must contain no teleports.
3. **Independent faithfulness audit** — a separate LLM (no shared context with the author) reads
   the agent's code *as text* — never executes it — against the derived requirements checklist and
   flags any mechanic that's faked rather than implemented.
4. **The played-through proof** — an **external vision policy** plays the actual game through its
   drivable `make_env()` interface, seeing only rendered frames, and must genuinely **win** —
   judged by the game's own code (`info["won"]`). The winning `episode.gif` is kept as evidence.
   Fixed-vocabulary solvability can't transfer to agent-authored gameplay, so the sandbox replaces
   that analytic guarantee with this empirical one: every successful world was provably playable
   and beaten by a player other than its author.

If any layer fails, the same agent gets the concrete failure fed back (the validator error, the
audit finding, the losing episode's evidence) and repairs its own work in the same persistent
workspace, up to `--max-repair-attempts` times. The outer process never imports or executes the
sandboxed code; it only ever reads back the named artifact files. Runs are labeled
`"source": "sandbox"` with all four verdicts side by side in `metrics.json`.

The sandbox agent runs on the **Claude Agent SDK by default** (`INFINIENV_SANDBOX_BACKEND=claude`,
model `claude-sonnet-5`), or the OpenAI Agents SDK (`=openai`). Both are interchangeable — same
workspace, artifacts, verification stack, and `metrics.json` shape.

See [CLAUDE.md](../CLAUDE.md) section 11 for the full design, the isolation boundary, and exactly
what each verification layer does and doesn't guarantee.

## Results (live API, summaries committed)

**Real-LLM benchmark** — `benchmark examples/prompts.txt --provider openai_agents` (8 prompts,
seed 42, full validate→repair→solve pipeline):

| Metric | Result |
|---|---|
| Valid + solved on the first try (zero repairs) | **7 / 8** |
| Solved overall | **8 / 8** (the one maze failure exhausted 3 repairs and fell back to the deterministic template — recorded as `used_fallback: true`, never hidden) |
| Avg generation time | 10.0 s |
| Avg solution path | 20.4 actions |

(The mock-provider control run — the deterministic template pipeline — is 8/8 first-try by
construction; the LLM numbers above are the real result.)

**Vision-policy episodes** — `navigate`, 10 live episodes across 5 example worlds, success judged
by `is_goal_complete` over game state (never pixels):

| World | Episodes | Code-judged success | Naive VLM judge agreed |
|---|---|---|---|
| kitchen deliver | 2 | 2/2 | 2/2 |
| obstacle course | 2 | 2/2 | 2/2 |
| key + locked door | 2 | 2/2 | 2/2 |
| vision demo | 2 | 2/2 | 2/2 |
| push-physics puzzle | 2 | **0/2** | 2/2 |

8/10 solved by a pixel-only stand-in policy. The push-physics failures are honest: the stand-in
VLM never discovers shove-by-walking — the exact capability gap a trained vision policy closes,
and the code signal reports it truthfully either way.

**Dataset export** — `export-dataset` over 28 executed runs (two 5-level curricula, 10 validated
mutations each actually solved, and the 8 live-LLM benchmark worlds) → one JSONL row per run with
a per-goal `programmatic_reward` (e.g. `{"unlock_door": 1, "deliver_package": 1, "total": 2}`) —
the code-truth signal the brief wants reward models trained on.

## Evaluation-criteria mapping

| Challenge criterion | How this repo addresses it |
|---|---|
| Creativity | An agent authors each world's real game code (physics, NPCs, win conditions — no fixed vocabulary ceiling), with generated pixel-art sprites, a mutation/curriculum/asset pipeline for infinite validated variants, and a Gymnasium-style **pixel-observation env** so a vision policy can actually play every world. |
| Clarity | One README-to-GUI on-ramp; the GUI streams the whole loop live in the browser (the agent's decisions, code, audit, and playthrough); truthful `metrics.json` per run with every verification verdict recorded. |
| Working output | `pytest` (480+ tests) covers the env, vision loop, validator, solver, assets, mutation, dataset export, sandbox, audit, and playthrough gate; `metrics.json` never overclaims — a world only counts as generated once an external policy has actually beaten it. |

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
