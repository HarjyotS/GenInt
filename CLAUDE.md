# CLAUDE.md

This file gives Claude Code the project-specific context and operating rules for **InfiniEnv**.

InfiniEnv is a 2D agent harness, built for the General Intuition **Infinite Environment
Generation via an Agent Harness** technical challenge, that has grown past that original brief.
It compiles natural-language commands into structured scene specifications, validates and
repairs them deterministically, builds playable environments, solves them with a deterministic
agent, and emits reviewer-friendly artifacts — including, now, real generated sprites, LLM- and
model-defined game mechanics beyond a fixed vocabulary, mutation/curriculum/dataset-export
tooling, and a persistent cache so both assets and mechanics get reused instead of reinvented.

The core philosophy, unchanged since the very first line of code and never up for renegotiation:

> Use AI for semantic generation. Use deterministic code for truth.

Everything below assumes that. The system may keep growing well past what's described here; when
it does, extend this document rather than letting it drift out of sync with the code — a stale
CLAUDE.md is worse than none, because it actively misleads the next session.

---

## 1. Status and how to read this document

This is not a build spec for an MVP anymore — the MVP shipped, is committed, and is verified
against the real API. This document now describes the *current, standing system*: what exists,
what invariants it must keep, and how to extend it further. When a request would add capability,
default to building it. Don't weigh new work against "is this MVP scope" — that framing doesn't
apply anymore. The only questions that matter for new work are:

1. Does it keep section 2's invariants intact (validator wins, no model-authored code execution)?
2. Is the new capability itself deterministic and testable, even if what it *enables* the model
   to express is more open-ended?

If a request would require trading away #1 to get something the user wants, say so explicitly and
ask before building it. Section 5's declarative effect system is the example of resolving that
tension *without* the trade-off — delivering "let the model define real behavior" through a fixed,
validated vocabulary. Section 11's sandbox mode is the example of the user making an informed
call *to* take the trade-off after two earlier rounds of exactly this pushback: it's real
model-authored code execution, scoped to an isolated per-run workspace and opt-in via `--sandbox`,
with the loss of guarantee disclosed rather than hidden. Both are legitimate answers to "the model
needs to do something the fixed vocabulary can't express" — which one applies depends on whether
the user actually wants determinism preserved or has explicitly chosen to trade it away for a
given capability.

`notes.md` is the running decision log — read it when you need the *why* and historical context
behind something (a rejected alternative, a bug that was found and fixed, a live-verification
result). This file is the *what/how*, kept current; `notes.md` is chronological and never
rewritten. `README.md` is the reviewer-facing pitch. `PATHWAY.md` is a superseded roadmap
document — treat it as historical input that was partially adopted (see `notes.md` for exactly
which parts and why), not as a second source of truth.

---

## 2. Non-negotiable invariants

These hold at every stage of the project, past, present, and future, regardless of how much the
system grows:

- **The validator wins.** The LLM may propose a scene, repair a scene, mutate a scene, or define
  new mechanics for a scene. But schema validation, object placement, collision checks, bounds
  checks, reachability, pathfinding, inventory transitions, goal completion, and scoring are
  always deterministic and testable. If model output and deterministic validation conflict, the
  validator wins, full stop.
- **Sandbox is the only `generate` mode, and the deterministic engine is its substrate — including
  running the real validator on every sandbox scene.** As of the user's explicit decision, `generate`
  *only* runs section 11's sandbox agent (a plain `generate --prompt ...`; `--sandbox` is an accepted
  no-op, and there is no non-sandbox `generate` path). The deterministic engine is *not* deleted — it
  can't be: `build_workspace_dir` copies `schema/engine/navigation/validation/render/assets` into
  every workspace (the agent imports them), and the outer check runs the **real deterministic
  validator** (`validation/validator.py::validate_scene`) on the sandbox's `scene.json`, enforcing the
  vocabulary-agnostic geometry checks (`OUT_OF_BOUNDS`, `DUPLICATE_ID` — a real bug for any mechanics)
  and recording the rest in `metrics.json`'s `deterministic_validation`. So sandbox runs *do* have the
  validator's checks, not "traded away." The one thing that genuinely can't transfer is fixed-
  vocabulary **solvability** — the A*/symbolic planner can't play a game the agent wrote in its own
  code — so that's covered by the outer image check + the independent faithfulness audit + the agent's
  own trace invariants instead of a planner guarantee, every affected run labeled `"source":
  "sandbox"` so nothing is pretended. The deterministic path's guarantees still live on in the
  *tooling*: `validate`/`solve`/`mutate`/`curriculum`/`benchmark`/`export-dataset` still run the fixed-
  vocabulary validator + solver over any scene (e.g. `examples/*.json`), which is the code-defined-
  truth / programmatic-reward machinery — kept deliberately, since it's what best matches the brief's
  "code-level objectives beat a VLM" claim. See section 11 and `notes.md` for the full history (two
  earlier rounds where model-authored code was proposed and declined, then chosen, then made the
  default, then made the only mode).
- **Movement and physics stay deterministic code, not per-step LLM calls — outside sandbox mode.**
  The model plans task *semantics* (which goals exist, what a custom interaction's effects are);
  A* pathfinding and the primitive action executor (`engine/actions.py`) are always plain Python,
  never an LLM call in the loop, for every run except section 11's sandbox mode, where the agent
  may rewrite that logic itself inside its isolated workspace copy.
- **Extend by adding new deterministic primitives, not by loosening the two rules above.** A
  genuinely new capability (a new effect op, a new provider, a new pipeline stage) is real code
  with real tests, same as it always was — never a way to let the model bypass the validator or
  execute something unvetted.
- **File writes are confined to the selected output directory**, with path-traversal validation
  (`artifacts/writer.py::resolve_out_dir`).

---

## 3. Architecture (current)

```text
GenInt/                            # repo root
├── README.md                      # reviewer-facing pitch and usage
├── CLAUDE.md                      # this file
├── PATHWAY.md                     # superseded roadmap (see notes.md for what was adopted)
├── notes.md                       # chronological decision log — read for "why"
├── pyproject.toml
├── .env                           # OPENAI_API_KEY / OP_KEY / ANTHROPIC_API_KEY (gitignored)
├── .infinienv_asset_cache/        # generated sprite cache, keyed by object type (gitignored)
├── .infinienv_mechanics_cache.json  # custom object type/interaction cache (gitignored)
├── examples/
│   ├── prompts.txt                # benchmark-format prompt suite
│   ├── kitchen_can.json / warehouse_key.json / obstacle_course.json / throw_vase_demo.json
│   └── curriculum_warehouse.txt
├── runs/                          # generated run output (gitignored except .gitkeep)
├── src/infinienv/
│   ├── cli.py                     # generate/validate/solve/play/benchmark/mutate/curriculum/export-dataset/gui
│   ├── schema/
│   │   └── scene_schema.py        # SceneSpec, Mechanics, InteractGoal, etc. (pydantic)
│   ├── llm/
│   │   ├── base.py                # SceneProvider protocol, ProviderError
│   │   ├── __init__.py            # get_provider() registry (lazy imports per provider)
│   │   ├── providers/
│   │   │   ├── mock.py            # deterministic, no key needed
│   │   │   ├── openai_agents.py   # default runtime: ScenePlannerAgent/RepairAgent/MutationAgent
│   │   │   ├── openai_responses.py  # lower-level fallback, one Responses API call
│   │   │   └── anthropic.py       # optional Claude provider
│   │   └── prompts/
│   │       ├── scene_planner.md   # includes the mechanics worked example + rules
│   │       ├── repair_agent.md
│   │       ├── mutation_agent.md
│   │       ├── sandbox_agent.md   # the sandbox agent's system prompt (section 11)
│   │       ├── sandbox_auditor.md # the independent faithfulness auditor's prompt (section 11)
│   │       └── prompt_refiner.md  # enrich a raw prompt into a fuller spec pre-handoff (section 11)
│   ├── generation/
│   │   ├── compiler.py            # generate_and_validate: propose -> validate -> repair -> fallback
│   │   ├── templates.py           # mock provider's deterministic scene templates
│   │   ├── mutation.py            # 5 deterministic strategies + optional LLM-proposed mutations
│   │   ├── curriculum.py          # build/write/run_curriculum (--run executes every level)
│   │   └── mechanics_cache.py     # persists/reuses custom object types + interactions
│   ├── engine/
│   │   ├── grid.py                # static occupancy from a SceneSpec
│   │   ├── state.py                # GameState/ObjectState (mutable runtime state)
│   │   ├── actions.py               # apply_action: move/pick_up/drop/unlock/wait + routes to...
│   │   ├── interactions.py          # ...the custom-interaction effect interpreter
│   │   ├── physics.py               # deterministic grid-physics: push + slide (section 5b)
│   │   ├── action_registry.py       # ActionSpace: generic closed-action dispatch (section 11)
│   │   ├── motion_patterns.py       # generic patrol/pulse_cycle/pursue (section 11)
│   │   ├── animation.py             # generic phase_of/oscillate/cycle_variant (section 11)
│   │   ├── platformer_physics.py    # generic integrate_grounded_2d/climb_step (section 11)
│   │   ├── grid_collision.py        # generic segment_blocked/move_with_collision (section 11)
│   │   ├── level_generation.py      # generic generate_organic_region/region_is_connected (section 11)
│   │   ├── puzzle_state.py          # generic PuzzleState/Gate: named state + declarative gating (section 11)
│   │   ├── pushables.py             # generic Sokoban crate/block pushing (section 11)
│   │   ├── pathfinding.py           # generic BFS find_path/next_step_toward over a walls set (section 11)
│   │   ├── vision.py                # generic line-of-sight/range/cone perception (section 11)
│   │   ├── perception.py            # generic KnowledgeMap: closed perception / fog-of-war (section 11)
│   │   ├── agent_behavior.py        # generic BehaviorMachine: reactive-NPC state graph (section 11)
│   │   └── rendering.py             # generic feet_anchor/SpriteBook/paste_column render helpers (section 11)
│   ├── validation/
│   │   ├── errors.py                # ValidationIssue/ValidationResult
│   │   ├── reachability.py          # BFS reachability pre-check
│   │   ├── solvability.py           # full solve_scene() run as the real solvability check
│   │   └── validator.py             # validate_scene / validate_scene_dict — the single source of truth
│   ├── navigation/
│   │   ├── astar.py                 # A* pathfinding
│   │   ├── planner.py               # plan_goal / is_goal_complete (reach/pickup/deliver/unlock/interact/sequence)
│   │   └── policy.py                # solve_scene(): top-level solver, SolveResult incl. goal_results
│   ├── render/
│   │   ├── image_export.py          # render.png, with sprite pasting + flat-color fallback
│   │   └── replay_export.py         # replay.gif, re-simulates from the action list
│   ├── assets/
│   │   ├── placeholder_gen.py       # generates the checked-in base/*.png (run once, committed)
│   │   ├── base/*.png               # local placeholder sprites, no key/network needed
│   │   ├── generator_openai.py      # real sprite generation via the OpenAI Images API
│   │   ├── generator_diffusion.py   # local on-device sprite generation (opt-in, no rate limit)
│   │   ├── resolver.py              # resolve_assets(): none/local/generated/auto modes,
│   │   │                            # INFINIENV_SPRITE_BACKEND picks openai vs. diffusion
│   │   └── manifest.py              # AssetEntry, asset_plan.json / asset_manifest.json builders
│   ├── evaluation/
│   │   ├── runner.py                # run_generation(): the full generate->...->artifacts pipeline
│   │   ├── metrics.py               # compute_metrics()
│   │   └── benchmark.py             # run_benchmark() over a prompt file
│   ├── export/
│   │   └── dataset.py                # export_dataset(): runs dir -> JSONL with programmatic_reward
│   ├── artifacts/
│   │   ├── writer.py                  # resolve_out_dir (path-traversal-safe), JSON/report writers
│   │   └── report.py                  # report.md builder
│   └── gui/
│       ├── app.py                      # Flask app: SSE-streamed generate jobs, artifact serving,
│       │                               # runs listing -- a frontend on run_generation, not a
│       │                               # second implementation. Optional dep (`pip install
│       │                               # infinienv[gui]`), lazily imported.
│       └── templates/index.html          # single page, vanilla JS, no build step
└── tests/                              # one file per module above, plus test_cli.py, test_compiler.py
```

Keep files small and responsibilities separated. When adding a module, put it in the package that
owns that responsibility above — don't create a new top-level package without a reason.

---

## 4. Scene representation: SceneSpec

The scene spec is the contract between AI and the deterministic engine. It is typed (pydantic),
explicit, and the single thing every provider must produce and every validator check runs
against. Top-level fields: `version`, `seed`, `metadata`, `grid`, `agent`, `objects`, `walls`,
`goals`, `mechanics`.

```json
{
  "version": "0.1",
  "seed": 42,
  "metadata": {"name": "kitchen_can_delivery", "prompt": "...", "theme": "kitchen"},
  "grid": {"width": 16, "height": 12, "tile_size": 32},
  "agent": {"id": "agent", "x": 1, "y": 1, "inventory": []},
  "objects": [
    {"id": "table_1", "type": "table", "x": 6, "y": 4, "solid": true, "portable": false},
    {"id": "can_1", "type": "can", "x": 6, "y": 3, "solid": false, "portable": true},
    {"id": "sink_1", "type": "sink", "x": 13, "y": 9, "solid": false, "portable": false}
  ],
  "walls": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
  "goals": [{"id": "deliver_can_to_sink", "type": "deliver", "object_id": "can_1", "target_id": "sink_1"}],
  "mechanics": {"custom_object_types": [], "custom_interactions": []}
}
```

Rules:

- Every object/agent/goal/interaction needs a stable, unique `id`.
- Coordinates are grid-based integers, `0 <= x < width`, `0 <= y < height`.
- Walls and solid objects block movement; `walls` entries are single `{"x","y"}` cells, not line
  segments.
- Portable objects can be picked up when adjacent to or on the agent's cell.
- Goals must be checkable from state, not from pixels.
- `SceneObject.type` is a free string at the schema (parse) layer — it's the validator, not
  pydantic, that decides whether a given type is allowed (built-in, or declared in
  `mechanics.custom_object_types`). This is deliberate: it's what lets custom types exist at all
  while still being fully rejected if undeclared.

### Base (built-in) vocabulary

```text
Object types:  wall, floor, table, can, box, key, door, package, sink, exit, hazard, distractor
Object flags:  solid, portable, locked, key_id, pushable, slippery   (all default false/null)
Actions:       move_up, move_down, move_left, move_right, pick_up(object_id), drop(object_id),
               unlock(door_id, key_id), wait
Goal types:    reach(target_id), pickup(object_id), deliver(object_id, target_id),
               unlock(door_id), interact(interaction_id, target_id), push(object_id, target_id),
               sequence([...subgoals])
```

This vocabulary is closed by design — closed enough that the solver can *guarantee* solvability
rather than hope for it. It is not, however, the ceiling on what a scene can express; that's what
sections 5 and 5b are for.

### Locked doors

A door needs `"solid": true, "locked": true, "key_id": "<a portable key object's id>"`. Goals for
a key/door task are two ordered top-level entries in `scene.goals` (not a `sequence` wrapper):
`unlock` for the door, then whatever needs what's behind it. The planner auto-fetches the key
(paths to it, picks it up) the first time `unlock` needs it.

### Deterministic grid-physics (pushable / slippery + the `push` goal)

Physics is a first-class part of the base vocabulary, not a bolt-on: `pushable`/`slippery` object
flags and a `push` goal, interpreted by `engine/physics.py`. See section 5b for the full design;
the one-line summary is that it stays integer-grid and fully simulable, so the solver plans and
the validator verifies pushes exactly like any other goal — the solvability guarantee holds.

---

## 5. Extended mechanics: model-defined object types and interactions

The base vocabulary above doesn't cover everything a task might need — "a window you can throw
things out of," "a switch that unlocks a door," "a wall safe you crack with a stethoscope." A
scene can declare its own **mechanics** so the model can express these without every idea needing
a new hand-written Python feature to land in this repo first, and without ever letting it author
code (see section 2's invariants).

- **`mechanics.custom_object_types`**: `[{"id": "window", "description": "..."}]`. A type must be
  declared here before any object uses it, or validation rejects it (`UNSUPPORTED_OBJECT_TYPE`) —
  same as an unsupported type always has been. A custom type id colliding with a built-in one is
  also rejected (`MECHANICS_TYPE_COLLISION`).
- **`mechanics.custom_interactions`**: a new verb (`trigger_action`, must not collide with a
  built-in action — `MECHANICS_ACTION_COLLISION`), a `target_type` it applies to, an optional
  `must_hold_type` precondition, and an ordered, non-empty list of **effects**.
- Each effect is `{"op": ..., "target": ..., ...}` where `op` is one of a **fixed, small
  vocabulary** implemented in `engine/interactions.py`:

  | op | effect |
  |---|---|
  | `remove_held_object` | the held object (matching `must_hold_type`) is removed from the world entirely |
  | `drop_held_object_at_target` | the held object ends up at the target's position |
  | `remove_object` | removes the referenced object (`target`: `"target"`/`"held"`/an explicit id) from the world |
  | `unlock_target` | unlocks the target object (same effect as a normal `unlock`, generalized) |
  | `set_object_property` | sets `property_name`/`property_value` on the referenced object's `properties` bag |
  | `teleport_agent` | moves the agent to `x`/`y` |

  There is no "run this code" op. The model composes behavior out of these primitives; it never
  writes the primitives themselves. A new op is a real code change with tests, same as adding a
  new built-in action always was.
- A goal `{"type": "interact", "interaction_id": ..., "target_id": ...}` is satisfied once that
  interaction has actually been performed against that target — planned the same deterministic
  way as `unlock`/`deliver` (path to target, satisfy `must_hold_type` by picking up a matching
  portable object first if needed, apply the interaction, done), tracked in
  `GameState.completed_interactions`.

Validated, planned, executed, and replayed exactly like everything else: `validate_scene` checks
the mechanics block is internally consistent (no built-in collisions, every reference resolves,
every interaction has effects) before ever touching reachability/solvability;
`navigation/planner.py` plans `interact` goals the same way it plans `unlock`;
`render/replay_export.py` shows the effect (an object vanishing, a door unlocking) in the
animated replay because it re-simulates from the real action list, same as everything else.

### Mechanics get cached, not reinvented per scene

`generation/mechanics_cache.py` persists every new custom object type/interaction from a
*validated* scene into `.infinienv_mechanics_cache.json` (gitignored, project-local runtime
cache — same treatment as the asset cache below). The `get_known_mechanics` tool exposes that
cache back to `ScenePlannerAgent`/`RepairAgent`/`MutationAgent`; the prompt instructs the model to
check it first and reuse an existing definition verbatim rather than invent a new one. First
definition wins on a cache write (existing entries are never overwritten) — once "window" means
something, it keeps meaning that.

### What this deliberately does not do

- No `eval`/`exec`, no model-authored Python, no dynamically imported code of any kind.
- No unbounded property system — `SceneObject.properties` and `set_object_property` are for
  simple flags an interaction's effects can read/write, not a general scripting surface.
- The validator still decides whether a scene is accepted. A model can propose `mechanics` that
  don't validate exactly as it can propose an invalid `SceneSpec` today — same repair loop, same
  fallback.

Worked example (`examples/throw_vase_demo.json` is a hand-authored, always-valid instance of
this):

```json
{
  "objects": [
    {"id": "vase_1", "type": "vase", "x": 4, "y": 4, "portable": true},
    {"id": "window_1", "type": "window", "x": 9, "y": 4, "solid": false}
  ],
  "mechanics": {
    "custom_object_types": [{"id": "vase"}, {"id": "window"}],
    "custom_interactions": [{
      "id": "throw_through_window", "trigger_action": "throw", "target_type": "window",
      "must_hold_type": "vase", "effects": [{"op": "remove_held_object", "target": "held"}]
    }]
  },
  "goals": [{"id": "declutter", "type": "interact", "interaction_id": "throw_through_window", "target_id": "window_1"}]
}
```

Live-verified (see `notes.md`) with prompts that had no exact hand-authored precedent in this
repo — including a genuinely different mechanic (a "flip a switch to unlock a vault door"
interaction using `set_object_property` + `unlock_target`) — confirming the model generalizes
this pattern rather than echoing one canned example.

---

## 5b. Deterministic grid-physics: pushable objects and sliding

Section 5's declarative effects let the model define *what an interaction does*; this section is
about *movement dynamics* the base action set couldn't express — shoving a crate, a puck sliding
across ice — delivered as a first-class, **deterministic** engine primitive rather than punted to
`--sandbox`. It exists because "produce environments in a game or physics engine" wants physics to
be normal, not exotic, and the user asked for physics in the default path. The whole design is
built around one constraint: **it must not cost the validator-wins solvability guarantee.**
Continuous, force-based physics (pymunk-style smooth motion) fundamentally can't — an A* solver
can't verify it, and it needs float coordinates the whole engine doesn't have — so that stays
confined to section 11's sandbox mode. What lives here instead is *discrete grid-physics*: still
integer cells, still fully simulable, so the deterministic solver plans it and the validator
verifies it exactly like any other goal.

### The vocabulary (two object flags + one goal type)

- **`SceneObject.pushable`** (`bool`, default false): the agent shoves the object one cell by
  moving into it (Sokoban-style) instead of being blocked by it. Pushable objects should also be
  `solid` (that's what makes shoving them meaningful).
- **`SceneObject.slippery`** (`bool`, default false): a *pushable* object that, once shoved, keeps
  sliding in the push direction until the next cell is blocked (ice-puck momentum). Still integer
  cells — just several per push. A slippery object can therefore only come to rest against an
  obstacle, which the solver enforces: a mid-floor target for a slippery object is genuinely
  `UNSOLVABLE`, and that's reported, not hidden.
- **`push` goal** (`{"type": "push", "object_id": ..., "target_id": ...}`): satisfied once the
  pushable `object_id` rests on `target_id`'s cell. Distinct from `deliver` — the agent shoves the
  object across the floor rather than picking it up and carrying it, so `push` works for
  heavy/non-portable objects.

### Where it lives (parallel to the interaction system)

- `engine/physics.py` — the deterministic interpreter: `pushable_at` (live lookup),
  `try_push` (shove one cell, or slide until blocked if slippery), and `cell_blocked` /
  `solid_blocker_at` (**live** collision — computed from current object positions, not the static
  `Grid`, since the Grid records only the initial solid layout and would be stale once a pushable
  moves). For a scene with no pushables these yield the same blocking decisions the old static
  check did, so existing scenes are unaffected.
- `engine/actions.py::apply_action` — the `move_*` branch now checks `pushable_at` first: moving
  into a pushable object shoves it (raising `ActionError` if it can't move) instead of blocking.
- `navigation/planner.py::_plan_push` — plans a push via **BFS over the joint (agent, box)
  state**, simulating the exact same push/slide rule the engine applies, so the emitted moves are
  guaranteed to reproduce the pushes on execution. Single-box: every *other* solid object is a
  static obstacle (multi-box coordination is out of scope and not guaranteed). Bounded by
  `_PUSH_SEARCH_NODE_CAP`; exceeding it is reported as unsolvable, never a hang. `plan_goal` /
  `is_goal_complete` get a `"push"` branch.
- `validation/validator.py` — `_iter_goal_refs` includes a push goal's `object_id`/`target_id`;
  a new `PHYSICS_NOT_PUSHABLE` check rejects a push goal whose object isn't `pushable`; and the
  reachability pre-check treats pushable objects as *optimistically passable* (like unlocked
  doors — a crate walling a corridor can be shoved aside, so it isn't a permanent `UNREACHABLE`
  block). Real solvability is still the authoritative gate via the extended solver.
- `render/replay_export.py` — `build_replay_frames` detects an object that moved more than one
  cell in a single action (a slide) and inserts per-cell intermediate frames, so a slippery slide
  reads as smooth gliding motion instead of a teleport. This is what makes physics runs *look
  good*, especially with `--assets`.
- `generation/templates.py` — a `push_slide_puzzle` mock template (agent shoves a slippery puck
  into a wall-adjacent plate), always solvable by construction, so `--provider mock` (the offline
  path) exercises physics too. Routed by `push`/`slide`/`ice`/`crate`/… keywords.

### What it deliberately does not do

- No continuous/float motion, no forces, no `pymunk` — those can't preserve the solvability
  guarantee and stay in section 11's sandbox mode. This is integer-cell physics only.
- No multi-box coordinated push planning (single-box is the guaranteed case).
- The `Grid` stays static; only the *live* collision in `engine/physics.py` reflects moved
  objects. A* navigation for *non-push* goals still assumes pushables at their initial cells, so a
  scene shouldn't require the agent to walk through where it earlier pushed a box away (the
  push-goal path itself is fine — it's planned via the live-simulating joint BFS, not A*).

Live-verified first-try with the real `openai_agents` provider on a prompt with no hand-authored
precedent ("push a heavy crate onto a floor switch, then reach the exit"): the model produced a
valid, solvable `push` + `reach` scene, `pushable: true` crate and all — confirming the model
picks up the new vocabulary from the prompt and generalizes it. See `notes.md`.

---

## 6. Validation

`validation/validator.py::validate_scene` is the single source of truth; every provider's output
goes through it before anything is built, rendered, or solved. Returns structured errors, not
vague strings:

```json
{
  "valid": false,
  "errors": [
    {"code": "UNREACHABLE_OBJECT", "message": "Object can_1 cannot be reached from the agent spawn.", "object_id": "can_1", "severity": "error"}
  ]
}
```

Checks, roughly in order (later checks short-circuit if earlier ones fail, since geometry that's
broken enough makes reachability/solvability meaningless to even attempt):

1. Schema parses (`SCHEMA_ERROR` from pydantic if not).
2. `DUPLICATE_ID` across agent + all objects + all interaction ids.
3. Mechanics internal consistency: `MECHANICS_TYPE_COLLISION`, `MECHANICS_ACTION_COLLISION`,
   `MECHANICS_UNKNOWN_TYPE` (an interaction's `target_type`/`must_hold_type` isn't known/declared),
   `MECHANICS_NO_EFFECTS`, `UNSUPPORTED_OBJECT_TYPE`, `MECHANICS_UNKNOWN_INTERACTION` (a goal
   references an undeclared interaction), `PHYSICS_NOT_PUSHABLE` (a `push` goal targets an object
   that isn't `pushable` — see section 5b).
4. `OUT_OF_BOUNDS` for the agent, every object, every wall.
5. `ILLEGAL_OVERLAP` — two solid occupants (walls, solid objects, the agent) on one cell.
6. `MISSING_GOAL_OBJECT` — every goal's referenced object/target/door/interaction-target/push id
   must exist.
7. `NO_GOALS` if the scene has none.
8. `UNREACHABLE_OBJECT` — a cheap BFS pre-check from spawn, with doors *and pushable objects*
   treated as *optimistically passable* (this is "is it walled off entirely by permanent walls,"
   not a real lock/key or push-order simulation — that's next).
9. `UNSOLVABLE` — the real gate: `validation/solvability.py` actually runs `solve_scene()` (the
   full deterministic planner) and requires every goal to be genuinely completable in order,
   respecting real lock state as it evolves through the scene.

Validation is deterministic for a given scene.

---

## 7. Generation pipeline: providers, repair, fallback

`generation/compiler.py::generate_and_validate` owns the loop:

```text
1. provider.generate_scene(prompt, seed)
2. validate_scene(scene)
3. If valid: done. If a schema-parse/API failure occurred: treated as a validation
   failure (GENERATION_FAILED), not a crash -- feeds into the same repair loop.
4. If invalid and attempts < MAX_REPAIR_ATTEMPTS (default 3, env override):
   provider.repair_scene(prompt, scene, errors, seed) -> validate again -> repeat.
5. If still invalid after repair budget:
   - allow_fallback=True (default): fall back to the deterministic template generator
     (always valid by construction). used_fallback=True is recorded.
   - allow_fallback=False (--no-fallback): raise GenerationFailedError instead, showing
     every attempt's real error (not just the last, often-generic one) -- see notes.md
     for why that message construction matters.
6. On any valid result: remember_scene_mechanics(scene) persists new custom mechanics
   to the shared cache.
```

Every attempt (valid or not) is recorded in `validation.json`'s `repair_history` and surfaced in
`report.md`. Never silently discard a failure.

### Providers (`llm/providers/`, common `SceneProvider` protocol in `llm/base.py`)

| Provider | Key needed | Notes |
|---|---|---|
| `mock` | No | Deterministic templates (`generation/templates.py`): kitchen delivery, warehouse key/door, obstacle course — picked by prompt keywords, parameterized by `--seed`. Always valid and solvable by construction. This is the CI/offline path, not the primary demo path. |
| `openai_agents` | `OPENAI_API_KEY` | **Default runtime.** `ScenePlannerAgent`/`RepairAgent` (required) and `MutationAgent` (optional, via a duck-typed `propose_mutation` method) built with the OpenAI Agents SDK. Structured output via `AgentOutputSchema(SceneSpec, strict_json_schema=False)` — non-strict because `SceneObject.properties`/`InteractionEffect.property_value` are open-ended dict/union shapes OpenAI's strict/grammar-constrained mode rejects outright. Tools: `get_scene_schema`, `get_supported_mechanics`, `validate_scene_tool` (`strict_mode=False`, same reason), `get_known_mechanics`. |
| `openai_responses` | `OPENAI_API_KEY` | Lower-level fallback: one Responses API call with a non-strict `text.format` json_schema, no agent orchestration. |
| `anthropic` | `ANTHROPIC_API_KEY` | Optional Claude provider. Same protocol, same JSON-parsing path as `openai_responses`. Implemented but not exercised against a live key as heavily as the OpenAI paths — see `notes.md`. |

The model never executes code or writes files directly: it emits `SceneSpec` JSON and may call
the read-only/validate-only tools above. All file writes, retries, rendering, and scoring are
owned by this repo's Python code.

### Key loading

`.env` (`OPENAI_API_KEY=...` or `OP_KEY=...`, `ANTHROPIC_API_KEY=...`) is loaded with
`load_dotenv(override=True)` specifically so a stale key already exported in the parent shell
doesn't silently win over a freshly-updated `.env` — this was a real bug (see `notes.md`).
`OP_KEY`, if set, is unconditionally copied over `OPENAI_API_KEY` — i.e. `OP_KEY` wins whenever
both are present, not just as a fallback when `OPENAI_API_KEY` is absent.

---

## 8. Engine and navigation

Deterministic, always — no LLM in this loop, ever (section 2).

- `engine/grid.py` — static occupancy (walls, solid objects) built once from a `SceneSpec`.
- `engine/state.py` — `GameState`/`ObjectState`: mutable runtime state (agent position,
  inventory, per-object `properties`/`pushable`/`slippery`, `unlocked_doors`,
  `completed_interactions`).
- `engine/actions.py::apply_action` — the primitive executor for
  move/pick_up/drop/unlock/wait, with legality checks (adjacency, portability, held-state). A
  `move_*` into a `pushable` object shoves it via `engine/physics.py` (section 5b). An
  unrecognized verb routes to `engine/interactions.py::apply_custom_interaction` when the scene
  defines a matching `custom_interactions` entry; otherwise it's a hard `ActionError`.
- `engine/physics.py` — deterministic grid-physics: `try_push` (push one cell / slide until
  blocked) and *live* collision helpers. See section 5b.
- `navigation/astar.py` — plain A* pathfinding over the grid.
- `navigation/planner.py::plan_goal` — the symbolic task planner: expands one goal
  (reach/pickup/deliver/unlock/interact/push/sequence) into a primitive action sequence, applying
  each action to `state` immediately as it's planned (via `_emit`) so later planning steps see
  up-to-date state. `push` is planned by a joint (agent, box) BFS (`_plan_push`, section 5b); all
  others by A*. If a `trace` list is passed in, `_emit` also records a step snapshot *at the
  moment the action is applied* — this must stay true; see `notes.md` for the bug that happened
  when a caller tried to reconstruct per-step trace data after the fact instead.
- `navigation/policy.py::solve_scene` — the top-level solver: runs every top-level goal in
  order, returns a `SolveResult` with `success`, `actions`, `trace`, and `goal_results` (a
  per-top-level-goal `{"id","type","success"}` list — the real signal behind dataset export's
  `programmatic_reward`, not a single flattened bool).

For a `deliver` goal: path to object, pick up, path to target, drop, verify. For a locked door:
path to key, pick up, path to door, unlock, path to what's behind it. For `interact`: path to a
`must_hold_type` match if not already held and pick it up, path to the interaction's target,
apply the interaction. For `push`: BFS over the joint (agent, box) state, simulating the exact
push/slide rule the engine applies, until the box rests on the target cell (section 5b).

---

## 9. Renderer and asset pipeline

### Renderer (`render/`)

Pillow-based, not pygame — pygame needs an SDL display context that's a real risk headless;
Pillow produces both deliverables reliably with no such dependency risk.

- `render/image_export.py::save_render_png` — static top-down map with a legend. Draws a sprite
  (via `asset_paths`) when one's resolved for a given `type`/`"wall"`/`"agent"`, and always falls
  back to a flat colored cell + first-letter label when it isn't — this fallback is what makes
  novel model-defined object types render sensibly with zero per-type code.
- `render/replay_export.py::save_replay_gif` — re-simulates the scene from the actual action list
  (not from the solver's internal state) frame by frame, so the GIF is always a faithful replay
  even if something about trace bookkeeping elsewhere were ever wrong.

### Asset pipeline (`assets/`)

`generate --assets {none,local,generated,auto}` (default `none`, i.e. the original flat-colored
rendering, unchanged unless opted into):

- `local` — checked-in placeholder sprites (`assets/base/*.png`, produced once by
  `assets/placeholder_gen.py`, simple Pillow-drawn icons). No key or network needed.
- `generated` — real sprites via the OpenAI Images API (`assets/generator_openai.py`). No silent
  fallback if generation fails.
- `auto` — **combine both, routed per type**: the simple structural types
  (`resolver.py::SIMPLE_LOCAL_TYPES` = wall/floor/box/door/exit/key/hazard/distractor) resolve to
  their checked-in local placeholder with **no image-API call** (note `"auto: simple type drawn
  locally"`), and only the types that actually benefit from generation — characters (`agent`) and
  novel/custom types (creatures, plants, props) — are OpenAI-generated, still falling back to a local
  placeholder if a generation fails (note `"fallback: generated unavailable"`). This is deliberate:
  wall/floor are placed in nearly every cell and were the exact types repeatedly hitting the image
  API's 5-images/min rate limit (429), so drawing them locally is both faster and far more reliable,
  while `agent`/custom sprites still get real generated art. `generated` mode is unchanged (generates
  everything, no fallback).

**Model:** `gpt-image-1`, not `gpt-image-2` — per OpenAI's own docs, `gpt-image-2` explicitly does
not support `background: "transparent"`; `gpt-image-1`/`1.5`/`1-mini` do. Overridable via
`INFINIENV_IMAGE_MODEL`, but transparency silently stops working on `gpt-image-2`. Two request
shapes, chosen by whether the type is a discrete object or a tile *texture*:

- **Discrete objects** (everything not in `TEXTURE_TILE_TYPES`): `background="transparent"`,
  a prompt asking for an isolated object filling most of the frame, then
  `_crop_to_content` (crop to the alpha bounding box + small padding, pad to square) before the
  final 64x64 resize — without this, the model's baked-in canvas margin makes sprites look small
  and sparse once tiled.
- **Texture tiles** (`TEXTURE_TILE_TYPES = {"wall", "floor"}`): these aren't objects sitting on a
  tile, they *are* the tile's surface — `background="opaque"`, a distinct prompt demanding a
  seamless, zero-margin, edge-to-edge texture, and **no** crop step (cropping a texture meant to
  already fill 100% of the frame is a no-op at best, clips a busy pattern at worst). Getting this
  distinction wrong was a real, user-reported bug — see `notes.md`.

Both prompt templates (and the `generator_diffusion.py` equivalents, for parity) explicitly demand a
**cohesive blocky retro-16-bit pixel-art style** — chunky visible square pixels, a small flat
palette, a bold dark outline, flat cel shading, and explicitly *no* smooth gradients / photorealism /
3D / soft shading — so every generated sprite reads as the same tile-game aesthetic and sits
seamlessly next to the others and the square tiles (a user ask: "make sure all images generated are
block style so that it fits seamlessly"). Live-verified against the real Images API: a brick tile,
coin, green-tunic hero, plant monster, and round enemy all came back as consistent chunky pixel-art
that tiles together cleanly (see `notes.md`). Note the pre-existing, unrelated moderation caveat is
unchanged — a *description* that reads as a copyrighted character (e.g. "Italian plumber") still
returns `400 moderation_blocked` regardless of style; that's a description problem, not a style one.

Sprites are cached **by object type**, not per-scene or per-run, in `.infinienv_asset_cache/` at
the repo root (gitignored) — generating "table" once means every future scene with a table reuses
it; `generated`/`auto` only ever calls out for types not already cached. `asset_manifest.json`
records exactly where each sprite came from (`local`/`generated`/`none`) so a run never silently
claims a generated asset that wasn't actually generated.

**Generation is concurrent, not sequential, and defaults to low quality.**
`assets/resolver.py::resolve_assets` used to call `generate_sprite` for every uncached type in a
plain `for` loop — for a scene with N novel object types, wall-clock time was N times one image's
latency, since each call blocked the next. `_generate_many` now dispatches every pending type's
generation to a small bounded thread pool (`DEFAULT_ASSET_CONCURRENCY = 4`, overridable via
`INFINIENV_ASSET_CONCURRENCY`) — these are independent, I/O-bound API calls, so running them
concurrently drops wall-clock time to roughly the single slowest call instead of the sum of all of
them (live-verified: 4 novel sprites in ~16s concurrently, vs. an expected ~4x that sequentially).
Bounded, not unbounded, to stay polite to API rate limits on scenes with many custom types. One
type's generation failure is isolated (caught per-future) and doesn't take down the others already
in flight — `resolve_assets`'s existing per-type fallback/note behavior is unchanged, just faster.
Separately, `generate_sprite` now passes `quality="low"` by default (overridable via
`INFINIENV_IMAGE_QUALITY`) — gpt-image-1's generation latency scales heavily with `quality`, and
every sprite gets resized down to 64x64 immediately after generation regardless, so paying for the
API default (`auto`, a slow high-effort render) bought nothing visible at that resolution.
Live-verified sprites at `quality="low"` are still clean and usable at 64x64.

**Sprite descriptions come from the scene itself, not a generic default.** `generate_sprite` used
to prompt from `OBJECT_DESCRIPTIONS.get(object_type, object_type.replace("_", " "))` — for the
`"agent"` asset key this was *always* `"a small friendly robot character"` regardless of what any
given scene actually needed, and custom types fell back to their bare type name, discarding the
description the model had already written in `mechanics.custom_object_types`. A real,
user-reported quality complaint ("the generated graphics for our italian friend are a little
poor") traced to exactly this. `resolver.py::_scene_descriptions(scene)` now derives a
`{type: description}` map from the scene itself — verbatim `custom_object_types[].description`
for declared types, and `scene.metadata.prompt` for the `"agent"` key specifically (not a declared
object type — the top-level `SceneSpec.agent` — but the original task prompt almost always
describes the intended protagonist far better than any static default). `generate_sprite` gained a
`description` override parameter that `resolve_assets`/`_generate_many` pass through automatically
— no new parameters for callers. Separately, `sandbox_agent.md` gained a concrete `paste_sprite`
code example for custom continuous-position draw loops (load once, cache, paste at a computed
pixel position, fall back to a primitive shape only when no sprite was resolved), since a hand-
rolled simulation loop resolving assets and then drawing primitives anyway defeats the point — the
gap between "resolve assets" and "actually use them" was real and previously undetected because no
sandbox run that session had ever passed `--assets` at all. Live-verified end to end with
`--sandbox --assets generated` on the same prompt: a real, recognizable capped hero sprite and
turtle sprites with an actual shell pattern, replacing the crude hand-drawn circles/rectangles
every prior run had used.

**A generation failure's reason used to be silently discarded.** `resolve_assets()` has always
returned `(entries, notes)`, `notes` carrying the real per-type failure reason -- but both the
reference sandbox `run_scene.py` template and every sandbox-agent-authored rewrite of it captured
`notes` and then threw it away, so a sprite that silently fell back to a hand-drawn primitive left
zero trace of why. A user-reported "the graphics look so poor" screenshot (two of eight sprites in
a Mario-style scene were crude primitives while the rest were real art) led to fixing this: the
reference template (`sandbox/workspace.py::_RUN_SCENE_TEMPLATE`) now records `asset_notes` in
`metrics.json` unconditionally (empty list when there's nothing to report), and `sandbox_agent.md`
tells the agent to do the same if it rewrites `run_scene.py`. This immediately paid off: re-running
the same prompt surfaced the *real* cause in `asset_notes` -- genuine `429 rate_limit_exceeded`
errors from `gpt-image-1` ("Rate limit reached... Limit 5, Used 5, Requested 1"). The account's
real limit is 5 images/minute; a scene with several novel object types resolved concurrently
(`DEFAULT_ASSET_CONCURRENCY = 4`) can exceed that routinely, and `--assets generated`'s "no silent
fallback" design means those sprites just... don't exist, with no diagnostic anywhere before this
fix. This is a real, load-bearing example of why `asset_notes` matters, not a hypothetical.

### Sprite generation backend: OpenAI (default) or local diffusion

The rate-limit finding above led directly to `assets/generator_diffusion.py`: a second
`generate_sprite(object_type, cache_dir, *, model=, quality=, description=)` implementation with
the *exact same contract* as `generator_openai.py`'s, so it's a drop-in alternate backend, not a
parallel code path callers need to know about. Selected via `INFINIENV_SPRITE_BACKEND` --
deliberately an env var, not a fifth `--assets` mode value, matching
how every other asset-generation knob in this project already works
(`INFINIENV_IMAGE_MODEL`/`INFINIENV_IMAGE_QUALITY`/`INFINIENV_ASSET_CONCURRENCY` are all env-only)
and keeping `--assets {none,local,generated,auto}`'s meaning stable regardless of which pipeline
actually produces a "generated" sprite. `resolver.py::_select_sprite_generator()` is the seam;
`resolve_assets()` records which backend actually ran in `AssetEntry.note` (`"backend: openai"` /
`"backend: diffusion"`) for provenance. Note the naming choice: "local" was already taken
(`--assets local` means the checked-in static placeholders in `assets/base/`), so the new backend
is called "diffusion" throughout (mode value, env var, extra name) to avoid colliding with that
existing, load-bearing meaning.

**Default was briefly flipped from `openai` to `diffusion`, then reverted -- both changes for real,
live-verified reasons, not speculation.** After a second real OpenAI failure mode surfaced on top
of the rate limit (a later run's hero sprite, `agent_run_1`/`agent_run_2`, was rejected outright by
OpenAI's moderation system, `400 moderation_blocked`, "Your request was rejected by the safety
system" -- almost certainly because a character description like "an Italian man in green
clothing" reads as a request to depict a copyrighted character) and the user's direction (*"make
it use the local image gen not openai anymore"*), the default became `diffusion`. Live-verifying
that change (see the follow-up entries below: a sandbox-cache bug, then a CLIP-truncation bug,
both found and fixed) eventually produced a genuinely working pipeline end-to-end -- but the
*character/hero sprite quality itself*, once actually looked at in a real rendered scene, was
poor: a small, fast, 2-step distilled model is only weakly prompt-adherent, and even with the
truncation fix, a narrative-heavy player-character description doesn't reliably produce one clean
isolated character. User's verdict on the real rendered output: *"this is shit go back to
openai."* `_select_sprite_generator()`'s default is back to
`os.environ.get("INFINIENV_SPRITE_BACKEND", "openai")`; `diffusion` remains fully available as an
explicit opt-in (`INFINIENV_SPRITE_BACKEND=diffusion`) -- it still worked well for textures and
simple objects (see the live-verification entries below), and is a real option when OpenAI's rate
limit or moderation is specifically the blocker for a given run. None of the infrastructure built
along the way was reverted -- the backend-selection seam, `generator_diffusion.py` itself, the
project-level model cache, and the prompt-ordering fix are all still real, tested, working code;
only which backend runs *by default* changed back.

- **Model**: `stabilityai/sd-turbo` by default (1-4 step inference, `guidance_scale=0.0`,
  deliberately a small/fast turbo model given these end up as 64x64 sprites regardless of source
  fidelity), overridable via `INFINIENV_DIFFUSION_MODEL`. **License disclosure**: SD-Turbo ships
  under the Stability AI Community License (free for research/personal/small-business use, a
  revenue threshold applies beyond that) -- not as permissive as this repo's other dependencies;
  the env var override exists specifically so a different, more permissively licensed model can
  replace it with zero code changes.
- **Device**: auto-detects `cuda` -> `mps` -> `cpu` (`float16` on `cuda` only; `float32`
  elsewhere, since `float16` on MPS has a history of being unreliable in `diffusers`). Pipeline is
  a lazily-loaded, lock-guarded module-level singleton -- loaded once per process, actual
  inference calls serialized through the same lock (local generation is compute-bound, unlike the
  network-bound OpenAI path, so there's no latency-hiding argument for true concurrency here, and
  diffusers pipeline objects aren't guaranteed safe for concurrent `__call__`).
- **Transparency: two designs tried, the second one live-verified to actually work.** Local
  diffusion pipelines have no request-time "transparent background" feature the way OpenAI's
  Images API does (no alpha channel at all).
  - *First attempt*: prompt discrete objects against a solid magenta chroma-key background, then
    threshold by color distance to alpha 0. Live-verified NOT reliable: a hard single-threshold
    cutoff left a visible magenta fringe around every sprite (anti-aliased edge pixels are a real
    RGB blend of object color and background, so no single cutoff handles them cleanly); a softer
    ramp between an inner/outer threshold reduced but didn't eliminate it; then, decisively, a
    real generated sprite for "a wooden table" came back as pink corrugated stripes with a
    red-framed square -- SD-Turbo at 2 inference steps simply doesn't reliably paint a clean solid
    background at all, so there was nothing correct to key against no matter how the threshold was
    tuned. Confirmed by dumping the raw pre-processed image directly, not by guessing.
  - *Second, shipped design*: `_remove_background()` runs `rembg` (a U2Net-based background-removal
    model, requires `rembg[cpu]` for the `onnxruntime` backend -- a bare `rembg` install raises at
    call time without it) on the raw generated image, which segments foreground from background
    regardless of what the generator actually painted -- it doesn't depend on prompt adherence at
    all. `DIFFUSION_SPRITE_PROMPT_TEMPLATE` no longer asks for any specific background color,
    just "a plain simple background clearly distinct from the object." Live-verified after the
    swap: both the "can" and "table" sprites came back with clean transparent backgrounds and no
    fringe, confirmed visually in an actual `render.png` (no tinted patches behind either sprite,
    unlike the chroma-key attempts), and a `wall` texture tile (which skips background removal
    entirely, same as the OpenAI backend's texture branch) produced a genuine seamless brick
    pattern. `_crop_to_content` (from `generator_openai.py`, reused unchanged) still runs after
    background removal to trim margin, same as the OpenAI path.
  - *Third finding, live-caught during the physics-fix verification below*: a long player-character
    description (`_scene_descriptions()` embeds up to 220 characters of the scene prompt for the
    `"agent"` key) silently exceeded SD-Turbo's CLIP text encoder's 77-token hard limit, truncating
    away the *trailing* "isolated object... plain background" instructions in the original
    desc-first template -- confirmed by dumping the raw pre-`rembg` image directly: SD-Turbo drew
    an entire multi-element scene (floating islands, water, several small figures) instead of one
    character, which `rembg` then had no single foreground object to cleanly segment, producing a
    nearly-blank sprite. Fixed by reordering both templates so the fixed style/framing instructions
    come *before* `{desc}`, not after -- truncation (which still happens for long descriptions) now
    only ever drops the tail of the description text, never the formatting instructions the rest of
    the pipeline depends on. Also installed `accelerate` (added to the `diffusion` extra) after
    noticing every pipeline load printed "Cannot initialize model with low cpu memory usage because
    `accelerate` was not found" -- confirmed gone after installing it. **Net honest result**:
    reordering fixed the "draws an entire scene" failure mode, but character-sprite quality for
    narrative-heavy descriptions is still visibly weaker than the OpenAI backend's -- SD-Turbo is a
    small, fast, weakly-prompt-adherent model, and a description built by embedding a full scene
    prompt verbatim (reasonable for the much larger OpenAI model) isn't necessarily the right shape
    of input for it. Not treated as fully solved; flagged here rather than overclaimed.
- **Optional dependency**: `pip install infinienv[diffusion]` (`torch`, `diffusers`,
  `transformers`, `rembg[cpu]`) -- lazy-imported only inside `_get_pipeline()`/`_run_pipeline()`/
  `_remove_background()`, following this project's standard pattern (`llm/providers/anthropic.py`,
  `gui/app.py::launch()`): an `ImportError` from any of them becomes a `ProviderError` naming the
  exact install command. No other code path needs these installed; `mock`-only usage, and
  `--assets none`, are completely unaffected. `sandbox/runner.py::_interpreter_briefing()` gained
  a matching availability note (mirroring the existing `pymunk` one) so a sandbox agent knows
  whether the extra is present in its interpreter without guessing.
- **Sandbox mode needed real new plumbing after all -- a live-caught bug, not a hypothetical.**
  `assets/` being fully copied and the `SandboxPathGrant(path=sys.prefix, read_only=True, ...)`
  added for `pymunk` were enough for the *package* to be importable inside a sandboxed run, but
  not for the *model weights* to be found: `HOME` resolves within that one sandboxed run's own
  ephemeral, per-attempt workspace filesystem, not the host's real home directory, so
  `diffusers`/`rembg`'s default cache locations (normally under `~/.cache/huggingface`, `~/.u2net`)
  landed inside the sandbox instead -- one real run's `sandbox_workspace` grew to 1.2GB from a
  full from-scratch SD-Turbo + U2Net download that vanished with the workspace, and would have
  repeated on every subsequent sandboxed run using this backend. Fixed with
  `generator_diffusion.py::model_cache_dir()` (`INFINIENV_MODEL_CACHE_DIR`, default
  `.infinienv_model_cache/` next to `.infinienv_asset_cache/`, setting `HF_HOME`/`U2NET_HOME`
  underneath it) plus a second `SandboxPathGrant` in `sandbox/runner.py` -- read-write, for that
  exact host path, with the env var explicitly set in the outer process before session creation
  so the sandboxed subprocess inherits the identical absolute path rather than each recomputing
  its own from a `cwd` that doesn't correspond to the host repo. One download, by any run
  (sandboxed or not), is now reused by every run after it -- the same reuse guarantee
  `.infinienv_asset_cache/` already gives individual sprites, just for the underlying model
  weights.

---

## 10. Creativity systems: mutation, curriculum, dataset export

### Mutation (`generation/mutation.py`)

`infinienv mutate <scene.json> --count N [--provider openai_agents --llm-fraction 0.5]`. Five
deterministic strategies (reposition objects, add obstacle, add distractor, reverse start, theme
reskin) plus an optional LLM-proposed strategy — `provider.propose_mutation(scene, seed)`,
duck-typed (only `OpenAIAgentsProvider` implements it; `mutate` skips the LLM path entirely if no
provider is given or `llm_fraction=0`). Every candidate, LLM-proposed or deterministic, goes
through the exact same `validate_scene()` before being kept; a failed/malformed LLM proposal is
caught and treated like any other rejected candidate — the loop just keeps trying, never crashes.
"Theme reskin" is metadata-only (`metadata.theme`, not a distinct per-theme object vocabulary) —
the object-type vocabulary being fixed-or-declared is a deliberate schema-simplicity choice, so a
"reskin" can't swap object types without redeclaring mechanics. Automatic key-door-dependency
injection isn't a deterministic strategy (only available via the LLM-proposed path) — see
`notes.md` for the scoping call.

### Curriculum (`generation/curriculum.py`)

`infinienv curriculum --theme X --levels N [--out path]` writes a prompts.txt-style level list
(5 built-in level templates: open-room pickup → obstacle → cross-room delivery → key/door →
decoy + long path). Add `--run --provider ... --seed ...` to actually execute every level
end-to-end (generate/validate/solve/render) into `<out>/level_NN/`, not just write the prompt
list — `<out>/prompts.txt` is still written alongside for benchmark compatibility.

### Dataset export (`export/dataset.py`)

`infinienv export-dataset <runs_dir> --out dataset.jsonl` scans a directory of executed run
folders (anything with `scene.json` + `metrics.json` — curriculum level dirs, benchmark
`prompt_NNN/` dirs, mutation-then-solve output, etc.) and emits one JSONL row per run:
`id` (unique: `<run_dir_name>__<scene_metadata_name>`), `prompt`, `scene_path`,
`asset_manifest_path`, `replay_path`, `gif_path`, `success`, `path_length`, `goal`, and
`programmatic_reward` — a **real per-goal completion signal** sourced from
`SolveResult.goal_results` in `replay.json` (`{"deliver_package": 1, "unlock_door": 1, "total":
2}`), not a single flattened success bit.

### Benchmark (`evaluation/benchmark.py`)

`infinienv benchmark <prompts.txt> --provider ... --out runs/benchmark` runs `run_generation`
over every prompt (blank lines and `#`-comments in the prompt file are skipped), aggregates valid
on first try / valid after repair / failed after repair / solved successfully / avg repair
attempts / avg path length / avg generation time, and writes `benchmark_summary.json`.

---

## 11. Sandbox agents: model-authored engine code, per-run isolated

Every capability above keeps the validator-wins guarantee intact by construction: the model
proposes data (a `SceneSpec`, a mutation, a declarative effect), deterministic code decides
whether it's valid. That's deliberate and it's not going away as the default. But it has a real
ceiling — the model can only express what the fixed action/goal vocabulary and section 5's fixed
effect-op vocabulary already support. `--sandbox` is the disclosed, opt-in exception: the user
explicitly asked for a general mechanism ("it could be any condition set by a user... update the
plan to allow sandboxes to code the game from our basis and edit everything too") after two
earlier rounds in this project where sandboxed code execution was proposed and declined on
exactly these correctness/determinism grounds. This section exists so that exception is documented
as plainly as the guarantee it trades away, not quietly bolted on.

### What it is

`infinienv generate --sandbox --prompt "..." --seed N --out runs/id` hands the scene prompt to a
`SandboxAgent` (OpenAI Agents SDK, `agents.sandbox`, `UnixLocalSandboxClient` — a local backend,
no Docker/cloud requirement) running inside a **fresh, isolated per-run copy** of this project's
`schema/`, `engine/`, `navigation/`, `validation/`, `render/`, and `assets/` packages (plus a
partial copy of `llm/base.py`, just for `ProviderError`, which `assets/generator_openai.py` and
`assets/resolver.py` need), plus a reference `run_scene.py` entrypoint
(`sandbox/workspace.py::build_workspace_dir`). The agent may read, edit, or add any file in that
copy — including rewriting the engine itself — to implement a mechanic the base vocabulary doesn't
support (a chasing NPC, a physics-based interaction, a custom win/lose condition), then must run
what it built and leave behind the same five standard artifacts every other run produces:
`scene.json`, `metrics.json`, `replay.json`, `render.png`, `replay.gif`. `pymunk` (a `physics`
extra in `pyproject.toml`) is available inside the sandbox if a mechanic needs real physics
simulation, but nothing requires the agent to use it — reusing the existing `SceneSpec` schema and
extending `navigation/policy.py` in place has worked just as well in live verification (see
below).

`--assets {none,local,generated,auto}` applies to sandbox runs exactly as it does everywhere else
(it used to be silently ignored — see "Asset generation inside the sandbox" below): a plain-text
`ASSETS_MODE` file at the workspace root tells the agent's `run_scene.py` which mode was
requested, and the default template resolves real sprites via the copied `assets/resolver.py`
before rendering, caching them in a per-run `./asset_cache/` inside the workspace (not shared with
the repo's real `.infinienv_asset_cache/` or across sandbox runs, same "no cross-run reuse"
precedent as sandbox-authored mechanics below).

### The isolation boundary, and why it's real

- **Nothing the agent does touches this repo or another run.** `build_workspace_dir` copies from
  the *installed* package into `<out_dir>/sandbox_workspace/`, a fresh directory scoped to that
  one run; the sandbox backend hydrates its own separate execution filesystem from a tar of that
  copy (`session.hydrate_workspace`), and after the run `sync_full_workspace` pulls the sandbox's
  actual final filesystem state back onto disk via `session.persist_workspace()` — overwriting the
  pre-run copy so `sandbox_workspace/` on disk is a true record of what the agent wrote, not the
  template it started from. This was a real bug caught during live verification: the first
  version of this code only ever extracted the five named artifact files, so the *kept* workspace
  silently stayed frozen at its pre-run state even though the agent had genuinely edited
  `navigation/policy.py` inside the sandbox — see `notes.md`.
- **The outer (trusted) process never imports or executes the sandboxed `.py` files.** It only
  ever reads back the five named artifact files (`sandbox/workspace.py::extract_artifacts`). If
  this process instead imported the sandbox's edited code afterward, the sandbox boundary would be
  theater — isolation only means something if untrusted code never runs outside it.
- **An outer sanity check re-parses the sandbox's `scene.json` against the real, unmodified
  schema**, and confirms `render.png`/`replay.gif` are genuine, non-trivial, loadable images, and
  that `replay.gif` is an actual multi-frame animation (`outer_sanity_check`). This is explicitly
  **not** a solvability guarantee — it can't be, that's the nature of the trade-off — just a floor
  against a malformed or fabricated success being reported. It exists because live verification
  found real cases of exactly that: a sandbox run that self-reported `"success": true` with a
  43-byte, header-only `replay.gif` it never actually checked itself; a run that self-reported
  success with a technically-valid, correctly-sized `replay.gif` that was just one static frame —
  a real image file, but not a replay of anything happening; and, found from a user report on a
  run's replay ("gui_1783609484 run failed replay"), a `replay.gif` with a correct header/trailer
  and well-formed frame descriptors — passing both `Image.verify()` and the frame-count check —
  but malformed LZW-compressed pixel data in every single frame, because `Image.verify()`
  validates GIF *container* structure, not that the pixel data inside actually decodes. All three
  are now caught before `success` can be `true`: the check forces a real per-frame `.load()` on
  both `render.png` and every frame of `replay.gif`, not just `verify()` plus a frame count. It
  also applies one **heuristic motion floor**: it best-effort-parses `replay.json`, extracts the
  main entity's per-frame position (trying the shapes agents actually write), and fails a run whose
  entity makes an egregious single-frame jump -- a scale-free outlier (`> 6x` the 90th-percentile
  step), which catches a `pos = target` teleport (a 10-30x spike) while leaving normal run/jump
  motion well under the bar. This modestly widens the check from "the artifacts are well-formed" to
  "the motion isn't physically absurd" -- still explicitly **not** a semantic-correctness guarantee
  (it can't judge whether the game's *rules* are real), just a floor against the specific,
  repeatedly-observed failure of an agent assigning a position straight to a target. Best-effort:
  an unrecognized `replay.json` shape skips the motion floor rather than failing (see the
  "harness-enforced no-teleport floor" subsection below). It's the one enforced correctness floor
  the agent can't skip by writing a weak self-check, added after prompt guidance alone repeatedly
  failed to stop teleports reaching a user.
- **An agent conversation that doesn't finish cleanly (e.g. hits its turn budget) still gets a
  full, honest report.** `sandbox/runner.py` captures that failure as `run_error` rather than
  letting it propagate past artifact extraction, workspace sync, and the sanity check — whatever
  the agent produced up to that point is still extracted, sanity-checked, and recorded as one
  attempt in the repair loop below, instead of being reported as a bare crash.
- **Copied modules import each other from the sandboxed copy, not the real installed package.**
  `infinienv` is installed editable (`pip install -e .`), so it's importable from any process on
  this venv regardless of `cwd`. The files `build_workspace_dir` copies use this project's normal
  `from infinienv.engine.grid import Grid`-style absolute imports, which — uncorrected — resolve
  to the real installed `infinienv` package, not the sandboxed copy sitting next to them. That
  meant an agent's edit to its copy of `engine/grid.py` could be silently ignored by any other
  copied module that still imported `infinienv.engine.grid`, directly contradicting this section's
  claim that the agent can edit anything "including rewriting the engine itself." This was a real,
  previously-undetected gap, not a security issue (the sandbox still can't write back to this
  repo's actual files) — a correctness gap between what the mode promises and what it delivered.
  Fixed by `_rewrite_internal_imports()`: after copying, every `.py` file in the workspace has its
  `infinienv.X` imports rewritten to bare `X` so cross-module references resolve locally. Covered
  by `test_build_workspace_dir_copy_is_actually_self_contained`, which runs a real subprocess with
  `cwd` set to the built workspace and asserts `engine.grid.Grid`'s `__file__` points at the
  sandboxed copy, not site-packages — the only way to actually catch this class of bug, since an
  in-process assertion would share `sys.path`/`sys.modules` with whatever already imported the
  real package during test collection. See `notes.md` for the full account, including a first
  version of the rewrite regex that missed indented/lazy imports (e.g. `resolve_assets()`'s
  function-body import of `generator_openai`).
- **On macOS, the SDK confines every `exec_command` with a real `sandbox-exec` (Seatbelt)
  profile**, not just a workspace-directory convention — it denies filesystem reads under broad
  roots including the entire `/Users` tree, then narrowly re-allows the ephemeral workspace root
  plus a small, hand-picked system allowlist. This is a real, previously-undiscovered
  consequence: a harness-local Python environment living under a user's home directory (e.g. a
  project `.venv`, the normal case) is reachable by *name* (its executable's containing
  directory gets auto-allowed) but not by *content* — its `lib/site-packages` stays denied, so
  the interpreter crashes during its own startup trying to read `pyvenv.cfg`
  (`Fatal Python error: init_import_site`, root cause a `PermissionError`), regardless of which
  absolute path the agent is told to invoke. `sandbox/runner.py::_run_async` now constructs the
  session's `Manifest` with `extra_path_grants=(SandboxPathGrant(path=sys.prefix,
  read_only=True, ...),)`, granting read-only access to the harness's own Python prefix so its
  interpreter (and everything installed in it, `pymunk` included if the `physics` extra is
  present) actually works inside the confinement. Reproduced and fixed against the SDK's real
  profile-generation code, not guessed — see `notes.md` for the full three-round diagnosis
  (prompt-only fixes were necessary but insufficient; the actual blocker was structural, not
  agent behavior).
- **`sandbox/runner.py::_interpreter_briefing()` tells the agent exactly which Python
  interpreter to use** (`sys.executable`, the same one running the harness) and whether
  `pymunk` is importable in it, checked at runtime. Without this, an agent has no way to know
  which of several interpreters on the host has this project's dependencies and burns turns
  hunting through `which -a python`, other interpreters, and `-S` (which disables site-packages
  on *any* interpreter) — observed live before this fix landed. The briefing also explains that
  shell commands run through a login shell that reorders `PATH` on every command (so a bare
  `python`/`python3` name is unreliable even with a correctly inherited environment — always use
  the absolute path), and that `PYTHONHOME=` (empty) is a real crash-inducing override, not a
  no-op.

### Live narration made this bug visible in the first place

Both bugs above (import isolation, pymunk access) were found *because* of section 11's live
narration feature (below), not despite it. Before narration existed, an agent quietly giving up
on `pymunk` and falling back to hand-rolled force-based physics looked identical to an agent
*choosing* hand-rolled physics as a legitimate design decision — there was no way for a run's
output, or a user watching a run, to tell the difference. A user pasted a live narration
transcript showing the agent's actual `which -a python`/`-S`/`PYTHONHOME=` flailing and asked why
— that transcript is what made this fixable at all.

### A failure class the outer check structurally cannot catch: a real-looking fake simulation

A user reported a sandbox run ("Italian man rescues a princess from a tower, avoiding turtles")
where the replay showed the hero walking straight over the turtles and up to the tower with no
ladder, and a health bar that never did anything, despite `metrics.json` self-reporting
`"success": true` and the outer sanity check passing. Reading the synced `run_scene.py` found the
actual bug: the agent had computed the hero's and turtles' positions as **fixed functions of the
frame index alone** — a hardcoded list of waypoints interpolated with easing for the hero, a
sine-lane oscillation for each turtle — then checked "collision avoided" *after the fact* as a
distance formula between those two already-decided paths. This is not a simulation; it's an
animation of an outcome chosen in advance. It can't enforce any rule, because no rule was ever
evaluated during "play" — the character glides through walls and past hazards because the curve
was drawn far enough away, not because anything blocked it. `outer_sanity_check` correctly passed
this run: `scene.json` parsed, the images were real, `replay.gif` had 96 genuinely different
frames. It has no way to know those frames came from stepping real game state versus a lookup
table — judging that is exactly the kind of semantic mechanics check section 11's own scope notes
already rule out ("not achievable without reintroducing the fixed-vocabulary constraint this mode
exists to escape"). This is a structural blind spot, not an oversight to patch in the checker.

The fix is therefore in `sandbox_agent.md`, not the outer check: two new sections. **"Simulate,
don't animate"** names the exact anti-pattern (position as a pure function of frame index,
success/collision computed as a post-hoc geometric check against a pre-decided path) and gives a
concrete self-test ("if you can compute frame 50 without having stepped frames 0–49 in order, you
built an animation, not a simulation"), requiring instead a real `state = step(state, dt)` loop
where collisions, hazard-contact health loss, and structure-gated movement (a ladder cell required
to traverse a column, etc.) are resolved from *current* state every frame. **"Before you finish,
look at your own gameplay"** requires the agent to extract several representative frames from its
own `replay.gif` (start, a hazard-proximity moment, any rule-triggering moment, the end) and
actually call the sandbox's built-in `view_image` tool (from the `Filesystem()` capability) on
them plus `render.png`, reasoning explicitly about whether what's depicted is consistent with the
rules it wrote down — and, if not, fixing the simulation and re-rendering rather than adjusting a
threshold or the reported `success` value to make a check pass. The closing "keep iterating" note
makes explicit that clearing the outer check is necessary but not sufficient — a run that passes
it but fails the agent's own honest visual review is still a failure, and the point of the
existing repair loop (below) is to keep trying until a real one lands, not to stop at the first
attempt that merely doesn't crash.

### Follow-on findings from watching the fix in production, and why the prompt no longer lists them one by one

Live narration of five subsequent runs (same "rescue the princess" prompt) surfaced five more real
bugs, each traced from a user report to an exact line of agent-authored code and fixed with a
prompt addition naming that exact case: a miscalibrated hitbox (contact math using `< 0.32` tile
units against sprites drawn ~0.5–0.875 tiles wide, so sprites visibly touched with no consequence);
a gating rule silently punched open by its own debugging fallback (`on_ladder = ... or x > 12.4`);
hazards that technically obeyed every rule but could never geometrically reach the agent's
hardcoded route; and a "dodge" implemented as unconstrained vertical velocity — the character
hovering at whatever height was safest, no gravity, no jump arc. (A fifth, separate finding in this
project's own code rather than agent-authored code: narration's `_describe_tool_output` showed only
a failed command's *first* output line, which hid the real error behind incidental noise like
`perl`'s cosmetic locale warning — fixed by showing first and last line when they differ; see
`notes.md` for the full account of all five, including exact code and live-verification transcripts
for each.)

After the fifth round, the user made the correct structural objection: patching the prompt with a
new named worked example for every incident doesn't scale, and re-framed the actual requirement --
"the solving agent can only do stuff allowed in the game rules and any other actions should not be
allowed." That's this project's own "validator wins" principle (section 2: a fixed vocabulary,
deterministic code decides what's legal), which every one of the five bugs was actually a
violation of in agent-authored physics with no external validator: some code path mutated
position/health/state *outside* whatever the control logic was meant to be limited to. Re-examined
that way, five separate incidents collapsed into one recurring root defect wearing different
costumes. `sandbox_agent.md`'s five separate "a real, previously observed bug" paragraphs were
replaced with five *general*, numbered principles under "Design principles: a closed action space
is what makes a simulation real":

1. Write the rules down, then build a small, fixed set of action/physics functions that are the
   *only* code path allowed to change state. Decision logic may only select among them, never
   assign state directly.
2. A rule with exceptions isn't a rule — gravity/collision/gating/contact apply unconditionally; a
   stuck controller means fix the decision logic or the level, never loosen the rule.
3. Every declared hazard/structure must be reachable by what the action space can actually do, and
   grounded characters only move vertically via climbing or a real jump arc, never a free velocity.
4. Size contact/collision against what's actually drawn.
5. The general self-test: for any state change in the trace, can you name the declared action that
   produced it? If not, that's the root defect, whatever form it takes.

The self-review section was also rewritten to lead with a **programmatic invariant check over the
whole trace** (a short script asserting the rules actually hold — every action in the declared set,
every hazard came within threat range at some point, a grounded character's height only changes via
climb/jump) *before* the pre-existing qualitative frame-sampling pass — exhaustive and precise
where sampling a few frames is neither, and a much more literal way to make "the agent finds these
issues itself" true.

Live-verified the rewrite is a genuine structural change, not just reworded prose: the next run
produced exactly three action functions (`walk_right`/`wait`/`climb_up`, declared explicitly and
enforced — no other code path touched position), a `check_trace()` function that actually asserted
those invariants and was actually called before writing output, and correctly chose climbing over
jumping since this task has a real ladder (the principle is "climb or jump," not "always jump").
One real, partial gap surfaced even so: two of three turtles turned out to patrol rows the agent's
path never reached — principle 3 already covers this, but the self-review's example invariant list
never named "every hazard came within threat range at some point" as a concrete check, so the
agent's own `check_trace()` didn't verify it. Fixed by adding that as an explicit example under the
existing invariant-check step — filling out an enumeration under a principle that already existed,
not adding a sixth named incident. See `notes.md` for the full, honest verification transcript,
including the gap — reported as found, not glossed over, consistent with this project's standing
practice of verifying against real output rather than a self-report.

### Self-repair against the outer sanity check

A single agent attempt failing the outer sanity check isn't the end of the run. `sandbox/runner.py`
mirrors `generation/compiler.py`'s repair loop for the non-sandbox path: if the outer check fails,
the concrete failure (the real pydantic error, the missing-frame message, whatever it was) is fed
back to the *same* agent as a new message, and it gets another attempt — up to
`--max-repair-attempts` times (default 2, so 3 attempts total; `INFINIENV_SANDBOX_MAX_REPAIR_ATTEMPTS`
env override). The sandbox *filesystem* persists across attempts (same session, same
`hydrate_workspace` call from the start of the run) even though each attempt is a fresh agent
conversation with no memory of the previous one — the repair prompt tells the agent its prior
files are still on disk and to inspect (`ls`/`cat`) and fix them rather than starting over. Every
attempt is recorded in `metrics.json`'s `repair_history` (mirroring the non-sandbox path's
`repair_history` in `validation.json`) so a reviewer can see exactly what failed and what changed
between attempts, not just the final verdict. This does not weaken the outer sanity check or make
its failure less real — it's still the harness deciding pass/fail, not the model; the model simply
gets more chances against the same real check, the same way the non-sandbox path gets more chances
against the same real validator.

### Live narration of what the agent is actually doing

`--sandbox` runs used to report only coarse attempt-boundary progress ("Running sandbox agent
(attempt 1/3)...") for the entire agent conversation, then dump the final summary at the end — a
reviewer watching the CLI or GUI live had no visibility into what the agent was actually doing in
between. `sandbox/runner.py` now drives the agent via `Runner.run_streamed` instead of the
single-shot `Runner.run`, consuming `stream_events()` as the conversation happens and turning each
event into a short line through the same `on_stage` callback everything else already uses — no
new plumbing needed on either the CLI or the GUI, since both already render every `on_stage`
message as its own line. `sandbox/runner.py::_describe_stream_event` maps:

- a shell command the agent runs (`exec_command` tool call) → `$ <command>`
- files the agent edits via `apply_patch` → `Editing: edit <path>, add <path>, ...` — the file
  list only, parsed from the patch's own `*** Add/Update/Delete File:` headers, **never the hunk
  content itself** (the user explicitly didn't want a diff surfaced, just the decision)
- a failed shell command's exit code and first line of output (successful commands and every
  `apply_patch` result stay silent, since the intent was already announced and a `0` exit isn't
  informative)
- the model's own reasoning summary and any intermediate message text, when the model produces
  one (`Thinking: ...` / `Agent: ...`) — this is what actually surfaces the agent's *decisions*
  ("Python is picking a blocked venv; I'll rerun with system isolation disabled."), not just its
  actions

This is deliberately duck-typed against the stream event/item shapes (no `agents` import, no
`isinstance` checks against SDK classes, wrapped in a `try/except` that swallows and silences any
per-event failure) so it degrades gracefully rather than crashing a real run if a future SDK
version changes an item's internal shape — narration is best-effort commentary layered on top of
a real run, never something a run's correctness depends on. Live-verified: a real sandbox run
against a kitchen-delivery prompt showed the actual shell commands the agent ran, its own stated
reasoning as it worked around a blocked default Python interpreter in its workspace, and failed
attempts with their exit codes, before its final summary — all pure narration, not synthesized
after the fact from the finished artifacts.

### What a run's `metrics.json` looks like

Sandbox runs are labeled `"source": "sandbox"` and carry both verdicts side by side, so a
reviewer can immediately tell which guarantee (if any) applies and where the two checks agreed or
disagreed:

```json
{
  "source": "sandbox", "provider": "openai_agents_sandbox", "model": "gpt-5.5", "seed": 2,
  "success": true, "sandbox_self_reported_success": true,
  "outer_sanity_passed": true, "outer_sanity_error": null,
  "audited": true, "audit_passed": true, "audit_findings": null,
  "deterministic_validation": {"ran": true, "valid": false, "errors": ["UNSOLVABLE"], "enforced_codes": ["DUPLICATE_ID", "OUT_OF_BOUNDS"]},
  "missing_artifacts": [], "repair_attempts": 0,
  "repair_history": [{"attempt": 0, "run_error": null, "outer_sanity_passed": true, "outer_sanity_error": null, "audited": true, "audit_passed": true, "audit_findings": null, "missing_artifacts": []}]
}
```

`success` is `true` only if both the artifact set is complete and the outer sanity check passes
(and, if the agent conversation itself failed, `success` is always `false` regardless of what
partial artifacts exist). CLI output for `--sandbox` prints the sandbox agent's own summary and
the kept workspace path, distinctly from the normal `generate` progress output, so a reviewer
never confuses a sandbox run's report for a validator-guaranteed one.

### Live verification (see `notes.md` for the full account)

A prompt describing a chase/catch mechanic ("a girl and a boy NPC chase the agent; touching the
agent before a friend is delivered fails the run") was run twice. Both times the agent reused the
real `SceneSpec` schema (grid/agent/objects/walls/goals, `sequence` goals,
`mechanics.custom_object_types` for the NPC types) rather than inventing an incompatible format,
and extended `navigation/policy.py` in place with real chase-stepping logic dispatched off the
scene's declared custom object types — confirmed by diffing the synced workspace against this
repo's actual `navigation/policy.py`, not by trusting the agent's own summary. `render.png` was
confirmed to be a genuine render from this project's real renderer. An earlier run (before the
turn budget and prompt were tuned) demonstrated the failure path instead: the agent invented its
own incompatible scene format, which the outer sanity check correctly rejected.

Separately, five prompts targeting distinct `pymunk` physics behaviors (steering-force pursuit,
momentum/pushable objects, projectile arcs, collision ricochet, multi-body herding) were run live
to stress the physics side specifically. The first attempt at the pursuit prompt reproduced the
exact "invents its own incompatible scene format" failure mode again (a pixel/world-coordinate
format with `mechanics.robot_force`, correctly rejected by the outer check) — the sandbox prompt
was tightened to explicitly require self-validating `scene.json` against the copied schema before
finishing and to clarify that `scene.json` only needs the *static* layout (continuous physics
state can live in the agent's own code). Two more real bugs surfaced from there, both fixed and
covered by regression tests: the `run_error`-swallowing bug described above, and — found from a
user-reported "the gif is just blank" on an otherwise-`SUCCESS` run — a `replay.gif` that was
technically a valid, correctly-sized image but only one static frame, which passed every existing
image check without showing anything happening. `outer_sanity_check` now also requires
`replay.gif` to have more than one frame. Re-run after both fixes, the same prompt succeeded on
the first attempt with a genuine 56-frame animated GIF (agent visibly moving across the maze from
spawn to exit while the robot trails behind) — visually confirmed by extracting and inspecting the
first and last frames, not just checking `success: true`.

### Generic reusable game-dev primitives, not per-case prompt instructions (2026-07-09)

A user-reported screenshot of a Mario-style prompt ("...moving plants try to eat him from below
like a side-scrolling platform game") showed the "chomping plants" drifting side to side along the
ground on a sine wave, with a static mouth shape, and asked "where are the animations and the full
feature set that a later stage project has." Reading the actual generated code
(`runs/gui_1783629196/sandbox_workspace/run_scene.py`, not just the agent's self-report) confirmed
both complaints: `plant_position()` moved every plant purely horizontally, and `draw_frame()` drew
the exact same three fixed primitives at the plant's current position every frame — nothing about
any entity's *drawn state* ever varied with time, only its position.

**First attempt (rejected by the user):** worked-example prose in `sandbox_agent.md` — a sentence
naming "the canonical Piranha-Plant pattern," and a code snippet keyed on
`plant_open`/`plant_closed`. The user explicitly rejected this: *"i dont want you to add that
stuff to the sandbox prompt, i need you to engineer it in a way that makes that behavior possible
without specifying specific cases into it... new info/sprite costumes etc should be in our sandbox
already, new types of characters/actions too should be makable easily."* This is the same lesson
already learned one level up in this same section (five bug-specific prompt patches consolidated
into general "closed action space" principles) — applied here to conclude that a worked example
in prose doesn't scale, but real importable code does.

**Structural fix.** Three small, generic, reusable pure-function modules were added under
`engine/` (already copied verbatim into every sandbox workspace by
`sandbox/workspace.py::_COPIED_PACKAGES` — no workspace-builder change needed, and its existing
`_rewrite_internal_imports()` already rewrites any `.py` file's `infinienv.X` imports generically):

- `engine/action_registry.py` — `ActionSpace`: `register()`/`.action()` decorator/`dispatch()`,
  raising `UnknownActionError` on an unregistered name. Makes "state may only change through a
  declared action" (principle 1) structural rather than a discipline upheld by memory.
- `engine/motion_patterns.py` — `patrol()` (sinusoidal back-and-forth), `pulse_cycle()` (a
  rise/hold/fall/idle timing curve for anything that emerges and retracts on a cycle — the
  general form of what a Piranha-Plant-style hazard needs, without the module knowing or caring
  what kind of hazard it is), `pursue()` (step toward a target at capped speed, snapping instead
  of overshooting).
- `engine/animation.py` — `phase_of()` (time to a repeating `[0, 1)` phase), `oscillate()` (sweep
  a drawn parameter between two values by phase), `cycle_variant()` (pick a named sprite/pose by
  phase).

`assets/resolver.py` gained `variant_types()`/`variant_descriptions()` (a canonical
`{base}__{state}` naming convention) and `resolve_assets(scene, mode, cache_dir, *, extra_types=,
extra_descriptions=)` (keyword-only, backward compatible) — closing a real gap found during
design: `scene_asset_types()` only scans placed `SceneObject`s, so an animation-variant sprite
type with no placed object instance was previously never requested by the automatic scan.
`sandbox_agent.md` now only *points at* these modules (a short "Reusable building blocks" section,
plus one inline sentence each at principles 3 and 6 naming the specific function names) — no
worked creature/game example anywhere in the prompt.

**Live verification, round 1 (honest null result).** A prompt describing an unrelated mechanic
(a factory floor with an erupting steam vent and a chasing security drone —
`runs/factory_infra_test`) produced a working, correct run, but reading the synced `run_scene.py`
showed the agent had *not* imported any of the three new modules — it hand-rolled its own
`vent_active()` (a phase-cycle function functionally identical to `pulse_cycle`), `step_toward()`
(identical to `pursue`), and inline sine-driven animation (identical to `oscillate`). The good
news: an independent agent converged on nearly the same three primitives from scratch, validating
the library's shape. The bad news: it never looked at the library — the pointer existed but wasn't
prominent enough to change behavior under the same turn-budget pressure that was already driving
many iterative fix-and-rerun cycles in that run.

**Visibility fix, live verification round 2 (confirmed).** Per this section's own standing rule
("increase visibility, not prescriptiveness" — never re-add a worked example), the "Reusable
building blocks" section's framing was strengthened from "none of these are required" to "prefer
these over writing the same math yourself," and principles 3/6 were changed from a "see below"
cross-reference to naming the actual function signatures inline at the point of relevance — still
fully generic, no creature or game named. Re-run with a second, again-unrelated prompt (a
submarine cave with blooming stinging anemones and a pursuing eel — `runs/cave_infra_test`):
reading the synced code confirmed genuine, correct reuse this time — `ActionSpace` gates all
motion through registered `thrust`/`hold` actions via real `register()`/`dispatch()` calls (not
decorative), `pulse_cycle()`'s return value drives both the sting-gating logic *and* the anemone's
drawn bloom radius/spike angle (real animation, not just internal math), and `pursue()` drives the
eel's distance-gated chase-vs-return-to-rest behavior. Confirmed visually, not just from the
agent's summary: extracted frames show the submarine's health dropping from 5 to 3 (a real hazard
contact) and the same anemone in genuinely different bloom states between frames.

Separately, `sandbox/runner.py`'s `max_turns` default was bumped from 40 to 60 (the original
bug-report run had already hit the 40-turn ceiling once on its first attempt).

### Grounded-character physics: `engine/platformer_physics.py` (2026-07-09)

A user-reported screenshot on a Mario-rescue prompt ("really bad character asset, runs off the
screen, flys on a place it cant and teleports") led to reading `runs/gui_1783638533`'s real
`metrics.json` and agent-authored `run_scene.py` directly, surfacing four concrete, distinct bugs:

1. **Bad character asset**: `asset_notes` (this session's earlier fix, immediately paying off
   again) showed the hero's two sprite variants both failed with a real OpenAI
   `400 moderation_blocked` ("Your request was rejected by the safety system... category:
   other") — very likely "an Italian man in green clothing" reading as a request to depict a
   copyrighted character. `tower`/`wall` separately hit the same rate limit found earlier. Every
   custom type in the scene fell back to a hand-drawn primitive; the hero's fallback was visibly
   cruder than the plant's. Addressed by flipping the default sprite backend, above.
2. **Runs off the screen**: `vx` was set to a constant run speed unconditionally every step,
   including during the `climb_tower` branch — nothing ever zeroed it while climbing, so the
   character kept drifting horizontally off the tower's face the entire time it was supposedly
   climbing straight up. No world/screen-bounds clamp existed anywhere in the file.
3. **"Flies in a place it can't"**: the climb condition was gated by a *lower* x-bound only, no
   upper bound tied to the tower's actual right edge — once bug 2 drifted the character past the
   tower into open air, the condition was still true, so it kept "climbing" (rising) while
   floating beside the structure in empty space. A direct violation of principle 3 ("climbing a
   real structure," not a loosely-gated condition).
4. **Teleports**: a post-rescue "celebration" tail appended frames at a hardcoded literal position
   regardless of where the trace's actual last simulated position was — given bugs 2-3, often far
   from that literal, so the very next frame snapped instantly. A violation of principle 5 ("can
   you name the declared action that produced this state change?").

Also notable: this run's code imported `engine/motion_patterns.py`/`engine/animation.py`
(genuinely used, for the plants and flag) but **not** `engine/action_registry.py` — the player's
own movement was a hand-rolled ad hoc if-chain, not routed through discrete, mutually-exclusive
registered actions. That's the structural root of bugs 2-3: nothing prevented "run" and "climb"
from both partially mutating state in the same frame.

Consistent with this session's now twice-confirmed rule (fix root causes with real code, don't
patch the prompt with this incident's specific name): `engine/platformer_physics.py` generalizes
*player-locomotion* physics the same way `motion_patterns.py`/`animation.py` generalized *hazard*
motion — gravity/ground/climbing/world-bounds are needed by nearly every platformer-style sandbox
scene and were exactly where these bugs came from being hand-rolled fresh, unbounded, and
overlapping.

- `integrate_grounded_2d(pos, vel, *, gravity, dt, ground_y, bounds=None)` — one physics step:
  gravity, integration, ground clamp, and (if given) a silent world/screen-bounds clamp — directly
  targets bug 2's missing bounds check.
- `climb_step(pos, climb_speed, dt, *, structure_bounds)` — moves *only* `y`; structurally cannot
  also apply a horizontal run velocity in the same call, which is what makes bug 2's climb-drift
  impossible if this is used instead of hand-integrating `y` during a climb branch. Raises
  `ValueError` if `x` is outside the structure's bounds — bug 3's exact shape becomes a loud
  failure during the agent's own testing, not a silent floating character, following the same
  "structurally unable to do that" philosophy as `engine/action_registry.py`'s
  `UnknownActionError`.
- `clamp_to_bounds(pos, bounds)` — the standalone form, usable every frame regardless of which
  action fired.

Prompt changes were deliberately minimal and principle-level only, no named incident: one line in
the "Reusable building blocks" list, one clause added to principle 3's existing grounded-movement
sentence (pointing at the two functions, phrased generically — "a run action," "a climb branch,"
no mention of Mario/towers), and "world/screen bounds" added to principle 2's existing list of
rules that must apply unconditionally. Bug 4 (the teleport) isn't a new principle — it's already
squarely covered by principles 1 and 5, which this run simply didn't fully apply (its own
self-check asserted success and hazard proximity but not "no unexplained position jump between
consecutive frames," despite that exact check being one of the self-review section's suggested
examples).

### Grid-wall collision and procedural terrain: `engine/grid_collision.py` /
### `engine/level_generation.py` (2026-07-09)

A user-reported screenshot on a cave-navigation prompt ("A cave explorer chooses among uneven
rocky tunnels... collects at least two glowing gems, then exits") flagged two complaints: the
agent visibly phased through solid rock, and the level had no uneven terrain or multiple paths
despite the prompt explicitly asking for a "procedurally generated cave... multiple possible
paths." Reading the actual generated code confirmed both, precisely:

- **Phasing through walls**: the agent's movement wasn't a real simulation at all — a hardcoded
  `route` list of waypoint cells was interpolated in a straight line, cell-center to cell-center,
  with **no check against the scene's own `walls` array** the same script had just generated. A
  direct reproduction against the actual `route`/`floors` data found the bug was not hypothetical:
  one waypoint, `(7,6)`, routed straight into a cell that was never a floor cell at all, and two
  consecutive-waypoint segments cut diagonally through a wall corner where *both* adjacent cells
  were blocked (`(6,6)`/`(7,7)` and `(11,8)`/`(12,9)`). This is the same "animation, not
  simulation" anti-pattern already named in this section's history, just for grid navigation
  instead of platformer physics: a route was planned to *look* like it avoids walls, then trusted,
  never actually checked.
- **No procedural generation, no real branching**: `floors`/`path_cells` was a hand-listed set of
  specific grid cells forming essentially one winding corridor with a couple of one-cell alcoves —
  nothing procedural, and no gameplay-relevant choice between distinct routes, regardless of what
  the prompt asked for.

Two new generic modules, same reasoning as every prior addition in this section — a real, tested
capability the agent can import, not a worked example in prose:

- `engine/grid_collision.py` — `segment_blocked(p0, p1, blocked, tile_size)` checks a straight-line
  move at sub-tile sampling resolution (not just its endpoints), which is exactly what would have
  caught the diagonal-corner-cut bug; `move_with_collision(pos, target, speed, dt, blocked,
  tile_size)` is a drop-in replacement for hand-rolled waypoint interpolation that stops at a wall
  instead of moving through it, mirroring `motion_patterns.pursue()`'s shape with real collision
  awareness added.
- `engine/level_generation.py` — `generate_organic_region(width, height, start, *, steps, seed,
  branch_chance, max_walkers)`, a seeded branching random-walk ("drunkard's walk") cave carver:
  every carved cell is connected to `start` by construction, and real branch points emerge from
  the algorithm itself rather than needing to be hand-designed, directly answering "procedurally
  generated... multiple possible paths." `region_is_connected(region, start)` is a general BFS
  reachability check, useful for verifying *any* level (generated or hand-authored) is actually
  fully navigable — the motivating bug's out-of-floor waypoint would have failed this check
  immediately instead of surfacing as a visual glitch.

Prompt changes stayed principle-level: both new modules added to "Reusable building blocks";
principle 2 (rules must apply unconditionally) gained a clause naming grid-wall collision as one
of the rules that must actually be checked, not just planned around; principle 3 (build what the
task describes) gained a clause extending its existing "read the task's language, don't default to
the easiest pattern" idea from hazard motion to level structure itself; the self-review section's
invariant-check example list gained "no consecutive position pair in the trace crosses a wall
cell" alongside its existing examples. No mention of caves/gems/this specific run anywhere in the
prompt.

### Generic state and gating: `engine/puzzle_state.py` (2026-07-09)

Feedback on the cave-navigation fix above wasn't about it being wrong — it worked, verified live.
It was about a capability ceiling: every sandbox run this session (a Mario-style rescue, a cave, a
factory floor, a submarine cave) had produced *static navigation* — walk through a space, avoid or
reach things — with the win condition collapsing to whatever single check is simplest (a bare
position, a raw item count), never real state-dependent puzzle logic: a locked exit that only
opens once several conditions are jointly satisfied, an ordering between sub-objectives. The
user's own graded difficulty table named this precisely (open room → maze → maze+hazards →
**maze+lock/key/gems+required order** → moving hazards+switches+crates+NPCs+backtracking) and
asked for a *generic* fix, explicitly not another round of tuning the cave prompt specifically.

Root cause, consistent with every module added in this section: no reusable primitive existed for
*state-dependent gating* the way `action_registry.py` gave closed action dispatch and
`grid_collision.py` gave real wall collision. The base engine's schema already models locks/keys
and ordered `sequence` goals, but that's wired through `GameState`/`solve_scene()`, which no
sandbox run observed this session actually uses (every one writes its own custom simulation loop).
New module, same shape as the others — pure, dependency-free, mirrors `action_registry.py`'s
"declare once, check structurally" philosophy applied to preconditions instead of actions:

- `PuzzleState` — a named flag/counter store: `set()`, `increment()`, `get()`, `snapshot()`.
- `Gate` — a declarative precondition over several flags/counters jointly, e.g.
  `Gate(requires={"gems": 2, "plate_pressed": True})`, with `is_open()`/`missing()`. Numeric
  thresholds are satisfied by `>=` (composes with `increment`); boolean thresholds by equality; an
  unset flag defaults to `0`/`False`, so a gate starts closed by default.

Prompt changes: one "Reusable building blocks" entry, and a genuinely new principle 7 (state/
sequencing the task describes must be real dependency structure, not collapsed to the simplest
true check) — a new principle rather than folded into principle 3 again, since this is a different
category of concern (win-condition *structure*, not motion/terrain/rendering), the same reasoning
that justified principle 6 (animation) as its own addition earlier. The self-review invariant list
gained one more example: if a `Gate` was declared, assert it was actually closed at some point in
the trace before it opened.

**Live-verified on the user's own suggested harder prompt** (not the cave prompt this fix was
explicitly not meant to over-fit to): *"Create a cave maze where the exit is locked until the
player collects two gems, avoids spikes, presses a pressure plate, and then reaches the exit."*
First attempt, no visibility-tuning round needed this time. Confirmed from the synced code, not
the agent's summary: `Gate({"gems": 2, "plate_pressed": True})` gates the exit directly
(`if pos==exitc and gate.is_open(pstate) and not state["lost"]`), and the agent went further than
asked — the pressure plate itself only activates *after* 2 gems are collected
(`pos==plate and pstate.get("gems",0)>=2`), a real ordering dependency on top of the joint gate,
unprompted. Confirmed with the actual trace data, not assumed: `gate_open` was `False` for 28 of
36 steps and only flipped to `True` once every condition was met, staying open through the end —
the gate was genuinely tested, not decorative. The run also used `action_registry.py` (closed
action dispatch) and `grid_collision.py`/`animation.py` alongside `puzzle_state.py` in the same
file — four of the session's reusable primitives composed together in one run. `render.png` showed
a coherent branching maze with gems, spikes, a visible pressure plate, and the exit.

### Completing the primitive vocabulary: crates, reactive NPCs, perception, pathfinding (2026-07-10)

The previous round's report closed with "crates (pushable objects) and reactive NPCs aren't
addressed -- not claimed as solved," and the user objected to leaving that hanging: *"I need you
to fully flesh out it so it becomes really really strong and able to handle any prompt."* This
round closes the remaining categories in the user's own graded difficulty table so its top tier
(moving hazards, switches, crates, NPCs, backtracking) is covered by tested, composable
primitives. Honest framing kept: "any prompt" is aspirational, not a provable claim; the
defensible claim earned here is that **no common structural 2D-game mechanic is left for the agent
to hand-roll from scratch.**

Two coupling facts drove the design (same relationship `grid_collision.py` already had to the base
engine): `navigation/astar.py::find_path` needs a full `Grid` (unusable from a sandbox custom loop
with only a wall-cell set -- the cave run hand-rolled BFS because of this), and
`engine/physics.py::try_push` mutates `ObjectState` against a `Grid` (unusable for a crate tracked
as a plain `{id: (x,y)}` dict). Four new dependency-free `engine/` modules, auto-copied into every
workspace, close the gaps -- crates map to `pushables`; a genuinely reactive NPC needs all three
of `agent_behavior` + `vision` + `pathfinding` (the decision, the perception, and maze navigation
respectively; a half-version with distance-only triggers and straight-line motion is the same
"collapsed to the simplest thing" failure this session keeps fixing):

- `engine/pushables.py` -- `try_push_block()` (a crate can't be shoved through a wall, another
  block, or off-grid), `cell_is_free()` (so a block obstructs the agent), `all_targets_satisfied()`
  (the "every crate on its switch" win check).
- `engine/pathfinding.py` -- `find_path()` (BFS shortest path over a plain wall-cell set) and
  `next_step_toward()` (per-frame first step) -- lets a chasing NPC navigate *around* walls in a
  maze instead of straight-lining into them.
- `engine/vision.py` -- `has_line_of_sight()` (reuses `grid_collision.segment_blocked`, no
  duplicated raycast), `within_range()`, `within_cone()`, and `can_see()` composing them -- makes
  "chases on sight" a real occluded-line check, not a distance check that sees through walls.
- `engine/agent_behavior.py` -- `BehaviorMachine`: a reactive NPC's decision logic as a declared,
  testable state graph (patrol -> chase -> return) instead of an ad-hoc if-chain that gets stuck
  in one state. A pure FSM -- the caller wires `vision`/`pathfinding` into its conditions/actions.

Prompt changes stayed at the level the session settled on -- no new principle (reactive NPCs and
crates are both "build what the task describes, don't collapse to the simplest version," already
covered by principles 2 and 3): four "Reusable building blocks" entries; a clause on principle 2
(a pushable obeys collision -- use `try_push_block`, don't reassign the coordinate); a clause on
principle 3 (a described-as-reactive NPC needs a real behavior machine + perception + pathfinding,
not one fixed motion or a see-through-walls distance check); and one self-review invariant example
(a reactive NPC's behavior state must actually change across the trace).

**Live-verified on a composite "very hard"-tier prompt** (not a re-run of any prior prompt): a
dungeon where the player pushes a crate onto a switch to open a locked gate while a guard patrols
and chases on sight, then backtracks to the opened gate and exits. First attempt, no
visibility-tuning round. Confirmed from the synced code, not the summary: all four new modules
imported and load-bearing -- `try_push_block`/`cell_is_free`/`all_targets_satisfied` (crate
pushing + the crate-on-switch gate condition + the crate obstructing the agent), `BehaviorMachine`
with real `patrol<->chase` transitions gated on `sees`/`lost`, `can_see` with a real sight radius
(and even the closed gate as a vision occluder), and `next_step_toward` routing the guard around
walls+crate in both patrol and chase. Confirmed from the actual trace, not a glance: the guard's
behavior state genuinely changed (14 patrol frames, 6 chase frames -- not stuck in one), the gate
was closed for every frame before the crate reached the switch (opened at frame 5), and the player
exited uncaught. Five of the session's reusable primitives (`action_registry`, `pushables`,
`agent_behavior`, `vision`, `pathfinding`) composed in one run. One honest note: it used a plain
`gate_open` boolean + `all_targets_satisfied` for the single crate->switch condition rather than
wrapping it in `puzzle_state.Gate` -- which is correct, not a gap: `Gate` is for *multiple* jointly
-required conditions, and the single-condition dependency here is genuinely enforced (the gate
blocks movement and occludes vision until the crate lands). Still honestly out of the "very hard"
tier: an NPC with richer internal goals than patrol/chase/flee, and multi-crate coordination
puzzles, aren't specifically primitive-backed -- the building blocks compose toward them, but that
isn't claimed as proven.

### Prompt enrichment before the handoff: `sandbox/prompt_refiner.py` (2026-07-10)

A one-line prompt leaves the expected feature set implicit ("mario-style" without saying jump/
gravity/pipes; "procedural cave" without saying branching/uneven), and several runs this session
showed the agent under-building against it. Per the user's request -- *"we know what it needs to
meet the users expectations so we should take their prompt improve it and then hand that off"* --
`--sandbox` runs now run a **best-effort LLM pass that expands the raw prompt into a fuller,
intent-preserving build spec** (`sandbox/prompt_refiner.py`, system prompt in
`llm/prompts/prompt_refiner.md`) and hand *that* to the agent instead of the bare prompt. This is
pure semantic generation -- it improves an instruction, runs no code, touches no validator-wins
guarantee (and only ever runs on the already-disclosed sandbox path) -- the same shape as the
existing `scene_planner.md`/`repair_agent.md` LLM steps, producing enriched natural language rather
than a `SceneSpec`.

- **Best-effort, never fatal** (same posture as sandbox live narration): no key, missing `openai`
  package, API error, or empty output all degrade to the original prompt with the reason recorded
  -- refinement is an enhancement layer, not a dependency.
- **Transparent**: `metrics.json` records `original_prompt`, `refined_prompt`, `prompt_refined`,
  and `prompt_refine_note`, and the refined text streams as an `on_stage` line so a reviewer sees
  the exact handoff (what the user typed vs. what the agent was given). The refiner's system prompt
  hard-rule is "preserve the user's core intent -- expand and clarify, never replace the game or
  add contradictory mechanics"; it deliberately points at the mechanic vocabulary the sandbox has
  primitives for (moving/emerging hazards, reactive NPCs, crates, switches/locked gates, procedural
  terrain, animation) without dictating implementation or naming modules.
- **Default-on, opt-out**: `--no-refine-prompt` (CLI) / a "Refine prompt" checkbox (GUI, default
  checked) disables it; `INFINIENV_REFINER_MODEL` overrides the model (defaults to the sandbox
  model). Sandbox-only -- the non-sandbox path already has the schema-aware `ScenePlannerAgent`.
  Wired in `sandbox/runner.py::_run_async` just before the agent message is built.

Live-verified: the deliberately terse `"a ninja platformer"` (3 words) was expanded into a
three-paragraph spec that preserved the genre and added concrete win/lose conditions (collect
three scrolls, reach the rooftop, health/damage/restart), specific mechanics the sandbox can
deliver (switch/key-gated locks, guards that patrol and chase on sight, moving platforms, timed
traps), level structure, and a pixel-art visual style -- recorded in `metrics.json` alongside the
original, and the run built and passed the outer check. The enrichment leaned naturally toward the
exact primitive vocabulary the `engine/` modules back, without being told to.

### The render must reflect the simulation: `engine/rendering.py` + principle 8 (2026-07-10)

A user-reported screenshot showed the hero floating a tile above the grass in a Mario-rescue run,
with two more asks: "we should also monitor collisions and win cases should be displayed." Reading
the actual code (`runs/gui_1783691768/sandbox_workspace/run_scene.py`) found three related root
causes, all the same underlying failure -- *the render not faithfully reflecting the simulation*:

1. **Physics ground line ≠ drawn ground line**: physics clamped the hero to `GROUND_Y = 8.0` (tile
   units) but the grass was drawn from pixel row `9*TILE` -- a full tile of mismatch between the
   number the sim uses and the number the render uses.
2. **Center-anchored sprite**: the hero was pasted centered at its position (`top-left = center -
   size/2` on both axes), so a standing sprite's feet land half its height below its center --
   floating it above the ground even ignoring (1).
3. **No outcome or collision shown**: `rescued`/`lost` and plant contact were computed but
   `draw_frame` only drew the title -- no win/lose banner, no health/hit HUD; a viewer couldn't
   see collisions register or whether the run was won.

Fix, per this session's discipline (generic reusable code + principle + self-review, never a
per-prompt worked example): new `engine/rendering.py` with `feet_anchor(center_x, ground_y_px,
size)` / `feet_anchor_rect(...)` -- the paste top-left so a sprite's *bottom* edge sits on the
ground line, fixing cause (2) correct-by-construction (center-anchoring floats a standing sprite
by half its height). New **principle 8** ("the render must legibly reflect the simulation, not
float free of it"): a grounded entity drawn feet-on-ground using the *same* shared ground constant
the physics clamps to; the win/lose outcome visibly rendered; collision/health state shown (HUD,
hit-flash). A requirements bullet and both self-review passes gained matching checks (physics-
ground == draw-ground and feet-on-line programmatically; feet-touch-ground, banner-present,
collision-visible visually).

**Live-verified on the exact reported prompt** (`--no-refine-prompt`, to match the reported run's
conditions): all three fixed, confirmed from the code and the render, not the agent's summary.
`GROUND_Y = 288` is now a *single pixel constant* used by both `integrate_grounded_2d(..., ground_y
=GROUND_Y)` and the drawn grass (`d.rectangle([0, GROUND_Y, ...])`), and the hero is drawn with its
feet at that line -- the render shows the hero standing firmly on the grass, no float. A "Health: N"
HUD and a "Plant bites flash yellow" indicator make collisions legible (the hero visibly ended at
Health 1 after two hits), and a "RESCUED! YOU WIN" banner displays the outcome. The agent also
added a trace invariant `assert hero.y <= GROUND_Y + 1` unprompted. A second run with `--assets
local` (seed 7) confirmed the *pasted-sprite* path too: the hero is a real pasted placeholder
sprite, pasted at `cy = y - PLAYER_SIZE/2` so its bottom lands exactly on `GROUND_Y` (the same
constant physics clamps to) -- an early frame shows it standing feet-on-grass, with a "Health: N /
Plants threatened: 3/3" HUD and the "RESCUED! YOU WIN" banner. **Honest finding**: neither run
actually *imported* `engine/rendering.py`'s `feet_anchor` -- both hand-rolled the correct
feet-anchor from principle 8's guidance (shift the paste center up by half the sprite so the bottom
sits on the shared ground line). So the fix is real and verified in both asset modes, but it's the
*principle* doing the work here, not the helper; the helper remains available for when an agent
reaches for it, and its pointer wasn't escalated because the outcome was already correct (a future
run that floats a center-anchored sprite would justify escalating it -- none did).

### Harness-enforced no-teleport floor + climb-gating + assets-resolved-but-not-pasted (2026-07-10)

A user-reported GUI run (`runs/gui_1783698976`, `--assets auto`) had the character teleport, climb
where there were no ladders, and use no real art despite auto mode -- read the actual code, three
root causes: (1) the controller assigned the hero's position straight to a waypoint (`st.x=tx`;
`st.x=st.x-100` on a fall) -- a teleport, not movement; (2) it computed `on_ladder` but gated the
climb on "target within 70px of any ladder" instead, so it climbed in open air; (3) `resolve_assets`
ran and the run's `asset_cache/` held 11 genuinely nice generated sprites, but `draw_frame` drew
flat primitives and pasted none of them (the "resolve then ignore" bug, confirmed by the user: "it
has an asset cache of what it could have used, really nice assets, but it didn't"). The run imported
none of the reusable primitives and wrote a self-check (`assert won and lives>0 and key`) that
caught none of this.

Since prompt guidance alone has repeatedly failed to stop teleports, the user chose a
**harness-enforced floor**: `outer_sanity_check` now best-effort-parses `replay.json`, extracts the
main entity's position series, and fails a run with an egregious single-frame jump (scale-free,
`> 6x` the p90 step) -- so a teleporting run fails the outer check and enters the repair loop, the
one correctness floor the agent can't skip with a weak self-check (`_positions_from_replay` /
`_teleport_frame` in `sandbox/workspace.py`, both unit-tested; unparseable traces skip the floor,
never false-fail). The climb-gating and asset-usage fixes stay prompt/self-review-side (the user
chose "require + self-review" for assets): principle 3 now spells out that a climb must be gated on
the character being ON the structure (use `climb_step`, which raises off-structure), not on
proximity to a waypoint; principle 5 names the teleport anti-pattern explicitly; the assets bullet
now names the resolve-then-ignore bug and requires pasting the cached sprites; and self-review step
1 was reworked from a loose "e.g." list into a **required checklist** (no-teleport, gated-change,
no-wall-crossing, hazard-mattered, NPC-state-changed, feet-on-ground, assets-actually-pasted) so a
compliant agent's own repair catches these first.

**Honest framing**: the teleport floor is a heuristic, not a guarantee -- it can in principle miss a
degenerate all-teleport trace or a trace whose shape it can't parse, and it slightly widens the
outer check's role (documented in the outer-check paragraph and the scope note). It's the strongest
lever available without reintroducing the fixed-vocabulary constraint this mode exists to escape.
Climb-gating and asset-usage remain agent-discretionary (the agent can still write arbitrary code);
this raises the floor and the self-review bar, it doesn't make them impossible -- reported as-is.
**Live-verified (`runs/cave_verify`, a procedural-cave prompt):** the agent drove the hero entirely
through `ActionSpace` actions with real gravity + a jump arc + a `terrain_y` ground clamp (no
waypoint interpolation -- the only `route` list in the file is used to *draw* the ledge, not to move
the hero), and its self-check asserted the step cap (<=26), that every trace action was in the
declared action set, that a hazard actually hit the hero (health 3->2 from a triggered falling
rock), and that the reactive creature changed state -- confirmed from the synced code and the
extracted frames, not the agent's summary.

### A second agent runtime: the Claude Agent SDK backend (2026-07-10)

The user has an Anthropic key and asked to run the sandbox with Anthropic's **Claude Agent SDK**
(`claude-agent-sdk` -- Claude Code packaged as a library, distinct from the plain `anthropic`
Messages-API SDK). This adds a *second, interchangeable agent runtime* for sandbox mode, selected
by `INFINIENV_SANDBOX_BACKEND` (default `openai`, or `claude`) -- an env var, not a new flag, the
same precedent as `INFINIENV_SPRITE_BACKEND` (§9), so `--sandbox`'s meaning stays stable regardless
of which agent produces the artifacts. Both backends are genuinely interchangeable: the same
isolated `sandbox_workspace/` copy of the engine, the same five artifacts, the same
`outer_sanity_check` (including the teleport floor above), the same self-repair loop against it, the
same prompt enrichment, and the same `metrics.json` shape -- only `provider`/`model` differ
(`openai_agents_sandbox` vs `claude_agent_sandbox`). `sandbox/claude_runner.py` reuses
`sandbox/runner.py`'s shared helpers (`_interpreter_briefing`, `_repair_message`, prompt refiner,
`build_workspace_dir`, `outer_sanity_check`) rather than forking the pipeline; `run_sandbox_generation`
dispatches on the env var.

- **The two SDKs have structurally different execution models**, which is the whole reason this is a
  second backend and not a drop-in model swap. The OpenAI Agents SDK copies the workspace into a
  *separate ephemeral filesystem* (`hydrate_workspace`), runs the agent's shell/file tools there under
  a macOS Seatbelt profile, then syncs that filesystem back onto disk (`sync_full_workspace`) and
  extracts the artifacts from it -- the isolation boundary is a real, OS-enforced separate FS. The
  Claude Agent SDK (Claude Code as a library) instead runs its built-in Read/Write/Edit/Bash tools
  *directly on a working directory* (`cwd`). So here `cwd` **is** `sandbox_workspace/` on disk: the
  agent edits it in place, there is no tar hydrate/sync round trip, and "extract artifacts" is a plain
  copy of the five files out of that directory (`_copy_artifacts_from_dir`). Isolation is by
  working-directory convention on a throwaway copied workspace (built from the *installed* package,
  never this repo's real source), configured with `permission_mode="bypassPermissions"` (autonomous:
  no human to approve each tool call) and `setting_sources=[]` (so Claude Code does **not** walk up
  and load this repo's own `CLAUDE.md`/settings, which would confuse the sandbox agent with
  instructions meant for the outer project). The sandbox_agent.md prompt is appended to Claude Code's
  own `claude_code` system-prompt preset (not replacing it), keeping the built-in file/bash competence
  plus this task's rules.
- **Honest disclosure of the isolation difference.** The Claude backend does **not** apply the OpenAI
  backend's Seatbelt confinement -- isolation is by `cwd` on a throwaway copied workspace, weaker than
  a separate OS-enforced filesystem. This is consistent with §11's standing posture (sandbox mode is a
  *disclosed* trade-off, not a hidden one), and the one guarantee both backends keep is the load-bearing
  one: **the outer (trusted) process still never imports or executes the agent-written `.py` files** --
  it only ever reads back the five named artifact files, exactly as the OpenAI backend does. The
  Claude Agent SDK does expose its own OS-level `SandboxSettings`; wiring that in to recover
  Seatbelt-grade confinement is a reasonable follow-up, deliberately left out of this first cut to keep
  it a minimal, working "try it" -- flagged here rather than overclaimed.
- **Auth -- login-first, `ANTHROPIC_API_KEY` deliberately left unset.** The Claude Agent SDK spawns
  the `claude` CLI, whose credential order is `ANTHROPIC_API_KEY` -> `ANTHROPIC_AUTH_TOKEN` -> the
  stored `claude.ai` login. An *earlier* version of this backend promoted `CL_KEY` (InfiniEnv's own
  name for the Anthropic key) to `ANTHROPIC_API_KEY` in `cli._load_dotenv`. That turned out to be
  actively harmful: setting `ANTHROPIC_API_KEY` forces the CLI onto the API-key account **in
  preference to the working claude.ai login** -- confirmed live by the CLI's own warning
  ("connectors are disabled because ANTHROPIC_API_KEY ... takes precedence over your claude.ai
  login") -- and when that API account was out of credit (a hard 400 "credit balance is too low"),
  every Claude run failed even though the login itself was fine. So the mapping was **removed** at
  the user's direction: `cli._load_dotenv` and `claude_runner` no longer set `ANTHROPIC_API_KEY` at
  all, and the backend does not require a key -- it lets the CLI use the login, and if there's no
  auth at all the SDK surfaces that as a normal `run_error`. `CL_KEY` stays under its own name for
  code that explicitly wants the raw key (the `anthropic` provider reads `CL_KEY` -> then a
  user-set `ANTHROPIC_API_KEY`, and passes it *directly* to `anthropic.Anthropic(api_key=...)` --
  never via the global env var, so it can't clobber the CLI's login). The default model for this
  backend is `claude-sonnet-5` (`DEFAULT_SANDBOX_CLAUDE_MODEL` --
  Sonnet, not Opus, chosen deliberately: sandbox runs are long and iterative, doing many
  build-and-rerun cycles, so the cheaper Sonnet tier is the sensible default and stays plenty
  capable at driving the closed-action-space discipline; use Opus via the override for the hardest
  prompts),
  overridable via `INFINIENV_SANDBOX_MODEL`, distinct from the OpenAI backend's `gpt-5.5` default.
- **Live narration** works the same as the OpenAI backend: `_describe_claude_message` maps the SDK's
  streamed `AssistantMessage`/`ResultMessage` blocks to the same `on_stage` lines (`$ <command>` for a
  Bash tool call, `Editing: <path>` for Write/Edit, `Agent:`/`Thinking:` for text, a failure line for
  an errored tool result; Read/Glob/Grep stay silent) -- duck-typed against the block shapes, wrapped
  so a future SDK shape change degrades to silence rather than crashing, same best-effort discipline as
  the OpenAI `_describe_stream_event`. So the CLI and GUI show live progress identically for both
  backends with no new plumbing.
- **Dependency.** `pip install infinienv[claude]` (`claude-agent-sdk`); the `claude` CLI must be on
  PATH (the SDK spawns it). Lazy-imported inside `claude_runner._run_async`, following the project's
  optional-dependency pattern -- a missing package becomes a `ProviderError` naming the install command,
  and the default `openai` backend is entirely unaffected.

**Live-verification (2026-07-10, honest partial result).** Exercised end to end against the real
Claude Agent SDK + `claude` CLI on a maze/patrolling-enemy prompt (`--assets none`,
`--no-refine-prompt`). *Confirmed working from the run's own output, not the agent's self-report:*
CL_KEY authentication (the CLI's own precedence warning -- "connectors are disabled because
ANTHROPIC_API_KEY ... takes precedence over your claude.ai login" -- confirms the key, not a stored
subscription login, is in use); workspace prep; the agent genuinely reading the copied engine
primitives (`grid_collision`/`motion_patterns`/`animation`/`pathfinding`/`vision`) and iterating on
`run_scene.py` via the absolute venv interpreter (the interpreter briefing worked -- it never hunted
for a different python); live-narration parity; and -- validated *by* this run -- the graceful
mid-run failure path: when the Anthropic account's API credit was exhausted partway through, the SDK
error was caught as `run_error`, the repair loop tried all three attempts against it, and
`metrics.json` recorded an honest failure (`success:false`, `source`/`provider`/`outer_sanity_error`
populated) instead of crashing -- exactly the designed behavior. *Not yet confirmed:* a **successful**
run emitting all five artifacts, because the CL_KEY account ran out of credit (a 400 "credit balance
is too low", reproduced on a trivial one-token call) -- an account/billing state no code change can
fix. A full sandbox run makes many API calls over many minutes, so it needs a funded account; once
topped up, re-run `INFINIENV_SANDBOX_BACKEND=claude` to confirm a green end-to-end. The default
`openai` backend is unaffected. Reported as found rather than overclaimed, per this project's
standing verification discipline.

### `SpriteBook`: make "every generated sprite got used" a one-line assertion (2026-07-10)

A user compared two sandbox runs of the same Mario-rescue prompt. The earlier one
(`runs/gui_1783691768`) looked great: its agent-authored `run_scene.py` pasted *every* element
(hero, tower, princess, plants, coins, walls) from the generated art via one clean
`paste_sprite(key, cx, cy, size)` loop at consistent tile-aligned sizes (`TILE`, `TILE*3`). The
later one (`runs/gui_1783714443`) looked much worse despite all 15 sprites generating fine into
`sandbox_workspace/asset_cache/` -- diagnosed by reading both files' actual code, not the agents'
summaries. The later `draw_frame`: (1) asked for key `'hero'` when the player's resolved key is
`'agent'`, so the hero silently fell back to a primitive; (2) drew ~10 types as primitives anyway
(coins, pipes, tower, gate, fireball, bricks) despite each having a generated sprite -- the
"resolve then ignore" bug; (3) rescaled sprites to arbitrary per-entity pixel sizes (54/42/44...)
on a large canvas, so they read small and inconsistent (the user's "it scales generated sprites"
complaint). `sandbox_agent.md` *already* warned about resolve-then-ignore and shipped a
`paste_sprite` example, and the later run still reproduced it -- prompt-only guidance is
insufficient, the same lesson this section keeps relearning.

**Scope correction from the user:** the HUD (health/lives/score + win/lose outcome, principle 8)
**stays** -- the user explicitly wants it (my first read of "doesn't show health+score" as *praise*
of the clean run was wrong; the clean run's advantage is its *art*, not the missing HUD). So
principle 8 is untouched; this change is only about actually using the generated sprites at a
sensible scale.

The durable fix follows this section's standing discipline (a reusable, tested primitive + a
self-review invariant, never a per-case worked example): `engine/rendering.py` gained a small
`SpriteBook(asset_paths)` class -- `paste(img, key, cx, cy, size, anchor="center"|"feet")` (cached
load/resize/alpha-paste; records the key as used; returns `False` so the per-key primitive fallback
still runs) and `unused_keys()` (resolved keys the draw loop never pasted). That turns "every
generated sprite actually got drawn" into a one-line `assert not book.unused_keys()` that catches
**both** failure modes at once -- an ignored sprite is unused, and a mismatched key leaves the real
key (`'agent'`) unused. `sandbox_agent.md` now points at `SpriteBook` in the reusable-building-blocks
list and the asset-usage section (with the assertion and a "paste at consistent tile-tied sizes, not
arbitrary per-entity sizes" clause), and self-review step 1's asset bullet was upgraded from a soft
"confirm asset_cache isn't full of ignored sprites" to the concrete `assert not unused_keys()`.
`engine/rendering.py` is already copied into every workspace, so no workspace-builder change was
needed; the reference `run_scene.py` template (which delegates to the real renderer and has no
hand-rolled draw loop, so it was never the bug site) just gained a comment pointing *rewrites* at
`SpriteBook`. Honest scope: `unused_keys()` is agent-discretionary (the agent must actually build
and assert it), not a harness floor -- detecting "primitives instead of sprites" from `render.png`
isn't reliable, so this stays self-review-enforced, same posture as the climb-gating/asset fixes
above. Obstacle-avoidance quality (the third complaint) is covered by existing principle 4 + the
hazard-threat self-review invariant, not a new mechanism. [Live-verification result appended after
the verification run.]

### Movement must be physics-verified, not a smooth scripted route (2026-07-10)

A follow-on user report on a "CLIMB TOWER" platformer run (`runs/gui_1783727953`) -- "it doesn't
have floors, the plants are static moving upside down when you KNOW they should have functionality."
This run *did* adopt the `SpriteBook`/animation changes above (sprites used well), but reading its
actual `run_scene.py` showed the "animation, not simulation" failure §11 documents, in platformer
form: `build_trace()` moves the hero along a **hardcoded waypoint `route`** via linear `interp`
(`hero.x, hero.y = interp(sx, target, t)`) -- **no gravity, no platform collision**; `scene.walls`
(the drawn platforms) are decorative and never collided with, so the hero glides between scripted
points through open air. It even caps and asserts step size, so the teleport floor passes -- but
nothing ever checks the hero is *supported*. The plants translate the whole sprite up by
`oscillate(...)*42` (a mid-air bob) instead of emerging from their pipe, and never use `pulse_cycle`.

The user's sharpening: **"all movements need to be verified possible by the physics environment."**
That's stronger than "a floor is under the hero" -- a *smooth* precomputed route clears every
existing check (no teleport, bounded step) yet is exactly as wrong as a teleport, because the
positions were assigned from a pre-decided path, not produced by physics; no floor/wall/structure
was ever consulted. Per the user's choice (self-review, not a harness floor -- a "grounded"
outer-check heuristic is too coupled to each run's coordinate system and would false-fail legit
jumps/falls), the fix is principle-level, following this section's standing discipline (reusable
primitives already exist -- `integrate_grounded_2d`, `move_with_collision`, `climb_step`,
`pulse_cycle` -- the gap was guidance + a mechanical invariant, never a per-case example):

- **Principle 5** now names the *smooth scripted route* explicitly as the same defect as a teleport
  wearing a smooth costume, and states the real rule: every movement must be one the physics
  permits, guaranteed by making the physics functions the *only* thing that moves an entity (drive
  locomotion through `integrate_grounded_2d`/`move_with_collision`/`climb_step` so it's valid by
  construction, with no separate route to drift out of sync with the drawn floors).
- **Self-review step 1** gained a comprehensive invariant: walk the trace and assert each
  consecutive position pair is a legal physics transition (supported by a floor/platform, or falling
  under gravity, or on a ladder within its span, or inside a declared jump arc -- never hovering over
  a gap, never crossing a wall). A waypoint-interpolated hero fails this; it's the check that
  exposes a route the physics never produced.
- **Principle 3** (emerging hazards) now says an emerge must *reveal/grow from its base* (out of the
  pipe/hole), `pulse_cycle`-driven with `active` tied to how far it's out -- not translate the whole
  sprite up through open air (which reads as a detached floating shape, the "static/upside-down"
  complaint).

Honest scope: this is self-review-enforced (agent-discretionary), not a harness guarantee -- the
outer check still can't distinguish a stepped simulation from a convincing animation (§11's standing
blind spot). It raises the bar and points every lever at the right primitives; it doesn't make a
scripted route impossible.

Follow-up on the very next run (`runs/gui_1783730799`, a Donkey-Kong-style 5-floor climb): the
movement/logic was genuinely improved (gated `walk`/`climb` actions, real `Gate`-based rescue,
bounded steps -- the physics guidance landed), and the user confirmed "it actually worked." The
remaining complaint was pure watchability: "im blind but its soo quick" -- the whole 5-floor climb
compressed into a ~5-second, 50-frame GIF (the sim wins in ~168 steps, sampled every 4th at
`duration=100`). A correct run that blurs past in a few seconds reads as "nothing happened, it just
says you won." Fixed with a `replay.gif` **watchability requirement** in `sandbox_agent.md` (a hard
artifact-requirement bullet + a self-review step-2 check): aim for ~8-20 s total, ~70-110 ms/frame,
don't over-subsample (one gif frame per 1-3 sim steps), and hold the final win/lose frame ~1.5-2 s
so the outcome is readable; if the sim ends in very few steps, slow the motion rather than ship a
blur. The base renderer's `save_replay_gif` already defaults to a watchable 220 ms/frame, so this is
scoped to the sandbox custom draw loops that subsample hard -- no base-renderer change.
**Live-verified (`runs/cave_verify`):** the run's `replay.gif` came back at 194 frames / ~17.5 s at
90 ms/frame with the final win frame held ~1.6 s -- comfortably watchable, not the 5-second blur that
prompted this.

### A ladder must be drawn as one contiguous floor-to-floor span (2026-07-10)

User report on `runs/gui_1783731619` (a ladder-tower rescue): "why are the ladders separated, this
cannot happen." Diagnosed from the actual code: the *data* was correct -- `FLOORS`/`LADDERS` had each
ladder spanning exactly one floor-to-floor gap and the `climb` action used the full span -- but the
*render* (`for y in range(upper+1, lower): if y % 2 == 0: draw ladder`) trimmed off both floor cells
and skipped every other remaining cell, so a continuous climbable ladder was drawn as sparse rungs
floating in the gap, touching neither floor. Pure render fidelity, same class as the `feet_anchor`/
`SpriteBook` fixes (an agent hand-rolls structure rendering and a `+1`/`% 2` flourish breaks it).

Fix per the standing discipline (reusable primitive + self-review invariant, never a per-case
example): `SpriteBook.paste_column(img, key, cx, y_top, y_bottom, tile)` (`engine/rendering.py`)
tiles a sprite contiguously across the whole inclusive vertical span so a ladder/pipe/column meets
both endpoints by construction -- no `range(top+1, bottom)` trim or `% 2` gap possible. Principle 8
gained a "structure drawn as a continuous connecting span" clause pointing at it; the reusable-blocks
list mentions it; and self-review step 1 gained an invariant (a ladder's drawn cell span equals its
climbable span and includes both floor rows; secondary: every ladder's endpoints lie on real floors).
Honest scope: self-review-enforced render fidelity, not a harness guarantee -- the outer check can't
inspect whether a ladder visually connects floors. `paste_column` is unit-tested in
`test_rendering.py`. **Live-verified (`runs/cave_verify`):** the cave's "ROCK HOLDS" climbable
structure rendered as one solid contiguous vertical column meeting both ledges -- no separated/
dashed segments, the exact failure this fix targets. (The run didn't call `paste_column` by name --
it drew the full contiguous span itself -- so, as with `feet_anchor`, the *principle* did the work
and the helper stays available; pointer not escalated since the outcome was already correct.)

### Don't cheese "procedural": seeded side-view generators + an anti-cheese principle (2026-07-10)

A user pointed at `runs/gui_1783735401` (a procedurally-generated *open-world* cave whose viewbox
follows the player): "it needs to have the capability to do that and many other things in the
similar realm, it cant be cheesing the prompt." Reading the synced code gave a split verdict: the
**open-world camera is genuinely real** (world `W=70` tiles / 2240px vs an 800px view; `camera_x`
smoothly follows the hero, clamped to world bounds, with a world->screen transform), but
**"procedurally generated" was cheesed** -- the entire level is hardcoded (`platforms = [(1,21,18),
(7,13,19), ...]` plus fixed ladders/gems/hazards), and the only `random.Random(42)` in the file
draws *background ambience dots*: a decorative RNG with a fixed seed used as camouflage so the run
looks generative while nothing structural depends on the seed. (`runs/cave_verify` cheesed the same
way with a hand-authored continuous `terrain_y`.)

Two root causes: (1) **capability gap** -- the only generator that existed, `generate_organic_region`,
is a *top-down* grid carver; the refiner turns these prompts into *side-view* platformers whose
levels are a continuous ground profile or discrete platforms+ladders, neither of which it produces,
so hardcoding was the path of least resistance; (2) **enforcement gap** -- principle 3 literally
permitted it ("hand-authored or generated, just verify connected") and no self-review proved the
layout varies by seed.

Fix (real primitives + a general principle + a mechanical self-review invariant, per the standing
discipline): `engine/level_generation.py` gained seeded side-view generators alongside the unchanged
top-down `generate_organic_region` -- `generate_terrain_profile` (uneven ground heightmap),
`carve_gaps` (fatal pit columns clear of the ends), `generate_platform_layout` (returns
`(platforms, ladders)` in the exact `(left,row,right)`/`(col,top,bottom)` shapes these runs use, with
every adjacent level ladder-connected *by construction*), and `scatter_on_supports` (seeded gem/hazard
placement on real supports, spaced, off pits/ends). `sandbox_agent.md` gained **principle 9**
("implement the capability, don't hardcode a fixed instance of its output -- don't cheese the
prompt"), naming the decorative-RNG-as-camouflage tell and the self-test "if you can't change the
seed and get a different-but-valid level, you hardcoded it"; principle 3's loophole was closed (when
the prompt asks for generation, hand-listing the layout is cheesing); and self-review step 1 gained a
**seed-variance invariant** (generate with two seeds, assert the results differ and each is valid) --
the check a hardcoded layout can never pass. Honest scope: this is self-review-enforced, **no harness
lever** -- detecting hardcoded-vs-generated needs comparing two seeds, and the outer process never
executes the sandboxed code (a load-bearing isolation invariant), so a harness seed-variance check
isn't possible without breaking it. New generators unit-tested in `test_level_generation.py`
(determinism, varies-by-seed, bounds, ladder-connectivity, scatter constraints).
**Live-verified (`runs/procgen_verify`, the same open-world cave prompt): the cheese is gone.** The
agent imported the new generators and built the whole level from the seed via a `build_layout(seed)`
that calls `generate_terrain_profile`/`carve_gaps`/`generate_platform_layout` -- and, unprompted by
any worked example, adopted the seed-variance self-check verbatim in spirit: `alternate =
build_layout(SEED + 1); assert (TERRAIN, PITS, PLATFORMS) != alternate[:3], "seeded cave layout did
not vary"`. The run passed that assert, so the layout genuinely varies by seed. Independently
confirmed (not the agent's word): running the generators with the agent's exact params for seed 5 vs
6 gives different terrain, gaps, and platform sets. The render shows a real generated uneven cavern
(jagged multi-level terrain, pits, a contiguous ladder, scattered gems + falling-rock/lava/spike
hazards) -- nothing like the hardcoded five-platform layout of `gui_1783735401`; the open-world
camera it already got right is preserved.

### The systemic anti-cheese layer: an independent faithfulness auditor (2026-07-10)

By this point the session had fixed ~six sandbox issues one at a time (sprite usage, physics
movement, ladder render, watchability, procedural-gen, perception), each with a per-incident triple:
a reusable primitive + a prompt principle + a self-review invariant. The user named the real problem:
this is whack-a-mole -- every fix is the *same* failure (the agent produces something that passes the
mechanical outer check and looks right, but fakes the requirement), and the prompt keeps accreting a
named bug per incident. The latest instance was a fog-of-war minecraft run (`gui_1783742552`) whose
solver navigates straight to the ground-truth `game.layout.diamond` while line-of-sight is applied
*only* in the render -- cosmetic fog, an omniscient player.

Root cause is structural: sandbox mode has no external semantic validator by design, so the only
enforcement is prompt principles the agent may ignore and **the agent grading its own work** with a
self-review it can make vacuous. The author is the judge. Deterministic semantic checking is ruled
out (it would reintroduce the fixed vocabulary this mode exists to escape), so the only thing that
can judge open-ended intent is an LLM -- and the lever is *who* judges. The user chose an
**independent auditor**.

`sandbox/auditor.py::audit_run(out_dir, refined_prompt)` runs after the outer sanity check passes,
before `success` is finalized. A fresh LLM instance (adversarial system prompt in
`llm/prompts/sandbox_auditor.md`, no shared context with the author, OpenAI Responses API directly --
so it's cross-model whenever the author is the Claude backend) reads the synced `run_scene.py` **as
text (never executed)** plus the trace and the agent's declared `rules` block, and hunts for
requirements faked rather than implemented. It returns `PASS` or `FAIL` + concrete findings; a `FAIL`
feeds the *same* repair loop an outer-check failure does (`_repair_message` gained an `audit_findings`
branch), so the author gets the specific cheat and must fix it. `success` now also requires
`audit_passed`; `metrics.json` carries `audited`/`audit_passed`/`audit_findings` beside the
`outer_sanity_*` fields, and each `repair_history` entry records the audit verdict. Both backends
share it (`runner.py` and `claude_runner.py`). Best-effort/disclosed, same posture as the prompt
refiner: no `OPENAI_API_KEY`, `INFINIENV_SANDBOX_AUDIT=0`, an API error, or unparseable output all
yield `audited=False, passed=True` (a run is never failed because the auditor couldn't run);
`INFINIENV_SANDBOX_AUDITOR_MODEL` overrides the model.

Two supporting pieces landed with it: (1) a **rules contract** -- `sandbox_agent.md` now asks the
agent to write a machine-readable `rules` block (`{requirement, enforced_by}`) into `metrics.json`,
giving the auditor a coverage target and making a dropped/vacuous rule visible; (2) the per-incident
principles are now framed under **one faithful-implementation meta-principle** ("implement the spec,
never fake it -- could you change the seed/perceived cells/physics and still be correct, or did you
hardcode the appearance?"), with the specific principles kept as concrete examples, so the prompt
stops growing a named bug per incident. The perception cheat also got its own capability +
principle: `engine/perception.py` (`KnowledgeMap` -- the solver's fog-of-war memory it plans over
instead of ground truth; `visible_cells` -- one LOS+radius rule) and **principle 10** (a closed
perception model, the read-side twin of the closed action space: the solver may only read what it
has observed). The auditor is what makes "the solver follows the author's perception rules" enforced
*generally*, not per-mechanic.

**Honest bounds, stated plainly:** the auditor is a probabilistic reviewer, not a guarantee -- it can
miss a real cheat or false-flag a clean run; the repair budget caps it. It does **not** violate §2's
default-path invariants: it's sandbox-only (which already trades away the deterministic guarantee),
and it *reads the code as text, never executes it* (the outer process still never runs sandboxed
code). It's the honest best available for open-ended sandbox mode -- it reintroduces a verifier, but
a semantic LLM one instead of the fixed-vocabulary deterministic one, and crucially it separates the
author from the grader. Hermetic tests in `test_auditor.py` (verdict parsing, payload building,
best-effort skips) and `test_sandbox_runner.py` (an audit FAIL forces a repair and is recorded; a
persistent FAIL fails the run; a skip never blocks).

**Live-verified against two already-captured runs (real OpenAI auditor), and it earned its keep
immediately.** On `runs/gui_1783742552` (the fog-of-war minecraft cheat) it returned FAIL and named
the exact perception cheat found by hand -- `run_policy()` navigates using ground-truth
`game.layout.diamond`/`coal`/`iron_cells` while `visible_cells()` is used only in `draw_frame` --
plus a second real defect the hand review had missed (the "PRESS R TO RESTART" is cosmetic; no
restart action exists). More tellingly, on `runs/procgen_verify` -- a run this file had earlier
recorded as a clean success -- it returned FAIL for a cheat the **hand review genuinely missed**: the
procedural *generation* was real (varies by seed, which was verified), but the generated upper
platforms/ladders are cosmetic backdrop -- `advance()` only collides with the lower terrain profile,
the "optional" upper gem is unreachable and absent from collection logic, so the prompt's "multiple
possible paths / route choice" was faked. An independent reviewer caught what a careful human reviewer
(deliberately reading the synced code, not the summary) overlooked -- which is exactly the value the
mode is for. The earlier `procgen_verify` "clean success" note stands corrected: its *generation* was
honest, its *navigable multiple paths* were not. [End-to-end result -- auditor forcing a repair
inside a fresh run -- appended after that run.]

### Sandbox is the only `generate` mode, and it runs the deterministic validator (2026-07-11)

At the user's explicit direction, sandbox is the **only** `generate` mode. `cmd_generate` always calls
the sandbox runner; the `--no-sandbox`/`--provider`/`--no-fallback` flags are gone (`--sandbox` stays
as an accepted no-op), and the GUI form dropped its sandbox toggle + provider fields (always sandbox).
The deterministic engine is **not** deleted — the sandbox is built on it (copies
`schema/engine/navigation/validation/render/assets`), and the `validate`/`solve`/`mutate`/`curriculum`/
`benchmark`/`export-dataset` tools still run the fixed-vocabulary validator + solver over any scene
(that's the retained code-defined-truth / programmatic-reward machinery; `app.py` also keeps a
non-form non-sandbox API route so the hermetic mock test still works).

Paired with the mode change, the user's "sandbox should not trade away the validator checks" is now
real: `outer_sanity_check` runs `validate_scene` on the sandbox scene.json and **enforces** the
vocabulary-agnostic geometry codes (`_ENFORCED_VALIDATION_CODES = {OUT_OF_BOUNDS, DUPLICATE_ID}` —
genuine bugs regardless of mechanics; failing them feeds the repair loop), while
`deterministic_validation_summary` records the full validator verdict (valid + all error codes +
which were enforced) in `metrics.json`. The vocabulary-*specific* checks (`UNSUPPORTED_OBJECT_TYPE`,
`MECHANICS_*`, `UNREACHABLE_OBJECT`, `NO_GOALS`, `ILLEGAL_OVERLAP`) and fixed-vocabulary `UNSOLVABLE`
are recorded, **not** enforced — a sandbox scene legitimately escapes the fixed vocabulary (custom
types, code win-conditions, custom movement), and requiring them would false-fail legitimate runs.
Fixed-vocabulary solvability is the one guarantee that genuinely can't transfer to agent-authored
gameplay; the outer image checks + the faithfulness audit + the agent's own trace invariants stand in
for it. No-key consequence: sandbox needs an OpenAI key, so `README.md`'s no-key path is now
`solve examples/*.json` (the deterministic tools, offline) plus a committed `examples/example_world/`
run the GUI surfaces in its gallery. Honest note for the GI submission: the challenge's headline claim
is that code-defined objectives beat a VLM checking pixels, and a sandbox-only `generate` softens that
(its reliability leans on the agent's own code + geometry validation + the LLM auditor, not a full
solvability guarantee) — which is why the deterministic `solve`/`mutate`/`export-dataset` machinery is
kept first-class for the reward-data use case.

### Explicitly out of scope for this mode (for now)

The `docker`-backed sandbox client (Unix-local was the pragmatic
first choice); having the outer layer verify sandbox-authored mechanics beyond basic
well-formedness (not achievable without reintroducing the fixed-vocabulary constraint this mode
exists to escape — the repair loop above strengthens *that* check's pass rate, it doesn't add a
new kind of check); reusing sandbox-authored mechanics across runs (no cache/reuse mechanism like
`generation/mechanics_cache.py` for this mode — every run starts from the same clean workspace
copy); folding sandbox mode into the *non-sandbox* repair loop in `generation/compiler.py` (they
remain two separate code paths with two different kinds of guarantee, even though each now has its
own repair loop internally).

---

## 12. CLI reference

```bash
# `generate` is sandbox-only (section 11): a model writes and runs its own engine code in an
# isolated per-run workspace copy. Needs an OpenAI key + `pip install -e ".[openai]"`.
python -m infinienv generate --prompt "..." --seed 42 --out runs/demo [--max-repair-attempts N] \
  [--assets {none,local,generated,auto}] [--no-refine-prompt]

# No-key path: the deterministic tools still run offline over any scene (validate/solve/mutate/...):
python -m infinienv solve examples/kitchen_can.json --out runs/demo

  # --- generate (sandbox only): model-authored engine code in an isolated per-run copy.
  # ignores --provider/--no-fallback (no LLM-repair-agent path or fallback-template path to
  # apply them to); --assets applies the same as any other run, resolved inside the sandbox
  # workspace via a copy of assets/resolver.py; --max-repair-attempts here means repair
  # attempts against the outer sanity check (default 2), not the LLM repair agent; trades
  # away the validator-guaranteed solvability check every other run has -- see metrics.json's
  # outer_sanity_* fields. By default a best-effort LLM step first expands the raw prompt into a
  # fuller build spec handed to the agent (--no-refine-prompt disables it; both the original and
  # refined prompt are recorded in metrics.json). See section 11's prompt-enrichment subsection.
  # INFINIENV_SANDBOX_BACKEND selects the agent runtime: openai (default, OpenAI Agents SDK) or
  # claude (Anthropic's Claude Agent SDK -- authenticates via the `claude` CLI's own claude.ai
  # login; ANTHROPIC_API_KEY is deliberately NOT set from CL_KEY, see section 11's auth note. Needs
  # the `claude` CLI on PATH and `pip install infinienv[claude]`; default model claude-sonnet-5).
  # Interchangeable:
  # same workspace, artifacts, outer check, and metrics.json shape -- only `provider`/`model` differ.
  # See section 11's Claude Agent SDK backend subsection.
  # After the outer check passes, an independent faithfulness auditor (a separate LLM instance)
  # reviews the run for cheese and can force a repair; INFINIENV_SANDBOX_AUDIT=0 disables it,
  # INFINIENV_SANDBOX_AUDITOR_MODEL overrides its model. Best-effort (no OpenAI key -> skipped,
  # audited=false); records audited/audit_passed/audit_findings in metrics.json. See section 11's
  # faithfulness-auditor subsection.

python -m infinienv validate runs/demo/scene.json
python -m infinienv solve runs/demo/scene.json [--out runs/demo]
python -m infinienv play runs/demo/scene.json          # interactive terminal play; also
                                                          # accepts a scene's custom trigger_actions
python -m infinienv benchmark examples/prompts.txt --provider mock --out runs/benchmark

python -m infinienv mutate runs/demo/scene.json --count 10 --out runs/mutations \
  [--provider openai_agents --llm-fraction 0.5]

python -m infinienv curriculum --theme warehouse --levels 5 --out examples/curriculum_warehouse.txt
python -m infinienv curriculum --theme warehouse --levels 5 --run --provider mock --seed 1 --out runs/curriculum

python -m infinienv export-dataset runs/curriculum --out runs/curriculum/dataset.jsonl

python -m infinienv gui [--host 127.0.0.1] [--port 5050] [--no-browser]  # local web GUI
```

Every `generate` writes stage-by-stage progress to stdout (`[n/total] ...`) ending in a clear
`Result: SUCCESS`/`FAILED (see report.md)`. Design all CLI output for a reviewer skimming a
terminal, not just for a human who already knows what happened. `main()` calls
`sys.stdout.reconfigure(line_buffering=True)` once at startup — Python only line-buffers stdout for
an interactive terminal by default, so a long `generate`/`--sandbox` run whose output is redirected
to a file/pipe (the normal way to kick one off in the background) would otherwise show nothing
until the process exits, even though real progress is happening — a real, user-reported "is it
stuck?" moment. `gui` is a thin Flask frontend on
the exact same `run_generation` pipeline, streaming that same stage-by-stage progress live over
SSE instead of stdout — see `gui/app.py`. It requires `pip install infinienv[gui]`; nothing else
in the project depends on `flask`. The GUI also has a `--sandbox` toggle that calls
`sandbox/runner.py::run_sandbox_generation` the same way the CLI does (not a second
implementation) — checking it disables the provider/`--no-fallback` fields (ignored by sandbox
mode, same as the CLI); the Assets field stays enabled and applies to sandbox runs exactly as it
does to non-sandbox ones, same as the CLI's `--assets`. When `--sandbox` is checked, the GUI also
reveals an **Agent runtime** selector (OpenAI Agents SDK / Claude Agent SDK) -- the frontend
equivalent of `INFINIENV_SANDBOX_BACKEND` (section 11's Claude Agent SDK backend). It POSTs
`sandbox_backend`, which `run_sandbox_generation`'s `backend` parameter accepts as a per-run
override (falling back to the env var, then `openai`, when the frontend doesn't send one) -- so the
choice is a GUI control, not a second code path, same discipline as every other sandbox field.
Alongside it is an **Agent model** picker whose options track the selected runtime -- OpenAI
(`gpt-5.6-terra`/`sol`/`luna`, `gpt-5.5`/`-pro`) or Claude (`claude-sonnet-5`/`-opus-4-8`/`-fable-5`),
the account's actually-available variants. It POSTs `sandbox_model`, validated against
`gui/app.py::SANDBOX_MODELS` for the chosen backend (an unlisted/mismatched model is a 400, not
forwarded) and threaded to `run_sandbox_generation`'s existing `model` parameter -- the frontend
equivalent of `INFINIENV_SANDBOX_MODEL`, a per-run override of the backend's default. First option is
the default (`gpt-5.6-terra` / `claude-sonnet-5`); absent -> the env/default fallback, unchanged.
Streams the same per-attempt `on_stage`
progress messages
(`sandbox/runner.py`'s repair loop now takes an `on_stage` callback mirroring
`evaluation/runner.py`'s), and renders results distinctly: both verdicts side by side
(`sandbox_self_reported_success`/`outer_sanity_passed`), the agent's own summary text, and a
"sandbox" badge on both the live result banner and that run's entry in the recent-runs gallery —
so a reviewer browsing past runs in the GUI can never mistake a sandbox result for a
validator-guaranteed one, the same requirement the CLI output already met.

Those live messages are **not shown as a raw scrolling log** — they render as a **structured
activity view**. `gui/app.py::_classify_stage(msg)` tags each `on_stage` line with a `kind` (keyed
off the stable narration prefixes: `command`/`edit`/`decision`/`agent`/`audit`/`attempt`/`refine`/
`workspace`/`image`/`error`, else `status`) carried on the SSE `stage` event; the frontend renders
each as an icon+chip row, color-coded, with **decisions and the audit verdict emphasized** and the
feed **segmented by attempt**. A sticky phase header tracks Refine → Build → Audit → Done with an
attempt counter and elapsed timer. On completion it shows **verdict cards** (Result / Outer sanity /
Faithfulness audit — Passed·Cheat-found·Not-verified from `audited`/`audit_passed`/`audit_note` /
Repair attempts) and an **Assets used** panel: a thumbnail grid of the sprites the run actually
resolved (`gui/app.py::_sandbox_assets_summary` lists `sandbox_workspace/asset_cache/*.png`, served
via the `/artifact` route, plus any `asset_notes` like a rate-limit fallback), included in the
`done` payload as `assets`. render.png/replay.gif and collapsibles (refined prompt, agent summary,
scene.json, all metrics, raw log) sit below. The raw log stays available as a collapsible for power
users; the classifier degrades any unrecognized line to `status`, so it never breaks a run. This
directly answers a user ask — surface the decisions, code writes, commands, assets, and audit as a
readable, pretty view, not one text blob.

Artifacts written per successful `generate` run:

```text
runs/<run_id>/
├── scene.json            # structured SceneSpec ground truth
├── validation.json       # validator checks + full repair_history
├── metrics.json          # solvability, path length, success, timings
├── replay.json           # action trace + per-goal completion (goal_results)
├── render.png             # static top-down visualization
├── replay.gif              # animated replay of the agent solving the task
├── report.md                # human-readable run summary
├── asset_plan.json          # (only if --assets != none) requested sprite types
└── asset_manifest.json      # (only if --assets != none) resolved sprite source per type
```

---

## 13. Evaluation and metrics

`metrics.json` (via `evaluation/metrics.py::compute_metrics`):

```json
{
  "success": true, "provider": "openai_agents", "seed": 42, "repair_attempts": 1,
  "used_fallback": false, "validation_passed": true, "solver_success": true,
  "path_length": 34, "num_objects": 8, "num_walls": 44, "num_goals": 1,
  "generation_time_seconds": 2.41, "solve_time_seconds": 0.02
}
```

`success` is only `true` if both `validation_passed` and `solver_success` are true — never claim
success otherwise, in `metrics.json` or in `report.md`.

---

## 14. Coding standards

Python 3.11+.

Prefer: pydantic models for structured specs, type hints everywhere, clear module boundaries
(section 3's package layout), deterministic seeds, small pure functions, explicit exceptions for
invalid state (`ActionError`, `PlanError`, `ProviderError`, `GenerationFailedError`), pytest,
lazy imports for optional/heavy dependencies (every provider module, `assets/generator_openai.py`,
`gui/app.py`'s `flask` import) so `mock`-only, no-GUI usage never needs them installed.

Avoid: hidden global state, nondeterministic tests, giant files, broad `except Exception` without
a clear reason, model-authored code execution (section 2), adding a mechanic/effect op without a
validator check and a test, dead code (delete it, don't comment it out or leave it "for later").

---

## 15. Testing

One test module per source module, roughly (`tests/test_<name>.py` for `src/infinienv/**/<name>.py`).
Minimum coverage per area:

```text
test_schema.py        - valid scenes parse; missing fields fail; arbitrary `type` strings parse
                         at the schema layer (validator.py enforces the allowlist, not pydantic);
                         mechanics/InteractGoal parse
test_validator.py      - bounds/overlap/missing-goal-object/duplicate-id/no-goals fail; every
                          MECHANICS_* code has a test; the full "throw vase through window"
                          scenario validates end-to-end
test_reachability.py    - reachable passes; sealed fails
test_solver.py           - pickup/deliver/locked-door succeed; impossible task fails cleanly;
                            trace/goal_results reflect real incremental state, not a
                            reconstructed-after-the-fact snapshot (regression coverage for the
                            bug in notes.md)
test_interactions.py      - the effect interpreter: precondition enforcement, each effect op,
                             routing from apply_action for an unrecognized verb
test_physics.py            - push/slide engine (section 5b): shove one cell, slide-until-blocked,
                             blocked push raises, live collision (walk through a vacated cell)
test_action_registry.py    - generic closed-action dispatch (section 11): register/dispatch,
                             decorator registration, unregistered-name raises, duplicate raises
test_motion_patterns.py    - generic patrol/pulse_cycle/pursue (section 11): range/periodicity,
                             rise/hold/fall/idle timing, capped-speed step incl. snap-to-target
test_animation.py          - generic phase_of/oscillate/cycle_variant (section 11): wrapping,
                             sweep bounds, phase-to-variant bucketing
test_platformer_physics.py - generic integrate_grounded_2d/climb_step/clamp_to_bounds (section 11):
                             gravity+ground+bounds clamp, climb moves only y and raises off-structure
test_grid_collision.py     - generic segment_blocked/move_with_collision (section 11): sub-tile
                             sampling catches a diagonal wall-corner cut, stops instead of crossing
test_level_generation.py   - generic generate_organic_region/region_is_connected (section 11):
                             determinism, bounds, always-connected-by-construction, BFS reachability;
                             seeded side-view generators (section 11): generate_terrain_profile
                             (bounds/max_step, varies by seed), carve_gaps (avoids margins),
                             generate_platform_layout (tuple shapes, every adjacent level ladder-
                             connected, varies by seed), scatter_on_supports (spacing/pits/ends)
test_puzzle_state.py       - generic PuzzleState/Gate (section 11): flags/counters, numeric and
                             boolean thresholds, unset-flag defaults, joint/mixed requirements
test_pushables.py          - generic Sokoban push (section 11): push into free cell, blocked by
                             wall/block/bounds, cell_is_free, all_targets_satisfied
test_pathfinding.py        - generic BFS find_path/next_step_toward (section 11): straight path,
                             routes around a wall, None when walled off, shortest, first-step
test_vision.py             - generic perception (section 11): line-of-sight clear/blocked, range,
                             facing-cone half-angle, can_see composing range+cone+LOS
test_perception.py         - closed perception model (section 11): KnowledgeMap only knows what it
                             observed, find() can't reach an unseen target, memory persists out of
                             view; visible_cells radius + occlusion + bounds
test_auditor.py            - independent faithfulness auditor (section 11), hermetic (faked OpenAI):
                             verdict parsing (PASS/FAIL/garbage/code-fence), payload includes spec+
                             code+rules, best-effort skips (no key / AUDIT=0 / unparseable / no code /
                             API error) return audited=False,passed=True
test_agent_behavior.py     - generic BehaviorMachine (section 11): transitions fire only from the
                             current state, registration-order wins, one transition per update
test_rendering.py          - generic feet_anchor/feet_anchor_rect (section 11): sprite bottom edge
                             lands on the ground line (feet-anchored, not center-anchored/floating);
                             SpriteBook: paste present/missing key (fallback), unused_keys() reports
                             ignored sprites and the 'hero'-vs-'agent' key mismatch, caches by
                             (path,size), feet-anchor sits the sprite bottom on the ground line;
                             paste_column tiles a ladder/column contiguously across the full
                             inclusive floor-to-floor span (no % 2 gaps / trimmed endpoints)
test_sandbox_workspace.py  - ...also the teleport floor (section 11): _positions_from_replay parses
                             common trace shapes / None on unknown; _teleport_frame flags an
                             outlier jump but not smooth motion; outer_sanity_check fails a
                             teleporting replay, passes a smooth one, skips an unparseable one;
                             the deterministic validator on sandbox scenes: enforces OUT_OF_BOUNDS
                             + DUPLICATE_ID, does NOT enforce vocabulary-specific errors (records
                             them), deterministic_validation_summary shape
test_replay_export.py       - per-action frames + smooth interpolation of a multi-cell slide
test_mechanics_cache.py    - persist/reload, no duplication or overwrite on repeated calls
test_mock_generation.py     - mock provider is deterministic and always valid
test_assets.py               - scene_asset_types includes wall+agent; none/local/generated
                                 resolution modes; concurrent generation actually overlaps in
                                 time (not just faster-looking sequential calls) and respects
                                 INFINIENV_ASSET_CONCURRENCY; one type's generation failure is
                                 isolated and doesn't block the rest; auto-mode local fallback;
                                 variant_types/variant_descriptions naming (section 11); extra_types/
                                 extra_descriptions resolve without a placed SceneObject
test_generator_openai.py      - mocked OpenAI client (no network): texture vs. discrete-object
                                 branching (background param, prompt template, crop applied only
                                 for discrete types); quality defaults to "low", overridable via
                                 INFINIENV_IMAGE_QUALITY env var or the quality= kwarg
test_mutation.py               - mutations valid+distinct; LLM-proposed path used and validated;
                                  LLM failure degrades gracefully
test_curriculum.py              - level templates easy->hard; --run executes and writes artifacts
test_dataset_export.py           - per-goal programmatic_reward, not a flattened bool
test_compiler.py                  - --no-fallback raises with the real root cause surfaced, not
                                     just the last (often generic) history entry
test_cli.py                        - generate/validate/solve write expected artifacts
test_prompt_refiner.py             - sandbox prompt enrichment (section 11): mocked OpenAI client
                                     returns enriched text; graceful fallback to the original on
                                     no-key/API-error/empty-output; INFINIENV_REFINER_MODEL override
test_claude_runner.py              - Claude Agent SDK sandbox backend (section 11): duck-typed
                                     narration mapping (Bash/Write/Edit/text/thinking/errored-result;
                                     Read/Grep silent; malformed shapes degrade to silence),
                                     _copy_artifacts_from_dir, INFINIENV_SANDBOX_BACKEND dispatch
                                     (claude routes with the claude-sonnet default; default stays
                                     openai/gpt), and the no-key path proceeds via CLI login (never
                                     sets ANTHROPIC_API_KEY); test_cli.py asserts CL_KEY is NOT
                                     promoted to ANTHROPIC_API_KEY. All hermetic (no CLI/network)
test_gui.py                         - Flask test client (no live server needed): index page,
                                       validation errors, a full generate job consumed as real SSE
                                       events (stage + done), artifact serving incl. path-traversal
                                       rejection, runs listing
```

Before considering a change done:

```bash
pytest
python -m infinienv generate --provider mock --prompt "Create a kitchen delivery task" --out runs/smoke_test
python -m infinienv validate runs/smoke_test/scene.json
python -m infinienv solve runs/smoke_test/scene.json --out runs/smoke_test
```

For anything touching a provider, a mechanic, or the asset pipeline, also verify live against the
real API at least once (not just the offline test suite) before calling it done — several real
bugs this session (schema/structured-output mismatches, the trace bug, the wall-texture bug) were
only caught this way. Record what you verified live in `notes.md`. If tests can't be run in the
current environment, say so honestly rather than claiming untested work passes.

---

## 16. Documentation

`README.md` must stay reviewer-first: one-paragraph pitch, pipeline diagram, setup, no-key mock
mode, CLI examples, artifact examples, evaluation-criteria mapping, an honest limitations section.
A reviewer should understand how to run the project within 60 seconds of opening it. Don't bury
the demo instructions.

When you ship a feature, update, in this order: the code, its tests, `README.md` (if it changes
what a reviewer would run or see), this file (if it changes an invariant, adds a new
subsystem, or changes an existing one's behavior), and `notes.md` (the decision/bug log entry —
always, for anything non-obvious).

---

## 17. Safety and sandboxing

Sandbox mode — where the runtime LLM writes and runs its own code — is now the **default** `generate`
path (the MVP; see section 11 and the §2 rewrite). The deterministic, no-model-authored-code path is
opt-in via **`--no-sandbox`**, and on *that* path (section 2's guarantee) the following holds:

Allowed: emitting `SceneSpec`/mechanics JSON, calling the deterministic read-only/validate-only
tools (`get_scene_schema`, `get_supported_mechanics`, `get_known_mechanics`, `validate_scene_tool`),
requesting validation, requesting repair.

Not allowed (on `--no-sandbox`): shell execution from model output, arbitrary file writes from model
output (`artifacts/writer.py::resolve_out_dir` rejects path traversal outside the working directory),
importing packages chosen by the model at runtime, unrestricted `eval`/`exec`.

**The default sandbox path is the disclosed trade** (built after two earlier rounds where the idea
was proposed and declined, then explicitly chosen, then made the default). It lets the model write
and run real code — scoped to an isolated per-run workspace copy (never this repo's real source,
never another run's workspace), with the outer process never importing or executing the sandboxed
code itself, an independent faithfulness auditor reviewing each run, and every affected run labeled
`"source": "sandbox"` so the lost solvability guarantee is visible, not hidden. Use `--no-sandbox`
whenever the hard code-defined-truth guarantee matters. See section 11 for the full design and what
it does and doesn't verify.

---

## 18. Roadmap: GPU and 3D

Not required for the CPU/2D core. GPU (or Apple Silicon MPS) becomes relevant for local LLM
inference (an OpenAI-compatible endpoint, e.g. `LLM_PROVIDER=vllm`,
`LLM_BASE_URL=http://localhost:8000/v1`), batch prompt-suite generation, future vision-policy
training, or — as of section 9's `generator_diffusion.py` — optional local sprite generation
(`pip install infinienv[diffusion]`, `INFINIENV_SPRITE_BACKEND=diffusion`). None of this should
make the *basic* CLI require a GPU: `--assets` defaults to `none` and `INFINIENV_SPRITE_BACKEND`
defaults to `openai`, so a GPU/MPS device is only ever touched by an explicit, opt-in choice. (An
earlier design briefly made `diffusion` the default sprite backend too; reverted after
live-verified character-sprite quality problems — see section 9.)

3D: the schema is deliberately flat 2D (`x`/`y`, not `position`/`rotation`/`scale`) so validation
stays simple. A future 3D path would add those fields and exporters (Godot, Unity ML-Agents,
Isaac Lab, Genesis, Habitat, ManiSkill, MuJoCo) targeting the same `SceneSpec` abstraction, with a
vision-based policy eventually replacing the deterministic 2D planner for that path specifically
— the 2D path keeps its guarantees regardless.

---

## 19. Release checklist

```text
[ ] README explains the project in under 60 seconds.
[ ] `pip install -e .` works.
[ ] `python -m infinienv generate --provider mock ...` works without API keys.
[ ] `python -m infinienv generate --provider openai_agents ...` works with a real key.
[ ] Invalid scenes produce clear, structured validation errors; repair attempts are recorded.
[ ] render.png / replay.gif / metrics.json / replay.json are generated and metrics.json is truthful.
[ ] `--assets local` and `--assets generated` both produce a correctly-scaled, seamless-where-
    appropriate render (texture tiles fill edge-to-edge; discrete objects fill their cell without
    a boxy margin).
[ ] benchmark mode runs over multiple prompts; mutate/curriculum --run/export-dataset all work.
[ ] At least one scene exercises section 5's extended mechanics end-to-end (validated, solved,
    replayed), ideally live-verified against a prompt with no exact hand-authored precedent.
[ ] `--sandbox` mode (section 11) live-verified end-to-end at least once: agent-edited workspace
    actually synced back (not just the pre-run copy), outer sanity check runs and its verdict is
    recorded alongside the agent's own self-report in metrics.json.
[ ] pytest passes; anything touching a provider/mechanic/asset was also verified live, not just
    against the offline suite.
[ ] notes.md reflects any non-obvious decision or bug fix from the current work.
```

---

## 20. Framing

> InfiniEnv is a verified environment factory. It compiles natural language into structured,
> playable worlds — including worlds with mechanics the model defined itself, expressed as safe,
> validated data rather than code; repairs invalid generations using deterministic validator
> feedback; mutates successful worlds into infinite variants; and emits replayable proof that an
> agent can complete code-defined objectives.

Avoid framing it as "an LLM makes a game." Use instead:

> A model proposes. The harness verifies. The agent proves.

That is the core research contribution, and it still holds at every layer this project has grown
into.
