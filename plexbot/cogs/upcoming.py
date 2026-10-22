"""/plex upcoming: new episodes this week, from Sonarr's calendar when it's set up, else from TMDB."""

import asyncio
import logging
import time
from datetime import date, datetime, time as day_start, timedelta, tzinfo
from typing import Optional

import nextcord
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from plexbot.slash_commands import plex
from plexbot.ui.cards import episode_air_date, episode_code, plex_web_link
from plexbot.ui.embeds import ErrorKind, Reply, card, empty_state, error_state, service_down
from plexbot.ui.format import day_label, join
from plexbot.ui.theme import Limits, brand_colour

logger = logging.getLogger("plexbot.upcoming")

UPCOMING_DAYS = 7
_STALE = 12 * 3600  # seconds before a show's next episode is asked about again
_FINISHED = {"Ended", "Canceled"}  # TMDB statuses not asked about again until the bot restarts
_WHOLE_LIBRARY = 100_000  # get_items needs a limit; this means "all of them"
# Each of the 16 lookups at a time holds its slot this long: at most 32 a second, under TMDB's ~40.
_MIN_REQUEST_SECONDS = 0.5


def upcoming_rows(shows: list, entries: dict, today: date, days: int = UPCOMING_DAYS) -> list:
    """(air date, cached show, TMDB episode) for episodes airing in the next `days` days, soonest first."""
    # shortcut: next_episode_to_air is one episode per show, so a multi-episode drop lists the first;
    # Sonarr's calendar lists them all.
    rows, seen = [], set()
    for show in shows:
        if show.get("tmdb_id") in seen:  # the same show in two libraries (HD and 4K)
            continue
        seen.add(show.get("tmdb_id"))
        episode = (entries.get(show.get("tmdb_id")) or {}).get("next")
        day = episode_air_date(episode)
        if day and today <= day < today + timedelta(days=days):
            rows.append((day, show, episode))
    return sorted(rows, key=_row_order)


def _row_order(row: tuple) -> tuple:
    day, show, episode = row
    title = (show.get("title") or "").lower()
    return day, title, episode.get("season_number") or 0, episode.get("episode_number") or 0


def sonarr_rows(episodes: list, shows_by_tmdb: dict, zone: tzinfo, today: date,
                days: int = UPCOMING_DAYS) -> list:
    """upcoming_rows' (day, show, episode) from Sonarr's calendar: each episode on its day in `zone`.

    The show is the cached one with the series' TMDB id, for its Plex link, else just the series' title.
    """
    rows = []
    for episode in episodes:
        try:
            day = datetime.fromisoformat(episode["airDateUtc"]).astimezone(zone).date()
        except (KeyError, TypeError, ValueError):
            continue
        if not today <= day < today + timedelta(days=days):
            continue
        series = episode.get("series") or {}
        show = shows_by_tmdb.get(series.get("tmdbId")) or {"title": series.get("title")}
        rows.append((day, show, {"season_number": episode.get("seasonNumber"),
                                 "episode_number": episode.get("episodeNumber"),
                                 "name": episode.get("title"), "downloaded": bool(episode.get("hasFile"))}))
    return sorted(rows, key=_row_order)


def _episode_line(show: dict, episode: dict, server_id: Optional[str]) -> str:
    title = escape_markdown(show.get("title") or "Unknown")
    url = plex_web_link(server_id, show.get("rating_key"))
    name = escape_markdown(episode["name"]) if episode.get("name") else None
    downloaded = "✅ Downloaded" if episode.get("downloaded") else None
    return join([f"[{title}]({url})" if url else f"**{title}**", episode_code(episode), name, downloaded])


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
        self.sonarr = bot.shared_resources.get("sonarr")
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
        if self.tmdb is not None and self.sonarr is None and self.media_cache is not None:
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
            answer, _ = await asyncio.gather(self.tmdb.show(tmdb_id), asyncio.sleep(_MIN_REQUEST_SECONDS))
            return answer

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
        if self.tmdb is None and self.sonarr is None:
            await error_state(ErrorKind.INPUT, "Neither Sonarr nor TMDB is set up.",
                              hint="An admin can add either in config.json or .env.",
                              ).send(interaction, ephemeral=True)
            return
        await interaction.response.defer()
        zone = await self.tautulli.server_timezone()
        today = datetime.now(zone).date()
        if self.sonarr is not None:
            start = datetime.combine(today, day_start(), tzinfo=zone)
            episodes = await self.sonarr.calendar(start, start + timedelta(days=UPCOMING_DAYS))
            if episodes is None:
                await service_down("Sonarr").send(interaction, ephemeral=True, replace_placeholder=True)
                return
            # Only for Plex links, so the cache as it is: a first fill would take minutes.
            shows = self._shows(self.media_cache.media_items.values()) if self.media_cache else []
            rows = sonarr_rows(episodes, {show["tmdb_id"]: show for show in shows}, zone, today)
        else:
            rows = await self._tmdb_rows(interaction, today)
            if rows is None:
                return
        if not rows:
            await empty_state("Nothing's airing in the next week.").send(interaction)
            return
        await upcoming_card(rows, today, await self.tautulli.server_id()).send(interaction)

    async def _tmdb_rows(self, interaction: nextcord.Interaction, today: date) -> Optional[list]:
        """The week's rows from what TMDB said; None (after replying) when it has said nothing."""
        cached = []
        if self.media_cache:
            cached = await self.media_cache.get_items(media_type="show", limit=_WHOLE_LIBRARY)
        shows = self._shows(cached)
        if self.entries:
            self._refresh_in_background(shows)  # answer with what's known; due shows update for next time
        elif not await self.refresh(shows):
            await service_down("TMDB").send(interaction, ephemeral=True, replace_placeholder=True)
            return None
        return upcoming_rows(shows, self.entries, today)


def setup(bot):
    cog = Upcoming(bot)
    bot.add_cog(cog)
    cog.start_warm_up()
