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

## 2026-07-09: Genre-accurate hazard behavior and real per-frame animation

### Diagnosis

User-reported screenshot (a run's `replay.gif`, prompt: "...moving plants try to eat him from
below like a side-scrolling platform game") showed the "chomping plants" drifting side to side
along the ground, and asked "where are the animations and the full feature set that a later stage
project has." Read the actual generated code for that run
(`runs/gui_1783629196/sandbox_workspace/run_scene.py`) rather than guessing at the cause:

- `plant_position(p, t)` returned `p.base_x + sin(t*speed+phase)*amp` for `x`, with only a tiny
  6px vertical wobble -- pure horizontal patrol, nothing popping up from a gap or retracting.
- `draw_frame()` drew the exact same three fixed primitives (stem rectangle, body ellipse, a fixed
  triangular "mouth" polygon) at the plant's current position every single frame across a 700-step
  trace -- the mouth shape itself never opened or closed. Same for the hero: one fixed pose,
  translated only. Nothing in the code varied an entity's *drawn state* by its own phase/timer,
  only its position -- true independent of `ASSETS_MODE` (this run had it set to `none`, but the
  gap is in the drawing logic itself, not sprite usage).

Root cause traced to two real gaps in `sandbox_agent.md`, not a one-off mistake in this run:
principle 3 already said "build the specific behavior the task actually describes... rather than
whatever's easiest to code," but its own example list ("moves side to side, up and down, chases,
patrols a fixed lane") presented "side to side" as an equally-valid default alongside the others,
giving the agent no signal to prefer whatever pattern the task's own wording actually implied
("eat him from below" is the canonical Piranha-Plant pop-up-from-a-pipe pattern, not a ground
patrol). And there was no guidance anywhere in the prompt about animating an entity's drawn state
over time -- the only code example (`paste_sprite`) pastes one static image per entity per frame,
with nothing telling the agent that a mouth, walk cycle, or other obviously-animated real-world
reference should visibly change pose/state across frames, independent of position.

### Fix

Both fixes are prompt-only (sandbox mode's entire premise is that the agent can already write
arbitrary Python -- it just wasn't told to do either of these things), continuing this session's
established pattern of generalizing a live-observed bug into a durable principle rather than
special-casing "plants":

- Sharpened principle 3 to explicitly require reading the task's own positional/behavioral
  language ("from below," "emerges," "erupts," "guards a doorway," "chases") and implementing the
  motion pattern that language actually implies, rather than defaulting to a generic side-to-side
  patrol just because it would technically satisfy "the hazard is reachable" -- naming the
  from-below/Piranha-Plant case as the one concrete illustration.
- Added principle 6: "Animate what has an obviously animated real-world reference, not just its
  position." Requires driving at least one drawn parameter from the entity's own phase/state timer
  whenever its real-world reference has an obvious animated aspect, via either (a) a procedurally
  animated overlay (mouth-angle, leg-offset, squash-and-stretch, retract/extend cycle) or (b), when
  assets are enabled, resolving more than one `custom_object_type` per entity (e.g.
  `plant_open`/`plant_closed`) and swapping the resolved sprite by state -- `resolve_assets`
  already supports resolving as many declared custom types as given, no code change needed for
  this path. Self-test: freeze two frames at the same position but a different point in the cycle;
  if the pose is pixel-identical, it wasn't animated, only moved.
- Extended the existing `paste_sprite` code example with a second snippet showing a state-keyed
  sprite swap in practice, and extended the "before you finish" visual-review step to explicitly
  check whether animated entities actually look different in pose/state between sampled frames,
  not just at different positions.
- Bumped `sandbox/runner.py`'s `max_turns` default from 40 to 60 -- the run that surfaced this bug
  had already hit the 40-turn ceiling once on its first attempt before this change asks for
  measurably more per-attempt reasoning (behavioral-language mapping, an animation approach,
  potentially extra sprite types).

### Live verification

Re-ran the same prompt (`runs/mario_animation_test`, `--assets none`, seed 42). Read the
agent-authored `run_scene.py` directly rather than trusting its self-report:

- `plant_state(p, t)` now computes a `cycle` phase and derives vertical `y = 9.05 - ext*1.28` from
  an extend/hold/retract/hidden schedule -- a genuine pop-up-from-pipe cycle. Static pipe openings
  are drawn as fixed rectangles at each plant's `x`; the plant body only appears while `extent >
  0.05`, drawn emerging from within the pipe.
- `draw_frame()` derives the mouth polygon's opening size from `sin(p['phase'] * pi)` when
  `p['open']` is true, and the hero's leg rectangles from `sin(t * 18)` -- both genuinely
  phase-driven, independent of position, exactly principle 6's shape.
- Extracted and viewed frames at six points across the 173-frame `replay.gif`: frame 28 shows all
  three plants at different emergence/mouth states simultaneously (one fully emerged with an open
  mouth, one partially emerged, one just poking its head above the pipe); by frame 86 the plant
  that was fully emerged at frame 28 has fully retracted out of view into its pipe while a
  different plant has newly emerged -- confirming real cycling over time, not a static shape in
  horizontal translation.
- Principles 1-5 held throughout in the same run, unprompted by anything changed this round: the
  agent's own generated `validate_trace()` still asserts every vertical hero movement traces to a
  declared action (`run`/`jump`/`climb`) and every plant came within a declared threat distance
  (`assert all(v<2.2 for v in threats.values())`) before allowing `success=True`.

### Net effect

Genuinely fixed, not just re-described: the before/after code shapes are structurally different
(horizontal-only `sin` patrol with a static mouth vs. a vertical extend/retract cycle with a
phase-driven mouth and leg animation), confirmed from the agent's own code and by visually
comparing frames at different points in the cycle -- not from trusting either run's self-reported
summary.

## 2026-07-09 (same day, follow-up): the user rejected the prompt-only fix -- generic library
## primitives instead of per-case prose

### The correction

Immediately after the entry above, the user rejected the approach directly: *"i dont want you to
add that stuff to the sandbox prompt, i need you to engineer it in a way that makes that behavior
possible without specifying specific cases into it, like new info/sprite costumes etc should be
in our sandbox already, new types of characters/actions too should be makable easily."* This is
the same lesson this session already learned one level up (five bug-specific prompt patches
consolidated into general "closed action space" principles, same file, same day, entries above) --
now applied a level further: even a *general* principle illustrated with one specific worked
example (the Piranha-Plant sentence, the `plant_open`/`plant_closed` code snippet) is still prose
the agent has to be individually taught, not a capability it can just use. The fix has to be code,
not prompt.

### Design

Three exploration agents confirmed the mechanics before writing anything:
- `sandbox/workspace.py::_COPIED_PACKAGES` already copies `engine/` wholesale via
  `shutil.copytree`, and `_rewrite_internal_imports()` already rewrites any `.py` file's
  `infinienv.X` imports generically -- new files just need to exist under `engine/`, no
  workspace-builder change needed at all.
- Multi-variant sprite resolution (`plant_open`/`plant_closed`-style) already worked with zero
  code changes today (`_generate_many` fans out arbitrary type strings through the same
  concurrent path) -- but only for types matching a placed `SceneObject`, since
  `scene_asset_types()` only scans `scene.objects[].type`. A genuinely new capability was needed
  here: resolving a sprite variant with no placed object instance.
- `engine/actions.py::apply_action`'s if/elif dispatch (reject-the-unrecognized-case pattern) and
  `engine/physics.py`'s size/style (small, flat, pure, thoroughly-documented functions) were the
  models to match for new modules.

Built `engine/action_registry.py` (`ActionSpace`: register/dispatch/`UnknownActionError`,
structurally enforcing "state only changes through a declared action"), `engine/motion_patterns.py`
(`patrol`, `pulse_cycle`, `pursue` -- three generic composable functions, none named after or
tuned to a specific creature), `engine/animation.py` (`phase_of`, `oscillate`, `cycle_variant` --
generic phase-driven animation), and extended `assets/resolver.py` with `variant_types()`/
`variant_descriptions()` plus `resolve_assets(..., extra_types=, extra_descriptions=)` (keyword-
only, backward compatible, closes the placed-object gap above). `sandbox_agent.md`'s case-specific
content was reverted -- the Piranha-Plant sentence, the `plant_open`/`plant_closed` snippet -- and
replaced with a short "Reusable building blocks" pointer section plus one inline sentence per
principle naming the actual function names, still fully generic (no creature/game named anywhere).
36 new tests across three new test files plus extensions to `test_assets.py`/
`test_sandbox_workspace.py`; full suite (188 tests) passes.

### Live verification round 1: an honest null result

Ran `--sandbox` on a deliberately unrelated prompt (a factory floor, an erupting steam vent, a
chasing security drone -- `runs/factory_infra_test`) specifically to test genuine discovery, not
imitation of a worked example that was never given. The run succeeded and was correct, but reading
the synced `run_scene.py` (not trusting the agent's summary) showed zero imports from any of the
three new modules: the agent had hand-rolled `vent_active()` (a phase-cycle function functionally
identical to `pulse_cycle`), `step_toward()` (identical to `pursue`), and inline sine-driven leg/
pulse animation (identical to `oscillate`) from scratch. Reported honestly rather than glossed
over, consistent with this session's verification discipline. Two readings: the library's shape
was validated (an independent agent reasoning from the task alone converged on nearly the same
three primitives InfiniEnv had already built) but the *reuse* goal wasn't met -- the pointer
existed but wasn't prominent enough to beat the pull of just writing three quick lines inline,
especially under the same kind of iterative fix-and-rerun turn pressure visible throughout that
run's narration log.

### Visibility fix and round 2: confirmed

Per the plan's own anticipated fallback ("the pointer needs to be more visible, not more
prescriptive" -- decided before running the first verification, so this wasn't a post-hoc
rationalization), two changes: the "Reusable building blocks" section's framing moved from "none
of these are required" to "prefer these over writing the same math yourself, they're already
tested"; and principles 3 and 6's cross-references moved from "see below" to naming the actual
function signatures inline at the point of relevance (`patrol()`/`pulse_cycle()`/`pursue()` in
principle 3, `phase_of()`/`oscillate()`/`cycle_variant()` in principle 6) -- still fully generic,
no creature or game named, just the real function names an agent could act on without an extra
file-read.

Re-ran on a second, again-unrelated prompt (a submarine cave, blooming stinging anemones, a
pursuing eel -- `runs/cave_infra_test`). This time the synced code opened with
`from engine.action_registry import ActionSpace` and `from engine.motion_patterns import
pulse_cycle, pursue`, and the usage was genuinely correct, not decorative: `ActionSpace` gates
every position change through registered `thrust`/`hold` actions via real `register()`/
`dispatch()` calls; `pulse_cycle()`'s return value drives both the sting-gating logic *and* the
anemone's drawn bloom radius/spike angle (`r=8+18*a["bloom"]`) -- meaning the same reused function
also solved principle 6's animation requirement as a side effect, unprompted; `pursue()` drives
the eel's distance-gated chase-vs-return-to-rest behavior. Confirmed visually, not from the
agent's summary: extracted two `replay.gif` frames showing the submarine's health dropping 5 → 3
(a real hazard contact occurred) and the same anemone in genuinely different bloom states between
them.

### Net effect

A real, closed loop: found a gap (the library wasn't discovered), predicted the fix in advance
(visibility, not more instruction), applied it, and confirmed it actually worked against a *third*
prompt with no overlap with either the original bug report or the first verification's factory
scenario -- three genuinely different mechanics (Piranha-Plant-style pop-up, factory hazards,
underwater bloom/chase) now share the same three reusable modules, with the second and third
proving the fix generalizes rather than being tuned to look right on one retry. Also worth being
honest about the limits of what this proves: the agent still had to *choose* to import the
library both times it worked (nothing forces it to), and one confirmed success after one confirmed
failure is a promising signal, not a guarantee for every future prompt -- if a future run shows the
same null result again, the next lever is likely making disuse itself detectable (e.g. a
self-review question asking "did you check `engine/` before writing this math") rather than
pushing the pointer's visibility a third time.

## 2026-07-09: asset_notes -> real rate-limit root cause -> local diffusion backend

### Diagnosis chain

A user-reported screenshot ("the graphics look so poor") on a Mario-style scene showed the hero
and two "chomping plants" rendered as crude hand-drawn primitives while the princess and tower had
real generated art. Reading the run's own `ASSETS_MODE`/`asset_cache` confirmed `generated` mode
was active and 6 of 8 requested types succeeded -- only `agent`/`chomper_plant` were missing.
Digging into why surfaced a real, generic bug: `resolve_assets()` has always returned `(entries,
notes)`, `notes` carrying the exact per-type failure reason, but both the reference sandbox
`run_scene.py` template (`sandbox/workspace.py`) and every sandbox-agent-authored rewrite of it
captured `notes` and threw it away -- a sprite that silently fell back to a primitive left zero
trace of why, anywhere. Fixed generically: the reference template now records `asset_notes` in
`metrics.json` unconditionally, and `sandbox_agent.md` tells the agent to do the same if it
rewrites `run_scene.py`. New regression test:
`test_reference_run_scene_records_asset_notes_in_metrics` (runs the real template via subprocess).

Re-running the same prompt with this fix in place surfaced the real cause immediately:
`asset_notes` contained genuine `429 rate_limit_exceeded` errors from `gpt-image-1` -- "Rate limit
reached... Limit 5, Used 5, Requested 1." The account's real limit is 5 images/minute, and a scene
with several novel object types resolved concurrently (`DEFAULT_ASSET_CONCURRENCY = 4`) routinely
exceeds it; `--assets generated`'s "no silent fallback" design means those sprites just don't
exist, previously with no diagnostic anywhere. (Side note: my own first attempt to reproduce this
independently via a raw `generate_sprite()` call hit a 401 "invalid API key" -- turned out to be my
own test script skipping the `OP_KEY` -> `OPENAI_API_KEY` copy step that `cli.py::_load_dotenv()`
does, not a real key problem; the user correctly diagnosed this immediately as "theres a global oai
key set and you didnt use the one in env." Once reproduced correctly, both `agent` and
`chomper_plant` generated fine standalone -- confirming the 429s were real account-level rate
limiting, not a broken key or a content-policy rejection.)

### Local diffusion backend: two designs, the first live-verified to fail

Asked whether a local model could help (these end up as 64x64 sprites regardless of source
quality). User chose bundling a small local diffusion model directly
(`pip install infinienv[diffusion]`) over pointing at a self-hosted OpenAI-compatible server.
Three research passes confirmed: no existing backend-selection seam in `resolver.py`
(`_generate_many` hardcoded the OpenAI import); "local" was already taken (`--assets local` means
checked-in static placeholders); every asset knob in this project is env-var-only, never a CLI
flag, so `INFINIENV_SPRITE_BACKEND` (not a 5th `--assets` value) was the right shape; sandbox mode
needed zero new plumbing since `assets/` is already fully copied and the existing
`SandboxPathGrant(path=sys.prefix, ...)` (added for `pymunk`) already covers any harness-venv
package. Built `assets/generator_diffusion.py` (same `generate_sprite()` contract as
`generator_openai.py`, `stabilityai/sd-turbo` default model, `cuda`/`mps`/`cpu` auto-detection,
lock-serialized pipeline singleton) plus `resolver.py::_select_sprite_generator()` as the seam,
with `AssetEntry.note` recording which backend actually ran.

First transparency design: prompt for a solid magenta chroma-key background, remove it by
color-distance thresholding. Live-verified in three stages, each one a real finding:

1. A hard single-threshold cutoff left a visible magenta fringe around every sprite in the actual
   `render.png` -- confirmed by looking at the rendered scene, not just the isolated sprite file.
2. A softer ramp between an inner/outer threshold reduced the fringe on one sprite (`can`) but a
   second sprite (`table`) came back with almost its *entire* background still magenta-tinted.
3. Dumped the raw pre-processed image directly to find out why: for "a wooden table," SD-Turbo at
   2 inference steps produced pink corrugated stripes with a red-framed square -- nothing close to
   a flat background. This is a real prompt-adherence limitation of a tiny 2-step distilled model,
   not a threshold-tuning problem -- there was nothing clean to key against no matter where the
   threshold was set.

User's call once this was reported: replace chroma-keying with a real background-removal model
(`rembg`, U2Net-based) rather than keep tuning a fragile threshold. This is a strictly more robust
design -- segmentation doesn't depend on the generator painting any particular background color,
only on there being a foreground object at all. Rewrote `generator_diffusion.py`:
`DIFFUSION_SPRITE_PROMPT_TEMPLATE` no longer requests a specific background color (just "a plain
simple background clearly distinct from the object"), and `_remove_background()` runs `rembg.remove()`
after generation, before the same `_crop_to_content` (reused unchanged from `generator_openai.py`)
and 64x64 resize. `rembg` needs the `[cpu]` extra for its `onnxruntime` backend -- a bare `rembg`
install raises a runtime error, not an import error, at first actual use; caught live (the pyproject
extra was `rembg>=2.0` initially, corrected to `rembg[cpu]>=2.0` after the real failure showed up).

### Live verification (final)

Cleared the two previously-bad cached sprites and regenerated via `INFINIENV_SPRITE_BACKEND=diffusion
--assets generated` on the same kitchen-delivery scene. Both `can` and `table` came back with clean
transparent backgrounds and no fringe -- confirmed both in the isolated sprite PNGs and in the
actual `render.png` (no tinted patches behind either sprite this time, unlike every chroma-key
attempt). Separately generated a `wall` texture tile (skips background removal entirely, same as
the OpenAI backend's texture branch) -- a genuine seamless brick pattern, no artifacts.
`asset_manifest.json` correctly recorded `"note": "backend: diffusion"` only for the two
freshly-generated types, `"note": "cache hit"` for the rest (shared on-disk cache with prior
OpenAI-backend runs, as designed). Timing: first-ever call paid a real one-time cost (SD-Turbo
weights + U2Net weights downloads, ~4GB combined, several minutes on this connection); once warm,
per-sprite generation dropped to roughly 1-2 seconds on this machine's Apple Silicon (MPS) GPU --
fast enough that the design's choice to serialize local inference (rather than trying to run it
concurrently like the network-bound OpenAI path) doesn't cost much wall-clock time in practice for
scenes with a handful of novel types.

### Net effect

A real, complete diagnosis-to-fix chain, each step verified against actual output rather than
assumed: a vague "graphics look poor" report traced to a specific silent-discard bug, which once
fixed surfaced the actual root cause (a real account rate limit, not a code bug), which motivated
a new local backend, whose first design was live-verified to fail for a specific, understood
reason (weak prompt adherence at 2 inference steps breaks chroma-keying) before landing on a
structurally more robust second design (real segmentation, not color-matching) that was then
independently confirmed to work. Consistent with this session's standing practice: every claim
here is backed by looking at the actual generated file or rendered scene, not by trusting that a
command exited zero.

## 2026-07-09 (same day, follow-up): four real bugs in the next Mario-rescue run, found by reading
## the actual code -- grounded-character physics module + default backend flip

### Diagnosis

User reported (screenshot) the most recent run (`runs/gui_1783638533`, GUI-triggered) had "a
really bad character asset, runs off the screen, flys on a place it cant and teleports." Read the
run's real `metrics.json` and agent-authored `run_scene.py` rather than guessing:

1. **Bad character asset** -- `asset_notes` (this session's earlier fix, immediately useful again)
   showed `agent_run_1`/`agent_run_2` both failed with a real `400 moderation_blocked`
   ("Your request was rejected by the safety system... category: other") -- almost certainly
   because "an Italian man in green clothing" reads as a copyrighted-character request. `tower`/
   `wall` hit the already-known rate limit. Every custom type in the scene fell back to a
   hand-drawn primitive; the hero's was visibly cruder than the plant's.
2. **Runs off the screen** -- `vx=RUN` (125 px/s) was assigned unconditionally every step
   (`run_scene.py` line 77) including during the `climb_tower` branch; nothing ever zeroed it
   while climbing, so the character drifted horizontally off the tower's face the entire time it
   was supposedly climbing straight up. No world/screen-bounds clamp existed anywhere.
3. **"Flies in a place it can't"** -- the climb condition (`nx>24*TILE-10 and ny>6*TILE`) had a
   lower x-bound only, no upper bound tied to the tower's actual right edge. Once bug 2 drifted
   the character past the tower, the condition stayed true, so it kept "climbing" (rising) while
   floating in open air beside the structure.
4. **Teleports** -- a post-rescue celebration tail appended 46 frames at the hardcoded literal
   `(26*TILE+16, 5*TILE+8)`, regardless of the trace's actual last position -- given bugs 2-3, often
   far from that literal, producing an instant snap on the very next frame.

Also notable: this run's code imported `engine/motion_patterns.py`/`engine/animation.py`
(genuinely used, for the plants and flag) but not `engine/action_registry.py` at all -- the
player's own movement was a hand-rolled ad hoc if-chain, not routed through discrete,
mutually-exclusive registered actions. Structural root of bugs 2-3: nothing prevented "run" and
"climb" from both partially mutating state in the same frame.

### Fix 1: `engine/platformer_physics.py`

Same reasoning as the earlier `motion_patterns.py`/`animation.py` work, one level over: gravity +
ground + climbing + world bounds get hand-rolled fresh (and subtly wrong) every run with no shared
tested primitive for *player-locomotion* physics, the same way hazard motion used to. New module:
`integrate_grounded_2d(pos, vel, *, gravity, dt, ground_y, bounds=None)` (gravity + ground clamp +
optional silent world-bounds clamp -- targets bug 2 directly), `climb_step(pos, climb_speed, dt,
*, structure_bounds)` (moves only `y`, structurally cannot also apply horizontal velocity in the
same call -- targets bug 2's climb-drift specifically; raises `ValueError` if `x` is outside the
structure's bounds, turning bug 3's exact shape into a loud failure during the agent's own
testing rather than a silently floating character), and a standalone `clamp_to_bounds(pos,
bounds)`. 12 new tests, mirroring `test_motion_patterns.py`'s shape.

Prompt changes were deliberately minimal, principle-level, no named incident: one "Reusable
building blocks" line, one clause on principle 3's existing grounded-movement sentence (generic —
"a run action," "a climb branch," no Mario/tower mentioned), and "world/screen bounds" added to
principle 2's existing list of rules that must apply unconditionally. Bug 4 (the teleport) needed
no new principle -- it's already covered by principles 1 and 5; this run's own self-check just
didn't apply the "no unexplained position jump between frames" example the self-review section
already suggests.

### Fix 2: default sprite backend flipped to `diffusion`

Originally planned as an opt-in `INFINIENV_SPRITE_FALLBACK_BACKEND` var (openai primary, diffusion
fallback on failure), but the user redirected mid-plan: *"make it use the local image gen not
openai anymore."* Simpler than a fallback chain -- `_select_sprite_generator()`'s default changed
from `"openai"` to `"diffusion"` directly. `openai` stays fully available as an explicit opt-in
(`INFINIENV_SPRITE_BACKEND=openai`). Deliberately no automatic fallback *to* `openai` if the
`diffusion` extra is missing -- that would silently reintroduce the exact dependency/cost this
change exists to remove; a missing extra just gets the existing clear `ProviderError`.

Real test fallout from the flip: several `tests/test_assets.py` tests mocked
`generator_openai.generate_sprite` and called `resolve_assets(..., "generated"/"auto")` without
setting the backend env var, relying on the old implicit default -- after the flip they started
routing through the *real* diffusion backend instead of their mocks. Caught immediately: running
`pytest tests/test_assets.py` after the flip visibly hung/ran for minutes doing real pipeline
generation instead of finishing in under a second. Killed the run and pinned every such test to
`monkeypatch.setenv("INFINIENV_SPRITE_BACKEND", "openai")` explicitly; added a parallel default-is-
diffusion test. Full suite back to passing in ~11s afterward, confirming nothing was silently left
hitting the real backend.

### Net effect

Both fixes trace to the same single user report, both are generic/reusable rather than
incident-specific (a physics module usable by any grounded-character scene; a backend default that
helps any future moderation-sensitive or rate-limited prompt, not just this one), and both were
caught either by direct code reading (the four bugs) or by a fast, honest test-suite signal (the
mocking gap) rather than by assumption.

## 2026-07-09 (same day, follow-up 2): sandbox runs were re-downloading the diffusion model from
## scratch every time -- a live-caught 1.2GB-per-run bug, project-level model cache fix

### Diagnosis

Live-verifying the physics-module + default-backend-flip fix above (same prompt, `--sandbox
--assets generated`) surfaced its own real bug via the run's own narration: "Asset generation is
hanging too long, so I'm interrupting and using recorded fallback rendering" -- the agent gave up
on sprite generation entirely and switched `ASSETS_MODE` to `none` itself mid-run. User's
instruction: *"remove all the runs with the model downloaded, then make the model at the project
level, it should never download a torch model every time."*

Checked disk usage across every `runs/*/sandbox_workspace` directory: every prior run was under
7MB; this one alone was 1.2GB, and `find ... -iname "*huggingface*"` pointed straight at
`runs/physics_fix_verify/sandbox_workspace/.cache/huggingface` -- a full, from-scratch SD-Turbo
download that had happened *inside the sandboxed workspace itself*. Root cause: `HOME` resolves
within a sandboxed run's own ephemeral, per-attempt filesystem, not the host's real home
directory (the same category of bug as the earlier `sys.prefix`/pymunk Seatbelt finding, just for
a different env var), so `diffusers`/`rembg`'s default cache locations (`~/.cache/huggingface`,
`~/.u2net`) landed inside that one run's workspace and vanished with it -- meaning every sandboxed
run using the diffusion backend would repeat the full multi-GB download from scratch, forever.

### Fix

`generator_diffusion.py` gained a project-level cache root: `INFINIENV_MODEL_CACHE_DIR`
(default `.infinienv_model_cache/`, next to the existing `.infinienv_asset_cache/`), with
`HF_HOME`/`U2NET_HOME` set (via `setdefault`, so an explicit user override of those standard env
vars still wins) to subdirectories under it at module-import time, and a new `model_cache_dir()`
accessor. `sandbox/runner.py::_run_async` now explicitly sets `INFINIENV_MODEL_CACHE_DIR` in the
outer process's environment *before* creating the sandbox session (so the sandboxed subprocess's
inherited `env = os.environ.copy()` carries the identical absolute host path, rather than each
sandboxed run recomputing its own from a `cwd` that doesn't correspond to the host repo), and
grants that exact path read-write via a second `SandboxPathGrant` (read-write, not read-only like
the `sys.prefix` grant -- a first-time download needs to actually write into the cache).
`_interpreter_briefing()` also gained a note explaining that a slow first-time download is
normal and shouldn't be interrupted, addressing the agent's own "hanging too long" narration
directly -- not with a case-specific instruction, but by giving it the actual missing fact (this
is a one-time, now-shared cost, not a hang).

Cleanup per the user's explicit instruction: deleted `runs/physics_fix_verify` (the only bloated
run), and moved the already-downloaded weights from `~/.cache/huggingface`/`~/.u2net` into the
new project-level `.infinienv_model_cache/` directly (a plain `mv`, same filesystem) rather than
deleting and re-downloading them -- ~5GB of SD-Turbo + U2Net weights preserved and now reusable by
every future run, sandboxed or not. Added `.infinienv_model_cache/` to `.gitignore`, mirroring
`.infinienv_asset_cache/`'s treatment. New regression test
(`test_session_is_created_with_a_read_write_grant_for_the_model_cache_dir`) asserts the grant
exists and is read-write; the pre-existing `sys.prefix` grant test was loosened from asserting
exactly one grant to asserting the specific `sys.prefix` grant it actually cares about, since a
second, unrelated grant now legitimately coexists with it.

### Net effect

A second real, live-caught bug found *while verifying the fix for the first set of bugs* --
consistent with this session's repeated experience that live verification surfaces genuine issues
prompt-only or first-principles reasoning wouldn't have: nothing about the physics-module or
default-backend-flip design would have predicted a sandbox-specific `HOME`-resolution bug, it only
showed up by actually running the thing and reading the real narration and real disk usage.

## 2026-07-09 (same day, follow-up 3): re-verification found a broken hero sprite -- CLIP
## truncation eating the prompt's own formatting instructions

### Diagnosis

Re-ran the physics-fix verification after the model-cache fix (`runs/physics_fix_verify2`):
`sandbox_workspace` stayed at 3.7MB this time (confirming the cache fix worked, no re-download),
and the run succeeded. But `render.png` showed the plant/tower/princess sprites looking genuinely
good while the hero was essentially invisible -- a tiny illegible dark speck. Opened
`asset_cache/agent.png` directly: nearly blank, faint fragments on a transparent background.

Reproduced standalone by calling `_run_pipeline()` directly with the real player-character prompt
and saving the *raw*, pre-`rembg` image (same diagnostic technique as the earlier chroma-key
investigation): SD-Turbo had drawn an entire elaborate scene -- floating islands, water, trees,
several small figures -- instead of one isolated character. The terminal output explained why:
"Token indices sequence length is longer than the specified maximum sequence length for this
model (95 > 77)... The following part of your input was truncated." `_scene_descriptions()`
embeds up to 220 characters of the scene prompt for the `"agent"` key (reasonable for the OpenAI
backend, a much larger model with no such hard limit) -- but `DIFFUSION_SPRITE_PROMPT_TEMPLATE`
put the fixed "isolated object... plain simple background" instructions *after* `{desc}`, so for
any moderately long description, CLIP's 77-token truncation silently cut those instructions away
entirely, leaving only the raw (narrative, multi-element) description text. `rembg` then had no
single clear foreground object to segment against a scene with several unrelated figures, which
explains the near-blank result -- not a `rembg` bug, a garbage-in problem one step upstream.

Separately, the user noticed live generation was using a lot of memory; every pipeline load had in
fact been printing "Cannot initialize model with low cpu memory usage because `accelerate` was not
found in the environment" the whole time -- a real, generically-useful fix sitting in plain sight
in the tool's own output, not specific to this incident.

### Fix

Reordered both `DIFFUSION_SPRITE_PROMPT_TEMPLATE` and `DIFFUSION_TEXTURE_PROMPT_TEMPLATE` so the
fixed style/framing instructions come *before* `{desc}`, not after -- truncation (which still
happens for long descriptions) now only ever drops the tail of the description text, never the
instructions the rest of the pipeline (crop-to-content, background removal) depends on. Added
`accelerate` to the `diffusion` extra in `pyproject.toml`. New regression tests: one asserting the
`{desc}` placeholder sits at the end of both templates (a structural check, not tied to any real
model), one confirming a long description still produces a prompt whose "isolated object..."
instructions remain intact ahead of the `Subject:` marker.

### Live re-verification, honest partial result

Reinstalled the `diffusion` extra with `accelerate` -- the "low cpu memory usage" warning is
confirmed gone. Regenerated the hero sprite directly with the same real long description: no
longer draws an entire scene (the reordering fix worked for its specific failure mode), but the
result is still a cluster of small, somewhat disconnected fragments rather than one clean
character -- visibly weaker than what the OpenAI backend or the plant/tower sprites in the same
run produced. Read into why: the description that *does* survive truncation still contains
narrative content ("...rescues a princess from a tower... piranha plants rise from below..."),
and SD-Turbo, being small and only weakly prompt-adherent, tends to render narrative content
somewhat literally (drawing scene elements it's told about) rather than understanding "this
describes who the player is, draw only them" the way a much larger model can. This is reported
honestly as a **known, only-partially-fixed limitation**, not claimed as solved: the reordering
fix is real and correct (verified via the raw pre-processing image, not assumed), but a
narrative-style character description may still be the wrong shape of input for a small local
model, and further improvement would likely mean giving the diffusion backend a shorter,
more visual-only description rather than embedding the full scene prompt -- a genuinely separate
follow-up, not attempted this round given diminishing returns from further blind prompt tuning.

### Net effect

Two more real bugs found and fixed (or honestly partially fixed) purely from live-verifying the
*previous* fix, each confirmed by direct evidence (disk usage, a dumped raw image, the tool's own
warning text) rather than assumption -- consistent with this entire session's practice of treating
"it ran without crashing" as necessary but never sufficient.

## 2026-07-09 (same day, follow-up 4): default sprite backend reverted back to OpenAI

### What happened

Despite the CLIP-truncation fix and `accelerate` install genuinely improving the local diffusion
pipeline, the user's verdict on the next real rendered scene was direct: a screenshot showing a
garish, badly-segmented checkerboard-pattern hero sprite next to otherwise-fine plant/tower art,
captioned *"this is shit go back to openai."* Reverted immediately, no further tuning attempted --
the honest "known limitation" flagged in the previous entry turned out not to be acceptable in
practice, and further blind prompt/model tuning wasn't the right response to direct user feedback
on the actual output.

### Fix

`resolver.py::_select_sprite_generator()`'s default changed back to
`os.environ.get("INFINIENV_SPRITE_BACKEND", "openai")`. `diffusion` remains fully available and
working as an explicit opt-in (`INFINIENV_SPRITE_BACKEND=diffusion`) -- nothing about the backend
itself, the model cache fix, or the prompt-ordering fix was reverted, since those are all real,
correct, still-useful infrastructure regardless of which backend is the default; only the default
selection changed back. Updated `tests/test_assets.py` (renamed/re-pointed the default-backend and
note-provenance tests back to `openai`-as-default, removed one test that became a duplicate of an
existing explicit-opt-in test), `CLAUDE.md` §9/§18, and `README.md`'s backend section to describe
this as what it honestly is: a value that was tried as the default, live-verified thoroughly (a
cache bug found and fixed, a truncation bug found and fixed), and still reverted once the actual
visual quality was judged unacceptable by the person who has to look at the output.

### Net effect

The diffusion backend saga end to end is a genuine example of this session's verification
discipline holding up even when it leads somewhere other than "ship the new thing": three real,
non-obvious bugs were found and fixed by actually running the pipeline and looking at real
output (the sandbox cache re-download, the CLIP truncation, and along the way a free memory-usage
fix from `accelerate`) -- but fixing every *mechanical* bug in a pipeline doesn't guarantee the
*product* quality clears the bar, and the right response to "this is shit" from the person the
software is for is to revert immediately, not to keep defending the choice with more fixes.

## 2026-07-09 (same day, follow-up 5): phasing through walls + no real procedural generation on a
## cave-navigation run

### Diagnosis

User-reported screenshot on a different prompt ("A cave explorer chooses among uneven rocky
tunnels... collects at least two glowing gems, then exits") flagged: phases through walls, and no
uneven terrain or multiple paths despite the prompt explicitly asking for "procedurally
generated... multiple possible paths." Found the run (`runs/gui_1783652701`) and read the actual
generated code rather than guessing.

`floors`/`path_cells` was a hand-listed set of specific grid cells -- essentially one winding
corridor with a couple of one-cell alcoves, nothing procedural, no real branch choice. Worse: the
agent's own movement wasn't checked against this data at all -- a hardcoded `route` list of
waypoints was interpolated in a straight line between consecutive cell centers with zero
wall-collision checking, despite the script generating a `walls` array from the exact same
`floors` set moments earlier. Reproduced the exact defect programmatically (not by eyeballing):
waypoint `(7,6)` on the route was never a floor cell at all, and two consecutive-waypoint segments
(`(6,7)->(7,6)` and `(11,9)->(12,8)`) cut diagonally through a wall corner where *both* adjacent
cells were blocked. This is the same "animation, not simulation" anti-pattern from earlier in this
session's history, recurring in a new form: a route was planned to look collision-free, then
trusted, never actually checked frame by frame.

### Fix

Two new generic modules, same pattern as every prior fix in this thread -- real, tested,
importable capability, not a prompt worked-example:

- `engine/grid_collision.py`: `segment_blocked(p0, p1, blocked, tile_size)` samples a move at
  sub-tile resolution (not just its two endpoints) -- the exact check that would have caught the
  diagonal-corner-cut bug, which neither endpoint cell alone reveals. `move_with_collision(pos,
  target, speed, dt, blocked, tile_size)` is a drop-in replacement for hand-rolled waypoint
  interpolation that stops at a wall instead of passing through it.
- `engine/level_generation.py`: `generate_organic_region(width, height, start, *, steps, seed,
  branch_chance, max_walkers)`, a seeded branching random-walk cave carver -- connected by
  construction, real branch points as an emergent property of the algorithm rather than something
  to hand-design. `region_is_connected(region, start)` is a general BFS reachability check for
  verifying any level (generated or hand-authored) is actually fully navigable -- the motivating
  bug's out-of-floor waypoint would have failed this immediately instead of surfacing as a visual
  glitch three steps later.

Prompt changes stayed principle-level, no named incident: both modules added to "Reusable building
blocks"; principle 2 gained a clause naming grid-wall collision as a rule that must actually be
*checked*, not just planned around; principle 3 gained a clause extending its existing "build what
the task describes" idea from hazard motion to level structure itself; the self-review invariant-
check example list gained "no consecutive position pair in the trace crosses a wall cell." 32 new
tests across the two modules, mirroring the existing `motion_patterns`/`animation` test shape.

### Net effect

The fifth real, live-caught bug class in this session found by reading actual agent-authored code
rather than trusting a self-report -- and, like several before it, traces to the same root
tendency (a plan that *looks* correct, interpolated or executed without ever being checked against
the rules it was supposedly respecting) recurring in a domain (grid navigation, level layout) the
existing principles hadn't yet been extended to cover explicitly.

## 2026-07-09 (same day, follow-up 6): capability-ceiling feedback -- no state-dependent puzzle
## logic yet, only static navigation

### Diagnosis

User feedback on the cave-navigation fix was not about it being wrong -- it worked, verified live.
It was a capability-ceiling observation with a graded difficulty table attached: every sandbox run
this session (Mario rescue, cave, factory floor, submarine cave) had produced *static
navigation* -- walk a space, avoid/reach things -- with the win condition always collapsing to
whichever single check is simplest (a bare position, a raw item count). None had produced real
state-dependent puzzle logic: a locked exit gated on multiple jointly-required conditions, an
ordering between sub-objectives. Explicit instruction: fix this *generically*, not by re-tuning
the cave prompt again -- and a specific harder prompt to verify against: "Create a cave maze where
the exit is locked until the player collects two gems, avoids spikes, presses a pressure plate,
and then reaches the exit."

Root cause, consistent with every fix in this thread: no reusable primitive existed for
state-dependent gating, the way `action_registry.py` gave closed action dispatch and
`grid_collision.py` gave real wall collision. The base engine's schema already models locks/keys
and ordered `sequence` goals -- but wired through `GameState`/`solve_scene()`, which no sandbox
run this session has actually used (every one writes a custom simulation loop instead). A
sandbox-facing equivalent needed to be dependency-free like the other `engine/` additions, not
coupled to `GameState`.

### Fix

New module `engine/puzzle_state.py`: `PuzzleState` (named flag/counter store --
`set`/`increment`/`get`/`snapshot`) and `Gate` (a declarative precondition over several
flags/counters jointly -- `Gate(requires={"gems": 2, "switch_pressed": True})`,
`is_open()`/`missing()`; numeric thresholds by `>=`, boolean by equality, unset flags default to
closed). One real implementation subtlety caught by its own test suite: `bool` is a subclass of
`int` in Python, so a naive "is this numeric" check on `increment()` would silently allow
incrementing a boolean flag (`True + 1 == 2`) -- excluded explicitly.

Prompt gained a genuinely new principle (7: state/sequencing the task describes must be real
dependency structure, not collapsed to the simplest true check) rather than folding into principle
3 again, since goal/win-condition *structure* is a different category from motion/terrain/
rendering -- the same reasoning that justified principle 6 (animation) as its own addition. One
"Reusable building blocks" entry, one self-review invariant example (assert a declared `Gate` was
actually closed at some point before it opened).

### Live verification

Ran the user's own suggested harder prompt (not the cave prompt, deliberately, since this fix was
explicitly not meant to over-fit to it) via `--sandbox`. First attempt, no visibility-tuning round
needed. Confirmed from the synced code, not the agent's summary:
`gate=Gate({"gems":2,"plate_pressed":True})` gates the exit directly (`if pos==exitc and
gate.is_open(pstate) and not state["lost"]`) -- and the agent went further than asked, gating the
pressure plate itself on already having 2 gems (`pos==plate and pstate.get("gems",0)>=2`), a real
ordering dependency layered on top of the joint gate, unprompted. Checked the actual trace data
(not assumed): `gate_open` was `False` for 28 of 36 steps and only flipped `True` once every
condition held, staying open through the end -- a genuinely tested dependency, not a decorative
one that was open from frame zero. The same run also imported and used `action_registry.py`
(closed action dispatch) and `grid_collision.py`/`animation.py` alongside `puzzle_state.py` --
four of this session's reusable primitives composed together in a single file. `render.png` showed
a coherent branching maze with gems, spikes, a visible pressure plate, and the exit.

### Net effect

Closes the specific capability gap named in the feedback (locked-exit/gem/switch/required-order
puzzles), verified against a prompt the fix was deliberately *not* tuned to, with hard trace data
rather than a visual glance or a self-report -- consistent with this entire session's practice.
The user's harder-still tier (moving hazards+switches+crates+NPCs+backtracking) is now partially
covered too: moving hazards (`motion_patterns`), a switch/plate pattern (`puzzle_state`), real
wall collision and terrain (`grid_collision`/`level_generation`) all exist and compose, as this
run itself demonstrated by using four of them together unprompted. Crates (pushable objects) and
NPCs (reactive, not just patrol/pursue) remain unaddressed -- not claimed as solved here.

## 2026-07-10: completing the primitive vocabulary -- crates, reactive NPCs, perception, pathfinding

### Why

The previous entry's closing "crates and reactive NPCs remain unaddressed" drew a direct user
objection: "I need you to fully flesh out it so it becomes really really strong and able to handle
any prompt." So this round closes the remaining categories in the user's own difficulty table
(top tier: moving hazards, switches, crates, NPCs, backtracking). Kept the framing honest in the
docs: "any prompt" is aspirational; the defensible claim is "no common structural mechanic is left
to hand-roll from scratch."

Briefly reconsidered whether to just wait for the Ultraplan cloud session (the plan had been handed
off to it) -- but it failed, so local implementation was the path. (Also flagged to the user
mid-round that I'd started implementing locally in parallel, to avoid a silent duplicate-work mess
had the cloud PR also landed.)

### What

Two coupling facts confirmed by reading the code drove the "dependency-free `engine/` module"
shape (same as `grid_collision.py` was to the base wall-blocking): `navigation/astar.py::find_path`
needs a full `Grid` (the cave run hand-rolled BFS to avoid this), and `engine/physics.py::try_push`
mutates `ObjectState` against a `Grid` (unusable for a crate as a plain dict). Four new modules:

- `engine/pushables.py` -- `try_push_block`/`cell_is_free`/`all_targets_satisfied` (Sokoban crate
  pushing with real collision + the crate-on-switch win check).
- `engine/pathfinding.py` -- `find_path`/`next_step_toward` (BFS over a wall-cell set) so a chasing
  NPC routes around walls instead of straight-lining into them.
- `engine/vision.py` -- `has_line_of_sight` (reuses `grid_collision.segment_blocked`, no duplicated
  raycast), `within_range`, `within_cone`, `can_see` -- real "chases on sight," not see-through-walls
  distance.
- `engine/agent_behavior.py` -- `BehaviorMachine`, a pure reactive-NPC state graph (caller wires
  vision/pathfinding into its conditions/actions).

Reactive NPCs deliberately needed all three of behavior+vision+pathfinding, not just one -- a
half-version (distance triggers, straight-line motion) is the exact "collapsed to the simplest
thing" failure recurring all session. Prompt: four building-block entries + a principle-2 clause
(pushables obey collision) + a principle-3 clause (reactive NPC = real FSM + perception + maze
navigation) + one self-review invariant (NPC state must change across the trace). No new principle
-- both are "build what the task describes," already principles 2/3. One implementation subtlety
caught by its own test: `within_cone` on an exact 45deg-into-a-90deg-cone boundary is a
floating-point tie (came out 45.0000001 > 45), so the *test* was moved off the exact edge rather
than adding an epsilon to production code.

### Live verification

Composite "very hard" prompt (crate onto switch opens a locked gate; guard patrols and chases on
sight; player evades, pushes, backtracks, exits). First attempt, no visibility-tuning round.
Confirmed from the synced code (not the agent's summary): all four modules load-bearing --
`try_push_block` (crate can't phase through walls) + `all_targets_satisfied` (gate opens only when
crate on switch) + `cell_is_free` (crate obstructs agent); `BehaviorMachine` with real
patrol<->chase transitions on `sees`/`lost`; `can_see` with a real radius and the closed gate as a
vision occluder; `next_step_toward` routing the guard around walls+crate. Confirmed from the hard
trace (not a glance): guard state genuinely changed (14 patrol / 6 chase frames), gate closed for
every frame before the crate reached the switch (opened frame 5), player exited uncaught.

### Honest gaps

It used a plain `gate_open` boolean + `all_targets_satisfied` for the single crate->switch
condition instead of `puzzle_state.Gate` -- correct, not a gap, since `Gate` is for *multiple*
joint conditions and the single dependency here is genuinely enforced (gate blocks movement and
occludes vision until the crate lands). Genuinely still beyond the primitive set: an NPC with
richer internal goals than patrol/chase/flee, and multi-crate coordination puzzles -- the blocks
compose toward them but that isn't claimed as proven. This is the honest ceiling, stated rather
than papered over -- but the specific gap the user objected to (crates, reactive NPCs) is closed,
verified against a prompt the fix was not tuned to, with trace data rather than a self-report.

## 2026-07-10: prompt enrichment before the sandbox handoff

### Why

User: "we take the users prompt in, and then fix it/update it so when we handover to the sandbox
agent it has more information and details to operate with... take their prompt improve it and then
hand that off." A one-line prompt leaves the expected feature set implicit and the agent
under-builds. A prompt-refinement LLM call is squarely "use AI for semantic generation" -- it
improves an instruction, runs no code, breaks no invariant, and only runs on the already-disclosed
sandbox path. (Handed the plan to the Ultraplan cloud session first; it failed again, so
implemented locally.)

### What

`sandbox/prompt_refiner.py::refine_prompt(prompt, *, model=None) -> RefineResult` -- one
`client.responses.create()` call (mirrors `openai_responses.py`'s pattern), system prompt in
`llm/prompts/prompt_refiner.md`. Best-effort and never fatal: no key / missing package / API error
/ empty output all degrade to the original prompt with a `note`, like live narration. Wired into
`sandbox/runner.py::_run_async` just before the agent message is built; both prompts recorded in
`metrics.json` (`original_prompt`/`refined_prompt`/`prompt_refined`/`prompt_refine_note`) and the
refined text streamed as an `on_stage` line for transparency. Default-on with `--no-refine-prompt`
(CLI) and a GUI checkbox; `INFINIENV_REFINER_MODEL` override. Sandbox-only (the non-sandbox path
has the schema-aware ScenePlannerAgent already). The refiner's hard rule is intent-preservation
(expand, never replace or contradict), pointing at the sandbox's real mechanic vocabulary without
dictating implementation.

Test note: the existing `test_sandbox_runner.py` tests now flow through default-on refinement, so
added an autouse fixture deleting `OPENAI_API_KEY`/`OP_KEY` to keep them hermetic (refiner no-ops
-> raw prompt) rather than risking a real API call if a key is exported; plus two dedicated tests
(refined text reaches the agent + is recorded; `refine_prompt=False` uses the raw prompt).
`tests/test_prompt_refiner.py` mocks the OpenAI client for the enrich/fallback/model-override paths.

### Live verification

Terse `"a ninja platformer"` -> a three-paragraph spec preserving the genre and adding concrete
win/lose (three scrolls, rooftop exit, health/restart), sandbox-deliverable mechanics
(switch/key-gated locks, guards that patrol and chase on sight, moving platforms, timed traps),
level structure, and pixel-art style. `metrics.json` recorded `original_prompt="a ninja
platformer"`, `prompt_refined=true`, and the full refined spec; the run built and passed the outer
check. The enrichment leaned toward the exact primitive vocabulary the `engine/` modules back
without being told to -- the feature does what the user asked, verified from the recorded metrics
and the printed handoff, not a self-report.

## 2026-07-10: floating characters + render must reflect the simulation

### Diagnosis

User screenshot: hero floats a tile above the grass in a Mario-rescue run; plus "monitor
collisions" and "win cases should be displayed." Read the real code
(`runs/gui_1783691768/sandbox_workspace/run_scene.py`). Three related causes, all "the render
disagrees with the sim": (1) physics clamps to `GROUND_Y=8.0` tiles but the grass is drawn at row
`9*TILE` -- two different numbers for "the ground"; (2) the sprite is center-anchored (`top-left =
center - size/2`), so a standing sprite's feet land half its height below its center, floating it;
(3) `rescued`/`lost`/contact are computed but never drawn -- no banner, no HUD.

### Fix

New `engine/rendering.py`: `feet_anchor(center_x, ground_y_px, size)` / `feet_anchor_rect` -- paste
top-left so the sprite bottom sits on the ground line (fixes center-anchor float by construction).
New principle 8 ("the render must legibly reflect the simulation, not float free of it"): grounded
entities drawn feet-on-ground using the SAME shared ground constant physics clamps to; win/lose
outcome visibly rendered; collision/health shown (HUD, hit-flash). Requirements bullet + both
self-review passes gained matching checks. Reusable-building-blocks entry for `rendering.py`. Per
the session rule: generic primitive + principle + self-review, no per-prompt worked example. 4 new
tests (`test_rendering.py`), workspace-presence test extended; full suite 304 -> green.

### Live verification

Re-ran the exact reported prompt with `--assets none --no-refine-prompt`. Confirmed from the code
and render (not the summary): `GROUND_Y=288` is now one pixel constant used by both
`integrate_grounded_2d(ground_y=GROUND_Y)` and the drawn grass; the hero's feet are on that line --
render shows it standing on the grass, no float. "Health: N" HUD + "Plant bites flash yellow"
indicator make collisions legible (hero ended at Health 1 after two hits); "RESCUED! YOU WIN"
banner shows the outcome. Agent added `assert hero.y <= GROUND_Y+1` unprompted. Honest scope note:
that run drew the hero as primitives (assets none), so it followed principle 8 without exercising
`feet_anchor` (which targets the pasted-sprite center-anchor case the original had). A second run
with `--assets local` confirmed the pasted-sprite path: the hero is a real pasted placeholder,
pasted at `cy = y - PLAYER_SIZE/2` so its bottom sits on the shared `GROUND_Y` -- early frame shows
it feet-on-grass, HUD + banner present. Honest finding: NEITHER run imported the `feet_anchor`
helper; both hand-rolled the correct feet-anchor from principle 8's guidance. So the fix is real
and verified in both asset modes, but the *principle* is doing the work, not the helper -- the
helper stays available, and I deliberately did not escalate its pointer since the outcome was
already correct (a future run that floats a center-anchored sprite would justify that; none did).
This is the same pattern seen earlier this session with motion_patterns: a correct outcome from
the principle even when the specific reusable module isn't imported -- reported as-is, not dressed
up as "the helper was used."

## 2026-07-10: harness-enforced no-teleport floor + climb-gating + assets-resolved-but-not-pasted

### Diagnosis

User report (GUI run `gui_1783698976`, `--assets auto`): character teleports, climbs where there
are no ladders, uses no real art despite auto. Read the code, three root causes: (1) `st.x=tx` and
`st.x=st.x-100` -- position assigned straight to a waypoint / snapped, i.e. teleport; (2) computes
`on_ladder` but gates climb on "target within 70px of a ladder" instead, so climbs in open air (and
imported no primitives -- no `climb_step`, which would raise off-structure); (3) `resolve_assets`
ran and `asset_cache/` had 11 nice sprites but `draw_frame` drew primitives, pasting none -- the
"resolve then ignore" bug, confirmed by the user's note. The run's self-check was `assert won and
lives>0 and key`, catching none of this.

### Decision (asked the user)

Prompt guidance alone has repeatedly failed to stop teleports/off-ladder climbs, so I asked how hard
enforcement should be. User chose: (1) a HARNESS-enforced floor for teleports (they can't ship one);
(2) require + self-review for assets. So teleport enforcement moves into the outer check; climb and
assets stay prompt/self-review (the mode's boundary -- the agent writes arbitrary code, and the
outer check deliberately doesn't judge game rules).

### Fix

`sandbox/workspace.py`: `_positions_from_replay(data)` best-effort-extracts the main entity's
per-frame (x,y) from the trace shapes agents actually write (`trace`/`frames`/`states` list;
`hero`/`agent`/`player` dict or top-level x/y or `pos`/`position`); `_teleport_frame(positions)`
flags the first step exceeding 6x the p90 step (scale-free -- a `pos=target` snap is a 10-30x spike,
smooth run/jump stays under). Wired into `outer_sanity_check` after the image checks; unparseable/
short traces skip it (never false-fail). Unit-tested (smooth passes, snap fails, unknown-shape
skips). Prompt: principle 3 climb clause now requires gating on being ON the structure (use
`climb_step`), not proximity; principle 5 names the teleport anti-pattern and notes the outer check
now enforces it; the assets bullet names the resolve-then-ignore bug and requires pasting the
cached sprites; self-review step 1 reworked from a loose "e.g." list into a required checklist
(no-teleport / gated-change / no-wall-cross / hazard-mattered / NPC-state-changed / feet-on-ground /
assets-pasted). Full suite 312 green.

### Honest framing

The teleport floor is a heuristic (could miss a degenerate all-teleport trace or an unparseable
shape; slightly widens the outer check's role -- documented). It's the one correctness floor the
agent can't skip with a weak self-check, and the strongest lever without reintroducing the
fixed-vocabulary constraint this mode exists to escape. Climb-gating and asset-usage stay
agent-discretionary -- this raises the floor and the self-review bar, it doesn't make them
impossible. [Live-verification result appended after the run.]

---

## 2026-07-10 -- Second sandbox agent runtime: the Claude Agent SDK backend

User: "you now have a cluade key, i want to try the agent sandbox with claude agent sdk." Added a
second, interchangeable agent runtime for `--sandbox`, selected by `INFINIENV_SANDBOX_BACKEND`
(default `openai`; `claude` for Anthropic's Claude Agent SDK). Env var, not a new flag -- same
precedent as `INFINIENV_SPRITE_BACKEND` -- so `--sandbox`'s meaning stays stable.

### Why a second backend and not a model swap

The two SDKs have structurally different execution models:
- **OpenAI Agents SDK** (`agents.sandbox`): copies the workspace into a *separate ephemeral
  filesystem* (`hydrate_workspace`), runs shell/file tools there under a macOS Seatbelt profile,
  then syncs it back onto disk (`sync_full_workspace`) and extracts artifacts from it. Real,
  OS-enforced FS isolation.
- **Claude Agent SDK** (`claude-agent-sdk` -- Claude Code as a library, NOT the plain `anthropic`
  Messages SDK): runs built-in Read/Write/Edit/Bash tools *directly on a `cwd`*. So `cwd` **is**
  `sandbox_workspace/` on disk; no tar hydrate/sync, and "extract artifacts" is a plain file copy
  (`_copy_artifacts_from_dir`). Isolation is by `cwd` on a throwaway copied workspace +
  `permission_mode="bypassPermissions"` + `setting_sources=[]` (so Claude Code does NOT load this
  repo's own CLAUDE.md by walking up from cwd). sandbox_agent.md is appended to Claude Code's
  `claude_code` system-prompt preset, not replacing it.

Verified the SDK API against the installed package (0.2.115) before writing, per the claude-api
skill's "never guess SDK usage": `query(prompt=, options=ClaudeAgentOptions(...))`,
`ClaudeAgentOptions(cwd/model/max_turns/system_prompt/permission_mode/setting_sources)`,
`system_prompt={"type":"preset","preset":"claude_code","append":...}`, message/block shapes
(`AssistantMessage.content` -> `TextBlock.text` / `ThinkingBlock.thinking` /
`ToolUseBlock(name,input)` / `ToolResultBlock(is_error,content)`, `ResultMessage.result/is_error`).

### Honest isolation disclosure

The Claude backend does NOT apply the OpenAI backend's Seatbelt confinement -- weaker than a
separate OS-enforced FS. Consistent with section 11's standing posture (disclosed trade-off, not
hidden). The one load-bearing guarantee both keep: the outer trusted process never imports/executes
agent-written `.py` files -- only reads back the five artifacts. The Claude Agent SDK exposes its
own `SandboxSettings`; wiring it in to recover Seatbelt-grade confinement is a reasonable follow-up,
left out of this minimal first cut and flagged rather than overclaimed.

### Auth

Claude Agent SDK spawns the `claude` CLI (credential order: ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN
-> stored claude.ai login). *First* version mapped `CL_KEY -> ANTHROPIC_API_KEY` in
`cli._load_dotenv` (+ defensively in `claude_runner`). **Reverted** the same session: setting
ANTHROPIC_API_KEY forces the CLI onto the API-key account in preference to the working claude.ai
login (the CLI even warns: "connectors are disabled because ANTHROPIC_API_KEY ... takes precedence
over your claude.ai login"), and when that API account ran out of credit (hard 400 "credit balance
is too low") every Claude run failed though the login was fine. User's call: "dont set an anthropic
api, remove it from my env and give it a different name for our program to use." So: the mapping is
gone from both `cli._load_dotenv` and `claude_runner`; the backend no longer requires a key (it uses
the CLI login; genuine no-auth surfaces as a normal run_error); CL_KEY stays under its own name and
the `anthropic` provider reads `CL_KEY` (then a user-set ANTHROPIC_API_KEY) and passes it *directly*
to `anthropic.Anthropic(api_key=...)`, never via the global env var. (Diagnostic worth keeping: a
`python - <<'PY'` heredoc showed `ANTHROPIC_API_KEY set: False` yet PONG returned -- because no-path
`load_dotenv()` walks up from the caller's frame file and a stdin heredoc's frame is `<stdin>`, so
it never found `.env`; the real `cli.py` at src/infinienv/ finds it fine -- and the CLI used the
login.) Default model `claude-sonnet-5` (Sonnet chosen over Opus at
the user's direction: sandbox runs are long/iterative with many build-and-rerun cycles, so the
cheaper tier is the sensible default and stays capable; Opus available via the override),
overridable via `INFINIENV_SANDBOX_MODEL`.

### What was reused vs new

`sandbox/claude_runner.py` reuses `runner.py`'s `_interpreter_briefing`/`_repair_message`/prompt
refiner and `workspace.py`'s `build_workspace_dir`/`outer_sanity_check`/`ARTIFACT_FILES` -- not a
fork of the pipeline. Same five artifacts, same outer sanity check (incl. the teleport floor), same
self-repair loop, same metrics.json shape (`provider: "claude_agent_sandbox"`). Live narration
(`_describe_claude_message`) maps the SDK's streamed blocks to the same `on_stage` lines as the
OpenAI `_describe_stream_event`, duck-typed and best-effort. `pip install infinienv[claude]`
(`claude-agent-sdk`), lazy-imported; `claude` CLI must be on PATH.

Tests: `test_claude_runner.py` (10, hermetic -- narration mapping incl. malformed-shape silence,
`_copy_artifacts_from_dir`, backend dispatch routes claude with the opus default / default stays
openai/gpt, no-key ProviderError) + a CL_KEY mapping test in `test_cli.py`. Full suite 323 green.

### Live-verification (honest partial result)

Exercised end to end against the real Claude Agent SDK + `claude` CLI (maze/patrolling-enemy prompt,
`--assets none --no-refine-prompt`). Confirmed from the run's output, not the agent's self-report:
CL_KEY auth in use (the CLI's precedence warning names ANTHROPIC_API_KEY over the claude.ai login);
workspace prep; the agent genuinely read the copied engine primitives
(grid_collision/motion_patterns/animation/pathfinding/vision) and iterated on run_scene.py via the
absolute venv interpreter (briefing worked -- no python-hunting); live-narration parity; and,
validated *by* the run, the graceful mid-run failure path -- when the Anthropic account's API credit
was exhausted partway through, the SDK error was caught as `run_error`, the repair loop tried all 3
attempts, and metrics.json recorded an honest failure (success:false) instead of crashing.

NOT yet confirmed: a *successful* run emitting all 5 artifacts -- the CL_KEY account ran out of
credit (400 "credit balance is too low", reproduced on a trivial one-token call). Account/billing
state, no code fix. A full sandbox run makes many calls over many minutes, so it needs a funded
account; the earlier PONG smoke test ($0.09) and the ~20-min Opus run drained the available balance.
Once topped up, re-run `INFINIENV_SANDBOX_BACKEND=claude` for a green end-to-end. Reported as found.

Also this session: the sandbox metrics now record `"model"` (both backends) for the audit trail --
motivated by the user asking which model was in use. And the GUI gained an "Agent runtime" selector
(shown when --sandbox is checked; POSTs `sandbox_backend`, threaded through
`run_sandbox_generation(backend=...)` -- a per-run override of INFINIENV_SANDBOX_BACKEND, not a
second code path). Default Claude model set to `claude-sonnet-5` at the user's direction. Suite 325
green throughout.

---

## 2026-07-10 -- `SpriteBook`: paste every generated sprite, at good scale (sprite-usage regression)

A user compared two sandbox runs of the same Mario-rescue prompt. Earlier `runs/gui_1783691768`
looked great; later `runs/gui_1783714443` looked much worse. Diagnosed by reading both agent-
authored `run_scene.py` files and their `asset_cache/` dirs directly (not the agents' summaries):

- All 15 sprites generated fine into the later run's `asset_cache/` (agent, coin, brick, pipe,
  tower, gate, fireball, plant, princess, walker, ...). The regression was purely the draw loop.
- The later `draw_frame` (1) asked for key `'hero'` when the player object resolves as `'agent'`
  (so the hero fell back to a primitive), (2) drew coins/pipes/tower/gate/fireball/bricks/ground as
  primitives despite each having a generated sprite -- the "resolve then ignore" bug -- and (3)
  rescaled sprites to arbitrary per-entity pixel sizes (54/42/44) on a 1280-wide canvas, reading
  small/inconsistent. The earlier good run pasted everything via one clean `paste_sprite` loop at
  `TILE`/`TILE*3`.
- `sandbox_agent.md` already warned about resolve-then-ignore and shipped a `paste_sprite` example;
  the later run reproduced the bug anyway. Prompt-only guidance is insufficient here.

Scope correction: I first misread the user's "doesn't show health+score" as *praise* of the clean
run and asked whether to drop the HUD; the user corrected -- "no, i need the hud stuff." So the HUD
(principle 8) stays; the clean run's advantage is its art, not the missing HUD. Principle 8 left
untouched.

Fix (reusable primitive + self-review invariant, per this project's standing discipline, never a
per-case worked example): `engine/rendering.py` gained `SpriteBook(asset_paths)` --
`paste(img, key, cx, cy, size, anchor="center"|"feet")` (cached, records key used, returns False so
the primitive fallback still runs) and `unused_keys()` (resolved keys never pasted). One line --
`assert not book.unused_keys()` -- catches both an ignored sprite (unused) and a mismatched key (the
real key stays unused). Prompt: `SpriteBook` added to reusable-building-blocks and the asset-usage
section (with the assertion + a "consistent tile-tied sizes, not arbitrary per-entity sizes" scale
clause); self-review step 1's asset bullet upgraded from soft "confirm asset_cache isn't ignored" to
the concrete `assert not unused_keys()`. `engine/rendering.py` is already copied into every
workspace -- no builder change; the reference template (delegates to the real renderer, not a
hand-rolled loop, so never the bug site) just gained a comment pointing rewrites at `SpriteBook`.
Tests in `test_rendering.py` (present/missing key, unused_keys reporting incl. the hero/agent
mismatch, caching, feet anchor). Honest limits: `unused_keys()` is agent-discretionary (must be
built + asserted), not a harness floor -- you can't reliably tell primitives from pasted sprites in
`render.png`. Obstacle-avoidance (third complaint) stays covered by principle 4 + the hazard-threat
self-review invariant. Live-verification result appended after the run.

---

## 2026-07-10 -- Movement must be physics-verified, not a smooth scripted route

Follow-on user report on `runs/gui_1783727953` (a "CLIMB TOWER" platformer): "it doesn't have
floors, the plants are static moving upside down when you KNOW they should have functionality." The
run adopted the SpriteBook/animation changes (sprites fine), but reading its `run_scene.py` showed
the "animation, not simulation" failure: the hero is moved along a hardcoded waypoint `route` via
linear `interp` -- no gravity, no platform collision; `scene.walls` (the drawn platforms) are
decorative, so the hero glides through open air. It caps+asserts step size (teleport floor passes)
but never checks the hero is supported. Plants translate the whole sprite up (`oscillate*42`, a
mid-air bob) instead of emerging from the pipe; never use `pulse_cycle`.

User sharpened the requirement: "all movements need to be verified possible by the physics
environment." Stronger than "floor under feet" -- a *smooth* precomputed route clears every existing
check yet is as wrong as a teleport (positions assigned from a path, not produced by physics).
User chose self-review enforcement (not a harness floor -- a "grounded" outer heuristic is too
coupled to each run's coordinate system and would false-fail legit jumps/falls). Fix is
principle-level (primitives already exist: integrate_grounded_2d, move_with_collision, climb_step,
pulse_cycle):
- Principle 5 now names the smooth scripted route as the same defect as a teleport in a smooth
  costume, and states the rule: every movement must be physics-permitted, guaranteed by making the
  physics functions the only thing that moves an entity.
- Self-review step 1 gained a comprehensive invariant: each consecutive position pair is a legal
  physics transition (supported/falling/on-a-ladder-within-span/in-a-jump-arc; never floating over a
  gap, never crossing a wall). A waypoint-interpolated hero fails it.
- Principle 3 (emerging hazards): an emerge reveals/grows from its base (out of the pipe),
  pulse_cycle-driven with active tied to how far out -- not a whole-sprite translate through the air
  (the "static/upside-down" read).

Honest scope: self-review-enforced, not a harness guarantee -- the outer check still can't tell a
stepped sim from a convincing animation (§11's standing blind spot). Raises the bar, points at the
right primitives; doesn't make a scripted route impossible. Live-verification appended after a run.

---

## 2026-07-10 -- replay.gif must be watchable (too-fast sandbox replays)

Next run after the physics-movement fix (`runs/gui_1783730799`, DK-style 5-floor climb): the
gameplay/logic was genuinely good this time (gated walk/climb actions, real Gate rescue, bounded
steps -- physics guidance landed) and the user confirmed "it actually worked." The only remaining
complaint: "im blind but its soo quick" -- the whole climb was a ~5s, 50-frame GIF (sim wins in
~168 steps, sampled every 4th at duration=100ms). A correct run that blurs past in seconds reads as
"nothing happened, it just says you won."

Fix: a replay.gif watchability requirement in sandbox_agent.md -- a hard artifact-requirement bullet
+ a self-review step-2 check. Target ~8-20s total, ~70-110ms/frame, don't over-subsample (one gif
frame per 1-3 sim steps), hold the final win/lose frame ~1.5-2s; if the sim ends in very few steps,
slow the motion rather than ship a blur. Base renderer's save_replay_gif already defaults to a
watchable 220ms/frame, so this is scoped to sandbox custom draw loops that subsample hard -- no
base-renderer change. Prompt-only; self-review-enforced. (Note: I had started diagnosing this run's
contact rules as possibly disabled by *_struck flags initialized True, but the user's "it actually
worked" made clear the gameplay was acceptable -- the real issue was watchability, not fake hazards;
dropped that thread.)

---

## 2026-07-10 -- ladders must be drawn as one contiguous floor-to-floor span

User report on `runs/gui_1783731619` (ladder-tower rescue): "why are the ladders separated, this
cannot happen." Diagnosed from the code: the DATA was fine -- `FLOORS = [38,31,24,17,10,3]`,
`LADDERS = [(4,38,31),(11,31,24),...]` each span exactly one floor gap and `climb` uses the full
min..max span. The RENDER was the bug: `for y in range(upper+1, lower): if y % 2 == 0: draw ladder`
trimmed off both floor cells (range excludes upper and lower) AND skipped every other remaining cell,
so a continuous climbable ladder drew as sparse rungs floating in the gap, touching neither floor.
Pure render fidelity, same class as feet_anchor/SpriteBook (agent hand-rolls structure rendering, a
+1/%2 flourish breaks it).

Fix (reusable primitive + self-review invariant, no per-case example): SpriteBook.paste_column(img,
key, cx, y_top, y_bottom, tile) in engine/rendering.py tiles a sprite contiguously across the whole
inclusive vertical span (endpoints either order), records the key used (feeds unused_keys), returns
0 + draws nothing when unresolved -- so a ladder/pipe/column meets both floors by construction, no
range-trim or %2 gap possible. Principle 8 gained a "structure drawn as a continuous connecting span"
clause pointing at it; reusable-blocks list mentions it; self-review step 1 gained an invariant
(drawn cell span == climbable cell span incl. both floor rows; secondary: every ladder endpoint lies
on a real floor). Unit-tested in test_rendering.py (inclusive fill, either-order endpoints, missing
key draws nothing). Self-review-enforced render fidelity, not a harness guarantee (the outer check
can't see whether a ladder visually connects floors). Live-verification appended after a run.

---

## 2026-07-10 -- all generated sprites forced to a cohesive blocky pixel-art style (live-verified)

User: "make sure all images generated are block style so that it fits seamlessly" + "raise the bar
on yourself." Both SPRITE_PROMPT_TEMPLATE and TEXTURE_PROMPT_TEMPLATE in generator_openai.py (and the
DIFFUSION_* equivalents in generator_diffusion.py, for parity) now explicitly demand retro-16-bit
BLOCKY pixel art: chunky visible square pixels, a small flat palette, a bold dark outline, flat cel
shading, and explicitly NO smooth gradients / photorealism / 3D / soft shading. Kept the test-required
wording ("isolated object" in the sprite template, "seamless" in the texture template) so
test_generator_openai.py stays green.

Raised the bar per the user: actually live-verified against the real OpenAI Images API instead of
deferring. Generated brick (texture), coin, a green-tunic hero, a man-eating plant monster, and a
round purple enemy -- all came back as consistent chunky pixel-art with flat palettes and bold
outlines that sit together seamlessly at tile scale (inspected the PNGs visually, not just success
codes). Two initial attempts with copyrighted-reading descriptions ("carnivorous piranha plant",
"Italian plumber hero") hit the pre-existing 400 moderation_blocked -- a *description* problem
already documented in section 9, independent of the style change; generic descriptions
("green tunic and cap", "green man-eating plant creature") generated fine. No functional/pipeline
change -- prompt text only; crop/transparency/texture branching and caching all unchanged.

---

## 2026-07-10 -- live end-to-end sandbox verification: procedural-cave prompt (runs/cave_verify)

"Raise the bar" + a real prompt to tackle: "Create a procedurally generated cave with uneven
terrain, hazards, collectibles, and multiple possible paths... collect at least two gems before
exiting." Ran it live (--sandbox --assets generated, seed 7, gpt-5.6-terra), SUCCESS on attempt 2
(1 repair). Verified from the SYNCED CODE + extracted frames, not the agent's summary.

Confirmed working (this run exercises most of this session's fixes at once):
- Physics-verified movement: hero driven entirely through ActionSpace actions with real gravity
  (vy += 520*DT), a jump arc, and a terrain_y ground clamp. The only `route` list draws the ledge
  graphics, NOT hero motion. Self-check asserts step cap <=26, all trace actions in the declared
  set, a real hazard hit (health 3->2 from a triggered falling rock), and >=2 creature states.
- Hazards matter: jumped the spike pit, took a real rock hit (HIT! -1 HEALTH), reactive creature
  (BehaviorMachine patrol->chase->return via vision.can_see + pursue) visibly entered CHASE.
- Gems gate the exit: Gate({"gems":2}); collected 3/2; exit only unlocks via gate.is_open, win
  requires exit_unlocked. Asserted.
- SpriteBook used + `assert not book.unused_keys()`; all sprites generated (asset_notes empty);
  block-style art (headlamp hero, crystals, door). Watchable replay: 194 frames / ~17.5s @ 90ms +
  ~1.6s held win frame. "ROCK HOLDS" climbable drawn as one contiguous column (no separated
  segments -- the paste_column lesson held, though it hand-drew the span rather than calling the
  helper). Six reusable primitives composed (ActionSpace, BehaviorMachine, vision, motion_patterns,
  puzzle_state.Gate, SpriteBook).

Honest gap: NOT truly procedurally generated. terrain_y and hazard/gem positions are hand-authored;
engine/level_generation.generate_organic_region (which exists for exactly this) was not used, so a
different seed yields the same layout. "Multiple paths" exist visually (upper rock-holds ledge vs
main route) but the sim plays one fixed safe route rather than simulating a choice among branches.
Principle 3 already points at generate_organic_region for "procedurally generated" terrain; the
agent under-used it here. Reported as found, not overclaimed -- the one real shortfall against the
prompt; everything else the prompt asked for is present and genuinely simulated.

---

## 2026-07-10 -- don't cheese "procedural": seeded side-view generators + anti-cheese principle 9

User pointed at runs/gui_1783735401 (procedurally-generated open-world cave, viewbox follows player):
"it needs to have the capability to do that and many other things in the similar realm, it cant be
cheesing the prompt." Split verdict from the synced code: the open-world CAMERA is genuinely real
(world 70 tiles/2240px vs 800px view; camera_x smoothly follows hero, clamped, world->screen xy());
but "procedurally generated" was CHEESED -- level is hardcoded (platforms=[(1,21,18),(7,13,19),...]
+ fixed ladders/gems/hazards), and the only random.Random(42) draws background ambience dots: a
decorative fixed-seed RNG as camouflage. (cave_verify cheesed the same way via a hand-authored
terrain_y.)

Root causes: (1) capability gap -- generate_organic_region is TOP-DOWN grid only; refiner makes
these SIDE-VIEW platformers (continuous ground profile or discrete platforms+ladders), which it
doesn't produce, so hardcoding was easiest; (2) enforcement gap -- principle 3 permitted
"hand-authored or generated" and nothing proved seed-variance.

Fix (real primitives + general principle + mechanical self-review, per discipline):
- engine/level_generation.py gained seeded side-view generators (top-down generate_organic_region
  unchanged): generate_terrain_profile (uneven heightmap, bounds+max_step, varies by seed),
  carve_gaps (pit columns clear of ends), generate_platform_layout (returns (platforms, ladders) in
  the (left,row,right)/(col,top,bottom) shapes these runs use; every adjacent level ladder-connected
  by construction; varies by seed), scatter_on_supports (seeded placement on real supports, spaced,
  off pits/ends).
- sandbox_agent.md: NEW principle 9 (implement the capability, don't hardcode a fixed instance of
  its output; names the decorative-RNG-as-camouflage tell; self-test "if you can't change the seed
  and get a different-but-valid level, you hardcoded it"). Closed principle 3's loophole (when the
  prompt asks for generation, hand-listing is cheesing). Reusable-blocks entry updated (top-down vs
  side-view). Self-review step 1 gained a seed-variance invariant (two seeds -> different valid
  layouts) -- the check a hardcoded layout can't pass.
- Tests: test_level_generation.py (+7): determinism, varies-by-seed, bounds, ladder-connectivity by
  construction, scatter spacing/pits/ends, bad-param rejection.

Honest scope: self-review-enforced, NO harness lever -- detecting hardcoded-vs-generated needs
comparing two seeds and the outer process never executes sandboxed code (isolation invariant), so a
harness seed-variance check isn't possible without breaking it. Live-verification appended after a run.

**Live-verified (runs/procgen_verify):** cheese gone. Agent imported the new generators, built the level from the seed via build_layout(seed), and adopted the seed-variance self-check unprompted (assert (TERRAIN,PITS,PLATFORMS) != build_layout(SEED+1)[:3]) -- run passed it, so the layout genuinely varies. Independently confirmed seed 5 vs 6 differ (terrain/gaps/platforms). Render shows a real generated uneven cavern with pits, a contiguous ladder, scattered gems + falling-rock/lava/spike hazards; open-world camera preserved. Not the hardcoded 5-platform layout of gui_1783735401.

---

## 2026-07-10 -- GUI sandbox model picker (OpenAI + Claude variants)

User asked to pick the sandbox agent model from the frontend, including the other available variants
(gpt-5.6 sol/luna, etc.) and Claude models. Discovered the real available IDs by querying the
account: OpenAI has gpt-5.6-terra/sol/luna, gpt-5.5, gpt-5.5-pro (among others); Anthropic exposes
claude-sonnet-5, claude-opus-4-8, claude-fable-5, etc.

Added an "Agent model" picker to the GUI (templates/index.html) shown when --sandbox is checked,
next to the existing Agent runtime selector. Its options track the backend: OpenAI ->
gpt-5.6-terra(default)/sol/luna, gpt-5.5, gpt-5.5-pro; Claude -> claude-sonnet-5(default),
claude-opus-4-8, claude-fable-5. JS repopulates the <select> on backend change. Payload gains
sandbox_model. gui/app.py: SANDBOX_MODELS allowlist per backend (first = default), validated in
api_generate (unlisted/mismatched -> 400, never forwarded), threaded through _run_sandbox_job(model=)
to run_sandbox_generation's existing model param (the frontend equivalent of INFINIENV_SANDBOX_MODEL;
run_sandbox_generation already had the param and the env fallback). Not a second code path -- a GUI
control over existing plumbing, same discipline as the backend picker. Tests in test_gui.py: threads
model through, rejects a Claude model under the OpenAI backend, accepts a Claude model under Claude.
Frontend SANDBOX_MODELS kept in sync with app.py's by comment. CLI unchanged (already has
INFINIENV_SANDBOX_MODEL / the model param).

---

## 2026-07-10 -- systemic anti-cheese: an independent faithfulness auditor (the whack-a-mole fix)

User stepped back: we'd fixed ~6 sandbox issues one at a time (sprite usage, physics movement,
ladder render, watchability, procedural-gen, perception), each a per-incident triple (primitive +
prompt principle + self-review invariant). All the SAME failure: the agent produces something that
passes the mechanical outer check and looks right but FAKES the requirement (cosmetic fog-of-war over
a ground-truth-beeline solver; "procedural" that's hardcoded + decorative RNG; smooth motion with no
physics; vacuous self-checks). Root cause: sandbox mode has no external semantic validator, so the
author grades its own work. Deterministic semantic checking is ruled out (reintroduces the fixed
vocabulary), so the only judge of open-ended intent is an LLM -- the lever is WHO judges. User chose
an independent auditor.

sandbox/auditor.py::audit_run(out_dir, refined_prompt): runs after outer_sanity_check passes, before
success is finalized. Fresh LLM instance (adversarial prompt llm/prompts/sandbox_auditor.md, OpenAI
Responses API direct -> cross-model when author is Claude), reads run_scene.py AS TEXT (never
executed) + trace + the agent's declared rules block, returns PASS or FAIL+findings. A FAIL feeds the
SAME repair loop an outer-check failure does (_repair_message gained an audit_findings branch); the
author gets the specific cheat and must fix it. success now also requires audit_passed. metrics gains
audited/audit_passed/audit_findings (+ per repair_history entry). Both backends share it (runner.py +
claude_runner.py). Best-effort: no OPENAI_API_KEY / INFINIENV_SANDBOX_AUDIT=0 / API error /
unparseable -> audited=False,passed=True (never fails a run because the auditor couldn't run).
INFINIENV_SANDBOX_AUDITOR_MODEL overrides model.

Supporting: (1) rules contract -- sandbox_agent.md asks for a machine-readable `rules` block
({requirement, enforced_by}) in metrics.json (the auditor's coverage target). (2) Meta-principle --
the per-incident principles now sit under one "faithfully implement the spec, never fake it" head
(self-test: could you change the seed/perceived-cells/physics and still be correct, or did you
hardcode the appearance?), so the prompt stops accreting a named bug per incident. (3) Perception
capability -- engine/perception.py (KnowledgeMap: the solver's fog-of-war memory it plans over, not
ground truth; visible_cells: one LOS+radius rule) + principle 10 (closed perception model, the
read-side twin of the closed action space). The auditor is what enforces "solver follows the author's
perception rules" generally.

Honest bounds: probabilistic reviewer, not a guarantee (can miss / false-flag; repair budget caps
it). Does NOT violate section 2 default-path invariants -- sandbox-only, reads code as text and never
executes it (outer process still never runs sandboxed code). It reintroduces a verifier, but a
semantic LLM one, and separates author from grader. Tests: test_auditor.py (hermetic, faked OpenAI:
verdict parsing, payload, best-effort skips), test_perception.py (KnowledgeMap/visible_cells),
test_sandbox_runner.py (audit FAIL forces a repair + is recorded; persistent FAIL fails the run; skip
never blocks). Suite green. Live-verification appended after a run.

### Auditor live-verification (isolated) -- caught a cheat the hand review missed

Ran the real auditor against two already-captured runs. gui_1783742552 (fog-of-war minecraft): FAIL,
named the exact perception cheat (run_policy navigates ground-truth game.layout.diamond/coal/iron
while visible_cells is only in draw_frame) PLUS a missed one (cosmetic "PRESS R TO RESTART", no
restart action). procgen_verify (which notes.md had recorded as a clean success): FAIL for a cheat
the HAND REVIEW MISSED -- generation was genuinely seeded/varied (verified), but the generated upper
platforms/ladders are cosmetic: advance() only collides with the lower terrain, the optional upper
gem is unreachable and not in collection logic, so "multiple possible paths" was faked. Correction to
the earlier procgen_verify entry: its generation was honest, its navigable multiple-paths were not.
The independent auditor caught what deliberate manual code review overlooked -- the core value of the
mechanism. End-to-end (auditor forcing a repair in a fresh run) appended after that run completes.

---

## 2026-07-11 -- structured, pretty GUI run view (replaces the raw scrolling log)

User: the sandbox run view was a wall of scrolling monospace log; wanted decisions, code writes,
command runs, assets used, and the audit surfaced as a categorized, pretty view. The narration
already encodes categories via stable prefixes, so this was mostly frontend + two small backend
additions.

Backend (gui/app.py): _classify_stage(msg) tags each on_stage line with a kind (command/edit/
decision/agent/audit/attempt/refine/workspace/image/error/status) carried on the SSE stage event
(runner/CLI on_stage(str) contract unchanged; unrecognized -> status, never breaks).
_sandbox_assets_summary(out_dir) lists sandbox_workspace/asset_cache/*.png (served via /artifact) +
asset_notes, added to the done payload as `assets`.

Frontend (templates/index.html): replaced the .log pane with a structured activity feed (icon+chip
rows, color-coded by kind, decisions in gold + audit pass/fail color-coded, segmented by attempt
dividers), a sticky phase header (Refine->Build->Audit->Done, attempt N/M, elapsed timer + spinner),
verdict cards on done (Result / Outer sanity / Faithfulness audit [Passed/Cheat found/Not verified
from audited+audit_passed+audit_note] / Repair attempts), and an Assets-used thumbnail grid with the
rate-limit note. render/replay + collapsibles (refined prompt, agent summary, scene.json, metrics,
raw log) below. Design system extends the existing light/dark CSS vars; verified pretty in both
themes via the browse skill (injected a realistic 2-attempt run + done payload, screenshotted light
and dark).

Also fixed two auditor honesty bugs found while verifying: the live auditor_verify run had SKIPPED
its audit (audited=False) on a transient error yet the log said "passed the faithfulness audit,"
letting a cheat ship as SUCCESS. Fixes: auditor.py retries the API call once before skipping;
runner + claude_runner now record audit_note in metrics/repair_history and report "audit skipped,
not verified" distinctly from "passed the audit"; the GUI verdict card shows "Not verified" (warn)
when audited=False. Tests: test_gui.py (_classify_stage mapping, stage events carry kind, sandbox
done payload includes assets); auditor honesty covered by existing test_auditor/test_sandbox_runner.

---

## 2026-07-11 -- GUI identity redesign: "World Foundry" (frontend-design pass)

User invoked the frontend-design skill: make the GUI look really good with a distinctive point of
view. Redesigned templates/index.html (CSS + markup; JS wiring and all element IDs preserved, tests
green). Design grounded in the subject (a console that compiles prompts into playable game worlds):

- Concept: "World Foundry" -- a control station; form mirrors function.
- Palette: phosphor console (deep indigo-navy base) with a MULTI-signal set mapped to meaning --
  gem cyan (primary/edit), coin gold (decisions), life green (pass), lava red (fail/hazard), plasma
  violet (AI audit). Deliberately NOT the generic near-black+single-acid default; each color is one
  of the game's own element colors and encodes a category/verdict.
- Type: system monospace as the display/console voice (no CDN, subject-true), system sans for prose;
  identity from scale, tight tracking, uppercase mono micro-labels, a blinking gem cursor block.
- Signature: a faint tile-grid substrate (the coordinate grid every world lives on) that glows while
  building (body.building), plus pixel detailing (hard 4px corners, pixelated sprite inventory).
- Hero copy = the project's own motto: "A model proposes / the harness verifies / the agent proves"
  with the verbs color-mapped to the signal palette. Consistent vocabulary: compile a world ->
  Compile world -> World compiled -> Recent worlds.
- Committed to a single dark look (deliberate for a game-hardware instrument), reduced-motion +
  mobile breakpoint respected.

Verified via the browse skill: injected a realistic 2-attempt run + done payload, screenshotted the
full page -- categorized feed (gold decisions, cyan edits, violet/red audit), segmented phase bar,
verdict gauges, pixel asset inventory, and the render/replay viewport all read cleanly; no JS console
errors. test_gui.py green (all IDs preserved). Not treated as reference-perfect -- a real end-to-end
run would confirm the live feel, but the structure + styling are verified.

---

## 2026-07-11 -- smarter `auto` assets + view_image path fix (+ failed-run diagnosis)

**Failed-run diagnosis (runs/gui_1783792588, fog-of-war minecraft prompt).** repair_history told the
whole story: attempt 0 crashed on `Error running tool view_image: manifest path must be relative:
/private/var/.../review_start.png`; attempt 1 PASSED the outer sanity check but the AUDITOR correctly
caught the perception cheat ("line-of-sight restriction is cosmetic; visible() used only for
rendering") and forced a repair; attempt 2 hit the view_image absolute-path crash AGAIN -> budget
exhausted -> failed. So: (a) the auditor worked (caught the exact fog-of-war cheat), but (b) a tooling
bug denied the agent a real chance to fix it. The agent's self-review step extracts frames and calls
the sandbox `view_image` tool, but passes an ABSOLUTE path (os.path.abspath / temp dir), which the
tool rejects ("manifest path must be relative"), and unhandled it crashes the whole run.
Fix: sandbox_agent.md self-review step 2 now states view_image needs a workspace-RELATIVE path
(view_image("review_start.png")), naming the exact error, so the agent writes frames into the
workspace and passes bare filenames.

**Smarter `auto` assets (user request: "auto should combine both -- use openai for generation and
locally write the simple stuff").** resolver.py: added SIMPLE_LOCAL_TYPES =
{wall,floor,box,door,exit,key,hazard,distractor}. In `auto` mode these resolve to their checked-in
local placeholder with NO image-API call (note "auto: simple type drawn locally"); only the types
that benefit -- agent (character) and novel/custom types -- are OpenAI-generated (still falling back
to local on failure). Rationale: wall/floor are in nearly every cell and were the exact types hitting
the 5/min 429 rate limit, so local for them is faster + reliable while agent/custom still get real
art. `generated` mode unchanged. Tests updated (auto splits simple->local-no-call vs complex->
generate) and a new test asserts a simple type is never sent to the generator while agent is. GUI
`auto` option relabeled "generate the complex, draw simple ones locally". Suite green.

---

## 2026-07-11 -- sandbox mode is now the DEFAULT generate path (the MVP)

User decision: make sandbox the default. Flipped it in code: cli.py `--sandbox` now has an implicit
default of True (set_defaults(sandbox=True)) with a new `--no-sandbox` opt-out (store_false) for the
deterministic validator-wins path; GUI sandbox checkbox is now `checked` by default (frontend always
sends sandbox:true). A plain `generate --prompt ...` runs the sandbox agent.

This flips §2's former non-negotiable "no model-authored code in the default path" invariant. Handled
honestly (a stale CLAUDE.md is worse than none): §2 rewritten -- the deterministic path is unchanged,
still exists, and remains the truth-bearing one (`--no-sandbox`, the only path the no-key mock demo
works on, the one with the hard solvability/programmatic-reward guarantee), but it's now opt-in rather
than default; the default DOES run model-authored code (isolated per-run copy, labeled
"source":"sandbox", reviewed by the independent auditor), a disclosed trade. §11's "making sandbox the
default" removed from out-of-scope and replaced with a dated "sandbox is now the default" subsection.
§12 CLI ref and §17 safety section updated. README 60-second demo: default = sandbox (procedural cave,
needs OpenAI key + `pip install -e ".[openai]"`), no-key path = `--no-sandbox --provider mock`.

Blast radius handled: test_cli.py's deterministic-path calls got `--no-sandbox` (the mock demo needs
it), + a new test asserts a plain `generate` routes to run_sandbox_generation. GUI API still treats an
absent `sandbox` key as False (frontend always sends it), so API tests posting explicit values are
unaffected. Suite 374 green.

Honest caveat recorded for the GI submission (§11): the challenge's headline is that code-defined
objectives beat a VLM on pixels; sandbox-as-default softens that (reliability leans on the agent's own
code + the LLM auditor, not a deterministic solver), which is exactly why --no-sandbox is kept
first-class for the reward-data use case.

---

## 2026-07-11 -- sandbox is the ONLY generate mode + it runs the deterministic validator

Two linked user decisions, one change.

1. Sandbox is the only `generate` mode (chose the minimal-deletion scope). cli.py: cmd_generate
always routes to the sandbox runner; removed --no-sandbox/--provider/--no-fallback from generate
(--sandbox kept as an accepted no-op). The deterministic engine is NOT deleted -- the sandbox copies
schema/engine/navigation/validation/render/assets into every workspace, and validate/solve/mutate/
curriculum/benchmark/export-dataset still run the fixed-vocabulary validator+solver over any scene.
GUI form dropped the sandbox toggle + provider/no_fallback fields (always sandbox); app.py keeps a
non-form non-sandbox API route so the hermetic mock test still works. api_runs now also scans
examples/ so a no-key reviewer sees a real world in the gallery.

2. "Sandbox should not trade away the validator checks." outer_sanity_check now runs the real
validate_scene on the sandbox scene.json and ENFORCES the vocabulary-agnostic geometry codes
(_ENFORCED_VALIDATION_CODES = {OUT_OF_BOUNDS, DUPLICATE_ID} -- real bugs for any mechanics; failing
them feeds the repair loop). deterministic_validation_summary records the full verdict (valid + all
error codes + enforced_codes) in metrics.json. Vocabulary-specific errors (UNSUPPORTED_OBJECT_TYPE,
MECHANICS_*, UNREACHABLE_OBJECT, NO_GOALS, ILLEGAL_OVERLAP) and fixed-vocabulary UNSOLVABLE are
recorded but NOT enforced -- a sandbox scene legitimately escapes the fixed vocabulary, so enforcing
them would false-fail. Honest limit: fixed-vocabulary solvability genuinely can't transfer to
agent-authored gameplay (the planner can't play arbitrary code); the outer image checks + the audit
+ the agent's own trace invariants stand in for it.

No-key consequence: sandbox needs an OpenAI key, so README's no-key path is now `solve examples/*.json`
(deterministic tools, offline) + a committed examples/example_world/ (procgen_verify's artifacts) the
GUI surfaces. Docs: §2 invariant rewritten (sandbox-only; deterministic engine is the substrate + its
geometry checks run on every sandbox scene), §11 subsection (default -> only mode + validator-on-
sandbox design + honest limit), §12 CLI ref, metrics.json example gains deterministic_validation, §15
test list. Tests: test_cli repointed to example scenes (validate/solve incl. a push scene) + generate-
is-sandbox-only; test_sandbox_workspace geometry-enforcement cases; test_gui index-is-sandbox-only.
Suite 375 green.

Honest note for the GI submission: sandbox-only softens the brief's "code-defined objectives beat a
VLM" headline (reliability now leans on the agent's code + geometry validation + the LLM auditor, not
a full solvability guarantee) -- which is why the deterministic solve/mutate/export-dataset machinery
is kept first-class for the reward-data use case. Live verification (deterministic_validation in a real
run's metrics) appended after a run.

---

## 2026-07-11 -- root cause of budget-exhausted sandbox failures: the prompt refiner over-scopes

User showed two consecutive audit-failed runs (budget exhausted) on cave prompts. Both failures were
about `restart` -- a death/respawn/regenerate-the-cave subsystem. Checked runs/gui_1783819810's
original vs refined prompt: the ORIGINAL never mentioned death/restart ("procedural cave, uneven
terrain, spikes, gems, safe route, collect 2, exit, varies each run"), but the REFINER invented
"Touching spikes or falling into a bottomless pit ends the run and allows an immediate restart with a
newly generated cave" plus branching risk routes, fixed+timed floor/wall hazards with warnings, etc.
So the agent was graded (and failed) on a hard subsystem the user never asked for. Every requirement
the refiner adds is another thing the independent auditor checks -> a faked add-on fails the whole
run. The refiner was turning doable prompts into unreliable maximal specs.

Fix (llm/prompts/prompt_refiner.md, not per-case): told the refiner every requirement is independently
audited and a single faked/half-built mechanic fails the ENTIRE run, so a lean spec fully delivered
beats a rich one; explicitly forbade inventing whole secondary subsystems the user didn't ask for --
above all a death/restart/respawn/regenerate mechanic unless the user mentioned dying/losing/restarting
(a simple "touching a spike ends the run" is fine, never "restart with a newly generated level"); and
forbade stacking many simultaneous mechanics. Rebalanced "keep it proportionate" from "rich and
concrete / most impressive" toward "the smallest spec that captures the user's intent, one a single
agent can fully build and pass an audit on in a couple of attempts."

Live-verified on the exact failing prompt: the refined spec now contains NO restart/respawn/regenerate
language (was the whole failure cause), keeps a simple lose condition, and preserves the user's real
intent (procedural varies-each-run, branching routes, spikes, 2-gem gate). test_prompt_refiner (mocked,
so unaffected) + full suite green. Complementary levers still available: a stronger model tier for hard
prompts, a bigger repair budget.

---

## 2026-07-11 -- competitors must share the player's physics (crippled-CPU cheat)

User: a Pong sandbox run's CPU was much slower than the player. Confirmed from the code:
PLAYER_SPEED, CPU_SPEED = 7.0, 0.65 -- the CPU paddle moves ~11x slower, so the 7-0 "win" is a
walkover, not real play. The multi-actor form of a decorative hazard: the opponent looks like a
competitor but physically can't compete. User's general point: every actor's actions must be bound
by the same physics.

Fix, three layers (principle-level, not per-case):
- sandbox_agent.md principle 1: added "the same closed physics governs EVERY actor, not just the
  player" -- opponents/AI/enemies/NPCs move through the same action/physics functions and caps
  (speed/accel/collision/bounds) as the player unless a fair asymmetry is explicitly declared; the
  only thing that differs is the decision logic, never the physics available. Named the 0.65-vs-7.0
  example.
- Self-review invariant: assert an opponent's largest per-frame move is within the SAME bound as the
  player's; a win/loss that only happens because the opponent moves far slower/faster is fake.
- sandbox_auditor.md: added a "crippled or asymmetric opponent" recurring-fake bullet. First pass
  (spec-relative wording) the auditor PASSED the pong run -- the refined spec didn't demand a
  "competitive" opponent, so a slow-but-present CPU didn't fake a stated requirement. Sharpened to:
  a competitive game inherently implies a contest, so an egregious order-of-magnitude (~5x+) speed
  asymmetry is a fake even if the spec never said "fair opponent" (a modest gap is fine). Re-tested
  live: auditor now FAILS the pong run with a precise finding ("over a 10x movement-speed advantage
  ... a walkover, not a competitive Pong match"). Suite 375 green.

## 2026-07-11 -- the vision-policy loop: a pixel-observation env + a stand-in vision policy (section 11b)

Strategic question from the user: "for this brief what more could we do to make it far better and
more impressive." Honest read of the GI brief: its three "why this matters" bullets are ALL about
a consumer InfiniEnv never touched -- a vision-based policy that *observes rendered frames* and
emits *controller actions*. The project generated worlds, solved them with a deterministic
*symbolic* planner (reasons over code state), and exported programmatic reward -- but had no
frame-in/controller-out env and no pixel-observing agent anywhere (confirmed by grep: no gym/step
API, no vision loop). So it *asserted* the "code beats a VLM on pixels" thesis without
*demonstrating the bridge*. Chosen directions (user picked both): close that loop + a clarity pass.

Built (all reusing existing pieces, no new deps):
- `engine/env.py::InfiniEnv` -- a Gymnasium-compatible env (reset/step 5-tuple, no hard gymnasium
  dep). Observation = a rendered frame (`render_scene_image`, the same per-state renderer the
  replay uses). Action = a discrete controller set (forward/back/left/right/interact/wait). Reward
  = newly-completed goals via `is_goal_complete` (code truth, §2 invariant preserved -- only the
  *player* becomes pixel-based, no trade). `interact` is a "use" button the env resolves in code
  (unlock->custom-interaction->pickup->drop priority). Illegal move = no-op recorded in info, not
  a crash. Verified end-to-end by driving it with the deterministic solver's plan mapped to
  controller actions: kitchen_can terminates reward 1.0, warehouse_key (key+deliver) reward 2.0.
- `navigation/vision_policy.py::VisionPolicy` -- pixel->action stand-in. Sees ONLY the frame
  (base64 PNG) + the goal in words; returns a controller action. Never sees GameState. OpenAI
  vision default (OPENAI_API_KEY -- the funded key; CL_KEY has hit credit limits) / Claude opt-in;
  network isolated behind a `responder` callable so the whole loop is hermetically testable.
  Honest framing everywhere: a stand-in for GI's unavailable policy, proving the interface + the
  reward loop, not a competing policy.
- `evaluation/vision_runner.py::run_navigation` + `navigate` CLI -- writes episode.gif (frames the
  policy saw), episode.json (per-step action + code reward), metrics.json (vision_success, CODE-
  judged). The money line: also a naive `judge_final_frame` VLM verdict (`vlm_judge_success`) beside
  the code truth + `judge_agrees_with_code` -- a disagreement is the brief's bullet 2 made concrete.
  Best-effort (`--no-judge`, judge errors never fail the run).
- Prompt `llm/prompts/vision_navigator.md` (controller actions + "you see only the frame").

Tests: `test_env.py` (11 cases), `test_vision_policy.py` (fake responder), a faked-policy `navigate`
case in `test_cli.py`. Full suite 394 green (was 375; +19). One test fixed: I'd asserted 'leftover'
matches 'left' -- the whole-word regex correctly does NOT (that's the feature); corrected the test.

Clarity pass (README): new intro/headline leads with the loop + the thesis; `navigate` is the first
"run it in 60 seconds" command; pipeline diagram ends in "a pixel-policy plays it, scored by code
truth"; a dedicated section maps the brief's three unlocks to `engine/env.py`/`is_goal_complete`/the
VLM-judge contrast; demoted stale non-sandbox `generate --provider`/`--no-fallback` references
(generate is sandbox-only) in the CLI list, providers, GUI, and criteria sections. CLAUDE.md gained
section 11b + CLI-ref/test-list/release-checklist entries.

Live-verification (real OpenAI vision API, `gpt-5.6-terra`, `examples/vision_demo.json` -- a small
7x5 pickup+deliver scene added for a cheap reproducible demo): **first-try clean success.** The
pixel-only policy completed the deliver task in the optimal 7 steps (`right`, `interact`->pick_up,
4x `right`, `interact`->drop onto the sink), driven entirely from the rendered frames + the goal
text -- it never saw GameState. `metrics.json`: `vision_success: true` (CODE-judged via
is_goal_complete, not pixels), `total_reward: 1.0`, `goal_results: {deliver_can: true}`,
`vlm_judge_success: true`, `judge_agrees_with_code: true`. Confirmed the artifacts are real, not a
self-report: `episode.gif` is a genuine 8-frame animation, and the extracted final frame visually
shows the agent standing on the sink with the can delivered. README's `navigate` examples now point
at `examples/vision_demo.json` for reproducibility. Full suite 394 green (was 375; +19 new).

Note on the code-vs-VLM-judge contrast: on this clean, unambiguous scene the naive VLM judge
correctly agreed with the code truth -- the disagreement that proves the point is expected on
harder/ambiguous frames (a near-miss, an occluded target), not a trivially-clear delivered can. The
value is that both signals are recorded every run, so a divergence is captured whenever it occurs.

## 2026-07-11 -- decorative hazards + collapsed levels (the "easy level for itself" cheese)

User report on a sandbox cave run (`runs/gui_1783825764`, the procedural-cave prompt): "it just
made an easy level for itself with no actual challenges and doesnt even use half the map." Read the
actual `run_scene.py` (not the screenshot). Two root causes, both the same recurring class -- the
agent designs the easiest level its own trivial auto-solver can beat, then self-checks only "I won":
1. **Decorative hazards.** The 3 `wall_spike` hazards are mounted at row 2 (under the ceiling), while
   the hero walks rows 11-14 and its jump arc only rises ~3.7 tiles (JUMP=12, GRAVITY*DT=0.9) -- it
   can never get within ~5 tiles of them. And the controller's `danger_xs` explicitly EXCLUDES them
   (`[floor spikes] + [gaps]` only). So they can never kill; pure decoration. This is a textbook
   "declared hazard can't reach the player" fake -- which the auditor's OWN list already names -- yet
   the run's `self_check()` asserted the wall spikes *exist and are counted* (a proxy), not that they
   ever threatened the hero, and the auditor passed it.
2. **Collapsed level.** Terrain jammed into rows 11-14 of an 18-row grid (agent chose base_row=14,
   min/max 11/14), all 3 gems on the floor path (`terrain[col]-1`), the 4 platforms (rows 7-9) purely
   decorative (only hold crystals; never required), 1 trivial chasm, 3 hardcoded floor-spike columns.
   The refined spec explicitly asked for "ledges... branching routes... gems near riskier routes" --
   the agent built a flat floor with decorative ledges. Most of the map is dead air.

User chose "enforce both, generally" (no cave-specific patch, per the standing discipline). Fix:
- `sandbox_agent.md` **principle 3**: added a concrete clause -- a hazard geometrically out of reach
  (mounted above the jump arc, walled off) or left out of the avoidance/collision set is decorative
  no matter how it renders; the same collapse applies to the whole level (confining play to a flat
  strip with every objective on the easy ground while the structure is scenery = the easiest level,
  not the described one).
- `sandbox_agent.md` **self-review invariants** (the required checklist): (a) rewrote the hazard bullet
  from a passive "came within a plausible threat distance" into a forceful *per-hazard, by-trace*
  requirement that explicitly rejects the existence/count proxy and names the out-of-reach/excluded
  forms; (b) added a NEW invariant -- the required objectives must actually make the player use the
  structure/risk the spec described (walk the successful trace; at least one required objective forced
  the player off the flat start ground / through a declared risk, and the declared structure is
  load-bearing, not scenery). Both phrased generally, no cave/gem references.
- `sandbox_auditor.md`: two new named fakes -- "A decorative hazard that can't reach the player"
  (check the trace: did the player ever come within contact distance of each declared hazard?) and
  "A level collapsed to its easiest version" (spec describes structure/risk but every required
  objective sits on the flat path and the structure is decorative; the generation may even be
  genuinely seeded yet the challenge is faked down to a straight stroll).

**Verification (real OpenAI auditor, re-run against the exact captured `runs/gui_1783825764`):** the
strengthened auditor now returns FAIL (the previous auditor had passed it clean), with two findings
matching the hand diagnosis precisely and citing the exact code -- the row-2 spikes vs the ~3.7-tile
jump ("cannot come within the < 0.62 collision radius even though resolve_spike_contacts() nominally
checks them") and the gems-on-the-floor collapse ("platforms are never objectives or required routes
... fakes the specified risk/route choice as decorative backdrop"). Since an audit FAIL feeds the
repair loop, a fresh run gets exactly these findings and must fix them. Full fresh live run on the same prompt (`runs/cave_fix_verify`, `--assets none`): **the loop
caught and fixed exactly the reported problems.** repair_history shows attempt 0 FAILED the
strengthened audit with the collapse cheese verbatim ("the claimed generated uneven terrain is
cosmetic rather than playable ... collision platforms are hardcoded ... profile is used only to
draw the ceiling silhouette ... support_y()/physics() never use it. Build the solid floor/platform
geometry from the generated profile so seed-driven uneven floors actually affect movement and
routes"); attempt 1 FAILED on a separate real cheese (hardcoded LAYOUT = build_layout(SEED=7), so
re-runs produce identical caves); attempt 2 PASSED. The final level has load-bearing uneven terrain
(the hero's trace spans a 3.9-tile vertical range, not the old flat bottom strip), real chasms
requiring jumps, and spikes placed in the traversed play area rather than decoratively overhead --
visually confirmed in render.png. Not claimed as a maximally hard level, but the two specific
reported failures (decorative hazards, unused space) are fixed and verified end-to-end. Full suite
394 green (prompt/auditor edits are markdown; no code changed).

## 2026-07-12 -- the vision-policy loop is now a GUI mode (Generate <-> Navigate)

The navigate/vision loop (section 11b) shipped CLI-only; the GUI had zero references to it. User:
"is this added to the frontend" -> "yes update everything in the frontend." Added a **mode toggle**
(Generate <-> Navigate) to the single-page GUI, reusing the existing SSE/feed/verdict/media
machinery -- a frontend on `run_navigation`, not a second implementation.

Backend (`gui/app.py`): `_run_navigate_job` (mirrors `_run_sandbox_job` -- loads the scene, calls
`run_navigation` with an `on_stage` callback, emits a `done` with `mode:"navigate"`,
`success:vision_success`, `episode_url`); `POST /api/navigate` (validates scene against the offered
list for path-safety, backend/model/assets/max_steps/judge); `GET /api/scenes` (lists
`examples/*.json` -- the deterministic, guaranteed-playable set); `_classify_stage` gained
goal/step/judge cases; `/api/runs` broadened so a run qualifies with `scene.json` OR `episode.gif`
and carries a `vision` flag (using episode.gif as the thumbnail).

Frontend (`index.html`): a segmented Generate/Navigate toggle that swaps the form + result view and
the button label; navigate fields (scene dropdown defaulting to vision_demo.json, vision
backend/model reusing the SANDBOX_MODELS lists, assets, max steps, judge checkbox); mode-aware
`PHASES` (`Play/Judge/Done`); new feed KINDS (goal/step/judge) with CSS; a navigate `buildResult`
branch rendering four verdict cards (Vision policy [code truth] / VLM on pixels / Agreement /
Steps) + the goal line + episode.gif; gallery vision badge.

Tests (`test_gui.py`, +5, all hermetic with a faked VisionPolicy): both modes in the index,
/api/scenes lists examples, /api/navigate rejects an unknown scene, a full navigate job streams
stage+done with code-judged vision_success + served episode.gif, a vision run shows in /api/runs.
Full suite **399 green**.

**Live-verified in a real browser** (browse skill, GUI on :5057): Generate mode renders unchanged;
clicking Navigate swaps to the scene/vision form with the button reading "Run vision policy";
clicking it ran the pixel policy against `examples/vision_demo.json` end to end in ~20s -- the play
log streamed (`[7/40] interact -> drop can_1 +reward`), and the result showed the PLAY/JUDGE/DONE
phase bar, the four verdict cards ("Reached goal" / "Says done" / "Agrees" / "7 / 40 · gpt-5.6-terra
· openai"), the goal line, and the episode.gif -- with the new `nav_...` run appearing in the
gallery badged as vision. Screenshots captured at each step. No regression to Generate.

## 2026-07-12 -- navigate now plays sandbox-generated worlds too

User: "the navigate stuff should work on the worlds we generate with the sandbox." It was scoped to
`examples/*.json` on the theory that a sandbox scene.json might not be deterministically playable.
Tested that assumption against real sandbox runs and it was wrong in the good direction: every
sampled sandbox `scene.json` loads through the real schema, is deterministically SOLVABLE, and the
env steps it without crashing -- because sandbox agents reuse the real SceneSpec with fixed-
vocabulary goals (reach/deliver) and declare a reachable exit.

Change: `_list_scenes()` now offers the curated `examples/` worlds AND any generated run's
scene.json under `runs/` (newest-first, capped at 40), labeled `example:`/`run:`. Path-safety
unchanged (paths enumerated here, validated against this list in `/api/navigate`). The scene-hint
and CLAUDE.md §11b disclose the honest boundary: navigate plays the **deterministic reading** of the
declared scene (static layout + declared goals), scored by code -- NOT the sandbox agent's custom-
coded physics/win-condition, which the trusted process never runs (isolation invariant). So a
declared `reach exit` goal can be a *simplification* of the sandbox game's full narrative.

Tests: `test_scenes_endpoint_includes_generated_runs` (a runs/*/scene.json is listed, labeled
`run:`); fixed the examples test to match the new `example:`-prefixed labels. Suite **400 green**.

**Live-verified on the user's own cave** (`runs/gui_1783825764/scene.json`, the "easy level" from the
earlier fix): navigate played it end to end -- the pixel policy reached the exit in 6 steps,
`vision_success: True` (code-judged via is_goal_complete on the declared `reach exit`), a real
7-frame episode.gif. Notably it surfaced the code-vs-pixels contrast for real: `vlm_judge_success:
False` (DISAGREES) -- the VLM, judging the narrative prompt ("collect glowing gems... jumping over
spikes") from the final frame, said not done, while the code goal (reach exit) was met. Honest
reading: this isn't a clean "code right / VLM wrong" -- the declared `reach` goal is simpler than the
sandbox game's real gem-gated win condition (which lived in run_scene.py and the deterministic env
doesn't enforce), exactly the disclosed caveat. Reported as-is.

## 2026-07-12 -- faithful vision-play: a vision policy plays the REAL sandbox game

User showed a navigate run on a side-view sandbox platformer (`runs/lolipop` -> `runs/nav_1783872149`)
that looked horrible and failed (156 steps oscillating). Root cause: navigate played it through the
deterministic TOP-DOWN engine (`InfiniEnv` + `render_scene_image`), but the sandbox world is a
side-view platformer whose real rendering/physics/win (`Gate(requires={'gems':2})`, gravity, jump)
live in the agent's `run_scene.py`, which the trusted process never runs. Top-down of a side-view
game = a sparse mis-rendered grid the VLM can't navigate. Confirmed: lolipop IS solvable top-down,
so the failure was the alien frame + a wrong interpretation, not an impossible task. User chose
**faithful play**: a vision policy plays the actual game.

Built (isolation preserved -- the game runs only INSIDE the sandbox; the trusted process only reads
back episode.gif + vision_metrics.json):
- **make_env contract** (`sandbox_agent.md` + `_RUN_SCENE_TEMPLATE`): every run_scene.py exposes a
  module-level `make_env()` -> env with `.actions` / `.reset()->PIL.Image` / `.step(action)->
  (frame,reward,done,info)` (info["won"] from the game's own win), with two import-safety rules
  (make_env at module level; generation under `if __name__=="__main__"`). Reference template models
  it by wrapping InfiniEnv; a custom game exposes it over its own physics/render. Generation records
  `playable_env` via a smoke check in the same session.
- `sandbox/vision_play.py`: a self-contained driver copied into the workspace + run inside the
  sandbox -- imports make_env, drives it with an inline OpenAI-vision policy (frame + goal +
  env.actions), writes episode.gif (real frames) + vision_metrics.json (vision_success from
  info["won"]). `run_vision_episode(env, act, ...)` core is unit-testable with a fake env + fake
  caller.
- `sandbox/vision_runner.py::play_sandbox_world`: orchestrator reusing runner.py's session mechanics
  (temp-copy the run's sandbox_workspace, inject driver+config, fresh session with the same Manifest
  grants -> interpreter+network+key work inside, hydrate, session.exec the driver, extract the two
  artifacts, animation-check, aclose). A missing make_env -> clear "regenerate" ProviderError.
- Triggers: GUI Navigate routes a sandbox world (dir has sandbox_workspace/ + metrics source==sandbox)
  to faithful play (badge "real game - vision"); CLI `navigate <run_dir>` faithful, `navigate
  <scene.json>` deterministic. Honest limit: needs the contract, so only worlds generated after it
  (lolipop can't be retrofitted).

Tests: `test_vision_play.py` (8, hermetic -- fake game env + fake controller: win from info["won"],
PNG frames + action list passed, illegal-action coercion, truncation, game-step-error survival;
reference make_env import-safe + drivable; play_sandbox_world rejects a non-sandbox dir), a GUI
routing test (sandbox scene -> faithful path, mocked). Suite **409 green**.

**Live-verified end to end.** (1) Generated a fresh side-view platformer (`runs/faithful_gen_test`,
`--assets none`): `success:true`, **`playable_env: true`** -- the agent implemented make_env and the
smoke check confirmed it drivable (its run_scene.py builds a game around `e=make_env()` + `e.step`).
(2) Faithfully played it (`runs/faithful_play_test`, `gpt-5.6-terra`): the vision policy played the
REAL game inside the sandbox, choosing from the game's OWN action set **`['left','right','jump',
'wait']`** (jump! a side-view action, not the top-down forward/back/interact set), for 18 steps until
the game's own code hit a terminal state. `vision_success: False` from the game's own win (didn't
collect 2 gems + reach the sealed exit) -- an honest "the VLM didn't beat the platformer" result.
The `episode.gif` (1152x576, 19 frames) is the REAL side-view platformer -- a mid frame shows the
hero on a stone ledge, uneven platforms with gaps, floating cyan gems, white spikes, a "SEALED"
exit, HUD "GEMS 0/2", controls "A/D MOVE - SPACE/UP JUMP" -- nothing like lolipop's sparse top-down
grid. The naive VLM judge said "done" while the code said not-won (`judge_agrees_with_code: false`)
-- the code-vs-pixels contrast, again. (3) The pre-contract lolipop faithful play failed cleanly with
"This world wasn't built with a playable interface ... regenerate", no crash, no top-down garbage.
Reported as found: the mechanism is proven (real game, real actions, real code-judged win); the VLM
policy winning a real platformer is a separate, harder problem.

## 2026-07-12 -- faithful play now uses the world's real generated assets (+ picker mtime fix)

Two follow-on bugs from the first faithful-play cut, both found on the user's `absence` run:

1. **Picker ordering** -- `runs/absence` (a recent named run) didn't appear in the navigate picker:
   `_list_scenes`/`api_runs` sorted runs by NAME (`sorted(..., reverse=True)`), not mtime, despite
   the "newest first" comment. Name-sort only looked right for `gui_<timestamp>` names; `absence`
   ranked 100th of 101 and was cut by the 40-run cap. Fixed with `_run_dirs_newest_first()` (sort run
   dirs by mtime desc), used by both the picker and the gallery. Regression test added.

2. **Faithful play ignored the world's already-generated sprites** -- it "made its own character"
   (rendered primitives). Two causes: (a) `sandbox/vision_runner.py` stripped `asset_cache/` when
   copying the workspace, so even a correct make_env couldn't reuse the sprites; (b) the make_env
   contract didn't require rendering with real assets, so the agent's `make_env()` was
   `return CaveEpisode(SEED)` with NO asset_paths -> empty SpriteBook -> primitives, while only its
   `main()`/replay resolved the generated sprites. Fixes: keep `asset_cache/` in the copy; the
   reference `_RUN_SCENE_TEMPLATE` make_env now resolves ASSETS_MODE from `./asset_cache` and passes
   `asset_paths` to InfiniEnv; `sandbox_agent.md`'s make_env contract now REQUIRES rendering with the
   same real sprites the replay uses (resolve + SpriteBook inside make_env). Also added jump-caution
   to the vision player's system prompt (`vision_play.py::_PLAYER_SYSTEM`) targeting the "jumped into
   the void" complaint (only jump when there's a ledge to land on / a gap or hazard directly ahead).

Honest limit: `absence` (and any pre-fix world) has a make_env written without assets and can't be
retrofitted -- regenerate to get asset-correct faithful play. Suite **411 green** (+ a reference-
make_env-resolves-assets test + the picker mtime regression test).

**Live-verified.** Generated a fresh platformer with `--assets auto` (`runs/assetfix_gen`):
`playable_env: true`, and its make_env now calls `resolve_book(scene)` -> resolves the generated
sprites (agent.png, gem.png in asset_cache). Faithfully played it (`runs/assetfix_play`,
gpt-5.6-terra): the episode.gif renders the REAL generated sprites -- a mid frame shows a real
generated pixel-art explorer character (not a primitive box), generated crystal gems, red spikes, a
green exit, platforms with gaps, HUD "CRYSTAL CAVERN GEMS 1/2 SPIKES = ONE HIT". The policy chose
from the game's own `['left','right','jump','wait']` and collected a gem (1/2) before the game ended;
`vision_success: false` -- it didn't get 2 gems + exit in 30 steps. So the asset bug is fixed and
verified (real sprites, not primitives); the VLM reliably *winning* a real platformer remains a
separate, harder problem (the jump-caution + real frames help it see, but don't guarantee a win).

## 2026-07-12 -- faithful play hydrates from the real workspace, no host-side copy

User asked why faithful play copies the workspace to a temp dir instead of using the run's own
folder. It doesn't need to: the sandbox still requires its own hydrated FS (isolation -- the game
code must run inside the sandbox, never on the host), but the host-side temp copy (a full copytree
of the engine packages + asset_cache) was pure waste. Replaced it with `_tar_workspace_with()`:
tars `run_dir/sandbox_workspace/` in place and injects the trusted `vision_play.py` + config as
in-memory tar members (skipping __pycache__/.pyc), then hydrates. No temp dir, no copytree, no
cleanup, and the kept sandbox_workspace/ is never polluted (the driver/config exist only inside the
tar). Removed the `shutil`/`tempfile` imports. Round-trip + full-suite verified (412 green).

## 2026-07-12 -- faithful play observes occasionally (frame-skip / action-repeat), not every frame

User: faithful vision-play "should have occasional frame grabs, not grab every frame of the
simulation and choose which frames to do actions on." The driver was 1 vision call per env.step
(a decision on every simulation frame) -- unnatural and wasteful. Added frame-skip / action-repeat
to `run_vision_episode` (`hold`, default 6): each decision grabs ONE frame, the policy picks an
action, and that action is HELD for `hold` simulation frames while the game advances -- like a human
glancing, holding a key, glancing again. The policy is called once per decision, never once per
frame; all sim frames are still collected for a smooth gif (subsampled to ~140 for watchability).
`hold=1` reproduces the old behavior (tests). Metrics now report `steps` (decisions/vision-calls),
`sim_frames` (frames advanced), and `hold`. The player prompt tells the model its action is held
briefly so it commits to a move rather than reacting per-frame. Config/param `hold` (VISION_HOLD env,
`play_sandbox_world(hold=)`). Suite **414 green** (+ frame-skip + hold-default tests).

Live-verified on `runs/assetfix_gen` (--max-steps 25): **7 vision decisions produced 40 sim frames**
(hold 6) -- occasional grabs (1 per ~6 frames), not per-frame -- with the action held between, the
character moving/jumping continuously; a 41-frame watchable gif. (The game ended at decision 7 on its
own win/lose logic; vision_success False -- the VLM's platforming skill remains the separate limit.)

## 2026-07-12 -- faithful-play replay is real-time and ends when the game ends

User: "the replay should be in real time and should finish when the game is done." The episode.gif
used an arbitrary 110ms/frame + an 8-frame final-frame hold. Fixed: the game exposes `env.dt`
(seconds of game time per step -- e.g. the observed game's `DT=0.05`, 20fps) or `env.fps`; the
driver reads it (default 0.05 if absent) and `_save_episode_gif` now plays in REAL time -- total
playback == `sim_frames * dt` == the real game duration -- with NO padded final hold, so the replay
finishes exactly when the game did. When the sim produced many frames it subsamples by a stride and
stretches each shown frame to `stride*dt`, keeping total playback real-time while the frame count /
per-frame duration stay sane (gif renderers clamp very short durations). Added `env.dt` to the
make_env contract (sandbox_agent.md) + the reference template (`_Episode.dt = 0.2`). Metrics record
`game_dt` + `real_time_seconds`. Suite **414 green**.

Live-verified on `runs/assetfix_gen`: 46 sim frames at dt 0.05 -> a 47-frame episode.gif with total
playback **2.35s = 46*0.05** (real time, the game's own speed), final frame 50ms (no hold), ending
exactly when the game hit `done` at decision 8. Both requests satisfied.

## 2026-07-12 -- tested faithful play on `absence` (pre-fix world) + best-effort dt fallback

User asked to test faithful play on `runs/absence` -- a pre-fix run (no asset-passing make_env, no
env.dt; real DT=1/30). Added `vision_play._resolve_dt(env, module)`: env.dt -> env.fps ->
run_scene.DT/dt/TIMESTEP -> run_scene.FPS/fps -> 0.05 default. So a world built before the env.dt
contract (like absence) still gets a real-time replay from its module timestep constant. Hermetic
test added. `env.dt` remains the clean documented path; this is the fallback.

Live test (`runs/absence_play`, --max-steps 25): **frame-skip confirmed** -- 25 vision decisions for
150 sim frames (hold 6, occasional grabs, not per-frame); **real-time replay confirmed** -- game_dt
0.0333 (=1/30, read from run_scene.DT via the fallback, not the 0.05 default that would've played
1.5x slow), episode.gif ~4.6s ~= sim_frames*dt, ending on the final frame with no padded hold. The
policy collected 1 gem and got +reward each step; vision_success False (didn't get 2 gems + exit in
150 frames). **Honest caveat, as flagged:** the character/gems/spikes render as PRIMITIVES (orange
ellipse hero) because absence's pre-fix make_env ignores assets -- can't be retrofitted; regenerate
absence (new contract) for real generated sprites. Suite 415 green. runs/absence left untouched;
runs/absence_play cleaned.

## 2026-07-12 -- a live TODO + memory harness that makes a run true to the prompt (mcraft follow-up)

The mcraft run failed (placed plank had no break-table entry; hotbar excluded collected wood; auditor
caught it, repair budget exhausted). User's direction, via LangChain's "how to build a custom agent
harness": stop adding a per-case principle per failure -- give the agent a live TODO it maintains via
tool calls + a persistent memory, fed as context at every step, so it implements EVERY requirement.
Chosen impl (user picked "files as source of truth + a todo tool"):

- `sandbox/checklist.py::build_checklist` -- an independent LLM pass (mirrors prompt_refiner) turns the
  refined prompt into concrete `[{id,requirement,how_to_verify}]` items, derived independently of the
  builder so it can't drop a hard requirement. Best-effort -> [] (agent self-derives). Prompt in
  `checklist_generator.md`.
- `sandbox/workspace.py::seed_todo` writes TODO.json (source of truth: status/verified_by), MEMORY.md,
  and `todo.py` -- a seeded workspace CLI the agent runs via Shell (show/add/done/fail/note), mutating
  the files + printing greppable TODO_UPDATE/TODO_PROGRESS/MEMORY_NOTE lines. A workspace CLI (not an
  SDK function tool) so both backends work identically and there's no diverging harness-side state.
- runner.py + claude_runner.py: build+seed after refinement, thread the item list into the agent
  message (`_todo_brief`), re-inject still-open items on repair (`_repair_message(open_todo=)` -- FS
  persists so a repair resumes with TODO+memory intact), record final TODO.json as metrics.checklist.
- sandbox_agent.md meta-principle now LEADS with "work your TODO... call `todo.py done <id>` only after
  a real check that fails if faked; never finish while an item is pending." auditor.py + sandbox_auditor.md
  audit against the checklist (completeness + each done item genuinely implemented). GUI: a "todo"
  _classify_stage kind (live checklist) + a per-item pass/fail report from metrics.checklist. Repair
  default 2 -> 3.

Suite 425 green (+ test_checklist.py, todo.py/seed_todo in test_sandbox_workspace, repair-reinjection in
test_sandbox_runner, auditor-checklist in test_auditor).

**Live-verified two ways.** (1) Auditor-against-checklist on the captured `runs/mcraft` (real OpenAI
auditor): with a checklist, it returned FAIL naming the exact items -- "item r2 not genuinely
implemented: mine() indexes NEED['plank']/BREAK['plank'] which don't exist -> KeyError", "item r3 false:
HOTBAR omits wood" -- plus flagged the checklist incomplete. Per-item enforcement works. (2) Full
end-to-end fresh generation (`runs/todo_verify`, a cave-platformer prompt): the generator derived an
**18-item** checklist (each concrete, e.g. "collects exactly once, increments by exactly one"; "touching
the locked exit before 2 gems does not win"), seeded it, the agent worked it via todo.py, and finished
**18/18 done -- first try, repair_attempts 0, audit_passed True**, with substantive per-item verified_by
("Physics replay crosses four void gaps with declared jumps", "ActionSpace registers left and right"),
a real render.png + replay.gif. The harness makes fidelity a tracked, per-item, independently-enforced
contract. Honest scope: full Minecraft-lite remains a hard one-run build; auditor+repair are the backstop.

## 2026-07-12 -- floating goals popup in the GUI (live "currently on" tracker)

User: the goals should be a floating popup showing which one the agent is currently on. Added a fixed
floating panel (`#goals-popup`, top-right) to the GUI: it lists every requirement, dims+strikes done
ones, and HIGHLIGHTS the current item (cyan glow, ▶). Fed live from the stream -- `TODO_SEED r<n>: ...`
lines seed it, and the agent's `todo.py doing/done <id>` tool-call lines (which stream as `$ ...` command
narration) update statuses; current = the item marked `doing`, else the first not-done. Collapsible
(click the title) and dismissible (×), reconciled to the authoritative `metrics.checklist` on the done
event, reset on each run. To make "currently on" a real signal (not just an inference), added a
`todo.py doing <id>` subcommand (status "doing", verified_by preserved) + a nudge in `_todo_brief`
("call todo.py doing <id> when you START an item"). Suite 425 green. Live-verified in a real browser:
injected the exact stream lines -> the popup showed "GOALS 2/6" with two done (struck), "Collect at
least two gems" highlighted as current, the rest pending; collapse + close controls work (screenshot).

## 2026-07-12 -- split: REQUIREMENTS (acceptance) vs the agent's BUILD PLAN (popup shows the plan)

User clarified the goals popup should show Claude-Code-style progress points -- the PARTS OF THE
PROGRAM to build -- NOT the requirements (which are separate), and the build tasks should add up to
meet the requirements. Refactored the single "TODO = requirements" model into two:
- `REQUIREMENTS.json` -- the independently-derived acceptance criteria (auditor's contract; the agent
  does NOT tick these). Shown as a "Requirements" report + the audit verdict.
- `PLAN.json` -- the agent's own live build plan (progress points), starts EMPTY; the agent authors it
  with `plan.py add` and works it with `plan.py start/done`, ensuring the tasks cover every requirement.
  This is what the floating popup (now titled "Build plan") shows, current task highlighted.
Changes: workspace.py (`_PLAN_TOOL_TEMPLATE`/plan.py, `seed_workspace`, `read_requirements`/`read_plan`/
`open_plan_items`), runner.py + claude_runner.py (seed requirements, thread the reqs-vs-plan brief,
re-inject open build tasks on repair, metrics `requirements`+`build_plan`), sandbox_agent.md (author +
work a build plan that adds up to the requirements), auditor gets requirements, gui/app.py
(`_classify_stage` REQ_SEED/PLAN_*/plan.py) + index.html (popup parses plan.py add/start/done, title
"Build plan", requirements report from metrics.requirements + the audit verdict). plan.py `start`
replaces `doing`; items keyed by `task` not `requirement`. Tests updated (seed_workspace, plan.py
add/start/done, repair re-injects build tasks, classify_stage). Suite 425 green.

Live-verified in a browser: injecting the `plan.py add/start/done` command lines drove the popup to
"BUILD PLAN 2/5" with two tasks struck done and "gem pickup + counter" highlighted as the current one
(screenshot). The popup shows build tasks (tile world gen / jump physics / gem pickup / exit gate /
HUD), separate from the requirements report.

## 2026-07-14 -- popup fix: parse plan.py's clean OUTPUT lines, not the shell COMMAND line

User reported a GUI run whose build-plan popup showed a single garbled "1/1" item -- the whole spec
sentence plus a trailing `&& /Users/harjyot/GenInt/.venv/bin/python plan...` shell fragment mashed into
one entry. Diagnosed by reading the actual run (`runs/bottle/sandbox_workspace/PLAN.json`): the agent
behaved CORRECTLY -- PLAN.json held 4 clean tasks, all done, audit passed. The bug was purely frontend.
`handleTodoLine` was parsing the narrated shell COMMAND line (`$ ...python plan.py add "<task>" && python
plan.py ...`), so a `&&`-chained command became one garbled item and the real task ids were lost.

Fix: surface plan.py's own clean OUTPUT lines and parse those instead.
- `runner.py::_describe_tool_output` now returns any `PLAN_ADD/PLAN_UPDATE/PLAN_PROGRESS/MEMORY_NOTE`
  lines found in a command's OUTPUT even on a SUCCESSFUL (exit 0) command (previously it stayed silent on
  success, so plan progress never reached the GUI except via the fragile command line). Normal successful
  commands still stay silent; failures still surface.
- `claude_runner.py::_describe_block` mirrors this for the Claude backend's tool-result blocks (surfaces
  PLAN_/MEMORY lines from a successful tool result before the is_error check).
- `index.html::handleTodoLine` now parses the multi-line clean output (`PLAN_ADD t1: <task>` /
  `PLAN_UPDATE <id> done|doing`, with real ids) -- a `$`-prefixed command line no longer matches
  `^PLAN_ADD`, so the `&&` garble can't be captured. REQ_SEED lines still don't match, so requirements
  don't pollute the build-plan popup.
Regression test in test_sandbox_runner.py (`_describe_tool_output` surfaces PLAN lines on success, silent
for normal commands, still surfaces failures). Suite 426 green. Live-verified in a browser: injecting the
exact clean output lines the bottle run now emits drove the popup to a clean "BUILD PLAN 1/4" with four
separate tasks (t1 struck done, t2 highlighted current, t3/t4 pending) -- no `&&` fragment (screenshot).

## 2026-07-14 -- vision play in short PLANS, not one keystroke per model call

User pointed at the Navigate page playing `bottle`: the play log was `[44/60] up`, `[45/60] right`,
... -- **60 decisions = 60 separate vision/LLM calls, one keystroke each** (each shipping a full
frame to get back one token), and the run still failed ("Did not reach"). Direction: "it should use
vision to play but the actions shouldn't be done frame by frame." (Confirmed via AskUserQuestion that
the target is the vision navigate/play loop, not the build-side self-play.)

Fix: each vision call now returns a short ordered **plan** (up to `plan_len`, default 6) instead of
one action; the driver executes the plan action-by-action through the real `env.step()` (physics /
`hold`-frame collection unchanged), then re-observes -- or re-observes **early** if the level ends or
a move is blocked (deterministic path: `info["action_legal"] is False`; faithful path: a *stall* --
a `hold`-block that left the frame visually unchanged, `ImageChops.difference(...).getbbox() is
None`). Same gameplay length, ~`plan_len`x fewer model calls.

- `navigation/vision_policy.py`: `_parse_actions(raw, *, limit)` (ordered plan, appearance order,
  repeats kept, capped, never empty); `_parse_action` kept as `_parse_actions(...)[0]`;
  `VisionPolicy.act` returns `(list[str], raw)` with `max_actions`. `PLAN_LEN`
  (`INFINIENV_VISION_PLAN_LEN`, default 6).
- `sandbox/vision_play.py`: `_parse_actions`/`_idle_action`; `run_vision_episode` gains `plan_len`,
  executes plans with per-action `hold`, `_still_frame` stall-abort, records each decision's
  `plan`/`executed`, `env_steps`/`plan_len`; `_vision_act` returns a plan; `_PLAYER_SYSTEM` and
  `_config` (`VISION_PLAN_LEN`) updated; `main()` logs `[look d] plan: ...`. Back-compat: a bare
  string plan is coerced to `[string]`, `plan_len=1` reproduces the old one-action-per-look.
- `evaluation/vision_runner.py::run_navigation`: outer loop = vision calls, inner = the plan;
  blocked-move re-look; `episode.json` steps carry their `decision`; `metrics.json` gains
  `decisions` + `plan_len`. `sandbox/vision_runner.py` threads `plan_len` into the injected
  `vision_config.json` and `play_sandbox_world`.
- Prompts (`vision_navigator.md`, `_PLAYER_SYSTEM`): ask for a short ordered sequence of action
  words, note that a blocked/stalled move drops the rest and re-shows the frame (over-committing is
  cheap to recover from).

Honest framing kept in the code/docs: the guaranteed win is **fewer vision calls (tokens/latency)**
for the same gameplay length; it *may* also help success (a coherent few-step plan beats
frame-by-frame dithering; blocked-move re-looks avoid wall-bashing) but that isn't the claim. §2/§11b
invariants untouched -- pixels-only policy, code-defined success; only *how often the policy is
asked* changed. Suite 432 green (+6 vision tests: ordered-plan parsing, a plan of N in one decision,
bare-string coercion, stall aborts a plan early via a moving-frame fake game).

**Live-verified against the real OpenAI vision API, both paths:**
- Deterministic `run_navigation` (`navigate examples/kitchen_can.json --max-steps 30 --no-judge`):
  **3 vision calls** drove 15 env steps and SUCCEEDED (`vision_success=True`, `decisions=3`,
  `steps=15`) -- the pixel-only policy picked up and delivered the can. Before this change that
  would have been 15 separate vision calls. The `[look N] plan: ...` log and per-step `decision`
  tags (`[1,1,1,1,1,2,2,2,2,2,2,3,3,3,3]`) confirm the outer-look / inner-plan structure.
- Faithful sandbox play (`navigate runs/bottle` -- the exact case from the user's screenshot):
  **14 vision calls** drove 60 env actions / 360 sim frames (`decisions=14`, `env_steps=60`,
  `sim_frames=360`, `plan_len=6`, `hold=6`), each look a plan of up to 6 actions -- vs the
  screenshot's **60 single-keystroke calls**, a ~4.3x reduction for the same gameplay length, with a
  real 2 MB `episode.gif`. Honest outcome: `vision_success=False` -- the weak stand-in still didn't
  solve the maze, exactly as flagged (the guaranteed win is call-count/tokens, not success). The
  deterministic run above shows batching can *also* help when the level is solvable by the policy.

## 2026-07-14 -- a better navigation policy: feedback + memory + frame history (still pixels-only)

The faithful navigate of the `cartel` maze (`runs/nav_1784060137`) COMPLETELY FAILED:
`vision_success=False`, `total_reward=-0.76`. Reward is +0.05/move, -0.02/blocked, so -0.76 over 60
steps = **~54 of 60 moves were blocked** -- the policy moved ~6 cells into the top-right corner then
spammed a blocked direction (`d` into the boundary) for the rest of the episode (start+mid gif
frames both show it wedged in the same corner, `LAST: d (BLOCKED)`). User: "it completely fails as it
goes backwards all the time ... make a better policy for navigation solving."

Root causes (from reading `runs/cartel/sandbox_workspace/run_scene.py` + the run): (1) the driver
never told the policy a move was blocked, so it repeated it -- though the game DOES expose
`info["moved"]`/`info["blocked"]`; (2) the stall-abort added earlier was defeated by the live HUD
(`STEPS`/`LAST` change every frame, so `_still_frame` never fired -- and a whole-frame area diff
can't help either, a one-tile sprite move is <1% of the image); (3) no memory -- one context-free
frame each look -> oscillation/backtracking.

Decision (confirmed with user): stay a TRUE pixels-only policy (do NOT feed coordinates/a map -- that
would defeat the §11b demo). The harness may compute stuck-ness however it likes; only words+frames
reach the policy. Changes:
- `sandbox/vision_play.py`: `_moved(info, before, after, before_pos, after_pos)` prefers
  `info["moved"]`/`["blocked"]`/a position key over a frame diff; `_feedback_text(recent,
  last_outcomes, looping, actions)` (blocked-move summary / repeated-block "do NOT repeat" / stuck
  warning / untried-direction suggestion); `run_vision_episode` now aborts a plan on a BLOCKED move
  (not a frame stall), keeps a rolling `recent`+`visited` memory, passes the current frame + up to
  `history` recent frames (`VISION_HISTORY`, default 2) and the feedback text to the controller
  (new contract `act(frames_list, decision, actions, feedback)`); metrics gain
  `blocked_steps`/`stuck_looks`. `_PLAYER_SYSTEM` rewritten to real maze strategy.
- `navigation/vision_policy.py`: `Responder` now `(system, user_text, images: list[bytes])`;
  openai/claude responders attach each image; `VisionPolicy.act(frames, ..., feedback=)`; shared pure
  `build_feedback(...)` (mirrors `_feedback_text`, kept separate because vision_play.py is copied
  standalone into a sandbox).
- `evaluation/vision_runner.py::run_navigation`: tracks recent `(action, moved)` (moved = a real
  agent-cell change, read from `env.state.agent_pos()`) + `visited` for loop detection, builds the
  same feedback, keeps the last 2 frames, passes frames+feedback to `act`; records `blocked_steps`.
- `sandbox/vision_runner.py`: threads `history` into the injected `vision_config.json` +
  `play_sandbox_world`.
- Prompts: `vision_navigator.md` (maze strategy: turn on a block, don't reverse unless dead-ended,
  wall-follow, break a loop, obey the blocked/stuck feedback); `sandbox_agent.md` make_env contract
  now asks `env.step`'s info to include `"moved": bool` (optional; the driver falls back).

Honest scope (kept in code+docs): the guaranteed win is eliminating the stuck-in-a-corner
wall-bashing and the oscillation, and giving the policy recovery + memory so it EXPLORES. Solving a
deep 19x15 maze from pixels is still hard for a weak stand-in -- it gets much further, not a
guaranteed win. §2/§11b invariants untouched (pixels-only, code-defined success). Suite 437 green
(+5 tests: `_moved` prefers info; a blocked move aborts the plan via info + is fed back; history
frames reach the controller; `build_feedback`/`_feedback_text` report blocked/stuck/suggestions;
multi-image responder).

**Live-verified (real OpenAI vision API):**
- Deterministic path (`navigate examples/obstacle_course.json`, 52 walls): SUCCESS in 16 steps / 3
  vision calls, `blocked_steps=0`, `history=2`, and the `Last plan result: ...` feedback line was
  built and passed each look -- the shared feedback/memory/history plumbing confirmed working.
- Faithful path (`navigate runs/cartel` -- the exact failing case): the pathological "wedged in a
  corner spamming ONE blocked direction forever" is gone (blocked moves are now detected via
  `info["moved"]`, fed back, and the policy re-observes at each wall). But **the outcome is
  high-variance run to run** -- honestly reported, not cherry-picked: one run reached
  `total_reward +2.32` (reward is +0.05/move, -0.02/blocked, so the agent was genuinely MOVING
  through the maze, not bashing), another `-1.02` (mostly bashing again). No faithful cartel run
  SOLVED the maze (`vision_success=False` every time) -- expected and stated up front: a weak
  stand-in VLM solving a 19x15 dead-end maze from single rendered frames is hard and stochastic. The
  improvements (feedback / memory / frame history / blocked-detection / maze prompt) remove the
  specific reported failure (stuck going backwards) and make it EXPLORE; they do not make a weak
  policy reliably solve a deep maze, and the run-to-run variance is real.
  - **Two bugs/levers found from the live runs, both fixed:** (1) `blocked_steps` first read only the
    LAST of the `hold=6` held frames, miscounting a direction that advanced several cells then hit a
    wall as "blocked" (52/60). Fixed so an action counts as moved if it moved AT ALL during the hold
    (truthful counter). (2) The re-observation cadence is a real lever: the better (+2.32) run
    re-observed at EVERY wall-touch; committing further before re-looking (an intermediate version)
    did worse. Final design separates the two -- `block_moved` (moved at all) drives the honest
    `blocked_steps` counter + feedback, while a blocked FINAL held step (`step_moved` False, this
    direction is exhausted) triggers re-observe, keeping the frequent-relook cadence. Regression test
    `test_hold_block_counts_as_moved_if_it_moved_at_all`. Suite 438 green.
  - **Final-design live sample (the settled cadence: re-observe at each wall + truthful counter):**
    `total_reward +4.16`, `blocked_steps 10/40 = 25%`, `stuck_looks 0` -- the best of the runs, vs the
    original `-0.76` / ~90% blocked / wedged-in-a-corner. Still `vision_success=False` (didn't reach
    the deep package in 40 steps). Net honest read across all runs (`-0.76` original -> `+2.32` /
    `-1.02` / `+4.16`): the specific reported pathology is fixed and the settled design's sample is
    strongly positive, but a weak stand-in VLM does not reliably SOLVE a deep dead-end maze from
    single frames, and run-to-run variance is real -- reported, not cherry-picked.

## 2026-07-14 -- the minimap: give the vision policy a code-derived map so it ACTUALLY solves

Feedback+memory+history (above) made the policy explore instead of wedging in a corner, but it still
didn't reliably REACH the goal -- a weak stand-in VLM can't route a maze from pixels alone (verified:
it could clearly SEE the agent + goal in the frame; the gap was route-planning). User: "fix the
vision stuff till it actually runs properly." Asked whether to relax the earlier strict pixels-only
stance; user chose **"give it a minimap + coordinates."**

Change: each look, the policy now also gets a **text minimap** (`#`=wall `.`=floor `A`=you `P`=current
sub-goal) + coordinates, built from CODE TRUTH and prepended to the feedback string (no controller
signature change).
- Deterministic path: `vision_runner._deterministic_minimap(env, scene)` from `env.grid` +
  `env.state.agent_pos()` + the first incomplete goal's target (a `deliver` points at the object to
  fetch until `held`, then the drop target). `vision_navigator.md` explains the map + the
  forward/back/left/right<->(x,y) mapping.
- Faithful path: `vision_play._minimap(env, info)` best-effort from what the sandbox game exposes --
  `info["position"]` (or an env pos attr) + a walls set (`obstacle_cells`/`walls`/`blocked`) or a
  `walkable` set + a goal cell (`package`/`goal`/`target`/`exit`). Guards: returns "" if no grid
  state or bounds > 60/side (a continuous/pixel-coord game stays pixels-only). `_PLAYER_SYSTEM`
  explains it. `sandbox_agent.md`'s make_env contract now asks grid games to expose these.
  `metrics.json`/`vision_metrics.json` record `had_minimap`.

**Deliberate departure from "pixels-only"** (the earlier stance), made at the user's explicit
direction because they want it to actually solve. The load-bearing invariant is intact: the
REWARD/SUCCESS is still 100% code-defined (`info["won"]`/`is_goal_complete`, never a VLM judging
pixels) -- the minimap is an OBSERVATION aid, not a reward. That preserves the GI-brief point
(code-level objectives beat a VLM-on-pixels judge); it only changes what the *player* observes.

Tests (438->442 green): `_minimap` builds a correct grid from a cartel-shaped fake env + returns ""
for a game without grid state; the minimap reaches the controller as feedback (`had_minimap` True);
`_deterministic_minimap` marks A + P and points at the fetch object for a deliver. Live-verified the
deterministic path solves with the map (`obstacle_course`: SUCCESS 18 steps / 3 vision calls).
Faithful `cartel` live re-run WITH the minimap: `had_minimap=True`, `total_reward +2.96` (moving
well) but `vision_success=False` (didn't reach the deep package in 50 steps, `blocked_steps 21/50`).
Honest read: the minimap made the DETERMINISTIC play reliable and clearly helped faithful play, but a
weak VLM's ASCII-maze pathfinding + action-name mapping over a deep dead-end maze is still imperfect;
faithful deep-maze solving isn't guaranteed. Deterministic play (the substrate the human-play feature
uses) is the reliable one.

## 2026-07-14 -- human keyboard play in the GUI (Play mode)

User: "can we allow the player to play the environments after generation on the frontend?" Offered
two options (deterministic real-time keyboard play of any scene via InfiniEnv, vs turn-based faithful
play of the real sandbox game); user chose **deterministic keyboard play**. Faithful real-time human
play isn't practical anyway -- the real sandbox game runs isolated inside the sandbox (the trusted
process never runs its code, a core §11b invariant), so interactive per-keypress play would need a
long-lived in-sandbox process; out of scope.

Built a third GUI mode, **Play**: the human drives the same deterministic `InfiniEnv` that `navigate`
uses, just as the controller instead of a vision policy.
- Backend (`gui/app.py`): `_play_sessions` in-memory map (a server-side InfiniEnv per session, capped
  at `_PLAY_SESSION_CAP=24`, oldest-evicted); `POST /api/play/start {scene, assets}` -> first frame
  (base64 PNG) + goal + `CONTROLLER_ACTIONS` + goal state (scene from the path-safe `_list_scenes`
  allow-list); `POST /api/play/step {session_id, action}` -> new frame + steps/resolved/legal/reward/
  goals + done/won, freeing the session on done. Win is CODE-defined (`info["all_complete"]` /
  is_goal_complete), never pixels -- the §2 invariant holds; the human just replaces the solver.
- Frontend (`index.html`): a "Play" mode button + a scene/assets picker; a right-panel play stage
  (frame `<img>` + goal + status + WIN banner + controls). `keydown` maps arrows/WASD->move,
  Space/E->interact, POSTs each step (one in flight at a time), updates the frame + status, shows
  "YOU WIN" on `won`. Reuses the existing scene picker (populates both the navigate + play selects).
- Works for EVERY scene (examples + any generated/sandbox scene.json); a sandbox world plays as its
  declared top-down reading (same caveat as Navigate). Tests in `test_gui.py` (start/step/win-by-code/
  rejections/session-freed). Live-verified in a browser: Play renders the frame+legend, arrow keys
  advance the step counter + update the frame, and the YOU WIN banner shows on a code-judged win
  (screenshot). Suite 447 green.

## 2026-07-14 -- Play mode defaults to the world's OWN autogenerated sprites

User: "the assets for playing should be the ones that were autogenerated." A generated run keeps its
sprites in `runs/<name>/sandbox_workspace/asset_cache/<type>.png`. Added `_run_asset_paths(scene_path,
scene)` (maps each `scene_asset_types(scene)` type to that run's cached `<type>.png` if present) and a
new default Play assets mode **`world`**: `/api/play/start` renders with the run's own generated
sprites -- no new image-API calls -- falling back to flat cells for an example world or `assets=none`;
`none/local/generated/auto` still use the repo-cache resolution. Frontend Play assets dropdown now
defaults to "world - this run's autogenerated sprites". Test in `test_gui.py`
(`test_play_world_assets_use_the_runs_generated_sprites`: env.asset_paths point at the run's own
asset_cache). Live-verified in a browser: playing `runs/cans` rendered the real generated chef agent +
fridge/microwave/can/table pixel-art sprites, not flat cells (screenshot). Suite 448 green.

## 2026-07-14 -- faithful deliver/pickup tasks: interact guidance + carry note + oscillation fix

User reported a faithful navigate on the `cans` kitchen world failing with a telltale up/up/down/down
oscillation (`vision_success=False`, `had_minimap=True`, `blocked_steps=2`, `stuck_looks=0`,
`total_reward +2.52` -- MOVING, not blocked, but bouncing forever). Root cause from reading
`runs/cans/sandbox_workspace/run_scene.py`: it's a DELIVER/pickup task ("place ONLY the red soda can
in the sink"), the game has `e`/`space` = pick up / drop, and the win needs the can carried to the
sink -- but (a) the faithful `_PLAYER_SYSTEM` prompt only taught maze navigation, never "use interact
to pick up/place" (unlike the deterministic `vision_navigator.md`), so the policy never picked
anything up; (b) the minimap points at the final goal (the sink, `env.goal`), not "get the can
first," so the policy walked to the sink and bounced (arriving empty-handed does nothing); (c) the
oscillation went undetected -- `stuck_looks=0` -- because the loop check only fired on `moved_recent
== 0` or `len(set(visited)) <= 2`, and a 3-cell up/up/down/down bounce moves and spans 3 cells.

Fixes (generic, both faithful + deterministic paths):
- `_PLAYER_SYSTEM` (faithful prompt): added pickup/deliver guidance -- if the goal is collect/pick
  up/place/deliver, move onto the item and use the interact action (e/space/interact/use/pick), carry
  it, interact again at the destination.
- `_carry_note(info)` (faithful): surfaces the game's own carry state in the feedback ("Your hands are
  EMPTY -- go pick the item up first" / "You are carrying: X -- take it to the target and place it"),
  best-effort over common info keys (`carried`/`carrying`/`holding`/`inventory`/...). The `cans` game
  exposes `info["carried"]`, so this fires.
- Oscillation detection loosened (both paths): `looping` now also fires when a MAJORITY of recent
  cells are revisits (`2*len(set(visited)) <= len(visited)`), catching a moving-but-circling bounce,
  not just no-movement. The looping feedback now also says "if moving isn't working you probably need
  to INTERACT, not just move."
Tests updated/added (449 green): `_carry_note` (empty/holding/none), the new looping+interact wording,
"Options include:" suggestion.

**Live cans re-run (honest, mixed result):** with the fixes, `stuck_looks` went 0 -> 16 -- the
oscillation detection now FIRES and the policy is repeatedly told "you're oscillating; INTERACT, don't
just move" + "your hands are EMPTY, go pick it up." BUT the default model (`gpt-5.6-terra`) *ignores*
it -- it kept bouncing `a a a`/`d d d` and never issued the `e`/`space` interact action, so
`vision_success=False` again. A stronger model (`gpt-5.5-pro`) was tried but is too slow to finish a
60-step episode of 3-image vision calls inside a 15-min budget (timed out, no artifacts). Honest
conclusion: the infrastructure is correct and complete (minimap + blocked-detection + oscillation
detection + carry note + interact guidance all wired and verified firing), but reliably SOLVING a
multi-step "find the RIGHT item among distractors, pick it up, avoid wrong deposits, place it in the
sink" task needs a *capable game-playing policy*, which a weak stand-in VLM is not -- exactly the §11b
premise (our contribution is the interface + code-defined reward loop; GI supplies the real policy).
NOT pursued further: pointing the faithful minimap at the nearest item when empty-handed was
considered and rejected -- the task needs the RED soda specifically, so "nearest can" could steer it
to a wrong/dangerous deposit. The deterministic navigate (fixed-vocabulary scenes) + human Play mode
both work reliably; faithful solving of a hard bespoke task is the honest limit.

---

## 2026-07-15 -- GUI redesign: "clean product UI" (comprehension + polish)

The web GUI (`gui/templates/index.html`) worked but read as visually busy and hard to parse for a
time-boxed reviewer -- exactly the wrong signal against the GI brief's explicit **Clarity** criterion
("we will not have hours to review it"). User picked the "clean product UI" direction (over "refine the
existing game-console identity" or "comprehension changes only").

Scope: a **CSS-first restyle + additive markup**, single file, no logic changes. Every element `id`
and every JS-toggled class kept its name; the whole `<style>` block was rewritten under the same
selectors, and all legacy CSS custom-property *names* were kept defined (values remapped) because
markup and `buildResult`'s injected `nav-extra` reference `var(--gem/--dim/--faint)`. So the SSE feed,
phase bar, verdict cards, goals popup, Play mode, and runs gallery all stayed wired.

What changed:
- **Design system**: neutral dark surface ramp (near-black -> two elevated surfaces), a single indigo
  accent (`--accent #6e8efb`), and semantics (`--ok/--warn/--err`) used *only* on status elements --
  replacing the old 5-saturated-color coding (teal/yellow/green/red/purple all active at once). Removed
  the fixed grid `.substrate`, radial-gradient bg, neon glow shadows, and `gridpulse`.
- **Typography**: sans-first for all reading text; mono reserved for code/data (commands, scene.json,
  metrics, log). Dropped uppercase + wide letter-spacing from labels/headers/buttons/captions; hierarchy
  now via size+weight+color. Raised `--dim`/`--faint` for AA contrast on the new bg.
- **Comprehension**: a plain-language explainer strip at the top of the stage mapping the loop
  (describe -> agent writes & runs engine code -> independent checks + audit -> code decides), an
  `<details class="advanced">` collapsing the advanced knobs (agent runtime/model, seed, repair
  attempts, output dir) so only Prompt + Refine + Assets show by default, a better empty/loading state
  that sets the "a few minutes" expectation, and plainer copy.

Verified live with the gstack `/browse` skill against a running server: Generate empty state, an
existing sandbox run's detail (render.png/replay.gif side by side), Advanced expand (all field `id`s
resolve), Play mode (kitchen_can renders, keyboard drives it), and the <900px single-column
breakpoint. No console errors; `pytest tests/test_gui.py` -> 30 passed (template + endpoints unchanged).

---

## 2026-07-15 -- GUI: a dropped SSE stream is no longer reported as a run failure

User hit "✕ Failed / Error: connection error" in the GUI on a run that was actually still going.
Root cause: the frontend's `EventSource` `error` handler was fatal, and it conflated two very
different things. `EventSource` dispatches an event of type `error` for BOTH (a) a genuine app-level
failure the server sent as `event: error`, and (b) a native TRANSPORT drop (the socket closed --
common on a multi-minute sandbox build behind a proxy, on laptop sleep, etc.). The handler painted
"Failed" and `source.close()`d for either -- so any network blip killed the UI even though the job
kept running server-side, and the old stream endpoint *popped the job the instant it emitted a
terminal event*, so there was nothing to reconnect to.

Fix -- make the stream resumable and tell the two error kinds apart:

- **Server (`gui/app.py`)**: `Job` is now an append-only event **log** (with a `threading.Condition`)
  instead of a consume-once `queue.Queue`; `emit()` appends and never removes. Finished jobs are
  **retained** (capped at `_JOBS_CAP=32`, oldest-finished evicted via `_prune_jobs()` on new-job
  registration) so a reconnect/poll can still fetch the result. `/api/stream/<id>` now emits an `id:`
  per event and a `retry: 2000` hint, and **resumes from `Last-Event-ID`** (the header the browser
  resends automatically on reconnect) or `?from=`, replaying only missed events -- so a dropped
  stream catches back up losslessly incl. the terminal event, instead of the run looking failed. A
  genuine failure is emitted on a distinct SSE event name **`failed`** (not `error`) precisely so the
  client can distinguish it from a transport drop. New `/api/result/<id>` poll fallback returns the
  finished terminal payload (404 -> unknown/evicted).
- **Frontend (`templates/index.html`)**: a `settled` flag guards a single terminal handling.
  `done` -> `finishDone`; `failed` -> `finishFailure` (a real failure, ends the run). The native
  `error` handler is now NON-fatal: if `readyState === CONNECTING` it shows a one-time
  "reconnecting…" status and lets the browser auto-reconnect (which resends `Last-Event-ID`); if
  `CLOSED` it polls `/api/result` (`pollResult`, re-polls every 3s while `running`) so a completed
  run is recovered rather than shown as a failure.

Verified: `pytest tests/test_gui.py` -> 33 passed (added `test_stream_is_resumable_from_last_event_id`,
`test_result_endpoint_recovers_a_finished_run`, `test_run_failure_is_sent_on_failed_channel_not_error`).
Also verified live with curl against a running server: the raw SSE carries `retry: 2000` + per-event
`id:` (0..4) + `event:` names ending in `done`; a reconnect with `Last-Event-ID: 0` starts at id 1;
`/api/result/<job>` returns the finished payload after the stream ended. Page loads with no console
errors; the refactored `done`/`failed`/`error` handlers parse cleanly.

---

## 2026-07-15 -- Default SDK -> Claude; `auto` generates everything; image requests anonymised

Three user asks, driven by a run whose `asset_notes` were full of `moderation_blocked` (on the
`agent__walk/climb` sprites) and `429 rate_limit_exceeded`:

1. **Default sandbox SDK -> Anthropic.** `sandbox/runner.py::run_sandbox_generation` now defaults
   `INFINIENV_SANDBOX_BACKEND` to `claude` (was `openai`); the GUI backend `<select>` defaults to the
   Claude option. `openai` is still fully reachable (env var or the GUI picker). Tests updated:
   `test_backend_dispatch_default_is_claude` + `test_backend_dispatch_openai_is_still_reachable_explicitly`.

2. **`--assets auto` generates EVERY sprite via OpenAI.** Removed `resolver.py::SIMPLE_LOCAL_TYPES`
   and the auto-only "draw simple structural types locally, no API call" shortcut. `auto` now
   generates the same set as `generated` (incl. wall/floor), and differs only in failure handling:
   auto falls back to a local placeholder, generated leaves a hole. (The old shortcut existed to
   dodge the 5/min rate limit; that's now handled by backoff, below.) The two auto tests were
   rewritten (`..._falls_back_to_local_on_generation_failure`, `..._generates_every_type`).

3. **Anonymise content-generation requests + retry recoverable failures** (the real complaint,
   "this should not happen"). The Images API applies *output* moderation and rejected the `agent`
   sprite because its description embeds the scene prompt ("an Italian man in green rescues a
   princess..." reads as copyrighted IP). `generator_openai.py`:
   - `_anonymize_description()` scrubs every description before the call: named characters/brands ->
     neutral archetypes (`_IP_REPLACEMENTS`: mario->"a plumber-style hero", peach->"a royal", ...),
     nationality words dropped (`_NATIONALITY_WORDS`). Applied at the API boundary, so
     `_scene_descriptions` stays informative for logging/diffusion; only the OpenAI request is
     scrubbed. Not an exhaustive IP list -- paired with:
   - `_ORIGINAL_DESIGN_CLAUSE` appended to every prompt ("depict an ORIGINAL, generic design ... not
     any named/copyrighted character"), which does the heavy lifting since moderation fires on the
     generated *image*.
   - A retry loop in `generate_sprite`: a `429` waits and retries (`_rate_limit_sleep` reads the
     server's "try again in Ns" hint, else ~12s backoff capped 30s; `INFINIENV_IMAGE_MAX_RETRIES`
     default 4), and a `moderation_blocked` retries ONCE with a fully generic description
     (`_GENERIC_SPRITE_DESC`) before giving up (auto then falls back to local). Tests:
     `test_generate_sprite_anonymizes_the_description`, `_retries_on_rate_limit`,
     `_retries_generically_on_moderation` (all hermetic, `time.sleep` stubbed).

Honest note: this reduces but can't fully eliminate moderation rejections -- output moderation is
probabilistic and can flag a clean prompt whose generated image happens to look IP-ish; that's why
the generic retry + local fallback exist. And `auto` generating everything (incl. wall/floor) makes
more calls, so the 429 backoff is load-bearing, not optional -- a many-sprite scene will now take
longer (serial backoff waits) rather than silently dropping sprites. Not yet re-verified live against
the real Images API on the exact failing prompt (would consume image quota + several minutes);
flagged for a live check. `pytest` -> 455 passed (one non-obvious fixup: the default-backend flip
surfaced a latent coupling -- `test_sandbox_runner.py`'s `patched_sdk` fixture patches the *OpenAI*
Agents SDK `Runner` but called `run_sandbox_generation` with no explicit backend, so under the new
`claude` default those tests routed to the real `claude` CLI and hung; fixed by pinning
`INFINIENV_SANDBOX_BACKEND=openai` in that fixture).

> Follow-up (same day): the Claude backend's default model was changed from `claude-sonnet-5` to
> `claude-haiku-4-5-20251001` (Haiku 4.5) per the user -- the cheapest/fastest tier as the default for
> long iterative sandbox runs; Sonnet/Opus remain available via `INFINIENV_SANDBOX_MODEL` or the GUI
> picker (Haiku is now the first/default entry in `SANDBOX_MODELS["claude"]`).

---

## 2026-07-15 -- README simplified to a GUI quickstart; detail moved to docs/

Per the user, `README.md` was cut from ~454 lines to ~65: just the pitch, the GUI quickstart
(`pip install -e ".[gui,claude,openai]"` + the `claude` CLI login + a `.env` with `OPENAI_API_KEY`
+ `python -m infinienv gui`), and a "Learn more" links block. The install pulls in everything needed
to run the GUI with the **Claude Agent SDK** sandbox default (`gui` + `claude` extras) plus `openai`
(for prompt refinement, the faithfulness audit, `--assets`, and `navigate`). The `.env` only needs
`OPENAI_API_KEY` -- the Claude backend authenticates via the `claude` CLI's own login, not a key.

The removed detail was relocated (not deleted) into two linked pages so nothing was lost:
- `docs/overview.md` -- pipeline, the vision-policy loop, faithful sandbox play, the sandbox design,
  evaluation-criteria mapping + the brief's three unlocks, project layout, limitations.
- `docs/cli.md` -- every command, run artifacts, runtime providers, extended mechanics, deterministic
  physics, the asset pipeline (incl. the anonymisation/backoff note), and mutation/curriculum/export.
Both link back to the README and to CLAUDE.md/notes.md for deeper detail. CLAUDE.md section 16 was
updated so a future session keeps the README a thin on-ramp and puts new reviewer detail in `docs/`.
Verified every example path the docs reference still exists.

---

## 2026-07-15 -- `infinienv setup`: guided key entry + readiness check

Added a first-run setup command so a new user runs ONE command instead of hand-crafting a `.env`.
`src/infinienv/setup_env.py` holds the pure, tested pieces -- `parse_env`/`read_env`,
`merge_env_text` (replaces a key in place, appends new ones, preserves every comment/blank/unrelated
line, treats a blank value as "keep current"), `write_env_keys`, and `check_environment` (a
`{name, ok, detail, fix}` checklist over the OpenAI key + OP_KEY alias, the openai/flask/
claude-agent-sdk packages, and the `claude` CLI on PATH). `cli.py::cmd_setup` is the interactive
orchestration: hidden `getpass` prompts per managed key (OPENAI_API_KEY; CL_KEY optional), writes
the `.env`, then prints the readiness checklist with a `fix:` line for each ✗. Non-interactive/
scriptable via `--no-input --openai-key ... --anthropic-key ... --env-path ...`, and it never hangs
on `input()` in a non-tty (detects `sys.stdin.isatty()`). The README quickstart now leads with
`python -m infinienv setup` as step 2 (install -> setup -> gui); docs/cli.md + docs/overview.md +
CLAUDE.md updated. Tests in `tests/test_setup_env.py` (8, all hermetic). Live-verified the command
end to end (writes the key, prints an all-OK checklist on this machine).

---

## 2026-07-15 -- Deploy setup (Docker + Fly/Render), and why NOT Vercel

User tried to deploy the GUI to Vercel and hit "No Flask entrypoint found." Diagnosed and pushed
back: the GUI is a **long-running, stateful** server (SSE streamed for minutes, in-memory job/play
state + background threads, spawns the `claude` CLI subprocess, writes runs/ artifacts) -- serverless
(Vercel/Netlify) fundamentally can't run it (short-lived, stateless, read-only FS, no long
connections/subprocesses; and `generate` returns a job_id whose `/api/stream/<id>` would hit a
different stateless instance -> 404). So instead of a broken serverless entrypoint, added a
persistent-host deploy path (option A, user's choice, "cheap/free"):

- `Dockerfile` -- python:3.12-slim + Node 20 + the `claude` CLI (`@anthropic-ai/claude-code`) +
  `pip install -e ".[gui,claude,openai]"`; CMD `python -m infinienv gui --host 0.0.0.0 --port
  ${PORT:-5050} --no-browser`. `.dockerignore` excludes `.env`/runs/caches/.git.
- `fly.toml` (Fly.io, the recommended cheap path: Docker-native, 1gb VM, secrets for keys) and
  `render.yaml` (Render free tier, push-to-deploy Blueprint; free = 512MB + spin-down, caveated).
- `docs/deploy.md` -- the full guide: why not serverless, headless auth (OpenAI via OPENAI_API_KEY;
  the Claude backend via ANTHROPIC_API_KEY so no interactive `claude login` -- the project doesn't
  set/clobber it, so a host-provided env var flows straight to the CLI; or switch to
  INFINIENV_SANDBOX_BACKEND=openai to skip the CLI entirely), a cheap/free host table (Oracle Always
  Free VM = best free w/ real RAM; Fly ~a few $/mo; Render free w/ RAM caveat; Hetzner ~€4; Railway/
  Koyeb), a bare-VM `docker run`, and honest caveats (needs ~1GB+ RAM for a full sandbox run; must
  stay single-process -- in-memory job state + SSE can't be split across workers/replicas; local is
  the most reliable path for evaluation). Linked from the README.
- Code: `infinienv gui --port` now defaults to `int(os.environ.get("PORT", 5050))` so a PaaS-injected
  $PORT binds with no flag. Test `test_gui_port_defaults_to_PORT_env`.

Honest note: I couldn't build the Docker image here (no local Docker) or test a live deploy -- the
configs are written to the documented Fly/Render/Docker contracts but are un-exercised against a real
host; flagged in docs/deploy.md that a given host may need memory/timeout tuning for a heavy sandbox
run, and that running locally is the verified path. `pytest` (affected files) -> 49 passed.

---

## 2026-07-15 -- Public-deploy hardening: password + rate limits; Sonnet + auto defaults

Prompted by the public Oracle VM deploy (open, no auth). `gui/app.py`:
- **Password gate**: `INFINIENV_GUI_PASSWORD` -> HTTP Basic Auth `before_request` on every route
  (constant-time compare, any username). Unset -> open (local/tests). `launch()` **refuses to start a
  public bind** (host not in `{127.0.0.1,localhost,::1}`) without the password
  (`_public_bind`/`_password_required_message`, both unit-tested); localhost prints an UNAUTH warning.
  The Docker CMD binds 0.0.0.0, so a deploy without the password won't start -- by design.
- **Rate limits** on the credit-spending endpoints (`/api/generate`,`/api/navigate`): a concurrency
  cap (`INFINIENV_GUI_MAX_CONCURRENT`, default 1) counting non-done `_jobs`, and a per-IP sliding
  window (`INFINIENV_GUI_RATE_LIMIT`=20 / `INFINIENV_GUI_RATE_WINDOW`=3600s), both -> 429 with an
  `{error}` body the existing frontend already surfaces. Skipped under `TESTING`; per-IP state is
  per-app (no cross-test bleed). Frontend needed no change (button already disables mid-run).
- **Sonnet default** (reverted Haiku): `DEFAULT_SANDBOX_CLAUDE_MODEL="claude-sonnet-5"`;
  `SANDBOX_MODELS["claude"]` + the index.html picker reordered Sonnet-first.
- **Auto assets default** for the real flow: CLI `generate --assets` default `auto`; GUI api_generate
  defaults `auto` **only when sandbox** (`"auto" if sandbox else "none"`) -- the non-sandbox mock path
  stays `none` so hermetic tests never trigger image gen (a real hang I hit: auto + a live
  OPENAI_API_KEY made the mock-generate tests call the Images API with backoff sleeps).

Tests: added password-gate (401/200/wrong-pw), public-bind helper, rate-limit 429, concurrency 429
(injects a fake active `_jobs` entry) in test_gui.py; updated the sandbox assets-default assertion to
`auto`. Docs updated (docs/deploy.md access-control section + the run commands incl.
INFINIENV_GUI_PASSWORD; fly.toml/render.yaml; CLAUDE.md model-default revert; docs/cli.md). Full
suite (run with OPENAI_API_KEY unset) -> 469 passed.

> Follow-up: the container ran as root, and the `claude` CLI refuses --dangerously-skip-permissions
> (the sandbox agent's bypassPermissions mode) as root. Tried `ENV IS_SANDBOX=1` (waives the root
> guard for a `claude` invocation directly) -- but it did NOT hold when the Claude Agent SDK spawns
> the CLI inside the container (same root error persisted). Reverted to the reliable fix: run the
> image as a **non-root user** (uid 10001, HOME=/home/appuser, owns /app), which sidesteps the root
> guard entirely (Anthropic's recommended container pattern). Consequence: persist runs/ with a NAMED
> volume (`-v infinienv_runs:/app/runs`), not a host bind-mount (which the non-root user can't write).

---

## 2026-07-16 -- Sandbox: retry transient model rate limits instead of failing the run

A deployed OpenAI-backend sandbox run failed with all 4 repair attempts burned on an OpenAI TPM
rate limit ("Rate limit reached for gpt-5.6-terra ... tokens per min (TPM): Limit 500000, Used
479156 ... try again in 951ms"). Root cause: `sandbox/runner.py` caught the rate-limit exception
from `Runner.run_streamed`/`stream_events()` as a fatal `run_error`, which failed the attempt; the
repair loop then immediately retried and re-hit the still-saturated per-minute window, exhausting the
budget on a sub-second-transient limit. Fix: wrap the agent run in an inner rate-limit backoff-retry
loop (`_is_rate_limit_error` + `_rate_limit_backoff_seconds`, which prefers the API's own "try again
in Xms/Xs" hint, floored to ~2s so a per-minute window drains, capped 30s; `_MAX_RATE_LIMIT_RETRIES`
default 6, env-overridable). A rate limit is now waited out and retried IN PLACE and does NOT consume
a repair attempt; only a non-rate-limit failure (or an exhausted rate-limit budget) becomes a repair
attempt as before. Test `test_rate_limit_is_retried_without_consuming_a_repair_attempt` (hermetic:
first agent call raises a TPM error, retry succeeds, repair_attempts stays 0; asyncio.sleep stubbed).
Note: the real ceiling is the OpenAI account's TPM tier (500k) -- the sandbox agent's huge system
prompt burns ~480k/min, so this smooths transient spikes but a sustained over-limit still needs a
higher tier (or the Claude backend). Only the OpenAI runner is patched (that's where it fired).

---

## 2026-07-16 -- Live feed shows command output, edit diffs, and a "now doing" indicator

User: the feed should also show command outputs, what it's doing right now, and edit diffs -- "in a
good manner." Reversed two earlier deliberate choices (hide successful command output, hide diffs).

Backend narration (both sandbox runruntimes):
- Shared helpers in `sandbox/runner.py`: `_output_block(text)` (trimmed, "Output:"-tagged, collapsible)
  and `_make_diff(old, new)` (compact unified diff, no ---/+++ headers). Imported by `claude_runner`.
- OpenAI backend (`_describe_tool_called`/`_describe_tool_output`): apply_patch now appends the +/-
  hunk body after the file list; a SUCCESSFUL command's output is surfaced as an `Output:` block (was
  silent); failures unchanged; non-exec results (apply_patch/view_image) stay silent.
- Claude backend (`_describe_block`): `Edit` -> old->new diff, `Write` -> content-as-additions diff;
  a `Bash` command's result output is surfaced as an `Output:` block. To avoid dumping every file
  `Read`, a caller-owned `{tool_use_id: name}` map is threaded through `_describe_claude_message` so a
  result knows its source tool -- output is surfaced only for `Bash`.
- `gui/app.py::_classify_stage`: `Output:` -> kind `output`.

Frontend (`index.html`): `KINDS.output`; `pushEvent` renders an `edit` message's `\n`-appended diff via
`renderDiff` (green `+`, red `-`, accent `@@`, dim context) and an `output` message via `renderOutput`
(recessed scrollable mono block); a `#now` "what it's doing right now" line above the feed (`updateNow`,
shows the current event's icon+label+short text), hidden on run end/reset. CSS for `.now`, `pre.diff`,
`pre.outblock`.

Tests: claude narration (edit diff, write diff, Bash-output-only-via-tool_names), gui classify `output`,
and updated the OpenAI narration tests that asserted the old "no diff / silent success" behavior. Full
suite -> 473 passed (OPENAI_API_KEY unset). JS syntax-checked with `node --check`. Honest: couldn't grab
a live screenshot this run (the browse daemon lost localhost networking); verified via tests + the
served HTML containing the new code + a clean JS parse.

---

## 2026-07-16 -- Diagnosed the Claude "stuck": slow silent turns; added live streaming

Ran the Claude backend locally on the Mario prompt and profiled it. It is NOT hung -- the sandbox
`claude` CLI sits at ~0.4% CPU (idle, waiting on I/O) during each model turn, which produces NO feed
output until the turn completes, so a slow turn looks frozen. Timing (measured, `--assets none
--no-refine-prompt`):
- Requirements derivation (OpenAI `build_checklist`): **~45s** every run, before the Claude agent
  even starts.
- Claude turns: **~60s each on a COLD run** (first-ever), but **~2-3s each once warm** -- i.e.
  Anthropic prompt caching of the huge system prompt: the first run after inactivity is dramatically
  slower than subsequent ones (cache TTL ~5min). The agent also does many exploration turns (reading
  module after module) before building, so cold runs feel stuck for minutes.

Fix (the user's earlier ask): live-stream the model's thinking/text. `ClaudeAgentOptions(
include_partial_messages=True)` now yields `StreamEvent`s; `_describe_claude_message` surfaces their
`content_block_delta` text/thinking deltas via `on_stage` with an invisible `LIVE_PREFIX` sentinel
(`runner.LIVE_PREFIX`, U+2063-wrapped). `app._classify_stage` maps it to kind `live`; the GUI
accumulates deltas into a single in-place "Thinking live" bubble with a blinking caret (superseded by
the finalized event), plus a `heartbeat()` "still going -- Ns" line after >8s of silence. The CLI
suppresses live deltas (they'd flood the terminal). **Live-verified against the real SDK**: LIVE
deltas appeared at +57s in the timestamped run, confirming streaming works end to end.

Honest: the profiling run stopped at ~78s mid-exploration with no artifacts (didn't finish; cause of
the truncation not pinned down -- possibly an external kill in the test harness). Streaming makes the
wait legible; it doesn't make turns faster. Levers not taken here: speeding up the ~45s requirements
step (faster/optional checklist model), and cold-cache first-run latency. Tests: claude StreamEvent
-> live delta, gui classify `live`; JS syntax-checked. Full suite -> 474 passed.
