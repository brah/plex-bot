# Plex-Bot

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

Start from [`config.example.json`](config.example.json); anything you leave out uses a default.

| Setting | |
|---------|--|
| `core.token` | Discord bot token |
| `tautulli.ip`, `tautulli.apikey` | Tautulli's `host:port` and API key; `use_https` to encrypt |
| `discord.server_id` | Your server's ID. Commands appear there at once; blank registers them globally, which takes up to an hour |
| `discord.admin_ids` | Users or roles who are bot admins, besides the server's administrators |
| `discord.top_role_id`, `second_role_id`, `third_role_id` | Roles for the top three watchers |
| `discord.stream_manager_ids` | Users or roles who may stop streams |
| `discord.show_recent_viewers` | Show the last few viewers when nobody's watching (public, so off by default) |
| `qbittorrent.*` | Web UI address and login, for `/plex downloading` |
| `core.legacy_text_commands` | Point old `plex ` text commands at their slash version. Needs **Message Content Intent**, so it's off; `@PlexBot top` works either way |
| `core.log_level` | `DEBUG` to `ERROR` |

Appearance lives in `ui` (embed colour), `charts` (size and `dpi`) and `defaults`. The mascot art is in `assets/mascot/`.

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

**Docker:**

```bash
cp config.example.json config.json && echo "[]" > map.json
docker compose up -d --build
```

Inside a container, `localhost` means the container, so point `tautulli.ip` at your host's LAN address or the Tautulli service name. The container runs as `1000:1000`, so that user needs to be able to read and write `config.json` and `map.json`.

**systemd:** install into a venv, edit and copy [`deploy/plexbot.service`](deploy/plexbot.service) to `/etc/systemd/system/`, then `sudo systemctl enable --now plexbot`.

**Upgrading:** `git pull`, `pip install -r requirements.txt`, restart. The bot no longer asks for Message Content Intent, so you can turn it off in the portal unless you enable `core.legacy_text_commands`.

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
