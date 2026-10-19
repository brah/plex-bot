# Changelog

What changed in each release, newest first. Releases are also on
[GitHub](https://github.com/brah/plex-bot/releases), with Docker images at `ghcr.io/brah/plex-bot`.

## Unreleased

The first versioned release.

### New features

- The bot shows its version on `/plex status` and in its log, and logs when a newer release is out.

### Changes

- Docker Compose runs the published image, `ghcr.io/brah/plex-bot`, instead of building one. In
  the folder you run the bot from, `git pull` (or, if you've edited `docker-compose.yml`, replace
  `build: .` with `image: ghcr.io/brah/plex-bot:latest` yourself), then run
  `docker compose pull && docker compose up -d`. Keep using that folder: one set up elsewhere
  starts with an empty data volume, without your user links.
- systemd: the bot starts with `python -m plexbot`. Match `ExecStart=` and `WorkingDirectory=` to
  [`deploy/plexbot.service`](https://github.com/brah/plex-bot/blob/main/deploy/plexbot.service).
