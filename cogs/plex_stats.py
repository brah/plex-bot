# cogs/plex_stats.py

"""Stats commands: /plex top, /plex stats, /plex shows, /plex history and /plex hot."""

import asyncio
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Callable, Optional

import nextcord
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from config import config
from slash_commands import plex
from tautulli_wrapper import Tautulli
from ui.cards import media_icon, recent_viewer_line, session_title
from ui.embeds import ErrorKind, Reply, card, empty_state, error_state, service_down
from ui.format import join, plural, to_int, truncate
from ui.images import plex_image, poster_key, poster_strip
from ui.theme import Colour, Limits, brand_colour
from utilities import (
    UserMappings, format_duration, ignored_usernames, plex_username, viewer_avatar, viewer_name,
)

logger = logging.getLogger("plexbot.plex_stats")
logger.setLevel(logging.INFO)

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_RANKED = 10
_STRIP = 6


def rank_prefix(rank: int) -> str:
    return _MEDALS.get(rank, f"`{rank}.`")


def _escaped(text, limit: int = 80) -> str:
    return escape_markdown(truncate(str(text or "Unknown"), limit))


async def _poster_strip(tautulli, rows) -> Optional[object]:
    """Posters for rows (distinct titles, at most six) side by side, or None."""
    keys = []
    for row in rows:
        key = poster_key(row)
        if key and key not in keys:
            keys.append(key)
        if len(keys) == _STRIP:
            break
    posters = await asyncio.gather(*(
        plex_image(tautulli, key, filename=f"poster-{i}.jpg", width=200, height=300) for i, key in enumerate(keys)
    ))
    return await asyncio.to_thread(poster_strip, [p.data for p in posters if p])


# --- /plex top -------------------------------------------------------------------


def _last_watched(row: dict) -> Optional[str]:
    if row.get("media_type") == "episode":
        return row.get("grandparent_title") or row.get("title")
    return row.get("grandchild_title") or row.get("title")


def top_card(rows, *, days: int, by: str, name: Callable[[dict], str]) -> nextcord.Embed:
    lines = [f"-# Last {plural(days, 'day')} · by {'plays' if by == 'plays' else 'watch time'}"]
    for rank, row in enumerate(rows[:_RANKED], 1):
        watched = format_duration(to_int(row.get("total_duration")))
        plays = plural(to_int(row.get("total_plays")), "play")
        figures = [plays, watched] if by == "plays" else [watched, plays]
        lines.append(join([f"{rank_prefix(rank)} **{escape_markdown(name(row))}**", *figures]))
        last = _last_watched(row)
        if last:
            lines.append(f"-# Last watched {_escaped(last)}")
    total = sum(to_int(row.get("total_duration")) for row in rows[:_RANKED])
    embed = card(title="🏆 Top watchers", description="\n".join(lines), colour=Colour.GOLD)
    embed.set_footer(text=f"Together: {format_duration(total)}")
    return embed


# --- /plex stats -----------------------------------------------------------------


def library_line(rows) -> Optional[str]:
    movies = shows = episodes = watched = 0
    for row in rows or []:
        kind = row.get("section_type")
        if kind == "movie":
            movies += to_int(row.get("count"))
        elif kind == "show":
            shows += to_int(row.get("count"))
            episodes += to_int(row.get("child_count"))
        watched += to_int(row.get("duration"))
    return join([
        f"🎬 {plural(movies, 'movie')}" if movies else None,
        f"📺 {plural(shows, 'show')}" if shows else None,
        plural(episodes, "episode") if episodes else None,
        f"{format_duration(watched)} watched all time" if watched else None,
    ]) or None


def popular_lines(rows, limit: int = 5) -> Optional[str]:
    lines = [
        join([
            f"{rank_prefix(rank)} **{_escaped(session_title(row))}**",
            plural(to_int(row.get("total_plays")), "play"),
            plural(to_int(row.get("users_watched")), "person", "people"),
        ])
        for rank, row in enumerate(rows[:limit], 1)
    ]
    return "\n".join(lines) or None


def stats_card(libraries, movies, shows, *, days: int, strip) -> nextcord.Embed:
    description = join([f"-# Last {plural(days, 'day')}", library_line(libraries)], "\n")
    embed = card(title="📊 Server stats", description=description, colour=brand_colour())
    for heading, rows in (("🎬 Most popular movies", movies), ("📺 Most popular shows", shows)):
        lines = popular_lines(rows)
        if lines:
            embed.add_field(name=heading, value=truncate(lines, Limits.FIELD_VALUE), inline=False)
    if not (movies or shows):
        embed.add_field(name="Most popular", value=f"Nothing was watched in the last {plural(days, 'day')}.",
                        inline=False)
    if strip:
        embed.set_image(url=strip.url)
    return embed


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
        lines.append(f"{rank_prefix(rank)} **{escape_markdown(name(row))}** · {format_duration(seconds)}")
        if several_libraries:
            lines.append(f"-# {escape_markdown(', '.join(libraries))}")
    return card(title="📺 Top TV watchers", description="\n".join(lines), colour=Colour.GOLD)


# --- /plex history ---------------------------------------------------------------

_HISTORY_ROWS = 15
_USERS_TTL = 300


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
            f"{rank_prefix(rank)} **{escape_markdown(name(s['row']))}** · {plural(s['plays'], 'play')} · "
            f"{plural(len(s['titles']), 'title')}"
            for rank, s in enumerate(active, 1)
        ]))
    binges = sorted(
        ((user, *shows.most_common(1)[0]) for user, shows in episodes.items() if shows.most_common(1)[0][1] >= 5),
        key=lambda b: b[2], reverse=True,
    )[:3]
    if binges:
        sections.append(("🍿 Binge watchers", [
            f"**{escape_markdown(name(per_user[user]['row']))}** binged **{_escaped(show)}** ({count} episodes)"
            for user, show, count in binges
        ]))
    recent_keys = {str(item.get("rating_key")) for item in recent_items}
    discoveries = [t for key, t in titles.items() if key in recent_keys and len(t["users"]) == 1 and t["plays"] <= 2][:3]
    if discoveries:
        sections.append(("🧭 First to check out", [
            f"**{escape_markdown(name(t['row']))}** tried **{_escaped(t['title'])}**" for t in discoveries
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
        self._users: tuple = (0.0, [])  # (fetched at, Tautulli users)

    def _name(self, row: dict) -> str:
        return viewer_name(self.bot, row)

    @staticmethod
    def _rows(response, default=None):
        """The rows of a home-stats or list response; None when Tautulli failed."""
        if not Tautulli.check_response(response):
            return None
        data = Tautulli.get_response_data(response, default if default is not None else {})
        return data.get("rows", []) if isinstance(data, dict) else data

    # --- /plex top ---------------------------------------------------------------

    @plex.subcommand(description="Top watchers on the server, by watch time or plays")
    async def top(
        self,
        interaction: nextcord.Interaction,
        days: int = nextcord.SlashOption(
            description="How many days back (default: the server's setting)", required=False,
            min_value=1, max_value=3650,
        ),
        by: str = nextcord.SlashOption(
            description="What to rank by (default: watch time)", required=False,
            choices={"Watch time": "duration", "Plays": "plays"},
        ),
    ):
        await interaction.response.defer()
        default_days = to_int(config.get("defaults", "stats_duration", 7), 7)
        days, by = days or default_days, by or "duration"
        response = await self.tautulli.get_home_stats(params={
            "stats_type": by, "stat_id": "top_users", "stats_count": str(_RANKED + 10), "time_range": days,
        })
        rows = self._rows(response)
        if rows is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        ignored = ignored_usernames()
        ranked = [row for row in rows if plex_username(row) not in ignored][:_RANKED]
        if not ranked:
            await empty_state(f"Nobody's watched anything in the last {plural(days, 'day')}.").send(interaction)
            return
        embed = top_card(ranked, days=days, by=by, name=self._name)
        avatar = viewer_avatar(self.bot, ranked[0])
        if avatar:
            embed.set_thumbnail(url=avatar)
        await Reply([embed]).send(interaction)
        # Roles follow the server's standard ranking, not whatever window someone asked for.
        if interaction.guild and days == default_days and by == "duration":
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

    @plex.subcommand(description="Library totals and the most popular movies and shows")
    async def stats(
        self,
        interaction: nextcord.Interaction,
        days: int = nextcord.SlashOption(
            description="How many days back (default 30)", required=False, min_value=1, max_value=3650,
        ),
    ):
        await interaction.response.defer()
        days = days or to_int(config.get("defaults", "time_range", 30), 30)
        movies_r, shows_r, libraries_r = await asyncio.gather(
            self.tautulli.get_most_watched_movies(time_range=days),
            self.tautulli.get_most_watched_shows(time_range=days),
            self.tautulli.get_libraries_table(),
        )
        movies, shows = self._rows(movies_r), self._rows(shows_r)
        if movies is None and shows is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        movies, shows = movies or [], shows or []
        libraries = []
        if Tautulli.check_response(libraries_r):
            libraries = (Tautulli.get_response_data(libraries_r, {}) or {}).get("data") or []
        strip = await _poster_strip(self.tautulli, movies[:3] + shows[:3])
        embed = stats_card(libraries, movies, shows, days=days, strip=strip)
        await Reply([embed], [strip] if strip else []).send(interaction)

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
        embed = shows_card(ranked, name=self._name, several_libraries=len(tv) > 1)
        avatar = viewer_avatar(self.bot, ranked[0][0])
        if avatar:
            embed.set_thumbnail(url=avatar)
        await Reply([embed]).send(interaction)

    # --- /plex history -----------------------------------------------------------

    async def _plex_users(self) -> Optional[list]:
        """Tautulli's users minus ignored ones (cached briefly: autocomplete asks on every key)."""
        fetched, users = self._users
        if users and time.monotonic() - fetched < _USERS_TTL:
            return users
        rows = self._rows(await self.tautulli.get_users(), [])
        if rows is None:
            return None
        ignored = ignored_usernames()
        users = [row for row in rows if row.get("username") and row["username"] not in ignored]
        self._users = (time.monotonic(), users)
        return users

    def _user_label(self, row: dict) -> str:
        shown, plex_name = self._name(row), row.get("friendly_name") or row["username"]
        return shown if shown == plex_name else f"{shown} ({plex_name})"

    def _find_user(self, users: list, wanted: str) -> Optional[dict]:
        wanted = wanted.strip().lower()
        for row in users:
            names = {row["username"], row.get("friendly_name") or "", self._name(row)}
            if wanted in {n.lower() for n in names if n}:
                return row
        return None

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
            users = await self._plex_users()
            if users is None:
                await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
                return
            person = self._find_user(users, user)
            if person is None:
                await error_state(ErrorKind.INPUT, f"No Plex user called **{_escaped(user)}**.",
                                  hint="Pick one from the suggestions as you type.").send(
                    interaction, ephemeral=True, replace_placeholder=True)
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
            who = f"**{escape_markdown(self._name(person))}** hasn't" if person else "Nobody has"
            await empty_state(f"{who} watched anything yet.").send(interaction)
            return
        strip = await _poster_strip(self.tautulli, rows)
        embed = history_card(rows, name=self._name, person=person, strip=strip)
        if person:
            embed.set_author(name=truncate(self._name(person), Limits.AUTHOR),
                             icon_url=viewer_avatar(self.bot, person) or None)
        await Reply([embed], [strip] if strip else []).send(interaction)

    @history.on_autocomplete("user")
    async def _history_user_autocomplete(self, interaction: nextcord.Interaction, user: str):
        typed = (user or "").strip().lower()
        choices = {}
        for row in await self._plex_users() or []:
            label = truncate(self._user_label(row), Limits.SELECT_LABEL)
            if typed in label.lower() or typed in row["username"].lower():
                choices.setdefault(label, row["username"])
        await interaction.response.send_autocomplete(dict(list(choices.items())[:Limits.SELECT_OPTIONS]))

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
        history_r, recent_r = await asyncio.gather(
            self.tautulli.get_history({
                "after": since.strftime("%Y-%m-%d"), "order_column": "date", "order_dir": "desc",
                "length": to_int(config.get("defaults", "history_length", 10000), 10000),
            }),
            self.tautulli.get_recently_added(count=50),
        )
        if not Tautulli.check_response(history_r):
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        cutoff, ignored = since.timestamp(), ignored_usernames()
        history = [row for row in Tautulli.get_response_data(history_r, {}).get("data") or []
                   if to_int(row.get("date")) >= cutoff and plex_username(row) not in ignored]
        if not history:
            await empty_state(f"Nobody's watched anything in the last {plural(days, 'day')}.").send(interaction)
            return
        recent = []
        if Tautulli.check_response(recent_r):
            recent = [item for item in (Tautulli.get_response_data(recent_r, {}) or {}).get("recently_added") or []
                      if to_int(item.get("added_at")) >= cutoff]
        summary = hot_summary(history, recent, days=days, name=self._name)
        strip = await _poster_strip(self.tautulli, summary["posters"])
        await Reply([hot_card(summary, days=days, strip=strip)], [strip] if strip else []).send(interaction)


def setup(bot):
    bot.add_cog(PlexStats(bot))
