# media_details.py

"""One library item's full card (poster, art, facts, who watched it), shared by the commands that
show an item: /plex movie, /plex tv, /plex random, /plex recent details and /plex recommend."""

import asyncio
from typing import Optional

from nextcord.utils import escape_markdown

from tautulli_wrapper import Tautulli
from ui.cards import media_card, plex_web_link
from ui.embeds import Reply
from ui.format import join, plural, to_int
from ui.images import plex_image, poster_key
from utilities import format_duration, ignored_usernames, viewer_name


class TautulliUnavailable(Exception):
    """Tautulli didn't answer; the command replies with the service-down card."""


def _all_time(rows) -> dict:
    """Tautulli's watch-time stats come per period; query_days 0 is all time."""
    rows = rows if isinstance(rows, list) else []
    return next((r for r in rows if to_int(r.get("query_days"), -1) == 0), rows[-1] if rows else {})


def watch_stats(user_rows, time_rows, display_name, ignored: set) -> str:
    """The card's watch line: plays and time, then who watched it most."""
    totals = _all_time(time_rows)
    plays, seconds = to_int(totals.get("total_plays")), to_int(totals.get("total_time"))
    if not plays:
        return "-# Nobody's watched this yet."
    viewers = sorted(
        (row for row in user_rows or [] if to_int(row.get("total_plays")) and row.get("username") not in ignored),
        key=lambda row: to_int(row.get("total_plays")), reverse=True,
    )
    names = [f"**{escape_markdown(display_name(row))}** ({to_int(row.get('total_plays'))})" for row in viewers[:4]]
    if len(viewers) > 4:
        names.append(plural(len(viewers) - 4, "other"))
    lines = [join([f"▶ **{plural(plays, 'play')}**", f"{format_duration(seconds)} watched" if seconds else None])]
    if names:
        lines.append(f"-# Watched most by {', '.join(names)}")
    return "\n".join(lines)


class MediaDetails:
    """Renders an item's card from Tautulli."""

    def __init__(self, bot, tautulli):
        self.bot = bot
        self.tautulli = tautulli

    async def render(self, rating_key: str, footer: Optional[str] = None) -> Optional[Reply]:
        """The item's card with its watch stats; None when Tautulli has no such item.

        Raises TautulliUnavailable when Tautulli doesn't answer.
        """
        metadata_r, users_r, time_r, server_id = await asyncio.gather(
            self.tautulli.get_metadata(rating_key),
            self.tautulli.get_item_user_stats(rating_key),
            self.tautulli.get_item_watch_time_stats(rating_key),
            self.tautulli.server_id(),
        )
        if not Tautulli.check_response(metadata_r):
            raise TautulliUnavailable()
        item = Tautulli.get_response_data(metadata_r, {}) or {}
        if not item.get("title"):
            return None

        def data(response):
            return Tautulli.get_response_data(response, []) if Tautulli.check_response(response) else []

        stats = watch_stats(data(users_r), data(time_r), lambda row: viewer_name(self.bot, row),
                            ignored_usernames())
        poster, art = await asyncio.gather(
            plex_image(self.tautulli, poster_key(item), filename="poster.jpg"),
            plex_image(self.tautulli, item.get("art") or item.get("grandparent_art"), filename="art.jpg",
                       width=640, height=360),
        )
        embed = media_card(item, poster=poster, art=art, url=plex_web_link(server_id, rating_key), stats=stats)
        if footer:
            embed.set_footer(text=footer)
        return Reply([embed], [a for a in (poster, art) if a])
