"""Chart images for /plex chart, drawn with matplotlib's object API (posters with Pillow).

The object API (Figure, not pyplot) keeps no global state, so charts can be drawn in a worker
thread: call these through asyncio.to_thread so drawing never blocks the bot. Each returns an
Attachment, or None when there is nothing to draw.
"""

import io
import textwrap
from datetime import date
from typing import Dict, List, Optional, Sequence

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from matplotlib.ticker import MaxNLocator
from PIL import Image, UnidentifiedImageError

from plexbot.config import config
from plexbot.ui.images import Attachment

TEXT = "#DBDEE1"
MUTED = "#949BA4"
GRID = "#3F4147"
PEAK = "#F5C04A"  # the tallest bar, so the answer stands out


def _accent() -> str:
    return config.get("colors", "plex_orange", "#E5A00D")


def _figure():
    background = config.get("colors", "plex_grey_dark", "#1B1B1B")
    fig = Figure(
        # Discord shows an embed image about 430px wide, so a small canvas at a high dpi keeps text readable.
        figsize=(config.get("charts", "width", 7), config.get("charts", "height", 3.5)),
        dpi=config.get("charts", "dpi", 200),
        facecolor=background,
    )
    ax = fig.add_subplot()
    ax.set_facecolor(background)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=12, length=0, pad=8)
    ax.set_axisbelow(True)
    return fig, ax


def _save(fig: Figure, filename: str) -> Attachment:
    out = io.BytesIO()
    fig.savefig(out, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.3)
    return Attachment(filename, out.getvalue())


def bar_chart(labels: Sequence[str], values: Sequence[int], *, filename: str, horizontal: bool = False,
              label_every: int = 1, unit: str = "Plays") -> Optional[Attachment]:
    """Bars with the peak highlighted. label_every thins out crowded category labels."""
    if not values or not any(values):
        return None
    fig, ax = _figure()
    peak = max(values)
    colours = [PEAK if value == peak else _accent() for value in values]
    positions = list(range(len(values)))
    shown = [label if i % label_every == 0 else "" for i, label in enumerate(labels)]
    if horizontal:
        ax.barh(positions, values, color=colours, height=0.6)
        ax.set_yticks(positions, shown)
        ax.invert_yaxis()  # the biggest at the top
        ax.xaxis.grid(True, color=GRID, linewidth=0.8)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_xlabel(unit, color=MUTED, fontsize=12)
        ax.tick_params(axis="y", colors=TEXT, labelsize=13)
    else:
        ax.bar(positions, values, color=colours, width=0.7)
        ax.set_xticks(positions, shown)
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_ylabel(unit, color=MUTED, fontsize=12)
    return _save(fig, filename)


POSTER_WIDTH = 44  # points: the poster under each bar of a poster chart
POSTER_SLOTS = 8  # bars a poster chart has room for; fewer keep the same width, from the left


def _poster(data: Optional[bytes]) -> Optional[Image.Image]:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def poster_chart(values: Sequence[float], posters: Sequence[Optional[bytes]], names: Sequence[str],
                 bar_labels: Sequence[str], *, filename: str, unit: str = "Plays") -> Optional[Attachment]:
    """A bar per title with its poster beneath (its name where there's no poster) and its value on top."""
    if not values or not any(values):
        return None
    fig, ax = _figure()
    peak = max(values)
    positions = list(range(len(values)))
    bars = ax.bar(positions, values, color=[PEAK if value == peak else _accent() for value in values], width=0.7)
    ax.bar_label(bars, labels=bar_labels, color=TEXT, fontsize=12, padding=3)
    ax.set_xticks(positions, [""] * len(values))
    ax.set_xlim(-0.5, max(len(values), POSTER_SLOTS) - 0.5)
    ax.set_ylim(0, peak * 1.15)  # room for the top bar's label
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel(unit, color=MUTED, fontsize=12)
    under = {"xycoords": ("data", "axes fraction"), "boxcoords": "offset points"}
    for x, (data, name) in enumerate(zip(posters, names)):
        poster = _poster(data)
        if poster:
            # OffsetImage draws one image pixel per point, so zoom sets the width in points.
            box = OffsetImage(poster, zoom=POSTER_WIDTH / poster.width)
            ax.add_artist(AnnotationBbox(box, (x, 0), xybox=(0, -6), box_alignment=(0.5, 1), frameon=False,
                                         pad=0, **under))
        else:
            ax.annotate(textwrap.fill(name, 10, max_lines=4), (x, 0), xycoords=under["xycoords"],
                        xytext=(0, -6), textcoords="offset points", ha="center", va="top", color=MUTED,
                        fontsize=9)
    return _save(fig, filename)


def stacked_day_chart(days: Sequence[date], series: Dict[str, List[int]], colours: Dict[str, str], *,
                      filename: str, width: float = 0.8, unit: str = "Plays") -> Optional[Attachment]:
    """One stacked bar per date (a segment per series), with a legend; empty series are left out.
    width is in days: 0.8 for daily bars, 5.6 for weekly ones."""
    drawn = {name: values for name, values in series.items() if any(values)}
    if not days or not drawn:
        return None
    fig, ax = _figure()
    bottoms = [0] * len(days)
    for name, values in drawn.items():
        ax.bar(days, values, bottom=bottoms, label=name, color=colours.get(name, TEXT), width=width)
        bottoms = [b + v for b, v in zip(bottoms, values)]
    _date_axis(ax, unit, len(drawn))
    return _save(fig, filename)


def line_chart(days: Sequence[date], series: Dict[str, List[int]], colours: Dict[str, str], *,
               filename: str, unit: str) -> Optional[Attachment]:
    """A line per series over dates, from zero, with a legend; series that stay at zero are left out."""
    drawn = {name: values for name, values in series.items() if any(values)}
    if not days or not drawn:
        return None
    fig, ax = _figure()
    for name, values in drawn.items():
        # Steps, not slopes: a count changes on the day something is added, not gradually.
        ax.plot(days, values, label=name, color=colours.get(name, TEXT), linewidth=2.5, drawstyle="steps-post")
    ax.set_ylim(bottom=0)
    _date_axis(ax, unit, len(drawn))
    return _save(fig, filename)


def _date_axis(ax, unit: str, series: int) -> None:
    """Dates along the bottom, a gridded count up the side, and the legend above the plot."""
    locator = AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    ax.xaxis.get_offset_text().set_visible(False)  # "2026-Oct" in the corner; the embed says the window
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel(unit, color=MUTED, fontsize=12)
    # Above the plot, so it never sits on a bar or a line.
    ax.legend(frameon=False, labelcolor=TEXT, fontsize=12, loc="lower left", bbox_to_anchor=(0, 1),
              ncols=series, borderaxespad=0.2)
