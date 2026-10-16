"""/plex chart hours, days, users, media and months: aggregation, rendering and the commands."""

import asyncio
import io
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import nextcord
import pytest
from PIL import Image

import utilities
from cogs.visualizations import (
    Visualizations, days_chart, hour_label, hours_chart, media_chart, months_chart, users_chart, utc_offset,
)
from fakes import GUILD_ID, FakeTautulli, Recorder, interaction


@pytest.fixture(autouse=True)
def mappings(monkeypatch):
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings",
                        staticmethod(lambda: [{"plex_username": "mallory", "ignore": True}]))


def _at(y, m, d, h=20):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


PLAYS = [
    (_at(2026, 10, 10, 21), {"user": "alice", "friendly_name": "Alice", "media_type": "episode"}),  # Saturday
    (_at(2026, 10, 10, 21), {"user": "alice", "friendly_name": "Alice", "media_type": "movie"}),
    (_at(2026, 10, 12, 9), {"user": "bob", "friendly_name": "Bob", "media_type": "episode"}),  # Monday
    (_at(2026, 9, 1, 21), {"user": "bob", "friendly_name": "Bob", "media_type": "track"}),
]


def _png_size(attachment):
    with Image.open(io.BytesIO(attachment.data)) as image:
        return image.size


class TestAggregation:
    def test_hours(self):
        takeaway, render = hours_chart(PLAYS)
        assert takeaway == "Busiest hour: **9pm** · 3 plays"
        assert render.args[1][21] == 3 and render.args[1][9] == 1 and len(render.args[1]) == 24
        assert [hour_label(h) for h in (0, 9, 12, 23)] == ["12am", "9am", "12pm", "11pm"]

    def test_days(self):
        takeaway, render = days_chart(PLAYS)
        assert takeaway == "Busiest day: **Saturday** · 2 plays"
        assert render.args[0] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        assert render.args[1] == [1, 1, 0, 0, 0, 2, 0]

    def test_users(self):
        takeaway, render = users_chart(PLAYS, lambda row: row["friendly_name"])
        assert takeaway == "Most active: **Alice** · 2 plays"
        assert render.args[:2] == (["Alice", "Bob"], [2, 2])
        assert render.keywords["horizontal"] is True

    def test_media_fills_every_day(self):
        takeaway, render = media_chart(PLAYS, today=date(2026, 10, 15), days=7)
        dates, series = render.args[0], render.args[1]
        assert dates[0] == date(2026, 10, 9) and dates[-1] == date(2026, 10, 15) and len(dates) == 7
        assert series["TV"] == [0, 1, 0, 1, 0, 0, 0] and series["Movie"] == [0, 1, 0, 0, 0, 0, 0]
        assert takeaway == "2 episodes · 1 movie"  # the September track is outside the window

    def test_months_fill_gaps_and_label_years_once(self):
        takeaway, render = months_chart(PLAYS, today=date(2026, 10, 15), days=120)
        labels, counts = render.args
        assert labels == ["Jun\n2026", "Jul", "Aug", "Sep", "Oct"]
        assert counts == [0, 0, 0, 1, 3]
        assert takeaway == "Busiest month: **October 2026** · 3 plays"
        across_years, _ = months_chart([], today=date(2026, 2, 1), days=90)[1].args
        assert across_years == ["Nov\n2025", "Dec", "Jan\n2026", "Feb"]

    def test_utc_offset(self):
        winter = datetime(2026, 7, 1, tzinfo=timezone.utc)
        assert utc_offset(ZoneInfo("Australia/Sydney"), winter) == "UTC+10"
        assert utc_offset(ZoneInfo("America/St_Johns"), winter) == "UTC-2:30"
        assert utc_offset(ZoneInfo("UTC"), winter) == "UTC"


class TestRendering:
    def test_every_chart_renders_a_png(self):
        for takeaway, render in (
            hours_chart(PLAYS), days_chart(PLAYS), users_chart(PLAYS, lambda row: row["friendly_name"]),
            media_chart(PLAYS, today=date(2026, 10, 15), days=30),
            months_chart(PLAYS, today=date(2026, 10, 15), days=365),
        ):
            attachment = render()
            assert attachment.filename.startswith("chart-") and attachment.data[:8] == b"\x89PNG\r\n\x1a\n"
            width, height = _png_size(attachment)
            assert width > height > 200

    def test_nothing_to_draw(self):
        from ui.charts import bar_chart, stacked_day_chart

        assert bar_chart(["a"], [0], filename="x.png") is None
        assert stacked_day_chart([date(2026, 1, 1)], {"TV": [0]}, {}, filename="x.png") is None


def _build(tautulli):
    from plexbot import PlexBot, install_slash_groups

    bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None,
                  default_guild_ids=[GUILD_ID])
    bot.shared_resources = {"tautulli": tautulli}
    bot._connection.application_id = 999
    install_slash_groups(bot)
    cog = Visualizations(bot)
    bot.add_cog(cog)
    return bot, cog


def _run(fn, tautulli):
    async def run():
        bot, cog = _build(tautulli)
        return await fn(bot, cog)

    return asyncio.run(run())


def _history(*rows_ago):
    """History rows that started `hours` ago, as (user, hours, media_type)."""
    now = int(time.time())
    return [{"user": user, "friendly_name": user.title(), "started": now - hours * 3600, "media_type": kind}
            for user, hours, kind in rows_ago]


class TestCommands:
    def test_server_chart_skips_ignored_users_and_names_the_timezone(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.settings = {"default_timezone": "Australia/Sydney"}
        tautulli.history = _history(("alice", 2, "movie"), ("mallory", 3, "movie"), ("bob", 24 * 40, "movie"))
        _run(lambda bot, cog: Visualizations.chart_hours.callback(cog, interaction(bot), user=None, days=None),
             tautulli)
        params = tautulli.history_calls[-1]
        assert "user_id" not in params and params["after"]
        sent = recorder.last("followup")
        embed = sent["embeds"][0]
        assert embed.title == "🕘 Busiest hours"
        assert embed.description.startswith("-# Last 30 days · 1 play · times in Australia/Sydney (UTC+1")
        assert embed.image.url == "attachment://chart-hours.png"
        assert [f.filename for f in sent["files"]] == ["chart-hours.png"]

    def test_one_persons_chart(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.users = [{"user_id": 7, "username": "alice", "friendly_name": "Alice"}]
        tautulli.history = _history(("alice", 2, "episode"))
        _run(lambda bot, cog: Visualizations.chart_months.callback(cog, interaction(bot), user="alice", days=None),
             tautulli)
        assert tautulli.history_calls[-1]["user_id"] == 7
        embed = recorder.last("followup")["embeds"][0]
        assert embed.author.name == "Alice" and embed.description.startswith("-# Last 365 days · 1 play\n")

    def test_unknown_user_is_private(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: Visualizations.chart_days.callback(cog, interaction(bot), user="nobody", days=7),
             FakeTautulli([]))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True and "No Plex user called **nobody**" in sent["embeds"][0].description

    def test_nothing_watched(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: Visualizations.chart_users.callback(cog, interaction(bot), days=7), FakeTautulli([]))
        assert "Nobody has watched anything in the last 7 days." in recorder.last("followup")["embeds"][0].description

    def test_tautulli_down(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: Visualizations.chart_media.callback(cog, interaction(bot), user=None, days=None),
             FakeTautulli([], reachable=False))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True and "Couldn't reach **Tautulli**" in sent["embeds"][0].description

    def test_unknown_tautulli_timezone_falls_back_to_the_host(self):
        tautulli = FakeTautulli([])
        tautulli.settings = {"default_timezone": "Mars/Olympus_Mons"}

        async def zone(bot, cog):
            return await cog.timezone()

        assert _run(zone, tautulli) is not None


def test_chart_commands_registered_with_user_suggestions():
    from cogs.utility_commands import collect_help_entries
    from slash_commands import command_paths

    async def run(bot, cog):
        paths = dict(command_paths(bot))
        return paths, collect_help_entries(bot)

    paths, entries = _run(run, FakeTautulli([]))
    for kind in ("hours", "days", "media", "months"):
        assert paths[f"/plex chart {kind}"].options["user"].autocomplete_callback is not None
    assert "user" not in paths["/plex chart users"].options
    charts = {e.usage for e in entries if e.category == "charts"}
    assert charts == {
        "/plex chart hours [user] [days]", "/plex chart days [user] [days]", "/plex chart users [days]",
        "/plex chart media [user] [days]", "/plex chart months [user] [days]",
    }
