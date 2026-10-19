# CLAUDE.md

Discord bot (nextcord) that reads a Plex server through Tautulli's API. Public repo: github.com/brah/plex-bot.

@CONTRIBUTING.md

## Local commands (Windows)

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m plexbot        # needs config.json
```

Python 3.14 also works locally; CI covers 3.12 and 3.14.

## Notes for agents

- Directory READMEs (`assets/mascot/`, `docs/`) document how their files are made. Keep them current when you change those files.
- Edge's launcher exits before its headless child writes a screenshot; `scripts/render_mascots.render` waits for the file. Reuse it rather than calling Edge directly.
