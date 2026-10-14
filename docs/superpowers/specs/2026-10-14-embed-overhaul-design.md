# Embeds and responses overhaul: design

**Date:** 2026-10-14
**Status:** Approved in conversation; awaiting spec review
**Scope of this spec:** the shared presentation foundation and the first slice (`/plex status`, `/plex watchers`, `/plex help`). Every other cog is redesigned later, in the same pass as its slash-command migration, using this foundation.

## Problem

Every response looks the same: the same Plex-gold colour on every embed, the stock Plex logo as the thumbnail on the most-used screens, and dense field lists. `plex help` is a wall of text. Responses have no character, and nothing in a reply tells you at a glance what it is about or what state things are in.

## Decisions

| Question | Decision |
|---|---|
| What gives the bot character | **Content first.** Every response shows its subject (poster, art, avatar, current frame). The stock Plex logo is never used. |
| Responses with no natural subject | **Custom illustrations of a recurring mascot**, in named image slots. |
| Where illustrations come from | **Hybrid, slot-based.** Vector illustrations drawn in the repo (SVG source, PNG sent). Each slot also has an AI prompt in the same style, so any slot can be replaced by an AI-generated PNG with no code change. |
| Help layout | **Categories plus a dropdown**, private to the user. |
| Sequencing | **Foundation first, then per cog.** Build `ui/` and apply it to the commands already on slash. Each remaining cog is migrated and redesigned in one pass. |
| Code structure | **Shared builders and image helpers** (`ui/` package). A dedicated renderer exists only for shapes with more than one consumer or real complexity. |

## Visual language

### Colour has meaning

Colours are named roles in one palette (`ui/theme.py`), never literals in cogs.

| Role | Colour | Used for |
|---|---|---|
| `PLAYING` | green | streams playing; healthy status |
| `SUCCESS` | green (same value as `PLAYING`) | action confirmations |
| `WARNING` | amber | paused or buffering streams; Plex reporting an incident; input or permission problems |
| `ERROR` | red | failures |
| `NEUTRAL` | slate grey | empty states, informational |
| `MOVIE`, `TV`, `MUSIC` | Plex amber, blue, pink | media cards when no poster tint is available |
| `GOLD`, `SILVER`, `BRONZE` | metallic | top-watcher rankings |
| poster tint | the poster's dominant colour | single-media cards |

`ui.plex_embed_color` in config remains the neutral brand accent.

### Every embed shows its subject

Image priority:

1. **Media:** the poster as the thumbnail. Detail views also use the wide background art as the large image.
2. **People:** their avatar in the author line. Use the Discord avatar when the Plex user is mapped to a Discord member, since that's who people recognise in the server. Otherwise use the Plex avatar from Tautulli.
3. **No natural subject** (server status, empty states, errors, help): a mascot slot illustration.
4. **Never** the stock Plex logo. A missing or failed image means no image, not a placeholder.

Images are sent as attachments (`attachment://…`), the same way posters are today. This works without public hosting and when Plex is not reachable from the internet. Each attachment in a message gets a unique filename.

### Mascot slots

| Slot | Pose | Used on |
|---|---|---|
| `mascot-wave` | waving | help |
| `mascot-sleepy` | dozing | empty states |
| `mascot-server` | proud, with a server | `/plex status` |
| `mascot-unplugged` | tangled in a cable | a service is unreachable |
| `mascot-oops` | sheepish | unexpected errors |

The files live in `assets/mascot/`:
- `<slot>.png`: what the bot sends
- `<slot>.svg`: the vector source
- `PROMPTS.md`: one AI prompt per slot, all in the same style

The mascot's design is chosen from 2–3 rough concept sketches before the full set is drawn.

### Text style

- Times use Discord timestamps (`<t:…:R>`, `<t:…:t>`). They update live and show in each viewer's timezone.
- Footers carry one short context line.
- Emoji are used sparingly and consistently: at most one per section heading.
- Prefer tight description lines over walls of fields.

## The `ui/` package

```
ui/
  theme.py    palette roles, Discord limits, the small emoji set
  format.py   progress bars, Discord timestamps, durations, limit-aware truncation
  images.py   posters, art, current frame, poster strip, mascot slots, poster tint
  embeds.py   card / empty_state / error_state / success builders; Reply
  cards.py    media card, stream overview, stream detail renderers
assets/
  mascot/     <slot>.png, <slot>.svg, PROMPTS.md
```

- **`images.py`** is the only place that fetches or builds images.
  - Every helper returns an attachment (file plus URL), or `None` when the image isn't available. Callers then skip that image.
  - It wraps the existing `utilities.fetch_plex_image` rather than duplicating it.
  - Mascot PNG bytes are read once and cached.
  - The poster strip and the poster tint use Pillow. Pillow becomes a direct, pinned dependency in `requirements.txt`; it is currently only pulled in by matplotlib.
- **`embeds.py`** builds the standard frames: `card`, `empty_state`, `error_state` and `success`. **`Reply`** bundles a response's embeds, attachments and view. `await reply.send(interaction, ephemeral=...)` sends it correctly whether the interaction is a first response or a follow-up.
- **`cards.py`** renders the media card (later used by lookup, random, recently added and recommend) and the two stream cards for `/plex watchers`.
- **`format.py`** absorbs helpers currently spread across cogs (`_format_ms`, progress bars, truncation). It also enforces Discord's limits:
  - title: 256 characters
  - description: 4,096 characters
  - fields: 25
  - field value: 1,024 characters
  - total per embed: 6,000 characters
  - embeds per message: 10

Cogs keep their command logic and data fetching, and call `ui/` to render the result.

## Response states and privacy

| State | When | Presentation |
|---|---|---|
| Loading | any command that calls Tautulli, Plex, TMDB or qBittorrent | defer immediately ("PlexBot is thinking…") |
| Empty | the call succeeded but there is nothing to show | `NEUTRAL`, `mascot-sleepy`, what's empty and a next step |
| Input error | an unknown title, a bad value | `WARNING`, no mascot, how to fix it |
| Service down | Tautulli, Plex, TMDB or qBittorrent unreachable | `ERROR`, `mascot-unplugged`, names the service, "try again in a minute"; never a raw exception |
| No permission | the user isn't allowed to do this | `WARNING`, one line, says who can |
| Unexpected bug | an unhandled exception | `ERROR`, `mascot-oops`, "that's on us; it's been logged"; the traceback goes to `plexbot.log` |
| Success | an action completed | `SUCCESS` green, one line |

**Public:** watchers, status, lookups, random, recently added, recommend, stats, charts.

**Private:**
- help
- detail views opened from a public message
- everything under `/plexadmin`
- action confirmations
- permission messages
- **all errors**, including those on public commands

**Errors after a public defer:** the first follow-up after a defer inherits the defer's visibility. To keep the error private, `Reply` deletes the public "thinking…" placeholder and sends the error as a private follow-up. This must be verified live. If Discord doesn't allow it, the fallback is a compact public error line.

**Buttons on public messages:**
- Controls that change the shared message (paging in `recent` and `random`) remain limited to the user who ran the command. Anyone else gets a private hint to run the command themselves.
- Controls that only read or expand (Refresh, Stream details) work for anyone.
- Expired controls are removed rather than greyed out. The embed's "updated …" timestamp shows how old the data is.

## First slice

### `/plex status`

- **Colour shows health:**
  - `PLAYING` green when everything is healthy
  - `WARNING` amber when plex.tv reports an incident
  - `ERROR` red, using the service-down state, when Tautulli is unreachable
- **Data:**
  - server name, version and platform
  - Plex Pass
  - library totals (movies, shows, episodes, music)
  - active streams and bandwidth
  - an available Plex update, if there is one
- **Thumbnail:** `mascot-server`.

### `/plex watchers`

**Overview (public).** One embed lists **every** visible stream.

- Each stream takes two lines: a state icon, the viewer, the title; then a progress bar with time and a short quality and device note.
- Colour: `PLAYING` if any stream is playing, otherwise `WARNING`.
- **Large image:** a strip of the current posters, stitched with Pillow (at most 6, with "+N" beyond that). With a single stream, it is that stream's poster.
- Nobody watching shows the empty state with `mascot-sleepy`.
- Controls: **Refresh** (anyone) and a **Stream details…** dropdown (anyone). The *Manage streams* button is removed.

**Stream detail (private).** Opened from the dropdown.

- **Header:** the viewer's avatar and name in the author line; the full title (episode `S01E02 · title`, year) as the linked title.
- **Large image:** the **current frame**, taken from the session's `bif_thumb` when `indexes` is set, through Tautulli's image proxy. If it's unavailable, the background art; otherwise none. **Thumbnail:** the poster.
- **Progress:** bar, elapsed and total, and "ends at" as a Discord timestamp.
- **Stream:** Direct Play, Direct Stream or Transcode, with the transcode reason; video source → stream resolution; audio codec and channels; subtitles.
- **Player:** device, app and platform; local or remote; bandwidth.
- **Stop this stream** button, only when the viewer is a stream manager (admins plus `discord.stream_manager_ids`). It opens the existing message modal. After a successful stop, the private card is replaced by the success confirmation, and the public overview it was opened from is re-rendered.
- **Never shown, even to managers:** the viewer's IP address or location.

The `stop:` slash option with autocomplete is kept. It also covers ignored users, who are hidden from the overview.

### `/plex help`

- **Private overview:**
  - `mascot-wave` thumbnail and a one-line tagline
  - one line per category: emoji, summary, command count
  - footer: "Pick a category below · type /plex to browse everything"
- **Dropdown:** choosing a category edits the same private message to that category's page. Each command shows its full form with options (`/plex movie <title>`), a description and an example. An "Overview" entry returns to the overview.
- **Categories:**
  - Each cog declares a `help_category`: Watching, Library, Stats, Charts or Admin.
  - Admin (`/plexadmin`, user management) appears only to administrators.
- **Generated from command metadata:** slash command descriptions and options, or the first help line of commands not yet migrated. Unmigrated prefix commands appear in their `plex …` form until their cog migrates.
- `UtilityCommands`' prefix `help` is removed. `plex help` gets the existing "is now `/plex help`" hint.

### Shared states

- These three commands use the `ui/` states.
- The slash-command error handler (`errors.ErrorHandler.application_error_handler`) and `LoggedView` / `LoggedModal` switch to `error_state` cards.

## Testing

- **Unit tests for `ui/`:**
  - colour roles and poster tint
  - truncation at each Discord limit
  - progress bars and timestamps
  - image fallbacks (missing thumb, failed fetch, missing mascot file)
  - poster-strip layout (1, 2–6, and more than 6 posters)
- **Rendering tests:** stream overview, stream detail and status cards built from realistic Tautulli fixture payloads. These include:
  - no streams
  - a paused stream
  - a transcode
  - missing `bif_thumb`
- **Help tests:** category grouping, Admin hidden from non-admins, entries generated for both slash and prefix commands.
- **Dispatch tests:** the new dropdowns and buttons through nextcord's real dispatch path (`ConnectionState.parse_interaction_create`), not by calling callbacks directly.
- **Existing guard:** `tests/test_ui_classes.py` automatically covers the new views.

**Live checks on the real server** (these can't be tested offline):

1. The current frame appears for a stream from a library with preview thumbnails, and falls back to art for one without.
2. An error after a public defer arrives privately, and the placeholder disappears.
3. The cards render well on Discord desktop and mobile.

## Out of scope

- Redesigning cogs other than the three commands above. Each happens with its slash migration.
- Persistent (restart-surviving) buttons. This was considered and deferred; it can be revisited if restarts killing older messages' buttons becomes annoying.
