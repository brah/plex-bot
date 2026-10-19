# Kernel, the mascot

Kernel is a striped popcorn bucket: popcorn for movie night, amber for Plex. It sits in the corner of
some replies as a small thumbnail, so you can tell at a glance what kind of reply it is.

| | Slot | Shows up on |
| --- | --- | --- |
| <img src="mascot-wave.png" width="72" alt=""> | `mascot-wave` | `/plex help`, and the README banner |
| <img src="mascot-server.png" width="72" alt=""> | `mascot-server` | `/plex status` |
| <img src="mascot-downloading.png" width="72" alt=""> | `mascot-downloading` | `/plex downloading` |
| <img src="mascot-sleepy.png" width="72" alt=""> | `mascot-sleepy` | Nothing to show, like nobody watching |
| <img src="mascot-unplugged.png" width="72" alt=""> | `mascot-unplugged` | Tautulli or qBittorrent can't be reached |
| <img src="mascot-oops.png" width="72" alt=""> | `mascot-oops` | Something broke on the bot's side |

## How the art is made

Each slot has two files:

- `<slot>.svg` is the source: vector shapes written by hand as code (with an AI coding assistant),
  not output from an image generator.
- `<slot>.png` is what the bot sends, rendered from the SVG at 512×512 with a transparent
  background, because Discord can't display SVG.

After editing an SVG, re-render its PNG. This needs Edge or Chrome; set `CHROME_PATH` if the script
can't find one.

```bash
python scripts/render_mascots.py               # every slot
python scripts/render_mascots.py mascot-oops   # just one
```

[PROMPTS.md](PROMPTS.md) describes Kernel and each pose as a text-to-image prompt. Every prompt is
complete on its own, so you can paste one into an image generator and get art that matches the set.

## Using your own art

Save a square PNG with a transparent background over `<slot>.png`. No code changes needed. Keep it
at most 1024 px and under 250 KB, because it's uploaded with every reply that uses it; Discord
shows it at about 80 px, so keep the silhouette simple.

## Adding a slot

Add `<slot>.svg`, render `<slot>.png`, write a `## <slot>` prompt in PROMPTS.md, and list the slot in
`MASCOT_SLOTS` in [`plexbot/ui/images.py`](../../plexbot/ui/images.py). `tests/test_mascot_assets.py` checks that
every slot has all three files and that the PNG fits the limits above.
