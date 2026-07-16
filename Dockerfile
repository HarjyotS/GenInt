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

# The `claude` CLI refuses --dangerously-skip-permissions (the sandbox agent's bypassPermissions
# mode) when running as root, so run as a non-root user. Give it a real HOME (for ~/.claude auth/
# config) and ownership of /app so the app, the per-run sandbox workspaces under runs/, and the
# asset cache are all writable.
RUN useradd -m -u 10001 -s /bin/bash appuser \
    && mkdir -p /app/runs \
    && chown -R appuser:appuser /app
USER appuser
ENV HOME=/home/appuser PYTHONUNBUFFERED=1
# Artifacts are written under runs/ at runtime. To persist them, use a NAMED volume
# (`-v infinienv_runs:/app/runs`) so it inherits appuser's ownership; a host bind-mount would be
# owned by the host user and appuser (uid 10001) couldn't write to it.
EXPOSE 5050

# $PORT is injected by most PaaS hosts; defaults to 5050 locally. Bind 0.0.0.0 so it's reachable.
CMD ["sh", "-c", "python -m infinienv gui --host 0.0.0.0 --port ${PORT:-5050} --no-browser"]
