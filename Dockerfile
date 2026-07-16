# InfiniEnv GUI — a long-running Flask server (SSE, background jobs, subprocess agents).
# It is NOT serverless-compatible; run it as one persistent process, exactly like
# `python -m infinienv gui`. See docs/deploy.md for hosting options.
FROM python:3.12-slim

# Node + the Claude CLI. The default sandbox backend (INFINIENV_SANDBOX_BACKEND=claude) drives the
# `claude` CLI; on a server it authenticates via ANTHROPIC_API_KEY (set it in the host env) — no
# interactive `claude login` needed. Remove this block if you only use INFINIENV_SANDBOX_BACKEND=openai.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e ".[gui,claude,openai]"

ENV PYTHONUNBUFFERED=1
# Artifacts are written under runs/ at runtime; mount a persistent volume here to keep them across
# restarts (optional — the app works fine with an ephemeral disk, you just lose past runs on redeploy).
EXPOSE 5050

# $PORT is injected by most PaaS hosts; defaults to 5050 locally. Bind 0.0.0.0 so it's reachable.
CMD ["sh", "-c", "python -m infinienv gui --host 0.0.0.0 --port ${PORT:-5050} --no-browser"]
