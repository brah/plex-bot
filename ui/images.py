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
    fallback: Optional[str] = None,
) -> Optional[Attachment]:
    """Fetch a Plex image (poster, art, preview frame) through Tautulli's image proxy.

    A missing or failed image means no image, never a placeholder, so callers opt into a
    Tautulli substitute (fallback="poster") only deliberately.
    """
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
    return await plex_image(tautulli, key, filename=filename, width=640, height=360)


def poster_strip(
    posters: Sequence[bytes], *, extra: int = 0, filename: str = "posters.png",
    height: int = 300, gap: int = 8,
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
        draw.text(
            (x + extra_width / 2, height / 2), f"+{extra}",
            font=font, fill=(255, 255, 255, 255), anchor="mm",
        )

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
