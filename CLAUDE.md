# CLAUDE.md

Discord bot (nextcord) that reads a Plex server through Tautulli's API. Public repo: github.com/brah/plex-bot.

## Commands

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt pytest "ruff==0.17.0"
.venv/Scripts/python -m pytest -q                 # all tests; no network
.venv/Scripts/python -m ruff check .              # CI lint (E9 + F only)
.venv/Scripts/python -m plexbot                   # run the bot (needs config.json)
```

CI (`.github/workflows/ci.yml`) runs ruff, a compileall smoke test of every file, and pytest on Python 3.12. Python 3.14 also works locally.

## Layout

- `plexbot.py`: `PlexBot.setup_hook` builds `bot.shared_resources` (`tautulli`, `media_cache`, `tmdb`) once, then loads every `cogs/*.py`. nextcord never calls `setup_hook` itself; `start()` does.
- `slash_commands.py`: `/plex` and `/plexadmin` roots live in the `SlashGroups` cog (a group registered outside a cog is dropped on READY). Cogs attach with `@plex.subcommand` / `@plexadmin.subcommand`. `/plexadmin` is admin-only via `admin_check`.
- `cogs/`: one module per area (server, media, stats, charts, recommendations, user management, help).
- `ui/`: presentation. Commands build a `ui.embeds.Reply` (embeds + `Attachment`s + view) and call `.send(interaction)`. Use `error_state`, `service_down`, `empty_state`, `success` for standard frames. Views subclass `errors.LoggedView`.
- Intents: Server Members always. Message Content only when `core.legacy_text_commands` is on; it serves nothing but the old prefix commands' redirect in `errors/`. Mentions always work as a prefix (`when_mentioned`, which avoids nextcord's warning when the intent is off); `plex ` only when on.
- `config/`: every setting with its default; `config.get(section, key)`. `PLEXBOT_<SECTION>_<KEY>` env vars override `config.json`, typed from the default (blank = unset). Required keys (`core.token`, `tautulli.ip`, `tautulli.apikey`) only need to exist, so an empty token still passes.
- Docker: code in `/app` (root-owned), working dir and only writable path `/data` (`map.json`, `cache/`, `plexbot.log`). State paths resolve against the working dir, code paths against `__file__`.
- `utilities.py`: `UserMappings` (Plex user to Discord member, stored in `map.json`), viewer names/avatars, log redaction.
- `tautulli_wrapper.py`: every Tautulli call goes through `Tautulli.api_call`; check results with `Tautulli.check_response` / `get_response_data`.

## Testing

- `tests/fakes.py` is the harness: `FakeTautulli`, `Recorder` (monkeypatches nextcord interaction methods and records what was sent), `build_bot` / `build_media_bot` / `build_stats_bot`, `interaction(bot)`.
- To call a slash command body directly use `Cog.cmd.callback(cog, interaction(bot), **kwargs)` and pass **every** option, `None` for unset ones. Omitted options arrive as `SlashOption` objects, not defaults.

## Rules

- `config.json`, `map.json` and `cache/` hold secrets or user data and are gitignored (`*.json` except `config.example.json`). Never commit them or print their secrets.
- README screenshots (`docs/screenshots/`) are static images and must never show real Plex or Discord user names.
- `docs/brand/`: the README banner (`banner.webp` from `banner.html`, Edge at `--window-size=1280,360 --force-device-scale-factor=2 --default-background-color=00000000 --virtual-time-budget=8000` for the web fonts, then Pillow to WebP) and the GitHub social preview (`social-preview.png` from `social-preview.html` at `1280,640`; uploaded by hand under Settings → General, no API).
- When a command changes, update the README commands table.
- Don't drop user-visible features in cosmetic changes. README prose is plain, user-facing English.

## Tautulli quirks (found against a live server)

- `get_metadata` on a show: `children_count` is the **episode** count (Plex `leafCount`), not seasons.
- `get_library_media_info` can list titles Plex has removed; `get_metadata` on those returns HTTP 400 ("Unable to retrieve metadata"). The media cache skips them, so it holds fewer items than the library totals.
- `get_home_stats` (`top_movies`, etc.) still includes deleted titles, so a top title may not be lookup-able.
- Hours and weekdays use the Tautulli server's timezone (`get_settings`).

## Windows notes

- `scripts/render_mascots.render` waits for the file: Edge's launcher exits before its headless child writes the screenshot.
- Open repo JSON with `encoding="utf-8"`; the Windows default codepage fails on the media cache.
