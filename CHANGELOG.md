# Changelog

What changed in each release, newest first. Releases are also on
[GitHub](https://github.com/brah/plex-bot/releases), with Docker images at `ghcr.io/brah/plex-bot`.

## Unreleased

## 1.2.0 - 2026-10-20

### New features

- `/plex status` shows the Plex logo before the server version. The bot adds it to its own emojis on first start.

### Bug fixes

- Stopping the bot, for example with `docker stop`, no longer logs "Unclosed client session" errors.

### Changes

- Copies cloned before 18 October 2026 can't `git pull`: run
  `git fetch origin && git reset --hard origin/main` once.
- The README covers [moving from a Python install to Docker](https://github.com/brah/plex-bot#docker-compose).
- No PyNaCl warning at startup, and the HTTPS warning only shows for a Tautulli on the internet.

## 1.1.0 - 2026-10-20

### New features

- `/plex downloading` works even if qBittorrent auth is turned off/bypassed on LAN: leave `qbittorrent.username` and `password` blank.

### Bug fixes

- Device names and titles show as written on `/plex watchers`, even with characters Discord reads
  as formatting, and a device name can no longer turn into a link.
- Picking someone from the suggestions on `/plex history` and `/plex chart` always shows that
  person, even when another viewer's Discord name is the same as their Plex username.
- `/plex chart` counts whole days, today included, so its play count matches what the bars add up to.
- Title suggestions on `/plex movie` and `/plex tv` pick up new titles on their own, instead of
  only after someone runs `/plex random` or `/plex recommend`.
- **Refresh now** under `/plexadmin cache` always re-reads every title, even when a refresh was
  already running.
- `/plex hot`, `/plex chart` and `/plex recommend` no longer count a play twice when it ends while
  the bot is reading the history.

### Changes

- A Tautulli outage logs one error when it starts and one line when it ends, instead of errors
  every 15 seconds.
- Titles that Tautulli still lists after they're removed from Plex are asked about once a day
  instead of on every refresh, so they no longer fill the log with errors.
- The first fill of the media cache, and **Refresh now**, are several times faster.
- `/plex top` only changes the top-watcher roles of people whose rank changed.

## 1.0.0 - 2026-10-19

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
