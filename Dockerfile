# syntax=docker/dockerfile:1
# 3.14.9+ for GHSA-6q48-6c7w-9gw2; pinned so a stale cached base can't be reused.
FROM python:3.14.9-slim

# Unbuffered stdout/stderr so `docker logs` is live; no .pyc clutter in layers.
# PYTHONPATH finds the plexbot package in /app while the bot runs from /data.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so this layer caches unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root user. /data is the only place the bot writes (map.json, cache/, plexbot.log),
# and a named volume mounted there inherits this ownership on first use. The home
# directory gives matplotlib a writable config/font-cache dir.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home app \
    && mkdir /data \
    && chown app:app /data

# App code, root-owned so the bot can't modify it. Local state (config.json, map.json,
# cache/, .env) is excluded via .dockerignore and never baked in.
COPY . .

# The release workflow passes the version from its tag; a local build reports "dev".
ARG VERSION=dev
ENV BOT_VERSION=$VERSION

USER app
# The bot reads config.json and writes its state relative to the working directory.
WORKDIR /data

# No EXPOSE: the bot only makes outbound connections (Discord gateway + Tautulli).
# -P keeps the writable working directory off sys.path, so nothing in /data can shadow a module.
CMD ["python", "-P", "-m", "plexbot"]
