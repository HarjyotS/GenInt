# InfiniEnv

**Infinite Environment Generation via an Agent Harness**

InfiniEnv is a 2D-first agent harness that converts natural-language commands into playable, verified environments. It is designed for the General Intuition research challenge: generate diverse task worlds from text, encode objectives in code, and demonstrate that an agent can navigate and complete those objectives.

The core idea is simple:

> Use AI for semantic generation, but use deterministic code for truth.

A language model proposes a structured scene. The harness validates it, repairs failures, builds a playable environment, solves it with a deterministic agent, and emits replayable evidence that the objective was completed.

---

## Why this project exists

Vision-based agents need large numbers of diverse, goal-directed environments for training and evaluation. Human-built environments do not scale. Pure procedural generation scales, but is often semantically weak. Pure LLM generation is expressive, but can be flaky.

InfiniEnv combines both:

- **Natural-language control**: users describe tasks like “make a kitchen where the agent must pick up a can from the table and bring it to the sink.”
- **Structured environment generation**: the LLM compiles the request into a typed `SceneSpec` JSON format.
- **Programmatic truth**: reachability, object placement, physics constraints, and task success are checked by code.
- **Repair loop**: invalid environments are automatically sent back to the generator with precise errors.
- **Playable output**: the final scene can be run, visualized, solved, and replayed.

---

## MVP thesis

The strongest MVP is not a full 3D simulator. It is a reliable 2D harness that proves the full research loop:

```text
Text Command
    ↓
LLM Scene Compiler
    ↓
SceneSpec JSON DSL
    ↓
Deterministic Builder
    ↓
Validator + Repair Loop
    ↓
Playable 2D Environment
    ↓
Agent Navigation + Goal Verification
    ↓
Replay, Metrics, and Artifacts
```

This demonstrates the important research contribution before moving to 3D: language-conditioned environment generation with code-level objectives and verifiable success.


---

## Runtime agent choice: OpenAI Agents SDK

The recommended submitted runtime uses the **OpenAI Agents SDK** for the LLM orchestration layer. The project should still remain provider-agnostic, but the default agent workflow should be implemented with the Agents SDK because it provides a clean Python-first loop for tools, handoffs, guardrails, sessions, tracing, and multi-step artifact generation.

The important design principle is:

> The OpenAI agent plans and repairs. The deterministic harness validates, simulates, solves, and judges.

This keeps the research contribution grounded. The model is not trusted as the source of truth; it is a semantic compiler that proposes structured scenes and reacts to validator feedback.

### Why OpenAI Agents SDK fits this project

InfiniEnv is naturally an agent workflow, not just a one-shot model call:

```text
User prompt
  ↓
ScenePlannerAgent
  ↓
SceneSpec JSON
  ↓
validate_scene(scene_spec)
  ↓ valid                       ↓ invalid
Build + solve + replay          RepairAgent(error_report, old_scene)
  ↓                             ↓
Artifacts + metrics             retry until valid or fallback
```

The Agents SDK is useful because the MVP needs:

- **Function tools** for deterministic operations like `validate_scene`, `render_scene`, `solve_scene`, and `export_replay`.
- **Guardrails** to reject unsupported task types, invalid JSON, unsafe file paths, or schema-breaking outputs before they reach the engine.
- **Handoffs / specialist agents** so generation, repair, mutation, and evaluation can be separate, understandable components.
- **Tracing** so reviewers can inspect exactly how a prompt became a scene, why repair happened, and which checks passed.
- **Sessions** so a multi-step generation and repair loop can preserve context without stuffing everything into one prompt.

The runtime should not require Claude Code, Codex, or any interactive coding agent. Those are useful for development, but the submitted artifact should run from normal CLI commands.

### Recommended agent roles

| Agent | Responsibility | Tools it can call |
|---|---|---|
| `ScenePlannerAgent` | Convert natural language into a valid `SceneSpec` candidate | `load_schema`, `get_templates`, `emit_scene_spec` |
| `RepairAgent` | Fix a failed scene using validator errors while preserving the original task | `validate_scene`, `diff_scene_specs`, `emit_scene_spec` |
| `MutationAgent` | Generate solvable variants of an already valid scene | `validate_scene`, `solve_scene`, `emit_scene_spec` |
| `CurriculumAgent` | Produce easy → hard prompt suites for benchmark generation | `generate_prompt_suite`, `estimate_difficulty` |
| `ArtifactAgent` | Summarize the run into reviewer-readable artifacts | `write_report`, `collect_metrics` |

For the MVP, only `ScenePlannerAgent` and `RepairAgent` are required. `MutationAgent` is the highest-value creative extension because it directly demonstrates infinite environment generation.

### Agents SDK tool boundary

The agent should only call safe Python tools. It should not write arbitrary Python engine code during normal scene generation.

Good tool boundary:

```python
@function_tool
def validate_scene(scene_spec: dict) -> dict:
    """Return schema, geometry, reachability, and solvability errors."""

@function_tool
def solve_scene(scene_spec: dict) -> dict:
    """Run the deterministic planner and return action trace + success."""

@function_tool
def render_scene(scene_spec: dict, out_dir: str) -> dict:
    """Render PNG/GIF artifacts for reviewer inspection."""
```

Bad tool boundary:

```text
"Write any Python code you want and execute it."
```

The LLM output should be constrained to `SceneSpec` JSON. Engine behavior, physics, navigation, and scoring should remain deterministic and testable.

### Minimal OpenAI Agents SDK workflow

```python
from agents import Agent, Runner, function_tool

@function_tool
def validate_scene(scene_spec: dict) -> dict:
    return scene_validator.validate(scene_spec)

scene_planner = Agent(
    name="ScenePlannerAgent",
    instructions=(
        "Convert the user's environment request into SceneSpec JSON only. "
        "Use the schema exactly. Prefer simple, solvable 2D grid layouts. "
        "Do not invent unsupported mechanics."
    ),
    tools=[validate_scene],
)

result = Runner.run_sync(
    scene_planner,
    "Create a kitchen where the agent picks up a can from a table and drops it in the sink.",
)

scene_spec = result.final_output
```

In the full implementation, `Runner.run_sync` should be wrapped by a project-level generation function that performs schema parsing, deterministic validation, repair retries, and fallback template generation.

### Provider abstraction

OpenAI Agents SDK should be the default runtime path, but the repo should keep a provider interface:

```text
src/infinienv/llm/
├── providers/
│   ├── openai_agents.py      # default runtime agent workflow
│   ├── openai_responses.py   # lower-level fallback path
│   ├── anthropic.py          # optional Claude provider
│   ├── vllm.py               # local GPU OpenAI-compatible server
│   └── mock.py               # deterministic no-key demo mode
```

This lets the README honestly say the project is not dependent on one model. The OpenAI Agents SDK is the recommended orchestration layer, while the simulator and evaluator are model-independent.

### Required environment variables

```bash
OPENAI_API_KEY=sk-...
LLM_PROVIDER=openai_agents
LLM_MODEL=gpt-4.1
MAX_REPAIR_ATTEMPTS=3
TRACE_AGENT_RUNS=true
```

For local GPU inference through an OpenAI-compatible server:

```bash
LLM_PROVIDER=vllm
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct
```

### Recommended CLI behavior

```bash
python -m infinienv generate \
  --provider openai_agents \
  --prompt "Create a warehouse with a locked door, a key, and a package delivery objective." \
  --seed 42 \
  --out runs/warehouse_key
```

Expected run stages:

```text
[1] ScenePlannerAgent creates initial SceneSpec
[2] Validator checks schema, geometry, reachability, and solvability
[3] RepairAgent fixes the scene if needed
[4] Deterministic builder creates the playable world
[5] A* agent solves the task
[6] Harness emits scene.json, validation.json, metrics.json, render.png, and replay.gif
```

### Fallback requirement

The repo should still run without an API key:

```bash
python -m infinienv generate \
  --provider mock \
  --prompt "Create a kitchen delivery task" \
  --out runs/mock_kitchen
```

The fallback can use deterministic templates. This is important because reviewers should be able to verify the harness even if they do not configure model access.

---

## Demo examples

### Example 1: Kitchen delivery task

```bash
python -m infinienv generate \
  --prompt "Create a kitchen where the agent starts near the door, picks up a can from a table, and drops it in the sink." \
  --seed 42 \
  --out runs/kitchen_can

python -m infinienv play runs/kitchen_can/scene.json
python -m infinienv solve runs/kitchen_can/scene.json
```

Expected outputs:

```text
runs/kitchen_can/
├── scene.json
├── validation.json
├── replay.json
├── metrics.json
├── render.png
└── replay.gif
```

### Example 2: Maze navigation task

```bash
python -m infinienv generate \
  --prompt "Generate a maze with three rooms. The agent must find the blue key, unlock the door, and reach the green exit." \
  --seed 7 \
  --out runs/key_maze

python -m infinienv solve runs/key_maze/scene.json
```

### Example 3: Batch evaluation

```bash
python -m infinienv eval \
  --suite benchmarks/mvp_tasks.yaml \
  --num-samples 50 \
  --out evals/mvp_run
```

---

## Core features

### 1. Text-to-environment generation

The harness accepts natural-language task commands and converts them into a constrained JSON scene format.

Supported MVP task types:

- Navigate to target
- Pick up object
- Deliver object to target zone
- Find key and unlock door
- Avoid obstacle or hazard
- Multi-room traversal
- Simple household scene generation
- Maze-like procedural layouts

### 2. SceneSpec DSL

All generated environments are represented as typed JSON. This keeps the LLM constrained and makes validation deterministic.

Minimal example:

```json
{
  "version": "0.1",
  "seed": 42,
  "world": {
    "width": 16,
    "height": 12,
    "theme": "kitchen"
  },
  "agent": {
    "id": "agent_0",
    "position": [1, 1],
    "inventory": []
  },
  "objects": [
    {
      "id": "table_0",
      "type": "table",
      "position": [6, 4],
      "solid": true
    },
    {
      "id": "can_0",
      "type": "can",
      "position": [6, 3],
      "pickupable": true
    },
    {
      "id": "sink_0",
      "type": "sink",
      "position": [12, 8],
      "target_zone": true
    }
  ],
  "walls": [
    [[0, 0], [15, 0]],
    [[0, 11], [15, 11]],
    [[0, 0], [0, 11]],
    [[15, 0], [15, 11]]
  ],
  "goal": {
    "type": "deliver",
    "object": "can_0",
    "target": "sink_0"
  }
}
```

### 3. Deterministic validation

The validator checks that every generated environment is playable and meaningful.

MVP checks:

- JSON schema is valid
- Required fields exist
- Coordinates are in bounds
- Agent spawn is valid
- No illegal object overlap
- Pickup objects are reachable
- Goal target is reachable
- Key-door dependencies are solvable
- At least one valid action plan exists
- The final success condition is programmatically checkable

### 4. Repair loop

If validation fails, the harness sends a compact error report back to the LLM and asks for a corrected `SceneSpec`.

Example repair prompt:

```text
The generated scene failed validation.

Errors:
1. can_0 is unreachable from agent_0.
2. sink_0 is blocked by wall segment wall_3.

Modify only the layout and object positions. Preserve the original task:
"pick up the can and bring it to the sink."
Return only valid SceneSpec JSON.
```

### 5. Agent navigation

The MVP agent uses symbolic navigation, not vision. This is intentional: the goal is to prove generation, validation, and objective completion first.

MVP navigation stack:

- Grid-based movement
- BFS or A* pathfinding
- Subgoal planner
- Action executor
- Collision checks
- Inventory state
- Goal completion checker

Example action trace:

```json
[
  {"t": 0, "action": "move_right", "position": [2, 1]},
  {"t": 1, "action": "move_down", "position": [2, 2]},
  {"t": 9, "action": "pickup", "object": "can_0"},
  {"t": 21, "action": "drop", "target": "sink_0"},
  {"t": 22, "success": true}
]
```

---

## Recommended stack

### MVP stack

| Layer | Recommendation | Reason |
|---|---|---|
| Language | Python | Fast iteration, strong AI/ML ecosystem |
| Rendering | Pygame or Arcade | Simple 2D visualization and easy setup |
| Environment model | Custom grid engine | More controllable than a full physics engine for MVP |
| Pathfinding | BFS / A* | Deterministic, explainable, reliable |
| Scene format | JSON + Pydantic | Typed validation and clear errors |
| LLM integration | OpenAI Agents SDK default + provider abstraction | Best for tool calling, guardrails, handoffs, tracing, and multi-step repair workflows |
| Batch evaluation | Pytest + YAML suites + agent traces | Reproducible evaluation with inspectable generation/repair history |
| Output artifacts | JSON, PNG, GIF, metrics | Easy for reviewers to inspect |

### Optional GPU/AI stack

| Use case | Tooling |
|---|---|
| Local LLM inference | vLLM, Ollama, llama.cpp, Transformers, OpenAI-compatible servers |
| Open-source models | Qwen, Llama, DeepSeek Coder, Codestral-style coding models |
| Training or fine-tuning | PyTorch, TRL, PEFT/LoRA |
| Vision agent experiments | PyTorch, JAX, Gymnasium wrappers |
| 3D future path | Godot, Unity ML-Agents, Isaac Lab, Genesis, MuJoCo, Habitat |

---

## Architecture

```text
┌────────────────────┐
│   Text Command     │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│   LLM Compiler     │
│  prompt → JSON     │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│    SceneSpec DSL   │
│ typed JSON schema  │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│  Scene Validator   │◄────────────┐
│ schema + geometry  │             │
└─────────┬──────────┘             │
          │                        │
    valid │ invalid                │
          ▼                        │
┌────────────────────┐             │
│ Environment Builder│             │
│ grid + objects     │             │
└─────────┬──────────┘             │
          │                        │
          ▼                        │
┌────────────────────┐             │
│   Agent Harness    │             │
│ A* + subgoals      │             │
└─────────┬──────────┘             │
          │                        │
          ▼                        │
┌────────────────────┐             │
│ Goal Verification  │             │
│ code-level success │             │
└─────────┬──────────┘             │
          │                        │
          ▼                        │
┌────────────────────┐             │
│ Artifacts + Replay │             │
│ JSON / PNG / GIF   │             │
└────────────────────┘             │
                                   │
┌────────────────────┐             │
│    Repair Prompt   │─────────────┘
│ validation errors  │
└────────────────────┘
```

---

## Repository structure

```text
infinienv/
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── benchmarks/
│   ├── mvp_tasks.yaml
│   ├── kitchen_tasks.yaml
│   └── maze_tasks.yaml
├── docs/
│   ├── MVP_SPEC.md
│   ├── architecture.png
│   └── demo_script.md
├── examples/
│   ├── kitchen_can.json
│   ├── key_door_maze.json
│   └── cluttered_room.json
├── src/
│   └── infinienv/
│       ├── __init__.py
│       ├── cli.py
│       ├── compiler/
│       │   ├── llm_compiler.py
│       │   ├── prompts.py
│       │   ├── repair.py
│       │   └── templates.py
│       ├── llm/
│       │   ├── agents_runtime.py
│       │   ├── tools.py
│       │   └── providers/
│       │       ├── openai_agents.py
│       │       ├── openai_responses.py
│       │       ├── anthropic.py
│       │       ├── vllm.py
│       │       └── mock.py
│       ├── schema/
│       │   ├── scene_spec.py
│       │   └── validators.py
│       ├── engine/
│       │   ├── grid_world.py
│       │   ├── objects.py
│       │   ├── actions.py
│       │   └── renderer.py
│       ├── agent/
│       │   ├── planner.py
│       │   ├── pathfinding.py
│       │   └── executor.py
│       ├── eval/
│       │   ├── suite.py
│       │   ├── metrics.py
│       │   └── report.py
│       └── utils/
│           ├── io.py
│           └── seeds.py
├── tests/
│   ├── test_schema.py
│   ├── test_validation.py
│   ├── test_pathfinding.py
│   ├── test_goals.py
│   └── test_repair_loop.py
└── runs/
    └── .gitkeep
```

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/your-org/infinienv.git
cd infinienv
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure optional LLM access

```bash
cp .env.example .env
```

Example `.env`:

```bash
OPENAI_API_KEY=sk-...
LLM_PROVIDER=openai_agents
LLM_MODEL=gpt-4.1
LLM_TEMPERATURE=0.2
MAX_REPAIR_ATTEMPTS=3
TRACE_AGENT_RUNS=true
```

For local GPU inference:

```bash
LLM_PROVIDER=vllm
LLM_BASE_URL=http://localhost:8000/v1
LLM_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct
```

---

## Running the MVP

### Generate a scene

```bash
python -m infinienv generate \
  --provider openai_agents \
  --prompt "Create a small warehouse where the agent must pick up a package and deliver it to the loading dock." \
  --seed 123 \
  --out runs/warehouse_package
```

### Validate a scene

```bash
python -m infinienv validate runs/warehouse_package/scene.json
```

### Render a scene

```bash
python -m infinienv render runs/warehouse_package/scene.json \
  --out runs/warehouse_package/render.png
```

### Play manually

```bash
python -m infinienv play runs/warehouse_package/scene.json
```

### Let the agent solve it

```bash
python -m infinienv solve runs/warehouse_package/scene.json \
  --save-replay runs/warehouse_package/replay.json
```

### Export a GIF replay

```bash
python -m infinienv export-gif runs/warehouse_package/replay.json \
  --out runs/warehouse_package/replay.gif
```

---

## Evaluation

The MVP should be evaluated on both generation quality and objective completion.

### Core metrics

| Metric | Definition |
|---|---|
| Generation success rate | Percent of prompts producing valid scenes |
| Repair success rate | Percent of invalid scenes fixed within max repair attempts |
| Solvability rate | Percent of valid scenes with at least one valid plan |
| Agent completion rate | Percent of scenes where the agent completes the goal |
| Average repair attempts | Mean number of repairs needed per prompt |
| Average path length | Mean number of actions in successful runs |
| Diversity score | Variation across layouts, objects, and task structures |
| Runtime | Time from prompt to validated playable scene |

### Run the evaluation suite

```bash
python -m infinienv eval \
  --suite benchmarks/mvp_tasks.yaml \
  --num-samples 100 \
  --out evals/mvp_100
```

Expected report:

```json
{
  "num_prompts": 100,
  "generation_success_rate": 0.92,
  "repair_success_rate": 0.81,
  "solvability_rate": 0.96,
  "agent_completion_rate": 0.94,
  "avg_repair_attempts": 0.7,
  "avg_runtime_seconds": 3.8
}
```

---

## Benchmark prompts

Example `benchmarks/mvp_tasks.yaml`:

```yaml
suite_name: mvp_tasks
seed: 42
prompts:
  - id: kitchen_can
    prompt: Create a kitchen where the agent must pick up a can from a table and bring it to the sink.
    required_goal_type: deliver

  - id: key_door_exit
    prompt: Create a three-room maze where the agent must find a key, unlock a door, and reach the exit.
    required_goal_type: unlock_then_navigate

  - id: warehouse_package
    prompt: Create a small warehouse where the agent must pick up a package and deliver it to the loading dock.
    required_goal_type: deliver

  - id: cluttered_room
    prompt: Create a cluttered room where the agent must navigate around furniture to reach a laptop.
    required_goal_type: navigate
```

---

## LLM compiler strategy

The LLM should never emit arbitrary executable code in the MVP. It should emit only constrained `SceneSpec` JSON.

The default implementation should use the OpenAI Agents SDK as the orchestrator around this compiler. The SDK should manage the multi-step loop, while InfiniEnv's own Python tools handle validation, solving, rendering, and artifact writing.

### Generation prompt principles

- Give the model the exact schema.
- Require JSON only.
- Require bounded coordinates.
- Require explicit object IDs.
- Require task-relevant objects.
- Forbid unsupported mechanics.
- Ask for simple, solvable layouts.
- Let deterministic code reject invalid outputs.

### Recommended compiler modes

| Mode | Description | Use case |
|---|---|---|
| `template` | Non-AI procedural templates | Offline demos and tests |
| `openai_agents` | OpenAI Agents SDK workflow with planner, repair, tool calls, guardrails, and tracing | Best submitted runtime |
| `openai_responses` | Direct Responses API call without managed agent orchestration | Simpler fallback for one-shot JSON generation |
| `local` | Local GPU model through vLLM/Ollama/OpenAI-compatible server | Lower cost, private, extensible |
| `hybrid` | Template seed + LLM semantic edits | Most reliable for MVP |

The best demo mode is usually `hybrid`: generate a valid procedural skeleton first, then let the LLM adapt object choices, theme, and goal semantics.

---

## GPU path

The MVP does not require a GPU. GPU access becomes valuable for:

1. Running local LLM inference for scene generation.
2. Generating thousands of environments in parallel.
3. Training or fine-tuning a generator on accepted `SceneSpec` outputs.
4. Training navigation or reward models on programmatic success signals.
5. Extending from symbolic 2D navigation to pixel-based policies.

Recommended local setup:

```bash
vllm serve Qwen/Qwen2.5-Coder-32B-Instruct \
  --tensor-parallel-size 1 \
  --max-model-len 8192
```

Then:

```bash
python -m infinienv generate \
  --provider vllm \
  --prompt "Create a restaurant where the agent must bring a plate from the counter to a table." \
  --out runs/restaurant_plate
```

---

## 3D extension path

InfiniEnv starts in 2D because 2D lets the system prove environment generation and task verification without depending on a vision policy.

The path to 3D is to keep the same high-level `SceneSpec` and add exporters:

```text
SceneSpec
  ├── 2D Grid Engine Exporter
  ├── Godot 2D Exporter
  ├── Godot 3D Exporter
  ├── Unity ML-Agents Exporter
  ├── Isaac Lab Exporter
  └── Genesis / MuJoCo Exporter
```

The same logical task can then be rendered into richer environments for vision-based policies.

Example future 3D goal:

```json
{
  "goal": {
    "type": "pickup_and_place",
    "object": "can_0",
    "source": "table_0",
    "target": "sink_0",
    "success_condition": "distance(can_0, sink_0) < 0.25"
  }
}
```

---

## What makes this submission strong

This project is designed to be immediately understandable to reviewers:

- It has a narrow MVP scope.
- It produces working playable environments.
- It uses OpenAI Agents SDK in a controlled, research-relevant way.
- It includes deterministic validation and success checks outside the model.
- It generates replayable artifacts.
- It can run without access to a proprietary vision policy.
- It has a clear path from 2D symbolic navigation to 3D vision-based navigation.

The key argument:

> InfiniEnv treats the LLM as a semantic scene compiler and repair agent, while the harness acts as the deterministic simulator, validator, navigator, and judge.

---

## MVP acceptance criteria

The MVP is complete when the repo can demonstrate all of the following:

- [ ] Accepts a natural-language task prompt.
- [ ] Produces a valid `SceneSpec` JSON file.
- [ ] Builds a playable 2D environment from the spec.
- [ ] Validates object placement, reachability, and solvability.
- [ ] Repairs invalid generations automatically.
- [ ] Runs an agent that completes at least three task types.
- [ ] Saves replay, metrics, and agent trace artifacts.
- [ ] Includes a benchmark suite with at least 20 prompts.
- [ ] Provides one-command demo instructions with `--provider openai_agents` and `--provider mock` modes.
- [ ] Includes clear documentation explaining architecture and tradeoffs.

---

## Development roadmap

### Phase 1: Deterministic core

- Implement `SceneSpec` schema.
- Implement grid world and object model.
- Implement renderer.
- Implement BFS/A* pathfinding.
- Implement goal checker.
- Add unit tests.

### Phase 2: Generation layer

- Add template-based generator.
- Add LLM compiler.
- Add OpenAI Agents SDK runtime provider.
- Add function tools for validation, solving, rendering, and artifact writing.
- Add JSON extraction and schema validation.
- Add repair loop.
- Add prompt library.

### Phase 3: Agent harness

- Add subgoal planner.
- Add action executor.
- Add replay logging.
- Add GIF export.
- Add manual play mode.

### Phase 4: Evaluation and packaging

- Add benchmark YAML suites.
- Add metrics reporter.
- Add demo script.
- Add sample scenes.
- Add submission-ready docs.

### Phase 5: Advanced extensions

- Add local GPU model support.
- Add curriculum generation.
- Add diversity scoring.
- Add 3D export prototype.
- Add Gymnasium-compatible API.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agent workflow becomes too opaque | Save OpenAI agent traces, validation reports, and repair diffs |
| LLM emits invalid JSON | Strict schema, JSON repair, retry loop |
| Scene is visually plausible but unsolvable | Reachability and symbolic planner validation |
| Too much scope | Stay 2D-first, grid-first, DSL-first |
| Physics bugs | Keep MVP physics simple and deterministic |
| Weak demo clarity | Save render, replay, metrics, and validation report |
| Local GPU setup complexity | Support OpenAI Agents SDK API mode and mock/template modes as fallbacks |
| 3D path feels hand-wavy | Use a stable SceneSpec abstraction with exporter interfaces |

---

## Suggested demo script

1. Show a text prompt.
2. Run `python -m infinienv generate`.
3. Open the generated `scene.json`.
4. Show the validation report.
5. Render the environment.
6. Run the agent solver.
7. Show the replay GIF.
8. Open `metrics.json` proving success.
9. Run a second prompt to show diversity.
10. Explain the repair loop with one intentionally invalid scene.

---

## License

MIT License recommended for the MVP unless a challenge submission requires a different license.

---

## Status

MVP specification complete. Implementation should prioritize a reliable 2D path over an ambitious 3D prototype.
