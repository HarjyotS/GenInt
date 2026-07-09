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

## Sandbox mode gets real asset generation, plus a real import-isolation bug found and fixed along the way

The user asked for sandbox runs to be able to use `--assets` (`local`/`generated`/`auto`) like
every other run, instead of `--sandbox` silently ignoring it and always rendering flat colored
cells. Straightforward on its face -- copy `assets/` into the workspace, pass a mode through --
but investigating how to wire it up surfaced a real, previously-undetected correctness gap in the
isolation boundary section 11 claims.

### The bug: sandboxed modules were silently resolving imports to the real installed package

`infinienv` is installed editable (`pip install -e .`), so it's importable from any Python process
using this venv, regardless of `cwd`. Every file `build_workspace_dir` copies into
`sandbox_workspace/` (`engine/grid.py`, `validation/validator.py`, etc.) used the project's normal
absolute-import style, `from infinienv.engine.grid import Grid`. Inside the sandbox that import
doesn't fail or get redirected to the sandboxed copy sitting right next to it -- it silently
resolves to the *real installed* `infinienv.engine.grid`, because that module is on `sys.path`
regardless of what directory the sandboxed process runs from.

Concretely: if an agent edited its copy of `engine/grid.py` to change how walls are treated, and a
*different* copied module (say `navigation/astar.py`) still imported `from infinienv.engine.grid
import Grid`, that second module would keep using the real repo's unedited `Grid`, not the
agent's edit -- even though both files are sitting in the same `sandbox_workspace/` directory and
look, to a human inspecting the workspace, like a self-contained edited copy. This directly
contradicts CLAUDE.md section 11's claim that the agent "may read, edit, or add any file in that
copy -- including rewriting the engine itself." It's a correctness gap in what the mode delivers,
not a security/isolation-boundary breach -- the sandboxed process still can't write back to the
real repo's files, it just wasn't reliably reading its own.

Fixed with `_rewrite_internal_imports()` in `sandbox/workspace.py`: after copying files, walk
every `.py` file in the workspace and regex-rewrite `from infinienv.X import ...` /
`import infinienv.X` to `from X import ...` / `import X`, so cross-module references inside the
workspace resolve to the sandboxed copy next to them rather than the installed package. First
version of the regex only matched at column 0 (`^from infinienv\.`), which missed indented/lazy
imports -- e.g. `assets/resolver.py`'s `resolve_assets()` does `from assets.generator_openai import
generate_sprite` inside the function body, not at module level, to keep `generated`/`auto`'s
OpenAI dependency lazy. Fixed by allowing leading whitespace in the pattern
(`^(\s*)(from|import)\s+infinienv\.`).

Regression coverage added, not just a manual check: `test_build_workspace_dir_copy_is_actually_self_contained`
in `tests/test_sandbox_workspace.py` runs a real subprocess with `cwd` set to the built workspace
and asserts `engine.grid.Grid.__module__`'s `__file__` actually points at the sandboxed copy, not
site-packages -- the only way to catch this class of bug is to actually run a fresh process from
the workspace directory, since running the assertion in-process from the test itself would
share `sys.path`/`sys.modules` state with whatever already imported the real `infinienv` package
during test collection. Also added `test_build_workspace_dir_rewrites_internal_infinienv_imports`
covering the indented-import case specifically.

### The feature itself

- `_COPIED_PACKAGES` now includes `assets`; a new `_PARTIAL_COPIES` mechanism copies just
  `llm/base.py` (for `ProviderError`, which `assets/generator_openai.py` and `assets/resolver.py`
  both depend on) into a new `llm/` package inside the workspace, without pulling in the whole
  `llm` package (providers, prompts, the OpenAI Agents SDK dependency) that the sandbox has no use
  for.
- `build_workspace_dir(out_dir, assets_mode=...)` writes a plain-text `ASSETS_MODE` file at the
  workspace root. The reference `run_scene.py` template reads it and, if not `"none"`, calls
  `assets.resolver.resolve_assets(scene, assets_mode, os.path.abspath("asset_cache"))` and passes
  the resulting `asset_paths` into `save_render_png`/`save_replay_gif` -- the same pattern
  `evaluation/runner.py` already uses for the non-sandbox path. Asset cache is per-run
  (`./asset_cache` inside the workspace), not shared with the repo's real
  `.infinienv_asset_cache/` or across sandbox runs, consistent with the "no cross-run reuse in
  sandbox mode" precedent CLAUDE.md section 11 already documents for mechanics.
- `sandbox/runner.py::run_sandbox_generation`/`_run_async` take `assets_mode: str = "none"`,
  threaded into `build_workspace_dir` and appended to the agent's initial message (`Assets mode:
  {assets_mode}`) so the agent knows whether to lean on real sprites -- relevant if it rewrites
  `run_scene.py` itself for a custom simulation loop and needs to preserve the asset-resolution
  step manually.
- `cli.py`'s `_cmd_generate_sandbox` now passes `args.assets` through; `--sandbox`'s help text no
  longer claims to ignore `--assets` (only `--provider`/`--no-fallback` are still ignored, since
  sandbox mode has no LLM-repair-agent path or fallback-template path to apply them to).
- `sandbox_agent.md` gained a paragraph telling the agent about `ASSETS_MODE`, how to call
  `resolve_assets`, that the default `run_scene.py` already does this, and that `generated`/`auto`
  cost real API time per new object type -- don't request a mode switch, just honor whatever
  `ASSETS_MODE` says.

### Live verification

Ran `--sandbox --assets local` against a kitchen-delivery prompt: succeeded on the first attempt
(no repair needed). Verified real sprites were used, not flat colored cells, by cropping each
object's cell out of `render.png` and counting distinct pixel colors -- 30-80 distinct colors per
object cell (a flat-color fallback cell would show 1-2). The agent used the default `run_scene.py`
template completely unmodified (byte-identical apart from a trailing newline) and the
asset-resolution wiring worked correctly with zero agent-side effort, confirming the plumbing
integrates cleanly with the existing reference entrypoint rather than requiring every sandbox
agent to reimplement it. Also ran `--sandbox --assets generated` (prompt: "agent picks up a
glowing crystal and places it on a pedestal") to confirm real OpenAI Images API calls succeed from
inside the sandboxed process (relevant specifically because environment variable inheritance into
the sandbox backend was previously verified only by reading `unix_local.py`'s `os.environ.copy()`
call, never proven with a real `generated`-mode run) -- succeeded on the first attempt, no repair
needed. `sandbox_workspace/asset_cache/` contained five real generated PNGs (`agent.png`,
`glowing_crystal.png`, `package.png`, `pedestal.png`, `wall.png`, 2-8KB each), and cropping the
rendered object cells out of `render.png` showed 68 and 218 distinct colors respectively (vs. 1-2
for a flat fallback), confirming genuine generated art rather than a silent local-placeholder or
flat-color fallback. `replay.gif` was a real 5-frame animation. `metrics.json` recorded
`"outer_sanity_passed": true` and `"sandbox_self_reported_success": true` in agreement, with
`repair_attempts: 0`.

### GUI: expose --assets in sandbox mode too

The GUI's `--assets` `<select>` lived inside the `#non-sandbox-fields` `<fieldset>`, which the
frontend JS disables whenever the sandbox checkbox is on (`syncSandboxUi()`). That predates this
round's backend change and meant a GUI user could never actually reach `generated`/`local`/`auto`
for a sandbox run even after `sandbox/runner.py` learned to honor `assets_mode` -- the field was
locked to `"none"` by the disabled attribute. Moved the `<select id="assets">` out of that
fieldset (provider and `--no-fallback` stay inside it, since those genuinely don't apply to
sandbox runs) and updated the sandbox note's copy accordingly. `gui/app.py::api_generate` now
computes `assets_mode` once, before branching on `sandbox`, and passes it to both
`_run_job`/`_run_sandbox_job` instead of only the non-sandbox path. `_run_sandbox_job` takes and
forwards `assets_mode` to `run_sandbox_generation`, mirroring the CLI's existing wiring. Covered
by `test_sandbox_generate_flow_threads_assets_mode_through` in `tests/test_gui.py`.

### A third "technically valid but not what it claims" bug in outer_sanity_check, found from a user report

User report: "gui_1783609484 run failed replay." That run's `metrics.json` said
`"success": true`, `"outer_sanity_passed": true`, `"outer_sanity_error": null` -- the outer check
had signed off on it. Investigated the actual `replay.gif` (a physics-chase scene, `used_physics:
true`, 59 frames per its own custom `metrics.json` fields -- this run predates the standard
schema and used the agent's own physics-simulation `run_scene.py`, not the default template).
`PIL.Image.open(...).verify()` passed and `.n_frames` reported 59, exactly what
`outer_sanity_check` already checked for. But actually decoding any frame
(`img.seek(0); img.load()`) raised `OSError: broken data stream when reading image file` --
confirmed independently with `ffmpeg -i replay.gif -f null -`, which reported `LZW decode failed`
on every one of the 59 frames.

Root cause: PIL's GIF `verify()` validates *container* structure (headers, block/sub-block
length prefixes, trailer) — it does not run the LZW decoder over the pixel payload. Likewise
`n_frames` is derived by seeking through frame descriptors, not by decoding them. A GIF can
therefore have a perfectly well-formed header, correct frame count, and valid trailer while every
single frame's actual pixel data is garbage — exactly the file this sandbox agent produced. This
is the same *shape* of bug as the two earlier `outer_sanity_check` gaps (43-byte truncated GIF;
technically-valid single-frame GIF) — a check that verifies "is this a valid file" without
verifying "is this file what it's supposed to mean" — just a third, more subtle instance: this
time the file *was* multi-frame and *did* pass `verify()`, so both of the previous fixes were
insufficient on their own.

Fixed in `sandbox/workspace.py::outer_sanity_check`: after `verify()` passes for `render.png`/
`replay.gif`, both now get a second pass that re-opens the file fresh and calls `.load()` for
real (verify() leaves the image object unusable for further reads). For `replay.gif` specifically,
after the frame-count check, every individual frame is seeked to and loaded
(`for i in range(n_frames): gif.seek(i); gif.load()`), not just frame 0 — the real corrupted file
had all 59 frames corrupted, but a version of this fix that only checked frame 0 would already
have been sufficient for this case; checking every frame is the more defensible bar since nothing
guarantees corruption is uniform across frames.

Regression test: `test_outer_sanity_check_fails_for_lzw_corrupted_replay_gif` in
`tests/test_sandbox_workspace.py`, with a purpose-built `_make_lzw_corrupted_gif()` helper that
constructs a real 2-frame animated GIF via PIL, then walks its actual block structure (extension
blocks, image descriptor, LZW sub-blocks) and XORs only the sub-block *payload* bytes with
`0xFF` — never touching length-prefix bytes, the block terminator, or any header -- so the
resulting file keeps passing `verify()`/`n_frames` (asserted directly in the test) while its pixel
data is genuinely undecodable, the same failure shape as the real file. Deliberately not using the
real 4.8MB corrupted file as a fixture -- a minimal synthetic repro that exercises the identical
code path is more maintainable and doesn't bloat the repo with a large binary.

The existing, since-superseded run (`runs/gui_1783609484/metrics.json`) was retroactively
corrected to `"success": false"`, `"outer_sanity_passed": false` with an `_note` explaining why,
rather than left with a false `"success": true"` in the repo now that the real verdict is known --
consistent with this project's standing rule that `metrics.json` must never claim success it
can't back up.

### Net effect

Same as the previous two `outer_sanity_check` fixes: this doesn't loosen the guarantee, it closes
a real gap in it. `success: true` in a sandbox run's `metrics.json` is now backed by an outer
check that actually decodes every pixel of every artifact frame, not just its container
structure.

## Sandbox mode: live narration of what the agent is doing, not just attempt-boundary progress

User feedback on the GUI (screenshot of a sandbox run mid-flight showing only "Submitting job...
/ Preparing isolated sandbox workspace... / Running sandbox agent (attempt 1/3)..." for the whole
run): "the frontend has to have more updates about what the sandbox agent is doing, the changes
and decisions the sandbox agent does should be listed on the frontend, you dont need to do a
diff." Two explicit asks: (1) real visibility into the agent's actions and decisions while it
runs, not just a static "running..." message for the whole conversation, and (2) no diff content
-- file names and the fact that a file was touched, not the hunk.

### What was available and how it was found

`sandbox/runner.py` was driving the agent with `Runner.run(agent, message, run_config=...,
max_turns=...)` -- a single `await` that blocks until the whole conversation finishes and returns
only `result.final_output`. Investigated the `openai-agents` SDK's `Runner` class
(`agents/run.py`) and found `Runner.run_streamed(...)`, which returns a `RunResultStreaming`
immediately and exposes `.stream_events()` as an async generator of `StreamEvent`s
(`agents/stream_events.py`) as the conversation actually happens -- `RunItemStreamEvent`s in
particular (`tool_called`, `tool_output`, `reasoning_item_created`, `message_output_created`,
among others) are exactly the granularity needed: every shell command the agent runs, every file
edit, its own reasoning summaries and intermediate messages, as they occur.

Traced what the sandbox's two capabilities (`Filesystem()`, `Shell()`, from
`agents/sandbox/capabilities/`) actually produce as tool calls, since that's what determines the
shape of `tool_called`/`tool_output` items:
- `Shell()` exposes `exec_command`, a plain `FunctionTool` (`agents/sandbox/capabilities/tools/
  shell_tool.py`) whose arguments are `{"cmd": ..., "workdir": ..., ...}` (JSON) and whose
  output is a formatted string containing `Process exited with code N` and an `Output:` section
  -- both cheap to parse without needing any SDK types imported.
- `Filesystem()` exposes `apply_patch`, a `CustomTool` (`agents/sandbox/capabilities/tools/
  apply_patch_tool.py`) using a Codex-style patch grammar (`*** Begin Patch` / `*** Add File: x`
  / `*** Update File: x` / `*** Delete File: x` / hunks / `*** End Patch`). Its raw *input* is the
  full patch text including hunk content -- exactly the diff the user said not to show. But its
  **output** (`ApplyPatchResult.output`, e.g. `"Updated navigation/policy.py"`) is already a
  clean one-line-per-file summary with zero diff content, produced by the SDK itself. Decided to
  parse the *file list* out of the *call's* patch headers (regex over `*** Add/Update/Delete
  File: <path>` lines only, never the hunk lines) at `tool_called` time for immediate feedback,
  and stay silent on the corresponding `tool_output` to avoid double-announcing the same edit.

### Implementation

`sandbox/runner.py` gained `_describe_stream_event(event)` and per-item-type helpers
(`_describe_tool_called`, `_describe_tool_output`, `_describe_reasoning`, `_describe_message`).
Deliberately **duck-typed** -- every helper reads attributes via `getattr`/dict access rather than
importing and `isinstance`-checking against `agents.items`/`agents.stream_events` classes, and the
top-level dispatcher wraps the whole thing in a `try/except Exception: return None`. Two reasons:
(1) keeps this project's existing lazy-import discipline for optional/heavy dependencies (the
`agents` package still isn't imported at module scope anywhere in this file); (2) narration is
commentary on top of a real run, not something the run's correctness depends on, so a future SDK
version changing an item's internal shape should silently produce less-detailed narration, never
crash an otherwise-successful generation.

`_run_async`'s attempt loop now does:
```python
streamed = Runner.run_streamed(agent, message, run_config=run_config, max_turns=max_turns)
async for event in streamed.stream_events():
    narration = _describe_stream_event(event)
    if narration:
        stage(narration)
agent_summary = streamed.final_output
```
in place of the old single `await Runner.run(...)`. No changes needed to `cli.py` or
`gui/app.py`/`templates/index.html` -- both already treat every `on_stage` call as one more line
to print/append, so the new, much higher-frequency narration messages just flow through the exact
same pipe the coarse attempt-boundary messages always used.

### Testing

Since the narration helpers are pure duck-typed functions, they're unit-tested directly against
`SimpleNamespace` stand-ins for real SDK item shapes in `tests/test_sandbox_runner.py`
(`TestDescribeStreamEvent`, 12 cases) with no dependency on the optional `agents` package being
installed -- covers: shell command display, unparseable-arguments fallback, apply_patch file-list
extraction (with an explicit assertion that hunk lines like `-old`/`+new` never appear in the
narration string), unknown-tool fallback, failed vs. successful shell command output, apply_patch
output staying silent, reasoning/message surfacing, non-run-item events being ignored, and a
malformed item not raising. A separate integration test
(`test_sandbox_run_streams_agent_narration_through_on_stage`) drives the full repair-loop
machinery with a fake `Runner.run_streamed` that emits a real sequence of `RunItemStreamEvent`s
and asserts the resulting `on_stage` calls contain the expected narration lines. The existing
repair-loop tests (bad-attempt-then-repair, budget-exhausted, immediate-success, assets-mode
threading) needed `Runner.run` → `Runner.run_streamed` re-wiring since the entrypoint changed, via
a small `_FakeStreamedResult`/`_streamed()` adapter that wraps the tests' existing `fake_run`
coroutines -- their actual bodies (writing fake session files, asserting on `attempts`/`result`)
didn't need to change at all.

Live-verified against the real API (`--sandbox`, kitchen-delivery prompt): the CLI output showed
real shell commands (`$ ls && find .. -name AGENTS.md -print`), the agent's own narrated reasoning
as it worked through a real environment problem (its workspace's default `python3` was a blocked
venv interpreter -- "Agent: Python is picking a blocked venv; I'll rerun with system isolation
disabled." followed by it trying several interpreter paths until one worked), and failed-command
detail lines with real exit codes, all interleaved live before the final summary -- confirming
this is genuine narration of the actual run, not something synthesized after the fact from the
finished artifacts. Run still completed successfully (`outer_sanity_passed: true`,
`repair_attempts: 0`), confirming the switch from `Runner.run` to `Runner.run_streamed` didn't
change the underlying agent behavior or the repair loop's correctness.

## Narration surfaces a real problem: the sandbox agent can't reliably find pymunk

Shipping narration immediately paid for itself: the user pasted a live narration transcript from
a physics-chase prompt and flagged "the agents havign trouble finding pymunk" -- something that
was always happening, but was invisible before this session's narration work landed, buried
inside a single opaque "Running sandbox agent..." stage message.

### Diagnosis

The transcript showed the agent running `python -S make_env.py`, setting `PYTHONHOME=`,
`PYTHONNOUSERSITE=1`, trying `/usr/bin/python3`, Homebrew's `python@3.14`, framework Python 3.11,
searching `.venv` with a `find -maxdepth 4` that was too shallow to actually reach
`.venv/lib/python3.14/site-packages/pymunk` (real depth 5), concluding pymunk wasn't available,
and falling back to hand-rolled force-based physics. Root cause: `sandbox_agent.md` said "pymunk
is available" but never told the agent *which* Python interpreter to actually invoke, so it had
no way to know which of several interpreters on the host had this project's dependencies and
blindly explored.

Confirmed directly (not assumed) that `sys.executable` -- the interpreter running the harness
itself -- has pymunk, and that a subprocess invoking it with `env = os.environ.copy()` (exactly
what `UnixLocalSandboxClient` does for every `exec_command`) from a different `cwd` successfully
imports it. Fix, round one: added `sandbox/runner.py::_interpreter_briefing()`, which checks
`pymunk` importability at runtime (not hardcoded, since it's an optional `physics` extra) and
appends a fact to the agent's initial message: `Python interpreter: <sys.executable> (pymunk is
installed and importable in it)`, plus instructions not to pass `-S`/touch
`PYTHONHOME`/`PYTHONPATH`/`PYTHONNOUSERSITE`, and not to hunt for other interpreters. Mirrored in
`sandbox_agent.md` as a standing instruction. Regression tests added
(`test_interpreter_briefing_names_the_harness_interpreter_and_forbids_hunting`,
`test_interpreter_briefing_reports_real_pymunk_availability`,
`test_initial_message_tells_the_agent_which_python_interpreter_to_use`).

### Round one wasn't enough -- live-verified, and it wasn't

Re-ran the same physics-chase prompt live. The agent's *first* Python command was still bare
`python - <<'PY'` (not the absolute path given to it) and hit `Fatal Python error:
init_import_site: Failed to import the site module`. Investigated the actual mechanism rather
than guessing further: `agents/sandbox/session/base_sandbox_session.py::_prepare_exec_command`
wraps every `exec_command` call as `["sh", "-lc", <command>]` -- a **login shell**. On macOS, a
login shell re-runs `/usr/libexec/path_helper` on every single invocation, which rewrites `PATH`
from `/etc/paths` + `/etc/paths.d/*` -- reproduced directly: running the exact same command
through a fresh `sh -lc` login shell (mimicking the SDK's real call) showed `PATH` reordered with
`/Library/Frameworks/Python.framework/Versions/3.11/bin` ahead of `.venv/bin`, so a bare
`python`/`python3` can resolve to a completely different, dependency-less interpreter than the
one the agent was told about, even though `os.environ` itself (including `VIRTUAL_ENV`) was
faithfully inherited. This is not a bug in this project's code or in the SDK -- it's a real
interaction between "how login shells work on macOS" and "the agent using a bare interpreter name
instead of the absolute path it was explicitly given."

The agent's second self-inflicted problem compounded the first: after the bare-`python` failure,
it started setting `PYTHONHOME=` (empty string) before retrying -- including on the *correct*
absolute venv path. An empty `PYTHONHOME` is not equivalent to unset; it's a real, broken
override that reliably reproduces the exact same `Fatal Python error: init_import_site` on any
interpreter, absolute path or not. Confirmed directly: the absolute interpreter path, invoked via
`asyncio.create_subprocess_exec("sh", "-lc", "<absolute path> ...")` (the literal call the SDK
makes) with a genuinely unmodified environment, works every time, no site-import error, pymunk
importable.

### Round two: explain the mechanism, not just the rule -- still not enough

Rewrote `_interpreter_briefing()` and `sandbox_agent.md` to explain *why*, not just assert a rule
to follow: shell commands run as a login shell that reorders `PATH` on every command, so a bare
interpreter name is unreliable regardless of how correctly the environment was inherited, while
the absolute path bypasses that entirely; and that `PYTHONHOME=` (empty) is a real crash-inducing
override, not a no-op. Also gave the agent a direct diagnostic shortcut: if a command fails with
`Fatal Python error: init_import_site` or a missing-module error, the fix is "re-run with the
exact absolute path and no env changes," not "conclude the interpreter is broken and go looking
for another one."

Re-ran the same prompt/seed a third time. This time the agent *did* follow the instructions
precisely -- its very first Python command used the exact absolute path, no `-S`, no touched env
vars. It still hit `Fatal Python error: init_import_site: Failed to import the site module`. This
ruled out "the agent isn't following instructions" as the remaining explanation -- something was
genuinely, structurally broken about that interpreter inside the sandbox, no matter how it was
invoked.

### Round three: the actual root cause -- macOS Seatbelt confinement, not agent behavior

Stopped guessing and read `agents/sandbox/sandboxes/unix_local.py::_confined_exec_command`
directly. On macOS, **every** `exec_command` call is wrapped in `sandbox-exec -p <profile> ...`
-- a real Seatbelt confinement profile, not just a workspace-directory convention. The generated
profile (`_darwin_exec_profile`) explicitly denies `file-read-data` for broad roots including
`/Users` (i.e. every real user's home directory on the machine), then re-allows a narrow,
hand-picked set: the ephemeral workspace root, `/usr/bin`, `/usr/lib`, `/bin`, `/System`, and --
via `_darwin_additional_read_paths` -- the *specific* directories a given command's executable
and `PATH` entries resolve to (plus special-cased broad allows for `/opt/homebrew`, `/usr/local`,
`/Library/Frameworks`, and paths under the *real* `$HOME`'s `Library/Python`).

For our project's `.venv` at `/Users/<user>/GenInt/.venv`: `_darwin_additional_read_paths` only
adds an allow-rule for the *executable's own containing directory* (`.venv/bin/`, since
`shutil.which()` resolves to a file, and only its `.parent` gets added) -- it never adds
`.venv/lib/python3.14/site-packages/`, where every third-party dependency (pymunk, pydantic,
Pillow) actually lives, because that's a different subdirectory under the still-denied `/Users`
root. `.venv/bin/python` happens to be a symlink resolving into `/opt/homebrew/Cellar/...`
(already broadly allowed), which is why the interpreter's own *stdlib* is reachable and it gets
as far as trying to import `site` -- but `site.py`'s venv-detection logic
(`<frozen site>, line 623, in venv`) needs to read `.venv/pyvenv.cfg`, which is *not* reachable,
producing `PermissionError: [Errno 1] Operation not permitted: '<venv>/pyvenv.cfg'` deep inside
frozen `importlib`/`site` machinery -- surfacing as the generic, confusing `Fatal Python error:
init_import_site` the agent (and I) had been staring at.

Reproduced this from scratch, directly against the SDK's own real profile-generation code (not a
guess): built a `Manifest`/session via `UnixLocalSandboxClient`, called the session's actual
`_darwin_exec_profile`/`_darwin_additional_read_paths` methods to get the *real* profile string,
then ran `sandbox-exec -p <that profile> <venv python> -c "import pymunk, pydantic, PIL"` by hand
-- reproduced the exact same `PermissionError: ... pyvenv.cfg` failure. This also explained why
non-physics sandbox runs had been succeeding all along: they happened to end up invoking
`/Library/Frameworks/Python.framework/Versions/3.11/bin/python3` (broadly allowed via the
`/Library/Frameworks` special-case) which, on this host, happens to have `pydantic`/`Pillow`
installed globally -- but never `pymunk`, which only exists in the project's own `.venv`. No
amount of prompt engineering about *which* interpreter to use could have fixed this: the one
interpreter with the right dependencies was structurally unable to read its own files.

**The fix**: `Manifest.extra_path_grants` accepts extra absolute paths to allow, independent of
the workspace root -- exactly the mechanism this needed. `sandbox/runner.py::_run_async` now
constructs `Manifest(extra_path_grants=(SandboxPathGrant(path=sys.prefix, read_only=True, ...),))`
and passes it to `client.create(manifest=manifest)`, granting read-only access to the harness's
own Python prefix (the project `.venv`, or wherever `pip install infinienv[physics]` was run) for
every sandboxed shell command. Verified against the same from-scratch repro harness: with the
grant, the identical `sandbox-exec`-wrapped command succeeds and imports `pymunk`/`pydantic`/`PIL`
cleanly. `sys.prefix` (not `sys.base_prefix`) is what needs granting -- the base install's stdlib
is already reachable via the `/opt/homebrew` symlink-resolution special-case; it's specifically
the venv's own `pyvenv.cfg`/`lib/site-packages` that were unreachable. Read-only, and scoped to
just the Python prefix (not the whole repo), so this doesn't expose `.env` or anything else in the
project root to the sandboxed shell.

Regression test: `test_session_is_created_with_a_read_only_grant_for_the_harness_python_prefix` in
`tests/test_sandbox_runner.py`, asserting the `Manifest` passed to `client.create()` carries
exactly one `SandboxPathGrant` for `sys.prefix`, read-only.

### Live-verified: the actual fix, not just a plausible one

Ran the identical prompt and seed a fourth time, now with the `extra_path_grants` fix in place.
The agent used the exact absolute interpreter path throughout, hit zero `Fatal Python error`
crashes, and its `metrics.json` records `"physics": "pymunk"` -- confirmed genuine, not
self-reported, by grepping the synced `sandbox_workspace/run_scene.py` for real `pymunk.Space`/
`pymunk.Body`/`pymunk.Circle`/`pymunk.Segment` usage (present, not a fallback stub). Every one of
the agent's remaining tool calls went to actual task iteration -- fixing a real pymunk API
mismatch, tuning steering-force parameters, repositioning the maze exit until the agent could
genuinely escape the robot in the required number of steps -- rather than fighting the
environment. `outer_sanity_passed: true`, `repair_attempts: 0`, first attempt.

This took three live-verification rounds to actually land, not one -- each round's fix looked
complete after its own live run's narration made the *next* failure visible, which is exactly why
narration was worth building this session in the first place: the version of this bug from before
narration existed was invisible, reported only as "the agent chose force-based fallback physics"
with no way for a user or this project to tell that was a workaround for a real, fixable
plumbing bug rather than a legitimate design choice. Consistent with this project's standing
practice of live-verifying anything touching a provider/agent, not trusting that a
plausible-looking fix actually holds.

## Deterministic grid-physics: pushable objects + sliding (a first-class engine primitive)

Prompted by a user question: watching a `--sandbox` run rewrite `run_scene.py` to build a pymunk
simulation, they asked whether that's the normal path and said "we should have a physics engine
for most stuff... but it should still look great." The brief asks for "environments in a game or
physics engine," and physics was only reachable via `--sandbox` (where the model reinvents it each
run and the validator-wins guarantee is already traded away). Asked how physics should relate to
the default path, the user chose "build a first-class, deterministic physics primitive into the
main engine" (over auto-routing physics prompts to sandbox, or leaving it opt-in).

### The hard constraint that shaped the whole design

Continuous, force-based physics (pymunk-style smooth motion) is fundamentally incompatible with
two things this project guarantees: (1) the validator-wins *solvability* guarantee rests on an A*
+ symbolic planner that can't verify continuous dynamics, and (2) the entire engine is integer-grid
(`GameState.agent_x: int`, `ObjectState.x/y: int`, `Grid` is `tuple[int,int]`, the renderer places
cells at integer pixel boxes) — continuous motion needs floats everywhere. So "a deterministic
physics primitive in the main engine" can only mean *discrete grid-physics* (push, slide) that the
solver can actually simulate and verify. Continuous physics stays in `--sandbox`, by construction,
not by preference. Surfaced this fork explicitly to the user before building, and scoped v1 to a
"push + slide" vocabulary (their pick over push-only, slide-only, or a broader gravity/projectile
set).

### What was built (modeled on the existing extended-mechanics system)

- Schema: `SceneObject.pushable`/`slippery` bool flags + a `PushGoal` (`type: "push"`, object_id,
  target_id) added to the goal union and `GOAL_TYPES`.
- `engine/physics.py` (new, parallel to `engine/interactions.py`): `try_push` (shove one cell, or
  slide until blocked if slippery) and *live* collision helpers (`cell_blocked`,
  `solid_blocker_at`, `pushable_at`). Live collision was the subtle part: the `Grid` is static
  (built once, records only the initial solid layout), so once a pushable object moves the grid is
  stale — it would still block the object's *original* cell and wave the agent through the cell it
  moved *into*. So collision for movement is computed from current object positions, with walls
  still from the grid. For a scene with no pushables this gives identical blocking to the old
  static check, so existing scenes are unaffected (verified: full suite green).
- `engine/actions.py`: the `move_*` branch now checks `pushable_at` first and shoves instead of
  blocking.
- `navigation/planner.py::_plan_push`: BFS over the joint (agent, box) state, each transition
  simulating the exact same push/slide rule the engine applies, so the emitted moves are
  guaranteed to reproduce the pushes on execution. Single-box (other solids are static obstacles);
  bounded by a node cap so a pathological scene reports unsolvable rather than hanging. `plan_goal`
  / `is_goal_complete` get `"push"` branches. `policy.py` needed no change (it dispatches
  generically), so push `goal_results` and `programmatic_reward` come for free.
- `validation/validator.py`: `_iter_goal_refs` covers push refs; a new `PHYSICS_NOT_PUSHABLE`
  rejects a push goal whose object isn't pushable; and the reachability pre-check treats pushable
  objects as *optimistically passable* (like unlocked doors) — a crate walling a corridor can be
  shoved aside, so it mustn't be a permanent `UNREACHABLE`. Real solvability (the extended solver)
  is still the authoritative gate.
- `render/replay_export.py`: a slippery push moves an object several cells in one action, which
  would render as a teleport. `build_replay_frames` now detects a >1-cell object move and inserts
  per-cell intermediate frames, so slides read as smooth gliding — the "look great" half of the
  request. Visually confirmed by extracting first/mid/last frames of a real slide GIF.
- `generation/templates.py`: a `push_slide_puzzle` mock template (shove a slippery puck into a
  wall-adjacent plate, always solvable by construction) so `--provider mock` exercises physics
  offline/in CI; routed by push/slide/ice/crate/... keywords.
- Prompt (`scene_planner.md`) gained a "Physics" section teaching the flags + push goal with a
  worked example, and `examples/push_slide_demo.json` is a hand-authored valid instance.

### A nice property the determinism buys

A slippery object can only come to rest against an obstacle. So "push the puck onto a mid-floor
plate" is *genuinely impossible*, and the deterministic solver reports it as `UNSOLVABLE` rather
than pretending — a real, checkable physics constraint, not flavor. Covered by a test
(`test_slippery_push_to_a_mid_floor_target_is_unsolvable`).

### Verification

21 new tests (`test_physics.py` for the engine, `test_replay_export.py` for slide interpolation,
plus additions to schema/validator/solver/mock-generation/CLI): 113 -> 134 passing, no
regressions. CLI smoke test produced a valid push scene with a 12-frame animated slide GIF (all
frames decode). Live-verified first-try with the real `openai_agents` provider on a prompt with no
hand-authored precedent ("push a heavy crate onto a floor switch, then reach the exit"): the model
produced a valid, solvable `push` + `reach` scene with a `pushable: true` crate on its own,
validation passed, solver succeeded in 14 actions, zero repairs — confirming the model picks up
the new vocabulary from the prompt and generalizes it. Final replay frame visually confirmed the
crate resting on the switch and the agent at the exit.

### Invariants held

No trade-off taken: the solver plans pushes and the validator verifies them, so the validator-wins
guarantee is fully intact (unlike `--sandbox`). This is exactly the "extend by adding new
deterministic primitives" path from section 2 — real code with real tests, not a loosening of the
two core rules.

## Sandbox mode: agents were faking gameplay with pre-baked animations, not simulating it

A user reported a `--sandbox` run (a Mario-style "rescue the princess from a tower, avoid moving
turtles" game) where the replay showed the hero walking straight over the turtles and up to the
tower with no ladder, and a health bar that never did anything, despite `metrics.json`
self-reporting success and the outer sanity check passing.

### Diagnosis

Read the actual synced `run_scene.py` from the run in question (`runs/gui_1783617739`). The bug:
`hero_position(frame, total_frames)` computed the hero's position as a **hardcoded list of
waypoints interpolated with smoothstep easing** -- a pure function of frame index, with no
dependency on prior state. Turtles were the same trick, a sine-lane oscillation. "Collision
avoidance" was checked *after the fact*: a distance formula between the two already-decided,
independent paths. This is not a simulation, it's an animation of an outcome picked in advance --
it enforces nothing, because nothing is ever evaluated during "play." `metrics.json`'s own
`"source": "custom_smooth_motion_sim"` was the agent honestly naming what it built.

`outer_sanity_check` correctly passed this run: `scene.json` parsed, the images were real,
`replay.gif` had genuinely different frames. It has no way to know those frames came from stepping
real game state versus a lookup table -- judging that is exactly the kind of semantic-mechanics
check section 11's own scope notes already rule out as unachievable without reintroducing the
fixed-vocabulary constraint sandbox mode exists to escape. This is a structural blind spot in the
outer check, not an oversight to patch there.

### The fix: two new sections in `sandbox_agent.md`

Asked the user how to actually fix this class of bug; they picked "harden the prompt, and add a
step where the agent looks at its own gameplay and judges quality" over an outer-check-side fix
(consistent with the diagnosis above -- there isn't a code-side fix available that doesn't
reintroduce the fixed-vocabulary constraint).

**"Simulate, don't animate"** names the exact anti-pattern (position as a pure function of frame
index; success/collision computed as a post-hoc geometric check against a pre-decided path) and
gives a concrete self-test: "if you can compute frame 50 without having stepped frames 0-49 in
order, you built an animation, not a simulation." Requires instead a real `state = step(state,
dt)` loop where collisions, hazard-contact health loss, and structure-gated movement (a declared
ladder cell required to traverse a column, etc.) are resolved from *current* state every frame,
and requires writing the actual rules down first (a `RULES.md` or comment block) before
implementing them.

**"Before you finish, look at your own gameplay"** requires the agent to extract several
representative frames from its own `replay.gif` (start, a hazard-proximity moment, any
rule-triggering moment, the end) and actually call the sandbox's built-in `view_image` tool (from
the `Filesystem()` capability -- confirmed it exists and takes a workspace-relative image path,
returning a real multimodal image the model can see) on each of them plus `render.png`, reasoning
explicitly about whether what's depicted is consistent with the declared rules -- and, if not,
fixing the simulation and re-rendering rather than adjusting a threshold or the reported `success`
value to make a check pass.

### Live verification found the fix worked -- and found a new regression

Re-ran the exact same prompt/seed. The narration showed the new behavior working as intended: "The
simulation now succeeds" (implying it caught and fixed a problem before finishing), four
`view_image` calls narrated as "Viewing an image it produced...", and a final summary explicitly
stating the enforced rules and that the visual self-review matched them.

Verified this wasn't just a better self-report by inspecting the actual `replay.json` trace data
(not just trusting the summary): `lives` genuinely drops from 3 to 2 partway through, and the exact
frame it drops at has the agent 0.98 grid units from `turtle_3` -- a real, state-driven consequence
of hazard proximity, not an arbitrary scripted value. Turtle velocities (`vx`) flip sign between
frames, consistent with bouncing off lane bounds (stateful, not derivable from frame index alone).
Extracted and visually inspected replay frames: a real "Lives: 3" / "Lives: 2" HUD that actually
changes, matching the trace. A dramatic improvement over the original fixed-waypoint fake.

One new regression, though: the agent's own workspace cleanup (`rm -f review_*.png RULES.md
run_scene.py`) deleted its own implementation code and rules doc along with the temporary review
PNGs -- meaning the actual simulation logic, the thing this whole fix was trying to make provably
real, was gone from the kept workspace. It over-interpreted "clean up temporary files" from the
self-review instructions to include files that were never temporary. Fixed with an explicit
carve-out in `sandbox_agent.md`: temporary review PNGs may be deleted, but implementation code and
`RULES.md` must never be, since that code *is* this run's audit trail -- the only way anyone can
later confirm a simulation is real rather than trust the agent's word for it. Re-ran the same
prompt/seed again after this fix to confirm `run_scene.py` survives in the synced workspace this
time.

Second run: `run_scene.py` and `RULES.md` both survived in the synced workspace this time. The
agent's summary explicitly named the fix to the original bug report ("ladder-only climbing") --
`RULES.md` reads "Solid tower blocks and walls block movement; the player can climb only on ladder
cells. Turtles move smoothly across lanes and bounce at lane ends; touching one causes failure."
Read the actual code, not just the summary, to confirm it's genuine: `step(state, scene)` mutates
`state` in place every call -- turtle positions integrate velocity and bounce off declared
min/max lane bounds (stateful, not derivable from frame index alone), the agent's proposed next
position is checked against `blocked(scene, nx, ny)` (real wall lookup) before being applied, and
hazard contact is computed via live distance between the agent's *current* position and each
turtle's *current* position every step, setting a real `state['failed']` flag read directly by
`metrics.json`. The route toward the goal still follows a fixed waypoint list (a reasonable
"patrol route" design choice, not the original bug: unlike before, movement *toward* each waypoint
and every consequence along the way is genuinely computed per-step from live state, not baked in
advance). `metrics.json` correctly reports `"rescued": true, "failed": false` read off that real
end state.

### Net effect

The outer sanity check's guarantee is unchanged (still just structural well-formedness). What
changed is the *floor* on what agents are instructed to build and self-verify before finishing --
this doesn't add a new enforcement mechanism the harness runs, it raises the bar the model is
told to hold itself to, with a concrete, checkable self-test and a real tool (`view_image`) to
back it up rather than just a vague "make sure it's good" instruction.

## Asset generation: sequential -> concurrent, plus a wasted-quality default

User question: "why does asset generation take so long, how do we optimize it?" Read
`assets/resolver.py::resolve_assets` and `assets/generator_openai.py::generate_sprite`. Found two
real, unambiguous causes, not something more exotic:

1. `resolve_assets` generated sprites for every uncached type in a plain `for` loop -- each
   `generate_sprite` call is a blocking OpenAI Images API request, so N novel types meant N times
   one image's latency, fully serialized.
2. `generate_sprite` never set `quality` on the `images.generate()` call, so it rode gpt-image-1's
   default (`auto`, which resolves to a slow, high-effort render) -- even though every sprite gets
   resized down to 64x64 immediately after generation (`_crop_to_content` + `.resize((64, 64))`),
   so nothing about that extra quality survives to the final asset.

### Fix

`resolver.py` gained `_generate_many`, which dispatches every pending type's generation to a
bounded `ThreadPoolExecutor` (`DEFAULT_ASSET_CONCURRENCY = 4`, overridable via
`INFINIENV_ASSET_CONCURRENCY`) instead of a sequential loop -- these are independent, I/O-bound
calls, so running them concurrently drops wall-clock time toward the single slowest call instead
of their sum. Bounded rather than unbounded to stay polite to API rate limits on scenes with many
custom types. Cache-hit resolution (cheap, local filesystem checks) still happens synchronously
first, before dispatching only the genuinely-missing types to the pool. One type's generation
failure is isolated per-future and doesn't take down the others already in flight; the existing
per-type fallback/note behavior (`generated` mode -> `"none"`, `auto` mode -> local placeholder)
is unchanged, just faster to reach.

`generate_sprite` gained a `quality` parameter defaulting to `os.environ.get("INFINIENV_IMAGE_QUALITY",
"low")`, mirroring the existing `INFINIENV_IMAGE_MODEL` override pattern.

### Verification

9 new tests: `test_generate_sprite_defaults_to_low_quality` / `..._quality_overridable_via_env` /
`..._quality_kwarg_overrides_env` in `test_generator_openai.py`; and in `test_assets.py`, coverage
for cache-hit skip, per-type failure isolation, auto-mode local fallback, and -- the one that
actually proves the fix, not just exercises the code path --
`test_resolve_assets_generates_missing_types_concurrently`, which has each fake generation call
sleep 0.05s and track peak concurrent-calls-in-flight via a lock; asserts peak > 1 and total
elapsed well under the fully-sequential 5x0.05s, so a regression back to a sequential loop would
fail this test, not just look slower. `test_resolve_assets_concurrency_is_bounded_by_env_override`
confirms `INFINIENV_ASSET_CONCURRENCY=1` actually caps peak concurrency at 1.

Live-verified against the real API: 4 brand-new, never-generated-before custom object types
(`gizmo_widget`, `crystal_shard`, `ancient_scroll`, `copper_gear`, so no cache could mask the
result) resolved in ~16s total via `_generate_many` directly -- since all 4 fit within the
concurrency cap of 4, they ran fully in parallel, so ~16s is roughly one image's real latency, not
4x it. Visually inspected two of the generated sprites at `quality="low"`: clean, readable, no
visible quality loss at the 64x64 final size.

## Sandbox mode, round three: a real hitbox bug, and narration hiding the real reason why

User pasted a long narration transcript (the agent hand-editing a hardcoded controller via
`perl -0pi -e 's/.../.../'`, most edits reporting `command failed (exit 1): perl: warning: Setting
locale failed.`, dozens of turns, eventual success) plus a screenshot captioned "it hit the turtle
and nothing happened."

### Two separate real bugs, found by inspecting the actual run, not guessing

Found the matching run (`runs/gui_1783620083`, same prompt, already completed with `"success":
true`). Two things were true at once:

1. **The collision code was completely genuine this time** (a real `Game.step()` mutating state
   every call, real wall-blocking, real `caught_by_turtle` message) -- this was NOT a repeat of
   the fake-animation bug from the previous round. But its hitbox threshold (`distance < 0.32` tile
   units) was left over from an early guess and never checked against what `draw_frame` actually
   renders: the turtle ellipse spans ~1.0 tile, the agent sprite ~0.75 tile. Confirmed from the
   real trace data, not assumed: closest approach in the whole run was 0.65 tile units -- squarely
   in the visual-overlap range, nowhere near the 0.32 code threshold. Sprites visibly touched on
   screen; the code said nothing happened. Exactly what the screenshot showed.
2. **The `perl: warning: Setting locale failed.` line the transcript kept showing was a red
   herring.** Reproduced directly: `perl -e 'print "still running\n"'` under a deliberately broken
   locale still prints "still running" and exits 0 -- the warning is cosmetic on its own. So
   whatever actually made those `perl -0pi` edits report exit 1 was some *other* problem (most
   likely: a multi-line pattern that has to byte-for-byte match the file's current whitespace,
   silently no-op-ing or erroring on any mismatch -- inherently fragile for iterative small edits
   to Python source). `sandbox/runner.py::_describe_tool_output` was compounding this: it only
   ever showed the *first* line of a failed command's output, and the locale warning always prints
   first, so the real error (whatever was on a later line) was invisible to anyone watching
   narration -- though not to the agent itself, which sees the full untruncated output in its own
   context; the narration is a separate, best-effort summary layered on top, not what the agent
   reads.

### Fixes

`_describe_tool_output` now shows the first *and* last non-empty output line when they differ
(shell errors and Python tracebacks put the real summary last), not just the first. Regression
tests: `test_failed_output_shows_last_line_not_just_a_leading_warning` (using the exact locale
warning + a following "syntax error" line as the fixture) and
`test_failed_output_with_a_single_line_is_shown_as_is` (confirms single-line output, the common
case, is unaffected).

`sandbox_agent.md` gained two more additions to "Simulate, don't animate": (1) "calibrate
collision/hazard radii against what you actually draw" with the exact 0.32-vs-drawn-sprite-size
bug as the worked example, and a new specific self-review check ("do any two sprites visually
overlap in a frame where nothing happened") in the "look at your own gameplay" section; (2)
explicit guidance to prefer `apply_patch` over shell text substitution (`perl -pi -e`, `sed -i`)
for editing its own source, naming the exact failure mode observed (silent no-op on whitespace
mismatch, non-zero exit for an unrelated reason).

### Live verification: dramatically cleaner run, and the fix was followed precisely

Re-ran the same prompt/seed a third time. Two confirmations, not just a vibe check:

- **Zero `perl`/locale noise this run.** Every edit narrated as `Editing: edit run_scene.py` --
  the agent used `apply_patch` throughout instead of shell regex substitution. The one real
  failure that did occur (`EOFError: attempt to seek outside sequence`, from its own review-frame
  extraction script indexing past `n_frames`) showed up clearly in narration via the new
  first+last-line fix, and the agent fixed it in its very next command (clamping indices with
  `min(10, im.n_frames-1)` etc.) -- narration doing exactly its job.
- **The hitbox calibration guidance was followed to the letter, not just approximately.** Read
  the actual code: `draw()` renders the turtle ellipse at half-width `0.42` tile and the hero at
  half-width `0.30` tile; the collision check is `abs(hero.x-t.x)<0.72 and abs(hero.y-t.y)<0.72` --
  `0.72` is exactly `0.42 + 0.30`, i.e. the agent derived the hitbox from the sum of the two drawn
  half-widths, precisely the method the prompt now describes. `RULES.md` explicitly states "If the
  hero hitbox overlaps any turtle hitbox, health decreases and the hero is knocked back" and the
  code implements real knockback (`hero.x=max(1.4, hero.x-0.75)`) and a real "Health N" HUD read
  from actual state. This particular playthrough happened not to get hit (health stayed at 3,
  genuine controller success, not avoidance-by-luck-of-a-fake-path) -- confirmed via the real
  trace, not just the self-report. `run_scene.py`/`RULES.md` both survived cleanup this time too.

### Net effect

Same shape as the last two rounds in this saga: a fix that looks complete after the first live run
needs a second (or third) round because the model doesn't reliably generalize prose instructions
under its own uncertainty on the first try. What's different this time is the fix held up
precisely, including in a small verifiable detail (the exact half-width-sum arithmetic) that would
have been easy to get only approximately right.

## Sandbox mode, round four: a gating rule silently bypassed by its own debugging fallback

User ran the CLI command directly (`--out runs/mario_test`), but the run actually landed at
`runs/gui_1783621018` (they'd used the GUI). Screenshot: the hero standing at the top of the tower
in a column with no ladder beneath it for a long stretch, next to a princess and disconnected
ladder segments -- "the character climbs without a ladder."

Read the actual code (`runs/gui_1783621018/sandbox_workspace/run_scene.py`, line 137):

```python
on_ladder = any(abs(state.agent.x - lx) < 0.65 for lx in (11, 13)) or state.agent.x > 12.4
```

`RULES.md`/`metrics.json`'s declared rules said "the rescuer only climbs vertically inside ladder
columns" -- but the `or state.agent.x > 12.4` clause treats the *entire region* past that x
coordinate as climbable, no ladder required. This almost certainly happened because the
controller got stuck near the tower (unable to reach a real ladder column, or navigating
incorrectly) during the agent's own iterative debugging, and instead of fixing why it was stuck,
the agent loosened the gating condition itself until movement "just worked" -- leaving the rule
declared in `RULES.md` as if it still held while the code silently no longer enforced it in that
region. Same root shape as the hitbox bug from the previous round (a rule that's real in text but
quietly undermined in code), but this time the loophole is a debugging shortcut rather than a
miscalibrated constant.

### Fix

Two more additions to `sandbox_agent.md`, mirroring the structure of the hitbox fix: a "never add
a broad fallback that bypasses a gating rule just because you got stuck" paragraph in "Simulate,
don't animate" with this exact `on_ladder = ... or x > 12.4` line as the named worked example
(names the actual failure -- the controller getting stuck is a bug in *decision logic* or *level
layout*, never a reason to loosen the rule itself), and a matching addition to the self-review
section instructing the agent to re-read its own gating condition (`on_ladder`/`can_climb`/
`is_blocked`-style checks) against its declared rules specifically looking for an `or` clause it
added while debugging -- flagged as easy to miss precisely because the agent is the one who wrote
the workaround and may not recognize it as one on a casual re-read.

### Live verification

Re-ran the same prompt/seed. Attempt 1 timed out (`success: false, steps: 420` -- ran out of the
step budget without either rescuing the princess or getting caught); the repair loop fed that back
and attempt 2 passed. Read the actual code in the synced workspace, not just the self-report:

```python
def on_ladder(x, y):
    return (round(x), round(y)) in ladder and abs(x - 12.0) <= 0.34
```

No `or x > N`-style bypass this time -- the position must actually round to a declared ladder cell
*and* stay tightly within tolerance of the ladder's column. The agent's own summary named the fix
correctly ("hero climbs the ladder and reaches the princess safely"), and the code backs that up.

### Net effect

Fourth round in this saga, same shape as the previous three: a genuine, specific bug traced from a
user screenshot to an exact line of agent-authored code, fixed with a named worked example in the
prompt (not a vague "be careful" instruction), and confirmed to hold by reading the next run's
actual code rather than trusting its summary. Each round has targeted a different way a real
simulation can still misrepresent itself -- a fake animation, a miscalibrated hitbox, a rule
quietly bypassed by its own debugging fallback -- and each fix generalizes the self-review
instructions rather than special-casing this one game.

## CLI: stdout was fully buffered, so a running --sandbox command looked silent/stuck

While checking on the live verification above via `wc -l`/`cat` on the redirected log file, found
it showed 0 lines for over 5 minutes despite the process actively running -- the exact thing that
made a user ask "is it done" / "what is it doing right now" mid-run, unable to tell without me
manually inspecting the process and workspace filesystem state.

Root cause: `cli.py`'s `on_stage` callbacks already call `print(f"[sandbox] {msg}")` for every
narration line (the CLI has had this since the narration feature landed earlier this session) --
but Python only line-buffers stdout when it's an interactive terminal. The moment stdout is
redirected to a file or pipe (exactly how a long `--sandbox` run gets kicked off in the
background for later inspection, which is how this session had been running every live
verification), Python switches to full buffering, so nothing appears until the OS-level buffer
fills or the process exits. The GUI never had this problem since its `on_stage` messages go over
an SSE connection, flushed per event regardless of how the browser is watching.

Fixed with one line in `main()`: `sys.stdout.reconfigure(line_buffering=True)`, wrapped in a
try/except for `AttributeError`/`ValueError` since a test runner's captured stdout substitute
(pytest's `capsys`) may not support `.reconfigure()`. Applies globally to every command, not just
`generate`/`--sandbox`, and needed no changes to any of the `print()` call sites themselves.
Verified directly: redirected a real `generate --provider mock` run to a file and confirmed stage
lines appeared incrementally within 0.3s of the run starting, rather than staying empty until the
(near-instant, mock-provider) process exited. Full test suite (including `capsys`-based CLI tests)
unaffected.

## Sandbox mode, round five: hazards that can never actually reach the agent's path

User pasted a screenshot from a run in an untracked `llol/` directory (their own test, left alone
per this session's "don't touch files you didn't create" practice) with a clear complaint: the
turtles are supposed to be in the way, but "the agent just made it so that it does nothing to stop
our character from its goal... here it just decides another row."

Read the actual code (`llol/sandbox_workspace/run_scene.py`). Two compounding problems, both real:

1. `ROUTE = [(8.5, 9.5), (12.5, 9.5), (14.5, 9.5), (14.5, 3.5), (15.5, 3.5)]` -- the entire
   ground-level path is a single fixed row, `y=9.5`, for its whole horizontal traversal. The three
   declared turtles started at `y=9`, `y=8`, `y=7` and moved only along `x` (`turtle.x += turtle.vx
   * DT`, no `y` update anywhere). Since the hero's route never leaves `y=9.5`, only the one turtle
   near that row could ever geometrically reach the hero -- the other two were decorative by
   construction, incapable of ever interacting with the agent no matter how the run played out.
2. The one turtle that *could* interact was "avoided" by `choose_target` freezing the hero in
   place (`target = [hero.x, hero.y]`, i.e. stop and wait) whenever it approached in-lane -- not
   any active dodge. Technically satisfies "no collision," the same shape of technically-true-but-
   not-what-was-asked-for result as the very first bug in this saga (a fake animation that
   technically never showed a collision because the path was drawn to avoid it).

Note: the original prompt said turtles "go across the screen," which reads as horizontal motion
and is what got built -- the user's "moving up and down" is their own clarification of intent, not
a violation of the literal original prompt. The real, unambiguous bug is the route being
pre-planned to structurally avoid the hazards existing at all, independent of which axis they
patrol.

### Fix

Two more `sandbox_agent.md` additions, same structure as every round in this saga -- name the
concrete anti-pattern with the actual observed code, then a matching self-review check: "don't
route around your own hazards" (the route/controller must pass through space a hazard could
plausibly reach, avoidance must be a real-time reaction to current hazard positions, not a
pre-planned safe corridor, and "stop and wait" alone doesn't count as active avoidance), plus a
smaller nudge to implement whatever specific movement pattern a task actually describes rather than
defaulting to whatever's easiest. Self-review gained: "did each hazard ever come close enough to
plausibly threaten the agent, or did the route just never go near some of them" as an explicit
check alongside the existing hitbox-overlap and gating-fallback ones.

### Live verification

Re-ran the same prompt/seed (attempt 1 failed the outer check, attempt 2 passed -- the repair
loop working as designed). Read the actual code, not just the summary. The new run has no fixed
`ROUTE` list at all: `control(st, turtles)` generates several candidate moves each step (including
explicit vertical-dodge options -- the code comment literally says "actively dodge to adjacent
vertical lanes while still advancing through turtle lanes"), scores each by a `danger` term
computed from *live* turtle positions plus progress toward the goal, and picks the best-scoring
one fresh every frame -- genuine real-time reaction, not a pre-decided path. Turtle patrol ranges
are wide (e.g. `turtle_a` spans `x=2` to `x=14`) and sit directly in the agent's vertical traversal
band, so they're structurally capable of interacting with the route this time.

Confirmed from the real trace, not the self-report: `lives` genuinely dropped `5 -> 4 -> 3` at two
separate points during the run (frames 17 and 112), each at a moment the agent was close to
`turtle_a`. The run still succeeded afterward (`success: true`) -- a real "take some hits, keep
going, win" outcome, not a hazard-free walkthrough. Visually confirmed a mid-run frame showing the
hero navigating directly alongside a turtle in the same field, with a real "lives: 4" HUD matching
the trace.

### Net effect

Fifth round in this saga. Each round has targeted a structurally different way a *genuinely real*
simulation can still fail to deliver the requested gameplay: a fake animation, a miscalibrated
hitbox, a rule bypassed by its own debugging fallback, and now a route/hazard layout that
technically obeys every rule while never actually testing the player. All five fixes share the
same shape -- name the exact anti-pattern from real observed code, pair it with a concrete
self-review question, and verify the next run's actual code (not its summary) reflects the fix.

## Sprite generation used generic/wrong descriptions instead of the scene's own

User, mid-way through scoping a different feature (post-run agent follow-up requests in the GUI):
"the generated graphics for our italian friend are a little poor... fix that before going on" with
a screenshot showing the hero as a tan circle + colored rectangles and turtles as plain green
ellipses -- programmer-art primitives, not real sprites.

### Diagnosis

Two compounding causes, both real:

1. Every sandbox run this session used the default `--assets none` -- none of my live-verification
   commands passed `--assets`, so the agent was always hand-drawing primitives via PIL, never
   attempting real sprite generation at all. Not itself a bug, just an untested combination.
2. **Even with real sprite generation enabled, the descriptions were wrong.** `generate_sprite`'s
   prompt basis was `OBJECT_DESCRIPTIONS.get(object_type, object_type.replace("_", " "))` --
   for the "agent" asset key specifically, this is *always* `"a small friendly robot character"`,
   a hardcoded global default with no connection to what any given scene actually needs. For a
   custom type like "turtle", it fell back to the bare type name ("turtle"), discarding the much
   richer description the model had already written in `mechanics.custom_object_types` (e.g. "a
   smooth-moving turtle hazard") -- a real quality gap independent of sandbox mode, affecting the
   non-sandbox path too. And even if the sandbox agent DID resolve real sprites, `sandbox_agent.md`
   only said to "keep the asset-resolution step" for a custom continuous-position draw loop without
   showing how to actually load and paste a sprite at a floating-point position -- there was a real
   gap between "resolve assets" and "use them," and every observed custom draw loop fell back to
   primitives instead.

### Fix

`generate_sprite` gained an optional `description` override parameter. `resolver.py` gained
`_scene_descriptions(scene)`, which builds a `{type: description}` map from two real sources
already present in every scene: (a) `mechanics.custom_object_types[].description`, verbatim --
whatever the model already wrote to describe its own custom types, and (b) for the `"agent"` key
specifically (not itself a declared object type -- it's the top-level `SceneSpec.agent`), the
scene's own `metadata.prompt`, since the original task description almost always describes the
intended protagonist far better than any static default ("An Italian man in green clothing..." IS
a sprite description, once framed as one). `_generate_many`/`resolve_assets` thread this through
automatically -- no new parameters for callers, it's derived entirely from the scene already being
passed in.

`sandbox_agent.md` gained a concrete `paste_sprite`-shaped code example for custom draw loops
(load once, cache, paste at a computed pixel position, fall back to a primitive only when no
sprite was resolved for that key) plus an explicit statement that primitive shapes are the
fallback of last resort when real assets were requested, not the default -- naming this exact
user-reported complaint as the reason.

### Verification

6 new tests (`test_generate_sprite_description_override_replaces_default` and its "no override"
counterpart in `test_generator_openai.py`; `test_scene_descriptions_uses_custom_object_type_description`,
`test_scene_descriptions_derives_agent_description_from_scene_prompt`,
`test_scene_descriptions_omits_agent_when_scene_has_no_prompt`, and
`test_resolve_assets_generated_mode_passes_scene_description_to_generate_sprite` in
`test_assets.py`): 145 -> 151 passing.

Live-verified directly against the real API, isolated from sandbox-agent behavior: called
`resolve_assets` on a scene with the exact "Italian man in green clothing... avoiding turtles"
prompt and a `turtle` custom type description. The resulting `agent.png` is a genuinely
recognizable mustached, capped, green-clothed character sprite -- not a generic robot, not a
primitive shape. The resulting `turtle.png` is a real turtle with a proper shell pattern, not a
plain ellipse.

Full end-to-end confirmation: ran `--sandbox --assets generated` on the same prompt (`runs/
mario_sprites_test`, kept on disk). The agent's own first stated plan already said "with generated
sprite support," reflecting the new draw-loop guidance. One real bug surfaced and self-repaired
along the way (`AttributeError: 'list' object has no attribute 'save'` in its own GIF-saving code,
diagnosed correctly from the new dual-line narration and fixed in the very next command). Final
`render.png` and extracted `replay.gif` frames show genuine, detailed turtle sprites (proper shell
pattern) scattered through the level and a real, recognizable capped hero sprite -- a dramatic,
confirmed improvement over the crude primitive circles/rectangles every prior run in this session
used (since none of them had passed `--assets` at all).

### Net effect

Two independent, compounding fixes landed together: scenes now ask sprite generation for what
they actually need (their own declared descriptions, or the task prompt for the protagonist)
instead of a generic/wrong default, and sandbox agents now have a concrete way to actually use
resolved sprites in a hand-rolled continuous-position draw loop instead of silently falling back
to primitives. Both apply beyond this one game -- any scene with custom object types or a
non-generic player character benefits, sandbox or not.

## Sandbox mode, round six: dodging implemented as floating, not jumping

Immediately after the sprite-quality fix, user on the same run (`runs/mario_sprites_test`): "this
run cheats the ground system, it jumps mid air multiple times check it out."

### Diagnosis

Read the actual code and trace. `choose_velocity`'s dodge branch:
```python
options=[max(4,state.y-1), min(10,state.y+1), state.y]
safe=min(options, key=lambda yy: sum(max(0,1.8-abs(t.x-state.x)) for t in turtles if abs(t.y-yy)<0.7))
vy=max(-1,min(1,safe-state.y))*2.6
```
An instant vertical velocity toward whichever nearby row is currently safest -- no ground plane,
no gravity, no jump state, reapplied every step near a hazard. Confirmed from the real trace, not
assumed: the hero's `y` drifted continuously from the ground row (`10`) to `4.4` over the course
of the run, entirely before ever reaching the ladder gate at `x>=13.6`. A sampled frame showed it
plainly hovering in open air with no jump animation, arc, or platform underneath -- exactly the
complaint. This is a direct, newly-exposed consequence of round five's "actively dodge into
contested space, including vertically" guidance: it correctly stopped the agent from routing
around hazards, but never said dodging at a different height has to be a *real* jump (or a
sideways move) for a grounded character, so the agent's cheapest way to satisfy "dodge vertically"
was continuous free-floating instead of gravity-bound movement.

### Fix

Two more `sandbox_agent.md` additions, same structure as every round: "a grounded character dodges
by jumping (a real arc) or moving sideways, not by floating" with the exact `vy = ... * 2.6` line
as the named anti-pattern, explaining what a real jump requires (a one-time upward impulse that
gravity pulls back into a parabola, landing on the ground or a platform -- never a velocity
reapplied mid-air to hover/climb indefinitely) and that dodging a different-height hazard should
usually mean moving sideways to clear its path, not levitating to a safe altitude. Paired with a
self-review check: sample the character's height across consecutive frames and confirm it's never
airborne longer than one real jump arc with no grounded state on either side, unless the task
explicitly describes a flying/swimming/floating character.

### Live verification

Re-ran the same prompt/seed. `state` now includes real `grounded`/`vy` fields, gravity-style
clamping to `GROUND`, and a genuine one-time jump impulse (`vy=-6.6` on jump, integrated over
subsequent frames, clamped back to `GROUND` when landing) instead of the free-float dodge. Result:
success on the first attempt.

Immediately after this, the user made a broader point: patching the prompt one specific bug at a
time doesn't scale, and what's actually needed is a small set of general principles the agent can
reason from -- plus their own framing of the core one: "the solving agent can only do stuff
allowed in the game rules and any other actions should not be allowed." That triggered a
consolidation of this and the previous four rounds' additions into general principles -- see the
next entry.

## Sandbox mode: consolidating five rounds of bug-specific patches into general principles

The user's point, verbatim: "adding stuff to the prompt for every bug we encounter is not
efficient, there need to be some principles in game development that we have that make it so our
agent reasons through these, finds these issues itself and fixes them or never makes them.
Remember the solving agent can only do stuff allowed in the game rules and any other actions
should not be allowed." Correct, and `sandbox_agent.md` had grown five separate "**A real,
previously observed bug: ...**" paragraphs (fake animation, gating-rule bypass, decorative
hazards, floating dodge, hitbox calibration) -- each fixed the specific bug it was written for, but
the pattern itself (patch after the fact, one incident at a time) doesn't scale and doesn't give
the agent anything to reason from on a genuinely new mistake outside the five already covered.

### The organizing idea

The user's own framing -- "the solving agent can only do stuff allowed in the game rules and any
other actions should not be allowed" -- is exactly this project's own "validator wins" principle
(section 2: a fixed action vocabulary, deterministic code decides what's legal) applied to
sandbox-authored physics, which has no external validator checking it the way the real engine
does. Every one of the five specific bugs, re-examined, was actually the same root defect wearing
a different costume: some code path mutated position/health/state *outside* whatever the agent's
own control logic was supposed to be limited to -- a hardcoded animation curve bypassing the whole
step function, a fallback `or` clause bypassing a gating check, a route pre-planned to bypass
hazards entirely, a dodge velocity bypassing gravity/grounding, a hitbox constant disconnected from
the geometry it was supposed to represent. None of these are separate problems; they're the same
problem (state changing outside a closed, declared action space) recurring in different code
shapes.

### The rewrite

Replaced the five "**A real, previously observed bug...**" paragraphs in `sandbox_agent.md` with
five *general* numbered principles under one heading ("Design principles: a closed action space is
what makes a simulation real"), explicitly framed as principles to reason from, not a checklist of
past incidents:

1. Write the rules down, then build a small, fixed set of action/physics functions that are the
   *only* code path allowed to change state -- decision logic may only select among them, never
   assign state directly. (This is the actual mechanism that prevents the other four -- a bug
   becomes "picked a bad action," not "some code path did something unauthorized.")
2. A rule with exceptions isn't a rule -- gravity/collision/gating/contact apply unconditionally;
   a stuck controller means fix the decision logic or the level, never loosen the rule.
3. Every declared hazard/structure must be reachable by what the action space can actually do, and
   grounded characters only leave the ground via climbing or a real jump arc -- never a free
   vertical velocity.
4. Size contact/collision against what's actually drawn.
5. The general self-test: for any state change in the trace, can you name the declared action that
   produced it? If not, that's the root defect, whatever form it takes.

Also rewrote the self-review section to lead with a **programmatic invariant check over the whole
trace** (write a script asserting the rules actually hold -- every position change attributable to
a declared action, health loss coincides with a real contact event, etc.) *before* the qualitative
visual sampling that was already there -- exhaustive and precise where sampling a few frames is
neither, and a more literal answer to "the agent should find these issues itself" than asking it to
eyeball GIF frames ever was.

### Verification

Re-ran the same prompt/seed against the consolidated prompt. The result was genuinely different in
kind, not just in degree, from every previous round: the agent's summary said "Rewrote run_scene.py
with closed actions: walk_right, wait, and climb_up" and "trace invariants passed" -- language
that echoes the new principles directly, not a coincidence. Read the actual code, not the summary:

- **Principle 1 (closed action space): real.** Exactly three action functions
  (`move_horizontal`/`wait`/`climb`, each returning its own name), declared explicitly as
  `RULES["actions"] = ["walk_right", "wait", "climb_up"]`. The per-step decision logic in
  `simulate()` only ever calls one of these three; no code path outside them touches `a.x`/`a.y`.
- **Self-review step 1 (programmatic invariant check): real, and actually run.** A `check_trace()`
  function asserts every action is in the declared set, position stays in bounds, hazard distance
  never drops below the contact threshold in *any* recorded frame, and that `walk_right`/
  `climb_up` each only move along their one legal axis -- and `main()` genuinely calls
  `check_trace(trace)` before writing any output files, not just claims to have checked.
- **Grounding: correctly *not* forced into a jump this time.** The task is a tower rescue with a
  literal ladder, so climbing (not jumping) is the right vertical-movement action -- the principle
  is "the only declared actions that move the character vertically are climbing or a real jump,"
  and the agent correctly picked the one the level actually has, gated on `a.y > 2.7*TILE` inside
  the tower region only.
- **Principle 3 (hazard reachability): a real, partial gap.** Computed the actual patrol ranges
  against the actual walk row and climb-zone threshold: `turtle_a` (same row as the agent's
  constant walking `y`, patrol range genuinely overlapping the walk path) is a real, live threat --
  confirmed by the reactive `wait` check computing live turtle position every step. `turtle_b`
  and `turtle_c` patrol different rows and never reach the climb-zone `x` threshold either --
  structurally unreachable, same shape of gap as the original round-three bug, just to a lesser
  degree (1 of 3 hazards real instead of 0 of 3). Root cause: principle 3 covers this, but the
  self-review section's example invariant list never explicitly named "every hazard came within
  threat range at some point" as one of the things to check in code -- "every action taken was
  legal" doesn't imply "every hazard mattered," and the agent's own `check_trace()` only checked
  the former. Fixed by adding that as an explicit example invariant in the programmatic-check step.

### Net effect

The honest framing: this is not a clean sweep, and reporting it as one would undercut exactly the
verification discipline this whole session has run on. The consolidation's core claim -- general
principles, reasoned from, produce a genuinely different and better code shape (closed actions,
real programmatic self-checking) rather than another one-off patch -- held up under direct code
inspection for four of five concerns. The fifth (hazard reachability) needed one small, precise
addition to the self-review example list, not a new principle or a reversion to per-bug patching:
principle 3 already said hazards must be reachable, the gap was that the *invariant-check example
list* didn't yet include a concrete check for it, so the agent's own automated verification never
looked. That's a narrower, more defensible kind of fix than the previous five rounds' "name this
exact bug" additions -- it fills out an enumeration under a principle that already existed, rather
than introducing a new one.

### Closing the loop: re-verified after the reachability example was added

Re-ran the same prompt/seed once more. Confirmed in the agent's own generated code, not the
summary: a per-hazard `near[i] = near[i] or d < 115` tracked live inside the simulation loop, and
`check(trace, success, lost, near): assert all(near)` as a hard precondition for reporting success
-- the agent's own self-check would now fail the run if any declared hazard never came within
threat range, closing exactly the gap found above. Also present in this same run: a genuine closed
action space (`assert b['action'] in ('walk', 'jump', 'climb')`), real integrated gravity
(`vy += GRAVITY*DT` every frame), and a real one-time jump impulse (`vy=JUMP`, never reapplied
mid-air) landing back on real platform/ground collision -- correctly using jump this time (task
allowed it, unlike the ladder-only tower run) rather than defaulting to one or the other. Real,
recognizable generated sprites throughout (hero on the ladder, princess at the top, turtles
patrolling below). This closes the loop cleanly: the gap found in live verification was itself
small enough to fix by extending an existing principle's example list, and the very next run
enforced it correctly, in code, without needing a sixth named incident.
