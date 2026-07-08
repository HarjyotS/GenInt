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
