# AGENTS.md

Discord bot (nextcord) that reads a Plex server through Tautulli's API. Public repo: github.com/brah/plex-bot.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing anything: it covers where things live, how the code
fits together, tests, and the rules for changes.

## Commands

```bash
.venv/Scripts/python -m pytest -q      # .venv/bin/python on Linux and macOS
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m plexbot        # needs config.json or PLEXBOT_* variables
```

CI tests Python 3.12 and 3.14; the Docker image runs 3.14.

## Notes for agents

- Directory READMEs (`assets/mascot/`, `assets/emoji/`, `docs/`) say how their files are made. Keep them
  current when you change those files.
- Edge's launcher exits before its headless child writes a screenshot. `scripts/render_mascots.py` waits
  for the file, so use it for SVG to PNG; for the `docs/brand/` pages, wait for the file yourself.
