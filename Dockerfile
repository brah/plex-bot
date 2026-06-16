# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Unbuffered stdout/stderr so `docker logs` is live; no .pyc clutter in layers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# /app (not a mounted data dir): the bot resolves cogs/, config.json, map.json
# and the cache path relative to the current directory.
WORKDIR /app

# Install deps first so this layer caches unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. Local state (config.json, map.json, cache/) is excluded via
# .dockerignore and supplied at runtime through volumes — never baked in.
COPY . .

# Run as a non-root user. UID/GID 1000 matches the typical first host user, so
# bind-mounted config.json/map.json are read/writable out of the box; override
# with `user:` in docker-compose.yml if your host user differs. /app is chowned
# so the bot can write its log and so the named cache volume inherits app
# ownership on first use.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --no-create-home app \
    && mkdir -p /app/cache \
    && chown -R app:app /app
USER app

# No EXPOSE: the bot only makes outbound connections (Discord gateway + Tautulli).
CMD ["python", "plexbot.py"]
