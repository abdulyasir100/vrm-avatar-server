FROM python:3.12-slim

WORKDIR /app

# System deps for audio processing + Node.js for Claude CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
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
RUN mkdir -p audio data logs

EXPOSE 8800

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8800"]
