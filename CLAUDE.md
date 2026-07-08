# CLAUDE.md

This file gives Claude Code the project-specific context and operating rules for building **InfiniEnv**.

InfiniEnv is a runnable 2D-first agent harness for the General Intuition **Infinite Environment Generation via an Agent Harness** technical challenge. The harness accepts natural-language commands, compiles them into structured scene specifications, validates and repairs them deterministically, builds playable environments, solves them with an agent, and emits reviewer-friendly artifacts.

The core philosophy is:

> Use AI for semantic generation. Use deterministic code for truth.

The submission must optimize for three evaluation criteria:

1. **Creativity**: demonstrate a nontrivial approach to infinite environment generation, not just a one-off text-to-game demo.
2. **Clarity**: make the repo immediately understandable to a reviewer with limited time.
3. **Working output**: provide a harness that can be run from the command line with clear instructions and visible artifacts.

---

## 1. Project mission

Build a working MVP called **InfiniEnv**.

Given a text command such as:

```text
Create a warehouse where the agent must find a key, unlock a door, pick up a package, and deliver it to the exit.
```

The harness should produce:

```text
runs/<run_id>/
├── scene.json          # structured SceneSpec ground truth
├── validation.json     # validator checks and repair history
├── metrics.json        # solvability, path length, success, timings
├── render.png          # static visualization of the environment
├── replay.gif          # replay of the agent solving the task
└── report.md           # short human-readable run summary
```

The MVP should prove the full loop:

```text
Text Prompt
  → OpenAI Agents SDK runtime planner
  → SceneSpec JSON DSL
  → deterministic validation
  → repair loop if invalid
  → playable 2D environment
  → deterministic navigation / task planner
  → code-level objective verification
  → replay + metrics + report
```

---

## 2. Most important design rule

Do **not** let the LLM be the source of truth.

The LLM may propose a scene. It may repair a scene. It may mutate a scene. It may summarize a run.

But these must remain deterministic and testable:

- schema validation
- object placement
- collision checks
- bounds checks
- reachability checks
- pathfinding
- inventory transitions
- goal completion
- scoring
- replay generation

If there is a conflict between model output and deterministic validation, the validator wins.

---

## 3. Runtime agent choice

The recommended submitted runtime uses the **OpenAI Agents SDK**.

Claude Code is a development tool for building this repo. The final harness should not require an evaluator to run Claude Code interactively.

Good final submission shape:

```bash
pip install -e .
python -m infinienv generate \
  --provider openai_agents \
  --prompt "Create a kitchen where the agent picks up a can from the table and drops it in the sink." \
  --seed 42 \
  --out runs/kitchen_can
```

Bad final submission shape:

```text
Open Claude Code and ask it to create a level.
```

The runtime must also support a no-key fallback:

```bash
python -m infinienv generate \
  --provider mock \
  --prompt "Create a kitchen where the agent picks up a can from the table and drops it in the sink." \
  --seed 42 \
  --out runs/mock_kitchen
```

This ensures the project is runnable even without API credentials.

---

## 4. High-level architecture

Implement the project as a clean Python package.

Suggested structure:

```text
infinienv/
├── README.md
├── CLAUDE.md
├── pyproject.toml
├── examples/
│   ├── prompts.txt
│   ├── kitchen_can.json
│   ├── warehouse_key.json
│   └── obstacle_course.json
├── src/
│   └── infinienv/
│       ├── __init__.py
│       ├── cli.py
│       ├── schema/
│       │   ├── scene_schema.py
│       │   └── scene_schema.json
│       ├── llm/
│       │   ├── base.py
│       │   ├── providers/
│       │   │   ├── openai_agents.py
│       │   │   ├── openai_responses.py
│       │   │   ├── anthropic.py
│       │   │   ├── vllm.py
│       │   │   └── mock.py
│       │   └── prompts/
│       │       ├── scene_planner.md
│       │       ├── repair_agent.md
│       │       ├── mutation_agent.md
│       │       └── artifact_agent.md
│       ├── generation/
│       │   ├── compiler.py
│       │   ├── templates.py
│       │   ├── repair.py
│       │   ├── mutation.py
│       │   └── curriculum.py
│       ├── engine/
│       │   ├── grid.py
│       │   ├── objects.py
│       │   ├── physics.py
│       │   ├── actions.py
│       │   └── state.py
│       ├── validation/
│       │   ├── validator.py
│       │   ├── reachability.py
│       │   ├── solvability.py
│       │   └── errors.py
│       ├── navigation/
│       │   ├── astar.py
│       │   ├── planner.py
│       │   └── policy.py
│       ├── render/
│       │   ├── pygame_renderer.py
│       │   ├── image_export.py
│       │   └── replay_export.py
│       ├── evaluation/
│       │   ├── runner.py
│       │   ├── metrics.py
│       │   └── benchmark.py
│       └── artifacts/
│           ├── writer.py
│           └── report.py
└── tests/
    ├── test_schema.py
    ├── test_validator.py
    ├── test_reachability.py
    ├── test_solver.py
    ├── test_mock_generation.py
    └── test_cli.py
```

Keep files small and responsibilities separated.

---

## 5. MVP feature priorities

Build in this order.

### P0: Must work

- CLI command: `python -m infinienv generate --prompt ... --out ...`
- `SceneSpec` JSON schema
- mock provider with deterministic scenes
- OpenAI Agents SDK provider
- validator for schema, bounds, collisions, required objects, and reachability
- A* navigation
- simple task planner for pickup / deliver / unlock / reach goals
- static render image
- replay GIF
- metrics JSON
- human-readable report
- tests for core validator and solver behavior

### P1: Creativity boosters

- mutation engine that creates variants of valid scenes
- curriculum generator that produces easy → hard task suites
- repair loop with clear failure reasons
- benchmark mode over a prompt file
- support for locked-door/key dependencies
- support for distractor objects and decoy goals

### P2: Stretch

- local GPU inference through vLLM or another OpenAI-compatible server
- Godot export path
- 3D-ready scene schema fields
- sprite/theme packs
- visual observation frames for future vision-policy training

Do not overbuild P2 before P0 is stable.

---

## 6. SceneSpec DSL principles

The scene spec is the contract between AI and the deterministic engine.

It should be typed, explicit, compact, and easy to validate.

A good scene spec looks like this:

```json
{
  "version": "0.1",
  "seed": 42,
  "metadata": {
    "name": "kitchen_can_delivery",
    "prompt": "Create a kitchen where the agent picks up a can from the table and drops it in the sink.",
    "theme": "kitchen"
  },
  "grid": {
    "width": 16,
    "height": 12,
    "tile_size": 32
  },
  "agent": {
    "id": "agent",
    "x": 1,
    "y": 1,
    "inventory": []
  },
  "objects": [
    {"id": "table_1", "type": "table", "x": 6, "y": 4, "solid": true},
    {"id": "can_1", "type": "can", "x": 6, "y": 3, "portable": true},
    {"id": "sink_1", "type": "sink", "x": 13, "y": 9, "solid": false}
  ],
  "walls": [
    {"x": 0, "y": 0},
    {"x": 1, "y": 0}
  ],
  "goals": [
    {
      "id": "deliver_can_to_sink",
      "type": "deliver",
      "object_id": "can_1",
      "target_id": "sink_1"
    }
  ]
}
```

Rules:

- Prefer JSON over arbitrary generated code.
- Every object must have a stable ID.
- Coordinates are grid-based integers.
- Walls and solid objects block movement.
- Portable objects can be picked up if adjacent or on the same tile, depending on engine choice.
- Goals must be checkable from state, not from pixels.
- Add new mechanics only when validators and tests are updated.

---

## 7. Supported MVP mechanics

Keep the initial engine small.

Supported actions:

```text
move_up
move_down
move_left
move_right
pick_up(object_id)
drop(object_id)
unlock(door_id, key_id)
wait
```

Supported goal types:

```text
reach(target_id)
pickup(object_id)
deliver(object_id, target_id)
unlock(door_id)
sequence([...subgoals])
```

Supported object types:

```text
agent
wall
floor
table
can
box
key
door
package
sink
exit
hazard
distractor
```

Do not add complicated physics until the gridworld version is reliable.

---

## 8. Validation requirements

The validator should return structured errors, not vague strings.

Example:

```json
{
  "valid": false,
  "errors": [
    {
      "code": "UNREACHABLE_OBJECT",
      "message": "Object can_1 cannot be reached from the agent spawn.",
      "object_id": "can_1",
      "severity": "error"
    }
  ]
}
```

Minimum checks:

- JSON parses
- schema fields exist
- grid dimensions are valid
- all coordinates are in bounds
- no invalid object types
- no duplicate IDs
- no illegal overlaps
- agent exists exactly once
- required goal objects exist
- walls do not fully seal required objects
- all required subgoals are reachable in order
- final goal can be completed by deterministic planner

Validation should be deterministic for a given scene and seed.

---

## 9. Repair loop behavior

The repair loop should be explicit and bounded.

Default:

```text
MAX_REPAIR_ATTEMPTS=3
```

Flow:

```text
1. LLM proposes SceneSpec.
2. Validator checks SceneSpec.
3. If valid, build and solve.
4. If invalid, pass original prompt + previous SceneSpec + validator errors to RepairAgent.
5. RepairAgent returns a modified SceneSpec.
6. Repeat until valid or max attempts reached.
7. If still invalid, fall back to template generator and record failure.
```

The repair agent must preserve the original task unless impossible.

Do not silently discard failures. Record them in `validation.json` and `report.md`.

---

## 10. OpenAI Agents SDK implementation guidance

Implement the default runtime provider in:

```text
src/infinienv/llm/providers/openai_agents.py
```

Use the Agents SDK for orchestration, not for unrestricted code execution.

Recommended agents:

```text
ScenePlannerAgent
RepairAgent
MutationAgent
ArtifactAgent
```

For MVP, only `ScenePlannerAgent` and `RepairAgent` are required.

Tool boundary:

```python
@function_tool
def validate_scene(scene_spec: dict) -> dict:
    """Validate schema, geometry, reachability, and solvability."""

@function_tool
def get_scene_schema() -> dict:
    """Return the current SceneSpec schema."""

@function_tool
def get_supported_mechanics() -> dict:
    """Return supported objects, actions, and goals."""
```

The OpenAI agent should not call tools that write arbitrary project files during normal generation.

The project-level Python code should own:

- parsing final output
- calling validator
- retrying repair
- writing artifacts
- rendering
- solving
- benchmark execution

Keep model output constrained to SceneSpec JSON.

---

## 11. Provider abstraction

All model providers should implement a common interface.

Example:

```python
class SceneProvider(Protocol):
    def generate_scene(self, prompt: str, seed: int) -> SceneSpec:
        ...

    def repair_scene(
        self,
        prompt: str,
        scene: SceneSpec,
        validation_errors: list[ValidationError],
        seed: int,
    ) -> SceneSpec:
        ...
```

Required providers:

```text
mock.py             # deterministic, no API key required
openai_agents.py    # default intended runtime
```

Optional providers:

```text
openai_responses.py
anthropic.py
vllm.py
```

The benchmark should be able to compare providers without changing engine code.

---

## 12. CLI requirements

Minimum CLI commands:

```bash
python -m infinienv generate --prompt "..." --out runs/demo
python -m infinienv play runs/demo/scene.json
python -m infinienv validate runs/demo/scene.json
python -m infinienv solve runs/demo/scene.json --out runs/demo
python -m infinienv benchmark examples/prompts.txt --out runs/benchmark
```

Useful optional commands:

```bash
python -m infinienv mutate runs/demo/scene.json --count 10 --out runs/mutations
python -m infinienv curriculum --theme warehouse --out examples/curriculum_warehouse.txt
python -m infinienv export-godot runs/demo/scene.json --out exports/godot_demo
```

Always design CLI output for reviewers. It should clearly state what happened.

Good CLI output:

```text
InfiniEnv run: runs/kitchen_can
Prompt: Create a kitchen where the agent picks up a can from the table and drops it in the sink.
Provider: openai_agents
Seed: 42

[1/6] Generated initial SceneSpec
[2/6] Validation failed: 1 reachable-object error
[3/6] Repair succeeded on attempt 1
[4/6] Built playable gridworld
[5/6] Solver completed goal in 34 actions
[6/6] Wrote artifacts:
      - scene.json
      - validation.json
      - metrics.json
      - render.png
      - replay.gif
      - report.md
```

---

## 13. Renderer guidance

Use the simplest renderer that produces clear artifacts.

Preferred MVP option:

```text
pygame + Pillow/imageio
```

Renderer must create:

- `render.png`: static top-down map
- `replay.gif`: agent action replay

Do not spend time on beautiful art before the core loop works.

Reviewer-friendly visuals matter more than aesthetic perfection.

Use labels or a legend if helpful.

---

## 14. Navigation and planning

Use deterministic planning for MVP.

Recommended approach:

- A* or BFS over the grid for low-level movement
- high-level symbolic task planner for subgoals
- state machine for inventory and interactions

Example for deliver goal:

```text
1. path to object
2. pick_up(object)
3. path to target
4. drop(object)
5. verify object at target
```

For locked-door tasks:

```text
1. path to key
2. pick_up(key)
3. path to door
4. unlock(door, key)
5. path to target behind door
6. complete final goal
```

Do not use an LLM for every movement step.

The LLM may plan task semantics, but actual movement should be code.

---

## 15. Evaluation and metrics

Every run should produce `metrics.json`.

Minimum metrics:

```json
{
  "success": true,
  "provider": "openai_agents",
  "seed": 42,
  "repair_attempts": 1,
  "validation_passed": true,
  "solver_success": true,
  "path_length": 34,
  "num_objects": 8,
  "num_walls": 44,
  "num_goals": 1,
  "generation_time_seconds": 2.41,
  "solve_time_seconds": 0.02
}
```

Benchmark mode should aggregate:

- number of prompts
- valid on first try
- valid after repair
- failed after repair
- solved successfully
- average repair attempts
- average path length
- average generation time

This directly supports the challenge’s “working output” and “clarity” criteria.

---

## 16. Creativity features to emphasize

The project should not look like a basic text-to-grid demo.

Emphasize these ideas in code, docs, and demo examples:

### A. Verified environment factory

The harness does not merely create worlds. It creates worlds with machine-checkable objectives and proof of completion.

### B. Mutation engine

Given one valid scene, produce many valid variants:

```text
same goal, different layout
same layout, different object positions
same task, extra obstacles
same task, distractor objects
same task, key-door dependency added
same task, reversed start and target
```

Every mutation must pass validation and solvability checks.

### C. Curriculum generation

Generate easy → hard variants:

```text
Level 1: open room pickup
Level 2: pickup behind obstacle
Level 3: delivery across rooms
Level 4: key-door dependency
Level 5: decoy object and longer path
```

### D. Impossible prompt handling

If the user requests something impossible or contradictory, the harness should classify it:

```text
make_solvable
preserve_impossible_as_test
unsupported_mechanic
ambiguous_goal
```

For MVP, default to `make_solvable` unless the CLI flag says otherwise.

### E. Dual output

Every scene should produce both:

```text
symbolic truth: scene.json, validation.json, metrics.json
visual proof: render.png, replay.gif
```

This aligns with the research motivation of bridging code-defined truth and pixel observations.

---

## 17. Coding standards

Use Python 3.11+.

Prefer:

- dataclasses or Pydantic models for structured specs
- type hints everywhere
- clear module boundaries
- deterministic seeds
- small pure functions where possible
- explicit exceptions for invalid state
- pytest tests
- ruff or similar linting if configured

Avoid:

- hidden global state
- nondeterministic tests
- giant files
- broad `except Exception` blocks without logging
- arbitrary LLM-generated code execution
- adding mechanics without validators
- features that require GPU/API keys for the basic demo

---

## 18. Testing expectations

Add tests as functionality is implemented.

Minimum tests:

```text
test_schema.py
- valid SceneSpec parses
- missing required fields fail
- duplicate IDs fail

test_validator.py
- out-of-bounds objects fail
- overlapping solid objects fail
- missing goal target fails

test_reachability.py
- reachable target passes
- sealed target fails

test_solver.py
- pickup task succeeds
- deliver task succeeds
- locked door task succeeds
- impossible task fails cleanly

test_mock_generation.py
- mock provider creates deterministic valid scene

test_cli.py
- generate command writes expected artifacts
```

Before final handoff, run:

```bash
pytest
python -m infinienv generate --provider mock --prompt "Create a kitchen delivery task" --out runs/smoke_test
python -m infinienv validate runs/smoke_test/scene.json
python -m infinienv solve runs/smoke_test/scene.json --out runs/smoke_test
```

If tests cannot be run in the current environment, state that honestly in the final response and explain what was not verified.

---

## 19. Documentation requirements

The README must remain reviewer-first.

It should include:

- one-paragraph project explanation
- architecture diagram or text pipeline
- setup instructions
- OpenAI Agents SDK runtime section
- no-key mock mode
- CLI examples
- generated artifact examples
- evaluation criteria mapping
- limitations and roadmap

Do not bury the demo instructions.

A reviewer should understand how to run the project within 60 seconds of opening the README.

---

## 20. Development workflow for Claude Code

When working in Claude Code:

1. Read `README.md`, `CLAUDE.md`, and current file tree first.
2. Identify the smallest next working slice.
3. Make code changes.
4. Run relevant tests or smoke commands.
5. Fix failures.
6. Summarize what changed and what remains.

Default to building working vertical slices instead of many disconnected stubs.

Preferred order:

```text
1. SceneSpec model
2. validator
3. mock generator
4. grid engine
5. A* solver
6. renderer
7. CLI generate command
8. artifacts writer
9. OpenAI Agents SDK provider
10. repair loop
11. mutation / curriculum extensions
```

---

## 21. Suggested Claude Code subagents

Use subagents only when useful. Keep them focused.

Suggested subagents:

```text
schema-agent
- Owns SceneSpec models, JSON schema, and examples.

validator-agent
- Owns validation rules, reachability, and structured errors.

solver-agent
- Owns A*, task planning, and state transitions.

renderer-agent
- Owns render.png and replay.gif export.

openai-runtime-agent
- Owns OpenAI Agents SDK provider and tool boundaries.

docs-agent
- Owns README, reports, demo instructions, and evaluation framing.

test-agent
- Adds pytest coverage and smoke tests.
```

Do not let subagents make incompatible schema assumptions. The `SceneSpec` schema is the shared contract.

---

## 22. Prompting guidance for runtime agents

The runtime agent prompts should be strict.

Scene planner instruction should say:

```text
You are a scene compiler for InfiniEnv. Convert the user's request into a valid SceneSpec JSON object.
Use only supported object types, action types, and goal types.
Prefer solvable 2D grid layouts.
Do not output markdown.
Do not output explanation.
Do not invent unsupported mechanics.
All object IDs must be unique.
All coordinates must be integers inside the grid.
The agent must be able to complete the goal.
```

Repair agent instruction should say:

```text
You repair invalid SceneSpec JSON. Preserve the user's original task as much as possible.
Use the validator errors as ground truth.
Modify only what is necessary to make the scene valid and solvable.
Do not introduce unsupported mechanics.
Return only the repaired SceneSpec JSON object.
```

Mutation agent instruction should say:

```text
You create variants of an already valid scene.
Preserve the core objective but vary layout, object positions, obstacles, distractors, and difficulty.
Every variant must remain solvable.
Return only SceneSpec JSON.
```

Artifact agent instruction should say:

```text
You summarize a completed InfiniEnv run for a technical reviewer.
Be concise. Explain the prompt, generated world, validation result, solver result, and artifact files.
Do not claim success unless metrics.json says success=true.
```

---

## 23. Safety and sandboxing

The runtime LLM must not execute arbitrary generated code.

Allowed:

- emitting JSON
- calling deterministic Python tools with typed inputs
- requesting validation
- requesting repair

Not allowed:

- shell execution from model output
- arbitrary file writes from model output
- importing packages chosen by the model at runtime
- using unrestricted Python `eval` or `exec`

File writes should go only to the selected output directory.

Validate output paths to avoid path traversal.

---

## 24. GPU access guidance

GPU access is optional for MVP.

The core demo must run on CPU.

GPU can be used for:

- local LLM inference through vLLM
- batch generation of prompt suites
- future vision-policy training
- future 3D simulation or rendering

Do not make the basic CLI require a GPU.

If implementing local GPU support, use an OpenAI-compatible endpoint so the provider abstraction remains simple:

```bash
LLM_PROVIDER=vllm
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct
```

---

## 25. 3D extension path

The MVP is 2D-first. Do not pretend it solves full 3D navigation.

But design the schema so it can evolve.

Future 3D fields may include:

```json
{
  "position": {"x": 1.0, "y": 0.0, "z": 2.0},
  "rotation": {"yaw": 90.0, "pitch": 0.0, "roll": 0.0},
  "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
  "asset": "kitchen/table.glb"
}
```

Potential export targets:

```text
Godot
Unity ML-Agents
NVIDIA Isaac Lab
Genesis
Habitat
ManiSkill
MuJoCo
```

The honest story:

> InfiniEnv proves the language → verified scene → objective → replay loop in 2D. The same SceneSpec abstraction can later target 3D engines, where the internal vision-based policy would replace the deterministic 2D planner.

---

## 26. Final demo checklist

Before considering the MVP ready, verify:

```text
[ ] README explains the project in under 60 seconds.
[ ] `pip install -e .` works.
[ ] `python -m infinienv generate --provider mock ...` works without API keys.
[ ] `python -m infinienv generate --provider openai_agents ...` works with OPENAI_API_KEY.
[ ] A valid scene produces scene.json.
[ ] Invalid scenes produce clear validation errors.
[ ] Repair attempts are recorded.
[ ] Solver completes at least pickup and deliver tasks.
[ ] render.png is generated.
[ ] replay.gif is generated.
[ ] metrics.json reports success truthfully.
[ ] benchmark mode runs over multiple prompts.
[ ] At least one creative mutation/curriculum demo exists.
[ ] Tests pass or failures are documented honestly.
```

---

## 27. Submission framing

Frame the project like this:

> InfiniEnv is a verified environment factory. It compiles natural language into structured, playable worlds; repairs invalid generations using deterministic validator feedback; mutates successful worlds into infinite variants; and emits replayable proof that an agent can complete code-defined objectives.

Avoid framing it as:

```text
An LLM makes a game.
```

Use this stronger framing instead:

```text
A model proposes. The harness verifies. The agent proves.
```

That is the core research contribution.
