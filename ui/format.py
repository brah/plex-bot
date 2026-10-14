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


def plural(count: int, singular: str, plural_form: Optional[str] = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count:,} {word}"


def join(parts: Iterable[Optional[str]], sep: str = " · ") -> str:
    return sep.join(part for part in parts if part)
