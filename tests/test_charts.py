"""/plex chart hours, days, users, media and months: aggregation, rendering and the commands."""

import asyncio
import io
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import nextcord
from nextcord.ext import commands
import pytest
from PIL import Image

from plexbot import utilities
from plexbot.cogs.visualizations import (
    WATCH_TIME, Visualizations, days_chart, hour_label, hours_chart, library_chart, media_chart, months_chart,
    titles_chart, top_titles, users_chart, utc_offset,
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
        assert render.args[1][21] == 3
        assert render.args[1][9] == 1
        assert len(render.args[1]) == 24
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
        assert dates[0] == date(2026, 10, 9)
        assert dates[-1] == date(2026, 10, 15)
        assert len(dates) == 7
        assert series["TV"] == [0, 1, 0, 1, 0, 0, 0]
        assert series["Movie"] == [0, 1, 0, 0, 0, 0, 0]
        assert takeaway == "2 episodes · 1 movie · 0.4 plays a day"  # the September track is outside the window
        assert render.keywords["width"] == pytest.approx(0.8)

    def test_media_counts_whole_weeks_past_ninety_days(self):
        takeaway, render = media_chart(PLAYS, today=date(2026, 10, 15), days=120)
        dates, series = render.args[0], render.args[1]
        assert len(dates) == 17
        assert dates[-1] == date(2026, 10, 9)  # the last week ends today
        assert series["TV"][-1] == 2
        assert series["Movie"][-1] == 1
        assert sum(series["Other"]) == 1
        assert render.keywords["width"] == pytest.approx(5.6)
        assert takeaway == "2 episodes · 1 movie · 1 other · 0.2 plays a week"

    def test_months_fill_gaps_and_label_years_once(self):
        takeaway, render = months_chart(PLAYS, today=date(2026, 10, 15), days=120)
        labels, counts = render.args
        assert labels == ["Jun\n2026", "Jul", "Aug", "Sep", "Oct"]
        assert counts == [0, 0, 0, 1, 3]
        assert takeaway == "Busiest month: **October 2026** · 3 plays"
        across_years, _ = months_chart([], today=date(2026, 2, 1), days=90)[1].args
        assert across_years == ["Nov\n2025", "Dec", "Jan\n2026", "Feb"]

    def test_top_titles_sum_episodes_per_show(self):
        rows = [
            {"media_type": "episode", "grandparent_title": "Andor", "grandparent_rating_key": 1, "play_duration": 600},
            {"media_type": "episode", "grandparent_title": "Andor", "grandparent_rating_key": 1, "play_duration": 600},
            {"media_type": "episode", "grandparent_title": "Shogun", "grandparent_rating_key": 2,
             "play_duration": 7200},
            {"media_type": "movie", "title": "Heat", "year": 1995, "rating_key": 3, "duration": 9000},
        ]
        plays = [(_at(2026, 10, 10), row) for row in rows]
        assert [(row["grandparent_title"], n) for row, n in top_titles(plays, "episode")] == [
            ("Andor", 2), ("Shogun", 1)]
        by_time = top_titles(plays, "episode", WATCH_TIME)
        assert [(row["grandparent_title"], n) for row, n in by_time] == [("Shogun", 7200), ("Andor", 1200)]
        ranking, render = titles_chart(by_time, [None, None], filename="chart-shows.png", measure=WATCH_TIME)
        assert ranking == "`1.` **Shogun** · 2h\n`2.` **Andor** · 20m"
        assert render.args[0] == [2.0, 1200 / 3600]
        assert render.args[3] == ["2h", "20m"]
        movies = top_titles(plays, "movie")
        assert titles_chart(movies, [None], filename="x.png")[0] == "`1.` **Heat (1995)** · 1 play"

    def test_library_counts_titles_added_from_zero(self):
        titles = [(date(2026, 10, 14), "Movies"), (date(2026, 10, 14), "Movies"), (date(2026, 10, 15), "TV shows"),
                  (date(2020, 1, 1), "Movies")]
        takeaway, render = library_chart(titles, today=date(2026, 10, 15), days=3)
        assert takeaway == "**+2 movies** · **+1 show** · now 3 movies and 1 show"
        dates, running = render.args[0], render.args[1]
        assert dates == [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)]
        assert running == {"Movies": [0, 2, 2], "TV shows": [0, 0, 1]}
        quiet, _render = library_chart(titles[3:], today=date(2026, 10, 15), days=3)
        assert quiet == "Nothing added · now 1 movie and 0 shows"

    def test_watch_time_takeaway(self):
        plays = [(_at(2026, 10, 10), {"media_type": "episode", "play_duration": 5400}),
                 (_at(2026, 10, 11), {"media_type": "movie", "play_duration": 7200})]
        takeaway, render = media_chart(plays, today=date(2026, 10, 15), days=7, measure=WATCH_TIME)
        assert takeaway == "1h 30m of TV · 2h of movies · 30m a day"
        assert render.keywords["unit"] == "Hours"
        assert sum(render.args[1]["TV"]) == 1.5

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
            assert attachment.filename.startswith("chart-")
            assert attachment.data[:8] == b"\x89PNG\r\n\x1a\n"
            width, height = _png_size(attachment)
            assert width > height > 200

    def test_poster_chart_renders_with_and_without_posters(self):
        from fakes import _png
        from plexbot.ui.charts import poster_chart

        attachment = poster_chart([3, 1], [_png(), None], ["Andor", "Shogun"], ["3", "1"], filename="chart-shows.png")
        assert attachment.data[:8] == b"\x89PNG\r\n\x1a\n"
        assert poster_chart([0], [None], ["x"], ["0"], filename="x.png") is None

    def test_library_line_chart_renders(self):
        from plexbot.ui.charts import line_chart

        days = [date(2026, 10, 13), date(2026, 10, 14)]
        attachment = line_chart(days, {"Movies": [0, 2], "TV shows": [0, 0]}, {}, filename="chart-library.png",
                                unit="Titles added")
        assert attachment.data[:8] == b"\x89PNG\r\n\x1a\n"
        assert line_chart(days, {"Movies": [0, 0]}, {}, filename="x.png", unit="x") is None

    def test_nothing_to_draw(self):
        from plexbot.ui.charts import bar_chart, stacked_day_chart

        assert bar_chart(["a"], [0], filename="x.png") is None
        assert stacked_day_chart([date(2026, 1, 1)], {"TV": [0]}, {}, filename="x.png") is None


def _build(tautulli):
    from plexbot.bot import PlexBot, install_slash_groups

    bot = PlexBot(command_prefix=commands.when_mentioned, intents=nextcord.Intents.default(), help_command=None,
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


def _open(bot, cog, **options):
    return Visualizations.chart.callback(cog, interaction(bot), **{"user": None, "days": None, **options})


def _pick(select, value):
    select._selected_values = [value]
    return select


def _defaults(select):
    return [option.value for option in select.options if option.default]


class TestCommands:
    def test_opens_on_plays_per_day_for_everyone(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.history = _history(("alice", 2, "episode"), ("mallory", 3, "movie"), ("bob", 24 * 40, "movie"))
        _run(_open, tautulli)
        params = tautulli.history_calls[-1]
        assert "user_id" not in params
        assert params["after"]
        sent = recorder.last("followup")
        embed = sent["embeds"][0]
        assert embed.title == "🎞️ Plays per day"
        assert embed.description == "-# Last 30 days · 1 play\n1 episode"  # no "0.0 plays a day"
        assert embed.image.url == "attachment://chart-media.png"
        assert [f.filename for f in sent["files"]] == ["chart-media.png"]
        kind, viewer, *buttons = sent["view"].children
        windows, measures = buttons[:4], buttons[4:]
        assert [o.label for o in kind.options] == [
            "Over time", "By hour of day", "By day of week", "Per viewer", "Per month", "Top shows", "Top movies",
            "Library growth"]
        assert _defaults(kind) == ["media"]
        assert [o.label for o in viewer.options] == ["Everyone", "Alice"]  # mallory is ignored
        assert _defaults(viewer) == ["*everyone"]
        assert [(b.label, b.style) for b in windows] == [
            ("7 days", nextcord.ButtonStyle.secondary), ("30 days", nextcord.ButtonStyle.primary),
            ("90 days", nextcord.ButtonStyle.secondary), ("1 year", nextcord.ButtonStyle.secondary)]
        assert [(b.label, b.style) for b in measures] == [
            ("Plays", nextcord.ButtonStyle.primary), ("Watch time", nextcord.ButtonStyle.secondary)]

    def test_user_option_starts_on_that_viewer(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.users = [{"user_id": 7, "username": "alice", "friendly_name": "Alice"}]
        tautulli.history = _history(("alice", 2, "episode"), ("bob", 3, "movie"))
        _run(lambda bot, cog: _open(bot, cog, user="alice"), tautulli)
        assert "user_id" not in tautulli.history_calls[-1]  # everyone's, so the viewer dropdown is free
        sent = recorder.last("followup")
        embed = sent["embeds"][0]
        assert embed.author.name == "Alice"
        assert embed.description.startswith("-# Last 30 days · 1 play\n")
        assert _defaults(sent["view"].children[1]) == ["alice"]

    def test_quiet_user_keeps_their_name(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.users = [{"user_id": 7, "username": "alice", "friendly_name": "Alice"}]
        tautulli.history = _history(("bob", 3, "movie"))
        _run(lambda bot, cog: _open(bot, cog, user="alice"), tautulli)
        sent = recorder.last("followup")
        assert "**Alice** hasn't watched anything in the last 30 days." in sent["embeds"][0].description
        assert [o.label for o in sent["view"].children[1].options] == ["Everyone", "Alice", "Bob"]

    def test_unknown_user_is_private(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: _open(bot, cog, user="nobody", days=7), FakeTautulli([]))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True
        assert "No Plex user called **nobody**" in sent["embeds"][0].description

    def test_nothing_watched_keeps_the_controls(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: _open(bot, cog, days=7), FakeTautulli([]))
        sent = recorder.last("followup")
        assert "Nobody has watched anything in the last 7 days." in sent["embeds"][0].description
        assert sent["embeds"][0].title == "🎞️ Plays per day"
        assert sent["view"].children

    def test_tautulli_down(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(_open, FakeTautulli([], reachable=False))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True
        assert "Couldn't reach **Tautulli**" in sent["embeds"][0].description

    def test_unknown_tautulli_timezone_falls_back_to_the_host(self):
        tautulli = FakeTautulli([])
        tautulli.settings = {"default_timezone": "Mars/Olympus_Mons"}

        async def zone(bot, cog):
            return await cog.timezone()

        assert _run(zone, tautulli) is not None

    def test_tautulli_down_doesnt_pin_the_host_timezone(self):
        tautulli = FakeTautulli([], reachable=False)
        tautulli.settings = {"default_timezone": "Australia/Sydney"}

        async def zones(bot, cog):
            await cog.timezone()  # Tautulli is restarting
            tautulli.reachable = True
            return str(await cog.timezone())

        assert _run(zones, tautulli) == "Australia/Sydney"


def test_one_chart_command_with_user_suggestions():
    from plexbot.cogs.utility_commands import collect_help_entries
    from plexbot.slash_commands import command_paths

    async def run(bot, cog):
        return dict(command_paths(bot)), collect_help_entries(bot)

    paths, entries = _run(run, FakeTautulli([]))
    assert [path for path in paths if path.startswith("/plex chart")] == ["/plex chart"]
    assert paths["/plex chart"].options["user"].autocomplete_callback is not None
    charts = [(e.usage, e.example) for e in entries if e.category == "charts"]
    assert charts == [("/plex chart [user] [days]", "/plex chart user:alice days:90")]


class TestChartView:
    def _open(self, monkeypatch):
        tautulli = FakeTautulli([])
        tautulli.settings = {"default_timezone": "Australia/Sydney"}
        tautulli.history = _history(("alice", 2, "episode"), ("bob", 30, "movie"), ("bob", 31, "movie"))
        return Recorder(monkeypatch), tautulli

    def test_dropdown_switches_chart_without_refetching(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await _pick(view.children[0], "hours").callback(interaction(bot))
            return view

        view = _run(run, tautulli)
        assert len(tautulli.history_calls) == 1
        edited = recorder.last("edit_original_message")
        embed = edited["embeds"][0]
        assert embed.title == "🕘 Plays by hour of day"
        assert "times in Australia/Sydney" in embed.description
        assert edited["view"] is view
        assert _defaults(view.children[0]) == ["hours"]

    def test_viewer_dropdown_filters_without_refetching(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            viewers = [(o.label, o.description) for o in view.children[1].options[1:]]
            await _pick(view.children[1], "alice").callback(interaction(bot))
            alice = recorder.last("edit_original_message")["embeds"][0]
            await _pick(view.children[1], "*everyone").callback(interaction(bot))
            return viewers, alice

        viewers, alice = _run(run, tautulli)
        assert viewers == [("Bob", "2 plays"), ("Alice", "1 play")]  # most active first, after Everyone
        assert alice.author.name == "Alice"
        assert alice.description.startswith("-# Last 30 days · 1 play\n")
        everyone = recorder.last("edit_original_message")["embeds"][0]
        assert everyone.author.name is None
        assert "3 plays" in everyone.description
        assert len(tautulli.history_calls) == 1

    def test_plays_per_viewer_is_everyone(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await _pick(view.children[1], "alice").callback(interaction(bot))
            await _pick(view.children[0], "users").callback(interaction(bot))
            return view

        view = _run(run, tautulli)
        embed = recorder.last("edit_original_message")["embeds"][0]
        assert embed.title == "👥 Plays per viewer"
        assert embed.author.name is None
        assert view.viewer is None
        assert not any(isinstance(c, nextcord.ui.StringSelect) for c in view.children[1:])  # no viewer dropdown

    def test_window_buttons_refetch_and_long_windows_go_weekly(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await view.children[2].callback(interaction(bot))  # 7 days
            seven = recorder.last("edit_original_message")["embeds"][0]
            await view.children[5].callback(interaction(bot))  # 1 year
            return seven, view

        seven, view = _run(run, tautulli)
        assert seven.description.startswith("-# Last 7 days")
        year = recorder.last("edit_original_message")["embeds"][0]
        assert year.title == "🎞️ Plays per week"
        assert year.description.startswith("-# Last 365 days")
        assert len(tautulli.history_calls) == 3
        assert view.children[5].style == nextcord.ButtonStyle.primary

    def test_plays_per_month_greys_out_short_windows(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await _pick(view.children[0], "months").callback(interaction(bot))
            return view

        view = _run(run, tautulli)
        assert recorder.last("edit_original_message")["embeds"][0].description.startswith("-# Last 365 days")
        assert [(b.label, b.disabled) for b in view.children[2:6]] == [
            ("7 days", True), ("30 days", True), ("90 days", False), ("1 year", False)]

    def test_top_shows_with_posters_then_watch_time(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)
        for row in tautulli.history:
            row.update(grandparent_title="Andor", grandparent_rating_key=1, grandparent_thumb="/t/andor",
                       play_duration=1800)
        tautulli.history[0].update(grandparent_title="Shogun", grandparent_rating_key=2,
                                   grandparent_thumb="/t/shogun", play_duration=7200)
        for row in tautulli.history:
            row["media_type"] = "episode"
        fetched = []

        async def get_image(self, key, width=300, height=450, fallback=None):
            from fakes import _png

            fetched.append(key)
            return _png()

        monkeypatch.setattr(FakeTautulli, "get_image", get_image, raising=False)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await _pick(view.children[0], "shows").callback(interaction(bot))
            plays = recorder.last("edit_original_message")
            await view.children[-1].callback(interaction(bot))  # Watch time
            return plays, view

        plays, view = _run(run, tautulli)
        embed = plays["embeds"][0]
        assert embed.title == "📺 Top shows by plays"
        assert embed.description.endswith("`1.` **Andor** · 2 plays\n`2.` **Shogun** · 1 play")
        assert [f.filename for f in plays["files"]] == ["chart-shows.png"]
        watch = recorder.last("edit_original_message")["embeds"][0]
        assert watch.title == "📺 Top shows by watch time"
        assert "· 3h watched" in watch.description
        assert watch.description.endswith("`1.` **Shogun** · 2h\n`2.` **Andor** · 1h")
        assert sorted(fetched) == ["/t/andor", "/t/shogun"]  # once each, though drawn twice
        assert len(tautulli.history_calls) == 1
        assert view.children[-1].style == nextcord.ButtonStyle.primary

    def test_top_movies_header_counts_only_movies(self, monkeypatch):
        from fakes import patch_images

        recorder, tautulli = self._open(monkeypatch)
        patch_images(monkeypatch)
        tautulli.history[0].update(media_type="movie", title="Heat", rating_key=9, thumb="/t/heat",
                                   play_duration=3600)
        for row in tautulli.history[1:]:
            row.update(media_type="episode", play_duration=7200)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await _pick(view.children[0], "movies").callback(interaction(bot))
            plays = recorder.last("edit_original_message")["embeds"][0]
            await view.children[-1].callback(interaction(bot))  # Watch time
            return plays

        plays = _run(run, tautulli)
        assert plays.description.startswith("-# Last 30 days · 1 play\n`1.` **Heat** · 1 play")
        watch = recorder.last("edit_original_message")["embeds"][0]
        assert watch.description.startswith("-# Last 30 days · 1 play · 1h watched\n")  # not the 4h of TV

    def test_watch_time_over_time_and_no_movies(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)
        for row in tautulli.history:
            row.update(media_type="episode", play_duration=3600)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await view.children[-1].callback(interaction(bot))  # Watch time
            over_time = recorder.last("edit_original_message")["embeds"][0]
            await _pick(view.children[0], "movies").callback(interaction(bot))
            return over_time

        over_time = _run(run, tautulli)
        assert over_time.title == "🎞️ Watch time per day"
        assert over_time.description.startswith("-# Last 30 days · 3 plays · 3h watched\n3h of TV")
        assert "Nobody has watched any movies in the last 30 days." in \
            recorder.last("edit_original_message")["embeds"][0].description

    def _library(self, tautulli):
        now = int(time.time())
        tautulli.libraries_table = [{"section_id": 1, "section_type": "movie"},
                                    {"section_id": 2, "section_type": "show"},
                                    {"section_id": 3, "section_type": "artist"}]  # music isn't counted
        tautulli.library_items = {
            "1": [{"title": "Heat", "added_at": now - 2 * 86400}, {"title": "Alien", "added_at": now - 400 * 86400}],
            "2": [{"title": "Andor", "added_at": now - 60 * 86400}],
            "3": [{"title": "Abba", "added_at": now - 86400}],
        }
        calls = []
        original = tautulli.get_library_media_info

        async def counted(section_id=None, **kwargs):
            calls.append(str(section_id))
            return await original(section_id=section_id, **kwargs)

        tautulli.get_library_media_info = counted
        return calls

    def test_library_growth_ignores_viewer_and_watch_time(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)
        calls = self._library(tautulli)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await _pick(view.children[0], "library").callback(interaction(bot))
            month = recorder.last("edit_original_message")
            controls = [type(c).__name__ for c in view.children]
            await view.children[3].callback(interaction(bot))  # 90 days
            return month, controls

        month, controls = _run(run, tautulli)
        embed = month["embeds"][0]
        assert embed.title == "📚 Library growth"
        assert embed.description == "-# Last 30 days\n**+1 movie** · now 2 movies and 1 show"
        assert [f.filename for f in month["files"]] == ["chart-library.png"]
        assert controls == ["ChartKindSelect"] + ["ChartWindowButton"] * 4  # no viewer, no plays/watch time
        ninety = recorder.last("edit_original_message")["embeds"][0]
        assert ninety.description.startswith("-# Last 90 days\n**+1 movie** · **+1 show**")
        assert sorted(calls) == ["1", "2"]  # fetched once, though drawn for two windows

    def test_library_unreadable_is_private(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)
        self._library(tautulli)

        async def down(section_id=None, **kwargs):
            return None

        tautulli.get_library_media_info = down

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            await _pick(view.children[0], "library").callback(interaction(bot))
            return view

        view = _run(run, tautulli)
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True
        assert "Couldn't reach **Tautulli**" in sent["embeds"][0].description
        assert view.kind == "media"  # still on the chart the message shows

    def test_tautulli_down_on_a_click_is_private(self, monkeypatch):
        recorder, tautulli = self._open(monkeypatch)

        async def run(bot, cog):
            await _open(bot, cog)
            view = recorder.last("followup")["view"]
            tautulli.history_reachable = False
            await view.children[2].callback(interaction(bot))  # 7 days
            sent = recorder.last("followup")
            tautulli.history_reachable = True
            await _pick(view.children[0], "days").callback(interaction(bot))
            return sent

        sent = _run(run, tautulli)
        assert sent["ephemeral"] is True
        assert "Couldn't reach **Tautulli**" in sent["embeds"][0].description
        # The failed 7-day click didn't stick: the message still showed 30 days.
        assert recorder.last("edit_original_message")["embeds"][0].description.startswith("-# Last 30 days")


def test_stats_activity_page_is_drawn_on_first_click(monkeypatch):
    from plexbot.cogs.plex_stats import PlexStats
    from fakes import build_stats_bot

    recorder = Recorder(monkeypatch)
    tautulli = FakeTautulli([])
    tautulli.history = _history(("alice", 2, "episode"), ("mallory", 3, "movie"))

    async def run():
        bot, stats = build_stats_bot(tautulli)
        bot.add_cog(Visualizations(bot))
        await PlexStats.stats.callback(stats, interaction(bot), days=None)
        view = recorder.last("followup")["view"]
        fetched_before = len(tautulli.history_calls)
        button = view.children[-1]
        await button.callback(interaction(bot))
        await view.children[0].callback(interaction(bot))  # Overview, already built
        await button.callback(interaction(bot))  # Activity again: cached
        return fetched_before, button.label

    fetched_before, label = asyncio.run(run())
    assert label == "Activity"
    assert fetched_before == 0
    assert len(tautulli.history_calls) == 1
    activity = recorder.last("edit_original_message")
    embed = activity["embeds"][0]
    assert embed.title == "📈 Activity"
    assert embed.image.url == "attachment://chart-media.png"
    assert "1 episode" in embed.description
    assert "Busiest hour" in embed.description
    assert "Mallory" not in embed.description
    assert recorder.last("edit_message")["embeds"][0].title == "📈 Activity"


def test_stats_slow_page_doesnt_cover_a_later_click(monkeypatch):
    from plexbot.cogs.plex_stats import StatsView
    from plexbot.ui.embeds import card

    recorder = Recorder(monkeypatch)

    async def run():
        bot, _cog = _build(FakeTautulli([]))
        pages = {"Overview": (card(title="Overview"), None), "Movies": (card(title="Movies"), None)}

        async def slow_activity():
            await view.children[1].callback(interaction(bot))  # Movies, clicked while Activity loads
            return card(title="Activity"), None

        view = StatsView(pages, lazy={"Activity": slow_activity})
        await view.children[2].callback(interaction(bot))
        return view

    view = asyncio.run(run())
    assert recorder.last("edit_message")["embeds"][0].title == "Movies"
    assert "edit_original_message" not in recorder.kinds()
    assert "Activity" in view.pages  # cached for the next click


def test_a_picked_username_wins_over_someone_elses_discord_name():
    from plexbot.plex_users import PlexUsers

    users = [{"username": "sam", "friendly_name": "Sam"}, {"username": "alex", "friendly_name": "Alex"}]
    plex_users = PlexUsers(bot=None, tautulli=None)
    plex_users.name = lambda row: "alex" if row["username"] == "sam" else row["friendly_name"]  # Sam is "alex" on Discord
    assert plex_users.match(users, "alex")["username"] == "alex"  # what picking Alex's suggestion sends
    assert plex_users.match(users, "SAM")["username"] == "sam"  # typed names still match in any case
