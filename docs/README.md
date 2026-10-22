# docs

Images for the [README](../README.md) and the GitHub repo page. The bot doesn't use anything here,
and none of it goes into the Docker image.

## screenshots/

One WebP per command, shown in the README. They aren't screen captures: each one was drawn as a
vector image that replicates Discord's look, then exported. The exception is `tautulli-api-key.webp`,
a real screenshot of Tautulli's settings for the README's Install section, cropped to the page so it
shows no browser, address or extensions.

They're for reference only, not a spec. They can fall behind the bot, and a change doesn't have to
update them. If you do redraw one, keep its file name (the README links to it) and use made-up
names: never real Plex or Discord users.

## brand/

| File | Made from | Used for |
| --- | --- | --- |
| `banner.webp` | `banner.html` | The banner at the top of the README |
| `social-preview.png` | `social-preview.html` | The card GitHub shows when someone shares the repo link |

Both pages draw Kernel from [`assets/mascot/mascot-wave.svg`](../assets/mascot/README.md) and load
their fonts from Google Fonts. To re-render one, edit the HTML, then screenshot it with headless
Chrome or Edge from the repo root (Edge's command is `msedge`):

```bash
# Banner: transparent, at 2x, then converted to WebP
chrome --headless=new --hide-scrollbars --virtual-time-budget=8000 \
  --default-background-color=00000000 --window-size=1280,360 --force-device-scale-factor=2 \
  --screenshot="$PWD/banner.png" "file://$PWD/docs/brand/banner.html"
python -c "from PIL import Image; Image.open('banner.png').save('docs/brand/banner.webp', quality=90)"

# Social preview
chrome --headless=new --hide-scrollbars --virtual-time-budget=8000 --window-size=1280,640 \
  --screenshot="$PWD/docs/brand/social-preview.png" "file://$PWD/docs/brand/social-preview.html"
```

`--virtual-time-budget` gives the web fonts time to load; without it the text falls back to a
system font. On Windows, give the page as a full `file:///C:/...` URL, and wait a moment before
converting: Edge returns before it has written the file.

GitHub has no API for the social preview: upload it by hand under **Settings → General → Social
preview**.
