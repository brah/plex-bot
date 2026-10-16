# cogs/visualizations.py

"""/plex chart hours, days, users, media and months: when and what people watch, as charts."""

import asyncio
import logging
from collections import Counter
from datetime import date, datetime, timedelta, tzinfo
from functools import partial
from typing import Callable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import nextcord
import tzlocal
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from plex_users import PlexUsers
from slash_commands import plex
from tautulli_wrapper import Tautulli
from ui.charts import bar_chart, stacked_day_chart
from ui.embeds import Reply, card, empty_state, service_down, set_image
from ui.format import join, plural, to_int, truncate
from ui.theme import Colour, Limits, brand_colour
from utilities import ignored_usernames, plex_username, viewer_avatar, viewer_name

logger = logging.getLogger("plexbot.visualizations")

Play = Tuple[datetime, dict]  # when it started (in the server's timezone), the history row
Chart = Tuple[str, Callable]  # the one-line takeaway, and a no-argument renderer for a worker thread

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_KINDS = {"movie": "Movie", "episode": "TV"}  # anything else counts as "Other"
_MEDIA_COLOURS = {"Movie": f"#{Colour.MOVIE:06X}", "TV": f"#{Colour.TV:06X}", "Other": f"#{Colour.MUSIC:06X}"}
_TOP_USERS = 10


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


def hours_chart(plays: List[Play]) -> Chart:
    counts = [0] * 24
    for started, _row in plays:
        counts[started.hour] += 1
    labels = [hour_label(h) for h in range(24)]
    label, count = _busiest(labels, counts)
    return (f"Busiest hour: **{label}** · {plural(count, 'play')}",
            partial(bar_chart, labels, counts, filename="chart-hours.png", label_every=2))


def days_chart(plays: List[Play]) -> Chart:
    counts = [0] * 7
    for started, _row in plays:
        counts[started.weekday()] += 1
    label, count = _busiest(_WEEKDAYS, counts)
    return (f"Busiest day: **{label}** · {plural(count, 'play')}",
            partial(bar_chart, [d[:3] for d in _WEEKDAYS], counts, filename="chart-days.png"))


def users_chart(plays: List[Play], name: Callable[[dict], str]) -> Chart:
    counts = Counter(plex_username(row) for _started, row in plays)
    first_row = {}
    for _started, row in plays:
        first_row.setdefault(plex_username(row), row)
    top = counts.most_common(_TOP_USERS)
    labels = [truncate(name(first_row[user]), 24) for user, _count in top]
    values = [count for _user, count in top]
    return (f"Most active: **{escape_markdown(labels[0])}** · {plural(values[0], 'play')}",
            partial(bar_chart, labels, values, filename="chart-users.png", horizontal=True))


def media_chart(plays: List[Play], *, today: date, days: int) -> Chart:
    first = today - timedelta(days=days - 1)
    dates = [first + timedelta(days=i) for i in range(days)]
    series = {"Movie": [0] * days, "TV": [0] * days, "Other": [0] * days}
    for started, row in plays:
        index = (started.date() - first).days
        if 0 <= index < days:
            series[_KINDS.get(row.get("media_type"), "Other")][index] += 1
    totals = {kind: sum(values) for kind, values in series.items()}
    takeaway = join([
        plural(totals["TV"], "episode") if totals["TV"] else None,
        plural(totals["Movie"], "movie") if totals["Movie"] else None,
        f"{totals['Other']:,} other" if totals["Other"] else None,
    ])
    return takeaway, partial(stacked_day_chart, dates, series, _MEDIA_COLOURS, filename="chart-media.png")


def months_chart(plays: List[Play], *, today: date, days: int) -> Chart:
    start = today - timedelta(days=days - 1)
    months, cursor = [], date(start.year, start.month, 1)
    while cursor <= today:
        months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    index = {(m.year, m.month): i for i, m in enumerate(months)}
    counts = [0] * len(months)
    for started, _row in plays:
        position = index.get((started.year, started.month))
        if position is not None:
            counts[position] += 1
    # "Oct\n2025", "Nov", ..., "Jan\n2026": the year only where it changes, so labels never collide.
    labels = [m.strftime("%b") + (f"\n{m.year}" if i == 0 or m.month == 1 else "") for i, m in enumerate(months)]
    label, count = _busiest([m.strftime("%B %Y") for m in months], counts)
    return (f"Busiest month: **{label}** · {plural(count, 'play')}",
            partial(bar_chart, labels, counts, filename="chart-months.png",
                    label_every=max(1, len(months) // 24)))


# kind -> (embed title, default days, shows the timezone)
_CHARTS = {
    "hours": ("🕘 Busiest hours", 30, True),
    "days": ("📅 Busiest days", 30, True),
    "users": ("👥 Most active viewers", 30, False),
    "media": ("🎞️ Movies and TV, day by day", 30, False),
    "months": ("📈 Plays by month", 365, False),
}


def _user_option():
    return nextcord.SlashOption(description="Only this person's plays (default: everyone)", required=False,
                                autocomplete=True, max_length=100)


def _days_option(default: int):
    return nextcord.SlashOption(description=f"How many days back (default {default})", required=False,
                                min_value=1, max_value=3650)


class Visualizations(commands.Cog):
    """Charts of when and what people watch."""

    help_category = "charts"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.users = PlexUsers(bot, self.tautulli)
        self._zone: Optional[tzinfo] = None

    async def timezone(self) -> tzinfo:
        """The timezone Tautulli is set to (so 9pm means 9pm on the server), else the host's."""
        if self._zone is None:
            response = await self.tautulli.get_settings()
            if not Tautulli.check_response(response):
                return tzlocal.get_localzone()  # not remembered: ask Tautulli again next time
            name = (Tautulli.get_response_data(response, {}) or {}).get("default_timezone")
            try:
                self._zone = ZoneInfo(name) if name else tzlocal.get_localzone()
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning(f"Unknown Tautulli timezone {name!r}; using the host's.")
                self._zone = tzlocal.get_localzone()
        return self._zone

    async def plays(self, person: Optional[dict], days: int) -> Optional[List[Play]]:
        """Plays in the last `days` days (one person's, or everyone's but ignored users'); None if
        Tautulli didn't answer."""
        zone = await self.timezone()
        since = datetime.now(zone) - timedelta(days=days)
        params = {
            # Tautulli's "after" is a whole date; the exact cutoff is applied below.
            "after": (since.date() - timedelta(days=1)).isoformat(),
            "order_column": "date", "order_dir": "desc",
        }
        if person:
            params["user_id"] = person.get("user_id")
        rows = await self.tautulli.get_history_rows(params)
        if rows is None:
            logger.error("Failed to retrieve watch history from Tautulli.")
            return None
        ignored = set() if person else ignored_usernames()
        plays = []
        for row in rows:
            started = to_int(row.get("started")) or to_int(row.get("date"))
            if not started or plex_username(row) in ignored:
                continue
            moment = datetime.fromtimestamp(started, zone)
            if moment >= since:
                plays.append((moment, row))
        return plays

    def _build(self, kind: str, plays: List[Play], days: int, today: date) -> Chart:
        if kind == "hours":
            return hours_chart(plays)
        if kind == "days":
            return days_chart(plays)
        if kind == "users":
            return users_chart(plays, lambda row: viewer_name(self.bot, row))
        if kind == "media":
            return media_chart(plays, today=today, days=days)
        return months_chart(plays, today=today, days=days)

    async def send_chart(self, interaction: nextcord.Interaction, kind: str, user: Optional[str],
                         days: Optional[int]) -> None:
        await interaction.response.defer()
        title, default_days, shows_zone = _CHARTS[kind]
        days = days or default_days
        person = None
        if user:
            person, ok = await self.users.resolve(interaction, user)
            if not ok:
                return
        plays = await self.plays(person, days)
        if plays is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        who = escape_markdown(self.users.name(person)) if person else None
        if not plays:
            message = f"**{who}** hasn't" if who else "Nobody has"
            await empty_state(f"{message} watched anything in the last {plural(days, 'day')}.").send(interaction)
            return
        zone = await self.timezone()
        takeaway, render = self._build(kind, plays, days, datetime.now(zone).date())
        image = await asyncio.to_thread(render)
        context = join([
            f"Last {plural(days, 'day')}",
            plural(len(plays), "play"),
            f"times in {zone} ({utc_offset(zone)})" if shows_zone else None,
        ])
        embed = card(title=title, description=f"-# {context}\n{takeaway}", colour=brand_colour())
        if person:
            embed.set_author(name=truncate(self.users.name(person), Limits.AUTHOR),
                             icon_url=viewer_avatar(self.bot, person) or None)
        set_image(embed, image)
        await Reply([embed], [image] if image else []).send(interaction)

    # --- /plex chart ... ---------------------------------------------------------

    @plex.subcommand(name="chart", description="Charts of when and what people watch")
    async def chart(self, interaction: nextcord.Interaction):
        """Group root; Discord only ever invokes its subcommands."""

    @chart.subcommand(name="hours", description="Which hours of the day people watch")
    async def chart_hours(self, interaction: nextcord.Interaction, user: str = _user_option(),
                          days: int = _days_option(30)):
        await self.send_chart(interaction, "hours", user, days)

    @chart.subcommand(name="days", description="Which days of the week people watch")
    async def chart_days(self, interaction: nextcord.Interaction, user: str = _user_option(),
                         days: int = _days_option(30)):
        await self.send_chart(interaction, "days", user, days)

    @chart.subcommand(name="users", description="Who watches the most")
    async def chart_users(self, interaction: nextcord.Interaction, days: int = _days_option(30)):
        await self.send_chart(interaction, "users", None, days)

    @chart.subcommand(name="media", description="Movies, TV and other plays per day")
    async def chart_media(self, interaction: nextcord.Interaction, user: str = _user_option(),
                          days: int = _days_option(30)):
        await self.send_chart(interaction, "media", user, days)

    @chart.subcommand(name="months", description="Plays per month")
    async def chart_months(self, interaction: nextcord.Interaction, user: str = _user_option(),
                           days: int = _days_option(365)):
        await self.send_chart(interaction, "months", user, days)

    @chart_hours.on_autocomplete("user")
    @chart_days.on_autocomplete("user")
    @chart_media.on_autocomplete("user")
    @chart_months.on_autocomplete("user")
    async def _chart_user_autocomplete(self, interaction: nextcord.Interaction, user: str):
        await interaction.response.send_autocomplete(await self.users.choices(user))


def setup(bot):
    bot.add_cog(Visualizations(bot))
