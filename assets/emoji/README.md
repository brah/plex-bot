# Emoji

The bot uploads these as its own emojis on first start, so they show in every server.

| | Name | Shows up on |
| --- | --- | --- |
| <img src="plex.png" width="32" alt=""> | `plex` | `/plex status`, before the server version |

`plex.svg` is the source, drawn by hand as code. After editing it, re-render the PNG (needs Edge or Chrome):

```bash
python scripts/render_mascots.py plex --src assets/emoji --out assets/emoji --size 128
```

A changed image only reaches Discord after you delete the old emoji under **Emojis** in the
[Developer Portal](https://discord.com/developers/applications) and restart the bot.
