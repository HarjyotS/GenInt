# InfiniEnv

**Infinite environment generation via an agent harness.** Type a world in plain English; an agent
builds it, plays it, and proves it works — where success is decided by **code**, not by a model
looking at pixels.

> A model proposes. The harness verifies. A pixel-policy proves.

The bet, straight from the challenge brief: **code-defined objectives beat a VLM checking pixels.**
Generation is semantic (an LLM writes the world), but *truth* is deterministic Python — validation,
solvability, goal completion, and reward are all code. → **[How it works](docs/overview.md)**

## Quickstart

The web GUI is the easiest way to see the whole loop. It runs the sandbox agent on the **Claude
Agent SDK** by default.

### 1. Install everything

```bash
pip install -e ".[gui,claude,openai]"
```

### 2. Add your keys (guided)

```bash
python -m infinienv setup
```

This asks for your API keys, saves them to a `.env` in the repo root, and prints a readiness
checklist — telling you exactly what (if anything) still needs installing or logging in. You only
need an **`OPENAI_API_KEY`**; it powers prompt refinement, the independent faithfulness audit, sprite
generation (`--assets`), and the `navigate` vision policy.

The Claude sandbox agent (the default) authenticates through the **`claude` CLI**, not an API key. If
the readiness check flags it, install and log in once:

```bash
npm install -g @anthropic-ai/claude-code   # skip if you already have `claude`
claude login                                # authenticates via your claude.ai account
```

### 3. Run it

```bash
python -m infinienv gui        # opens http://127.0.0.1:5050
```

Type a prompt, hit **Compile world**, and watch the agent write and run real game code live — its
decisions, commands, code edits, chosen assets, and the audit verdict stream in as it works, then
the rendered world and its replay appear inline. A "Recent worlds" strip browses past runs, and a
**Play** mode lets you drive any world with the keyboard.

> No key at all? A committed example world lives in `examples/example_world/` and shows up in the
> GUI gallery. The offline `solve` tool also works — see the [CLI reference](docs/cli.md).

## Learn more

- **[How it works](docs/overview.md)** — the pipeline, the vision-policy loop, the sandbox, and how
  the harness maps to the challenge's evaluation criteria.
- **[CLI reference](docs/cli.md)** — every command, the runtime providers, extended mechanics,
  physics, the asset pipeline, and the mutation / curriculum / dataset tools.
- **[Deploying it](docs/deploy.md)** — hosting the GUI on a persistent server (Docker + Fly.io /
  Render / a cheap VM). Note: it's a long-running, stateful server, so **serverless (Vercel/Netlify)
  won't work** — see the doc for why.
- **[CLAUDE.md](CLAUDE.md)** — the full design doc and the non-negotiable invariants.
- **[notes.md](notes.md)** — the running decision / bug log from the build.
