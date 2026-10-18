<div align="center">

<img src="docs/brand/banner.webp" alt="Plex-Bot: now showing in your Discord" width="100%">

[![CI](https://github.com/brah/plex-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/brah/plex-bot/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
[![License: MIT](https://img.shields.io/github/license/brah/plex-bot)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)](#deployment)

**[Setup](#setup)** · **[Configuration](#configuration)** · **[Commands](#commands)** · **[Deployment](#deployment)** · **[Troubleshooting](#troubleshooting)**

</div>

A Discord bot for your Plex server. It reads [Tautulli](https://github.com/Tautulli/Tautulli) and answers with slash commands: who's watching, what's popular, who watches most, charts, recommendations and library lookups.

<table>
<tr>
<td><img src="docs/screenshots/stats.webp" alt="/plex stats" width="420"></td>
<td><img src="docs/screenshots/chart-shows.webp" alt="/plex chart" width="420"></td>
</tr>
</table>

<details>
<summary><b>More screenshots</b></summary>

<table>
<tr>
<td><img src="docs/screenshots/status.webp" alt="/plex status" width="420"></td>
<td><img src="docs/screenshots/watchers.webp" alt="/plex watchers" width="420"></td>
</tr>
<tr>
<td><img src="docs/screenshots/movie.webp" alt="/plex movie" width="420"></td>
<td><img src="docs/screenshots/tv.webp" alt="/plex tv" width="420"></td>
</tr>
<tr>
<td><img src="docs/screenshots/random.webp" alt="/plex random" width="420"></td>
<td><img src="docs/screenshots/recent.webp" alt="/plex recent" width="420"></td>
</tr>
<tr>
<td><img src="docs/screenshots/top.webp" alt="/plex top" width="420"></td>
<td><img src="docs/screenshots/hot.webp" alt="/plex hot" width="420"></td>
</tr>
<tr>
<td><img src="docs/screenshots/stats-activity.webp" alt="/plex stats, Activity page" width="420"></td>
<td><img src="docs/screenshots/history.webp" alt="/plex history" width="420"></td>
</tr>
<tr>
<td><img src="docs/screenshots/chart.webp" alt="/plex chart, over time" width="420"></td>
<td><img src="docs/screenshots/chart-hours.webp" alt="/plex chart, by hour" width="420"></td>
</tr>
<tr>
<td><img src="docs/screenshots/shows.webp" alt="/plex shows" width="420"></td>
<td><img src="docs/screenshots/help.webp" alt="/plex help" width="420"></td>
</tr>
<tr>
<td><img src="docs/screenshots/admin-users.webp" alt="/plexadmin users" width="420"></td>
<td><img src="docs/screenshots/admin-cache.webp" alt="/plexadmin cache" width="420"></td>
</tr>
</table>

</details>

## Setup

You need Python 3.12+, a working Tautulli and a Discord server you can add bots to. qBittorrent is optional.

**Discord.** In the [Developer Portal](https://discord.com/developers/applications), create an application. Under **Bot**, copy the token, turn on **Server Members Intent**, and turn off **Public Bot**. Then invite it, using your application ID:

```text
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot+applications.commands&permissions=268487680
```

For the top-watcher roles, move the bot's role above those roles.

**Tautulli.** Copy the API key from **Settings → Web Interface → API**.

**Install and run:**

```bash
git clone https://github.com/brah/plex-bot.git && cd plex-bot
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.json config.json                   # fill in token, tautulli and server_id
python plexbot.py
```

Type `/plex` in Discord. To keep it running, see [Deployment](#deployment).

## Configuration

You can set each option in `config.json` (start from [`config.example.json`](config.example.json)), as an environment variable, or both. Environment variables are the easy way with Docker: put them in `.env` (see [`.env.example`](.env.example)). If an option is set in both places, the environment variable wins. Anything you leave out uses a default.

| `config.json` | Environment variable | What it does |
|---------------|----------------------|--------------|
| `core.token` | `PLEXBOT_CORE_TOKEN` | Discord bot token |
| `tautulli.ip` | `PLEXBOT_TAUTULLI_IP` | Tautulli's address, as `host:port` |
| `tautulli.apikey` | `PLEXBOT_TAUTULLI_APIKEY` | Tautulli's API key |
| `tautulli.use_https` | `PLEXBOT_TAUTULLI_USE_HTTPS` | `true` to connect to Tautulli over HTTPS |
| `discord.server_id` | `PLEXBOT_DISCORD_SERVER_ID` | Your server's ID. Commands appear there at once; blank registers them globally, which takes up to an hour |
| `discord.admin_ids` | `PLEXBOT_DISCORD_ADMIN_IDS` | Users or roles who are bot admins, besides the server's administrators |
| `discord.top_role_id` | `PLEXBOT_DISCORD_TOP_ROLE_ID` | Role for the top watcher |
| `discord.second_role_id` | `PLEXBOT_DISCORD_SECOND_ROLE_ID` | Role for the second |
| `discord.third_role_id` | `PLEXBOT_DISCORD_THIRD_ROLE_ID` | Role for the third |
| `discord.stream_manager_ids` | `PLEXBOT_DISCORD_STREAM_MANAGER_IDS` | Users or roles who may stop streams |
| `discord.show_recent_viewers` | `PLEXBOT_DISCORD_SHOW_RECENT_VIEWERS` | Show the last few viewers when nobody's watching (public, so off by default) |
| `qbittorrent.ip`, `port` | `PLEXBOT_QBITTORRENT_IP`, `PLEXBOT_QBITTORRENT_PORT` | qBittorrent Web UI address, for `/plex downloading` |
| `qbittorrent.username`, `password` | `PLEXBOT_QBITTORRENT_USERNAME`, `PLEXBOT_QBITTORRENT_PASSWORD` | qBittorrent Web UI login |
| `core.legacy_text_commands` | `PLEXBOT_CORE_LEGACY_TEXT_COMMANDS` | Point old `plex ` text commands at their slash version. Needs **Message Content Intent**, so it's off; `@PlexBot top` works either way |
| `core.log_level` | `PLEXBOT_CORE_LOG_LEVEL` | `DEBUG` to `ERROR` |

In an environment variable, write a list of IDs with commas (`PLEXBOT_DISCORD_ADMIN_IDS=123,456`) and an on/off option as `true` or `false`.

Appearance lives in `ui` (embed colour), `charts` (size and `dpi`) and `defaults`. These have environment variables too, named the same way: `charts.dpi` is `PLEXBOT_CHARTS_DPI`. The mascot art is in `assets/mascot/`.

**Keep the bot private.** Leave `server_id` set and **Public Bot** off. Otherwise anyone could add the bot to their own server and browse your Plex activity. Admins are the administrators of `server_id` plus `admin_ids`; with `server_id` blank, only `admin_ids` counts. Discord only shows `/plexadmin` to administrators; let others see it under **Server Settings → Integrations**.

Upgrading from the old flat `config.json` (keys like `tautulli_ip`)? Run `python migrate_config.py --force`, which backs up the old one first.

## Commands

Everything is under `/plex`, plus `/plexadmin` for admins.

| Command | |
|---------|--|
| `/plex status` | Server health, library size and plex.tv status |
| `/plex watchers` | Current streams; stream managers can stop them |
| `/plex movie`, `/plex tv` | Look up a title: details, cast, who's watched it |
| `/plex random` | A random pick, optionally by type and genre |
| `/plex recent` | Recently added |
| `/plex recommend` | Unwatched picks from someone's favourite genres |
| `/plex top` | Top watchers; also hands out the top-three roles |
| `/plex stats` | Server stats across five pages |
| `/plex hot` | Trending titles, binges and new hits |
| `/plex history` | Recent plays, everyone's or one person's |
| `/plex shows` | Top TV watchers of all time |
| `/plex chart` | Charts by day, hour, weekday, viewer, show or movie |
| `/plex downloading` | qBittorrent progress |
| `/plex help` | What the bot can do |
| `/plexadmin users`, `link`, `unlink`, `ignore` | Link Plex users to Discord members, or hide them from stats |
| `/plexadmin cache`, `logs` | The media cache and recent logs |

Linking matters: names, avatars, roles and `/plex recommend` need it.

## Deployment

Run exactly one instance, near Tautulli. It needs no open ports.

### Docker Compose

You need Docker with Compose 2.24 or newer, plus the Discord and Tautulli steps from [Setup](#setup).

1. Get the code and make your settings file:

   ```bash
   git clone https://github.com/brah/plex-bot.git && cd plex-bot
   cp .env.example .env
   ```

2. Open `.env` and fill in `PLEXBOT_CORE_TOKEN`, `PLEXBOT_TAUTULLI_IP`, `PLEXBOT_TAUTULLI_APIKEY` and `PLEXBOT_DISCORD_SERVER_ID`. Inside a container, `localhost` means the container itself, so use your host's LAN address (`192.168.1.10:8181`) or, if Tautulli runs in the same Compose project, its service name (`tautulli:8181`).

3. Start it:

   ```bash
   docker compose up -d --build
   docker compose logs -f        # "Logged in as ..." means it's working; Ctrl+C stops following
   ```

The bot keeps its user links (`map.json`), media cache and `plexbot.log` in the `plexbot-data` volume, so they survive rebuilds.

| To | Run |
|----|-----|
| Update | `git pull && docker compose up -d --build` |
| Apply a changed `.env` | `docker compose up -d` |
| Stop | `docker compose down` (keeps the volume) |
| Back up user links | `docker compose cp plexbot:/data/map.json map.json` |

**Prefer `config.json`?** Copy `config.example.json` to `config.json` and uncomment its line under `volumes:` in `docker-compose.yml`. Settings in `.env` still win.

**Upgrading from the old layout** (with `config.json` and `map.json` beside `docker-compose.yml`): after `git pull`, uncomment the `config.json` line, then run `docker compose up -d --build`. Copy your links into the volume with `docker compose cp map.json plexbot:/data/map.json && docker compose restart`. The media cache rebuilds by itself.

### systemd

Install into a venv, edit and copy [`deploy/plexbot.service`](deploy/plexbot.service) to `/etc/systemd/system/`, then `sudo systemctl enable --now plexbot`.

To upgrade: `git pull`, `pip install -r requirements.txt`, restart. The bot no longer asks for Message Content Intent, so you can turn it off in the portal unless you enable `core.legacy_text_commands`.

## Troubleshooting

- **`PrivilegedIntentsRequired`**: turn on **Server Members Intent**.
- **No slash commands**: set `server_id`, and check the invite included `applications.commands`.
- **"Couldn't reach Tautulli"**: check `tautulli.ip` and the key.
- **No roles handed out**: set all three role IDs, raise the bot's role, and link the winners.
- **No TV sizes on `/plex status`**: run **Calculate Total File Sizes** on the library in Tautulli.

Logs go to `plexbot.log`; admins can also fetch them with `/plexadmin logs`, with secrets redacted.

## Development

```bash
pip install -r requirements.txt pytest "ruff==0.17.0"
pytest -q && ruff check .
```

Tests need neither Discord nor Tautulli.

## A note on AI

This is a hobby project, so don't be surprised to find AI in the commits. Use whatever tools help you build and contribute.

## License

[MIT](LICENSE)
