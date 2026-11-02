"""Everyone's plays in a window, from Tautulli's history: what /plex chart, /plex stats' Activity page,
/plex hot and /plex wrapped count. Kept out of the chart cog so those commands don't need matplotlib."""

import logging
from datetime import datetime, time, timedelta
from typing import List, Optional, Tuple

from plexbot.ui.format import to_int
from plexbot.utilities import ignored_usernames, plex_username

logger = logging.getLogger("plexbot.plays")

Play = Tuple[datetime, dict]  # when it started (in the server's timezone), the history row
# The history fields charts and /plex hot read; a play keeps only these, as a chart holds its plays for minutes.
PLAY_FIELDS = (
    "user", "username", "friendly_name", "user_thumb",  # viewer names and avatars
    "media_type", "play_duration", "duration", "rating_key", "grandparent_rating_key", "watched_status",
    "title", "grandparent_title", "year", "thumb", "parent_thumb", "grandparent_thumb",  # titles and posters
)


async def window_start(tautulli, days: int) -> datetime:
    """When the last `days` days begin: midnight, `days - 1` days before today, in the server's timezone.

    Whole days, as the per-day and per-month bars count them, so the header's play count is what the
    bars add up to. /plex hot uses the same window.
    """
    zone = await tautulli.server_timezone()
    return datetime.combine(datetime.now(zone).date() - timedelta(days=days - 1), time.min, tzinfo=zone)


async def plays(tautulli, days: int) -> Optional[List[Play]]:
    """Everyone's plays in the last `days` days, but ignored users'; None if Tautulli didn't answer."""
    return await plays_between(tautulli, await window_start(tautulli, days))


async def plays_between(tautulli, since: datetime, until: Optional[datetime] = None) -> Optional[List[Play]]:
    """Everyone's plays starting from `since` until before `until`, but ignored users'; None if Tautulli
    didn't answer."""
    zone = since.tzinfo
    params = {
        # Tautulli's "after" and "before" are whole dates; the exact cutoffs are applied below.
        "after": (since.date() - timedelta(days=1)).isoformat(),
        "order_column": "date", "order_dir": "desc",
    }
    if until:
        params["before"] = (until.date() + timedelta(days=1)).isoformat()
    rows = await tautulli.get_history_rows(params, fields=(*PLAY_FIELDS, "started", "date"))
    if rows is None:
        logger.error("Failed to retrieve watch history from Tautulli.")
        return None
    ignored = ignored_usernames()
    found = []
    for row in rows:
        started = to_int(row.get("started")) or to_int(row.get("date"))
        if not started or plex_username(row) in ignored:
            continue
        moment = datetime.fromtimestamp(started, zone)
        if moment >= since and (until is None or moment < until):
            found.append((moment, {key: row[key] for key in PLAY_FIELDS if key in row}))
    return found
