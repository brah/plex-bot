"""Text helpers shared by every response: truncation, durations, progress bars, timestamps."""

from datetime import date, datetime
from typing import Iterable, Optional, Union


def truncate(text, limit: int) -> str:
    """Cut text to at most `limit` characters, ending with an ellipsis when cut."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "…"


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
    return "█" * filled + "░" * (width - filled)


def timestamp(moment: Union[datetime, int, float], style: str = "R") -> str:
    """A Discord timestamp: renders live (style R: "2 minutes ago") in each viewer's timezone."""
    seconds = int(moment.timestamp()) if isinstance(moment, datetime) else int(moment)
    return f"<t:{seconds}:{style}>"


def day_label(day: date, today: date) -> str:
    """"Today", "Tomorrow", else like "Saturday 24 Oct"."""
    offset = (day - today).days
    if offset == 0:
        return "Today"
    if offset == 1:
        return "Tomorrow"
    return f"{day:%A} {day.day} {day:%b}"


def hour_label(hour: int) -> str:
    return f"{hour % 12 or 12}{'am' if hour < 12 else 'pm'}"


def plural(count: int, singular: str, plural_form: Optional[str] = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {word}"


def join(parts: Iterable[Optional[str]], sep: str = " · ") -> str:
    return sep.join(part for part in parts if part)


def file_size(num_bytes) -> str:
    """Decimal units, as on drive labels: 8,100,000,000,000 bytes is "8.1 TB"."""
    value = float(to_int(num_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000:
            break
        value /= 1000
    else:
        unit = "PB"
    if unit == "B":
        return f"{value:.0f} B"
    return f"{value:.1f} {unit}" if value < 100 else f"{value:.0f} {unit}"
