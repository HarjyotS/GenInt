# InfiniEnv build notes / decision log

Running log of non-obvious decisions made while building this repo from `CLAUDE.md` + `SPEC.md`.
Newest at the bottom.

## Schema: followed CLAUDE.md's SceneSpec shape, not SPEC.md's

`SPEC.md` and `CLAUDE.md` each sketch a slightly different `SceneSpec` (SPEC.md uses
`world.width`/`position: [x, y]`/a singular `goal`; CLAUDE.md uses `grid.width`/flat `x`,`y`/a
`goals` list). CLAUDE.md is the more detailed, implementation-directive of the two ("operating
rules for Claude Code"), so its shape won as the actual `SceneSpec` pydantic model
(`schema/scene_schema.py`). Flat `x`/`y` also makes grid/bounds/collision validation simpler
than nested `position` objects.

## Goals are a top-level list, treated as an implicit sequence

Rather than requiring an explicit `sequence` goal wrapper for multi-step tasks (e.g. key-door),
`scene.goals` is just a list and `navigation/policy.py::solve_scene` satisfies them in order.
`sequence` still exists as a goal *type* for nesting inside a single goal slot, but most scenes
don't need it. Key/door tasks are two top-level goals: `unlock` then `deliver`.

## Reachability pre-check treats all doors as optimistically unlocked

`validation/validator.py`'s reachability pass (distinct from the full solvability pass) checks
"is this object walled off entirely," not "can the agent reach it *right now* given locked
doors" — the latter is legitimately false for anything behind a locked door until the door is
unlocked, which is the whole point of key/door tasks. Using `unlocked_doors=all door ids` for
that specific check avoids false-positive `UNREACHABLE_OBJECT` errors on valid key-door scenes,
while walls (permanent) still correctly seal things off. Full solvability (the real check) runs
the deterministic planner/solver, which respects locks properly in order.

## Renderer: Pillow only, not pygame

CLAUDE.md suggests `pygame + Pillow/imageio`. Went Pillow-only for `render.png` and animated-GIF
`replay.gif` (via `Image.save(..., save_all=True)`) — pygame needs an SDL display/video context
that's a real risk in a headless CI/reviewer environment, and Pillow covers both deliverables
without that dependency risk. No `imageio` dependency either; Pillow's own animated-GIF support
was sufficient.

## Border-wall generator bug (caught by the validator, as designed)

First `mock` run failed validation with `ILLEGAL_OVERLAP` at all four grid corners —
`templates._border_walls` was appending each corner cell twice (once from the x-loop, once from
the y-loop). This is exactly the kind of bug the deterministic validator is supposed to catch
before it reaches a reviewer; fixed by deduping into a set before emitting `WallCell`s.

## Repair loop didn't survive a malformed LLM response — fixed

`generation/compiler.py` originally called `provider.generate_scene(...)` and let any
`pydantic.ValidationError` from a malformed model response propagate straight out of
`generate_and_validate`, crashing the whole `generate` command instead of feeding the failure
into the repair loop. Real first-run reproduction: `openai_agents` returned JSON using its own
field names (`world`/`position`/`agent_id`) instead of ours. Fixed by catching
`(ProviderError, pydantic.ValidationError)` around both the initial generate call and each
repair call, turning a parse failure into a `GENERATION_FAILED` validation issue that still
flows through the same repair-then-fallback path and gets recorded in `validation.json` /
`report.md` (never silently discarded, per CLAUDE.md section 9).

## OpenAI Agents SDK: structured output beats prompt-only JSON

Initial approach (plain-text agent + manual `json.loads`) was unreliable — the model would
substitute its own field names despite explicit prompt instructions. Fixed two ways, in order
of leverage:
1. Set `output_type=SceneSpec` on both `Agent`s so the SDK requests structured output matching
   the pydantic schema directly (`agents.strict_schema.ensure_strict_json_schema` handles making
   our schema, including the discriminated-union `goals` list, strict-mode compatible).
2. Also embedded a concrete worked example (deliver task + key/door task) directly in
   `llm/prompts/scene_planner.md` rather than relying on the model voluntarily calling the
   `get_scene_schema` tool.
Also had to pass `strict_mode=False` to the `validate_scene_tool` function tool specifically —
its `scene_spec: dict` parameter can't be made strict-schema-compatible (arbitrary dict shape),
which otherwise raised `UserError: additionalProperties should not be set for object types` at
agent-construction time.

`llm/providers/openai_responses.py` reuses the same `ensure_strict_json_schema` helper (from the
`agents` package) to build a `text.format` structured-output request for the plain Responses
API, with a graceful fallback to prompt-only JSON extraction if `openai-agents` isn't installed.

## `.env` key precedence bug

The shell environment already had a stale `OPENAI_API_KEY` exported (a bad/expired key) before
this session started. `cli._load_dotenv()` originally only copied `.env`'s `OP_KEY` into
`OPENAI_API_KEY` when `OPENAI_API_KEY` wasn't already set — so the stale shell value silently
won over the working key in `.env`. Fixed by calling `load_dotenv(override=True)` and always
re-deriving `OPENAI_API_KEY` from `OP_KEY` when present, since `.env` is the intended source of
truth for this project. Confirmed working end-to-end afterward (`openai_agents` and
`openai_responses` both generate valid scenes on the first try against the real API).

## `anthropic` provider: implemented, not just a registry stub

`llm/__init__.get_provider("anthropic")` referenced `llm/providers/anthropic.py` before that
file existed, which would have crashed with an unfriendly `ModuleNotFoundError` instead of a
clean `ProviderError` if someone picked `--provider anthropic`. Implemented it for real (same
protocol, direct Messages API call, same JSON-parsing path as `openai_responses`) rather than
leaving a dangling reference. Not exercised against a live key in this session (no
`ANTHROPIC_API_KEY` available) — fails cleanly with a clear message if the key is missing.

## `--no-fallback`: make silent template fallback opt-out, not just opt-in

The default repair-then-template-fallback behavior (CLAUDE.md section 9) is right for
unattended/reviewer runs, but it means a real provider failure (bad JSON, rate limit, wrong
schema) can still end in `Result: SUCCESS` because the *fallback* scene validated fine — masking
the actual problem while iterating on prompts/providers. Added `generate_and_validate(...,
allow_fallback: bool)`; when `False` and validation never passes, it raises
`GenerationFailedError` (a `ProviderError` subclass) instead of generating the template scene.
`runner.run_generation` raises before any artifact is written (no fake `scene.json` /
`metrics.json` for a run that didn't really work), and `cli.py`'s existing `ProviderError`
handler in `main()` turns that into a clean non-zero exit with the real reason printed. Wired up
as `infinienv generate --no-fallback`. Verified: `mock --no-fallback` still succeeds (mock is
valid by construction); `openai_agents --no-fallback` against the real API also now succeeds on
the first try (confirms the structured-output fix above actually resolved the underlying
reliability problem, not just the fallback masking it); a stub provider that always returns an
invalid scene correctly raises `GenerationFailedError` with `allow_fallback=False`.

## --no-fallback surfaced two more real bugs on a harder prompt

Used `--no-fallback` on a genuinely complex prompt ("4 packages across a multi-room office
building") and it did its job: two real problems surfaced instead of being masked.

1. **`SceneObject.type` was `str` + a runtime check, not a schema-level enum.** Under
   `output_type=SceneSpec` structured output, the model happily sampled `"desk"`, `"sofa"`,
   `"cabinet"`, `"chair"` — none of which are supported object types — because the JSON schema
   for `type` only said `{"type": "string"}`. The runtime `@model_validator` caught it, but only
   *after* the SDK had already tried (and failed) to parse the output, producing an opaque `SDK
   run failed` error. Fixed by making `type` a `Literal[OBJECT_TYPE_VALUES]` so the JSON schema
   itself carries an `enum` constraint — this is a real behavior change (constrains what the
   model can even sample), not just a nicer error. Added
   `test_object_type_is_an_enum_in_the_json_schema` as a regression test.
2. **`GenerationFailedError` only showed `history[-1]`.** When the initial `generate_scene` call
   raises, the loop appends one more entry ("no parseable previous scene to repair") before
   breaking — and the error message was built from *that* entry, not the original one, so the
   real cause (a 401, a JSON parse error, whatever) was invisible unless you went digging in
   `.history` yourself. Fixed to join every attempt's description into the message. Added
   `test_generation_failed_error_surfaces_root_cause_not_just_last_entry`.
3. **The model put `// comment` lines inside a large `walls` array**, which isn't valid JSON,
   on the same complex prompt — even with `output_type=SceneSpec` set. Best guess: the schema's
   recursive `sequence` goal (a goal containing a list of goals, one of which can itself be a
   `sequence`) makes the schema self-referential in a way OpenAI's strict/grammar-constrained
   structured-output mode doesn't fully support, so this particular call likely degraded to
   non-strict mode without erroring, and the model reverted to human-readable JSON-with-comments
   habits once its own walls array got long. Not confirmed via API-level introspection (would
   need to compare the actual request payload's `strict` flag), just the most plausible
   explanation for output that violates the schema in a way the JSON-schema `enum`/strict layer
   should have prevented. Mitigated at the prompt layer (`scene_planner.md` now explicitly
   forbids comments/trailing commas and calls out long wall arrays as expected). Confirmed fixed
   end-to-end: the same prompt+seed that failed now succeeds after one repair attempt.

## Mutation engine: 4 of the 5 listed strategies

CLAUDE.md section 16.B lists six mutation ideas. Implemented four as real operators in
`generation/mutation.py` (reposition objects, add obstacle, add distractor, reverse start),
each re-validated (full schema + solvability) before being kept. Skipped "add key-door
dependency" as a mutation operator — restructuring goals/walls to correctly retrofit a lock
into an arbitrary scene is materially more complex than the other four and lower value than
getting the rest of the P0/P1 surface solid first; noted as a gap in the README rather than
silently dropped.

## PATHWAY.md: build new capabilities on the working foundation, don't rewrite it

The user pasted `PATHWAY.md`, a much larger roadmap (asset pipeline, dataset export, richer
mutation/curriculum, a renamed package layout, pygame-ce, typer/rich CLI, a v0.2 `SceneSpec`
with `tiles`/`goal.steps`). Asked the user directly rather than guessing on scope, since large
parts of it either duplicate or conflict with decisions already made and *verified against the
real API* in this session. Confirmed direction: build the genuinely new capabilities
(asset pipeline, dataset export, curriculum execution, richer mutations) on top of the current
architecture; explicitly skip the package rename, the `SceneSpec` v0.2 migration, the typer/rich
CLI swap, and pygame-ce. Reasoning for each skip is in the README's Limitations section now
(not duplicating it here) — the short version is: none of those four actually add capability,
they're alternate implementations of things that already work and are tested, and the schema/
package rewrites specifically would invalidate every real-API-verified scene and test built so
far for no functional gain.

## Asset pipeline: type-keyed caching, not scene-keyed

`assets/resolver.py` resolves one sprite per object **type** (not per object instance, not per
scene) into a shared `.infinienv_asset_cache/` directory at the repo root (not inside any single
run's output dir). This was a deliberate choice, confirmed against the user's explicit ask ("we
should cache images... only generate more if we need it"): a "table" sprite generated for one
scene is reused by every future scene with a table, so `--assets auto`/`generated` only ever
calls the Images API for types that have never been generated before. Verified live: cold run
with 5 uncached types took ~4 minutes (real gpt-image-1 calls); an immediate second run with the
same types took 0.8s total, `asset_manifest.json` showing `"source": "generated", "note": "cache
hit"` for all 5. Local placeholders (`assets/placeholder_gen.py`) are simple Pillow-drawn icons,
generated once and checked into git (`assets/base/*.png`) — no network needed for `--assets
local`, which is the safe default for CI/offline smoke tests.

One real bug caught while building this: `resolver.scene_asset_types` only scanned
`scene.objects`, so `walls` (a separate list, not `SceneObject`s) never got a "wall" entry in the
manifest and always rendered as flat color even with `--assets local/auto`. Fixed by unconditionally
including `"wall"` in the requested type set whenever `scene.walls` is non-empty.

Model note: PATHWAY.md names `gpt-image-2`, which isn't a real released OpenAI model as of this
session; defaulted to `gpt-image-1` (the actual current image model), overridable via
`INFINIENV_IMAGE_MODEL` in case a newer model name becomes available later.

## Dataset export: real per-goal reward, not a flattened success bit

PATHWAY.md's `programmatic_reward` example has named per-subgoal keys (`picked_key: 1,
unlocked_freezer: 1, ...`). To make that real rather than just relabeling `metrics.json`'s single
`success` bool four ways, added `SolveResult.goal_results` to `navigation/policy.py` — a
per-top-level-goal `{"id", "type", "success"}` list recorded as `solve_scene` processes each goal
in order (including on early failure/`PlanError`, so every goal is always represented). This gets
written into `replay.json` (a new artifact `run_generation` now always writes, alongside the
existing six) and `export/dataset.py` reads it to build a genuine per-goal
`programmatic_reward` — e.g. a 4-package delivery scene where the solver got 3/4 shows
`{"deliver_package_1": 1, "deliver_package_2": 1, "deliver_package_3": 1, "deliver_package_4": 0,
"total": 3}`, not a flat `0`.

## Curriculum `--run`: reconciled two different meanings of `--out`

The original `curriculum` command's `--out` was a single prompts.txt file path. PATHWAY.md's
curriculum wants `<out>/level_01/{scene.json,replay.gif,metrics.json}` per level. Rather than
picking one and breaking the other, `--out` keeps its original meaning (a prompts.txt file path)
unless `--run` is passed, in which case `--out` is treated as a directory: each level gets
executed into `<out>/level_NN/` via the normal `run_generation` pipeline, and a `prompts.txt` is
still written alongside for benchmark compatibility. No existing usage breaks either way.

## LLM-driven mutations: duck-typed, not a schema/protocol change

`SceneProvider` (the `generate_scene`/`repair_scene` protocol) wasn't extended with a required
`propose_mutation` method — only `OpenAIAgentsProvider` implements it. `generation/mutation.py`
checks `hasattr(provider, "propose_mutation")` at call time and mixes LLM-proposed candidates in
alongside the deterministic strategies at a caller-chosen `--llm-fraction`; every candidate,
LLM-proposed or not, goes through the exact same `validate_scene()` before being kept, and a
failed/malformed LLM proposal (`ProviderError`/pydantic `ValidationError`) is caught and treated
like any other rejected candidate — the loop just keeps trying, never crashes. Verified live
against the real API: 4/4 requested mutations at `llm_fraction=0.9` came back genuinely
LLM-proposed (moved objects, added a distractor/obstacle box, added walls) and all passed full
validation on the first attempt.

## Extended mechanics: "let the model define real behavior" without letting it execute code

User request: the harness should be able to represent things outside the fixed object/action
vocabulary -- a window you can throw things out of, a switch that unlocks a door, etc. -- with
the model defining the actual behavior, not just flavor text. Explicitly asked me to update
`CLAUDE.md` to formally allow this, since it's a direct extension beyond the MVP's core rule
("do not let the LLM be the source of truth" / "add mechanics only when validators and tests are
updated").

Gave the user three options before building anything (curated hand-written expansion / generic
property system / model-authored per-scene behavior) because the third one directly touches
CLAUDE.md's most load-bearing safety rule (section 23: no eval/exec, no arbitrary code from model
output) and I wasn't going to guess at that tradeoff. User picked option 3, explicitly framed as
"beyond the MVP, make it full-fledged."

Resolved the apparent conflict with a **declarative effect system**, not code execution: a scene
can declare `mechanics.custom_object_types` (new type ids) and `mechanics.custom_interactions` (a
new verb + preconditions + an ordered list of **effects**, each one of a small *fixed* vocabulary
-- `remove_held_object`, `drop_held_object_at_target`, `remove_object`, `unlock_target`,
`set_object_property`, `teleport_agent` -- implemented in the new `engine/interactions.py`). The
model composes behavior out of these primitives; it never writes the primitives themselves, and
there is no eval/exec anywhere in the interpreter. This is the only way to honor "model defines
real behavior" without breaking section 23, and I said so explicitly rather than silently
reinterpreting the user's request into something safer without flagging the substitution.

New pieces: `SceneObject.type` reverted from the `Literal` enum (added earlier this session) back
to a free `str` -- custom types are now legitimate, so the enum would have blocked the very thing
being added; the actual "is this type allowed" check moved to `validate_scene` (built-in OR
declared in `mechanics.custom_object_types`). `InteractGoal` (`type: "interact"`) is satisfied
once `(interaction_id, target_id)` is in a new `GameState.completed_interactions` set. Planner
gained `_plan_interact` (paths to target, auto-picks-up a `must_hold_type` match first if not
already held, emits the custom verb) following the same pattern as `_plan_unlock`.
`generation/mechanics_cache.py` persists every new custom type/interaction from a *validated*
scene into `.infinienv_mechanics_cache.json` (gitignored, same treatment as the asset cache) and
a new `get_known_mechanics` tool exposes it back to the model so "window" means the same thing
next time instead of drifting.

**Real bug found via this feature, unrelated to it:** the very first live test (`--no-fallback`
on a real "throw a vase out a window" prompt) produced a scene where `replay.json`'s `trace` had
the *same* position/inventory repeated for every step past t=0, and `inventory` stayed `[]` even
after a successful `pick_up`. Root cause, pre-dating this session's mechanics work entirely:
`solve_scene`'s trace-building loop sampled `state` *after* `plan_goal` had already fully mutated
it to the goal's end state (since `plan_goal`/`_emit` apply each action to `state` immediately as
they're planned, by design, so later planning steps see up-to-date state) -- so every "per-step"
trace entry was actually reading the same final snapshot. `replay.gif` was never affected (
`render/replay_export.py` independently re-simulates from `actions`), which is why nobody had
noticed. Fixed by threading an optional `trace` list through `_emit`/`_path_moves_to`/
`_ensure_holding`/every `_plan_*` function in `navigation/planner.py`, so each trace entry is
recorded at the exact moment its action is applied, not reconstructed afterward from a
by-then-stale-in-a-different-way state reference. `solve_scene` now just passes its `trace` list
into `plan_goal` and stops trying to rebuild it itself. Added
`test_trace_records_incremental_state_not_final_state_repeated` as a regression test -- confirmed
it fails against the old code and passes against the fix.

**Real integration bug found via this feature:** `output_type=SceneSpec` (added earlier this
session for structured-output reliability) started failing with "Strict JSON schema is enabled,
but the output type is not valid" the moment `SceneObject.properties: dict[str, bool|str|int]`
and `InteractionEffect.property_value: bool|str|int|None` existed -- OpenAI's strict/
grammar-constrained structured-output mode rejects open-ended dict/union shapes outright (same
underlying class of issue as the earlier `validate_scene_tool(scene_spec: dict)` strict-mode
failure). Fixed by switching all three `Agent(...)` constructions in
`llm/providers/openai_agents.py` to `output_type=AgentOutputSchema(SceneSpec,
strict_json_schema=False)`, and `openai_responses.py`'s Responses API call to `"strict": False`
in its `text.format` (dropping the now-broken `ensure_strict_json_schema` conversion entirely,
since a schema this open-ended can't be made strict-compatible without lossy restructuring).
`validate_scene_dict` remains the real gate either way; non-strict just means the request-time
JSON schema is advisory instead of grammar-enforced.

**Live verification, both valid on the first attempt, no repair needed:** (1) "pick up a vase
from a table and throw it out the window" -> model declared `vase`/`window` custom types and a
`throw_through_window` interaction (`must_hold_type: "vase"`, effect `remove_held_object`),
solved in 10 actions, vase genuinely removed from `final_state.objects`. (2) An unrelated prompt
("flip a switch to unlock a vault door") to check this generalizes rather than just echoing the
prompt's one worked example -- came back with a *different* verb (`"flip"`) and a *different*
effect composition (`set_object_property` + `unlock_target` against an explicit target object id,
not the `"target"`/`"held"` shorthand), solved in 13 actions. Mechanics cache correctly
accumulated both across the two runs. Also confirmed existing systems degrade/compose correctly
with scenes that use mechanics: `mutate` preserves `mechanics` through `model_copy(deep=True)`
and re-validates each variant (including re-solving the custom interaction) same as any other
mutation; the renderer's existing `COLORS.get(type, gray)` fallback already handles types it's
never seen, no changes needed.

## Museum heist full demo: sprite-fill bug, LLM sampling variance, sandbox agents declined

Built a genuinely complex demo combining a built-in key/door goal with a custom `crack_open_safe`
interaction (`must_hold_type: "stethoscope"`, effect `unlock_target`) and `--assets generated`.
Emergent design worth noting: the model placed `jewel_1` and `safe_1` on the *same* cell with the
safe `solid=True, locked=True` -- the jewel is physically inaccessible until `crack` fires
`unlock_target` on the safe, at which point the cell becomes enterable. Not something I designed
for explicitly; the model figured out how to express "jewel is inside the safe" using only
existing primitives (shared position + solid/locked).

**Real bug, user-reported from a screenshot**: wall tiles had visible padding around a "brick
chunk" instead of filling their cell, making the whole layout read as scattered blocks rather
than a floor plan (compounding a separate, correct observation that the door looked "arbitrary" --
it wasn't logically arbitrary, there was a real wall gap, but non-filling wall sprites made the
partition unreadable as a wall). Root cause: `_crop_to_content` (added right before this) treats
every object the same way -- crop to bounding box, pad to square. That's correct for a discrete
object like a key sitting *on* a tile, but wrong for wall/floor, which aren't objects on a tile,
they *are* the tile's surface and should be a seamless edge-to-edge texture with zero margin.
Fixed by splitting into `TEXTURE_TILE_TYPES = {"wall", "floor"}` with their own prompt template
(explicitly demanding a seamless, opaque, zero-margin, zero-transparency tile) and
`background="opaque"` instead of `"transparent"`, and skipping `_crop_to_content` entirely for
that path (cropping a texture that's supposed to already fill 100% of the frame would be a
no-op at best, risk clipping a busy edge-to-edge pattern at worst). Verified with a 4x4 tiled
sheet of the regenerated wall sprite -- genuinely seamless, no visible grid. Re-rendering the
existing museum heist scene with the fixed wall sprite (everything else cache-hit) turned it from
"scattered icons" into a readable floor plan with the door correctly sitting in a real wall
opening. Added `tests/test_generator_openai.py` (mocked OpenAI client, no network) asserting the
texture/discrete branching: correct `background` value, correct prompt template, crop applied
only for discrete types.

**Model correction on gpt-image-2**: mentioned gpt-image-2 has transparent-background support.
Two independent OpenAI doc fetches (API reference + guide page) both said the opposite --
gpt-image-2 explicitly does NOT support `background: "transparent"`; that's gpt-image-1/1.5/
1-mini. Flagged the contradiction rather than either silently complying or silently overriding,
since shipping code that silently drops the requested feature (transparency requested on a model
that rejects it) would be worse than asking. Confirmed via `AskUserQuestion`: kept `gpt-image-1`
(already the default, already proven working this session) and added `background="transparent"`
+ `output_format="png"` to the real API call -- genuine alpha transparency instead of the
color-distance-based matting I'd been about to build as a workaround, which is a strictly better
fix once available.

**LLM sampling variance, not a bug**: chasing a "regenerate the same demo" request, the exact
same prompt+seed produced valid-on-first-try, invalid-with-recoverable-repair, and
invalid-after-exhausting-3-repairs outcomes across different calls. `seed` is embedded in the
user message text, not used as a real sampling seed, so this is expected -- confirmed the
specific "safe+jewel share a locked cell, deliver to separate exit" pattern solves correctly
when it validates (goal_results all true, 48 correct actions), so the sporadic `UNSOLVABLE`
failures are the validator correctly rejecting genuinely-different bad layouts on a harder
prompt, not a hidden engine defect. Reported this distinction explicitly rather than either
claiming a bug that wasn't there or hand-waving away real failures: the *delivered* output
(`runs/.../scene.json`) has always validated in every demo shipped this session, because the
repair-then-fallback loop is precisely the mitigation for this variance. The `--no-fallback`
failures the user saw were debug probes I ran on purpose with that safety net deliberately
switched off.

**Sandbox agents (declined for now)**: asked to give each game a
[sandbox agent](https://openai.github.io/openai-agents-python/sandbox_agents/) that could
redefine movement/physics/mechanics per scene via arbitrary tool use, framed as reducing errors.
Looked up what "sandbox agents" actually means in the Agents SDK before responding (shell access
+ command execution + file editing in an isolated sandbox) rather than assuming from the name.
That's model-authored code actually executing, which directly conflicts with two things at once:
CLAUDE.md section 23 (no arbitrary code execution from model output, sandboxed or not) and the
user's own separate request in the same conversation that generation should never produce
something that doesn't validate -- arbitrary sandboxed code has no equivalent to the fixed,
enumerable effect vocabulary that makes our validator's solvability guarantee possible, so
adopting it would trade a hard guarantee for a soft one while being told the opposite was wanted.
Explained the conflict and asked rather than either building it or refusing outright; user chose
to defer sandbox agents and continue extending capability through the existing effect-op
vocabulary instead (echoed in the new CLAUDE.md section 0).

## CLAUDE.md rewrite: build spec -> operating rules for the current system

Asked to refactor the entire file for "significant growth and feature expansion." Previous shape
was an MVP build spec (P0/P1/P2 priorities, a "suggested structure" that no longer matched the
real tree, an appendix section 28 bolted on for extended mechanics) written before any code
existed. Rewrote end-to-end into a description of the system as it actually is: accurate current
file tree, the full `SceneSpec` schema (base vocab + mechanics as equally first-class, not
base-vs-extension), all four providers with their real tool lists, the complete CLI surface, both
caches, and a permanent non-negotiable-invariants section up top (validator wins, no
model-authored code execution even sandboxed) that's meant to survive every future round of
growth without needing another rewrite. Verified every concrete claim (schema value lists via
`OBJECT_TYPE_VALUES`/`ACTION_TYPES`/`GOAL_TYPES`/`EFFECT_OP_VALUES`, every CLI flag via `--help`
on each subcommand, the `.env` key-precedence logic, the actual file tree via `find`) against the
running code rather than reconstructing from memory or from what I intended to build — caught and
fixed one real inaccuracy this way (`OP_KEY` unconditionally overwrites `OPENAI_API_KEY` when
set, not just as a fallback -- the first draft had the fallback framing backwards).

## Local web GUI: Flask, SSE for live progress, thin frontend on run_generation

Asked for "a simple gui with all the settings I can toggle" for prompts, then interrupted a
`pip install flask` mid-flight specifically to add "i also want it to print the current step" --
i.e. live per-stage progress, not a spinner-then-final-result. Redesigned around that requirement
before writing any code: `POST /api/generate` starts the real `run_generation` call in a
background thread (a `queue.Queue` per job collects `on_stage` callback messages), returns a
`job_id` immediately, and `GET /api/stream/<job_id>` is a Server-Sent-Events endpoint that yields
each stage message as it's queued (plus periodic `: keep-alive` comment lines so the connection
survives a slow real API call without the browser timing it out) and a final `done`/`error`
event. This needed no new dependency beyond Flask itself -- SSE is just a streaming HTTP response
with a specific content-type and event framing, not a separate protocol/library.

Deliberately a *frontend*, not a second implementation: the Flask routes call the exact same
`evaluation.runner.run_generation` the CLI calls, with the exact same `on_stage` callback
mechanism already built for the CLI's `[n/total] ...` output -- the GUI just consumes it over SSE
instead of printing to stdout. Every `generate` setting is a real form control (provider, seed,
`--assets` mode, `--no-fallback`, max repair attempts, output directory), not a subset. Added a
"recent runs" panel (`GET /api/runs`, scans `runs/` for any dir with `scene.json`) as a
complementary feature so the GUI is useful for browsing prior work too, not just kicking off new
jobs -- confirmed live it correctly picked up every run from earlier in this session, not just
GUI-created ones, since it's reading the real `runs/` tree.

`flask` is a new optional dependency (`pip install infinienv[gui]`), lazily imported inside
`gui/app.py` and only actually needed by `infinienv gui` -- consistent with how every other
optional dependency in this project is handled (`openai-agents`, `openai`, `anthropic`). Path
traversal on the artifact-serving route is guarded the same way `artifacts/writer.py` guards
output directories (`os.path.commonpath` check against cwd).

Verified two ways: `tests/test_gui.py` (6 cases) uses Flask's test client with the `mock`
provider -- including a full real SSE stream consumed and parsed event-by-event (not just
checking the route returns 200), and a path-traversal rejection test. Then a genuine live smoke
test: started the actual server as a background process, hit it with real `curl`/SSE against both
`mock` (fast) and `openai_agents` (real API call -- confirmed the `: keep-alive` lines actually
appear during the real network wait, and the run validated on the first try, rendered correctly,
and appeared in `/api/runs` afterward). Cleaned up the test run directories and killed the test
server before finishing.

## Sandbox agents: from "declined twice" to an explicit, disclosed exception

This project declined sandboxed model-authored code execution twice earlier in this session, on
correctness/determinism grounds (see the extended-mechanics entry above -- the declarative effect
system was built specifically as the non-code-execution answer to "let the model define real
behavior"). It came up a third time from a real, concrete gap: a generated museum/friend scene
had a girl and a boy NPC that were supposed to chase the agent, and did not, because no chasing
primitive existed anywhere in the engine. My first response was to scope a hand-built pymunk
chase/catch primitive. Mid-implementation (pymunk added to `pyproject.toml`, a `behavior` field
partially added to the schema), the user corrected the scope directly: "caught by npc is just one
case i had in mind, it could be any condition set by a user, i want the sandboxes, update the plan
to allow sandboxes to code the game from our basis and edit everything too." This wasn't a request
for a bigger fixed vocabulary -- it was an explicit, informed ask for general model-authored code,
after having already seen (and accepted) the argument against it twice. I asked one clarifying
question on the highest-risk ambiguity (does the sandbox edit the real repo or an isolated copy?);
the answer was "isolated per-run copy," which is what got built. The partial pymunk-schema change
was cleanly reverted (confirmed via `git diff --stat` showing empty output) before starting the
new implementation, so nothing from the superseded approach lingered in the codebase.

### API surface: three real quirks only live testing found

`agents.sandbox` ships inside the already-installed `openai-agents` package -- no new dependency
for the agent-orchestration side (pymunk was still added as a `physics` extra, available *inside*
the sandbox if a mechanic needs real physics, not as a new hand-built engine primitive). Reading
the SDK's types suggested a fairly direct API; three things only surfaced by actually running it:

1. **Model requirement.** `gpt-4.1` fails the sandbox's tool schema outright (`Error code: 400 -
   Invalid value: 'custom'`). `gpt-5.5` works. No amount of reading the `SandboxAgent` signature
   would have surfaced this -- it's a mismatch between that model's tool-calling format and what
   the sandbox's tool definitions need.
2. **Hydration isn't automatic.** `LocalSnapshotSpec(base_path=...)` looks like it should mount a
   local directory as the sandbox's starting filesystem on `client.create(snapshot=...)`. In the
   installed SDK version it does not -- `session.start()` leaves the workspace empty
   (`session.ls('.')` returns `[]`). The actual mechanism is `session.hydrate_workspace(data:
   io.IOBase)`, which needs an explicitly-built tar of the source directory.
3. **`session.write()` wants `io.IOBase`, not raw bytes** -- passing `bytes` directly raises
   `AttributeError: 'bytes' object has no attribute 'read'`.

None of these were guessable from type signatures alone; they were found by writing a minimal
"write a file, read it back" script and iterating against the real error messages.

### A real bug in the audit trail itself, found by trying to verify the audit trail

The isolation design's stated promise is that the sandbox workspace is kept on disk (in
`runs/<id>/sandbox_workspace/`) as the audit trail substituting for the solvability guarantee this
mode gives up. First live run "succeeded" (both self-reported and outer-sanity-passed), and its
own summary claimed it added `navigation/chase.py`. Trying to actually inspect that file for the
notes.md writeup, it didn't exist anywhere in the kept workspace -- `grep -rl "chase"` across the
whole directory came back empty. The reason: `hydrate_workspace()` populates the sandbox backend's
*own* separate execution filesystem (a fresh temp directory), not the `sandbox_workspace/`
directory `build_workspace_dir` wrote to disk. The original `extract_artifacts()` only ever pulled
the five named artifact files back out -- it never synced the sandbox's actual final filesystem
state anywhere. So the "kept for audit" workspace was silently frozen at its pre-run state the
entire time, even on a run that genuinely worked. This is exactly the kind of thing this project's
testing standard exists to catch (live verification over trusting an offline assumption) -- it
just caught it in the harness's own audit-trail code instead of in application logic.

Fix: `session.persist_workspace()` (the read-side counterpart of `hydrate_workspace()`, returning
a tar of the sandbox's actual current filesystem) is called after the run and extracted over the
kept `sandbox_workspace/` directory, replacing the stale pre-run copy with the real final state.
Added a unit test (`test_sync_full_workspace_replaces_stale_copy_with_agent_edits`) that builds a
fake "agent final state" tree with an added file and a removed stale directory, and asserts the
synced-to-disk workspace matches it exactly, not the pre-run copy.

While re-verifying with the fix in place, a second real gap surfaced: a run whose agent
conversation didn't finish cleanly (hit the 40-turn budget on a more complex prompt) raised a bare
`ProviderError` that propagated straight past artifact extraction, workspace sync, and the outer
sanity check/metrics write -- so a run that had produced real partial work got reduced to one
stderr line at the CLI's top level, with no `metrics.json`, no synced workspace, nothing to
inspect. Fixed by capturing that failure as `run_error` inside `_run_async` instead of letting it
propagate: artifact extraction, workspace sync, and the outer sanity check all still run
regardless, `run_error` is folded into a failed outer verdict (`success` is always `false` if the
agent run itself failed, even if stray artifacts happen to exist), and the CLI prints it distinctly
from the agent's own summary rather than crashing past the rest of the report.

### Two live end-to-end runs: one failure mode, one clean success

Same chase/catch-mechanic task family the museum screenshot originally surfaced, run twice:

- **First run**, less directive prompt: the agent self-reported success, but had invented its own
  incompatible scene format instead of reusing the real `SceneSpec` schema. The outer sanity check
  correctly rejected it (`scene.json does not parse against the real schema`) -- exactly the
  failure mode this mode's outer check exists to catch. It also had the 43-byte truncated
  `replay.gif` described above, which the sanity check did not yet catch at the time (that check
  was added afterward -- see `outer_sanity_check`'s image-validity block and
  `test_outer_sanity_check_fails_for_truncated_replay_gif`).
- **Second run**, prompt made explicit about reusing the existing schema/mechanics extension point
  and only extending engine logic: the agent produced a real `SceneSpec` (grid/agent/objects/
  walls/goals, a `sequence` goal for open-door/grab-friend/deliver-friend,
  `mechanics.custom_object_types` declaring `friend`/`girl_npc`/`boy_npc`), and extended
  `navigation/policy.py` in place with a `solve_chase_scene` path dispatched off the scene's
  declared custom object types, plus real chase-stepping (`_npc_step`, greedy Manhattan-distance
  movement respecting walls/doors/other NPCs) and catch detection. Confirmed by diffing the
  *synced* workspace against this repo's actual `navigation/policy.py` -- not by trusting the
  agent's own summary, which is exactly the discipline the first run's audit-trail bug had been
  masking. `render.png` was inspected directly and is a real, correctly-legended render from this
  project's actual renderer (agent/key/door/friend/girl_npc/boy_npc/sink/table all present).
  `metrics.json` showed `sandbox_self_reported_success: true` and `outer_sanity_passed: true` in
  agreement.

Both runs are evidence for the same conclusion: the sandbox agent is capable of recognizing and
reusing this project's real, existing extension points (schema, `mechanics.custom_object_types`)
rather than always reinventing from scratch, but that's a property of how directively it's
prompted and how much turn budget it has, not a guarantee -- which is exactly why the outer sanity
check and the honest `run_error`/`sandbox_self_reported_success`/`outer_sanity_passed` fields
exist as real, independent signals rather than trusting the agent's self-report alone.

### What this does and doesn't change

Every existing guarantee (validator-wins, no-model-code-execution) still holds unconditionally for
every run that doesn't pass `--sandbox` -- this is additive, not a loosening of the default path.
CLAUDE.md sections 1, 2, 11 (new), and 17 were all updated to state the sandbox exception plainly
rather than let the document's existing "declined" language go stale and misleading. See CLAUDE.md
section 11 for the full design writeup (isolation boundary, what the outer sanity check does and
doesn't verify, explicit scope exclusions for this pass).

## Sandbox agents, round two: from "one shot, report failure" to self-repair

After the first sandbox-agents pass shipped, live-tested it against five prompts specifically
designed to stress `pymunk` physics (steering-force pursuit, momentum/pushable objects,
projectile arcs, collision ricochet, multi-body herding). The very first one reproduced the exact
failure mode already documented above: the agent invented its own pixel/world-coordinate scene
format (`world.walls`, `agent_start`, `mechanics.robot_force`) instead of the real `SceneSpec`,
correctly rejected by the outer sanity check. The user's response was direct: "it just shouldn't
fail, it should edit everything so it works, any prompt should create a working game."

This is not a request to loosen the outer check -- it's a request that a single failed attempt
not be the end of the story. The non-sandbox path already has exactly this shape of answer
(`generation/compiler.py`'s repair loop: generate, validate, and if invalid, hand the concrete
errors back to a RepairAgent, up to a budget). Built the same pattern for sandbox mode:
`sandbox/runner.py`'s `_run_async` now loops up to `max_repair_attempts + 1` times (default 2
extra, so 3 total; `INFINIENV_SANDBOX_MAX_REPAIR_ATTEMPTS` env override, `--max-repair-attempts`
CLI flag now applies to sandbox mode too instead of being ignored). Each iteration: run the agent,
extract artifacts, sync the workspace, run `outer_sanity_check`; if it fails and budget remains,
build a message describing exactly what failed (`_repair_message`) and start a fresh `Runner.run`
call. The sandbox *session* (filesystem) is the same object across attempts, so even though each
`Runner.run` call is a fresh conversation with no memory of the last attempt, the agent's actual
files are still on disk -- the repair prompt tells it to `ls`/`cat` and fix rather than starting
from zero. Every attempt is recorded in a new `repair_history` list in `metrics.json`, mirroring
`repair_history` in the non-sandbox path's `validation.json`.

Also tightened `sandbox_agent.md` itself, since the format-invention failure was partly a
prompting gap: it now explicitly says `scene.json` must load through the copied
`schema/scene_schema.py` (grid-based `x`/`y`, not pixels), that only the *static* layout needs to
be in that format (continuous physics state can live in the agent's own code, not in scene.json),
and tells the agent to actually run a self-check against the schema before declaring done. Re-run
after this prompt fix alone, the same prompt succeeded on the first attempt -- the repair loop's
control-flow correctness was verified separately via three mocked tests
(`tests/test_sandbox_runner.py`, fake `Runner.run`/session objects using the exact call shapes
already proven live in the first sandbox-agents pass) rather than by deliberately trying to induce
a live failure just to watch the retry fire, since that would burn real API budget for something
the mocks already cover deterministically.

### A second real "technically valid but not what it claims" bug, found from a user bug report

While reviewing a `SUCCESS`-labeled run's `replay.gif`, the user reported "the physics chase gif
is just blank." Not blank -- `PIL.Image.open(...).n_frames` was `1`. The file was a genuine,
correctly-sized, loadable PNG-in-GIF-clothing: it passed every check `outer_sanity_check` had
(exists, over the size floor, `Image.verify()` succeeds), because none of those checks ever looked
at frame count. This is the same *shape* of bug as the 43-byte truncated-GIF case from the first
pass (an artifact that satisfies "is a valid file" while failing "is what this file is supposed to
mean"), just a different specific gap. Fixed by re-opening `replay.gif` after the existing
per-file checks (can't reuse the same `Image` object post-`verify()`) and requiring
`n_frames >= 2`, with a regression test (`test_outer_sanity_check_fails_for_single_frame_replay_gif`)
and a `sandbox_agent.md` clarification that `replay.gif` must be a genuine multi-frame animation,
not just a valid image file.

Re-ran the original failing prompt (robot-chase-with-real-physics) after both fixes: succeeded on
the first attempt, with a genuine 56-frame `replay.gif`. Didn't just trust `success: true` --
extracted and visually inspected the first and last frames directly, confirming the blue agent
moves from spawn across the maze to the green exit while the red robot NPC trails behind on a
visible path line. This is the standard this project holds itself to for every non-trivial claim
of correctness (see the very first live-verification entries in this file): the artifact itself,
not the self-report about the artifact, is the thing that gets checked.

### Net effect

Neither fix loosens what `outer_sanity_check` guarantees -- if anything both fixes make it catch
more real failure classes than before (truncated file, now also non-animated file), and the
repair loop still requires that check to genuinely pass, it just gives the same agent more
chances against the same real bar. `success: true` in a sandbox run's `metrics.json` is a
strictly stronger claim after this round than before it, not a weaker one.
