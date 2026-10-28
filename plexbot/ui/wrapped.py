"""/plex wrapped's slides: one 1200×750 image per part of someone's year, in the "Plex cinema" look.

Dark background, a soft gold glow, huge condensed headlines (Bebas Neue, in assets/fonts) and posters
edged in gold. Drawn with Pillow; call story() in a worker thread.
"""

import calendar
import io
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import matplotlib
from matplotlib.ft2font import FT2Font
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

from plexbot.ui.cards import genre_label
from plexbot.ui.format import hour_label, plural
from plexbot.ui.images import Attachment, dominant_colour, mascot
from plexbot.wrapped_year import Moment, Pick, WrappedYear

SIZE = W, H = 1200, 750
_X = 70  # left margin
BACKGROUND = (0x17, 0x18, 0x1B)
GOLD, GOLD_LIGHT = (0xE5, 0xA0, 0x0D), (0xF5, 0xC0, 0x4A)
WHITE, BODY, MUTED = (255, 255, 255), (0xC9, 0xCC, 0xD1), (0x94, 0x9B, 0xA4)
PANEL, BAR = (0x2B, 0x2D, 0x31), (0x3F, 0x41, 0x47)

BEBAS = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "BebasNeue-Regular.ttf"
_DEJAVU = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
DEJAVU, DEJAVU_BOLD = _DEJAVU / "DejaVuSans.ttf", _DEJAVU / "DejaVuSans-Bold.ttf"

Font = ImageFont.FreeTypeFont


@lru_cache(maxsize=None)
def _charmap(path: Path) -> frozenset:
    return frozenset(FT2Font(str(path)).get_charmap())


@lru_cache(maxsize=256)
def _font(path: Path, size: int) -> Font:
    return ImageFont.truetype(str(path), size)


def headline_font(text: str, size: int) -> Font:
    """Bebas Neue when it has every character of `text`, else DejaVu Sans Bold.

    shortcut: DejaVu has no Chinese, Japanese or Korean, so those titles draw as boxes; bundle a CJK
    font if servers with such titles ask.
    """
    covered = _charmap(BEBAS)
    path = BEBAS if all(ord(c) in covered for c in text if not c.isspace()) else DEJAVU_BOLD
    return _font(path, size)


def _body(size: int, bold: bool = False) -> Font:
    return _font(DEJAVU_BOLD if bold else DEJAVU, size)


def _ellipsize(text: str, font: Font, width: int) -> str:
    while text and font.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(text: str, font: Font, width: int) -> List[str]:
    """Greedy word wrap at spaces (not non-breaking ones); a word wider than `width` is broken between
    characters, and a short last line takes the word before it along when that fits."""
    lines = _greedy(text, font, width)
    if len(lines) >= 2 and font.getlength(lines[-1]) < width / 3 and " " in lines[-2]:
        head, moved = lines[-2].rsplit(" ", 1)
        if font.getlength(f"{moved} {lines[-1]}") <= width:
            lines[-2:] = [head, f"{moved} {lines[-1]}"]
    return lines


def _greedy(text: str, font: Font, width: int) -> List[str]:
    lines, line = [], ""
    for word in filter(None, text.split(" ")):
        candidate = f"{line} {word}" if line else word
        if font.getlength(candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ""
        for char in word:
            if font.getlength(line + char) > width and line:
                lines.append(line)
                line = ""
            line += char
    return lines + [line] if line else lines


def fit_lines(text: str, width: int, size: int, min_size: int, *, lines: int = 2,
              font: Callable[[str, int], Font] = headline_font) -> Tuple[Font, List[str]]:
    """The largest size down to `min_size` that fits `text` on one line; else wrapped at `min_size` onto
    up to `lines` lines, the last ending in "…" when text is left over."""
    while size > min_size and font(text, size).getlength(text) > width:
        size = max(min_size, size - 6)
    chosen = font(text, size)
    if chosen.getlength(text) <= width:
        return chosen, [text]
    wrapped = _wrap(text, chosen, width)
    if len(wrapped) > lines:
        wrapped = wrapped[:lines - 1] + [_ellipsize(" ".join(wrapped[lines - 1:]), chosen, width)]
    return chosen, wrapped


# --- drawing helpers ----------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _glow() -> Image.Image:
    """The soft gold glow behind a slide's hero, as a mask (blurred small, then scaled up: much faster)."""
    small = Image.new("L", (W // 4, H // 4), 0)
    ImageDraw.Draw(small).ellipse((W // 4 * 0.62, H // 4 * 0.0, W // 4 * 0.98, H // 4 * 0.62), fill=150)
    return small.filter(ImageFilter.GaussianBlur(28)).resize(SIZE, Image.BILINEAR)


def _canvas(tint: Optional[int] = None) -> Image.Image:
    """The background: dark with the gold glow, or, for a poster slide, fading into the poster's colour."""
    image = Image.new("RGB", SIZE, BACKGROUND)
    if tint is None:
        image.paste(Image.new("RGB", SIZE, GOLD), mask=_glow())
    else:
        colour = tuple(int(((tint >> shift) & 0xFF) * 0.55) for shift in (16, 8, 0))
        ramp = [0 if x < W * 0.4 else int(230 * (x - W * 0.4) / (W * 0.6)) for x in range(W)]
        mask = Image.new("L", (W, 1))
        mask.putdata(ramp)
        image.paste(Image.new("RGB", SIZE, colour), mask=mask.resize(SIZE))
    # The gold bar along the bottom edge.
    bar = Image.new("RGB", (2, 1))
    bar.putdata([GOLD, GOLD_LIGHT])
    image.paste(bar.resize((W, 14), Image.BILINEAR), (0, H - 14))
    return image


def _tracked(draw: ImageDraw.ImageDraw, xy, text: str, font: Font, fill, tracking: int) -> None:
    """Text with extra space between letters, for the small labels."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += font.getlength(char) + tracking


def _label(draw: ImageDraw.ImageDraw, text: str, x: int = _X, y: int = 56) -> None:
    _tracked(draw, (x, y), text, headline_font(text, 52), GOLD, 4)


def _hero(draw: ImageDraw.ImageDraw, text: str, y: int, width: int, size: int, min_size: int,
          lines: int = 2, x: int = _X, fill=WHITE) -> int:
    """Huge headline text, fitted to `width`; returns the y just below it."""
    font, rows = fit_lines(text.upper(), width, size, min_size, lines=lines)
    step = round(font.size * (0.9 if font.path.endswith(BEBAS.name) else 1.15))
    for row in rows:
        draw.text((x, y), row, font=font, fill=fill)
        y += step
    return y


def body_lines(text: str, width: int, size: int) -> List[str]:
    """Body text wrapped to `width` on up to three lines, the last ending in "…" when text is left over."""
    return fit_lines(text, width, size, size, lines=3, font=lambda _text, points: _body(points))[1]


def _lines(draw: ImageDraw.ImageDraw, rows: Sequence[Tuple[str, tuple]], y: int, width: int,
           size: int = 34, x: int = _X) -> int:
    """Body text, each row wrapped to `width` in its own colour; returns the y below it."""
    font = _body(size)
    for text, fill in rows:
        for row in body_lines(text, width, size):
            draw.text((x, y), row, font=font, fill=fill)
            y += round(size * 1.4)
    return y


def _open(data: Optional[bytes]) -> Optional[Image.Image]:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _rounded(size: Tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _poster_tile(data: Optional[bytes], size: Tuple[int, int], outline: int = 7) -> Image.Image:
    """A poster edged in gold, with a soft shadow, on a transparent tile a little bigger than `size`."""
    pad = 30
    tile = Image.new("RGBA", (size[0] + pad * 2, size[1] + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("L", tile.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 12, pad + size[0], pad + size[1] + 12), radius=16,
                                             fill=200)
    tile.paste(Image.new("RGBA", tile.size, (0, 0, 0, 255)), mask=shadow.filter(ImageFilter.GaussianBlur(14)))
    poster = _open(data)
    face = ImageOps.fit(poster, size) if poster else Image.new("RGB", size, PANEL)
    border = Image.new("RGB", (size[0] + outline * 2, size[1] + outline * 2), GOLD)
    border.paste(face, (outline, outline), mask=_rounded(size, 10))
    tile.paste(border, (pad - outline, pad - outline), mask=_rounded(border.size, 14))
    return tile


def _paste_poster(image: Image.Image, data: Optional[bytes], xy: Tuple[int, int], size: Tuple[int, int],
                  angle: float = 0) -> None:
    tile = _poster_tile(data, size)
    if angle:
        tile = tile.rotate(angle, resample=Image.BICUBIC, expand=True)
    x, y = xy
    image.paste(tile, (x - 30 - (tile.width - size[0] - 60) // 2, y - 30 - (tile.height - size[1] - 60) // 2),
                tile)


def _kernel(image: Image.Image, slot: str, xy: Tuple[int, int], size: int) -> None:
    art = mascot(slot)
    if art:
        with Image.open(io.BytesIO(art.data)) as kernel:
            kernel = kernel.convert("RGBA").resize((size, size), Image.LANCZOS)
        image.paste(kernel, xy, kernel)


def _day(day) -> str:
    return f"{day:%a} {day.day} {day:%b}"  # "14 Mar" never splits across lines


def duration_words(seconds: int) -> str:
    """"20 minutes" under an hour, else whole hours: "1 hour", "32 hours"."""
    minutes = round(seconds / 60)
    return plural(max(1, minutes), "minute") if minutes < 60 else plural(round(seconds / 3600), "hour")


def hours_headline(seconds: int) -> str:
    """"1 HR", "312 HRS"."""
    hours = round(seconds / 3600)
    return f"{hours:,} HR" if hours == 1 else f"{hours:,} HRS"


def _tint(data: Optional[bytes]) -> Optional[int]:
    return dominant_colour(data) if data else None


# --- slides -------------------------------------------------------------------------------------

_POSTER = (300, 450)
_POSTER_XY = (W - _POSTER[0] - 90, 120)
_BESIDE_POSTER = _POSTER_XY[0] - _X - 60  # text width left of a poster


def _intro(year: WrappedYear, name: str, **_) -> Image.Image:
    image = _canvas()
    _kernel(image, "mascot-party", (W - 520, 140), 490)
    draw = ImageDraw.Draw(image)
    _label(draw, "PLEX WRAPPED")
    _hero(draw, f"{name}'s {year.year}", 140, 590, 230, 110)
    _lines(draw, [("Let's see what you watched ▶", BODY)], H - 150, 640, size=38)
    return image


def _watch_time(year: WrappedYear, **_) -> Image.Image:
    image = _canvas()
    draw = ImageDraw.Draw(image)
    _label(draw, "YOU WATCHED")
    y = _hero(draw, hours_headline(year.seconds), 130, W - 2 * _X, 330, 150, lines=1)
    rows = []
    if year.seconds >= 2 * 86400:
        rows.append((f"That's {round(year.seconds / 86400)} days nonstop.", BODY))
    if year.rank == 1:
        rows.append(("Nobody on the server watched more.", GOLD_LIGHT))
    elif year.rank == 2:
        rows.append(("Only one person on the server watched more.", GOLD_LIGHT))
    else:
        rows.append((f"#{year.rank} of {year.viewers} on the server.", GOLD_LIGHT))
    _lines(draw, rows, max(y + 20, 470), W - 2 * _X, size=40)
    return image


def _poster_slide(label: str, pick: Pick, rows, posters: Dict[str, bytes]) -> Image.Image:
    data = posters.get(pick.poster)
    image = _canvas(_tint(data))
    _paste_poster(image, data, _POSTER_XY, _POSTER)
    draw = ImageDraw.Draw(image)
    _label(draw, label)
    y = _hero(draw, pick.title, 130, _BESIDE_POSTER, 170, 84)
    _lines(draw, rows, max(y + 24, 400), _BESIDE_POSTER)
    return image


def _top_show(year: WrappedYear, posters, **_) -> Optional[Image.Image]:
    top = year.top_show
    if not top:
        return None
    rows = [(f"{plural(top.episodes, 'episode')} · {duration_words(top.show.seconds)}", BODY)]
    if top.binge_episodes >= 2:
        rows.append((f"Biggest night: {top.binge_episodes} episodes on {_day(top.binge_day)}.", GOLD_LIGHT))
    return _poster_slide("YOUR #1 SHOW", top.show, rows, posters)


def _top_movie(year: WrappedYear, posters, **_) -> Optional[Image.Image]:
    top = year.top_movie
    if not top:
        return None
    first = f"Watched {top.finished} times." if top.finished >= 2 else "Your most-watched movie."
    rows = [(first, GOLD_LIGHT), (f"{plural(top.movies, 'movie')} this year in all.", BODY)]
    return _poster_slide("YOUR #1 MOVIE", top.movie, rows, posters)


def _top_genre(year: WrappedYear, posters, **_) -> Optional[Image.Image]:
    if not year.genres:
        return None
    image = _canvas()
    for (x, y, angle), key in zip(((760, 150, 8), (880, 120, -2), (1000, 160, -9)), year.genre_posters):
        _paste_poster(image, posters.get(key), (x, y), (150, 225), angle)
    draw = ImageDraw.Draw(image)
    _label(draw, "YOUR VIBE")
    y = _hero(draw, genre_label(year.genres[0]), 130, 620, 280, 110)
    rows = []
    rest = [genre_label(genre) for genre in year.genres[1:3]]
    if rest:
        rows.append((f"Then {' and '.join(rest)}.", GOLD_LIGHT))
    rows.append((f"{plural(year.genre_titles, 'title')} this year.", BODY))
    _lines(draw, rows, max(y + 24, 450), 620)
    return image


def _when(year: WrappedYear, **_) -> Image.Image:
    image = _canvas()
    draw = ImageDraw.Draw(image)
    _label(draw, "PEAK HOUR")
    y = _hero(draw, hour_label(year.peak_hour), 130, 560, 300, 150, lines=1)
    label, line = year.habit
    _lines(draw, [(label, GOLD_LIGHT), (line, BODY)], max(y + 24, 440), 540)
    # Plays per hour, the peak in gold.
    left, right, bottom, height, gap = 680, W - _X, 600, 380, 5
    width = (right - left - gap * 23) / 24
    most = max(year.hours) or 1
    for hour, count in enumerate(year.hours):
        x = left + hour * (width + gap)
        top = bottom - max(6, height * count / most)
        draw.rounded_rectangle((x, top, x + width, bottom), radius=4,
                               fill=GOLD_LIGHT if hour == year.peak_hour else BAR)
    tick = _body(22)
    for hour in (0, 6, 12, 18):
        draw.text((left + hour * (width + gap), bottom + 14), hour_label(hour), font=tick, fill=MUTED)
    return image


def _moment(image: Image.Image, draw: ImageDraw.ImageDraw, heading: str, moment: Moment, x: int,
            posters) -> None:
    _paste_poster(image, posters.get(moment.poster), (x, 150), (200, 300))
    _tracked(draw, (x + 240, 160), heading, headline_font(heading, 48), GOLD, 3)
    y = _hero(draw, moment.title, 220, 270, 64, 40, lines=3, x=x + 240)
    _lines(draw, [(_day(moment.day), BODY)], y + 10, 270, size=30, x=x + 240)


def _firsts(year: WrappedYear, posters, **_) -> Image.Image:
    image = _canvas()
    draw = ImageDraw.Draw(image)
    _label(draw, "HOW YOUR YEAR WENT")
    _moment(image, draw, "FIRST", year.first, _X, posters)
    _moment(image, draw, "LATEST", year.last, 620, posters)
    month = calendar.month_name[year.busiest_month]
    _lines(draw, [(f"Busiest month: {month}, {duration_words(year.busiest_month_seconds)}.",
                   GOLD_LIGHT)], H - 130, W - 2 * _X, size=36)
    return image


def _personality(year: WrappedYear, **_) -> Image.Image:
    image = _canvas()
    _kernel(image, "mascot-trophy", (W - 540, 140), 500)
    draw = ImageDraw.Draw(image)
    _label(draw, "YOU ARE")
    title, line = year.personality
    y = _hero(draw, title, 130, 640, 230, 110)
    _lines(draw, [(line, BODY)], max(y + 24, 520), 600, size=36)
    return image


def _avatar(image: Image.Image, data: Optional[bytes], name: str, xy: Tuple[int, int], size: int) -> None:
    ring = Image.new("RGB", (size + 12, size + 12), GOLD)
    face = _open(data)
    if face:
        ring.paste(ImageOps.fit(face, (size, size)), (6, 6), mask=_rounded((size, size), size // 2))
    else:
        initial = (name.strip()[:1] or "?").upper()
        draw = ImageDraw.Draw(ring)
        draw.text((ring.width / 2, ring.height / 2), initial, font=headline_font(initial, size // 2 + 20),
                  fill=BACKGROUND, anchor="mm")
    image.paste(ring, xy, mask=_rounded(ring.size, ring.width // 2))


def _summary(year: WrappedYear, name: str, avatar: Optional[bytes], posters, **_) -> Image.Image:
    image = _canvas()
    _avatar(image, avatar, name, (_X, 50), 120)
    draw = ImageDraw.Draw(image)
    _hero(draw, f"{name} · {year.year}", 66, W - _X - 230, 130, 60, lines=1, x=230)
    small = _body(22)
    for n, pick in enumerate(year.top5):
        x = _X + n * 215
        _paste_poster(image, posters.get(pick.poster), (x, 215), (170, 255))
        draw.text((x, 490), f"{n + 1}", font=headline_font("1", 40), fill=GOLD)
        title = pick.title if small.getlength(pick.title) <= 165 else _ellipsize(pick.title, small, 165)
        draw.text((x + 30, 500), title, font=small, fill=BODY)
    columns = (
        ("WATCHED", f"{hours_headline(year.seconds)} · #{year.rank} OF {year.viewers}"),
        ("TOP GENRE", genre_label(year.genres[0]) if year.genres else "—"),
        ("PERSONALITY", year.personality[0]),
    )
    for n, (label, value) in enumerate(columns):
        x = _X + n * 370
        _tracked(draw, (x, 570), label, _body(20, bold=True), MUTED, 2)
        _hero(draw, value, 600, 340, 72, 40, lines=1, x=x)
    return image


_SLIDES = (_intro, _watch_time, _top_show, _top_movie, _top_genre, _when, _firsts, _personality, _summary)


def story(year: WrappedYear, *, name: str, avatar: Optional[bytes], posters: Dict[str, bytes]) -> List[Attachment]:
    """Every slide with something to say, in order, as wrapped-1.png onwards; the last is the summary."""
    images = [slide(year, name=name, avatar=avatar, posters=posters) for slide in _SLIDES]
    attachments = []
    for image in images:
        if image is None:
            continue
        out = io.BytesIO()
        image.save(out, format="PNG")
        attachments.append(Attachment(f"wrapped-{len(attachments) + 1}.png", out.getvalue()))
    return attachments
