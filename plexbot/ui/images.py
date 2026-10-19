"""Every image the bot sends: posters, art, a stream's current frame, poster strips, mascots.

Each helper returns an Attachment (sent as a file, referenced as attachment://name) or
None when the image isn't available; callers simply leave that image out.
"""

import asyncio
import io
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import nextcord
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from plexbot.ui.format import is_truthy

logger = logging.getLogger("plexbot.ui.images")

MASCOT_DIR = Path(__file__).resolve().parents[2] / "assets" / "mascot"
MASCOT_SLOTS = (
    "mascot-wave", "mascot-sleepy", "mascot-server", "mascot-unplugged", "mascot-oops", "mascot-downloading",
)
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
    fallback: Optional[str] = None,
) -> Optional[Attachment]:
    """Fetch a Plex image (poster, art, preview frame) through Tautulli's image proxy.

    A missing or failed image means no image, never a placeholder, so callers opt into a
    Tautulli substitute (fallback="poster") only deliberately.
    """
    if not key:
        return None
    try:
        data = await asyncio.wait_for(
            tautulli.get_image(key, width, height, fallback=fallback), timeout=IMAGE_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning(f"Timed out fetching Plex image {key}")
        return None
    return Attachment(filename, data) if data else None


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
    return await plex_image(tautulli, key, filename=filename, width=640, height=360)


STRIP_POSTERS = 12  # two rows of six; Discord scales a third row's taller image down, shrinking every poster
_ROW = 6


_TILE = (32, 34, 37, 255)  # Discord's dark grey: the "+N" and section tiles


def _tiles(posters: Sequence[bytes], height: int) -> List[Image.Image]:
    """Each readable poster at a common height; unreadable ones are left out."""
    tiles = []
    for data in posters:
        try:
            with Image.open(io.BytesIO(data)) as image:
                rgba = image.convert("RGBA")
        except (UnidentifiedImageError, OSError, ValueError):
            continue
        width = max(1, round(rgba.width * height / rgba.height))
        tiles.append(rgba.resize((width, height), Image.Resampling.LANCZOS))
    return tiles


def _compose(rows: Sequence[Sequence[Image.Image]], *, filename: str, height: int, gap: int) -> Attachment:
    """Rows of equal-height tiles, left-aligned, on a transparent canvas."""
    width = max(sum(tile.width for tile in row) + gap * (len(row) - 1) for row in rows)
    canvas = Image.new("RGBA", (width, len(rows) * (height + gap) - gap), (0, 0, 0, 0))
    for r, row in enumerate(rows):
        x = 0
        for tile in row:
            canvas.paste(tile, (x, r * (height + gap)))
            x += tile.width + gap
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return Attachment(filename, out.getvalue())


def poster_strip(
    posters: Sequence[bytes], *, extra: int = 0, filename: str = "posters.png",
    height: int = 300, gap: int = 8,
) -> Optional[Attachment]:
    """Posters at a common height, in rows of at most six split evenly, plus a "+N" tile when more were left out."""
    tiles = _tiles(posters, height)
    if not tiles:
        return None
    if extra:
        tile = Image.new("RGBA", (round(height * 2 / 3), height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        draw.rounded_rectangle((0, 0, tile.width - 1, height - 1), radius=16, fill=_TILE)
        draw.text((tile.width / 2, height / 2), f"+{extra}", font=ImageFont.load_default(size=height // 5),
                  fill=(255, 255, 255, 255), anchor="mm")
        tiles.append(tile)

    per_row = math.ceil(len(tiles) / math.ceil(len(tiles) / _ROW))  # 7 tiles -> 4 + 3, not 6 + 1
    rows = [tiles[i:i + per_row] for i in range(0, len(tiles), per_row)]
    return _compose(rows, filename=filename, height=height, gap=gap)


def section_tile(heading: str, colour: int, height: int = 300) -> Image.Image:
    """A poster-sized divider reading "TOP" over the heading, edged in the section's colour."""
    width = round(height * 2 / 3)
    rgb = ((colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF, 255)
    tile = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=16, fill=_TILE, outline=rgb, width=5)
    draw.text((width / 2, height * 0.40), "TOP", font=ImageFont.load_default(size=height // 10), fill=rgb,
              anchor="mm")
    heading_font = ImageFont.load_default(size=height // 7)
    while heading_font.getlength(heading) > width * 0.85:  # a long heading shrinks to fit the tile
        heading_font = ImageFont.load_default(size=heading_font.size - 2)
    draw.text((width / 2, height * 0.55), heading, font=heading_font, fill=(255, 255, 255, 255), anchor="mm")
    # An arrow pointing along the row (drawn: the built-in font has no arrow glyph).
    y, half = height * 0.71, height / 30
    draw.line((width * 0.38, y, width * 0.58, y), fill=rgb, width=max(2, height // 60))
    draw.polygon([(width * 0.62, y), (width * 0.56, y - half), (width * 0.56, y + half)], fill=rgb)
    return tile


def sectioned_strip(
    sections: Sequence[Tuple[str, int, Sequence[bytes]]], *, filename: str = "posters.png",
    height: int = 300, gap: int = 8,
) -> Optional[Attachment]:
    """A row per (heading, colour, posters) section: its section_tile, then its posters. Sections with no
    readable poster are left out."""
    rows = []
    for heading, colour, posters in sections:
        tiles = _tiles(posters, height)
        if tiles:
            rows.append([section_tile(heading, colour, height), *tiles])
    return _compose(rows, filename=filename, height=height, gap=gap) if rows else None


async def _fetch_posters(tautulli, keys: Sequence[Optional[str]]) -> List[bytes]:
    posters = await asyncio.gather(*(
        plex_image(tautulli, key, filename=f"poster-{i}.jpg", width=200, height=300) for i, key in enumerate(keys)
    ))
    return [p.data for p in posters if p]


async def fetch_poster_strip(tautulli, keys: Sequence[Optional[str]], *, extra: int = 0) -> Optional[Attachment]:
    """The poster strip for the first STRIP_POSTERS image keys (missing posters are left out), drawn
    in a worker thread so it never blocks the bot; None when no poster could be fetched."""
    posters = await _fetch_posters(tautulli, keys[:STRIP_POSTERS])
    return await asyncio.to_thread(poster_strip, posters, extra=extra)


async def fetch_sectioned_strip(tautulli, sections: Sequence[Tuple[str, int, Sequence[Optional[str]]]]
                                ) -> Optional[Attachment]:
    """sectioned_strip for (heading, colour, image keys) sections, fetched together and drawn in a worker
    thread; None when no poster could be fetched."""
    fetched = await asyncio.gather(*(_fetch_posters(tautulli, keys) for _heading, _colour, keys in sections))
    return await asyncio.to_thread(
        sectioned_strip, [(heading, colour, posters) for (heading, colour, _keys), posters in zip(sections, fetched)]
    )


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
