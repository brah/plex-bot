# Embeds and responses overhaul (first slice): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared `ui/` presentation layer (colour roles, image helpers, standard response states, `Reply`). Use it to redesign `/plex watchers`, `/plex status` and a new `/plex help`. Add a mascot illustration set for responses that have no natural image.

**Architecture:**
- **`ui/`** is a plain-Python package. It holds the colour palette, text formatting, image fetching and composition (with Pillow), embed builders and the `Reply` sender.
- **Cogs** keep their data fetching and command logic and call `ui/` to render.
- **Images** are sent as message attachments.
- **Mascot PNGs** live in `assets/mascot/`, are drawn as SVG, and are rendered by a dev-only script.

**Tech stack:**
- Python 3.12
- nextcord 3.2.0 (slash commands, views, modals)
- Pillow 12.3.0
- Tautulli API v2
- pytest; ruff (rules `E9`, `F`; line length 108)

**Spec:** `docs/superpowers/specs/2026-10-14-embed-overhaul-design.md`. Read it before starting. This plan implements it and does not repeat its reasoning.

## Global Constraints

- **Colours:** only from `ui.theme.Colour` or `ui.theme.brand_colour()`. No colour literals in cogs.
- **No stock logo:** the Plex logo (`config ui.plex_image`) is never used by the redesigned commands.
- **Images go through `ui.images`:** they are sent as attachments (`attachment://name`), each with a filename unique within its message. A missing or failed image means no image, never a placeholder.
- **Privacy:**
  - Errors, help, detail views, permission messages and confirmations are always private (ephemeral).
  - `/plex watchers` and `/plex status` are public.
  - A viewer's IP address or location is never shown anywhere.
- **Discord limits are enforced via `ui.format.truncate` and `ui.theme.Limits`:**
  - title: 256
  - description: 4,096
  - field value: 1,024
  - author name: 256
  - select menus: 25 options, labels up to 100
  - embeds per message: 10
- **Pillow is a direct dependency:** `pillow==12.3.0` in `requirements.txt`.
- **Code style:**
  - follow the surrounding code
  - line length 108
  - `ruff check .` must pass
  - **no View, Modal or Select subclass may define a method that replaces a nextcord one**, other than `callback`, `interaction_check`, `on_timeout` and `on_error` (enforced by `tests/test_ui_classes.py`)
- **Test commands:** run tests with `python -m pytest -q` from the repo root, in a Python 3.12 environment with `requirements.txt` and `pytest` installed. Lint with `ruff check .`, at ruff 0.17.0 as pinned in CI.
- **Commits:**
  - before each commit, `git var GIT_AUTHOR_IDENT` must show `Dylan <24949824+brah@users.noreply.github.com>`
  - every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - commit after each task; do not push until Task 11

## Review Focus

These are inputs the spec implies but no feature test would naturally hit. Each line names the task whose tests pin it.

1. **Many concurrent streams** (30+): the overview description stays within 4,096 characters and ends with "…and N more streams". The dropdown offers at most 25. The poster strip shows 6 posters plus a "+N" tile. *(Tasks 4 and 5)*
2. **Sessions with missing or odd fields:** live TV (`duration` 0, no thumbs), music tracks, numbers as strings, titles containing Markdown characters (`*NSYNC`, `_Live_`). Cards must render without crashing, and Markdown must be escaped. *(Task 4)*
3. **A slow or broken Tautulli image proxy:** an image fetch that hangs must give up after 8 s and the response is sent without that image. A fetch that returns non-image bytes yields no image. *(Task 2)*
4. **A stream that ends between the overview and a click:**
   - picking it from the dropdown shows a private "That stream has ended" and re-renders the overview
   - stopping it reports that it isn't running, without a traceback

   *(Task 5)*
5. **Help opened by a non-admin, or with no guild permissions** (for example, a user object without `guild_permissions`): the Admin category is hidden and doesn't crash. *(Task 7)*

---

## Not in this plan

The spec's **media card** (lookup, random, recently added and recommend) and its **poster-tint** colouring arrive with the Library cog's slash migration. This plan builds `ui.images.dominant_colour` for them, but nothing in this slice uses it yet.

## File structure

| Path | Responsibility |
|---|---|
| `ui/__init__.py` | Package marker (docstring only) |
| `ui/theme.py` | `Colour` roles, `brand_colour()`, `media_colour()`, `Limits`, `Emoji` |
| `ui/format.py` | `truncate`, `format_ms`, `progress_bar`, `timestamp`, `plural`, `join`, `to_int`, `is_truthy` |
| `ui/images.py` | `Attachment`, `mascot`, `plex_image`, `poster_key`, `current_frame`, `poster_strip`, `dominant_colour`, `MASCOT_SLOTS` |
| `ui/embeds.py` | `card`, `set_thumbnail`, `set_image`, `Reply`, `ErrorKind`, `error_state`, `service_down`, `unexpected_error`, `empty_state`, `success` |
| `ui/cards.py` | Stream renderers: `session_user`, `session_title`, `session_lines`, `stream_overview`, `stream_detail`, `plex_web_link` |
| `assets/mascot/` | `<slot>.svg`, `<slot>.png`, `PROMPTS.md` |
| `scripts/render_mascots.py` | Dev-only: render SVG slots to PNG with headless Edge or Chrome |
| `utilities.py` | `fetch_plex_image` gains a `fallback` parameter and a request timeout |
| `tautulli_wrapper.py` | adds `get_pms_update()` |
| `cogs/server_commands.py` | `/plex watchers` and `/plex status` rebuilt on `ui/`; old watchers helpers and panel removed |
| `cogs/utility_commands.py` | `/plex help` (categories plus dropdown), replacing prefix `help` |
| `cogs/*.py` | each cog gains a `help_category` class attribute |
| `errors/__init__.py` | slash error handler and `LoggedView`/`LoggedModal` use `ui` error cards |
| `tests/fakes.py` | shared test fakes: sample Tautulli sessions, `FakeTautulli`, `Recorder` for nextcord dispatch |
| `tests/test_ui_*.py`, `tests/test_status_card.py`, `tests/test_help.py`, `tests/test_watchers_dispatch.py`, `tests/test_mascot_assets.py` | tests per task |

---

### Task 1: Theme and text formatting

**Files:**
- Create: `ui/__init__.py`, `ui/theme.py`, `ui/format.py`, `tests/test_ui_format.py`
- Modify: `cogs/server_commands.py` (drop its private `_truncate` and `_format_ms`; import from `ui.format`), `tests/test_helpers.py` (import `format_ms` from `ui.format`)

**Interfaces:**
- Produces:
  - `ui.theme`: `Colour` (int constants `PLAYING, SUCCESS, WARNING, ERROR, NEUTRAL, MOVIE, TV, MUSIC, GOLD, SILVER, BRONZE`); `brand_colour() -> int`; `media_colour(media_type: str | None) -> int`; `Limits` (int constants); `Emoji` (str constants `PLAYING, PAUSED, BUFFERING, BULLET`)
  - `ui.format`: `truncate(text, limit) -> str`; `format_ms(ms) -> str`; `progress_bar(fraction: float | None, width=10) -> str`; `timestamp(moment: datetime | int | float, style="R") -> str`; `plural(count: int, singular: str, plural_form: str | None = None) -> str`; `join(parts, sep=" · ") -> str`; `to_int(value, default=0) -> int`; `is_truthy(value) -> bool`

- [ ] **Step 1: Write the failing tests** in `tests/test_ui_format.py`:

```python
"""ui.format: the text helpers every response uses."""

from datetime import datetime, timezone

from ui.format import format_ms, is_truthy, join, plural, progress_bar, timestamp, to_int, truncate
from ui.theme import Colour, Limits, media_colour


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_exact_limit_unchanged(self):
        assert truncate("x" * 10, 10) == "x" * 10

    def test_long_text_cut_with_ellipsis(self):
        result = truncate("x" * 20, 10)
        assert len(result) == 10 and result.endswith("…")

    def test_none_becomes_empty(self):
        assert truncate(None, 10) == ""

    def test_non_string_is_stringified(self):
        assert truncate(12345, 3) == "12…"


class TestFormatMs:
    def test_minutes(self):
        assert format_ms(754000) == "12:34"

    def test_hours(self):
        assert format_ms(5025000) == "1:23:45"

    def test_string_and_missing_values(self):
        assert format_ms("754000") == "12:34"
        assert format_ms(None) == "0:00"


class TestProgressBar:
    def test_half(self):
        assert progress_bar(0.5) == "█████░░░░░"

    def test_clamped(self):
        assert progress_bar(1.7) == "██████████"
        assert progress_bar(-1) == "░░░░░░░░░░"

    def test_none_is_empty(self):
        assert progress_bar(None) == ""


class TestTimestamp:
    def test_from_datetime(self):
        moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert timestamp(moment) == f"<t:{int(moment.timestamp())}:R>"

    def test_from_number_with_style(self):
        assert timestamp(1700000000.9, "t") == "<t:1700000000:t>"


class TestSmallHelpers:
    def test_plural(self):
        assert plural(1, "stream") == "1 stream"
        assert plural(2, "stream") == "2 streams"
        assert plural(1234, "movie") == "1,234 movies"
        assert plural(3, "match", "matches") == "3 matches"

    def test_join_skips_empty_parts(self):
        assert join(["a", None, "", "b"]) == "a · b"
        assert join(["a", "b"], " ") == "a b"

    def test_to_int(self):
        assert to_int("42") == 42
        assert to_int("") == 0
        assert to_int(None, default=-1) == -1

    def test_is_truthy(self):
        assert all(is_truthy(v) for v in (True, 1, "1", "true", "True"))
        assert not any(is_truthy(v) for v in (False, 0, "0", "", None, "false"))


class TestTheme:
    def test_media_colour(self):
        assert media_colour("movie") == Colour.MOVIE
        assert media_colour("episode") == Colour.TV
        assert media_colour("track") == Colour.MUSIC
        assert media_colour(None) == Colour.NEUTRAL

    def test_limits(self):
        assert Limits.DESCRIPTION == 4096 and Limits.SELECT_OPTIONS == 25
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_ui_format.py`
Expected: collection error `ModuleNotFoundError: No module named 'ui'`.

- [ ] **Step 3: Implement.** `ui/__init__.py`:

```python
"""Shared presentation layer: colours, text formatting, images and response builders."""
```

`ui/theme.py`:

```python
# ui/theme.py

"""Colour roles, Discord limits and the small emoji set shared by every response."""

from config import config


class Colour:
    """Colour roles. Cogs pick a role, never a literal value."""

    PLAYING = 0x3BA55C
    SUCCESS = PLAYING
    WARNING = 0xF0A93B
    ERROR = 0xED4245
    NEUTRAL = 0x5D6572
    MOVIE = 0xE5A00D
    TV = 0x4A90D9
    MUSIC = 0xEB459E
    GOLD = 0xF1C40F
    SILVER = 0xBFC5CC
    BRONZE = 0xCD7F32


_MEDIA_COLOURS = {
    "movie": Colour.MOVIE,
    "show": Colour.TV,
    "season": Colour.TV,
    "episode": Colour.TV,
    "artist": Colour.MUSIC,
    "album": Colour.MUSIC,
    "track": Colour.MUSIC,
}


def brand_colour() -> int:
    """The neutral brand accent (config ui.plex_embed_color), for frames with no state."""
    return config.get("ui", "plex_embed_color", Colour.MOVIE)


def media_colour(media_type) -> int:
    return _MEDIA_COLOURS.get(media_type or "", Colour.NEUTRAL)


class Limits:
    """Discord's hard limits; exceeding one makes Discord reject the whole message."""

    TITLE = 256
    DESCRIPTION = 4096
    FIELDS = 25
    FIELD_NAME = 256
    FIELD_VALUE = 1024
    FOOTER = 2048
    AUTHOR = 256
    EMBEDS_PER_MESSAGE = 10
    SELECT_OPTIONS = 25
    SELECT_LABEL = 100


class Emoji:
    PLAYING = "\u25B6"
    PAUSED = "\u23F8"
    BUFFERING = "\u23F3"
    BULLET = "\u2022"
```

`ui/format.py`:

```python
# ui/format.py

"""Text helpers shared by every response: truncation, durations, progress bars, timestamps."""

from datetime import datetime
from typing import Iterable, Optional, Union


def truncate(text, limit: int) -> str:
    """Cut text to at most `limit` characters, ending with an ellipsis when cut."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "\u2026"


def to_int(value, default: int = 0) -> int:
    """Tautulli returns many numbers as strings, or "" when unknown."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_truthy(value) -> bool:
    """Tautulli flags arrive as bools, ints or strings ("1", "true")."""
    return str(value).strip().lower() in ("1", "true")


def format_ms(ms) -> str:
    """Format milliseconds into H:MM:SS or M:SS."""
    total_seconds = to_int(ms) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def progress_bar(fraction: Optional[float], width: int = 10) -> str:
    if fraction is None:
        return ""
    fraction = min(max(float(fraction), 0.0), 1.0)
    filled = round(fraction * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def timestamp(moment: Union[datetime, int, float], style: str = "R") -> str:
    """A Discord timestamp: renders live (style R: "2 minutes ago") in each viewer's timezone."""
    seconds = int(moment.timestamp()) if isinstance(moment, datetime) else int(moment)
    return f"<t:{seconds}:{style}>"


def plural(count: int, singular: str, plural_form: Optional[str] = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {word}"


def join(parts: Iterable[Optional[str]], sep: str = " \u00b7 ") -> str:
    return sep.join(part for part in parts if part)
```

In `cogs/server_commands.py`:
- Delete the module-level `_truncate` and `_format_ms` functions.
- Add `from ui.format import format_ms, truncate`.
- Replace every `_truncate(` with `truncate(` and every `_format_ms(` with `format_ms(`, in that file only.

In `tests/test_helpers.py`, replace `from cogs.server_commands import _format_ms` with `from ui.format import format_ms as _format_ms`.

- [ ] **Step 4: Run the full suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all tests pass (the earlier 107 plus the new ones); `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add ui tests/test_ui_format.py cogs/server_commands.py tests/test_helpers.py
git commit -m "Add ui.theme and ui.format: colour roles, limits and text helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Images

**Files:**
- Create: `ui/images.py`, `tests/test_ui_images.py`
- Modify: `utilities.py` (`fetch_plex_image`: `fallback` parameter and timeout), `requirements.txt` (add `pillow==12.3.0`)

**Interfaces:**
- Consumes: `utilities.fetch_plex_image(tautulli_ip, thumb_key, width, height, use_https=..., api_key=..., fallback=...) -> BytesIO | None`
- Produces:
  - `Attachment(filename: str, data: bytes)` with `.url -> str` and `.to_file() -> nextcord.File`
  - `MASCOT_SLOTS: tuple[str, ...]`, `MASCOT_DIR: Path`
  - `mascot(slot: str) -> Attachment | None`
  - `async plex_image(tautulli, key, *, filename, width=300, height=450, fallback="poster") -> Attachment | None`
  - `poster_key(session: dict) -> str | None`
  - `async current_frame(tautulli, session, *, filename="frame.jpg") -> Attachment | None`
  - `poster_strip(posters: Sequence[bytes], *, extra=0, filename="posters.png", height=300, gap=8) -> Attachment | None`
  - `dominant_colour(data: bytes) -> int | None`
  - `IMAGE_TIMEOUT: float = 8.0`

- [ ] **Step 1: Write the failing tests** in `tests/test_ui_images.py`:

```python
"""ui.images: attachments, mascots, Plex images, poster strips and poster tint."""

import asyncio
import io

import pytest
from PIL import Image

import ui.images as images
from ui.images import Attachment, current_frame, dominant_colour, mascot, plex_image, poster_key, poster_strip


def png(colour=(255, 0, 0), size=(200, 300)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


class FakeTautulli:
    tautulli_ip = "host:8181"
    use_https = False
    api_key = "key"


class TestAttachment:
    def test_url_and_fresh_files(self):
        attachment = Attachment("poster-1.jpg", b"data")
        assert attachment.url == "attachment://poster-1.jpg"
        first, second = attachment.to_file(), attachment.to_file()
        assert first.filename == "poster-1.jpg" and first.fp.read() == b"data"
        assert second.fp.read() == b"data"  # each send needs its own unread stream


class TestMascot:
    def test_missing_slot_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "MASCOT_DIR", tmp_path)
        images._mascot_bytes.cache_clear()
        assert mascot("mascot-nope") is None

    def test_existing_slot(self, tmp_path, monkeypatch):
        (tmp_path / "mascot-wave.png").write_bytes(png())
        monkeypatch.setattr(images, "MASCOT_DIR", tmp_path)
        images._mascot_bytes.cache_clear()
        attachment = mascot("mascot-wave")
        assert attachment.filename == "mascot-wave.png" and attachment.data == png()
        images._mascot_bytes.cache_clear()


class TestPlexImage:
    def test_wraps_fetch(self, monkeypatch):
        calls = {}

        async def fake_fetch(ip, key, width, height, *, use_https, api_key, fallback):
            calls.update(ip=ip, key=key, width=width, height=height, fallback=fallback)
            return io.BytesIO(b"img")

        monkeypatch.setattr(images, "fetch_plex_image", fake_fetch)
        result = asyncio.run(plex_image(FakeTautulli(), "/library/1/thumb", filename="p.jpg"))
        assert result == Attachment("p.jpg", b"img")
        assert calls == dict(ip="host:8181", key="/library/1/thumb", width=300, height=450, fallback="poster")

    def test_missing_key_or_failed_fetch_is_none(self, monkeypatch):
        async def failed(*args, **kwargs):
            return None

        monkeypatch.setattr(images, "fetch_plex_image", failed)
        assert asyncio.run(plex_image(FakeTautulli(), "", filename="p.jpg")) is None
        assert asyncio.run(plex_image(FakeTautulli(), "/x", filename="p.jpg")) is None

    def test_slow_fetch_gives_up(self, monkeypatch):
        async def hangs(*args, **kwargs):
            await asyncio.sleep(10)

        monkeypatch.setattr(images, "fetch_plex_image", hangs)
        monkeypatch.setattr(images, "IMAGE_TIMEOUT", 0.05)
        assert asyncio.run(plex_image(FakeTautulli(), "/x", filename="p.jpg")) is None


class TestSessionImages:
    def test_poster_key_prefers_show_and_album_art(self):
        assert poster_key({"media_type": "episode", "grandparent_thumb": "/show", "thumb": "/ep"}) == "/show"
        assert poster_key({"media_type": "track", "parent_thumb": "/album", "thumb": "/t"}) == "/album"
        assert poster_key({"media_type": "movie", "thumb": "/movie"}) == "/movie"
        assert poster_key({"media_type": "movie"}) is None

    def test_current_frame_needs_preview_thumbnails(self, monkeypatch):
        seen = []

        async def fake_fetch(ip, key, width, height, *, use_https, api_key, fallback):
            seen.append((key, width, height, fallback))
            return io.BytesIO(b"frame")

        monkeypatch.setattr(images, "fetch_plex_image", fake_fetch)
        with_index = {"bif_thumb": "/library/parts/9/indexes/sd/60000", "indexes": 1}
        assert asyncio.run(current_frame(FakeTautulli(), with_index)).data == b"frame"
        assert seen == [("/library/parts/9/indexes/sd/60000", 640, 360, None)]
        assert asyncio.run(current_frame(FakeTautulli(), {"bif_thumb": "/x", "indexes": 0})) is None
        assert asyncio.run(current_frame(FakeTautulli(), {"indexes": 1})) is None


class TestPosterStrip:
    def test_single_poster(self):
        strip = poster_strip([png(size=(200, 300))])
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (200, 300)

    def test_several_posters_with_gaps(self):
        strip = poster_strip([png(), png((0, 255, 0)), png((0, 0, 255))], gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (3 * 200 + 2 * 8, 300)

    def test_extra_count_adds_a_tile(self):
        strip = poster_strip([png()] * 6, extra=4, gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (6 * 200 + 6 * 8 + 200, 300)

    def test_invalid_images_skipped_and_none_when_empty(self):
        assert poster_strip([b"not an image"]) is None
        assert poster_strip([]) is None
        assert poster_strip([b"junk", png()]).filename == "posters.png"


class TestDominantColour:
    def test_solid_colour(self):
        assert dominant_colour(png((255, 0, 0))) == 0xFF0000

    def test_prefers_a_vivid_colour_over_black(self):
        im = Image.new("RGB", (100, 100), (0, 0, 0))
        im.paste((200, 30, 30), (0, 0, 30, 100))
        out = io.BytesIO()
        im.save(out, format="PNG")
        assert (dominant_colour(out.getvalue()) >> 16) & 0xFF > 150

    def test_invalid_bytes(self):
        assert dominant_colour(b"nope") is None


@pytest.mark.parametrize("slot", images.MASCOT_SLOTS)
def test_mascot_slots_are_named_consistently(slot):
    assert slot.startswith("mascot-")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_ui_images.py`
Expected: `ModuleNotFoundError: No module named 'ui.images'`.

- [ ] **Step 3: Update `utilities.fetch_plex_image`.** Add a `fallback` parameter: sending a fallback makes Tautulli return a generic poster instead of failing, which is wrong for current frames. Also add a request timeout. Replace its signature and its `params`/session block with:

```python
async def fetch_plex_image(
    tautulli_ip: str, thumb_key: str, width: int = 300, height: int = 450,
    use_https: bool = False, api_key: str = "", fallback: Optional[str] = "poster"
) -> Optional[BytesIO]:
```

```python
        params = {
            "cmd": "pms_image_proxy",
            "img": thumb_key.strip(),
            "width": width,
            "height": height,
        }
        # Tautulli substitutes a generic image when the real one is missing; callers that
        # want "no image" instead (e.g. a stream's current frame) pass fallback=None.
        if fallback:
            params["fallback"] = fallback
        if api_key:
            params["apikey"] = api_key

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
```

Keep the rest of the function as it is. Add `fallback:` to its docstring's Args list: `fallback: Image Tautulli substitutes when the real one is missing (None to fail instead)`.

- [ ] **Step 4: Add `pillow==12.3.0`** to `requirements.txt` under `# Data & visualization`, after `seaborn==0.13.2`, with the comment line `# Image composition (poster strips, poster tint); also a matplotlib dependency.` above it.

- [ ] **Step 5: Implement `ui/images.py`:**

```python
# ui/images.py

"""Every image the bot sends: posters, art, a stream's current frame, poster strips, mascots.

Each helper returns an Attachment (sent as a file, referenced as attachment://name) or
None when the image isn't available; callers simply leave that image out.
"""

import asyncio
import io
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

import nextcord
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from ui.format import is_truthy
from utilities import fetch_plex_image

logger = logging.getLogger("plexbot.ui.images")

MASCOT_DIR = Path(__file__).resolve().parent.parent / "assets" / "mascot"
MASCOT_SLOTS = ("mascot-wave", "mascot-sleepy", "mascot-server", "mascot-unplugged", "mascot-oops")
# A hung image proxy must not hold up a reply; the response goes out without the image.
IMAGE_TIMEOUT = 8.0


@dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes

    @property
    def url(self) -> str:
        return f"attachment://{self.filename}"

    def to_file(self) -> nextcord.File:
        """A fresh File per send: nextcord consumes the stream when uploading."""
        return nextcord.File(io.BytesIO(self.data), filename=self.filename)


@lru_cache(maxsize=None)
def _mascot_bytes(slot: str) -> Optional[bytes]:
    path = MASCOT_DIR / f"{slot}.png"
    try:
        return path.read_bytes()
    except OSError:
        logger.warning(f"Mascot image missing: {path}")
        return None


def mascot(slot: str) -> Optional[Attachment]:
    data = _mascot_bytes(slot)
    return Attachment(f"{slot}.png", data) if data else None


async def plex_image(
    tautulli, key: Optional[str], *, filename: str, width: int = 300, height: int = 450,
    fallback: Optional[str] = "poster",
) -> Optional[Attachment]:
    """Fetch a Plex image (poster, art, preview frame) through Tautulli's image proxy."""
    if not key:
        return None
    try:
        buffer = await asyncio.wait_for(
            fetch_plex_image(
                tautulli.tautulli_ip, key, width, height,
                use_https=tautulli.use_https, api_key=tautulli.api_key, fallback=fallback,
            ),
            timeout=IMAGE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Timed out fetching Plex image {key}")
        return None
    return Attachment(filename, buffer.getvalue()) if buffer else None


def poster_key(session: dict) -> Optional[str]:
    """The poster that represents a session: the show for episodes, the album for tracks."""
    media_type = session.get("media_type")
    if media_type == "episode":
        return session.get("grandparent_thumb") or session.get("thumb") or None
    if media_type == "track":
        return session.get("parent_thumb") or session.get("thumb") or None
    return session.get("thumb") or None


async def current_frame(tautulli, session: dict, *, filename: str = "frame.jpg") -> Optional[Attachment]:
    """The frame at the viewer's position, from Plex video preview thumbnails, if generated."""
    key = session.get("bif_thumb")
    if not key or not is_truthy(session.get("indexes")):
        return None
    return await plex_image(tautulli, key, filename=filename, width=640, height=360, fallback=None)


def poster_strip(
    posters: Sequence[bytes], *, extra: int = 0, filename: str = "posters.png", height: int = 300, gap: int = 8,
) -> Optional[Attachment]:
    """Posters side by side at a common height, plus a "+N" tile when more were left out."""
    tiles = []
    for data in posters:
        try:
            with Image.open(io.BytesIO(data)) as image:
                rgba = image.convert("RGBA")
        except (UnidentifiedImageError, OSError, ValueError):
            continue
        width = max(1, round(rgba.width * height / rgba.height))
        tiles.append(rgba.resize((width, height), Image.Resampling.LANCZOS))
    if not tiles:
        return None

    extra_width = round(height * 2 / 3) if extra else 0
    total_width = sum(tile.width for tile in tiles) + gap * (len(tiles) - 1)
    if extra:
        total_width += gap + extra_width
    canvas = Image.new("RGBA", (total_width, height), (0, 0, 0, 0))
    x = 0
    for tile in tiles:
        canvas.paste(tile, (x, 0))
        x += tile.width + gap
    if extra:
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((x, 0, x + extra_width - 1, height - 1), radius=16, fill=(32, 34, 37, 255))
        font = ImageFont.load_default(size=height // 5)
        draw.text((x + extra_width / 2, height / 2), f"+{extra}", font=font, fill=(255, 255, 255, 255), anchor="mm")

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return Attachment(filename, out.getvalue())


def dominant_colour(data: bytes) -> Optional[int]:
    """The most common vivid colour in an image (0xRRGGBB), for tinting a media card."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            small = image.convert("RGB").resize((64, 64))
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    quantized = small.quantize(colors=6)
    palette = quantized.getpalette()
    counts = sorted(quantized.getcolors(), reverse=True)

    def rgb(index: int):
        return tuple(palette[index * 3 : index * 3 + 3])

    chosen = rgb(counts[0][1])
    for _count, index in counts:
        r, g, b = rgb(index)
        high, low = max(r, g, b), min(r, g, b)
        saturation = (high - low) / high if high else 0
        if saturation >= 0.25 and 30 <= (high + low) / 2 <= 230:
            chosen = (r, g, b)
            break
    r, g, b = chosen
    return (r << 16) | (g << 8) | b
```

- [ ] **Step 6: Install Pillow, then run the tests and lint**

Run: `python -m pip install -r requirements.txt && python -m pytest -q && ruff check .`
Expected: all pass. `TestMascot` uses `tmp_path`, so it doesn't depend on the real assets, which arrive in Task 10.

- [ ] **Step 7: Commit**

```bash
git add ui/images.py tests/test_ui_images.py utilities.py requirements.txt
git commit -m "Add ui.images: attachments, mascots, Plex images, poster strips, poster tint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Standard response states and `Reply`

**Files:**
- Create: `ui/embeds.py`, `tests/test_ui_embeds.py`

**Interfaces:**
- Consumes: `ui.theme.Colour`, `ui.theme.Limits`, `ui.format.truncate`, `ui.images.Attachment`, `ui.images.mascot`
- Produces:
  - `card(*, title=None, description=None, colour=Colour.NEUTRAL, url=None) -> nextcord.Embed`
  - `set_thumbnail(embed, attachment) -> nextcord.Embed`, `set_image(embed, attachment) -> nextcord.Embed` (no-ops when `attachment` is None)
  - `Reply(embeds: list[Embed], attachments: list[Attachment] = [], view: View | None = None, content: str | None = None)`, with:
    - `async send(interaction, *, ephemeral=False, replace_placeholder=False) -> message | None`
    - `edit_payload() -> dict`
    - `async edit(interaction)`
    - `async edit_message(message)`
  - `ErrorKind` enum: `INPUT`, `SERVICE_DOWN`, `PERMISSION`, `BUG`
  - `error_state(kind, message, *, hint=None) -> Reply`, `service_down(service: str) -> Reply`, `unexpected_error(path: str | None = None) -> Reply`
  - `empty_state(message, *, hint=None, title=None) -> Reply`, `success(message) -> Reply`

- [ ] **Step 1: Write the failing tests** in `tests/test_ui_embeds.py`:

```python
"""ui.embeds: standard frames and Reply's send/edit behaviour."""

import asyncio

import nextcord

import ui.embeds as embeds
from ui.embeds import ErrorKind, Reply, card, empty_state, error_state, service_down, success, unexpected_error
from ui.images import Attachment
from ui.theme import Colour, Limits

ART = Attachment("mascot-oops.png", b"png")


class Response:
    def __init__(self, done=False):
        self.done = done
        self.calls = []

    def is_done(self):
        return self.done

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))


class Followup:
    def __init__(self):
        self.calls = []

    async def send(self, **kwargs):
        self.calls.append(("followup", kwargs))
        return "message"


class Interaction:
    def __init__(self, done=False, delete_fails=False):
        self.response = Response(done)
        self.followup = Followup()
        self.deleted = False
        self.delete_fails = delete_fails
        self.edits = []

    async def delete_original_message(self):
        if self.delete_fails:
            raise nextcord.HTTPException(type("R", (), {"status": 400, "reason": "Bad"})(), "nope")
        self.deleted = True

    async def edit_original_message(self, **kwargs):
        self.edits.append(kwargs)


class TestCard:
    def test_truncates_to_discord_limits(self):
        embed = card(title="t" * 400, description="d" * 5000, colour=Colour.ERROR)
        assert len(embed.title) == Limits.TITLE and len(embed.description) == Limits.DESCRIPTION
        assert embed.colour.value == Colour.ERROR

    def test_optional_parts(self):
        embed = card()
        assert embed.title is None and embed.description is None


class TestStates:
    def test_service_down_has_unplugged_mascot(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
        reply = service_down("Tautulli")
        embed = reply.embeds[0]
        assert "Tautulli" in embed.description and embed.colour.value == Colour.ERROR
        assert embed.thumbnail.url == "attachment://mascot-unplugged.png"
        assert [a.filename for a in reply.attachments] == ["mascot-unplugged.png"]

    def test_input_error_has_no_mascot(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: ART)
        reply = error_state(ErrorKind.INPUT, "No movie matching 'xyz'.", hint="Try fewer words.")
        assert reply.attachments == [] and reply.embeds[0].colour.value == Colour.WARNING
        assert "-# Try fewer words." in reply.embeds[0].description

    def test_missing_mascot_file_just_means_no_image(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: None)
        reply = unexpected_error("/plex status")
        assert reply.attachments == [] and not reply.embeds[0].thumbnail.url
        assert "/plex status" in reply.embeds[0].description

    def test_empty_and_success(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
        empty = empty_state("Nobody's watching right now.", hint="Hit Refresh to check again.")
        assert empty.embeds[0].colour.value == Colour.NEUTRAL
        assert empty.attachments[0].filename == "mascot-sleepy.png"
        done = success("Stopped Bob's stream.")
        assert done.embeds[0].colour.value == Colour.SUCCESS and done.attachments == []


class TestReplySend:
    def test_first_response(self):
        interaction = Interaction(done=False)
        asyncio.run(Reply([card(description="hi")], [ART]).send(interaction, ephemeral=True))
        kind, kwargs = interaction.response.calls[0]
        assert kind == "send_message" and kwargs["ephemeral"] is True
        assert [f.filename for f in kwargs["files"]] == ["mascot-oops.png"]
        assert "view" not in kwargs

    def test_followup_after_defer(self):
        interaction = Interaction(done=True)
        result = asyncio.run(Reply([card(description="hi")]).send(interaction))
        kind, kwargs = interaction.followup.calls[0]
        assert result == "message" and kwargs["ephemeral"] is False and kwargs["wait"] is True
        assert "files" not in kwargs

    def test_private_error_replaces_public_placeholder(self):
        interaction = Interaction(done=True)
        asyncio.run(Reply([card()]).send(interaction, ephemeral=True, replace_placeholder=True))
        assert interaction.deleted is True
        assert interaction.followup.calls[0][1]["ephemeral"] is True

    def test_placeholder_delete_failure_falls_back_to_public(self):
        interaction = Interaction(done=True, delete_fails=True)
        asyncio.run(Reply([card()]).send(interaction, ephemeral=True, replace_placeholder=True))
        assert interaction.followup.calls[0][1]["ephemeral"] is False


class TestReplyEdit:
    def test_edit_replaces_attachments_and_view(self):
        interaction = Interaction(done=True)
        asyncio.run(Reply([card()], [ART]).edit(interaction))
        kwargs = interaction.edits[0]
        assert kwargs["attachments"] == [] and kwargs["view"] is None
        assert [f.filename for f in kwargs["files"]] == ["mascot-oops.png"]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_ui_embeds.py`
Expected: `ModuleNotFoundError: No module named 'ui.embeds'`.

- [ ] **Step 3: Implement `ui/embeds.py`:**

```python
# ui/embeds.py

"""Standard response frames (card, empty, error, success) and Reply, which sends them."""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import nextcord

from ui.format import truncate
from ui.images import Attachment, mascot
from ui.theme import Colour, Limits

logger = logging.getLogger("plexbot.ui.embeds")


def card(*, title=None, description=None, colour: int = Colour.NEUTRAL, url=None) -> nextcord.Embed:
    """The base embed: every response frame starts here so limits are always respected."""
    return nextcord.Embed(
        title=truncate(title, Limits.TITLE) if title else None,
        description=truncate(description, Limits.DESCRIPTION) if description else None,
        colour=colour,
        url=url,
    )


def set_thumbnail(embed: nextcord.Embed, attachment: Optional[Attachment]) -> nextcord.Embed:
    if attachment:
        embed.set_thumbnail(url=attachment.url)
    return embed


def set_image(embed: nextcord.Embed, attachment: Optional[Attachment]) -> nextcord.Embed:
    if attachment:
        embed.set_image(url=attachment.url)
    return embed


@dataclass
class Reply:
    """A response's embeds, attachments and view, sent the same way from any interaction."""

    embeds: List[nextcord.Embed]
    attachments: List[Attachment] = field(default_factory=list)
    view: Optional[nextcord.ui.View] = None
    content: Optional[str] = None

    def _send_payload(self) -> dict:
        payload = {"embeds": self.embeds}
        if self.attachments:
            payload["files"] = [attachment.to_file() for attachment in self.attachments]
        if self.content is not None:
            payload["content"] = self.content
        if self.view is not None:
            payload["view"] = self.view
        return payload

    def edit_payload(self) -> dict:
        """Keyword arguments for editing a message into this reply (old attachments dropped)."""
        payload = {"content": self.content, "embeds": self.embeds, "attachments": [], "view": self.view}
        if self.attachments:
            payload["files"] = [attachment.to_file() for attachment in self.attachments]
        return payload

    async def send(self, interaction: nextcord.Interaction, *, ephemeral: bool = False,
                   replace_placeholder: bool = False):
        """Send as the first response, or as a follow-up after a defer.

        replace_placeholder: the command deferred publicly ("thinking…"), but this reply
        must be private. Discord makes the first follow-up inherit the placeholder's
        visibility, so delete the placeholder first.
        """
        if not interaction.response.is_done():
            await interaction.response.send_message(ephemeral=ephemeral, **self._send_payload())
            return None
        if ephemeral and replace_placeholder:
            try:
                await interaction.delete_original_message()
            except nextcord.HTTPException as e:
                logger.warning(f"Could not remove the public placeholder, replying publicly: {e}")
                ephemeral = False
        return await interaction.followup.send(ephemeral=ephemeral, wait=True, **self._send_payload())

    async def edit(self, interaction: nextcord.Interaction) -> None:
        """Edit the interaction's own message (after a defer) into this reply."""
        await interaction.edit_original_message(**self.edit_payload())

    async def edit_message(self, message) -> None:
        await message.edit(**self.edit_payload())


class ErrorKind(Enum):
    INPUT = "input"
    SERVICE_DOWN = "service_down"
    PERMISSION = "permission"
    BUG = "bug"


_ERROR_STYLES = {
    ErrorKind.INPUT: (Colour.WARNING, None),
    ErrorKind.SERVICE_DOWN: (Colour.ERROR, "mascot-unplugged"),
    ErrorKind.PERMISSION: (Colour.WARNING, None),
    ErrorKind.BUG: (Colour.ERROR, "mascot-oops"),
}


def _with_hint(message: str, hint: Optional[str]) -> str:
    # "-# " is Discord's small grey subtext line.
    return f"{message}\n-# {hint}" if hint else message


def _framed(embed: nextcord.Embed, slot: Optional[str]) -> Reply:
    art = mascot(slot) if slot else None
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])


def error_state(kind: ErrorKind, message: str, *, hint: Optional[str] = None) -> Reply:
    colour, slot = _ERROR_STYLES[kind]
    return _framed(card(description=_with_hint(message, hint), colour=colour), slot)


def service_down(service: str) -> Reply:
    return error_state(ErrorKind.SERVICE_DOWN, f"Couldn't reach **{service}**.", hint="Try again in a minute.")


def unexpected_error(path: Optional[str] = None) -> Reply:
    where = f" running `{path}`" if path else ""
    return error_state(ErrorKind.BUG, f"Something went wrong{where}.", hint="That's on us, and it's been logged.")


def empty_state(message: str, *, hint: Optional[str] = None, title: Optional[str] = None) -> Reply:
    return _framed(card(title=title, description=_with_hint(message, hint), colour=Colour.NEUTRAL), "mascot-sleepy")


def success(message: str) -> Reply:
    return Reply([card(description=f"\u2705 {message}", colour=Colour.SUCCESS)])
```

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ui/embeds.py tests/test_ui_embeds.py
git commit -m "Add ui.embeds: standard response states and Reply

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Stream cards

**Files:**
- Create: `ui/cards.py`, `tests/fakes.py` (sample sessions only in this task), `tests/test_ui_cards.py`

**Interfaces:**
- Consumes: `ui.embeds.card`, the `ui.format` helpers, `ui.images.Attachment`, `ui.theme`
- Produces:
  - `session_user(session) -> str`, `session_title(session) -> str`, `session_lines(session) -> str`
  - `stream_overview(sessions, *, bandwidth_kbps: int, strip: Attachment | None, updated: datetime) -> nextcord.Embed`
  - `stream_detail(session, *, avatar_url: str | None, link: str | None, frame: Attachment | None, poster: Attachment | None, now: datetime) -> nextcord.Embed`
  - `plex_web_link(server_id: str | None, rating_key) -> str | None`
  - `tests/fakes.py`: `MOVIE_PLAYING`, `EPISODE_PAUSED`, `TRACK_PLAYING`, `LIVE_TV`, `MARKDOWN_TITLE` (session dicts) and `many_sessions(n) -> list[dict]`

- [ ] **Step 1: Create `tests/fakes.py`** with realistic sessions. Field names follow Tautulli's `get_activity` output.

```python
"""Shared test fakes: Tautulli session payloads (and, from Task 5, dispatch helpers)."""

MOVIE_PLAYING = {
    "session_id": "abc", "session_key": "1", "media_type": "movie", "state": "playing",
    "friendly_name": "Alice", "username": "alice", "user_thumb": "https://plex.tv/users/1/avatar",
    "title": "The Matrix", "full_title": "The Matrix", "year": "1999", "rating_key": "101",
    "thumb": "/library/metadata/101/thumb/1", "art": "/library/metadata/101/art/1",
    "bif_thumb": "/library/parts/55/indexes/sd/600000", "indexes": 1,
    "view_offset": "600000", "duration": "8160000",
    "transcode_decision": "direct play", "video_decision": "direct play", "audio_decision": "direct play",
    "video_full_resolution": "1080p", "stream_video_full_resolution": "1080p", "video_codec": "h264",
    "audio_codec": "eac3", "audio_channel_layout": "5.1(side)",
    "player": "Living Room", "product": "Plex for Roku", "platform": "Roku",
    "location": "lan", "bandwidth": "12000",
}

EPISODE_PAUSED = {
    "session_id": "def", "session_key": "2", "media_type": "episode", "state": "paused",
    "friendly_name": "Bob", "username": "bob", "user_thumb": "",
    "title": "Hello, Ms. Cobel", "grandparent_title": "Severance", "parent_media_index": "1",
    "media_index": "2", "year": "2022", "rating_key": "202",
    "thumb": "/library/metadata/202/thumb/1", "grandparent_thumb": "/library/metadata/200/thumb/1",
    "art": "/library/metadata/200/art/1", "bif_thumb": "", "indexes": 0,
    "view_offset": "100000", "duration": "3000000",
    "transcode_decision": "transcode", "video_decision": "transcode", "audio_decision": "copy",
    "video_full_resolution": "4k", "stream_video_full_resolution": "1080p", "video_codec": "hevc",
    "audio_codec": "truehd", "audio_channel_layout": "7.1",
    "subtitle_codec": "srt", "subtitle_language": "English", "subtitle_decision": "burn",
    "player": "Bob's iPhone", "product": "Plex for iOS", "platform": "iOS",
    "location": "wan", "bandwidth": "8000",
}

TRACK_PLAYING = {
    "session_id": "ghi", "media_type": "track", "state": "playing", "friendly_name": "Cara",
    "username": "cara", "title": "Bye Bye Bye", "grandparent_title": "*NSYNC",
    "parent_thumb": "/library/metadata/300/thumb/1", "thumb": "/library/metadata/301/thumb/1",
    "view_offset": "60000", "duration": "200000", "transcode_decision": "direct play",
    "audio_codec": "flac", "audio_channel_layout": "stereo", "player": "Desktop", "location": "lan",
}

LIVE_TV = {
    "session_id": "jkl", "media_type": "clip", "state": "playing", "username": "dan",
    "title": "News at Ten", "view_offset": "0", "duration": "0", "thumb": "",
    "transcode_decision": "", "location": "wan",
}

MARKDOWN_TITLE = dict(MOVIE_PLAYING, session_id="mno", friendly_name="_eve_", title="**Bold** _Live_")


def many_sessions(n: int) -> list:
    return [
        dict(MOVIE_PLAYING, session_id=f"s{i}", friendly_name=f"User {i}", username=f"user{i}", title=f"Film {i}")
        for i in range(n)
    ]
```

- [ ] **Step 2: Write the failing tests** in `tests/test_ui_cards.py`:

```python
"""ui.cards: /plex watchers overview and detail cards from realistic Tautulli sessions."""

from datetime import datetime, timezone

from fakes import EPISODE_PAUSED, LIVE_TV, MARKDOWN_TITLE, MOVIE_PLAYING, TRACK_PLAYING, many_sessions
from ui.cards import plex_web_link, session_lines, session_title, session_user, stream_detail, stream_overview
from ui.images import Attachment
from ui.theme import Colour, Limits

NOW = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
STRIP = Attachment("posters.png", b"png")


class TestTitles:
    def test_movie_episode_track_live(self):
        assert session_title(MOVIE_PLAYING) == "The Matrix (1999)"
        assert session_title(EPISODE_PAUSED) == "Severance · S01E02 · Hello, Ms. Cobel"
        assert session_title(TRACK_PLAYING) == "*NSYNC — Bye Bye Bye"
        assert session_title(LIVE_TV) == "News at Ten"

    def test_user_fallbacks(self):
        assert session_user(MOVIE_PLAYING) == "Alice"
        assert session_user(LIVE_TV) == "dan"
        assert session_user({}) == "Unknown"


class TestSessionLines:
    def test_two_lines_with_progress_quality_device(self):
        first, second = session_lines(MOVIE_PLAYING).split("\n")
        assert first.startswith("▶") and "**Alice**" in first and "The Matrix (1999)" in first
        assert "10:00 / 2:16:00" in second
        assert "1080p Direct Play" in second and "Living Room" in second

    def test_markdown_is_escaped(self):
        line = session_lines(MARKDOWN_TITLE)
        assert "\\_eve\\_" in line and "\\*\\*Bold\\*\\*" in line

    def test_live_tv_without_duration_does_not_crash(self):
        assert "News at Ten" in session_lines(LIVE_TV)


class TestOverview:
    def test_header_colour_and_strip(self):
        embed = stream_overview([MOVIE_PLAYING, EPISODE_PAUSED], bandwidth_kbps=20000, strip=STRIP, updated=NOW)
        assert embed.description.startswith("**2 streams** · 20.0 Mbps · updated <t:")
        assert embed.colour.value == Colour.PLAYING
        assert embed.image.url == "attachment://posters.png"

    def test_all_paused_is_amber(self):
        embed = stream_overview([EPISODE_PAUSED], bandwidth_kbps=0, strip=None, updated=NOW)
        assert embed.colour.value == Colour.WARNING and not embed.image.url

    def test_many_streams_stay_within_limits(self):
        embed = stream_overview(many_sessions(200), bandwidth_kbps=1, strip=None, updated=NOW)
        assert len(embed.description) <= Limits.DESCRIPTION
        assert "more streams" in embed.description.splitlines()[-1]


class TestDetail:
    def test_movie_detail(self):
        frame, poster = Attachment("frame.jpg", b"f"), Attachment("poster.jpg", b"p")
        embed = stream_detail(MOVIE_PLAYING, avatar_url="https://a/1.png", link="https://app.plex.tv/x",
                              frame=frame, poster=poster, now=NOW)
        assert embed.title == "The Matrix (1999)" and embed.url == "https://app.plex.tv/x"
        assert embed.author.name == "Alice" and embed.author.icon_url == "https://a/1.png"
        assert embed.image.url == "attachment://frame.jpg" and embed.thumbnail.url == "attachment://poster.jpg"
        fields = {field.name: field.value for field in embed.fields}
        assert "Ends <t:" in fields["Progress"]
        assert "**Video** 1080p H264 · Direct Play" in fields["Stream"]
        assert "**Audio** EAC3 5.1(side) · Direct Play" in fields["Stream"]
        assert "Local" in fields["Player"] and "12.0 Mbps" in fields["Player"]

    def test_transcoded_paused_episode(self):
        embed = stream_detail(EPISODE_PAUSED, avatar_url=None, link=None, frame=None, poster=None, now=NOW)
        fields = {field.name: field.value for field in embed.fields}
        assert "Paused" in fields["Progress"] and "Ends" not in fields["Progress"]
        assert "4k → 1080p HEVC · Transcode" in fields["Stream"]
        assert "**Subtitles** English (SRT) · Burned in" in fields["Stream"]
        assert "Remote" in fields["Player"]
        assert embed.colour.value == Colour.WARNING and embed.author.icon_url is None

    def test_never_shows_ip_or_location(self):
        session = dict(MOVIE_PLAYING, ip_address="203.0.113.7", ip_address_public="203.0.113.7",
                       location="wan", city="Springfield")
        embed = stream_detail(session, avatar_url=None, link=None, frame=None, poster=None, now=NOW)
        text = str(embed.to_dict())
        assert "203.0.113.7" not in text and "Springfield" not in text

    def test_sparse_session_does_not_crash(self):
        stream_detail(LIVE_TV, avatar_url=None, link=None, frame=None, poster=None, now=NOW)


class TestLink:
    def test_plex_web_link(self):
        assert plex_web_link("abc123", "101") == (
            "https://app.plex.tv/desktop/#!/server/abc123/details?key=%2Flibrary%2Fmetadata%2F101"
        )
        assert plex_web_link(None, "101") is None and plex_web_link("abc", None) is None
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_ui_cards.py`
Expected: `ModuleNotFoundError: No module named 'ui.cards'`.

- [ ] **Step 4: Implement `ui/cards.py`:**

```python
# ui/cards.py

"""Renderers for the /plex watchers stream overview and stream detail cards."""

from datetime import datetime, timedelta
from typing import Optional, Sequence
from urllib.parse import quote

import nextcord
from nextcord.utils import escape_markdown

from ui.embeds import card, set_image, set_thumbnail
from ui.format import format_ms, join, plural, progress_bar, timestamp, to_int, truncate
from ui.images import Attachment
from ui.theme import Colour, Emoji, Limits

_DECISIONS = {"direct play": "Direct Play", "copy": "Direct Stream", "transcode": "Transcode"}
_SUBTITLE_DECISIONS = {"burn": "Burned in", "copy": "Direct Stream", "transcode": "Transcode"}
_STATE_ICONS = {"playing": Emoji.PLAYING, "paused": Emoji.PAUSED, "buffering": Emoji.BUFFERING}
_LOCATIONS = {"lan": "Local", "wan": "Remote", "cellular": "Mobile data"}


def session_user(session: dict) -> str:
    return session.get("friendly_name") or session.get("username") or session.get("user") or "Unknown"


def session_title(session: dict) -> str:
    title = session.get("title") or session.get("full_title") or "Unknown"
    media_type = session.get("media_type")
    if media_type == "episode":
        season = to_int(session.get("parent_media_index"), -1)
        episode = to_int(session.get("media_index"), -1)
        code = f"S{season:02d}E{episode:02d}" if season >= 0 and episode >= 0 else None
        return join([session.get("grandparent_title"), code, title])
    if media_type == "track":
        return join([session.get("grandparent_title"), title], " \u2014 ")
    year = session.get("year")
    return f"{title} ({year})" if year else title


def _state(session: dict) -> str:
    return str(session.get("state") or "").lower()


def _decision(value) -> Optional[str]:
    return _DECISIONS.get(str(value or "").lower())


def _progress(session: dict) -> str:
    offset, duration = to_int(session.get("view_offset")), to_int(session.get("duration"))
    if duration <= 0:
        return format_ms(offset) if offset else ""
    return f"`{progress_bar(offset / duration)}` {format_ms(offset)} / {format_ms(duration)}"


def session_lines(session: dict) -> str:
    """Two lines per stream for the overview: who and what, then progress, quality, device."""
    icon = _STATE_ICONS.get(_state(session), Emoji.BULLET)
    user = escape_markdown(session_user(session))
    title = escape_markdown(session_title(session))
    quality = join([session.get("stream_video_full_resolution"), _decision(session.get("transcode_decision"))], " ")
    device = session.get("player") or session.get("product") or session.get("platform")
    details = join([_progress(session), quality, device])
    return f"{icon} **{user}** \u00b7 {title}" + (f"\n{details}" if details else "")


def _state_colour(sessions: Sequence[dict]) -> int:
    return Colour.PLAYING if any(_state(s) == "playing" for s in sessions) else Colour.WARNING


def stream_overview(sessions: Sequence[dict], *, bandwidth_kbps: int, strip: Optional[Attachment],
                    updated: datetime) -> nextcord.Embed:
    header = join([
        f"**{plural(len(sessions), 'stream')}**",
        f"{bandwidth_kbps / 1000:.1f} Mbps" if bandwidth_kbps else None,
        f"updated {timestamp(updated)}",
    ])
    blocks = [header]
    used = len(header)
    reserve = 40  # room for the "...and N more" line
    for index, session in enumerate(sessions):
        block = session_lines(session)
        if used + 2 + len(block) > Limits.DESCRIPTION - reserve:
            blocks.append(f"\u2026and {plural(len(sessions) - index, 'more stream')}")
            break
        blocks.append(block)
        used += 2 + len(block)
    embed = card(title="Now playing", description="\n\n".join(blocks), colour=_state_colour(sessions))
    set_image(embed, strip)
    embed.set_footer(text="Pick a stream below for the details")
    return embed


def plex_web_link(server_id: Optional[str], rating_key) -> Optional[str]:
    if not server_id or not rating_key:
        return None
    key = quote(f"/library/metadata/{rating_key}", safe="")
    return f"https://app.plex.tv/desktop/#!/server/{server_id}/details?key={key}"


def _video_line(session: dict) -> Optional[str]:
    source, stream = session.get("video_full_resolution"), session.get("stream_video_full_resolution")
    if not (source or stream):
        return None
    resolution = source if not stream or stream == source else f"{source} \u2192 {stream}"
    codec = (session.get("video_codec") or "").upper()
    return "**Video** " + join([join([resolution, codec], " "), _decision(session.get("video_decision"))])


def _audio_line(session: dict) -> Optional[str]:
    codec = (session.get("audio_codec") or "").upper()
    if not codec:
        return None
    sound = join([codec, session.get("audio_channel_layout")], " ")
    return "**Audio** " + join([sound, _decision(session.get("audio_decision"))])


def _subtitle_line(session: dict) -> Optional[str]:
    language, codec = session.get("subtitle_language"), (session.get("subtitle_codec") or "").upper()
    if not (language or codec):
        return None
    label = f"{language} ({codec})" if language and codec else (language or codec)
    decision = _SUBTITLE_DECISIONS.get(str(session.get("subtitle_decision") or "").lower())
    return "**Subtitles** " + join([label, decision])


def stream_detail(session: dict, *, avatar_url: Optional[str], link: Optional[str],
                  frame: Optional[Attachment], poster: Optional[Attachment], now: datetime) -> nextcord.Embed:
    """The private per-stream card. Never includes the viewer's IP address or location."""
    state = _state(session)
    embed = card(title=session_title(session), url=link, colour=_state_colour([session]))
    embed.set_author(name=truncate(session_user(session), Limits.AUTHOR), icon_url=avatar_url or None)

    progress = _progress(session)
    offset, duration = to_int(session.get("view_offset")), to_int(session.get("duration"))
    if state == "paused":
        progress = join([progress, "Paused"], "\n")
    elif state == "playing" and duration > offset:
        ends = now + timedelta(milliseconds=duration - offset)
        progress = join([progress, f"Ends {timestamp(ends, 't')}"], "\n")
    embed.add_field(name="Progress", value=truncate(progress or "Unknown", Limits.FIELD_VALUE), inline=False)

    stream = join([_video_line(session), _audio_line(session), _subtitle_line(session)], "\n")
    if stream:
        embed.add_field(name="Stream", value=truncate(stream, Limits.FIELD_VALUE), inline=False)

    bandwidth = to_int(session.get("bandwidth"))
    player = join([
        join([session.get("player"), session.get("product")]),
        session.get("platform"),
        _LOCATIONS.get(str(session.get("location") or "").lower()),
        f"{bandwidth / 1000:.1f} Mbps" if bandwidth else None,
    ], "\n")
    if player:
        embed.add_field(name="Player", value=truncate(player, Limits.FIELD_VALUE), inline=False)

    set_image(embed, frame)
    set_thumbnail(embed, poster)
    return embed
```

- [ ] **Step 5: Run the suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ui/cards.py tests/fakes.py tests/test_ui_cards.py
git commit -m "Add ui.cards: /plex watchers overview and stream detail renderers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Rebuild `/plex watchers` on `ui/`

**Files:**
- Modify: `cogs/server_commands.py`:
  - remove `_format_session_field`, `StreamSelect`, `StopStreamPanel` and `WatchersView.manage_button`
  - rework `WatchersView` and `StopStreamModal`
  - add `StreamDetailsSelect`, `StreamDetailView`, `render_watchers`, `show_stream_detail`, `_find_session`, `_avatar_url`, `_server_id`
  - change `stop_stream` to return `(ok, text)`
- Modify: `tests/fakes.py` (add the dispatch helpers), `tests/test_ui_classes.py` (new class names)
- Create: `tests/test_watchers_dispatch.py`

**Interfaces:**
- Consumes:
  - `ui.cards.stream_overview`, `ui.cards.stream_detail`, `ui.cards.plex_web_link`
  - `ui.images.plex_image`, `poster_key`, `current_frame`, `poster_strip`
  - `ui.embeds.Reply`, `service_down`, `empty_state`, `success`, `error_state`, `ErrorKind`
  - `ui.format.to_int`
- Produces (in `cogs.server_commands`):
  - `WatchersView(cog, sessions, timeout=600)`, with `.message`, `.set_sessions(sessions)` and `async .rerender()`
  - `StreamDetailsSelect(choices)`, `StreamDetailView(cog, session_id, label, watchers_view, can_stop)`, `StopStreamModal(cog, session_id, label, watchers_view)`
  - `ServerCommands`:
    - `async render_watchers(activity, view) -> Reply`
    - `async show_stream_detail(interaction, session_id, watchers_view)`
    - `async stop_stream(actor, session_id, message) -> tuple[bool, str]`
    - `async _server_id() -> str | None`, with `self._pms_identifier` cached
  - `tests/fakes.py`:
    - `FakeTautulli(sessions=None, reachable=True)`
    - `Recorder(monkeypatch)`, with `.calls: list[tuple[str, dict]]` and `.kinds() -> list[str]`
    - `build_bot(tautulli) -> (bot, cog)`
    - `component_payload(custom_id, *, component_type=2, values=None, member=None, message_id=MESSAGE_ID) -> dict`
    - `member_payload(user_id=42, permissions="0", roles=()) -> dict`
    - `settle()`, `MESSAGE_ID`

- [ ] **Step 1: Add the dispatch helpers to `tests/fakes.py`.** Append:

```python
import asyncio
import io
from types import SimpleNamespace

import nextcord
from PIL import Image

GUILD_ID = 123
MESSAGE_ID = 5555


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (20, 30), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


class FakeTautulli:
    """Stands in for tautulli_wrapper.Tautulli; responses use Tautulli's envelope."""

    tautulli_ip, use_https, api_key = "host:8181", False, "key"

    def __init__(self, sessions=None, reachable=True):
        self.sessions = list(sessions or [])
        self.reachable = reachable
        self.terminated = []

    def _ok(self, data):
        return {"response": {"result": "success", "data": data}} if self.reachable else None

    def initialize(self):
        pass

    async def get_activity(self):
        bandwidth = sum(int(s.get("bandwidth") or 0) for s in self.sessions)
        return self._ok({"sessions": self.sessions, "stream_count": len(self.sessions),
                         "total_bandwidth": bandwidth})

    async def get_server_info(self):
        return self._ok({"pms_identifier": "srv1", "pms_name": "Home", "pms_version": "1.41.0",
                         "pms_platform": "Linux", "pms_plexpass": 1})

    async def terminate_session(self, session_key=None, session_id=None, message=None):
        self.terminated.append((session_id, message))
        self.sessions = [s for s in self.sessions if s.get("session_id") != session_id]
        return 200


def patch_images(monkeypatch):
    """Make every Plex image fetch return a tiny valid PNG instead of hitting the network."""
    import ui.images

    async def fake_fetch(*args, **kwargs):
        return io.BytesIO(_png())

    monkeypatch.setattr(ui.images, "fetch_plex_image", fake_fetch)


class FakeMessage:
    def __init__(self, recorder, message_id=MESSAGE_ID):
        self.id = message_id
        self.recorder = recorder

    async def edit(self, **kwargs):
        self.recorder.calls.append(("message.edit", kwargs))


class Recorder:
    """Replaces Discord-facing interaction methods and records what the bot sent."""

    def __init__(self, monkeypatch):
        self.calls = []
        recorder = self

        async def defer(self, *, ephemeral=False, with_message=False):
            recorder.calls.append(("defer", {"ephemeral": ephemeral, "with_message": with_message}))

        def is_done(self):
            return any(kind in ("defer", "send_message", "edit_message", "send_modal")
                       for kind, _ in recorder.calls)

        async def send_message(self, content=None, **kwargs):
            recorder.calls.append(("send_message", dict(kwargs, content=content)))

        async def edit_message(self, **kwargs):
            recorder.calls.append(("edit_message", kwargs))

        async def send_modal(self, modal):
            recorder.calls.append(("send_modal", {"modal": modal}))

        async def edit_original_message(self, **kwargs):
            recorder.calls.append(("edit_original_message", kwargs))

        async def delete_original_message(self, **kwargs):
            recorder.calls.append(("delete_original_message", {}))

        class Followup:
            async def send(self, content=None, **kwargs):
                recorder.calls.append(("followup", dict(kwargs, content=content)))
                return FakeMessage(recorder, message_id=6666)

        monkeypatch.setattr(nextcord.InteractionResponse, "defer", defer)
        monkeypatch.setattr(nextcord.InteractionResponse, "is_done", is_done)
        monkeypatch.setattr(nextcord.InteractionResponse, "send_message", send_message)
        monkeypatch.setattr(nextcord.InteractionResponse, "edit_message", edit_message)
        monkeypatch.setattr(nextcord.InteractionResponse, "send_modal", send_modal)
        monkeypatch.setattr(nextcord.Interaction, "edit_original_message", edit_original_message)
        monkeypatch.setattr(nextcord.Interaction, "delete_original_message", delete_original_message)
        monkeypatch.setattr(nextcord.Interaction, "followup", property(lambda self: Followup()))

    def kinds(self):
        return [kind for kind, _ in self.calls]

    def last(self, kind):
        return next(kwargs for k, kwargs in reversed(self.calls) if k == kind)


def build_bot(tautulli):
    """A PlexBot with ServerCommands loaded the way setup_hook does, without connecting."""
    from cogs.server_commands import ServerCommands
    from plexbot import PlexBot, install_slash_groups

    bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None,
                  default_guild_ids=[GUILD_ID])
    bot.shared_resources = {"tautulli": tautulli}
    bot._connection.application_id = 999
    install_slash_groups(bot)
    cog = ServerCommands(bot)
    cog.status_task.cancel()
    bot.add_cog(cog)
    return bot, cog


def member_payload(user_id=42, permissions="0", roles=()):
    return {
        "user": {"id": str(user_id), "username": f"user{user_id}", "discriminator": "0", "avatar": None},
        "roles": [str(r) for r in roles], "joined_at": "2020-01-01T00:00:00+00:00",
        "deaf": False, "mute": False, "permissions": permissions, "flags": 0,
    }


def component_payload(custom_id, *, component_type=2, values=None, member=None, message_id=MESSAGE_ID):
    """A button (type 2) or select (type 3) click as Discord sends it over the gateway."""
    data = {"custom_id": custom_id, "component_type": component_type}
    if values is not None:
        data["values"] = values
    return {
        "id": "1", "application_id": "999", "type": 3, "token": "tok", "version": 1,
        "guild_id": str(GUILD_ID), "channel_id": "777", "locale": "en-US", "app_permissions": "0",
        "entitlements": [], "authorizing_integration_owners": {},
        "member": member or member_payload(),
        "message": {
            "id": str(message_id), "channel_id": "777", "type": 0, "content": "",
            "author": {"id": "999", "username": "bot", "discriminator": "0", "avatar": None},
            "attachments": [], "embeds": [], "mentions": [], "mention_roles": [], "pinned": False,
            "mention_everyone": False, "tts": False, "timestamp": "2026-10-14T00:00:00+00:00",
            "edited_timestamp": None, "flags": 0, "components": [],
        },
        "data": data,
    }


async def settle():
    """Let nextcord's dispatched view/modal callback tasks run."""
    for _ in range(5):
        await asyncio.sleep(0.01)


# Re-exported for tests that need a plain object with guild permissions.
def permissions_user(admin=False, user_id=42, role_ids=()):
    return SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=r) for r in role_ids],
                           guild_permissions=SimpleNamespace(administrator=admin))
```

> `is_done` reads the recorder, because `InteractionResponse` uses `__slots__` and the patched methods can't mark it as responded. Keep it this way.

- [ ] **Step 2: Write the failing dispatch tests** in `tests/test_watchers_dispatch.py`:

```python
"""/plex watchers through nextcord's real component dispatch (ConnectionState.parse_interaction_create)."""

import asyncio

from fakes import (
    EPISODE_PAUSED, MOVIE_PLAYING, FakeMessage, FakeTautulli, Recorder, build_bot, component_payload,
    many_sessions, member_payload, patch_images, settle, MESSAGE_ID,
)
import nextcord

from cogs.server_commands import StreamDetailsSelect, StreamDetailView, WatchersView


def _store(bot, view):
    bot._connection.store_view(view, MESSAGE_ID)


def _click(bot, item, **kwargs):
    bot._connection.parse_interaction_create(component_payload(item.custom_id, **kwargs))


def test_overview_has_refresh_and_details_select(monkeypatch):
    patch_images(monkeypatch)

    async def run():
        _bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING, EPISODE_PAUSED]))
        view = WatchersView(cog, [])
        reply = await cog.render_watchers(await cog.fetch_activity(), view)
        return reply, view

    reply, view = asyncio.run(run())
    assert reply.embeds[0].title == "Now playing"
    assert [a.filename for a in reply.attachments] == ["posters.png"]
    select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
    assert [o.value for o in select.options] == ["abc", "def"]
    assert not any(getattr(c, "label", None) == "Manage streams" for c in view.children)


def test_nobody_watching_is_empty_state_without_select(monkeypatch):
    patch_images(monkeypatch)

    async def run():
        _bot, cog = build_bot(FakeTautulli([]))
        view = WatchersView(cog, [])
        return await cog.render_watchers(await cog.fetch_activity(), view), view

    reply, view = asyncio.run(run())
    assert "Nobody's watching" in reply.embeds[0].description
    assert not any(isinstance(c, StreamDetailsSelect) for c in view.children)


def test_details_select_capped_at_25(monkeypatch):
    patch_images(monkeypatch)

    async def run():
        _bot, cog = build_bot(FakeTautulli(many_sessions(30)))
        view = WatchersView(cog, [])
        await cog.render_watchers(await cog.fetch_activity(), view)
        return view

    view = asyncio.run(run())
    select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
    assert len(select.options) == 25


def test_refresh_click_edits_the_message(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        refresh = next(c for c in view.children if getattr(c, "label", None) == "Refresh")
        _click(bot, refresh)
        await settle()

    asyncio.run(run())
    assert recorder.kinds()[:2] == ["defer", "edit_original_message"]
    assert recorder.last("edit_original_message")["attachments"] == []


def test_refresh_when_tautulli_down_tells_clicker_privately(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING], reachable=False))
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        _click(bot, next(c for c in view.children if getattr(c, "label", None) == "Refresh"))
        await settle()

    asyncio.run(run())
    assert recorder.kinds() == ["defer", "followup"]
    assert recorder.last("followup")["ephemeral"] is True


def test_picking_a_stream_opens_private_detail(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING, EPISODE_PAUSED]))
        view = WatchersView(cog, [MOVIE_PLAYING, EPISODE_PAUSED])
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    assert recorder.kinds() == ["defer", "followup"]
    sent = recorder.last("followup")
    assert sent["ephemeral"] is True and sent["embeds"][0].title == "The Matrix (1999)"
    assert sorted(f.filename for f in sent["files"]) == ["frame.jpg", "poster.jpg"]
    detail_view = sent["view"]
    assert isinstance(detail_view, StreamDetailView)
    assert not any(getattr(c, "label", None) == "Stop this stream" for c in detail_view.children)


def test_stream_managers_get_stop_button(monkeypatch):
    # Offline there's no cached guild, so Member.guild_permissions can't resolve admin rights;
    # make user 42 a configured stream manager instead (the same check, via stream_manager_ids).
    import cogs.server_commands as server_commands

    real_get = server_commands.config.get
    monkeypatch.setattr(
        server_commands.config, "get",
        lambda section, key, default=None: ["42"] if (section, key) == ("discord", "stream_manager_ids")
        else real_get(section, key, default),
    )
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"], member=member_payload(user_id=42))
        await settle()

    asyncio.run(run())
    detail_view = recorder.last("followup")["view"]
    assert any(getattr(c, "label", None) == "Stop this stream" for c in detail_view.children)


def test_stream_that_ended_shows_private_notice_and_rerenders(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([]))  # the stream ended before the click
        view = WatchersView(cog, [MOVIE_PLAYING])
        view.message = FakeMessage(recorder)
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    assert "That stream has ended" in recorder.last("followup")["embeds"][0].description
    assert "message.edit" in recorder.kinds()  # overview re-rendered


def test_stop_from_detail_replaces_card_with_confirmation(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)
    import cogs.server_commands as server_commands

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(server_commands.asyncio, "sleep", no_wait)

    async def run():
        tautulli = FakeTautulli([MOVIE_PLAYING])
        bot, cog = build_bot(tautulli)
        watchers = WatchersView(cog, [MOVIE_PLAYING])
        modal = server_commands.StopStreamModal(cog, "abc", "Alice — The Matrix", watchers)
        modal.message_input._inputed_value = "Server maintenance"
        payload = dict(component_payload("modal"), type=5, data={"custom_id": modal.custom_id, "components": []})
        interaction = nextcord.Interaction(data=payload, state=bot._connection)
        await modal.callback(interaction)
        return tautulli

    tautulli = asyncio.run(run())
    assert tautulli.terminated == [("abc", "Server maintenance")]
    assert recorder.kinds()[:2] == ["defer", "edit_original_message"]
    edited = recorder.last("edit_original_message")
    assert "Stopped **Alice**" in edited["embeds"][0].description and edited["view"] is None


def test_stop_option_reports_ended_stream(monkeypatch):
    async def run():
        _bot, cog = build_bot(FakeTautulli([]))
        return await cog.stop_stream("admin", "gone", None)

    ok, text = asyncio.run(run())
    assert ok is False and "isn't running anymore" in text
```

In `tests/test_ui_classes.py`, change `test_finds_our_ui_classes` to:

```python
def test_finds_our_ui_classes():
    names = {cls.__name__ for cls in _our_ui_classes()}
    assert {"WatchersView", "StreamDetailsSelect", "StreamDetailView", "StopStreamModal", "LoggedView"} <= names
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_watchers_dispatch.py tests/test_ui_classes.py`
Expected: `ImportError: cannot import name 'StreamDetailsSelect'`.

- [ ] **Step 4: Implement in `cogs/server_commands.py`.**

Add these imports next to the existing ones:

```python
from ui.cards import plex_web_link, session_title, session_user, stream_detail, stream_overview
from ui.embeds import ErrorKind, Reply, empty_state, error_state, service_down, success
from ui.format import to_int
from ui.images import current_frame, plex_image, poster_key, poster_strip
```

Delete `_format_session_field`, `StreamSelect`, `StopStreamPanel` and the old `WatchersView` (including `manage_button`).

Also delete the module's own `_session_user` and `_session_title`, and import `session_user` and `session_title` from `ui.cards` instead (add them to the `ui.cards` import). Then:
- In `_session_label`, use `session_user(session)` and `session_title(session)`.
- In `stop_stream` below, use the same two names.

The existing `tests/test_watchers.py` label tests keep passing: `session_title` falls back to `full_title`, and adds a year only when there is one.

Replace `StopStreamModal` and add the new classes:

```python
class StopStreamModal(LoggedModal):
    """Asks for an optional message to the viewer, then stops the stream."""

    def __init__(self, cog: "ServerCommands", session_id: str, label: str, watchers_view: "WatchersView"):
        super().__init__(title=truncate(f"Stop: {label}", 45), timeout=300)
        self.cog = cog
        self.session_id = session_id
        self.watchers_view = watchers_view
        self.message_input = nextcord.ui.TextInput(
            label="Message to the viewer (optional)",
            style=nextcord.TextInputStyle.paragraph,
            required=False,
            max_length=200,
            placeholder="Shown on their Plex client when the stream stops",
        )
        self.add_item(self.message_input)

    async def callback(self, interaction: nextcord.Interaction):
        # Opened from the private detail card: acknowledge without a new message, then
        # edit that card into the result.
        await interaction.response.defer()
        ok, text = await self.cog.stop_stream(interaction.user, self.session_id, self.message_input.value)
        reply = success(text) if ok else error_state(ErrorKind.INPUT, text)
        await reply.edit(interaction)
        # Plex takes a moment to drop a stopped session from its activity list.
        await asyncio.sleep(2)
        await self.watchers_view.rerender()


class StreamDetailView(LoggedView):
    """Controls on a private stream detail card: Stop, for stream managers only."""

    def __init__(self, cog: "ServerCommands", session_id: str, label: str, watchers_view: "WatchersView",
                 can_stop: bool):
        super().__init__(timeout=600)
        self.cog = cog
        self.session_id = session_id
        self.label = label
        self.watchers_view = watchers_view
        if not can_stop:
            self.remove_item(self.stop_button)

    @nextcord.ui.button(label="Stop this stream", emoji="\U0001F6D1", style=nextcord.ButtonStyle.danger)
    async def stop_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if not self.cog.can_manage(interaction.user):
            await error_state(ErrorKind.PERMISSION, _NO_STREAM_PERMISSION).send(interaction, ephemeral=True)
            return
        await interaction.response.send_modal(
            StopStreamModal(self.cog, self.session_id, self.label, self.watchers_view)
        )


class StreamDetailsSelect(nextcord.ui.StringSelect):
    def __init__(self, choices: dict[str, str]):
        options = [nextcord.SelectOption(label=label, value=sid) for label, sid in choices.items()]
        super().__init__(placeholder="Stream details\u2026", options=options, row=0)

    async def callback(self, interaction: nextcord.Interaction):
        watchers_view: WatchersView = self.view
        await watchers_view.cog.show_stream_detail(interaction, self.values[0], watchers_view)


class WatchersView(LoggedView):
    """Controls under the public /plex watchers overview: stream details (anyone) and Refresh."""

    def __init__(self, cog: "ServerCommands", sessions: list, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.message = None
        self.set_sessions(sessions)

    def set_sessions(self, sessions: list) -> None:
        for item in [child for child in self.children if isinstance(child, StreamDetailsSelect)]:
            self.remove_item(item)
        choices = _session_choices(sessions, "")
        if choices:
            self.add_item(StreamDetailsSelect(choices))

    # Not named refresh(): nextcord calls View.refresh(components) itself when the
    # message is edited, and overriding it crashed the gateway connection.
    async def rerender(self):
        """Re-render the public overview, e.g. after a stream was stopped or ended."""
        if self.message is None:
            return
        activity = await self.cog.fetch_activity()
        if activity is None:
            return
        reply = await self.cog.render_watchers(activity, self)
        try:
            await reply.edit_message(self.message)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not refresh /plex watchers message: {e}")

    @nextcord.ui.button(label="Refresh", emoji="\U0001F504", style=nextcord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.defer()
        activity = await self.cog.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        reply = await self.cog.render_watchers(activity, self)
        await reply.edit(interaction)

    async def on_timeout(self):
        # Expired controls are removed; the embed's "updated" time shows its age.
        if self.message:
            try:
                await self.message.edit(view=None)
            except nextcord.HTTPException:
                pass
```

Add a module-level helper after `_can_manage_streams`:

```python
def _find_session(activity: dict, session_id: str) -> Optional[dict]:
    return next((s for s in activity.get("sessions", []) if str(s.get("session_id")) == session_id), None)
```

`from typing import Optional` is needed if the module doesn't already import it.

In `ServerCommands.__init__`, add `self._pms_identifier: Optional[str] = None`. Remove `self.plex_image` once nothing uses it (Task 6 removes the last use; delete it there if it's still referenced here).

Replace `build_watchers_embed`, `stop_stream` and the `watchers` command body. Keep `can_manage`, `fetch_activity` and the autocomplete exactly as they are:

```python
    def _visible_sessions(self, activity: dict) -> list:
        mappings = UserMappings.load_user_mappings()
        ignored = {u["plex_username"] for u in mappings if u.get("ignore", False)}
        return [s for s in activity.get("sessions", []) if s.get("username") not in ignored]

    async def render_watchers(self, activity: dict, view: "WatchersView") -> Reply:
        """The public overview for the current activity, with its controls rebuilt to match."""
        sessions = self._visible_sessions(activity)
        view.set_sessions(sessions)
        if not sessions:
            reply = empty_state("Nobody's watching right now.", hint="Hit Refresh to check again.")
            reply.view = view
            return reply
        posters = await asyncio.gather(*(
            plex_image(self.tautulli, poster_key(s), filename=f"poster-{i}.jpg", width=200, height=300)
            for i, s in enumerate(sessions[:6])
        ))
        strip = await asyncio.to_thread(
            poster_strip, [p.data for p in posters if p], extra=max(0, len(sessions) - 6)
        )
        embed = stream_overview(
            sessions, bandwidth_kbps=to_int(activity.get("total_bandwidth")), strip=strip,
            updated=nextcord.utils.utcnow(),
        )
        return Reply([embed], [strip] if strip else [], view=view)

    async def _server_id(self) -> Optional[str]:
        """The Plex server's machine identifier, for app.plex.tv links (cached)."""
        if self._pms_identifier is None:
            response = await self.tautulli.get_server_info()
            if Tautulli.check_response(response):
                self._pms_identifier = Tautulli.get_response_data(response, {}).get("pms_identifier") or ""
        return self._pms_identifier or None

    def _avatar_url(self, session: dict) -> Optional[str]:
        """The viewer's Discord avatar when mapped (recognisable in the server), else their Plex one."""
        mapping = UserMappings.get_mapping_by_plex_username(session.get("username"))
        if mapping and mapping.get("discord_id"):
            user = self.bot.get_user(to_int(mapping["discord_id"]))
            if user:
                return user.display_avatar.url
        thumb = session.get("user_thumb") or ""
        return thumb if thumb.startswith("http") else None

    async def show_stream_detail(self, interaction: nextcord.Interaction, session_id: str,
                                 watchers_view: "WatchersView") -> None:
        await interaction.response.defer(ephemeral=True, with_message=True)
        activity = await self.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        session = _find_session(activity, session_id)
        if session is None:
            await empty_state("That stream has ended.", hint="The list has been refreshed.").send(
                interaction, ephemeral=True
            )
            await watchers_view.rerender()
            return

        frame, poster = await asyncio.gather(
            current_frame(self.tautulli, session, filename="frame.jpg"),
            plex_image(self.tautulli, poster_key(session), filename="poster.jpg"),
        )
        if frame is None:
            frame = await plex_image(
                self.tautulli, session.get("art"), filename="art.jpg", width=640, height=360, fallback=None
            )
        embed = stream_detail(
            session,
            avatar_url=self._avatar_url(session),
            link=plex_web_link(await self._server_id(), session.get("rating_key")),
            frame=frame,
            poster=poster,
            now=nextcord.utils.utcnow(),
        )
        view = StreamDetailView(self, session_id, _session_label(session), watchers_view,
                                can_stop=self.can_manage(interaction.user))
        await Reply([embed], [a for a in (frame, poster) if a], view=view).send(interaction, ephemeral=True)

    async def stop_stream(self, actor, session_id: str, message: Optional[str]) -> tuple[bool, str]:
        """Stop a stream; returns (stopped, text for the manager who asked)."""
        activity = await self.fetch_activity()
        if activity is None:
            return False, "Couldn't reach Tautulli to stop that stream. Try again in a moment."
        session = _find_session(activity, session_id)
        if session is None:
            return False, "That stream isn't running anymore. Run `/plex watchers` to see current streams."

        label = _session_label(session)
        status = await self.tautulli.terminate_session(session_id=session_id, message=message or None)
        if status == 200:
            note = f" with message: {message}" if message else ""
            logger.info(f"{actor} stopped stream {session_id} ({label}){note}")
            return True, f"Stopped **{session_user(session)}**'s stream of **{session_title(session)}**."
        logger.warning(f"{actor} failed to stop stream {session_id} ({label}): Tautulli returned an error")
        return False, "Tautulli couldn't stop that stream. An admin can check `/plexadmin logs` for details."
```

The `watchers` command body. Keep its decorator and options as they are:

```python
        if stop:
            if not self.can_manage(interaction.user):
                await error_state(ErrorKind.PERMISSION, _NO_STREAM_PERMISSION).send(interaction, ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            ok, text = await self.stop_stream(interaction.user, stop, message)
            await (success(text) if ok else error_state(ErrorKind.INPUT, text)).send(interaction, ephemeral=True)
            return

        await interaction.response.defer()
        activity = await self.fetch_activity()
        if activity is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        view = WatchersView(self, [])
        reply = await self.render_watchers(activity, view)
        view.message = await reply.send(interaction)
```

Update `_NO_STREAM_PERMISSION` to `"Only stream managers can stop streams."`.

- [ ] **Step 5: Run the suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add cogs/server_commands.py tests/fakes.py tests/test_watchers_dispatch.py tests/test_ui_classes.py
git commit -m "Rebuild /plex watchers: overview with poster strip, private stream details

Every stream stays on the public overview; a Stream details dropdown opens a
private card with the current frame, stream and player details, and a Stop
button for stream managers. Manage streams is replaced by that flow.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Redesign `/plex status`

**Files:**
- Modify: `tautulli_wrapper.py` (add `get_pms_update`), `cogs/server_commands.py` (`status` command, `_plex_service_status`, `library_totals`, `build_status_reply`; remove `self.plex_image`)
- Create: `tests/test_status_card.py`

**Interfaces:**
- Consumes: `ui.embeds.card`, `set_thumbnail`, `Reply`, `service_down`; `ui.images.mascot`; `ui.format.plural`, `to_int`, `is_truthy`; `ui.theme.Colour`
- Produces:
  - `Tautulli.get_pms_update() -> dict | None`
  - `cogs.server_commands.library_totals(rows: list[dict]) -> dict[str, int]`, with keys `movies, shows, episodes, artists, tracks`
  - `cogs.server_commands.build_status_reply(server: dict, libraries: list, activity: dict | None, update: dict | None, plex_service: dict | None, *, now: datetime) -> Reply`
  - `ServerCommands._plex_service_status() -> dict | None`, returning `{"indicator": str | None, "description": str}`

- [ ] **Step 1: Write the failing tests** in `tests/test_status_card.py`:

```python
"""/plex status: library totals and the status card."""

from datetime import datetime, timezone

import ui.embeds
from cogs.server_commands import build_status_reply, library_totals
from ui.images import Attachment
from ui.theme import Colour

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
SERVER = {"pms_name": "Home", "pms_version": "1.41.0", "pms_platform": "Linux", "pms_plexpass": 1,
          "pms_identifier": "srv1", "pms_ip": "10.0.0.2"}
LIBRARIES = [
    {"section_type": "movie", "count": "1234", "parent_count": "", "child_count": ""},
    {"section_type": "movie", "count": "10", "parent_count": None, "child_count": None},
    {"section_type": "show", "count": "62", "parent_count": "240", "child_count": "3745"},
    {"section_type": "artist", "count": "300", "parent_count": "900", "child_count": "4500"},
    {"section_type": "photo", "count": "50"},
]
HEALTHY = {"indicator": "none", "description": "All Systems Operational"}


def _fields(reply):
    return {field.name: field.value for field in reply.embeds[0].fields}


def test_library_totals():
    assert library_totals(LIBRARIES) == {"movies": 1244, "shows": 62, "episodes": 3745,
                                         "artists": 300, "tracks": 4500}
    assert library_totals([]) == {"movies": 0, "shows": 0, "episodes": 0, "artists": 0, "tracks": 0}


def test_healthy_status(monkeypatch):
    monkeypatch.setattr(ui.embeds, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
    activity = {"stream_count": "2", "total_bandwidth": "20000"}
    reply = build_status_reply(SERVER, LIBRARIES, activity, {"update_available": False}, HEALTHY, now=NOW)
    embed = reply.embeds[0]
    assert embed.title == "Home" and embed.colour.value == Colour.PLAYING
    assert "Plex Media Server 1.41.0 on Linux · Plex Pass" == embed.description
    fields = _fields(reply)
    assert "1,244 movies" in fields["Library"] and "3,745 episodes" in fields["Library"]
    assert "4,500 tracks" in fields["Library"]
    assert fields["Activity"] == "2 streams · 20.0 Mbps"
    assert fields["Plex services"] == "All Systems Operational"
    assert "Update available" not in fields
    assert embed.thumbnail.url == "attachment://mascot-server.png"
    assert "10.0.0.2" not in str(embed.to_dict())


def test_incident_update_and_missing_parts(monkeypatch):
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: None)
    incident = {"indicator": "minor", "description": "Partial outage: Plex Web"}
    reply = build_status_reply(dict(SERVER, pms_plexpass=0), [], None,
                               {"update_available": 1, "version": "1.42.0"}, incident, now=NOW)
    embed = reply.embeds[0]
    fields = _fields(reply)
    assert embed.colour.value == Colour.WARNING and "Plex Pass" not in embed.description
    assert "Library" not in fields and fields["Activity"] == "Unavailable"
    assert fields["Plex services"].endswith("Partial outage: Plex Web")
    assert fields["Update available"] == "Plex Media Server 1.42.0"
    assert reply.attachments == []


def test_nobody_watching_and_unknown_service(monkeypatch):
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: None)
    reply = build_status_reply(SERVER, LIBRARIES, {"stream_count": 0}, None, None, now=NOW)
    fields = _fields(reply)
    assert fields["Activity"] == "Nobody's watching"
    assert fields["Plex services"] == "Status unavailable"
    assert reply.embeds[0].colour.value == Colour.PLAYING
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_status_card.py`
Expected: `ImportError: cannot import name 'build_status_reply'`.

- [ ] **Step 3: Add `get_pms_update` to `Tautulli`** in `tautulli_wrapper.py`, after `get_server_info`:

```python
    async def get_pms_update(self) -> Optional[Dict[str, Any]]:
        """Check whether a Plex Media Server update is available."""
        return await self.api_call("get_pms_update")
```

- [ ] **Step 4: Implement in `cogs/server_commands.py`.** Add the imports `from datetime import datetime`, `from ui.embeds import card, set_thumbnail` (merge with the existing `ui.embeds` import), `from ui.format import is_truthy, plural` (merge with the existing `ui.format` import), `from ui.images import mascot` (merge) and `from ui.theme import Colour`.

Module-level functions, placed after the `/plex watchers` helpers:

```python
# --- /plex status helpers ----------------------------------------------------


def library_totals(rows) -> dict:
    """Sum Tautulli's get_libraries rows into movie, show, episode, artist and track counts."""
    totals = {"movies": 0, "shows": 0, "episodes": 0, "artists": 0, "tracks": 0}
    for row in rows or []:
        kind = row.get("section_type")
        if kind == "movie":
            totals["movies"] += to_int(row.get("count"))
        elif kind == "show":
            totals["shows"] += to_int(row.get("count"))
            totals["episodes"] += to_int(row.get("child_count"))
        elif kind == "artist":
            totals["artists"] += to_int(row.get("count"))
            totals["tracks"] += to_int(row.get("child_count"))
    return totals


def build_status_reply(server: dict, libraries, activity: Optional[dict], update: Optional[dict],
                       plex_service: Optional[dict], *, now: datetime) -> Reply:
    incident = plex_service is not None and (plex_service.get("indicator") or "none") != "none"
    plex_pass = " \u00b7 Plex Pass" if to_int(server.get("pms_plexpass")) else ""
    embed = card(
        title=server.get("pms_name") or "Plex Media Server",
        description=(
            f"Plex Media Server {server.get('pms_version') or 'unknown version'} "
            f"on {server.get('pms_platform') or 'unknown platform'}{plex_pass}"
        ),
        colour=Colour.WARNING if incident else Colour.PLAYING,
    )

    totals = library_totals(libraries)
    library = [
        f"\U0001F3AC {plural(totals['movies'], 'movie')}" if totals["movies"] else None,
        f"\U0001F4FA {plural(totals['shows'], 'show')} \u00b7 {plural(totals['episodes'], 'episode')}"
        if totals["shows"] else None,
        f"\U0001F3B5 {plural(totals['artists'], 'artist')} \u00b7 {plural(totals['tracks'], 'track')}"
        if totals["artists"] else None,
    ]
    library = [line for line in library if line]
    if library:
        embed.add_field(name="Library", value="\n".join(library), inline=True)

    if activity is None:
        watching = "Unavailable"
    else:
        streams = to_int(activity.get("stream_count"))
        bandwidth = to_int(activity.get("total_bandwidth"))
        watching = join([plural(streams, "stream"), f"{bandwidth / 1000:.1f} Mbps" if bandwidth else None]) \
            if streams else "Nobody's watching"
    embed.add_field(name="Activity", value=watching, inline=True)

    service = plex_service.get("description") if plex_service else "Status unavailable"
    embed.add_field(name="Plex services", value=("\u26A0\uFE0F " if incident else "") + service, inline=False)

    if update and is_truthy(update.get("update_available")):
        embed.add_field(name="Update available", value=f"Plex Media Server {update.get('version')}", inline=False)

    embed.timestamp = now
    art = mascot("mascot-server")
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])
```

(Import `join` from `ui.format` too.)

Replace the `status` command body and add `_plex_service_status`:

```python
    async def _plex_service_status(self) -> Optional[dict]:
        """plex.tv's own service status (Statuspage format), or None if it can't be fetched."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://status.plex.tv/api/v2/status.json") as response:
                    if response.status != 200:
                        return None
                    status = (await response.json()).get("status", {})
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.warning(f"Could not fetch Plex service status: {e}")
            return None
        return {"indicator": status.get("indicator"), "description": status.get("description") or "Unknown"}

    @plex.subcommand(description="Show how the Plex server is doing: library, activity, updates")
    async def status(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        server_r, libraries_r, activity_r, update_r, plex_service = await asyncio.gather(
            self.tautulli.get_server_info(),
            self.tautulli.get_libraries(),
            self.tautulli.get_activity(),
            self.tautulli.get_pms_update(),
            self._plex_service_status(),
        )
        if not Tautulli.check_response(server_r):
            logger.error("Failed to retrieve server info from Tautulli.")
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        server = Tautulli.get_response_data(server_r, {})
        self._pms_identifier = server.get("pms_identifier") or self._pms_identifier

        def data(response, default):
            return Tautulli.get_response_data(response, default) if Tautulli.check_response(response) else default

        reply = build_status_reply(
            server, data(libraries_r, []), data(activity_r, None), data(update_r, None), plex_service,
            now=nextcord.utils.utcnow(),
        )
        await reply.send(interaction)
```

Delete `self.plex_image = config.get("ui", "plex_image")` from `__init__` if nothing references `plex_image` any more (`grep -n plex_image cogs/server_commands.py` should return nothing afterwards).

- [ ] **Step 5: Run the suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tautulli_wrapper.py cogs/server_commands.py tests/test_status_card.py
git commit -m "Redesign /plex status: health colour, library totals, activity, updates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `/plex help` with categories

**Files:**
- Modify: `cogs/utility_commands.py` (rewrite: `/plex help`, category data and helpers, `HelpView`; remove prefix `help`)
- Modify: `cogs/server_commands.py`, `cogs/media_commands.py`, `cogs/recommendations.py`, `cogs/plex_stats.py`, `cogs/visualizations.py`, `cogs/user_management.py`: add a `help_category` class attribute
- Modify: `cogs/server_commands.py` presence text `": plex help"` → `"/plex help"`
- Modify: `tests/test_slash_registration.py` (subset assertion), `tests/test_ui_classes.py` (add `HelpCategorySelect`)
- Create: `tests/test_help.py`

**Interfaces:**
- Consumes: `slash_commands.command_paths(bot)`, `slash_commands.plex`; `ui.embeds.card`, `Reply`, `set_thumbnail`; `ui.images.mascot`; `ui.format.plural`, `truncate`; `ui.theme.brand_colour`
- Produces (in `cogs.utility_commands`):
  - `CATEGORIES: list[tuple[str, str, str, str]]` (key, emoji, name, summary)
  - `HelpEntry(usage: str, description: str, category: str, example: str | None = None)`
  - `collect_help_entries(bot, prefix: str) -> list[HelpEntry]`
  - `visible_entries(entries, is_admin: bool) -> list[HelpEntry]`
  - `help_overview(entries) -> Reply`, `help_category_page(entries, key: str) -> Reply`
  - `HelpView(entries)`, `HelpCategorySelect(entries)`

- [ ] **Step 1: Write the failing tests** in `tests/test_help.py`:

```python
"""/plex help: entries from command metadata, categories, admin hiding."""

import asyncio
from types import SimpleNamespace

import nextcord
from nextcord.ext import commands

import cogs.utility_commands as help_module
from cogs.utility_commands import (
    HelpEntry, collect_help_entries, help_category_page, help_overview, visible_entries,
)
from fakes import FakeTautulli, build_bot

ENTRIES = [
    HelpEntry("/plex watchers [stop] [message]", "See who's watching", "watching", "/plex watchers"),
    HelpEntry("/plex status", "Server health", "watching"),
    HelpEntry("plex movie <movie_name>", "Look up a movie", "library"),
    HelpEntry("/plexadmin logs [level] [search]", "Bot logs", "admin"),
]


def _build_with_prefix_cog():
    class Library(commands.Cog):
        help_category = "library"

        @commands.command()
        async def movie(self, ctx, *, movie_name: str):
            """Lookup a movie in your Plex library.

            More detail that should not appear."""

        @commands.command(hidden=True)
        async def secret(self, ctx):
            """Hidden."""

    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        bot.add_cog(Library())
        bot.add_cog(help_module.UtilityCommands(bot))
        return collect_help_entries(bot, "plex ")

    return asyncio.run(run())


def test_entries_come_from_slash_and_prefix_metadata():
    entries = {e.usage: e for e in _build_with_prefix_cog()}
    assert entries["/plex watchers [stop] [message]"].category == "watching"
    assert entries["/plex watchers [stop] [message]"].description.startswith("See who's watching")
    assert entries["/plexadmin logs [level] [search]"].category == "admin"
    movie = entries["plex movie <movie_name>"]
    assert movie.category == "library" and movie.description == "Lookup a movie in your Plex library."
    assert not any(usage.startswith("plex secret") for usage in entries)
    assert not any(usage.startswith("/plex help") for usage in entries)  # help doesn't list itself


def test_admin_hidden_from_non_admins():
    assert [e.category for e in visible_entries(ENTRIES, is_admin=False)] == ["watching", "watching", "library"]
    assert len(visible_entries(ENTRIES, is_admin=True)) == 4


def test_overview_lists_categories_with_counts(monkeypatch):
    monkeypatch.setattr(help_module, "mascot", lambda slot: None)
    embed = help_overview(visible_entries(ENTRIES, is_admin=False)).embeds[0]
    assert "**Watching**" in embed.description and "2 commands" in embed.description
    assert "**Library**" in embed.description and "1 command" in embed.description
    assert "Admin" not in embed.description and "Charts" not in embed.description


def test_category_page(monkeypatch):
    embed = help_category_page(ENTRIES, "watching").embeds[0]
    assert "Watching" in embed.title
    assert "`/plex watchers [stop] [message]`" in embed.description
    assert "-# e.g. `/plex watchers`" in embed.description
    assert "plex movie" not in embed.description


def test_help_command_without_guild_permissions_hides_admin(monkeypatch):
    monkeypatch.setattr(help_module, "mascot", lambda slot: None)
    sent = {}

    class Response:
        def is_done(self):
            return False

        async def send_message(self, **kwargs):
            sent.update(kwargs)

    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        utility = help_module.UtilityCommands(bot)
        bot.add_cog(utility)
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=Response())
        await help_module.UtilityCommands.help.callback(utility, interaction)

    asyncio.run(run())
    assert sent["ephemeral"] is True
    assert "Admin" not in sent["embeds"][0].description
    select = next(c for c in sent["view"].children if isinstance(c, nextcord.ui.StringSelect))
    assert "admin" not in [o.value for o in select.options]
```

In `tests/test_slash_registration.py`, change `assert set(plex.children) == {"status", "watchers"}` to `assert {"status", "watchers"} <= set(plex.children)`.

In `tests/test_ui_classes.py`, add `"HelpCategorySelect"` to the expected set in `test_finds_our_ui_classes`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_help.py`
Expected: `ImportError: cannot import name 'HelpEntry'`.

- [ ] **Step 3: Add the `help_category` attributes.** Add one line as the first statement in each cog class body:
- `ServerCommands`: `help_category = "watching"`
- `MediaCommands`: `help_category = "library"`
- `Recommendations`: `help_category = "library"`
- `PlexStats`: `help_category = "stats"`
- `Visualizations`: `help_category = "charts"`
- `UserManagement`: `help_category = "admin"`

`PlexData` and `UtilityCommands` get none, so they're not listed. In `ServerCommands.status_task`, change `activity_text = ": plex help"` to `activity_text = "/plex help"`.

- [ ] **Step 4: Rewrite `cogs/utility_commands.py`:**

```python
# cogs/utility_commands.py

import logging
from dataclasses import dataclass
from typing import Optional

import nextcord
from nextcord.ext import commands

from config import config
from errors import LoggedView
from slash_commands import command_paths, plex
from ui.embeds import Reply, card, set_thumbnail
from ui.format import plural, truncate
from ui.images import mascot
from ui.theme import Limits, brand_colour

logger = logging.getLogger("plexbot.utility_commands")
logger.setLevel(logging.INFO)

# (key, emoji, name, summary). Cogs opt in with a `help_category` class attribute.
CATEGORIES = [
    ("watching", "\U0001F3AC", "Watching", "Who's streaming right now and how the server is doing"),
    ("library", "\U0001F4DA", "Library", "Look things up, get a random pick, see what's new"),
    ("stats", "\U0001F3C6", "Stats", "Top watchers, watch history and what's trending"),
    ("charts", "\U0001F4CA", "Charts", "When and what people watch, as charts"),
    ("admin", "\U0001F6E0\uFE0F", "Admin", "Server admin tools"),
]

# Worked examples, keyed by the command's usage path.
EXAMPLES = {
    "/plex watchers": "/plex watchers",
    "/plex status": "/plex status",
    "/plexadmin logs": "/plexadmin logs level:Error search:tautulli",
}


@dataclass(frozen=True)
class HelpEntry:
    usage: str
    description: str
    category: str
    example: Optional[str] = None


def _slash_usage(path: str, command) -> str:
    options = getattr(command, "options", {}) or {}
    parts = [path] + [f"<{name}>" if option.required else f"[{name}]" for name, option in options.items()]
    return " ".join(parts)


def collect_help_entries(bot, prefix: str) -> list:
    """Help text generated from the commands themselves, so it can't drift out of date."""
    entries = []
    for path, command in command_paths(bot):
        cog_category = getattr(getattr(command, "parent_cog", None), "help_category", None)
        category = "admin" if path.startswith("/plexadmin") else cog_category
        if category is None:
            continue
        entries.append(HelpEntry(_slash_usage(path, command), command.description or "", category,
                                 EXAMPLES.get(path)))
    for cog in bot.cogs.values():
        category = getattr(cog, "help_category", None)
        if category is None:
            continue
        for command in cog.get_commands():
            if command.hidden:
                continue
            usage = f"{prefix}{command.name} {command.signature}".strip()
            description = (command.help or "").strip().split("\n")[0]
            entries.append(HelpEntry(usage, description, category, EXAMPLES.get(f"{prefix}{command.name}")))
    return entries


def visible_entries(entries, is_admin: bool) -> list:
    return [entry for entry in entries if is_admin or entry.category != "admin"]


def _categories_with(entries):
    return [c for c in CATEGORIES if any(entry.category == c[0] for entry in entries)]


def help_overview(entries) -> Reply:
    lines = [
        f"{emoji} **{name}** \u2014 {summary} \u00b7 {plural(sum(e.category == key for e in entries), 'command')}"
        for key, emoji, name, summary in _categories_with(entries)
    ]
    embed = card(
        title="PlexBot",
        description="What's on, what's popular and who's watching.\n\n" + "\n".join(lines),
        colour=brand_colour(),
    )
    embed.set_footer(text="Pick a category below \u00b7 type /plex to browse everything")
    art = mascot("mascot-wave")
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])


def help_category_page(entries, key: str) -> Reply:
    _key, emoji, name, summary = next(c for c in CATEGORIES if c[0] == key)
    blocks = []
    for entry in entries:
        if entry.category != key:
            continue
        block = f"`{entry.usage}`\n{entry.description}"
        if entry.example:
            block += f"\n-# e.g. `{entry.example}`"
        blocks.append(block)
    description = truncate(summary + "\n\n" + "\n\n".join(blocks), Limits.DESCRIPTION)
    return Reply([card(title=f"{emoji} {name}", description=description, colour=brand_colour())])


class HelpCategorySelect(nextcord.ui.StringSelect):
    def __init__(self, entries):
        options = [nextcord.SelectOption(label="Overview", value="overview", emoji="\U0001F3E0")]
        options += [
            nextcord.SelectOption(label=name, value=key, emoji=emoji, description=truncate(summary, 100))
            for key, emoji, name, summary in _categories_with(entries)
        ]
        super().__init__(placeholder="Choose a category\u2026", options=options)

    async def callback(self, interaction: nextcord.Interaction):
        view: HelpView = self.view
        key = self.values[0]
        reply = help_overview(view.entries) if key == "overview" else help_category_page(view.entries, key)
        reply.view = view
        await interaction.response.edit_message(**reply.edit_payload())


class HelpView(LoggedView):
    def __init__(self, entries):
        super().__init__(timeout=600)
        self.entries = entries
        self.add_item(HelpCategorySelect(entries))


class UtilityCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @plex.subcommand(name="help", description="Show what PlexBot can do")
    async def help(self, interaction: nextcord.Interaction):
        permissions = getattr(interaction.user, "guild_permissions", None)
        is_admin = bool(permissions and permissions.administrator)
        prefix = config.get("core", "prefix", "plex ")
        entries = visible_entries(collect_help_entries(self.bot, prefix), is_admin)
        reply = help_overview(entries)
        reply.view = HelpView(entries)
        await reply.send(interaction, ephemeral=True)


def setup(bot):
    bot.add_cog(UtilityCommands(bot))
```

- [ ] **Step 5: Run the suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass. The guard test covers `HelpView` and `HelpCategorySelect`. `test_slash_equivalent`-style hints now include `/plex help`.

- [ ] **Step 6: Commit**

```bash
git add cogs tests/test_help.py tests/test_slash_registration.py tests/test_ui_classes.py
git commit -m "Add /plex help: categories with a dropdown, generated from command metadata

Replaces the prefix help's wall of text. Admin commands are shown only to
admins; plex help now points to /plex help.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Error cards for slash commands and components

**Files:**
- Modify: `errors/__init__.py` (`application_error_handler`, `LoggedView.on_error`, `LoggedModal.on_error` use `ui.embeds`)
- Modify: `tests/test_error_logging.py` (assert the card content)

**Interfaces:**
- Consumes: `ui.embeds.unexpected_error(path)`, `ui.embeds.error_state`, `ui.embeds.ErrorKind`, `Reply.send`
- Produces: no new names

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_error_logging.py`:

```python
def test_view_error_card_is_the_oops_state(monkeypatch):
    import ui.embeds

    monkeypatch.setattr(ui.embeds, "mascot", lambda slot: None)
    sent = {}

    class Response(FakeResponse):
        async def send_message(self, content=None, *, ephemeral=False, **kwargs):
            sent.update(kwargs, ephemeral=ephemeral)

    async def run():
        interaction = FakeInteraction()
        interaction.response = Response()
        await LoggedView().on_error(ValueError("x"), nextcord.ui.Button(label="x"), interaction)

    asyncio.run(run())
    assert sent["ephemeral"] is True
    assert "Something went wrong" in sent["embeds"][0].description


def test_slash_error_names_the_command(monkeypatch):
    import ui.embeds
    from errors import ErrorHandler

    monkeypatch.setattr(ui.embeds, "mascot", lambda slot: None)
    sent = {}

    class Response(FakeResponse):
        async def send_message(self, content=None, *, ephemeral=False, **kwargs):
            sent.update(kwargs, ephemeral=ephemeral)

    async def run():
        bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None)
        interaction = FakeInteraction()
        interaction.response = Response()
        interaction.data = {"name": "plex", "options": [{"type": 1, "name": "status"}]}
        await ErrorHandler(bot).application_error_handler(interaction, nextcord.ApplicationInvokeError(RuntimeError("boom")))

    asyncio.run(run())
    assert sent["ephemeral"] is True and "`/plex status`" in sent["embeds"][0].description
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest -q tests/test_error_logging.py`
Expected: the two new tests fail, because `embeds` isn't in the sent kwargs (replies are plain text today).

- [ ] **Step 3: Implement.** In `errors/__init__.py`, import lazily inside the functions, to keep `errors` importable from `ui`-free contexts and avoid import cycles:

Replace `_reply_privately`'s body and both `on_error` methods with:

```python
async def _reply_privately(interaction: nextcord.Interaction, reply) -> None:
    try:
        await reply.send(interaction, ephemeral=True)
    except nextcord.HTTPException as e:
        logger.warning(f"Could not send error reply: {e}")


class LoggedView(nextcord.ui.View):
    """View whose callback errors are logged and answered privately.

    nextcord's default prints them to stderr, so they never reach plexbot.log and the
    user just sees "This interaction failed".
    """

    async def on_error(self, error: Exception, item: nextcord.ui.Item, interaction: nextcord.Interaction):
        from ui.embeds import unexpected_error

        logger.error(f"Error in {type(self).__name__} ({type(item).__name__}): {error}", exc_info=error)
        await _reply_privately(interaction, unexpected_error())


class LoggedModal(nextcord.ui.Modal):
    """Modal whose submit errors are logged and answered privately (see LoggedView)."""

    async def on_error(self, error: Exception, interaction: nextcord.Interaction):
        from ui.embeds import unexpected_error

        logger.error(f"Error in {type(self).__name__}: {error}", exc_info=error)
        await _reply_privately(interaction, unexpected_error())
```

Delete `_COMPONENT_ERROR_TEXT`. In `ErrorHandler.application_error_handler`, replace the embed construction and the `try/except` send block with:

```python
        from ui.embeds import ErrorKind, error_state, unexpected_error

        path = invoked_command_path(interaction)
        if category in (ErrorCategory.API_ERROR, ErrorCategory.NETWORK_ERROR):
            reply = error_state(ErrorKind.SERVICE_DOWN, f"Couldn't reach a service `{path}` depends on.",
                                hint="Try again in a minute.")
        else:
            reply = unexpected_error(path)
        try:
            await reply.send(interaction, ephemeral=True)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not send error reply for {path}: {e}")
```

The existing tests in `tests/test_error_logging.py` record `(content, ephemeral)` from `send_message` / `followup.send`. They keep passing because `Reply.send` passes `ephemeral=` and the embeds as keyword arguments.

- [ ] **Step 4: Run the suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add errors/__init__.py tests/test_error_logging.py
git commit -m "Use ui error cards for slash command, button and form failures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Mascot concepts (checkpoint)

**Files:**
- Create: `scripts/render_mascots.py`
- Scratch only, not committed: three concept SVGs and their PNGs

**Interfaces:**
- Produces: `python scripts/render_mascots.py [slot ...] [--src DIR] [--out DIR] [--size N]`, which renders `<src>/<slot>.svg` to `<out>/<slot>.png`

- [ ] **Step 1: Write `scripts/render_mascots.py`:**

```python
"""Render mascot SVGs to transparent PNGs with a headless Chromium browser. Dev-only.

Discord can't display SVG, so the bot ships PNGs; this regenerates them from the
SVG sources. Needs Edge or Chrome (set CHROME_PATH to override).

Usage: python scripts/render_mascots.py [slot ...] [--src assets/mascot] [--out assets/mascot] [--size 512]
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "assets" / "mascot"


def find_browser() -> str:
    candidates = [
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        shutil.which("microsoft-edge"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("No Edge/Chrome found; set CHROME_PATH.")


def render(browser: str, svg: Path, png: Path, size: int) -> None:
    subprocess.run(
        [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--default-background-color=00000000", f"--window-size={size},{size}",
            f"--screenshot={png.resolve()}", svg.resolve().as_uri(),
        ],
        check=True, capture_output=True, timeout=60,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slots", nargs="*", help="slot names (default: every SVG in --src)")
    parser.add_argument("--src", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()

    browser = find_browser()
    svgs = [args.src / f"{slot}.svg" for slot in args.slots] if args.slots else sorted(args.src.glob("*.svg"))
    args.out.mkdir(parents=True, exist_ok=True)
    for svg in svgs:
        png = args.out / f"{svg.stem}.png"
        render(browser, svg, png, args.size)
        print(f"{svg.name} -> {png}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Draw three concept SVGs** in the scratchpad directory, not the repo:
- `concept-reel.svg`: a round film reel with a face, little arms and legs
- `concept-kernel.svg`: a popcorn bucket with Plex-amber stripes and a face
- `concept-telly.svg`: a retro CRT TV with an antenna and a face on the screen

Each is 512×512 (`width="512" height="512" viewBox="0 0 512 512"`) with a transparent background. All three share this style:
- flat vector shapes
- thick rounded outlines in `#2B2D31`
- Plex amber `#E5A00D` as the main accent, with warm supporting colours
- one soft shadow
- a big, simple face, so it still reads at Discord's ~80 px thumbnail size

Each concept shows the mascot in a neutral, friendly pose.

- [ ] **Step 3: Render and inspect them.** Run `python scripts/render_mascots.py --src <scratch> --out <scratch>`. Then open each PNG and look at it, using an image viewer or the Read tool. Also downscale each to 80 px with Pillow (`Image.open(p).resize((80, 80)).save(...)`) and check it still reads as a face. Fix anything that's illegible, then re-render.

- [ ] **Step 4: Commit the render script only**

```bash
git add scripts/render_mascots.py
git commit -m "Add dev script to render mascot SVGs to PNG

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: STOP and ask the user** to pick a concept, or ask for changes. Show the three PNGs, at full size and at 80 px. Do not start Task 10 until they choose.

---

### Task 10: Mascot slot art and AI prompts

**Files:**
- Create: `assets/mascot/{mascot-wave,mascot-sleepy,mascot-server,mascot-unplugged,mascot-oops}.svg` and the matching `.png` files, plus `assets/mascot/PROMPTS.md`
- Create: `tests/test_mascot_assets.py`

**Interfaces:**
- Consumes: `ui.images.MASCOT_SLOTS`, `ui.images.MASCOT_DIR`, `scripts/render_mascots.py`

- [ ] **Step 1: Write the failing test** in `tests/test_mascot_assets.py`:

```python
"""Every mascot slot ships a PNG (what the bot sends), its SVG source and an AI prompt."""

import io

import pytest
from PIL import Image

from ui.images import MASCOT_DIR, MASCOT_SLOTS

PROMPTS = (MASCOT_DIR / "PROMPTS.md").read_text(encoding="utf-8") if (MASCOT_DIR / "PROMPTS.md").exists() else ""


@pytest.mark.parametrize("slot", MASCOT_SLOTS)
def test_slot_is_complete(slot):
    png = MASCOT_DIR / f"{slot}.png"
    assert png.exists() and (MASCOT_DIR / f"{slot}.svg").exists()
    assert f"## {slot}" in PROMPTS
    data = png.read_bytes()
    assert len(data) < 250_000  # sent with every response that uses it
    with Image.open(io.BytesIO(data)) as image:
        assert image.mode == "RGBA" and image.size[0] == image.size[1] <= 1024
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest -q tests/test_mascot_assets.py`
Expected: 5 failures (files missing).

- [ ] **Step 3: Draw the five slot SVGs** for the chosen concept, in the same style as the concept and with the same character in each:

| Slot | Pose |
|---|---|
| `mascot-wave` | waving hello, cheerful |
| `mascot-sleepy` | dozing on a little sofa, "z" bubbles |
| `mascot-server` | proudly standing beside a small server rack with green lights |
| `mascot-unplugged` | tangled in a cable, with an unplugged plug in hand, dizzy expression |
| `mascot-oops` | sheepish, scratching its head, one small sweat drop |

Render them with `python scripts/render_mascots.py`. Inspect each at full size and at 80 px, as in Task 9, and fix any that are illegible.

- [ ] **Step 4: Write `assets/mascot/PROMPTS.md`.** Start with a shared style preamble that matches the vector style: flat vector illustration, thick rounded dark outlines, Plex amber accent, warm palette, soft single shadow, transparent background, centred, square, readable at small sizes. Then add one `## <slot>` section per slot, each containing a complete prompt (preamble plus pose description). Add a line explaining that any slot can be replaced by saving a square transparent PNG (≤1024 px, <250 KB) over `<slot>.png`.

- [ ] **Step 5: Run the suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add assets/mascot tests/test_mascot_assets.py
git commit -m "Add mascot illustrations for help, empty, status and error states

Each slot has its SVG source, the rendered PNG the bot sends, and an AI prompt
in PROMPTS.md so it can be swapped for generated art without code changes.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Docs, CI, end-to-end check, push

**Files:**
- Modify: `README.md` (commands table: `/plex help`, the new `/plex watchers` flow, `/plex status`), `.github/workflows/ci.yml` (compile `ui` too), `.dockerignore` (exclude `scripts/`)

- [ ] **Step 1: Update CI.** In `.github/workflows/ci.yml`, change the compileall arguments line `config errors cogs` to `config errors cogs ui`.

- [ ] **Step 2: Update `.dockerignore`.** Under `# Not needed at runtime`, add `scripts/`. `assets/` must stay included; `*.md` already excludes `PROMPTS.md`.

- [ ] **Step 3: Update `README.md`:**
- **Commands table:**
  - replace the `plex help [command]` row with `| /plex help | Shows what the bot can do, by category (private) |`
  - change the `/plex watchers` row to `| /plex watchers [stop] [message] | Shows every current stream with a poster strip; pick one for private details (current frame, quality, device). Stream managers can stop streams from the details card or with stop: |`
  - change the `/plex status` row to `| /plex status | Server health: library totals, activity, Plex service status and available updates |`
- **Below the table,** add one sentence: "Responses use posters, avatars and a mascot instead of a fixed logo; mascot art lives in `assets/mascot/` (see `PROMPTS.md` to swap in your own)."

- [ ] **Step 4: Full verification**

Run: `python -m pytest -q && ruff check . && python -m compileall -q plexbot.py media_cache.py tautulli_wrapper.py utilities.py migrate_config.py slash_commands.py config errors cogs ui`
Expected: all pass, no output from compileall.

Then smoke-check startup the way production does:

```bash
python - <<'EOF'
import asyncio, nextcord, plexbot
from types import SimpleNamespace
async def fake(): return {"tautulli": SimpleNamespace(initialize=lambda: None, tautulli_ip="h", use_https=False, api_key="k"), "tmdb": None, "media_cache": SimpleNamespace()}
plexbot.config.initialize(); plexbot.initialize_resources = fake
bot = plexbot.PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None, default_guild_ids=[1])
async def main():
    await bot.setup_hook()
    from slash_commands import command_paths
    print(sorted(p for p, _ in command_paths(bot)))
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task(): t.cancel()
asyncio.run(main())
EOF
```

Expected: the printed paths include `/plex help`, `/plex status`, `/plex watchers` and `/plexadmin logs`, with no traceback. Cog-loading errors for other cogs caused by the stub resources are acceptable only if they also happen on `main` before this branch; check with `git stash`.

- [ ] **Step 5: Commit and push**

```bash
git add README.md .github/workflows/ci.yml .dockerignore
git commit -m "Document the redesigned commands; compile ui in CI; keep scripts out of the image

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push origin main
```

Then wait for CI: `gh run watch <id> --exit-status`. CI must pass.

- [ ] **Step 6: Hand the live checklist to the user.** These can't be tested offline; take them from the spec:
1. In `/plex watchers` → Stream details, a stream from a library with video preview thumbnails shows the current frame; one without shows the background art.
2. An error after a public "thinking…" (for example, stop Tautulli, then run `/plex status`) arrives privately, and the placeholder disappears.
3. The new cards render well on Discord desktop and mobile. Also check the poster strip with 1 stream and with several.
4. Stopping a stream from its details card swaps the card for the confirmation, and the public overview updates about 2 seconds later.
