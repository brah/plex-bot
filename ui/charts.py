# ui/charts.py

"""Chart images for /plex chart, drawn with matplotlib's object API.

The object API (Figure, not pyplot) keeps no global state, so charts can be drawn in a worker
thread: call these through asyncio.to_thread so drawing never blocks the bot. Each returns an
Attachment, or None when there is nothing to draw.
"""

import io
from datetime import date
from typing import Dict, List, Optional, Sequence

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from config import config
from ui.images import Attachment

TEXT = "#DBDEE1"
MUTED = "#949BA4"
GRID = "#3F4147"
PEAK = "#F5C04A"  # the tallest bar, so the answer stands out


def _accent() -> str:
    return config.get("colors", "plex_orange", "#E5A00D")


def _figure():
    background = config.get("colors", "plex_grey_dark", "#1B1B1B")
    fig = Figure(
        figsize=(config.get("charts", "width", 14), config.get("charts", "height", 6)),
        dpi=config.get("charts", "dpi", 100),
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


def stacked_day_chart(days: Sequence[date], series: Dict[str, List[int]], colours: Dict[str, str], *,
                      filename: str) -> Optional[Attachment]:
    """One stacked bar per day (a segment per series), with a legend; empty series are left out."""
    drawn = {name: values for name, values in series.items() if any(values)}
    if not days or not drawn:
        return None
    fig, ax = _figure()
    bottoms = [0] * len(days)
    for name, values in drawn.items():
        ax.bar(days, values, bottom=bottoms, label=name, color=colours.get(name, TEXT), width=0.8)
        bottoms = [b + v for b, v in zip(bottoms, values)]
    locator = AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    formatter = ConciseDateFormatter(locator)
    ax.xaxis.set_major_formatter(formatter)
    ax.xaxis.get_offset_text().set_color(MUTED)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("Plays", color=MUTED, fontsize=12)
    ax.legend(frameon=False, labelcolor=TEXT, fontsize=12, loc="upper left")
    return _save(fig, filename)
