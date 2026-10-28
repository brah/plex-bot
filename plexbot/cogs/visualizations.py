"""/plex chart: when and what people watch, as charts.

One command opens on plays over time; dropdowns under the chart switch to another chart (or to the
library's growth) or one viewer, and buttons change the window and whether plays or watch time are
counted. /plex stats borrows the plays-over-time chart for its Activity page, and /plex hot its plays.
"""

import asyncio
import logging
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from functools import partial
from itertools import accumulate
from typing import Callable, Dict, List, Optional, Tuple

import nextcord
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from plexbot.errors import LoggedView
from plexbot.plex_users import PlexUsers
from plexbot.slash_commands import plex
from plexbot.tautulli_wrapper import Tautulli
from plexbot.ui.charts import POSTER_SLOTS, bar_chart, line_chart, poster_chart, stacked_day_chart
from plexbot.ui.embeds import Reply, bot_token_handle, card, empty_state, service_down, set_image
from plexbot.ui.format import join, plural, to_int, truncate
from plexbot.ui.images import Attachment, plex_image, poster_key
from plexbot.ui.theme import Colour, Limits, brand_colour
from plexbot.utilities import (
    format_duration, ignored_usernames, play_seconds, plex_username, viewer_avatar, viewer_name,
)

logger = logging.getLogger("plexbot.visualizations")

Play = Tuple[datetime, dict]  # when it started (in the server's timezone), the history row
# The history fields charts and /plex hot read; a play keeps only these, as a chart holds its plays for minutes.
_PLAY_FIELDS = (
    "user", "username", "friendly_name", "user_thumb",  # viewer names and avatars
    "media_type", "play_duration", "duration", "rating_key", "grandparent_rating_key", "watched_status",
    "title", "grandparent_title", "year", "thumb", "parent_thumb", "grandparent_thumb",  # titles and posters
)
Chart = Tuple[str, Callable]  # the one-line takeaway, and a no-argument renderer for a worker thread

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_KINDS = {"movie": "Movie", "episode": "TV"}  # anything else counts as "Other"
_MEDIA_COLOURS = {"Movie": f"#{Colour.MOVIE:06X}", "TV": f"#{Colour.TV:06X}", "Other": f"#{Colour.MUSIC:06X}"}
_TOP_USERS = 10
_WEEKLY_AFTER = 90  # days: longer windows count plays per week, or the bars are hairlines


@dataclass(frozen=True)
class Measure:
    """What a chart adds up for each play: 1 for plays, its seconds for watch time."""

    name: str  # "Plays", "Watch time": the button and the start of the title
    emoji: str
    axis: str  # the axis label
    weight: Callable[[dict], int]
    scale: int  # weights per drawn unit: watch time is summed in seconds but drawn in hours
    text: Callable[[int], str]  # a total, in words
    short: Callable[[int], str]  # a total, on top of a bar

    def draw(self, totals) -> list:
        return [total / self.scale for total in totals]


def _short_hours(seconds: int) -> str:
    return f"{seconds / 3600:,.0f}h" if seconds >= 3600 else f"{seconds // 60}m"


PLAY_COUNT = Measure("Plays", "▶️", "Plays", lambda row: 1, 1, lambda total: plural(total, "play"),
                     lambda total: f"{total:,}")
WATCH_TIME = Measure("Watch time", "⏱️", "Hours", play_seconds, 3600, format_duration, _short_hours)


def hour_label(hour: int) -> str:
    return f"{hour % 12 or 12}{'am' if hour < 12 else 'pm'}"


def utc_offset(zone: tzinfo, now: Optional[datetime] = None) -> str:
    """"UTC+10", "UTC-3:30", "UTC" for the zone's current offset."""
    offset = (now or datetime.now(zone)).astimezone(zone).utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    if not minutes:
        return "UTC"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{'+' if minutes > 0 else '-'}{hours}" + (f":{rest:02d}" if rest else "")


def _busiest(labels, counts) -> Tuple[str, int]:
    index = max(range(len(counts)), key=counts.__getitem__)
    return labels[index], counts[index]


# --- one function per chart: (takeaway, renderer) ----------------------------------


def hours_chart(plays: List[Play], measure: Measure = PLAY_COUNT) -> Chart:
    counts = [0] * 24
    for started, row in plays:
        counts[started.hour] += measure.weight(row)
    labels = [hour_label(h) for h in range(24)]
    label, count = _busiest(labels, counts)
    return (f"Busiest hour: **{label}** · {measure.text(count)}",
            partial(bar_chart, labels, measure.draw(counts), filename="chart-hours.png", label_every=3,
                    unit=measure.axis))


def days_chart(plays: List[Play], measure: Measure = PLAY_COUNT) -> Chart:
    counts = [0] * 7
    for started, row in plays:
        counts[started.weekday()] += measure.weight(row)
    label, count = _busiest(_WEEKDAYS, counts)
    return (f"Busiest day: **{label}** · {measure.text(count)}",
            partial(bar_chart, [d[:3] for d in _WEEKDAYS], measure.draw(counts), filename="chart-days.png",
                    unit=measure.axis))


def users_chart(plays: List[Play], name: Callable[[dict], str], measure: Measure = PLAY_COUNT) -> Chart:
    totals, first_row = Counter(), {}
    for _started, row in plays:
        totals[plex_username(row)] += measure.weight(row)
        first_row.setdefault(plex_username(row), row)
    top = totals.most_common(_TOP_USERS)
    labels = [truncate(name(first_row[user]), 24) for user, _total in top]
    values = [total for _user, total in top]
    return (f"Most active: **{escape_markdown(labels[0])}** · {measure.text(values[0])}",
            partial(bar_chart, labels, measure.draw(values), filename="chart-users.png", horizontal=True,
                    unit=measure.axis))


def media_chart(plays: List[Play], *, today: date, days: int, measure: Measure = PLAY_COUNT) -> Chart:
    """Movie, TV and other plays per day (per week past _WEEKLY_AFTER days), up to today."""
    step = 7 if days > _WEEKLY_AFTER else 1
    buckets = days // step  # whole weeks only: a part-week at the start would read as a dip
    first = today - timedelta(days=buckets * step - 1)
    dates = [first + timedelta(days=i * step) for i in range(buckets)]
    series = {"Movie": [0] * buckets, "TV": [0] * buckets, "Other": [0] * buckets}
    totals = Counter()
    for started, row in plays:
        kind = _KINDS.get(row.get("media_type"), "Other")
        if 0 <= (today - started.date()).days < days:
            totals[kind] += measure.weight(row)
        offset = (started.date() - first).days
        if 0 <= offset < buckets * step:
            series[kind][offset // step] += measure.weight(row)
    rate, unit = sum(totals.values()) / days * step, "week" if step == 7 else "day"
    if measure is PLAY_COUNT:
        parts = {"TV": plural(totals["TV"], "episode"), "Movie": plural(totals["Movie"], "movie"),
                 "Other": f"{totals['Other']:,} other"}
        average = f"{rate:.1f} plays a {unit}" if rate >= 0.05 else None  # never "0.0"
    else:
        parts = {"TV": f"{format_duration(totals['TV'])} of TV",
                 "Movie": f"{format_duration(totals['Movie'])} of movies",
                 "Other": f"{format_duration(totals['Other'])} other"}
        average = f"{format_duration(rate)} a {unit}" if rate >= 60 else None
    takeaway = join([text for kind, text in parts.items() if totals[kind]] + [average])
    drawn = {kind: measure.draw(values) for kind, values in series.items()}
    return takeaway, partial(stacked_day_chart, dates, drawn, _MEDIA_COLOURS, filename="chart-media.png",
                             width=0.8 * step, unit=measure.axis)


def months_chart(plays: List[Play], *, today: date, days: int, measure: Measure = PLAY_COUNT) -> Chart:
    start = today - timedelta(days=days - 1)
    months, cursor = [], date(start.year, start.month, 1)
    while cursor <= today:
        months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    index = {(m.year, m.month): i for i, m in enumerate(months)}
    counts = [0] * len(months)
    for started, row in plays:
        position = index.get((started.year, started.month))
        if position is not None:
            counts[position] += measure.weight(row)
    # "Oct\n2025", "Nov", ..., "Jan\n2026": the year only where it changes, so labels never collide.
    labels = [m.strftime("%b") + (f"\n{m.year}" if i == 0 or m.month == 1 else "") for i, m in enumerate(months)]
    label, count = _busiest([m.strftime("%B %Y") for m in months], counts)
    return (f"Busiest month: **{label}** · {measure.text(count)}",
            partial(bar_chart, labels, measure.draw(counts), filename="chart-months.png", unit=measure.axis,
                    label_every=max(1, -(-len(months) // 13))))  # a year fits unthinned


_TITLE_KINDS = {"shows": "episode", "movies": "movie"}  # the top-title charts, and the plays they count


def _title(row: dict) -> str:
    if row.get("media_type") == "episode":
        return row.get("grandparent_title") or "Unknown"
    year = row.get("year")
    return f"{row.get('title') or 'Unknown'}" + (f" ({year})" if year else "")


def top_titles(plays: List[Play], media_type: str, measure: Measure = PLAY_COUNT) -> List[Tuple[dict, int]]:
    """The most watched shows (media_type "episode", summed per show) or movies: [(a play's row, total)]."""
    totals, rows = Counter(), {}
    for _started, row in plays:
        if row.get("media_type") != media_type:
            continue
        if media_type == "episode":
            key = str(row.get("grandparent_rating_key") or row.get("grandparent_title"))
        else:
            key = str(row.get("rating_key") or row.get("title"))
        totals[key] += measure.weight(row)
        rows.setdefault(key, row)
    return [(rows[key], total) for key, total in totals.most_common(POSTER_SLOTS) if total]


def titles_chart(top: List[Tuple[dict, int]], posters: List[Optional[bytes]], *, filename: str,
                 measure: Measure = PLAY_COUNT) -> Chart:
    """A bar per title in `top` with its poster under it; the takeaway ranks them by name, left to right."""
    names = [_title(row) for row, _total in top]
    values = [total for _row, total in top]
    ranking = "\n".join(f"`{rank}.` **{escape_markdown(truncate(name, 60))}** · {measure.text(total)}"
                        for rank, (name, total) in enumerate(zip(names, values), 1))
    return ranking, partial(poster_chart, measure.draw(values), posters, names, [measure.short(v) for v in values],
                            filename=filename, unit=measure.axis)


_LIBRARY_KINDS = {"movie": "Movies", "show": "TV shows"}  # library section type -> its line
_LIBRARY_COLOURS = {"Movies": _MEDIA_COLOURS["Movie"], "TV shows": _MEDIA_COLOURS["TV"]}


def library_chart(titles: List[Tuple[date, str]], *, today: date, days: int) -> Chart:
    """A running count of the movies and shows added over the window, from zero, and what's there now."""
    first = today - timedelta(days=days - 1)
    dates = [first + timedelta(days=i) for i in range(days)]
    added = {kind: [0] * days for kind in _LIBRARY_KINDS.values()}
    for added_on, kind in titles:
        offset = (added_on - first).days
        if 0 <= offset < days:
            added[kind][offset] += 1
    new, owned = {kind: sum(values) for kind, values in added.items()}, Counter(kind for _day, kind in titles)
    takeaway = join([
        join([f"**+{plural(new['Movies'], 'movie')}**" if new["Movies"] else None,
              f"**+{plural(new['TV shows'], 'show')}**" if new["TV shows"] else None]) or "Nothing added",
        f"now {plural(owned['Movies'], 'movie')} and {plural(owned['TV shows'], 'show')}",
    ])
    running = {kind: list(accumulate(values)) for kind, values in added.items()}
    return takeaway, partial(line_chart, dates, running, _LIBRARY_COLOURS, filename="chart-library.png",
                             unit="Titles added")


# kind -> (emoji, dropdown label, dropdown description, shows the timezone), in dropdown order.
_CHARTS = {
    "media": ("🎞️", "Over time", "Movies and TV each day (each week past 90 days)", False),
    "hours": ("🕘", "By hour of day", "What time of day people watch", True),
    "days": ("📅", "By day of week", "Which days people watch most", True),
    "users": ("👥", "Per viewer", "Who watches the most", False),
    "months": ("📈", "Per month", "The long view, a bar a month", False),
    "shows": ("📺", "Top shows", "The most watched TV shows, with posters", False),
    "movies": ("🎬", "Top movies", "The most watched movies, with posters", False),
    "library": ("📚", "Library growth", "Movies and shows added to the library", False),
}
_DEFAULT_DAYS = 30
_WINDOWS = {"7 days": 7, "30 days": 30, "90 days": 90, "1 year": 365}  # the window buttons under a chart
_MONTHS_MIN_DAYS = 90  # a month-by-month chart over a shorter window is a bar or two


def chart_title(kind: str, days: int, measure: Measure = PLAY_COUNT) -> str:
    """"📺 Top shows by watch time", "🕘 Plays by hour of day", "🎞️ Watch time per week"."""
    emoji, label, _description, _zone = _CHARTS[kind]
    if kind == "library":  # titles, not plays
        return f"{emoji} {label}"
    if kind in _TITLE_KINDS:
        return f"{emoji} {label} by {measure.name.lower()}"
    if kind == "media":
        label = "per week" if days > _WEEKLY_AFTER else "per day"
    return f"{emoji} {measure.name} {label[0].lower()}{label[1:]}"


class Visualizations(commands.Cog):
    """Charts of when and what people watch."""

    help_category = "charts"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.users = PlexUsers(bot, self.tautulli)

    async def window_start(self, days: int) -> datetime:
        """When the last `days` days begin: midnight, `days - 1` days before today, in the server's timezone.

        Whole days, as the per-day and per-month bars count them, so the header's play count is what the
        bars add up to. /plex hot uses the same window.
        """
        zone = await self.tautulli.server_timezone()
        return datetime.combine(datetime.now(zone).date() - timedelta(days=days - 1), time.min, tzinfo=zone)

    async def plays(self, days: int) -> Optional[List[Play]]:
        """Everyone's plays in the last `days` days, but ignored users'; None if Tautulli didn't answer."""
        return await self.plays_between(await self.window_start(days))

    async def plays_between(self, since: datetime, until: Optional[datetime] = None) -> Optional[List[Play]]:
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
        rows = await self.tautulli.get_history_rows(params, fields=(*_PLAY_FIELDS, "started", "date"))
        if rows is None:
            logger.error("Failed to retrieve watch history from Tautulli.")
            return None
        ignored = ignored_usernames()
        plays = []
        for row in rows:
            started = to_int(row.get("started")) or to_int(row.get("date"))
            if not started or plex_username(row) in ignored:
                continue
            moment = datetime.fromtimestamp(started, zone)
            if moment >= since and (until is None or moment < until):
                plays.append((moment, {key: row[key] for key in _PLAY_FIELDS if key in row}))
        return plays

    async def posters(self, cache: Dict[str, Optional[bytes]], rows) -> List[Optional[bytes]]:
        """Each row's poster (None where Plex has none), fetching only those not already in `cache`."""
        keys = [poster_key(row) for row in rows]
        missing = [key for key in dict.fromkeys(keys) if key and key not in cache]
        fetched = await asyncio.gather(*(
            plex_image(self.tautulli, key, filename="poster.jpg", width=100, height=150) for key in missing
        ))
        cache.update({key: image.data if image else None for key, image in zip(missing, fetched)})
        return [cache.get(key) for key in keys]

    async def library_titles(self) -> Optional[List[Tuple[date, str]]]:
        """The day each movie and show in the library was added (in the server's timezone), with its line;
        None if Tautulli didn't answer. A show counts once, whatever its episodes."""
        response = await self.tautulli.get_libraries_table()
        if not Tautulli.check_response(response):
            return None
        libraries = [library for library in (Tautulli.get_response_data(response, {}) or {}).get("data") or []
                     if library.get("section_type") in _LIBRARY_KINDS]
        tables = await asyncio.gather(*(self.tautulli.get_library_media_rows(library["section_id"])
                                        for library in libraries))
        if any(rows is None for rows in tables):
            return None  # a library missing would read as titles never added
        zone = await self.tautulli.server_timezone()
        return [(datetime.fromtimestamp(to_int(row.get("added_at")), zone).date(),
                 _LIBRARY_KINDS[library["section_type"]])
                for library, rows in zip(libraries, tables) for row in rows if to_int(row.get("added_at"))]

    async def library_reply(self, view: "ChartView") -> Reply:
        """The library-growth chart (view.library already fetched), with the view under it."""
        title = chart_title("library", view.days)
        if not view.library:
            reply = empty_state("There are no movies or shows in the library yet.", title=title)
            reply.view = view
            return reply
        zone = await self.tautulli.server_timezone()
        takeaway, render = library_chart(view.library, today=datetime.now(zone).date(), days=view.days)
        image = await asyncio.to_thread(render)
        embed = card(title=title, description=f"-# Last {plural(view.days, 'day')}\n{takeaway}",
                     colour=brand_colour())
        set_image(embed, image)
        return Reply([embed], [image] if image else [], view=view)

    def _build(self, kind: str, plays: List[Play], days: int, today: date, measure: Measure) -> Chart:
        if kind == "hours":
            return hours_chart(plays, measure)
        if kind == "days":
            return days_chart(plays, measure)
        if kind == "users":
            return users_chart(plays, lambda row: viewer_name(self.bot, row), measure)
        if kind == "media":
            return media_chart(plays, today=today, days=days, measure=measure)
        return months_chart(plays, today=today, days=days, measure=measure)

    async def chart_reply(self, view: "ChartView") -> Reply:
        """The view's chart as it stands (view.plays already fetched), with the view under it."""
        if view.kind == "library":
            return await self.library_reply(view)
        kind, days, measure = view.kind, view.days, view.measure
        title, plays, viewer = chart_title(kind, days, measure), view.shown(), view.viewer_row()
        if kind in _TITLE_KINDS:  # the header counts only the plays the chart ranks
            plays = [play for play in plays if play[1].get("media_type") == _TITLE_KINDS[kind]]
        top = top_titles(plays, _TITLE_KINDS[kind], measure) if kind in _TITLE_KINDS else None
        if not plays or top == []:
            what = {"shows": "any TV", "movies": "any movies"}.get(kind, "anything")
            who = f"**{escape_markdown(viewer_name(self.bot, viewer))}** hasn't" if viewer else "Nobody has"
            reply = empty_state(f"{who} watched {what} in the last {plural(days, 'day')}.", title=title)
            reply.view = view
            return reply
        zone = await self.tautulli.server_timezone()
        if top:
            posters = await self.posters(view.poster_cache, [row for row, _total in top])
            takeaway, render = titles_chart(top, posters, filename=f"chart-{kind}.png", measure=measure)
        else:
            takeaway, render = self._build(kind, plays, days, datetime.now(zone).date(), measure)
        image = await asyncio.to_thread(render)
        context = join([
            f"Last {plural(days, 'day')}",
            plural(len(plays), "play"),
            f"{format_duration(sum(map(play_seconds, (row for _started, row in plays))))} watched"
            if measure is WATCH_TIME else None,
            f"times in {zone} ({utc_offset(zone)})" if _CHARTS[kind][3] else None,
        ])
        embed = card(title=title, description=f"-# {context}\n{takeaway}", colour=brand_colour())
        if viewer:
            embed.set_author(name=truncate(viewer_name(self.bot, viewer), Limits.AUTHOR),
                             icon_url=viewer_avatar(self.bot, viewer) or None)
        set_image(embed, image)
        return Reply([embed], [image] if image else [], view=view)

    async def activity_page(self, days: int) -> Optional[Tuple[nextcord.Embed, Optional[Attachment]]]:
        """/plex stats' Activity page: everyone's plays over time, with the busiest hour, day and viewer;
        None if Tautulli didn't answer."""
        plays = await self.plays(days)
        if plays is None:
            return None
        title, context = "📈 Activity", f"Last {plural(days, 'day')}"
        if not plays:
            return card(title=title, description=f"-# {context}\nNobody has watched anything.",
                        colour=brand_colour()), None
        zone = await self.tautulli.server_timezone()
        takeaway, render = media_chart(plays, today=datetime.now(zone).date(), days=days)
        image = await asyncio.to_thread(render)
        lines = [
            "-# " + join([context, plural(len(plays), "play"), f"times in {zone} ({utc_offset(zone)})"]),
            takeaway,
            "",
            hours_chart(plays)[0],
            days_chart(plays)[0],
            users_chart(plays, lambda row: viewer_name(self.bot, row))[0],
        ]
        embed = card(title=title, description="\n".join(lines), colour=brand_colour())
        set_image(embed, image)
        return embed, image

    # --- /plex chart ---------------------------------------------------------------

    @plex.subcommand(name="chart", description="Charts of plays and watch time, and the top shows and movies")
    async def chart(
        self,
        interaction: nextcord.Interaction,
        user: str = nextcord.SlashOption(description="Start on this person's plays (default: everyone)",
                                         required=False, autocomplete=True, max_length=100),
        days: int = nextcord.SlashOption(description=f"How many days back (default {_DEFAULT_DAYS})",
                                         required=False, min_value=1, max_value=3650),
    ):
        await interaction.response.defer()
        person = None
        if user:
            person, ok = await self.users.resolve(interaction, user)
            if not ok:
                return
        view = ChartView(self, person, days or _DEFAULT_DAYS)
        reply = await view.render()
        if reply is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        view.message = bot_token_handle(interaction, await reply.send(interaction))

    @chart.on_autocomplete("user")
    async def _chart_user_autocomplete(self, interaction: nextcord.Interaction, user: str):
        await interaction.response.send_autocomplete(await self.users.choices(user))


_EVERYONE = "*everyone"  # the viewer dropdown's value for everyone; "*" never starts a Plex username


class ChartKindSelect(nextcord.ui.StringSelect):
    def __init__(self, current: str):
        options = [nextcord.SelectOption(label=label, emoji=emoji, description=description, value=kind,
                                         default=kind == current)
                   for kind, (emoji, label, description, _zone) in _CHARTS.items()]
        # nextcord 3.2 StringSelect never generates a custom_id itself (see StreamDetailsSelect).
        super().__init__(custom_id=os.urandom(16).hex(), options=options, row=0)

    async def callback(self, interaction: nextcord.Interaction):
        await self.view.update(interaction, kind=self.values[0])


class ChartViewerSelect(nextcord.ui.StringSelect):
    def __init__(self, view: "ChartView"):
        counts = Counter(plex_username(row) for _started, row in view.plays)
        rows = {}
        for _started, row in view.plays:
            rows.setdefault(plex_username(row), row)
        usernames = [user for user, _count in counts.most_common(Limits.SELECT_OPTIONS - 1)]
        if view.viewer and view.viewer not in usernames:  # chosen, but quiet in this window
            usernames = [view.viewer, *usernames[:Limits.SELECT_OPTIONS - 2]]
        options = [nextcord.SelectOption(label="Everyone", emoji="👥", value=_EVERYONE, default=not view.viewer)]
        for user in usernames:
            row = rows.get(user) or view.viewer_row()
            options.append(nextcord.SelectOption(
                label=truncate(viewer_name(view.cog.bot, row), Limits.SELECT_LABEL), value=user,
                description=plural(counts[user], "play"), default=user == view.viewer,
            ))
        super().__init__(custom_id=os.urandom(16).hex(), options=options, row=1)

    async def callback(self, interaction: nextcord.Interaction):
        value = self.values[0]
        await self.view.update(interaction, viewer=None if value == _EVERYONE else value)


class ChartWindowButton(nextcord.ui.Button):
    def __init__(self, label: str, days: int, view: "ChartView"):
        style = nextcord.ButtonStyle.primary if days == view.days else nextcord.ButtonStyle.secondary
        disabled = view.kind == "months" and days < _MONTHS_MIN_DAYS
        super().__init__(label=label, style=style, disabled=disabled, row=2)
        self.days = days

    async def callback(self, interaction: nextcord.Interaction):
        await self.view.update(interaction, days=self.days)


class ChartMeasureButton(nextcord.ui.Button):
    def __init__(self, measure: Measure, view: "ChartView"):
        style = nextcord.ButtonStyle.primary if measure is view.measure else nextcord.ButtonStyle.secondary
        super().__init__(label=measure.name, emoji=measure.emoji, style=style, row=3)
        self.measure = measure

    async def callback(self, interaction: nextcord.Interaction):
        await self.view.update(interaction, measure=self.measure)


class ChartView(LoggedView):
    """Controls under a public /plex chart: which chart, whose plays, how far back, plays or watch time.
    Anyone can use them. Everyone's plays are fetched once per window, so the rest is free."""

    def __init__(self, cog: Visualizations, person: Optional[dict], days: int, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.cog, self.kind, self.days, self.measure = cog, "media", days, PLAY_COUNT
        self.person = person  # the command's `user`, for their name if they've no plays to take it from
        self.viewer: Optional[str] = plex_username(person) if person else None
        self.plays: Optional[List[Play]] = None
        self.poster_cache: Dict[str, Optional[bytes]] = {}  # poster key -> image, for the top-title charts
        self.library: Optional[List[Tuple[date, str]]] = None  # library_titles(), whatever the window
        self._lock = asyncio.Lock()  # one change at a time, so a chart never mixes two clicks' state

    def shown(self) -> List[Play]:
        """The plays the chart draws: everyone's, or the chosen viewer's."""
        if not self.viewer:
            return self.plays
        return [play for play in self.plays if plex_username(play[1]) == self.viewer]

    def viewer_row(self) -> Optional[dict]:
        """A row for the chosen viewer's name and avatar, or None for everyone."""
        if not self.viewer:
            return None
        rows = (row for _started, row in self.plays if plex_username(row) == self.viewer)
        fallback = self.person if self.person and plex_username(self.person) == self.viewer else None
        return next(rows, fallback or {"user": self.viewer})

    async def render(self) -> Optional[Reply]:
        """The current chart with fresh controls; None if Tautulli didn't answer."""
        if self.kind == "library":
            if self.library is None:
                self.library = await self.cog.library_titles()
                if self.library is None:
                    return None
        elif self.plays is None:
            self.plays = await self.cog.plays(self.days)
            if self.plays is None:
                return None
        self.clear_items()
        self.add_item(ChartKindSelect(self.kind))
        # Per viewer is everyone side by side, and the library isn't anyone's plays.
        if self.kind not in ("users", "library"):
            self.add_item(ChartViewerSelect(self))
        for label, days in _WINDOWS.items():
            self.add_item(ChartWindowButton(label, days, self))
        if self.kind != "library":
            for measure in (PLAY_COUNT, WATCH_TIME):
                self.add_item(ChartMeasureButton(measure, self))
        return await self.cog.chart_reply(self)

    async def update(self, interaction: nextcord.Interaction, **changes) -> None:
        """Apply a click's changes (kind, days, viewer, measure) and redraw."""
        await interaction.response.defer()
        self.message = interaction.message  # bot-token handle; the command's webhook expires
        async with self._lock:
            before = (self.kind, self.days, self.viewer, self.measure, self.plays)
            self.kind = changes.get("kind", self.kind)
            self.measure = changes.get("measure", self.measure)
            days = changes.get("days", self.days)
            if self.kind == "months" and days < _MONTHS_MIN_DAYS:
                days = 365
            if days != self.days:
                self.days, self.plays = days, None
            if self.kind == "users":
                self.viewer = None
            elif "viewer" in changes:
                self.viewer = changes["viewer"]
            reply = await self.render()
            if reply is None:
                # The message still shows the old chart, so the next click must start from it.
                self.kind, self.days, self.viewer, self.measure, self.plays = before
                await service_down("Tautulli").send(interaction, ephemeral=True)
                return
            await reply.edit(interaction)


def setup(bot):
    bot.add_cog(Visualizations(bot))
