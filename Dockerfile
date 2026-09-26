FROM python:3.12-slim

ARG SANDBOX_DIR=/home/user/companion-sandbox
ENV CLAUDE_CLI_SANDBOX_PATH=$SANDBOX_DIR

WORKDIR /app

# System deps for audio processing + Node.js for the agent CLIs (Claude, OpenCode)
# + Docker CLI for run_shell's `docker exec` (via the allowlisting docker-proxy). OpenCode is pinned to the version its JSON
# event schema was verified against — see services/opencode_cli.py.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code@2.1.220 \
    && npm install -g opencode-ai@1.18.23 \
    && curl -fsSL https://get.docker.com | sh \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Install Playwright browsers + OS deps (as root, set PLAYWRIGHT_BROWSERS_PATH for all users)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium

COPY . .

# Install cloudstream CLI for anime tool
RUN pip install --no-cache-dir ./cloudstream-cli

# Create dirs that may not exist
RUN mkdir -p audio data logs "$SANDBOX_DIR"

# Trust the bind-mounted companion-sandbox repos. Host UID != container UID otherwise
# trips git's "dubious ownership" check, which silently disables auto-reload in plugins/game.
RUN git config --system --add safe.directory "$SANDBOX_DIR/*"

EXPOSE 8800

# Shell form so a second instance can move off 8800 (network_mode: host binds it directly).
CMD ["sh", "-c", "exec python -m uvicorn main:app --host 0.0.0.0 --port ${AVATAR_SERVER_PORT:-8800}"]
