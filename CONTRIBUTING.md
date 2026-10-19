# Contributing

Bug reports and pull requests are welcome. This page covers running the code, where things live, and
the rules that keep a public repo about a private Plex server safe to share.

## Setup

```bash
git clone https://github.com/brah/plex-bot.git && cd plex-bot
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest "ruff==0.17.0"
pytest -q        # needs neither Discord nor Tautulli
ruff check .
```

To run the bot itself you need a Discord token and Tautulli; see [Setup](README.md#setup). CI runs
ruff and pytest on Python 3.12 and 3.14.

## Where things live

The bot is the `plexbot` package; `python -m plexbot` runs it.

| Path | What's there |
| --- | --- |
| `plexbot/bot.py` | Startup. `setup_hook` creates the shared Tautulli client and media cache once, then loads every cog. nextcord never calls `setup_hook` itself; `PlexBot.start()` does. |
| `plexbot/slash_commands.py` | The `/plex` and `/plexadmin` command groups. `/plexadmin` is admin-only, through `admin_check`. |
| `plexbot/cogs/` | The commands, one module per area: server, media, stats, charts, recommendations, user management, help. |
| `plexbot/ui/` | Everything users see: colours, text formatting, images, charts, and the `Reply` builder. |
| `plexbot/tautulli_wrapper.py` | The Tautulli API client. |
| `plexbot/media_cache.py` | A local copy of the library for autocomplete, random picks and recommendations. |
| `plexbot/utilities.py` | Links between Plex users and Discord members (`map.json`), viewer names and avatars, log redaction. |
| `plexbot/config.py` | Every setting with its default. |
| `plexbot/errors.py` | Error handling for commands, buttons and menus. |
| `plexbot/media_details.py`, `plex_status.py`, `plex_users.py` | Smaller helpers; each file's docstring says what it's for. |
| `tests/` | The test suite; see [Tests](#tests). |
| `assets/mascot/` | Kernel, the mascot. See its [README](assets/mascot/README.md). |
| `docs/` | README screenshots and banner. See its [README](docs/README.md). |
| `scripts/` | Developer tools, not used by the bot. |
| `deploy/`, `Dockerfile`, `docker-compose.yml` | Ways to run it. See [Deployment](README.md#deployment). |

## How the code fits together

- **Commands** attach to the shared groups with `@plex.subcommand` or `@plexadmin.subcommand`. The
  groups live in the `SlashGroups` cog because nextcord drops a group registered outside a cog.
- **Replies**: a command builds a `ui.embeds.Reply` (embeds, attachments and a view) and calls
  `.send(interaction)`. Use `error_state`, `service_down`, `empty_state` and `success` for the
  standard frames, so errors and empty results look the same everywhere. Views subclass
  `errors.LoggedView`, which logs their exceptions.
- **Tautulli**: every call goes through `Tautulli.api_call`. Check results with
  `Tautulli.check_response` and `Tautulli.get_response_data`.
- **Settings**: read with `config.get(section, key)`. A new setting goes in `plexbot/config.py` with
  its default, and gets a `PLEXBOT_<SECTION>_<KEY>` environment variable for free, typed from the
  default.
- **Files**: state (`config.json`, `map.json`, `cache/`, `plexbot.log`) resolves against the working
  directory, and code and assets against `__file__`. In Docker the code sits read-only in `/app` and
  `/data` is the only writable path.
- **Intents**: the bot always needs Server Members. Message Content is only requested when
  `core.legacy_text_commands` is on, and only serves the redirect from old text commands like `plex status`.
  An `@PlexBot` mention works as a prefix either way.
- Open JSON files with `encoding="utf-8"`; the Windows default codepage fails on the media cache.

## Tests

`tests/fakes.py` is the harness: `FakeTautulli`, `Recorder` (records what a command sent instead of
calling Discord), `build_bot`, `build_media_bot`, `build_stats_bot` and `interaction(bot)`.

To call a slash command directly, use `Cog.cmd.callback(cog, interaction(bot), **kwargs)` and pass
every option, with `None` for unset ones. An omitted option arrives as a `SlashOption` object, not
its default.

## Rules

- Never commit `config.json`, `map.json` or `cache/`: they hold tokens, API keys and user data. Git
  ignores every `*.json` except `config.example.json`.
- Screenshots are for reference only and can fall behind the bot, so a change doesn't have to
  update them. Any image you add must never show real Plex or Discord user names.
- When a command changes, update the commands table in the README.
- Until the project has versioned releases, docs describe how things work now: no changelogs,
  upgrade steps or migration notes.
- Cosmetic changes keep every user-visible feature.
- README text is plain English for people running the bot, not developers.

## Tautulli quirks

Found against a live server:

- `get_metadata` on a show: `children_count` is the number of **episodes** (Plex `leafCount`), not
  seasons.
- `get_library_media_info` can list titles Plex has removed, and `get_metadata` on those returns HTTP
  400 ("Unable to retrieve metadata"). The media cache skips them, so it holds fewer items than the
  library totals.
- `get_home_stats` (`top_movies` and friends) still includes deleted titles, so a top title may not
  be lookup-able.
- Hours and weekdays use the Tautulli server's timezone (from `get_settings`).
