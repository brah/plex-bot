"""One library item's full card (poster, art, facts, who watched it), shared by the commands that
show an item: /plex movie, /plex tv, /plex random, /plex recent details and /plex recommend."""

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import nextcord
from nextcord.utils import escape_markdown

from plexbot.errors import LoggedView
from plexbot.tautulli_wrapper import TMDB, TMDB_KINDS, Tautulli
from plexbot.ui.cards import item_choices, media_card, next_episode_line, plex_web_link
from plexbot.ui.embeds import ErrorKind, Reply, error_state, service_down
from plexbot.ui.format import join, plural, to_int
from plexbot.ui.images import plex_image, poster_key
from plexbot.utilities import format_duration, ignored_usernames, viewer_name


class TautulliUnavailable(Exception):
    """Tautulli didn't answer; the command replies with the service-down card."""


def _all_time(rows) -> dict:
    """Tautulli's watch-time stats come per period; query_days 0 is all time."""
    rows = rows if isinstance(rows, list) else []
    return next((r for r in rows if to_int(r.get("query_days"), -1) == 0), rows[-1] if rows else {})


def item_viewers(user_rows, ignored: set) -> list:
    """Who has played an item (its Tautulli user stats rows), most plays first, ignored users left out."""
    return sorted(
        (row for row in user_rows or [] if to_int(row.get("total_plays")) and row.get("username") not in ignored),
        key=lambda row: to_int(row.get("total_plays")), reverse=True,
    )


def watch_stats(user_rows, time_rows, display_name, ignored: set) -> str:
    """The card's watch line: plays and time, then who watched it most."""
    totals = _all_time(time_rows)
    plays, seconds = to_int(totals.get("total_plays")), to_int(totals.get("total_time"))
    if not plays:
        return "-# Nobody's watched this yet."
    viewers = item_viewers(user_rows, ignored)
    names = [f"**{escape_markdown(display_name(row))}** ({to_int(row.get('total_plays'))})" for row in viewers[:4]]
    if len(viewers) > 4:
        names.append(plural(len(viewers) - 4, "other"))
    lines = [join([f"▶ **{plural(plays, 'play')}**", f"{format_duration(seconds)} watched" if seconds else None])]
    if names:
        lines.append(f"-# Watched most by {', '.join(names)}")
    return "\n".join(lines)


MORE_LIKE_THIS = 10  # titles offered under a card


@dataclass
class TmdbExtras:
    """What TMDB adds to a card; empty without a TMDB key, a TMDB id or an answer."""

    trailer: Optional[str] = None  # YouTube URL
    similar: list = field(default_factory=list)  # media cache items, best first
    next_episode: Optional[dict] = None  # TMDB's next_episode_to_air


class MediaDetails:
    """Renders an item's card from Tautulli, with TMDB's extras when there's a TMDB key."""

    def __init__(self, bot, tautulli):
        self.bot = bot
        self.tautulli = tautulli
        self.tmdb: Optional[TMDB] = bot.shared_resources.get("tmdb")
        self.media_cache = bot.shared_resources.get("media_cache")

    def _tmdb_title(self, item: dict) -> tuple:
        """(Plex type, TMDB id, rating key) of the movie or show a card is about; Nones if unknown.

        Seasons and episodes have TMDB ids of their own, so they use their cached show's instead.
        """
        media_type = item.get("media_type")
        if media_type in TMDB_KINDS:
            return media_type, TMDB.id_from_guids(item.get("guids")), str(item.get("rating_key"))
        show_key = {"season": "parent_rating_key", "episode": "grandparent_rating_key"}.get(media_type)
        if not show_key or self.media_cache is None:
            return None, None, None
        show = self.media_cache.media_items.get(str(item.get(show_key)))
        if not show:
            return None, None, None
        return "show", show.get("tmdb_id"), str(show["rating_key"])

    async def tmdb_extras(self, item: dict) -> TmdbExtras:
        """The card's trailer, More like this titles on the server and, for shows, the next episode."""
        media_type, tmdb_id, own_key = self._tmdb_title(item)
        if self.tmdb is None or tmdb_id is None:
            return TmdbExtras()
        kind = TMDB_KINDS[media_type]
        details, more = await asyncio.gather(self.tmdb.title(kind, tmdb_id),
                                             self.tmdb.recommendations(kind, tmdb_id))
        if details is None:
            return TmdbExtras()
        ids = TMDB.suggestion_ids(details, more)
        similar = self.media_cache.by_tmdb_ids(media_type, ids) if self.media_cache else []
        return TmdbExtras(
            trailer=TMDB.trailer_url((details.get("videos") or {}).get("results")),
            similar=[i for i in similar if str(i.get("rating_key")) != own_key][:MORE_LIKE_THIS],
            next_episode=details.get("next_episode_to_air") if kind == "tv" else None,
        )

    async def render(self, rating_key: str, footer: Optional[str] = None,
                     view: Optional[nextcord.ui.View] = None) -> Optional[Reply]:
        """The item's card with its watch stats; None when Tautulli has no such item.

        TMDB's Trailer and More like this go on `view`, replacing the previous card's (a command that
        shows one card at a time), or on a new view when there's none.

        Raises TautulliUnavailable when Tautulli doesn't answer.
        """
        metadata_r, users_r, time_r, server_id = await asyncio.gather(
            self.tautulli.get_metadata(rating_key),
            self.tautulli.get_item_user_stats(rating_key),
            self.tautulli.get_item_watch_time_stats(rating_key),
            self.tautulli.server_id(),
        )
        if not Tautulli.check_response(metadata_r):
            # Tautulli refuses a title Plex removed, but also every title while Plex is down.
            if metadata_r is None or not Tautulli.check_response(await self.tautulli.get_server_info()):
                raise TautulliUnavailable()
            return None
        item = Tautulli.get_response_data(metadata_r, {}) or {}
        if not item.get("title"):
            return None

        stats = watch_stats(Tautulli.data(users_r, []), Tautulli.data(time_r, []),
                            lambda row: viewer_name(self.bot, row), ignored_usernames())
        poster, art, extras = await asyncio.gather(
            plex_image(self.tautulli, poster_key(item), filename="poster.jpg"),
            plex_image(self.tautulli, item.get("art") or item.get("grandparent_art"), filename="art.jpg",
                       width=640, height=360),
            self.tmdb_extras(item),
        )
        next_episode = None
        if extras.next_episode:
            today = datetime.now(await self.tautulli.server_timezone()).date()
            next_episode = next_episode_line(extras.next_episode, today)
        embed = media_card(item, poster=poster, art=art, url=plex_web_link(server_id, rating_key),
                           stats=stats, next_episode=next_episode)
        if footer:
            embed.set_footer(text=footer)
        controls = card_controls(self, extras)
        if view is None and controls:
            view = LoggedView(timeout=600)
        if view is not None:
            set_card_extras(view, controls)
        return Reply([embed], [a for a in (poster, art) if a], view=view)

    async def send_private(self, interaction: nextcord.Interaction, rating_key: str) -> None:
        """An item's card for just the person who picked it from a dropdown."""
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, with_message=True)
        try:
            reply = await self.render(rating_key)
        except TautulliUnavailable:
            reply = service_down("Tautulli")
        if reply is None:
            reply = error_state(ErrorKind.INPUT, "That's not in the library anymore.")
        message = await reply.send(interaction, ephemeral=True)
        if reply.view is not None:
            reply.view.message = message  # a private message is only editable through this token


class MoreLikeThisSelect(nextcord.ui.StringSelect):
    """Titles on the server like the card's. Anyone can pick one and gets its card privately."""

    def __init__(self, details: MediaDetails, items: list):
        options = [nextcord.SelectOption(label=label, value=key)
                   for label, key in item_choices(items).items()]
        # nextcord 3.2 StringSelect never generates a custom_id itself (see StreamDetailsSelect).
        super().__init__(custom_id=os.urandom(16).hex(), placeholder="More like this on the server…",
                         options=options)
        self.details = details

    async def callback(self, interaction: nextcord.Interaction):
        # Re-sending the view answers within Discord's 3 seconds and resets the dropdown for another pick.
        await interaction.response.edit_message(view=self.view)
        await self.details.send_private(interaction, self.values[0])


def card_controls(details: MediaDetails, extras: TmdbExtras) -> list:
    """The card's Trailer link and More like this dropdown, when TMDB had them."""
    controls = []
    if extras.trailer:
        controls.append(nextcord.ui.Button(label="Trailer", emoji="🎬", url=extras.trailer))
    if extras.similar:
        controls.append(MoreLikeThisSelect(details, extras.similar))
    return controls


def set_card_extras(view: nextcord.ui.View, items: list) -> None:
    """Swap the card controls on a view, removing the previous card's."""
    for item in getattr(view, "card_extras", []):
        view.remove_item(item)
    for item in items:
        view.add_item(item)
    view.card_extras = items
