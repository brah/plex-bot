"""/plex upcoming: new episodes this week for shows on the server, from TMDB."""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import nextcord
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from plexbot.slash_commands import plex
from plexbot.ui.cards import episode_code, plex_web_link
from plexbot.ui.embeds import ErrorKind, Reply, card, empty_state, error_state, service_down
from plexbot.ui.format import day_label, join
from plexbot.ui.theme import Limits, brand_colour

logger = logging.getLogger("plexbot.upcoming")

UPCOMING_DAYS = 7
_STALE = 12 * 3600  # seconds before a show's next episode is asked about again
_FINISHED = {"Ended", "Canceled"}  # TMDB statuses not asked about again until the bot restarts
_WHOLE_LIBRARY = 100_000  # get_items needs a limit; this means "all of them"


def _air_date(episode: Optional[dict]) -> Optional[date]:
    try:
        return datetime.strptime(str((episode or {}).get("air_date")), "%Y-%m-%d").date()
    except ValueError:
        return None


def upcoming_rows(shows: list, entries: dict, today: date, days: int = UPCOMING_DAYS) -> list:
    """(air date, cached show, TMDB episode) for episodes airing in the next `days` days, soonest first."""
    # shortcut: next_episode_to_air is one episode per show, so a multi-episode drop lists the first;
    # read the season's episodes if that matters.
    rows, seen = [], set()
    for show in shows:
        if show.get("tmdb_id") in seen:  # the same show in two libraries (HD and 4K)
            continue
        seen.add(show.get("tmdb_id"))
        episode = (entries.get(show.get("tmdb_id")) or {}).get("next")
        day = _air_date(episode)
        if day and today <= day < today + timedelta(days=days):
            rows.append((day, show, episode))
    return sorted(rows, key=lambda row: (row[0], (row[1].get("title") or "").lower()))


def _episode_line(show: dict, episode: dict, server_id: Optional[str]) -> str:
    title = escape_markdown(show.get("title") or "Unknown")
    url = plex_web_link(server_id, show.get("rating_key"))
    name = escape_markdown(episode["name"]) if episode.get("name") else None
    return join([f"[{title}]({url})" if url else f"**{title}**", episode_code(episode), name])


def upcoming_card(rows: list, today: date, server_id: Optional[str]) -> Reply:
    """The week's episodes under a heading per day, cut to fit with "and N more"."""
    lines, length, shown, day = [], 0, 0, None
    budget = Limits.DESCRIPTION - 20  # room for the "and N more" line
    for when, show, episode in rows:
        block = ([""] if lines else []) + [f"**{day_label(when, today)}**"] if when != day else []
        block.append(_episode_line(show, episode, server_id))
        added = sum(len(line) + 1 for line in block)
        if length + added > budget:
            break
        lines += block
        length += added
        shown += 1
        day = when
    if shown < len(rows):
        lines.append(f"-# and {len(rows) - shown} more")
    return Reply([card(title="📅 Airing this week", description="\n".join(lines), colour=brand_colour())])


class Upcoming(commands.Cog):
    help_category = "library"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli = bot.shared_resources.get("tautulli")
        self.tmdb = bot.shared_resources.get("tmdb")
        self.media_cache = bot.shared_resources.get("media_cache")
        # TMDB id -> {"status": TMDB's, "next": its next_episode_to_air, "fetched": time.monotonic()}
        self.entries: dict = {}
        self._lock = asyncio.Lock()  # concurrent commands share one refresh
        # TMDB takes ~0.6 s a request from some hosts, so ~370 shows take ~15 s even 16 at a time.
        self._semaphore = asyncio.Semaphore(16)
        self._refreshing: Optional[asyncio.Task] = None  # the warm-up or a background refresh

    def cog_unload(self) -> None:
        if self._refreshing is not None:
            self._refreshing.cancel()

    @staticmethod
    def _shows(items) -> list:
        return [item for item in items if item.get("media_type") == "show" and item.get("tmdb_id")]

    def start_warm_up(self) -> None:
        """Ask TMDB about every show in the background at startup, so /plex upcoming answers at once."""
        if self.tmdb is not None and self.media_cache is not None:
            self._refreshing = asyncio.create_task(self.warm_up())

    async def warm_up(self) -> None:
        await self.media_cache.wait_for_refresh()  # the startup fill can add shows and their TMDB ids
        await self.refresh(self._shows(self.media_cache.media_items.values()))

    def _refresh_in_background(self, shows: list) -> None:
        if self._refreshing is None or self._refreshing.done():
            self._refreshing = asyncio.create_task(self.refresh(shows))

    def _due(self, tmdb_id: int, now: float) -> bool:
        entry = self.entries.get(tmdb_id)
        if entry is None:
            return True
        return entry["status"] not in _FINISHED and now - entry["fetched"] > _STALE

    async def _ask(self, tmdb_id: int) -> Optional[dict]:
        async with self._semaphore:
            return await self.tmdb.show(tmdb_id)

    async def refresh(self, shows: list) -> bool:
        """Ask TMDB about shows not asked lately; False when it answered nothing and nothing is known."""
        async with self._lock:
            now = time.monotonic()
            due = list(dict.fromkeys(show["tmdb_id"] for show in shows if self._due(show["tmdb_id"], now)))
            answers = await asyncio.gather(*(self._ask(tmdb_id) for tmdb_id in due))
            for tmdb_id, details in zip(due, answers):
                if details is not None:  # no answer keeps what we had
                    self.entries[tmdb_id] = {"status": details.get("status"),
                                             "next": details.get("next_episode_to_air"), "fetched": now}
            if due and not any(answers):
                logger.warning(f"TMDB answered none of {len(due)} show lookups.")
            return bool(self.entries) or not due

    @plex.subcommand(description="New episodes airing this week for shows on the server")
    async def upcoming(self, interaction: nextcord.Interaction):
        if self.tmdb is None:
            await error_state(ErrorKind.INPUT, "TMDB isn't set up.",
                              hint="An admin can add a TMDB API key under tmdb in config.json or .env.",
                              ).send(interaction, ephemeral=True)
            return
        await interaction.response.defer()
        cached = []
        if self.media_cache:
            cached = await self.media_cache.get_items(media_type="show", limit=_WHOLE_LIBRARY)
        shows = self._shows(cached)
        if self.entries:
            self._refresh_in_background(shows)  # answer with what's known; due shows update for next time
        elif not await self.refresh(shows):
            await service_down("TMDB").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        today = datetime.now(await self.tautulli.server_timezone()).date()
        rows = upcoming_rows(shows, self.entries, today)
        if not rows:
            await empty_state("Nothing's airing in the next week.").send(interaction)
            return
        await upcoming_card(rows, today, await self.tautulli.server_id()).send(interaction)


def setup(bot):
    cog = Upcoming(bot)
    bot.add_cog(cog)
    cog.start_warm_up()
