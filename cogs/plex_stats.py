# cogs/plex_stats.py

"""Stats commands: /plex top, /plex stats, /plex shows, /plex history and /plex hot."""

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Callable, Optional

import nextcord
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from config import config
from errors import LoggedView
from plex_users import PlexUsers
from slash_commands import plex
from tautulli_wrapper import Tautulli
from ui.cards import media_icon, recent_viewer_line, session_title
from ui.embeds import Reply, bot_token_handle, card, empty_state, service_down
from ui.format import join, plural, timestamp, to_int, truncate
from ui.images import fetch_poster_strip, poster_key
from ui.theme import Colour, Limits, brand_colour
from utilities import (
    library_totals,
    UserMappings, format_duration, ignored_usernames, plex_username, viewer_avatar, viewer_mention,
    viewer_name,
)

logger = logging.getLogger("plexbot.plex_stats")

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_RANKED = 10
_PODIUM = 3  # /plex top gives the top three a second line
_TOP_DAYS = 7  # /plex top's default window, and the ranking that sets roles


def rank_prefix(rank: int) -> str:
    return _MEDALS.get(rank, f"`{rank}.`")


def _escaped(text, limit: int = 80) -> str:
    return escape_markdown(truncate(str(text or "Unknown"), limit))


# The renderers below take `name`: a viewer's name ready for embed text, as viewer_mention gives it
# (their Discord @mention when linked, else their escaped Plex name).


async def _poster_strip(tautulli, rows) -> Optional[object]:
    """A poster strip for rows (distinct titles, at most STRIP_POSTERS), or None."""
    return await fetch_poster_strip(tautulli, list(dict.fromkeys(key for key in map(poster_key, rows) if key)))


# --- /plex top -------------------------------------------------------------------


def _last_watched(row: dict) -> Optional[str]:
    if row.get("media_type") == "episode":
        return row.get("grandparent_title") or row.get("title")
    return row.get("grandchild_title") or row.get("title")


_SECONDS = {"day": 86400, "hr": 3600, "min": 60, "sec": 1}


def tautulli_duration(text: str) -> str:
    """Tautulli's own duration text ("41 days 6 hrs 2 mins") in the bot's style ("41d 6h 2m").

    Anything not entirely "<number> <unit>" pairs is shown as Tautulli wrote it, never half-read.
    """
    words = str(text).split()
    if not words or len(words) % 2:
        return str(text)
    seconds = 0
    for count, unit in zip(words[::2], words[1::2]):
        unit = unit.removesuffix("s")
        if not count.isdigit() or unit not in _SECONDS:
            return str(text)
        seconds += int(count) * _SECONDS[unit]
    return format_duration(seconds)


def top_card(rows, *, days: int, name: Callable[[dict], str], all_time: Optional[str] = None) -> nextcord.Embed:
    """The podium (top three, with what they last watched), then everyone else one line each."""
    lines = [f"-# Last {plural(days, 'day')} · by watch time", ""]
    for rank, row in enumerate(rows[:_RANKED], 1):
        if rank == _PODIUM + 1:
            lines.append("")
        watched = format_duration(to_int(row.get("total_duration")))
        lines.append(f"{rank_prefix(rank)} **{name(row)}** · {watched}")
        last = _last_watched(row)
        if last and rank <= _PODIUM:
            lines.append(f"-# Last watched {_escaped(last)}")
    total = sum(to_int(row.get("total_duration")) for row in rows[:_RANKED])
    embed = card(title="🏆 Top watchers", description="\n".join(lines), colour=Colour.GOLD)
    embed.set_footer(text=join([
        f"Together {format_duration(total)}", f"All time {tautulli_duration(all_time)}" if all_time else None,
    ]))
    return embed


# --- /plex stats -----------------------------------------------------------------


def library_line(rows) -> Optional[str]:
    totals = library_totals(rows)
    watched = sum(to_int(row.get("duration")) for row in rows or [])
    return join([
        plural(totals["movies"], "movie") if totals["movies"] else None,
        plural(totals["shows"], "show") if totals["shows"] else None,
        plural(totals["episodes"], "episode") if totals["episodes"] else None,
        f"{format_duration(watched)} watched all time" if watched else None,
    ]) or None


def popular_lines(rows, limit: int = 5) -> Optional[str]:
    lines = [
        join([
            f"`{rank}.` **{_escaped(session_title(row))}**",  # medals are for people (top, shows)
            plural(to_int(row.get("total_plays")), "play"),
            plural(to_int(row.get("users_watched")), "person", "people"),
        ])
        for rank, row in enumerate(rows[:limit], 1)
    ]
    return "\n".join(lines) or None


_POPULAR = 10  # titles on the movie and TV pages
_TEASER = 3  # of those, on the overview
_WATCH_TIME = 5
_PLATFORMS = 3
_ALL_TIME = 36500  # days: home stats has no "all time" option
# The home-stats cards /plex stats reads, with their extra get_home_stats params.
_STATS = {
    "popular_movies": {"stats_count": _POPULAR},
    "popular_tv": {"stats_count": _POPULAR},
    "top_tv": {"stats_count": _WATCH_TIME, "stats_type": "duration"},
    "top_libraries": {"stats_count": 50},  # every library, so the per-type sums are whole
    "top_platforms": {"stats_count": _PLATFORMS},
    "most_concurrent": {},
    # The Users page is all time, whatever the days option.
    # shortcut: the viewer count and total stop at 100 viewers; page the stat if a server has more.
    "top_users": {"stats_count": 100, "stats_type": "duration", "time_range": _ALL_TIME},
}
_SECTION_KINDS = {"movie": "🎬 Movies", "show": "📺 TV", "artist": "🎵 Music"}


def _usage(rows) -> tuple:
    """Plays and seconds watched, summed over top_libraries rows."""
    return (sum(to_int(row.get("total_plays")) for row in rows),
            sum(to_int(row.get("total_duration")) for row in rows))


def _usage_line(rows) -> Optional[str]:
    plays, seconds = _usage(rows)
    return f"**{plural(plays, 'play')}** · **{format_duration(seconds)}** watched" if plays else None


def peak_line(rows) -> Optional[str]:
    """The most streams at once in the window, and when ("6 streams at once · <date>")."""
    streams = next((row for row in rows if row.get("title") == "Concurrent Streams"), None)
    count = to_int((streams or {}).get("count"))
    if not count:
        return None
    started = to_int(streams.get("started"))
    return join([f"{plural(count, 'stream')} at once", timestamp(started, "d") if started else None])


def platform_lines(rows) -> Optional[str]:
    return "\n".join(
        f"**{_escaped(row.get('platform'), 40)}** · {plural(to_int(row.get('total_plays')), 'play')}"
        for row in rows[:_PLATFORMS]
    ) or None


def watch_time_lines(rows) -> Optional[str]:
    return "\n".join(
        f"`{rank}.` **{_escaped(session_title(row))}** · {format_duration(to_int(row.get('total_duration')))}"
        for rank, row in enumerate(rows[:_WATCH_TIME], 1)
    ) or None


def overview_card(stats: dict, libraries, *, days: int, strip) -> nextcord.Embed:
    """The window at a glance: plays per media type, the top three of each, platforms, the busiest moment."""
    by_kind = defaultdict(list)
    for row in stats["top_libraries"]:
        by_kind[row.get("section_type")].append(row)
    embed = card(title="📊 Server stats", colour=brand_colour(), description=join([
        f"-# Last {plural(days, 'day')}", _usage_line(stats["top_libraries"]),
    ], sep="\n"))
    for kind, heading in _SECTION_KINDS.items():
        if by_kind[kind]:
            plays, seconds = _usage(by_kind[kind])
            embed.add_field(name=heading, value=f"{plural(plays, 'play')}\n{format_duration(seconds)}", inline=True)
    movies, shows = stats["popular_movies"], stats["popular_tv"]
    for heading, rows in (("🎬 Top movies", movies), ("📺 Top shows", shows)):
        lines = popular_lines(rows, _TEASER)
        if lines:
            embed.add_field(name=heading, value=truncate(lines, Limits.FIELD_VALUE), inline=False)
    if not (movies or shows):
        embed.add_field(name="Top titles", value=f"Nothing was watched in the last {plural(days, 'day')}.",
                        inline=False)
    for heading, value in (("📱 Platforms", platform_lines(stats["top_platforms"])),
                           ("📈 Busiest", peak_line(stats["most_concurrent"]))):
        if value:
            embed.add_field(name=heading, value=value, inline=True)
    if strip:
        embed.set_image(url=strip.url)
    totals = library_line(libraries)
    if totals:
        embed.set_footer(text=totals)
    return embed


def media_card(kind: str, stats: dict, libraries, *, days: int, strip) -> nextcord.Embed:
    """The movie (kind "movie") or TV ("show") page: the top ten, per-library plays and library totals."""
    movie = kind == "movie"
    sections = [row for row in stats["top_libraries"] if row.get("section_type") == kind]
    embed = card(
        title="🎬 Movie stats" if movie else "📺 TV stats", colour=Colour.MOVIE if movie else Colour.TV,
        description=join([f"-# Last {plural(days, 'day')}", _usage_line(sections)], sep="\n"),
    )
    popular = popular_lines(stats["popular_movies" if movie else "popular_tv"], _POPULAR)
    embed.add_field(name="🔥 Most popular", inline=False, value=truncate(
        popular or f"Nothing was watched in the last {plural(days, 'day')}.", Limits.FIELD_VALUE))
    # For movies this is nearly the list above; for TV it's where the binges show.
    watch_time = None if movie else watch_time_lines(stats["top_tv"])
    if watch_time:
        embed.add_field(name="⏱️ Most watch time", value=truncate(watch_time, Limits.FIELD_VALUE), inline=False)
    if len(sections) > 1:
        lines = "\n".join(
            join([f"**{_escaped(row.get('section_name'), 40)}**", plural(to_int(row.get("total_plays")), "play"),
                  format_duration(to_int(row.get("total_duration")))])
            for row in sections
        )
        embed.add_field(name="📚 By library", value=truncate(lines, Limits.FIELD_VALUE), inline=False)
    if strip:
        embed.set_image(url=strip.url)
    totals = library_line([row for row in libraries if row.get("section_type") == kind])
    if totals:
        embed.set_footer(text=totals)
    return embed


def users_card(rows, *, name: Callable[[dict], str], avatar: Optional[str]) -> nextcord.Embed:
    """The Users page: all-time watch time and plays per viewer, the top three with what they last watched."""
    lines = ["-# All time · by watch time", ""]
    for rank, row in enumerate(rows[:_RANKED], 1):
        if rank == _PODIUM + 1:
            lines.append("")
        lines.append(join([f"{rank_prefix(rank)} **{name(row)}**", format_duration(to_int(row.get("total_duration"))),
                           plural(to_int(row.get("total_plays")), "play")]))
        last, when = _last_watched(row), to_int(row.get("last_play"))
        if last and rank <= _PODIUM:
            lines.append(join([f"-# Last watched {_escaped(last)}", timestamp(when) if when else None]))
    if not rows:
        lines.append("Nobody's watched anything yet.")
    embed = card(title="👥 User stats", description="\n".join(lines), colour=Colour.GOLD)
    if avatar:
        embed.set_thumbnail(url=avatar)
    if rows:
        total = sum(to_int(row.get("total_duration")) for row in rows)
        embed.set_footer(text=f"{plural(len(rows), 'viewer')} · {format_duration(total)} together")
    return embed


_PAGE_EMOJI = {"Overview": "📊", "Movies": "🎬", "TV": "📺", "Users": "👥"}


class StatsPageButton(nextcord.ui.Button):
    def __init__(self, label: str):
        super().__init__(label=label, emoji=_PAGE_EMOJI[label], style=nextcord.ButtonStyle.secondary)

    async def callback(self, interaction: nextcord.Interaction):
        view: StatsView = self.view
        view.message = interaction.message  # bot-token handle; the command's webhook expires
        await interaction.response.edit_message(**view.show(self.label).edit_payload())


class StatsView(LoggedView):
    """Page buttons under the public /plex stats message. Anyone can flip it; the data was fetched once."""

    def __init__(self, pages: dict, timeout: float = 600):
        """pages: _PAGE_EMOJI label -> (embed, poster strip or None), in button order."""
        super().__init__(timeout=timeout)
        self.pages = {label: Reply([embed], [strip] if strip else [], view=self)
                      for label, (embed, strip) in pages.items()}
        for label in pages:
            self.add_item(StatsPageButton(label))

    def show(self, label: str) -> Reply:
        """The page's reply, with its button highlighted."""
        for button in self.children:
            button.style = nextcord.ButtonStyle.primary if button.label == label else nextcord.ButtonStyle.secondary
        return self.pages[label]


# --- /plex shows -----------------------------------------------------------------


def tv_watchers(libraries, responses, ignored: set) -> list:
    """[(row, seconds, library names)] across TV libraries, most watched first."""
    totals: dict = {}
    for library, response in zip(libraries, responses):
        if not Tautulli.check_response(response):
            continue
        for row in Tautulli.get_response_data(response, []) or []:
            username = plex_username(row)
            if not username or username in ignored or not to_int(row.get("total_time")):
                continue
            entry = totals.setdefault(username, [row, 0, []])
            entry[1] += to_int(row.get("total_time"))
            entry[2].append(library.get("section_name") or "TV")
    return sorted((tuple(v) for v in totals.values()), key=lambda v: v[1], reverse=True)


def shows_card(ranked, *, name: Callable[[dict], str], several_libraries: bool) -> nextcord.Embed:
    lines = ["-# All time, by TV watch time"]
    for rank, (row, seconds, libraries) in enumerate(ranked[:_RANKED], 1):
        lines.append(f"{rank_prefix(rank)} **{name(row)}** · {format_duration(seconds)}")
        if several_libraries:
            lines.append(f"-# {escape_markdown(', '.join(libraries))}")
    return card(title="📺 Top TV watchers", description="\n".join(lines), colour=Colour.GOLD)


# --- /plex history ---------------------------------------------------------------

_HISTORY_ROWS = 15


def history_card(rows, *, name: Callable[[dict], str], person: Optional[str], strip) -> nextcord.Embed:
    lines = [
        recent_viewer_line(row, user=name(row), show_user=person is None) for row in rows[:_HISTORY_ROWS]
    ]
    embed = card(title="🕘 Watch history", description="\n".join(lines), colour=brand_colour())
    if strip:
        embed.set_image(url=strip.url)
    embed.set_footer(text=f"Last {plural(len(lines), 'play')}" + ("" if person else " on the server"))
    return embed


# --- /plex hot -------------------------------------------------------------------


def hot_summary(history, recent_items, *, days: int, name: Callable[[dict], str]) -> dict:
    """What's been popular: totals, then each section's lines (empty sections are left out)."""
    plays_by_key = Counter(str(row.get("rating_key")) for row in history)
    titles: dict = {}  # show (or movie) key -> {"title", "users", "plays", "row"}
    per_user: dict = defaultdict(lambda: {"plays": 0, "titles": set(), "row": None})
    episodes = defaultdict(Counter)  # user -> show -> episodes
    for row in history:
        user = plex_username(row)
        key = str(row.get("grandparent_rating_key") or row.get("rating_key"))
        title = row.get("grandparent_title") or row.get("full_title") or row.get("title")
        entry = titles.setdefault(key, {"title": title, "users": set(), "plays": 0, "row": row})
        entry["users"].add(user)
        entry["plays"] += 1
        stats = per_user[user]
        stats["plays"] += 1
        stats["titles"].add(row.get("title"))
        stats["row"] = stats["row"] or row
        if row.get("media_type") == "episode" and row.get("grandparent_title"):
            episodes[user][row["grandparent_title"]] += 1

    sections = []
    trending = sorted((t for t in titles.values() if len(t["users"]) > 1),
                      key=lambda t: (len(t["users"]), t["plays"]), reverse=True)[:5]
    if trending:
        sections.append(("🔥 Trending", [
            f"**{_escaped(t['title'])}** · {plural(len(t['users']), 'person', 'people')} · {plural(t['plays'], 'play')}"
            for t in trending
        ]))
    new_hits = sorted(
        ((item, plays_by_key[str(item.get("rating_key"))]) for item in recent_items
         if plays_by_key[str(item.get("rating_key"))]),
        key=lambda pair: pair[1], reverse=True,
    )[:5]
    if new_hits:
        sections.append(("🆕 New and watched", [
            f"{media_icon(item.get('media_type'))} **{_escaped(session_title(item))}** · {plural(plays, 'play')}"
            for item, plays in new_hits
        ]))
    active = sorted(per_user.values(), key=lambda s: s["plays"], reverse=True)[:5]
    if active:
        sections.append(("🏃 Most active", [
            f"{rank_prefix(rank)} **{name(s['row'])}** · {plural(s['plays'], 'play')} · "
            f"{plural(len(s['titles']), 'title')}"
            for rank, s in enumerate(active, 1)
        ]))
    binges = sorted(
        ((user, *shows.most_common(1)[0]) for user, shows in episodes.items() if shows.most_common(1)[0][1] >= 5),
        key=lambda b: b[2], reverse=True,
    )[:3]
    if binges:
        sections.append(("🍿 Binge watchers", [
            f"**{name(per_user[user]['row'])}** binged **{_escaped(show)}** ({count} episodes)"
            for user, show, count in binges
        ]))
    recent_keys = {str(item.get("rating_key")) for item in recent_items}
    discoveries = [t for key, t in titles.items() if key in recent_keys and len(t["users"]) == 1 and t["plays"] <= 2][:3]
    if discoveries:
        sections.append(("🧭 First to check out", [
            f"**{name(t['row'])}** tried **{_escaped(t['title'])}**" for t in discoveries
        ]))

    kinds = Counter(row.get("media_type") for row in history)
    movies, shows = kinds.get("movie", 0), kinds.get("episode", 0)
    leader = None
    if movies and shows:
        leader = (f"Movies lead: {plural(movies, 'movie')} to {plural(shows, 'episode')}" if movies > shows
                  else f"TV leads: {plural(shows, 'episode')} to {plural(movies, 'movie')}")
    return {
        "totals": join([
            f"**{plural(len(history), 'play')}**", plural(len(per_user), "person", "people"),
            plural(len(titles), "title"), f"{len(history) / days:.1f} a day",
        ]),
        "sections": sections,
        "leader": leader,
        "posters": [t["row"] for t in trending] or [row for row in history],
    }


def hot_card(summary: dict, *, days: int, strip) -> nextcord.Embed:
    description = join([f"-# Last {plural(days, 'day')}", summary["totals"]], "\n")
    embed = card(title="🔥 What's hot", description=description, colour=Colour.WARNING)
    for heading, lines in summary["sections"]:
        embed.add_field(name=heading, value=truncate("\n".join(lines), Limits.FIELD_VALUE), inline=False)
    if summary["leader"]:
        embed.set_footer(text=summary["leader"])
    if strip:
        embed.set_image(url=strip.url)
    return embed


# --- the cog ---------------------------------------------------------------------


class PlexStats(commands.Cog):
    """Who watches what, and how much."""

    help_category = "stats"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.users = PlexUsers(bot, self.tautulli)

    def _name(self, row: dict) -> str:
        return viewer_name(self.bot, row)

    def _mention(self, row: dict) -> str:
        return viewer_mention(self.bot, row)

    @staticmethod
    def _rows(response, default=None):
        """The rows of a home-stats or list response; None when Tautulli failed."""
        if not Tautulli.check_response(response):
            return None
        data = Tautulli.get_response_data(response, default if default is not None else {})
        return data.get("rows", []) if isinstance(data, dict) else data

    # --- /plex top ---------------------------------------------------------------

    @plex.subcommand(description="Top watchers on the server, by watch time")
    async def top(
        self,
        interaction: nextcord.Interaction,
        days: int = nextcord.SlashOption(
            description="How many days back (default 7)", required=False,
            min_value=1, max_value=3650,
        ),
    ):
        await interaction.response.defer()
        days = days or _TOP_DAYS
        response, history_r = await asyncio.gather(
            self.tautulli.get_home_stats(params={
                "stats_type": "duration", "stat_id": "top_users", "stats_count": str(_RANKED + 10),
                "time_range": days,
            }),
            self.tautulli.get_history({"length": 1}),  # carries the server's all-time watch total
        )
        rows = self._rows(response)
        if rows is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        ignored = ignored_usernames()
        ranked = [row for row in rows if plex_username(row) not in ignored][:_RANKED]
        if not ranked:
            await empty_state(f"Nobody's watched anything in the last {plural(days, 'day')}.").send(interaction)
            return
        all_time = None
        if Tautulli.check_response(history_r):
            all_time = (Tautulli.get_response_data(history_r, {}) or {}).get("total_duration")
        embed = top_card(ranked, days=days, name=self._mention, all_time=all_time)
        avatar = viewer_avatar(self.bot, ranked[0])
        if avatar:
            embed.set_thumbnail(url=avatar)
        await Reply([embed]).send(interaction)
        # Roles follow the server's standard ranking, not whatever window someone asked for.
        if interaction.guild and days == _TOP_DAYS:
            await self._assign_top_roles(interaction.guild, ranked[:3])

    async def _assign_top_roles(self, guild: nextcord.Guild, top_rows: list) -> None:
        """Give the top three their roles (discord.top_role_id, second_role_id, third_role_id)."""
        role_ids = [config.get("discord", key) for key in ("top_role_id", "second_role_id", "third_role_id")]
        roles = [guild.get_role(to_int(role_id)) for role_id in role_ids if role_id]
        if len(roles) != 3 or not all(roles):
            logger.debug("Top roles not fully configured; skipping role assignment.")
            return
        if not all(guild.me.top_role > role for role in roles):
            logger.warning("Bot role hierarchy too low to manage top user roles.")
            return
        winners = {}
        for rank, row in enumerate(top_rows, 1):
            mapping = UserMappings.get_mapping_by_plex_username(plex_username(row))
            if mapping and mapping.get("discord_id"):
                winners[to_int(mapping["discord_id"])] = roles[rank - 1]
        holders = {member for role in roles for member in role.members}
        for member in holders - {guild.get_member(uid) for uid in winners}:
            try:
                await member.remove_roles(*roles, reason="No longer a top user.")
            except nextcord.HTTPException as e:
                logger.error(f"Failed to remove top roles from {member.display_name}: {e}")
        for user_id, role in winners.items():
            member = guild.get_member(user_id)
            if not member:
                continue
            try:
                await member.add_roles(role, reason="Top Plex user.")
                others = [r for r in roles if r != role and r in member.roles]
                if others:
                    await member.remove_roles(*others, reason="Clearing other top roles.")
            except nextcord.HTTPException as e:
                logger.error(f"Failed to assign a top role to {member.display_name}: {e}")

    # --- /plex stats -------------------------------------------------------------

    @plex.subcommand(description="Server stats, with pages for movies and TV")
    async def stats(
        self,
        interaction: nextcord.Interaction,
        days: int = nextcord.SlashOption(
            description="How many days back (default 30)", required=False, min_value=1, max_value=3650,
        ),
    ):
        await interaction.response.defer()
        days = days or to_int(config.get("defaults", "time_range", 30), 30)
        *responses, libraries_r = await asyncio.gather(
            *(self.tautulli.get_home_stats(params={"stat_id": stat_id, "time_range": days, **params})
              for stat_id, params in _STATS.items()),
            self.tautulli.get_libraries_table(),
        )
        stats = {stat_id: self._rows(response) for stat_id, response in zip(_STATS, responses)}
        if all(rows is None for rows in stats.values()):
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        stats = {stat_id: rows or [] for stat_id, rows in stats.items()}
        libraries = []
        if Tautulli.check_response(libraries_r):
            libraries = (Tautulli.get_response_data(libraries_r, {}) or {}).get("data") or []
        movies, shows = stats["popular_movies"], stats["popular_tv"]
        ignored = ignored_usernames()
        users = [row for row in stats["top_users"] if plex_username(row) not in ignored]
        strips = await asyncio.gather(*(
            _poster_strip(self.tautulli, rows) for rows in (movies[:6] + shows[:6], movies, shows)
        ))
        view = StatsView({
            "Overview": (overview_card(stats, libraries, days=days, strip=strips[0]), strips[0]),
            "Movies": (media_card("movie", stats, libraries, days=days, strip=strips[1]), strips[1]),
            "TV": (media_card("show", stats, libraries, days=days, strip=strips[2]), strips[2]),
            "Users": (users_card(users, name=self._mention,
                                 avatar=viewer_avatar(self.bot, users[0]) if users else None), None),
        })
        view.message = bot_token_handle(interaction, await view.show("Overview").send(interaction))

    # --- /plex shows -------------------------------------------------------------

    @plex.subcommand(description="Top TV watchers of all time")
    async def shows(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        libraries = self._rows(await self.tautulli.get_libraries(), [])
        if libraries is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        tv = [lib for lib in libraries if lib.get("section_type") == "show"]
        responses = await asyncio.gather(*(self.tautulli.get_library_user_stats(section_id=lib["section_id"])
                                           for lib in tv))
        ranked = tv_watchers(tv, responses, ignored_usernames())
        if not ranked:
            await empty_state("Nobody's watched any TV yet.").send(interaction)
            return
        embed = shows_card(ranked, name=self._mention, several_libraries=len(tv) > 1)
        avatar = viewer_avatar(self.bot, ranked[0][0])
        if avatar:
            embed.set_thumbnail(url=avatar)
        await Reply([embed]).send(interaction)

    # --- /plex history -----------------------------------------------------------

    @plex.subcommand(description="Recent plays on the server, or one person's")
    async def history(
        self,
        interaction: nextcord.Interaction,
        user: str = nextcord.SlashOption(
            description="Whose history (default: everyone)", required=False, autocomplete=True, max_length=100,
        ),
    ):
        await interaction.response.defer()
        person = None
        params = {"length": _HISTORY_ROWS + 25, "order_column": "date", "order_dir": "desc"}
        if user:
            person, ok = await self.users.resolve(interaction, user)
            if not ok:
                return
            params.update(user_id=person.get("user_id"), length=_HISTORY_ROWS)
        response = await self.tautulli.get_history(params)
        if not Tautulli.check_response(response):
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        ignored = ignored_usernames()
        rows = [row for row in Tautulli.get_response_data(response, {}).get("data") or []
                if plex_username(row) not in ignored][:_HISTORY_ROWS]
        if not rows:
            who = f"**{self._mention(person)}** hasn't" if person else "Nobody has"
            await empty_state(f"{who} watched anything yet.").send(interaction)
            return
        strip = await _poster_strip(self.tautulli, rows)
        embed = history_card(rows, name=self._mention, person=person, strip=strip)
        if person:
            embed.set_author(name=truncate(self._name(person), Limits.AUTHOR),
                             icon_url=viewer_avatar(self.bot, person) or None)
        await Reply([embed], [strip] if strip else []).send(interaction)

    @history.on_autocomplete("user")
    async def _history_user_autocomplete(self, interaction: nextcord.Interaction, user: str):
        await interaction.response.send_autocomplete(await self.users.choices(user))

    # --- /plex hot ---------------------------------------------------------------

    @plex.subcommand(description="What's been popular lately: trending titles, binges, new hits")
    async def hot(
        self,
        interaction: nextcord.Interaction,
        days: int = nextcord.SlashOption(
            description="How many days back (default 7)", required=False, min_value=1, max_value=365,
        ),
    ):
        await interaction.response.defer()
        days = days or 7
        since = datetime.now() - timedelta(days=days)
        rows, recent_r = await asyncio.gather(
            self.tautulli.get_history_rows({
                # Tautulli reads "after" as a date in its own timezone: start a day early, then cut
                # off exactly below (as /plex chart does).
                "after": (since.date() - timedelta(days=1)).isoformat(),
                "order_column": "date", "order_dir": "desc",
            }),
            self.tautulli.get_recently_added(count=50),
        )
        if rows is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        cutoff, ignored = since.timestamp(), ignored_usernames()
        history = [row for row in rows if to_int(row.get("date")) >= cutoff and plex_username(row) not in ignored]
        if not history:
            await empty_state(f"Nobody's watched anything in the last {plural(days, 'day')}.").send(interaction)
            return
        recent = []
        if Tautulli.check_response(recent_r):
            recent = [item for item in (Tautulli.get_response_data(recent_r, {}) or {}).get("recently_added") or []
                      if to_int(item.get("added_at")) >= cutoff]
        summary = hot_summary(history, recent, days=days, name=self._mention)
        strip = await _poster_strip(self.tautulli, summary["posters"])
        await Reply([hot_card(summary, days=days, strip=strip)], [strip] if strip else []).send(interaction)


def setup(bot):
    bot.add_cog(PlexStats(bot))
