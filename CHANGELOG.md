# Changelog

What changed in each release, newest first. Releases are also on
[GitHub](https://github.com/brah/plex-bot/releases), with Docker images at `ghcr.io/brah/plex-bot`.

## Unreleased

### New features

- `/plex upcoming` can read Sonarr's calendar: every episode, on the day it airs in your timezone, with the downloaded ones marked. Set `sonarr.url` and `sonarr.apikey` ([how](https://github.com/brah/plex-bot#configuration)).
- `/plex upcoming` lists movies coming out this week that Radarr is monitoring and hasn't downloaded yet. Set `radarr.url` and `radarr.apikey`.

## 1.3.1 - 2026-10-22

### Security

- Patch 28 Pillow advisories ([list](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html)) - Update Pillow to 13.0.0 to fix crashes and memory bugs from crafted images.

## 1.3.0 - 2026-10-22

### New features

- Movie and TV cards get a **Trailer** button and **More like this** from your library.
- Show cards say when the next episode airs.
- `/plex upcoming`: new episodes this week for shows on the server.
- These need a TMDB API key in `tmdb.apikey` ([how](https://github.com/brah/plex-bot#configuration)).

### Security

- Patch [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) - Update certifi to 2026.10.19 and the Docker image to Python 3.14.9 to distrust compromised eMudhra CA certificates.

### Bug fixes

- New titles no longer drop out of suggestions and `/plex random` for a day after a Tautulli outage.
- `/plex status` shows TV and music sizes as soon as Tautulli calculates them.
- Lower memory use on long `/plex chart` windows.

### Changes

- Python installs: upgrade pip to 26.2.2 before updating ([how](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)).
- Charts use `ui.plex_embed_color`; the `colors` settings are removed.

## 1.2.1 - 2026-10-22

### Bug fixes

- A title deleted from Plex no longer shows "Couldn't reach Tautulli": `/plex random` picks another, and lookups say it's gone.
- **Refresh now** on `/plexadmin cache` drops deleted titles, and says when the refresh failed.

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
