# PATHWAY.md

# InfiniEnv Pathway: MacBook + API Access to an Impressive Real Submission

This pathway describes how to move from the current MVP into the version that will genuinely impress the General Intuition research team, while staying realistic for a MacBook development environment.

The key constraint: the MacBook should not do heavyweight model inference or training. It should run the deterministic harness locally, while OpenAI APIs provide scene planning, repair, asset planning, and optional sprite generation.

The final goal is not a toy gridworld. The final goal is a verified environment factory:

> InfiniEnv compiles natural language into playable, validated, replayable game environments. The LLM proposes scene graphs and assets; deterministic code validates physics, reachability, navigation, objective completion, replay, metrics, and dataset export.

---

## 0. Final Target Demo

The final submission should be runnable with one strong command:

```bash
python -m infinienv generate \
  "Create a convenience store where the agent must get a key from behind the counter, unlock the freezer, pick up a soda can, and bring it to checkout while navigating around blocked aisles." \
  --provider openai_agents \
  --model gpt-5.5 \
  --assets auto \
  --mutations 10 \
  --replay
```

Expected console output:

```text
✓ Scene generated with OpenAI Agents SDK
✓ SceneSpec schema valid
✓ Validator found 2 issues
✓ Repair Agent fixed unreachable freezer and missing checkout tag
✓ Scene validated after 1 repair cycle
✓ 9 assets resolved: 6 local, 3 generated
✓ All required objects reachable
✓ Key-door dependency solvable
✓ A* controller completed objective
✓ Replay saved to outputs/latest/replay.gif
✓ Metrics saved to outputs/latest/metrics.json
✓ 10 variants generated
✓ 9/10 variants solved
✓ Dataset export saved to outputs/latest/dataset.jsonl
```

Expected artifacts:

```text
outputs/latest/
  scene.json
  validation_report.json
  repair_trace.json
  asset_plan.json
  asset_manifest.json
  replay.json
  replay.gif
  metrics.json
  dataset.jsonl
  variants/
    variant_001/
      scene.json
      replay.gif
      metrics.json
    variant_002/
    ...
```

This is the version that directly satisfies the challenge criteria:

- Creativity: real agentic environment factory, assets, mutations, curriculum, repair.
- Clarity: one-command demo, readable outputs, metrics, replay GIF.
- Working output: local deterministic engine, validator, navigator, replay, tests.

---

## 1. What Runs Locally vs. What Uses APIs

### Local MacBook responsibilities

```text
- Python package and CLI
- SceneSpec schema
- deterministic validator
- grid engine
- Pygame/Arcade renderer
- A* navigation
- goal execution
- replay recorder
- replay GIF renderer
- mutation verifier
- curriculum verifier
- dataset exporter
- tests
- docs
```

### API responsibilities

```text
- scene planning from natural language
- scene repair from validator errors
- mutation proposals
- curriculum proposals
- asset planning
- optional image/sprite generation
```

The MacBook can run almost all of the impressive version because the hardest part locally is not GPU compute; it is correct software architecture.

---

## 2. Exact Repos and Libraries to Use

### Core runtime

Use these as the actual project dependencies:

```text
Python 3.11+
```

```text
openai-agents
Repo: https://github.com/openai/openai-agents-python
Purpose: real agent runtime, tool calls, handoffs, guardrails, tracing, sessions.
Use for: Scene Planner Agent, Repair Agent, Asset Planner Agent, Mutation Agent, Curriculum Agent.
```

```text
openai
Repo: https://github.com/openai/openai-python
Purpose: direct OpenAI API calls, especially image generation if not routed through agent tools.
Use for: gpt-image-2 sprite generation and lower-level Responses/Image API calls.
```

```text
pydantic
Repo: https://github.com/pydantic/pydantic
Purpose: strict SceneSpec schema, validation, typed outputs, JSON schema generation.
Use for: every object that crosses the LLM/deterministic boundary.
```

```text
typer
Repo: https://github.com/fastapi/typer
Purpose: clean CLI.
Use for: python -m infinienv generate/play/replay/eval/mutate/curriculum.
```

```text
rich
Repo: https://github.com/Textualize/rich
Purpose: pretty terminal output, tables, validation summaries.
Use for: making the demo readable in 30 seconds.
```

### 2D engine and rendering

Recommended primary choice:

```text
pygame-ce
Repo: https://github.com/pygame-community/pygame-ce
Purpose: local 2D game loop, rendering, keyboard play mode, simple visualization.
Why: easy to install, simple enough for evaluators, reliable on MacBook.
```

Optional alternative:

```text
arcade
Repo: https://github.com/pythonarcade/arcade
Purpose: nicer 2D rendering and sprite management.
Why optional: cleaner game abstractions, but Pygame is simpler and more familiar.
```

Use **pygame-ce first**. Do not over-optimize graphics before the validator and replay work.

### Navigation and algorithms

```text
networkx
Repo: https://github.com/networkx/networkx
Purpose: graph operations and reachability checks.
Use for: validator reachability, dependency graphs, optional pathfinding helpers.
```

However, implement a small custom A* too:

```text
infinienv/navigation/astar.py
Purpose: transparent reviewer-friendly pathfinding.
Why: the evaluator can read it quickly and trust it.
```

### Image and replay artifacts

```text
pillow
Repo: https://github.com/python-pillow/Pillow
Purpose: sprite loading, resizing, tilesheet processing, image cleanup, frame rendering.
```

```text
imageio
Repo: https://github.com/imageio/imageio
Purpose: replay GIF export.
```

```text
numpy
Repo: https://github.com/numpy/numpy
Purpose: grid arrays, frame buffers, path maps.
```

Optional background removal for generated assets:

```text
rembg
Repo: https://github.com/danielgatis/rembg
Purpose: remove generated image backgrounds if gpt-image-2 output is not transparent.
Note: use as optional, not required. It can add installation friction.
```

Safer fallback if `rembg` is too heavy:

```text
Use generated image as a square card/sprite with a clean background.
Or post-process by cropping object area and treating white/near-white as alpha.
```

### Testing

```text
pytest
Repo: https://github.com/pytest-dev/pytest
Purpose: validator, generator, replay, mutation, CLI tests.
```

```text
pytest-cov
Repo: https://github.com/pytest-dev/pytest-cov
Purpose: optional coverage.
```

### Packaging

```text
uv
Repo: https://github.com/astral-sh/uv
Purpose: fast Python package/install workflow.
```

Use `uv` if you want modern speed. Otherwise `pip` is fine.

### Optional later export targets

Do not build these first, but mention them credibly as extensions:

```text
Godot
Repo: https://github.com/godotengine/godot
Purpose: future richer 2D/3D engine export.
Path: SceneSpec -> Godot scene JSON / GDScript importer.
```

```text
ManiSkill
Repo: https://github.com/haosulab/ManiSkill
Purpose: future embodied 3D manipulation benchmark bridge.
Path: SceneSpec tasks -> manipulation scenes.
```

```text
Habitat-Lab
Repo: https://github.com/facebookresearch/habitat-lab
Purpose: future embodied navigation benchmark bridge.
Path: SceneSpec navigation goals -> Habitat episodes.
```

```text
Isaac Lab
Repo: https://github.com/isaac-sim/IsaacLab
Purpose: future GPU simulation / robotics environment scale-up.
Path: SceneSpec -> Isaac scene/task config.
```

For the MacBook submission, these are roadmap targets, not dependencies.

---

## 3. Exact OpenAI Models to Use

Use the model split below.

### Scene Planner Agent

```text
Model: gpt-5.5
Reasoning: high
Purpose: turn a natural-language task into valid SceneSpec JSON.
Why: highest-value reasoning/coding step. This is where mistakes are expensive.
```

### Repair Agent

```text
Model: gpt-5.5
Reasoning: high
Purpose: take validator errors and modify only the invalid parts of SceneSpec.
Why: repair needs precise constraint satisfaction.
```

### Mutation Agent

```text
Model: gpt-5.4-mini
Reasoning: medium
Purpose: create many valid variants from an already valid base scene.
Why: cheaper, faster, and mutations are lower risk than first-generation planning.
```

### Curriculum Agent

```text
Model: gpt-5.4-mini
Reasoning: medium/high
Purpose: produce a sequence of increasingly difficult tasks.
Why: mostly structured expansion, not deep one-shot reasoning.
```

### Asset Planner Agent

```text
Model: gpt-5.4-mini
Reasoning: low/medium
Purpose: map SceneSpec object types to asset needs.
Why: cheap classification/planning task.
```

### Image/Sprite Generation

```text
Model: gpt-image-2
Purpose: generate top-down game sprites or object cards.
Important caveat: gpt-image-2 currently does not support transparent backgrounds directly, so use a local post-processing step or accept square-card sprites.
```

### Fallback model strategy

If cost becomes an issue:

```text
Use gpt-5.5 only for:
- initial scene generation
- repair after failed validation

Use gpt-5.4-mini for:
- asset planning
- mutation
- curriculum
- descriptions
- README/demo text
```

If API access to image generation is unavailable:

```text
Use local placeholder sprites and keep --assets auto functional.
The run should say: "Image generation unavailable; using deterministic local sprites."
Do not silently claim real generated assets were used.
```

---

## 4. Required Repo Structure

Create this structure:

```text
infinienv/
  __init__.py
  cli.py

  agents/
    __init__.py
    openai_runtime.py
    prompts.py
    scene_planner.py
    repair_agent.py
    asset_planner.py
    mutation_agent.py
    curriculum_agent.py

  providers/
    __init__.py
    base.py
    openai_agents.py
    mock.py

  core/
    __init__.py
    schema.py
    validator.py
    goals.py
    repair.py
    mutations.py
    curriculum.py
    dataset.py
    errors.py

  engine/
    __init__.py
    gridworld.py
    pygame_renderer.py
    renderer_base.py
    replay.py
    sprites.py

  navigation/
    __init__.py
    astar.py
    controller.py
    planner.py

  assets/
    __init__.py
    resolver.py
    generator_openai.py
    postprocess.py
    manifest.py
    base/
      agent.png
      wall.png
      floor.png
      door.png
      key.png
      can.png
      table.png
      sink.png
      checkout.png
      freezer.png
    generated/
      .gitkeep

  export/
    __init__.py
    godot.py
    jsonl.py

examples/
  prompts/
    kitchen_can.txt
    warehouse_key_package.txt
    convenience_store_soda.txt
  scenes/
    kitchen_can.valid.json
    warehouse_key_package.valid.json

outputs/
  .gitkeep

tests/
  test_schema.py
  test_validator.py
  test_astar.py
  test_goals.py
  test_replay.py
  test_mutations.py
  test_cli_smoke.py

README.md
CLAUDE.md
PATHWAY.md
pyproject.toml
.env.example
```

---

## 5. SceneSpec: The Contract Between AI and Engine

The most important design rule:

> The LLM never writes arbitrary game code. It only writes SceneSpec JSON.

The engine accepts only validated `SceneSpec` objects.

Minimum schema:

```json
{
  "version": "0.2",
  "metadata": {
    "name": "convenience_store_soda",
    "theme": "convenience_store",
    "prompt": "Create a convenience store...",
    "difficulty": 3
  },
  "grid": {
    "width": 20,
    "height": 14,
    "tile_size": 32
  },
  "agent": {
    "id": "agent_1",
    "position": [2, 2],
    "inventory_capacity": 1
  },
  "tiles": [
    {"type": "wall", "positions": [[0,0], [1,0]]},
    {"type": "floor", "fill": "default"}
  ],
  "objects": [
    {
      "id": "counter_1",
      "type": "counter",
      "position": [5, 4],
      "size": [4, 1],
      "blocking": true
    },
    {
      "id": "key_1",
      "type": "key",
      "position": [4, 5],
      "pickupable": true,
      "opens": "freezer_door_1"
    },
    {
      "id": "freezer_door_1",
      "type": "door",
      "position": [12, 5],
      "locked": true,
      "requires": "key_1",
      "blocking": true
    },
    {
      "id": "soda_can_1",
      "type": "soda_can",
      "position": [14, 5],
      "pickupable": true
    },
    {
      "id": "checkout_1",
      "type": "checkout",
      "position": [17, 10],
      "target": true
    }
  ],
  "goal": {
    "type": "sequence",
    "steps": [
      {"action": "pickup", "object": "key_1"},
      {"action": "unlock", "object": "freezer_door_1", "using": "key_1"},
      {"action": "pickup", "object": "soda_can_1"},
      {"action": "deliver", "object": "soda_can_1", "target": "checkout_1"}
    ]
  },
  "assets": {
    "style": "top-down pixel art",
    "mode": "auto"
  }
}
```

Pydantic should reject anything outside the allowed schema.

---

## 6. OpenAI Agents SDK Runtime Design

Use the Agents SDK for the real runtime because this project needs multiple coordinated steps: planner, deterministic tool calls, repair, asset planning, mutation, and tracing.

### Agent graph

```text
SceneManager Agent
  ├── ScenePlanner Agent
  ├── Validator Tool
  ├── Repair Agent
  ├── AssetPlanner Agent
  ├── AssetResolver Tool
  ├── Mutation Agent
  └── Curriculum Agent
```

### Core tools exposed to agents

The agents should only get controlled tools:

```python
def validate_scene(scene_spec: dict) -> dict:
    """Return schema errors, overlap errors, reachability errors, and goal errors."""


def repair_scene_request(scene_spec: dict, errors: list[dict]) -> dict:
    """Package deterministic errors for the Repair Agent."""


def resolve_assets(scene_spec: dict, mode: str) -> dict:
    """Resolve local, generated, or placeholder assets and return asset manifest."""


def run_navigation(scene_spec: dict) -> dict:
    """Run deterministic A* controller and return success, path, and metrics."""


def render_replay(scene_spec: dict, replay: dict) -> dict:
    """Render replay frames/GIF and return artifact paths."""
```

Agents should not have file-system write access except through approved output functions.

### Why this matters

The model proposes. The deterministic harness disposes.

This gives the project a research-grade reliability story instead of a fragile prompt demo.

---

## 7. CLI Commands to Implement

### Generate one environment

```bash
python -m infinienv generate "Create a kitchen where the agent picks up a can from the table and brings it to the sink" \
  --provider openai_agents \
  --model gpt-5.5 \
  --assets auto \
  --replay
```

### Play an environment manually

```bash
python -m infinienv play outputs/latest/scene.json
```

### Run deterministic agent

```bash
python -m infinienv solve outputs/latest/scene.json --replay
```

### Replay a completed run

```bash
python -m infinienv replay outputs/latest/replay.json
```

### Generate mutations

```bash
python -m infinienv mutate outputs/latest/scene.json \
  --count 10 \
  --provider openai_agents \
  --model gpt-5.4-mini
```

### Generate curriculum

```bash
python -m infinienv curriculum \
  "retrieve a soda can and bring it to checkout" \
  --levels 5 \
  --provider openai_agents \
  --model gpt-5.4-mini
```

### Evaluate prompt suite

```bash
python -m infinienv eval examples/prompts \
  --provider openai_agents \
  --model gpt-5.5 \
  --assets local \
  --max-repairs 3
```

### Offline smoke test

```bash
python -m infinienv eval examples/prompts \
  --provider mock \
  --assets local
```

Important: `mock` is only for CI/smoke tests. It is not the success path.

---

## 8. Asset Pipeline That Actually Works on a MacBook

### Asset modes

```text
--assets none
Use colored shapes only.

--assets local
Use only assets/base/*.png.

--assets generated
Call gpt-image-2 for missing assets.

--assets auto
Try local first, then generated, then fallback placeholder.
```

### Recommended asset flow

```text
SceneSpec objects
  ↓
Asset Planner Agent
  ↓
asset_plan.json
  ↓
Asset Resolver
  ├── local cache hit: assets/base/*.png
  ├── generated cache hit: assets/generated/*.png
  ├── call gpt-image-2 if missing
  └── fallback placeholder if API unavailable
  ↓
asset_manifest.json
```

### Important implementation detail

Because `gpt-image-2` does not currently support transparent backgrounds directly, do not depend on transparent PNG output.

Use one of these strategies:

1. Generate square top-down sprite cards with clean backgrounds.
2. Use local post-processing to remove white/flat backgrounds.
3. Use local placeholder sprites for collision-critical objects.
4. Keep visual asset separate from collision geometry.

The final game truth should always come from SceneSpec, not from pixels.

### Asset manifest example

```json
{
  "soda_can": {
    "source": "generated",
    "path": "assets/generated/soda_can_pixel_001.png",
    "style": "top-down pixel art",
    "collision": "circle",
    "size": [32, 32]
  },
  "freezer": {
    "source": "local",
    "path": "assets/base/freezer.png",
    "collision": "rect",
    "size": [64, 32]
  }
}
```

### Sprite generation prompt template

```text
Top-down 2D game sprite of: {object_description}.
Style: clean pixel art, readable at 32x32, centered object, no text, no labels, plain light background, isolated object, suitable for a tile-based game.
```

For larger objects:

```text
Top-down 2D game sprite of: {object_description}.
Style: clean pixel art, readable at 64x64 or 64x32, centered object, no text, no labels, plain light background, isolated object, suitable for a tile-based game.
```

---

## 9. Deterministic Validation Requirements

Before any scene is accepted, it must pass:

```text
Schema validation
Object ID uniqueness
Grid bounds validation
No illegal overlap
Agent spawn exists
Goal references exist
Required pickup objects exist
Required targets exist
Collision map valid
Reachability from agent to each subgoal
Key-door dependency order solvable
A* path exists for each goal stage
Goal executor can complete objective
Replay can be generated
```

Validation report format:

```json
{
  "valid": false,
  "solvable": false,
  "errors": [
    {
      "code": "UNREACHABLE_OBJECT",
      "message": "soda_can_1 is unreachable because freezer_door_1 is locked and no key opens it.",
      "object_id": "soda_can_1",
      "severity": "fatal"
    }
  ],
  "repair_hints": [
    "Add a key object with opens='freezer_door_1' and make the key reachable before the door."
  ]
}
```

Repair prompt should include:

```text
You must modify only the SceneSpec JSON.
Preserve the original task intent.
Do not remove required goal steps.
Fix all validator errors.
Return only valid JSON matching the schema.
```

---

## 10. Navigation and Goal Execution

Do not use the LLM for every movement step.

Use deterministic navigation:

```text
LLM decides scene and goals.
Code converts goals into subgoals.
A* finds paths between subgoals.
Controller executes moves.
Goal executor updates inventory/door/object state.
Verifier checks completion.
```

Subgoal expansion example:

```text
Goal: deliver soda_can_1 to checkout_1

Expanded:
1. Navigate to key_1
2. Pickup key_1
3. Navigate to freezer_door_1
4. Unlock freezer_door_1
5. Navigate to soda_can_1
6. Pickup soda_can_1
7. Navigate to checkout_1
8. Drop/deliver soda_can_1
```

Action log:

```json
[
  {"t": 0, "action": "move", "direction": "right", "position": [3, 2]},
  {"t": 1, "action": "move", "direction": "right", "position": [4, 2]},
  {"t": 9, "action": "pickup", "object": "key_1"},
  {"t": 22, "action": "unlock", "object": "freezer_door_1", "using": "key_1"},
  {"t": 35, "action": "pickup", "object": "soda_can_1"},
  {"t": 58, "action": "deliver", "object": "soda_can_1", "target": "checkout_1"}
]
```

---

## 11. Mutation Engine

The mutation engine is the main answer to “infinite environment generation.”

### Required mutation types

```text
layout_shift
Move rooms, aisles, walls, doors, or counters while preserving solvability.

object_reposition
Move objects to new reachable positions.

distractor_injection
Add visually plausible but goal-irrelevant objects.

route_lengthening
Increase path length without making the task impossible.

dependency_injection
Add key-door, switch-door, or collect-before-deliver constraints.

theme_reskin
Same symbolic task, different theme: kitchen -> warehouse -> store.
```

### Mutation command

```bash
python -m infinienv mutate outputs/latest/scene.json \
  --count 10 \
  --types layout_shift,object_reposition,distractor_injection,route_lengthening \
  --provider openai_agents
```

### Mutation acceptance

Every mutation must pass the same validator and solver as the base scene.

Metric output:

```json
{
  "base_scene": "outputs/latest/scene.json",
  "requested": 10,
  "generated": 10,
  "valid": 10,
  "solved": 9,
  "failed": 1,
  "average_path_length": 96.4,
  "average_repairs": 0.7
}
```

---

## 12. Curriculum Generator

Curriculum generation makes the project look like a training/evaluation system, not just a game generator.

Command:

```bash
python -m infinienv curriculum \
  "retrieve a soda can and bring it to checkout" \
  --levels 5 \
  --provider openai_agents
```

Expected output:

```text
Level 1: open store, soda visible, checkout nearby
Level 2: aisles added, longer route
Level 3: soda behind freezer door, key visible
Level 4: key behind counter, distractor cans added
Level 5: multiple locked rooms, blocked aisles, longer route
```

Each curriculum level should output:

```text
curriculum/level_01/scene.json
curriculum/level_01/replay.gif
curriculum/level_01/metrics.json
...
```

---

## 13. Dataset Export

The challenge specifically cares about post-training environments and code-level objectives. Export that explicitly.

Dataset row:

```json
{
  "id": "convenience_store_soda_v003",
  "prompt": "Create a convenience store...",
  "scene_path": "variants/variant_003/scene.json",
  "asset_manifest_path": "variants/variant_003/asset_manifest.json",
  "replay_path": "variants/variant_003/replay.json",
  "gif_path": "variants/variant_003/replay.gif",
  "success": true,
  "path_length": 119,
  "goal": {
    "type": "sequence",
    "steps": ["pickup key", "unlock freezer", "pickup soda", "deliver checkout"]
  },
  "programmatic_reward": {
    "picked_key": 1,
    "unlocked_freezer": 1,
    "picked_soda": 1,
    "delivered_soda": 1,
    "total": 4
  }
}
```

Command:

```bash
python -m infinienv export-dataset outputs/latest/variants \
  --format jsonl \
  --out outputs/latest/dataset.jsonl
```

---

## 14. Development Milestones

### Milestone 1: Real local core

Goal: no APIs yet, but core deterministic system works.

Build:

```text
- Pydantic SceneSpec
- validator
- grid engine
- A* navigator
- goal executor
- Pygame renderer
- replay JSON
- replay GIF
- local example scenes
```

Acceptance:

```bash
pytest
python -m infinienv solve examples/scenes/kitchen_can.valid.json --replay
```

Must produce:

```text
replay.gif
metrics.json
validation_report.json
```

### Milestone 2: OpenAI Agents SDK scene generation

Goal: real text prompt becomes valid SceneSpec.

Build:

```text
- openai_agents provider
- Scene Planner Agent
- schema-constrained output
- validator tool
- repair loop
- traces saved
```

Acceptance:

```bash
python -m infinienv generate "Create a kitchen where the agent picks up a can from the table and brings it to the sink" \
  --provider openai_agents \
  --model gpt-5.5 \
  --assets local \
  --replay
```

Must produce a solved scene without using mock.

### Milestone 3: Repair loop robustness

Goal: broken generations are repaired automatically.

Build:

```text
- structured validator errors
- Repair Agent
- max repair cycles
- repair_trace.json
```

Acceptance:

```bash
python -m infinienv eval examples/prompts --provider openai_agents --max-repairs 3
```

Target:

```text
>= 80% valid scenes after repair
>= 70% solved scenes after repair
```

### Milestone 4: Asset pipeline

Goal: environments look like games, not just squares.

Build:

```text
- base sprite assets
- asset planner
- asset manifest
- generated asset cache
- optional gpt-image-2 generation
- post-processing/fallback handling
```

Acceptance:

```bash
python -m infinienv generate "Create a convenience store with freezer, soda can, key, checkout" \
  --provider openai_agents \
  --assets auto \
  --replay
```

Must produce:

```text
asset_manifest.json
rendered sprites in replay.gif
clear fallback message if image API unavailable
```

### Milestone 5: Mutation engine

Goal: one solved environment becomes many solved variants.

Build:

```text
- mutation agent
- deterministic mutation validators
- variant output directories
- mutation metrics
```

Acceptance:

```bash
python -m infinienv mutate outputs/latest/scene.json --count 10 --provider openai_agents
```

Target:

```text
>= 8/10 variants valid
>= 7/10 variants solved
```

### Milestone 6: Curriculum generator

Goal: generate increasingly hard tasks.

Build:

```text
- curriculum agent
- difficulty rubric
- level directories
- metrics per level
```

Acceptance:

```bash
python -m infinienv curriculum "retrieve a can and bring it to checkout" --levels 5 --provider openai_agents
```

Must produce five validated levels with increasing path length or dependencies.

### Milestone 7: Submission polish

Goal: evaluator understands it in under one minute.

Build:

```text
- README quickstart
- hero demo GIF
- sample outputs committed or linked
- architecture diagram
- evaluation table
- clear limitations
- future 3D path
```

Acceptance:

```bash
git clone <repo>
cd infinienv
uv sync
cp .env.example .env
# add OPENAI_API_KEY
python -m infinienv demo
```

Must run with real provider if API key is present, and with local examples if not.

---

## 15. Environment Variables

`.env.example`:

```bash
OPENAI_API_KEY=
INFINIENV_DEFAULT_PROVIDER=openai_agents
INFINIENV_DEFAULT_MODEL=gpt-5.5
INFINIENV_FAST_MODEL=gpt-5.4-mini
INFINIENV_IMAGE_MODEL=gpt-image-2
INFINIENV_OUTPUT_DIR=outputs
INFINIENV_ASSET_MODE=auto
INFINIENV_MAX_REPAIRS=3
```

---

## 16. pyproject.toml Dependency Target

Suggested dependencies:

```toml
[project]
name = "infinienv"
version = "0.2.0"
description = "Verified natural-language environment generation harness"
requires-python = ">=3.11"
dependencies = [
  "openai>=1.0.0",
  "openai-agents",
  "pydantic>=2.0.0",
  "typer>=0.12.0",
  "rich>=13.0.0",
  "pygame-ce>=2.5.0",
  "pillow>=10.0.0",
  "imageio>=2.0.0",
  "numpy>=1.26.0",
  "networkx>=3.0",
  "python-dotenv>=1.0.0"
]

[project.optional-dependencies]
dev = [
  "pytest",
  "pytest-cov",
  "ruff",
  "mypy"
]
assets = [
  "rembg"
]

[project.scripts]
infinienv = "infinienv.cli:app"
```

---

## 17. README Quickstart to Include

```bash
git clone <repo-url>
cd infinienv
uv sync
cp .env.example .env
# Add OPENAI_API_KEY to .env

python -m infinienv generate \
  "Create a convenience store where the agent must get a key, unlock the freezer, pick up a soda can, and bring it to checkout." \
  --provider openai_agents \
  --assets auto \
  --mutations 10 \
  --replay
```

For offline smoke test:

```bash
python -m infinienv demo --provider mock --assets local
```

README must clearly say:

```text
The mock provider exists only for offline smoke tests and CI. The main demo uses OpenAI Agents SDK with a real API key.
```

---

## 18. What Not to Build First

Avoid these until the 2D verified loop works:

```text
- 3D engine first
- local LLM inference on MacBook
- RL training
- vision-policy training
- complex physics
- multi-agent gameplay
- arbitrary code generation
- asset generation before validation
```

The impressive version comes from correctness + live generation + replay + mutation, not from overcomplicated graphics.

---

## 19. Final Submission Framing

Use this language in README, demo video, and project description:

> InfiniEnv is a verified environment factory. It uses OpenAI Agents SDK to compile natural-language commands into structured SceneSpec JSON, then uses deterministic validators, pathfinding, goal execution, and replay generation to prove that each environment is playable and solvable. Successful scenes can be automatically mutated into many variants and exported as dataset rows with programmatic rewards.

One-line demo pitch:

> Type a task. Get a playable world, validation proof, agent replay, and ten harder variants.

---

## 20. The Best Path From Here

Do the work in this order:

```text
1. Finish local SceneSpec + validator + A* + replay.
2. Add OpenAI Agents SDK scene generation.
3. Add repair loop.
4. Make one hero prompt work extremely well.
5. Add local assets.
6. Add optional generated assets.
7. Add mutation engine.
8. Add curriculum generator.
9. Add dataset export.
10. Polish README and record hero GIF/video.
```

The hero prompt should be:

```text
Create a convenience store where the agent must get a key from behind the counter, unlock the freezer, pick up a soda can, and bring it to checkout while navigating around blocked aisles.
```

If this one prompt works end-to-end with real API calls, validated JSON, generated/local assets, replay, and variants, the project will look substantially stronger than a normal technical challenge submission.
