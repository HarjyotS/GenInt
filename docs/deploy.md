# Deploying the InfiniEnv GUI

[← back to README](../README.md)

## Why not Vercel / Netlify / serverless

The GUI is a **long-running, stateful server**, not a request/response web app. It streams
Server-Sent Events for minutes, keeps in-memory job/play state and background threads, spawns the
`claude` CLI as a subprocess, and writes run artifacts to disk. Serverless platforms are short-lived,
stateless, have a read-only filesystem, and can't hold a long connection or spawn subprocesses — so
`generate`, `navigate`, `play`, and the live activity feed all break there. Deploy it as **one
persistent process** instead.

The app is just:

```bash
python -m infinienv gui --host 0.0.0.0 --port $PORT --no-browser
```

The provided `Dockerfile` runs exactly that (binding `$PORT` if the host sets one). Any host that
runs a container or a long-lived process works.

## Auth on a server (no browser)

- **OpenAI** (prompt refinement, the audit, `--assets`, `navigate`): set `OPENAI_API_KEY`.
- **The Claude sandbox backend** (the default) drives the `claude` CLI. On a headless server it
  authenticates from **`ANTHROPIC_API_KEY`** — no interactive `claude login` needed. Set that env
  var and make sure the account has credit. The `Dockerfile` installs the `claude` CLI for you.
- Prefer not to deal with the CLI? Set **`INFINIENV_SANDBOX_BACKEND=openai`** and only
  `OPENAI_API_KEY` — no Node/CLI needed (you can delete the Node block from the `Dockerfile`).

Set keys as the host's **secrets/env vars**, never in a committed `.env` (the `.dockerignore`
excludes `.env` on purpose).

## Cheap / free options

Ranked for this workload (it wants ~1 GB RAM and a persistent process; full sandbox runs are
compute- and time-heavy, and cost API credit regardless of host):

| Option | Cost | Notes |
|---|---|---|
| **Oracle Cloud "Always Free" VM** | **Free forever** | Ampere ARM, up to 4 cores / 24 GB — by far the most capable free tier. More setup (SSH + install), ARM arch (Node + the CLI + our deps all run on ARM), needs a card to sign up. Best if you want genuinely free with real resources. |
| **Fly.io** | ~a few $/mo | Docker-native, keeps the process alive, SSE + subprocess friendly. Use the included `fly.toml`. Smallest paid VM (256 MB) is too small — the config asks for 1 GB. |
| **Render** | **Free tier** | Easiest push-to-deploy (use `render.yaml`). Free = 512 MB RAM + spins down when idle — good for the UI and light runs, likely to OOM on a full sandbox build; move to the `starter` plan for that. |
| **Hetzner CX22 (VM)** | ~€3.79/mo | Best price/perf: 2 vCPU / 4 GB. Rock-solid for the full app. Manual setup (Docker on a VM, below). |
| **Railway / Koyeb** | cheap / small free | Both Docker-friendly and work the same way; trial credit then usage-based. |

### Fly.io (recommended cheap path)

```bash
fly launch --no-deploy          # or `fly apps create <name>`; then set `app` in fly.toml
fly secrets set INFINIENV_GUI_PASSWORD=choose-a-strong-password OPENAI_API_KEY=sk-... ANTHROPIC_API_KEY=sk-ant-...
fly deploy
```

### Render (free)

Push this repo to GitHub → Render → **New ▸ Blueprint** → select the repo (it reads `render.yaml`) →
fill in `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` when prompted.

### Any VM (Oracle / Hetzner / EC2 / DigitalOcean)

```bash
# on the VM, with Docker installed:
git clone https://github.com/HarjyotS/GenInt && cd GenInt
docker build -t infinienv .
docker run -d --restart unless-stopped -p 80:5050 \
  -e INFINIENV_GUI_PASSWORD='choose-a-strong-password' \
  -e OPENAI_API_KEY=sk-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e INFINIENV_SANDBOX_BACKEND=claude \
  -v "$PWD/runs:/app/runs" \
  infinienv
```

The image binds `0.0.0.0` (a public bind), so **it refuses to start without
`INFINIENV_GUI_PASSWORD`** — deliberate (see below). Set it to a strong value. (The image also sets
`IS_SANDBOX=1` so the `claude` CLI accepts the sandbox agent's permission-bypass mode inside the
container; the throwaway container is itself the isolation boundary.)

(No Docker? `pip install -e ".[gui,claude,openai]"`, install the `claude` CLI, then run the `gui`
command under `systemd`/`tmux`/`pm2` so it stays up.)

## Access control & rate limiting

The GUI has no accounts and can spend real API credit, so a public deploy is protected:

- **Password (required on public binds).** Set `INFINIENV_GUI_PASSWORD` and the whole GUI is behind
  a single shared password (a browser login prompt / HTTP Basic Auth) — enter it once per browser.
  Because the deploy binds `0.0.0.0`, the server **refuses to start without it** (a plain
  `python -m infinienv gui` on `127.0.0.1` doesn't require one). On Fly/Render, set it as a secret /
  env var alongside the API keys.
- **Rate limits (on by default).** The credit-spending endpoints (`/api/generate`, `/api/navigate`)
  are capped:
  - `INFINIENV_GUI_MAX_CONCURRENT` (default **1**) — only this many runs at once; extra requests get
    a `429` "a run is already in progress". Keeps a small VM from being overwhelmed.
  - `INFINIENV_GUI_RATE_LIMIT` (default **20**) per `INFINIENV_GUI_RATE_WINDOW` seconds (default
    **3600**), per client IP — a `429` past that.

  Tune these up if you have a bigger box / more trusted users. Even with a password, the concurrency
  cap is what protects a 1 GB VM from a burst of heavy sandbox runs.

## Honest caveats

- **A full sandbox `generate` runs an LLM agent that writes and executes code for minutes** and
  needs API credit. Free tiers with 512 MB RAM, shared CPU, or idle-spin-down may OOM, be slow, or
  get killed mid-run. Budget ~1 GB+ RAM for reliable sandbox runs.
- **The Flask dev server is what runs** (`app.run(threaded=True)`). That's intentional: the app's
  in-memory job/session state and SSE require a **single process**, so it must not be scaled to
  multiple workers/replicas (a `generate` on one worker and its `/api/stream` on another wouldn't
  find each other). One instance, threaded, is correct here. It's fine for a demo / small audience;
  don't put it behind a multi-worker autoscaler.
- **For evaluating the project, running locally is the most reliable path** (`python -m infinienv
  gui`) — the deploy is for sharing a URL, and a given host may need memory/timeout tuning before a
  heavy sandbox run completes.
