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
- **No model-authored code execution in the default path.** Every `generate` run other than the
  explicit, opt-in `--sandbox` mode never lets the model write or run code: not `eval`/`exec`, not
  shell commands, not dynamically imported/chosen packages, not arbitrary file writes outside the
  selected output directory. This is why section 5's extended mechanics are a *declarative effect
  system* (a fixed, finite vocabulary of effect ops interpreted by real, tested Python in
  `engine/interactions.py`) and not "let the model write and run a handler function." **The one
  disclosed exception is section 11's sandbox mode** — built after the user explicitly requested
  general model-authored engine code (not just a fixed set of effects) and, when asked to resolve
  the highest-risk design question, chose an isolated per-run workspace copy over touching the
  real installation. That mode does not pretend the validator-wins guarantee survives it — it
  labels every affected run `"source": "sandbox"` and documents exactly what's lost. See section
  11 and `notes.md` for the full history, including the two earlier rounds where this was
  proposed and declined before the user's explicit, informed redirect.
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
│   │       └── mutation_agent.md
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
│   │   └── physics.py               # deterministic grid-physics: push + slide (section 5b)
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
│   │   ├── resolver.py              # resolve_assets(): none/local/generated/auto modes
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
- `auto` — generated, falling back to the local placeholder (noted in `asset_manifest.json`) if
  generation is unavailable.

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
  both `render.png` and every frame of `replay.gif`, not just `verify()` plus a frame count.
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

### Follow-on findings from watching the fix in production

Live narration of subsequent runs (same "rescue the princess" prompt) surfaced three more real
problems — one in this project's own narration code, two in what agents commonly get wrong even
with a genuine simulation:

- **A user-reported screenshot ("it hit the turtle and nothing happened") traced to a real hitbox
  bug, not a repeat of the fake-animation problem.** That run's `step()` function was completely
  genuine — real per-frame collision resolution against live positions — but its contact check
  used `distance < 0.32` tile units while the sprites it drew (`draw_frame`) spanned roughly
  0.5–0.875 tile units each. Confirmed from the actual trace data: the closest approach in the
  whole run was 0.65 units — squarely inside the visual-overlap range but outside the code's
  threshold, so sprites visibly touched on screen with no consequence. `sandbox_agent.md` gained
  an explicit "calibrate collision/hazard radii against what you actually draw" section with this
  exact bug as the worked example, plus a specific self-review check: "do any two sprites visually
  overlap in a frame where nothing happened."
- **The narration transcript for that run showed the agent stuck in a long, unproductive
  trial-and-error loop**, repeatedly hand-editing constants via `perl -pi -e 's/.../.../'` and
  re-running, with most edits reporting `command failed (exit 1): perl: warning: Setting locale
  failed.` Reproduced directly: that locale warning is cosmetic on its own (`perl` still exits 0
  when only that warning fires) — so something else was the real failure, and
  `_describe_stream_event`'s `_describe_tool_output` was showing only the *first* output line,
  which is exactly where an incidental warning like this sits, hiding whatever the real error
  actually was from anyone watching the narration (the agent's own context isn't affected by this
  — narration is a separate, best-effort summary of the same conversation, not what the agent
  itself reads). Fixed by showing the first *and* last non-empty output line when they differ
  (shell errors and Python tracebacks put the real summary last); also added guidance in
  `sandbox_agent.md` to prefer `apply_patch` over shell text substitution for editing the agent's
  own source, since a multi-line pattern silently no-ops on any whitespace mismatch while the
  command can still exit non-zero for an unrelated reason — exactly the trap this run fell into.
- **A third: a declared gating rule silently bypassed by its own debugging fallback.** A follow-up
  screenshot ("the character climbs without a ladder") traced to `on_ladder = any(abs(x - lx) <
  0.65 for lx in (11, 13)) or x > 12.4` — the `or` clause treats an entire region past that x
  coordinate as climbable regardless of ladder presence, almost certainly added while the
  controller was stuck near the tower during the agent's own debugging, loosening the rule instead
  of fixing why it was stuck. `sandbox_agent.md` gained a "never add a broad fallback that bypasses
  a gating rule just because you got stuck" paragraph naming this exact line as the worked example,
  plus a self-review instruction to re-read gating conditions specifically for an `or` clause added
  mid-debugging — flagged as easy to miss since the agent that wrote the workaround is the one
  reviewing it. Live-verified: the next run's `on_ladder` check required both an exact ladder-cell
  match and tight column tolerance, no broad bypass.

Each of these three targets a different way a *genuine* simulation can still misrepresent itself —
a fake animation, a miscalibrated hitbox, a rule quietly bypassed by its own fix — and each fix
generalizes the self-review instructions rather than special-casing one game.

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
  "source": "sandbox", "provider": "openai_agents_sandbox", "seed": 2,
  "success": true, "sandbox_self_reported_success": true,
  "outer_sanity_passed": true, "outer_sanity_error": null,
  "missing_artifacts": [], "repair_attempts": 0,
  "repair_history": [{"attempt": 0, "run_error": null, "outer_sanity_passed": true, "outer_sanity_error": null, "missing_artifacts": []}]
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

### Explicitly out of scope for this mode (for now)

Making sandbox mode the default; the `docker`-backed sandbox client (Unix-local was the pragmatic
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
python -m infinienv generate --prompt "..." --provider {mock,openai_agents,openai_responses,anthropic} \
  --seed 42 --out runs/demo [--max-repair-attempts N] [--no-fallback] \
  [--assets {none,local,generated,auto}]

python -m infinienv generate --sandbox --prompt "..." --seed 42 --out runs/demo [--max-repair-attempts N] \
  [--assets {none,local,generated,auto}]
  # opt-in, section 11: model-authored engine code in an isolated per-run workspace copy.
  # ignores --provider/--no-fallback (no LLM-repair-agent path or fallback-template path to
  # apply them to); --assets applies the same as any other run, resolved inside the sandbox
  # workspace via a copy of assets/resolver.py; --max-repair-attempts here means repair
  # attempts against the outer sanity check (default 2), not the LLM repair agent; trades
  # away the validator-guaranteed solvability check every other run has -- see metrics.json's
  # outer_sanity_* fields.

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
does to non-sandbox ones, same as the CLI's `--assets`. Streams the same per-attempt `on_stage`
progress messages
(`sandbox/runner.py`'s repair loop now takes an `on_stage` callback mirroring
`evaluation/runner.py`'s), and renders results distinctly: both verdicts side by side
(`sandbox_self_reported_success`/`outer_sanity_passed`), the agent's own summary text, and a
"sandbox" badge on both the live result banner and that run's entry in the recent-runs gallery —
so a reviewer browsing past runs in the GUI can never mistake a sandbox result for a
validator-guaranteed one, the same requirement the CLI output already met.

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
test_replay_export.py       - per-action frames + smooth interpolation of a multi-cell slide
test_mechanics_cache.py    - persist/reload, no duplication or overwrite on repeated calls
test_mock_generation.py     - mock provider is deterministic and always valid
test_assets.py               - scene_asset_types includes wall+agent; none/local/generated
                                 resolution modes; concurrent generation actually overlaps in
                                 time (not just faster-looking sequential calls) and respects
                                 INFINIENV_ASSET_CONCURRENCY; one type's generation failure is
                                 isolated and doesn't block the rest; auto-mode local fallback
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

The runtime LLM must not execute arbitrary generated code **in the default path** (section 2).
Concretely, for every `generate` run other than `--sandbox`:

Allowed: emitting `SceneSpec`/mechanics JSON, calling the deterministic read-only/validate-only
tools (`get_scene_schema`, `get_supported_mechanics`, `get_known_mechanics`, `validate_scene_tool`),
requesting validation, requesting repair.

Not allowed: shell execution from model output, arbitrary file writes from model output
(`artifacts/writer.py::resolve_out_dir` rejects path traversal outside the working directory),
importing packages chosen by the model at runtime, unrestricted `eval`/`exec`.

**Section 11's `--sandbox` mode is the one disclosed exception**, built after the user explicitly
asked for it following two earlier rounds where the same idea was proposed and declined on these
exact grounds. It does let the model write and run real code — scoped to an isolated per-run
workspace copy (never this repo's real source, never another run's workspace), opt-in via an
explicit flag, with the outer process never importing or executing the sandboxed code itself and
every affected run labeled `"source": "sandbox"` so the lost guarantee is visible, not hidden. See
section 11 for the full design and what it does and doesn't verify.

---

## 18. Roadmap: GPU and 3D

Not required for the CPU/2D core. GPU becomes relevant for local LLM inference (an
OpenAI-compatible endpoint, e.g. `LLM_PROVIDER=vllm`, `LLM_BASE_URL=http://localhost:8000/v1`),
batch prompt-suite generation, or future vision-policy training — none of this should make the
basic CLI require a GPU.

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
