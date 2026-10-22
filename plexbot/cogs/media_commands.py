"""Library commands: /plex movie, /plex tv, /plex random, /plex recent, /plex downloading,
and /plexadmin cache."""

import asyncio
import difflib
import logging
import os
import random
import time
from typing import Optional

import nextcord
import qbittorrentapi
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from plexbot.config import config
from plexbot.errors import LoggedView
from plexbot.media_cache import MediaCache
from plexbot.media_details import MediaDetails, TautulliUnavailable
from plexbot.slash_commands import plex, plexadmin
from plexbot.tautulli_wrapper import Tautulli
from plexbot.ui.cards import genre_label, media_icon, session_title
from plexbot.ui.embeds import ErrorKind, Reply, bot_token_handle, card, empty_state, error_state, service_down, set_thumbnail
from plexbot.ui.format import file_size, join, plural, progress_bar, timestamp, to_int, truncate
from plexbot.ui.images import STRIP_POSTERS, fetch_poster_strip, mascot, poster_key
from plexbot.ui.theme import Colour, Limits, brand_colour
from plexbot.utilities import format_duration

logger = logging.getLogger("plexbot.media_commands")


# --- title lookups -------------------------------------------------------------

# Autocomplete values carry the chosen item's rating key; anything else is typed text.
_KEY_PREFIX = "key:"
_KINDS = {"movie": ("movie", "movie"), "tv": ("show", "TV show")}  # kind -> (section type, label)


def title_choices(items) -> dict:
    """Autocomplete choices ({shown name: value}) for media cache items."""
    choices = {}
    for item in items:
        name = truncate(session_title(item), Limits.SELECT_LABEL)
        choices.setdefault(name, f"{_KEY_PREFIX}{item['rating_key']}")
    return choices


# --- /plex recent ----------------------------------------------------------------



def recent_line(entry: dict) -> str:
    added = to_int(entry.get("added_at"))
    return join([
        f"{media_icon(entry.get('media_type'))} **{escape_markdown(session_title(entry))}**",
        f"added {timestamp(added)}" if added else None,
    ])


def recent_overview(entries, *, strip) -> nextcord.Embed:
    lines, used = [], 0
    for index, entry in enumerate(entries):
        line = recent_line(entry)
        if used + len(line) + 1 > Limits.DESCRIPTION - 40:
            lines.append(f"…and {plural(len(entries) - index, 'more')}")
            break
        lines.append(line)
        used += len(line) + 1
    embed = card(title="Recently added", description="\n".join(lines), colour=brand_colour())
    if strip:
        embed.set_image(url=strip.url)
    embed.set_footer(text="Pick one below for the details")
    return embed


def _recent_choices(entries) -> dict:
    """{label: rating_key} for the details dropdown (unique keys, at most 25)."""
    choices, seen = {}, set()
    for entry in entries:
        key = str(entry.get("rating_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        base = truncate(session_title(entry), Limits.SELECT_LABEL)
        label, number = base, 2
        while label in choices:  # Discord shows labels, so make repeats distinguishable
            label = f"{truncate(base, Limits.SELECT_LABEL - 4)} #{number}"
            number += 1
        choices[label] = key
        if len(choices) == Limits.SELECT_OPTIONS:
            break
    return choices


class RecentDetailsSelect(nextcord.ui.StringSelect):
    def __init__(self, choices: dict):
        options = [
            nextcord.SelectOption(label=label, value=key) for label, key in choices.items()
        ]
        # nextcord 3.2 StringSelect never generates a custom_id itself (see StreamDetailsSelect).
        super().__init__(custom_id=os.urandom(16).hex(), placeholder="Details…", options=options)

    async def callback(self, interaction: nextcord.Interaction):
        view: RecentView = self.view
        view.message = interaction.message  # bot-token handle; the command's webhook expires
        await view.cog.send_item_detail(interaction, self.values[0])
        # Re-send the view so the same choice can be picked again.
        try:
            await interaction.message.edit(view=view)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not reset the /plex recent dropdown: {e}")


class RecentView(LoggedView):
    """Read-only controls on the public /plex recent message: anyone can open details."""

    def __init__(self, cog: "MediaCommands", entries: list, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog
        choices = _recent_choices(entries)
        if choices:
            self.add_item(RecentDetailsSelect(choices))


# --- /plex random ----------------------------------------------------------------

_WHOLE_POOL = 100_000  # get_items needs a limit; this means "all of them"


class RandomView(LoggedView):
    """Roll again on a public random pick; only the person who ran the command can re-roll."""

    def __init__(self, cog: "MediaCommands", author_id: int, pool: list, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.author_id = author_id
        self.pool = pool
        self._unseen: list = []

    def pick(self) -> dict:
        """A random item, without repeats until the whole pool has been shown."""
        if not self._unseen:
            self._unseen = random.sample(self.pool, len(self.pool))
        return self._unseen.pop()

    @nextcord.ui.button(label="Roll again", emoji="\U0001F3B2", style=nextcord.ButtonStyle.primary)
    async def roll_again(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if interaction.user.id != self.author_id:
            await error_state(
                ErrorKind.PERMISSION, f"Only <@{self.author_id}> can re-roll this one.",
                hint="Run /plex random to roll your own.",
            ).send(interaction, ephemeral=True)
            return
        await interaction.response.defer()
        self.message = interaction.message
        try:
            reply = await self.cog.render_pick(self)
        except TautulliUnavailable:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        await reply.edit(interaction)


# --- /plex downloading -------------------------------------------------------------

_SHOWN_DOWNLOADS = 10
_SHOWN_STALLED = 5
_NO_ETA = 8_640_000  # qBittorrent's "infinity"


def split_torrents(torrents) -> tuple:
    """(active, stalled) downloads, skipping paused ones; active sorted by progress."""
    active, stalled = [], []
    for torrent in torrents:
        if torrent.state_enum.is_paused:
            continue
        no_speed = torrent.dlspeed == 0
        if no_speed and (torrent.progress > 0.99 or (torrent.state == "stalledDL" and torrent.num_seeds == 0)):
            stalled.append(torrent)
        else:
            active.append(torrent)
    active.sort(key=lambda t: t.progress, reverse=True)
    return active, stalled


def torrent_block(torrent, *, now: float) -> str:
    eta = to_int(torrent.eta, -1)
    details = join([
        f"`{progress_bar(torrent.progress)}` {torrent.progress * 100:.0f}%",
        f"{file_size(torrent.dlspeed)}/s" if torrent.dlspeed else None,
        file_size(torrent.size),
        f"done {timestamp(now + eta)}" if 0 < eta < _NO_ETA else None,
        plural(to_int(torrent.num_seeds), "seed"),
    ])
    return f"⬇️ **{escape_markdown(truncate(torrent.name, 80))}**\n{details}"


def downloads_card(active, stalled, *, now: float) -> Reply:
    total_speed = sum(t.dlspeed for t in active)
    header = join([
        f"**{plural(len(active), 'download')}**",
        f"{file_size(total_speed)}/s" if total_speed else None,
        f"{len(stalled)} stalled" if stalled else None,
        f"updated {timestamp(now)}",
    ])
    blocks = [header] + [torrent_block(t, now=now) for t in active[:_SHOWN_DOWNLOADS]]
    if len(active) > _SHOWN_DOWNLOADS:
        blocks.append(f"…and {plural(len(active) - _SHOWN_DOWNLOADS, 'more download')}")
    if stalled:
        names = [f"• {escape_markdown(truncate(t.name, 60))}" for t in stalled[:_SHOWN_STALLED]]
        if len(stalled) > _SHOWN_STALLED:
            names.append(f"…and {len(stalled) - _SHOWN_STALLED} more")
        blocks.append("**Stalled**\n" + "\n".join(names))
    embed = card(title="Downloading", description="\n\n".join(blocks),
                 colour=Colour.PLAYING if active else Colour.WARNING)
    art = mascot("mascot-downloading")
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])


class DownloadsView(LoggedView):
    """Refresh on the public /plex downloading message; read-only, so anyone can use it."""

    def __init__(self, cog: "MediaCommands", timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog = cog

    @nextcord.ui.button(label="Refresh", emoji="\U0001F504", style=nextcord.ButtonStyle.secondary)
    async def refresh_button(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.defer()
        self.message = interaction.message
        reply, ok = await self.cog.render_downloads()
        if not ok:
            await reply.send(interaction, ephemeral=True)
            return
        reply.view = self
        await reply.edit(interaction)


# --- /plexadmin cache ----------------------------------------------------------------


def cache_status(media_cache: MediaCache, note: Optional[str] = None) -> Reply:
    items = list(media_cache.media_items.values())
    movies = sum(item.get("media_type") == "movie" for item in items)
    shows = sum(item.get("media_type") == "show" for item in items)
    updated = media_cache.last_updated
    lines = [
        join([f"**{plural(len(items), 'item')}**", plural(movies, "movie"), plural(shows, "show")]),
        join([
            f"Updated {timestamp(updated)}" if updated else "Never updated",
            f"new titles every {format_duration(media_cache.update_interval)}",
        ]),
        "-# Used by /plex random and the title suggestions on /plex movie and /plex tv. "
        "Refresh now also re-reads genres and summaries.",
    ]
    if note:
        lines.append(note)
    return Reply([card(title="Media cache", description="\n".join(lines), colour=Colour.NEUTRAL)])


class CacheView(LoggedView):
    """Private admin controls under /plexadmin cache."""

    def __init__(self, media_cache: MediaCache, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.media_cache = media_cache

    @nextcord.ui.button(label="Refresh now", emoji="\U0001F504", style=nextcord.ButtonStyle.primary)
    async def refresh_now(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        button.disabled = True
        busy = cache_status(self.media_cache, "⏳ Refreshing… this can take a few minutes.")
        busy.view = self
        await interaction.response.edit_message(**busy.edit_payload())
        self.refresh_private(interaction)
        logger.info(f"{interaction.user} started a full media cache refresh")
        refreshed = await self.media_cache.refresh()
        button.disabled = False
        note = (f"✅ Refreshed {timestamp(time.time())}." if refreshed
                else "⚠️ The refresh failed, so nothing changed. `/plexadmin logs` says why.")
        done = cache_status(self.media_cache, note)
        done.view = self
        await done.edit(interaction)


# --- the cog -------------------------------------------------------------------------


class MediaCommands(commands.Cog):
    help_category = "library"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.details = MediaDetails(bot, self.tautulli)
        logger.info("MediaCommands cog initialized.")

    # Note: the Tautulli client is shared across all cogs and owned by the bot
    # (PlexBot.close()). A cog must not close it on unload.

    # --- shared: one item's card -------------------------------------------------

    async def render_item(self, rating_key: str, footer: Optional[str] = None) -> Optional[Reply]:
        """The card for one library item; None when Tautulli has no such item."""
        return await self.details.render(rating_key, footer)

    async def send_item_detail(self, interaction: nextcord.Interaction, rating_key: str) -> None:
        """A private card for an item picked from a public message."""
        await interaction.response.defer(ephemeral=True, with_message=True)
        try:
            reply = await self.render_item(rating_key)
        except TautulliUnavailable:
            reply = service_down("Tautulli")
        if reply is None:
            reply = error_state(ErrorKind.INPUT, "That's not in the library anymore.")
        await reply.send(interaction, ephemeral=True)

    # --- /plex movie and /plex tv ---------------------------------------------------

    async def _search_libraries(self, title: str, section_type: str) -> Optional[str]:
        """The best title match across the libraries of that type, straight from Tautulli."""
        libraries_r = await self.tautulli.get_libraries()
        if not Tautulli.check_response(libraries_r):
            raise TautulliUnavailable()
        libraries = [
            lib for lib in Tautulli.get_response_data(libraries_r, []) if lib.get("section_type") == section_type
        ]
        responses = await asyncio.gather(*(
            self.tautulli.get_library_media_info(section_id=lib["section_id"], search=title, length=10)
            for lib in libraries
        ))
        wanted = title.lower()
        results = []
        for response in responses:
            if Tautulli.check_response(response):
                results.extend(Tautulli.get_response_data(response, {}).get("data") or [])
        exact = next((r for r in results if (r.get("title") or "").lower() == wanted), None)
        if exact:
            return exact.get("rating_key")
        if not results:
            return None
        best = max(results, key=lambda r: difflib.SequenceMatcher(None, (r.get("title") or "").lower(), wanted).ratio())
        return best.get("rating_key")

    async def find_rating_key(self, query: str, kind: str) -> Optional[str]:
        if query.startswith(_KEY_PREFIX):
            # Only keys the suggestions could have offered: a typed key could reach any Plex item,
            # photos and home videos included.
            key = query[len(_KEY_PREFIX):]
            return key if self.media_cache and key in self.media_cache.media_items else None
        section_type, _label = _KINDS[kind]
        key = await self._search_libraries(query, section_type)
        if key:
            return str(key)
        matches = self.media_cache.suggest(query, kind, limit=1) if self.media_cache else []
        return str(matches[0]["rating_key"]) if matches else None

    async def lookup(self, interaction: nextcord.Interaction, query: str, kind: str) -> None:
        await interaction.response.defer()
        _section_type, label = _KINDS[kind]
        try:
            rating_key = await self.find_rating_key(query.strip(), kind)
            reply = await self.render_item(rating_key) if rating_key else None
        except TautulliUnavailable:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        if reply is None:
            if query.startswith(_KEY_PREFIX):
                reply = error_state(ErrorKind.INPUT, f"That {label} isn't in the library anymore.")
            else:
                reply = error_state(
                    ErrorKind.INPUT, f"No {label} matching **{escape_markdown(truncate(query, 80))}** in the library.",
                    hint="Pick one of the suggestions as you type the title.",
                )
            await reply.send(interaction, ephemeral=True, replace_placeholder=True)
            return
        await reply.send(interaction)

    def _title_suggestions(self, typed: str, kind: str) -> dict:
        if not self.media_cache:
            return {}
        return title_choices(self.media_cache.suggest(typed, kind, limit=Limits.SELECT_OPTIONS))

    @plex.subcommand(name="movie", description="Look up a movie in the library, with who's watched it")
    async def movie(
        self,
        interaction: nextcord.Interaction,
        title: str = nextcord.SlashOption(description="The movie's title", autocomplete=True, max_length=100),
    ):
        await self.lookup(interaction, title, "movie")

    @movie.on_autocomplete("title")
    async def _movie_autocomplete(self, interaction: nextcord.Interaction, title: str):
        await interaction.response.send_autocomplete(self._title_suggestions(title, "movie"))

    @plex.subcommand(name="tv", description="Look up a TV show in the library, with who's watched it")
    async def tv(
        self,
        interaction: nextcord.Interaction,
        title: str = nextcord.SlashOption(description="The show's title", autocomplete=True, max_length=100),
    ):
        await self.lookup(interaction, title, "tv")

    @tv.on_autocomplete("title")
    async def _tv_autocomplete(self, interaction: nextcord.Interaction, title: str):
        await interaction.response.send_autocomplete(self._title_suggestions(title, "tv"))

    # --- /plex random ---------------------------------------------------------------

    async def render_pick(self, view: RandomView) -> Reply:
        """A fresh pick from the view's pool, skipping items Tautulli no longer has."""
        for _attempt in range(3):
            item = view.pick()
            footer = f"🎲 Random pick from {plural(len(view.pool), 'match', 'matches')}"
            reply = await self.render_item(str(item["rating_key"]), footer=footer)
            if reply is not None:
                reply.view = view
                return reply
        return error_state(ErrorKind.INPUT, "Couldn't find a pick that's still in the library.",
                           hint="An admin can refresh the media cache with /plexadmin cache.")

    @plex.subcommand(name="random", description="Get a random movie or show from the library")
    async def random_pick(
        self,
        interaction: nextcord.Interaction,
        kind: str = nextcord.SlashOption(
            name="type", description="Movies or TV shows (default: either)", required=False,
            choices={"Movie": "movie", "TV show": "tv"},
        ),
        genre: str = nextcord.SlashOption(
            description="Only this genre", required=False, autocomplete=True, max_length=50,
        ),
    ):
        await interaction.response.defer()
        genre = (genre or "").strip() or None
        pool = await self.media_cache.get_items(
            media_type=kind, genres=[genre] if genre else None, limit=_WHOLE_POOL,
        )
        if not pool:
            if kind or genre:
                what = {"movie": "movies", "tv": "TV shows"}.get(kind, "titles")
                tagged = f" tagged **{escape_markdown(genre)}**" if genre else ""
                reply = error_state(ErrorKind.INPUT, f"No {what}{tagged} in the library.",
                                    hint="Pick a genre from the suggestions as you type.")
            else:
                reply = error_state(ErrorKind.INPUT, "The library list is still loading.",
                                    hint="Try again in a few minutes.")
            await reply.send(interaction, ephemeral=True, replace_placeholder=True)
            return
        view = RandomView(self, interaction.user.id, pool)
        try:
            reply = await self.render_pick(view)
        except TautulliUnavailable:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        view.message = bot_token_handle(interaction, await reply.send(interaction))

    @random_pick.on_autocomplete("genre")
    async def _genre_autocomplete(self, interaction: nextcord.Interaction, genre: str, kind: str = None):
        typed = (genre or "").strip().lower()
        genres = self.media_cache.genres(kind) if self.media_cache else []
        matches = [g for g in genres if g.startswith(typed)] + [g for g in genres if typed in g and not g.startswith(typed)]
        await interaction.response.send_autocomplete(
            {genre_label(g): g for g in matches[:Limits.SELECT_OPTIONS]}
        )

    # --- /plex recent ---------------------------------------------------------------

    @plex.subcommand(description="See what was added to the library recently")
    async def recent(
        self,
        interaction: nextcord.Interaction,
        count: int = nextcord.SlashOption(
            description="How many to show (default 10)", required=False, min_value=1, max_value=25,
        ),
        kind: str = nextcord.SlashOption(
            name="type", description="Only movies or only TV", required=False,
            choices={"Movies": "movie", "TV": "show"},
        ),
    ):
        await interaction.response.defer()
        count = count or max(1, min(to_int(config.get("defaults", "recent_count"), 10), 25))
        response = await self.tautulli.get_recently_added(count=count, media_type=kind)
        if not Tautulli.check_response(response):
            logger.error("Failed to retrieve recent additions from Tautulli.")
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        entries = (Tautulli.get_response_data(response, {}) or {}).get("recently_added") or []
        if not entries:
            await empty_state("Nothing's been added recently.").send(interaction)
            return
        strip = await fetch_poster_strip(self.tautulli, [poster_key(e) for e in entries],
                                         extra=max(0, len(entries) - STRIP_POSTERS))
        view = RecentView(self, entries)
        reply = Reply([recent_overview(entries, strip=strip)], [strip] if strip else [], view=view)
        view.message = bot_token_handle(interaction, await reply.send(interaction))

    # --- /plex downloading ------------------------------------------------------------

    @staticmethod
    def _qbit_client() -> Optional[qbittorrentapi.Client]:
        """A qBittorrent client from config, or None when it has no address.

        The login is optional: qBittorrent can let clients in without one (its "Bypass authentication"
        options).
        """
        ip, port, username, password = (
            config.get("qbittorrent", key) for key in ("ip", "port", "username", "password")
        )
        if not ip:
            return None
        return qbittorrentapi.Client(host=ip, port=port or None, username=username, password=password)

    async def render_downloads(self) -> tuple:
        """(reply, ok): the downloads card, or the error to send privately."""
        client = self._qbit_client()
        not_set_up = error_state(ErrorKind.INPUT, "qBittorrent isn't set up.",
                                 hint="An admin can add it under qbittorrent in config.json or .env.")
        if client is None:
            return not_set_up, False
        try:
            if client.username:
                await asyncio.to_thread(client.auth_log_in)
            torrents = await asyncio.to_thread(client.torrents.info.downloading)
        except (qbittorrentapi.LoginFailed, qbittorrentapi.Forbidden403Error):
            logger.exception("Login to qBittorrent failed.")
            if not client.username:
                return error_state(ErrorKind.INPUT, "qBittorrent wants a login.",
                                   hint="An admin can set the qbittorrent username and password."), False
            return error_state(ErrorKind.SERVICE_DOWN, "qBittorrent didn't accept the bot's login.",
                               hint="An admin should check the qbittorrent username and password."), False
        except Exception as e:
            if not client.username:
                # Most likely nobody set qBittorrent up, and the default address (localhost:8080) is empty.
                logger.info(f"Nothing answered as qBittorrent, so it counts as not set up: {e}")
                return not_set_up, False
            logger.exception("Couldn't reach qBittorrent.")
            return service_down("qBittorrent"), False
        active, stalled = split_torrents(torrents)
        if not active and not stalled:
            return empty_state("Nothing's downloading right now.", hint="Hit Refresh to check again."), True
        return downloads_card(active, stalled, now=time.time()), True

    @plex.subcommand(description="See what qBittorrent is downloading")
    async def downloading(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        reply, ok = await self.render_downloads()
        if not ok:
            await reply.send(interaction, ephemeral=True, replace_placeholder=True)
            return
        view = DownloadsView(self)
        reply.view = view
        view.message = bot_token_handle(interaction, await reply.send(interaction))

    # --- /plexadmin cache ---------------------------------------------------------------

    # Admin-only and guild-only via the /plexadmin group.
    @plexadmin.subcommand(description="Check the media cache behind /plex random and title suggestions")
    async def cache(self, interaction: nextcord.Interaction):
        reply = cache_status(self.media_cache)
        reply.view = CacheView(self.media_cache)
        reply.view.message = await reply.send(interaction, ephemeral=True)


def setup(bot):
    bot.add_cog(MediaCommands(bot))
