"""/plex top, stats, shows, history and hot: renderers and commands against fakes."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from nextcord import ButtonStyle
from nextcord.utils import escape_markdown

import utilities
from cogs.plex_stats import (
    _ALL_TIME, _STATS, PlexStats, hot_summary, library_line, media_card, overview_card, peak_line, popular_lines,
    tautulli_duration, top_card, tv_watchers, users_card,
)
from fakes import FakeTautulli, Recorder, build_stats_bot, interaction, patch_images

NOW = int(time.time())


def name(row):
    """Like viewer_mention for someone not linked to Discord: their Plex name, escaped."""
    return escape_markdown(row.get("friendly_name") or row.get("user") or row.get("username"))


ALICE_ON_DISCORD = SimpleNamespace(mention="<@42>", display_name="Ali",
                                   display_avatar=SimpleNamespace(url="https://cdn.example/ali.png"))


@pytest.fixture(autouse=True)
def mappings(monkeypatch):
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings",
                        staticmethod(lambda: [{"plex_username": "mallory", "ignore": True}]))
    patch_images(monkeypatch)


TOP_USERS = [
    {"user": "mallory", "friendly_name": "Mallory", "total_duration": 99_000, "total_plays": 50},
    {"user": "alice", "friendly_name": "Alice", "total_duration": 45_000, "total_plays": 12,
     "media_type": "episode", "grandparent_title": "Severance", "user_thumb": "https://plex.tv/a.png"},
    {"user": "bob", "friendly_name": "_Bob_", "total_duration": 3_600, "total_plays": 2,
     "media_type": "movie", "grandchild_title": "Heat"},
    {"user": "cara", "friendly_name": "Cara", "total_duration": 60, "total_plays": 1},
    {"user": "dan", "friendly_name": "Dan", "total_duration": 30, "total_plays": 1},
]


class TestRenderers:
    def test_top_card_ranks_with_medals(self):
        embed = top_card(TOP_USERS[1:], days=7, name=name)
        lines = embed.description.split("\n")
        assert lines[0] == "-# Last 7 days · by watch time"
        assert lines[1] == ""
        assert lines[2] == "🥇 **Alice** · 12h 30m"
        assert lines[3] == "-# Last watched Severance"
        assert lines[4] == "🥈 **\\_Bob\\_** · 1h"
        assert lines[6] == "🥉 **Cara** · 1m"
        assert lines[7:] == ["", "`4.` **Dan** · 0m"]
        assert embed.thumbnail.url is None
        assert embed.footer.text == "Together 13h 31m"
        with_total = top_card(TOP_USERS[1:], days=7, name=name, all_time="41 days 6 hrs 2 mins")
        assert with_total.footer.text == "Together 13h 31m · All time 41d 6h 2m"

    def test_only_the_podium_shows_what_they_last_watched(self):
        rows = [dict(TOP_USERS[1], friendly_name=f"U{i}") for i in range(5)]
        lines = top_card(rows, days=7, name=name).description.split("\n")
        assert lines.count("-# Last watched Severance") == 3
        assert lines[-1] == "`5.` **U4** · 12h 30m"

    def test_tautulli_duration(self):
        assert tautulli_duration("459 days 13 hrs 30 mins") == "459d 13h 30m"
        assert tautulli_duration("1 day 1 hr 1 min") == "1d 1h 1m"
        assert tautulli_duration("3 mins") == "3m"

    def test_tautulli_duration_never_half_reads(self):
        # Shown as written rather than silently dropping the parts it doesn't know.
        for text in ("2 days 5 hours 3 mins", "about 2 days", "2 days 5", "", "something else"):
            assert tautulli_duration(text) == text

    def test_stats_pages_when_nothing_was_watched(self):
        stats = {stat_id: [] for stat_id in _STATS}
        embed = overview_card(stats, [], days=7, strip=None)
        assert embed.description == "-# Last 7 days"
        assert [(f.name, f.value) for f in embed.fields] == [("Top titles", "Nothing was watched in the last 7 days.")]
        assert embed.footer.text is None  # no library totals, so no footer
        assert embed.image.url is None
        tv = media_card("show", stats, [], days=7, strip=None)
        assert [(f.name, f.value) for f in tv.fields] == [("🔥 Most popular", "Nothing was watched in the last 7 days.")]

    def test_media_card_splits_libraries_and_shows_watch_time_for_tv(self):
        stats = {stat_id: [] for stat_id in _STATS}
        stats["top_libraries"] = [
            {"section_type": "show", "section_name": "TV", "total_plays": 30, "total_duration": 36_000},
            {"section_type": "show", "section_name": "Anime", "total_plays": 10, "total_duration": 7_200},
            {"section_type": "movie", "section_name": "Movies", "total_plays": 4, "total_duration": 28_800},
        ]
        stats["top_tv"] = [{"title": "Severance", "media_type": "show", "total_duration": 90_000}]
        libraries = [{"section_type": "show", "count": "5", "child_count": "80", "duration": "3600"},
                     {"section_type": "movie", "count": "9"}]
        tv = media_card("show", stats, libraries, days=30, strip=None)
        assert tv.description == "-# Last 30 days\n**40 plays** · **12h** watched"
        assert [f.name for f in tv.fields] == ["🔥 Most popular", "⏱️ Most watch time", "📚 By library"]
        assert tv.fields[1].value == "`1.` **Severance** · 1d 1h"
        assert tv.fields[2].value == "**TV** · 30 plays · 10h\n**Anime** · 10 plays · 2h"
        assert tv.footer.text == "5 shows · 80 episodes · 1h watched all time"
        movies = media_card("movie", stats, libraries, days=30, strip=None)
        assert [f.name for f in movies.fields] == ["🔥 Most popular"]  # one library, and no watch-time list
        assert movies.footer.text == "9 movies"

    def test_users_card_is_all_time_with_plays(self):
        rows = [dict(TOP_USERS[1], last_play=1_700_000_000), *TOP_USERS[2:]]
        embed = users_card(rows, name=name, avatar="https://plex.tv/a.png")
        lines = embed.description.split("\n")
        assert lines[:4] == ["-# All time · by watch time", "", "🥇 **Alice** · 12h 30m · 12 plays",
                             "-# Last watched Severance · <t:1700000000:R>"]
        assert lines[4:6] == ["🥈 **\\_Bob\\_** · 1h · 2 plays", "-# Last watched Heat"]
        assert lines[-2:] == ["", "`4.` **Dan** · 0m · 1 play"]
        assert embed.thumbnail.url == "https://plex.tv/a.png"
        assert embed.footer.text == "4 viewers · 13h 31m together"
        empty = users_card([], name=name, avatar=None)
        assert empty.description.endswith("Nobody's watched anything yet.")
        assert empty.footer.text is None

    def test_overview_platforms_and_peak(self):
        stats = {stat_id: [] for stat_id in _STATS}
        stats["top_platforms"] = [{"platform": "Roku", "total_plays": 12}, {"platform": "Chrome", "total_plays": 1}]
        stats["most_concurrent"] = [{"title": "Concurrent Transcodes", "count": 2},
                                    {"title": "Concurrent Streams", "count": 6, "started": 1_700_000_000}]
        fields = {f.name: f.value for f in overview_card(stats, [], days=30, strip=None).fields}
        assert fields["📱 Platforms"] == "**Roku** · 12 plays\n**Chrome** · 1 play"
        assert fields["📈 Busiest"] == "6 streams at once · <t:1700000000:d>"
        assert peak_line([{"title": "Concurrent Streams", "count": 0}]) is None

    def test_library_line_and_popular(self):
        rows = [{"section_type": "movie", "count": "1866", "duration": "360000"},
                {"section_type": "show", "count": "608", "child_count": "27431", "duration": "7200"}]
        assert library_line(rows) == "1,866 movies · 608 shows · 27,431 episodes · 4d 6h watched all time"
        assert library_line([]) is None
        popular = popular_lines([{"title": "The Matrix", "year": "1999", "media_type": "movie",
                                  "total_plays": 12, "users_watched": 1}])
        assert popular == "`1.` **The Matrix (1999)** · 12 plays · 1 person"

    def test_tv_watchers_sum_libraries_and_skip_ignored(self):
        libraries = [{"section_name": "TV"}, {"section_name": "Anime"}]
        ok = lambda rows: {"response": {"result": "success", "data": rows}}  # noqa: E731
        responses = [
            ok([{"username": "alice", "total_time": 100}, {"username": "mallory", "total_time": 999}]),
            ok([{"username": "alice", "total_time": 50}, {"username": "bob", "total_time": 120},
                {"username": "zed", "total_time": 0}]),
        ]
        ranked = tv_watchers(libraries, responses, {"mallory"})
        assert [(row["username"], seconds, libs) for row, seconds, libs in ranked] == [
            ("alice", 150, ["TV", "Anime"]), ("bob", 120, ["Anime"]),
        ]


def _play(user, title, *, show=None, key, media_type="episode", date=NOW):
    row = {"user": user, "friendly_name": user.title(), "title": title, "rating_key": key, "date": date,
           "media_type": media_type}
    if show:
        row.update(grandparent_title=show, grandparent_rating_key=f"show-{show}")
    return row


class TestHot:
    def test_sections(self):
        history = (
            [_play("alice", f"Ep {i}", show="Severance", key=f"s{i}") for i in range(6)]
            + [_play("bob", "Ep 1", show="Severance", key="s1")]
            + [_play("bob", "Heat", key="m1", media_type="movie")]
        )
        recent = [{"rating_key": "m1", "title": "Heat", "year": "1995", "media_type": "movie"}]
        summary = hot_summary(history, recent, days=7, name=name)
        assert summary["totals"] == "**8 plays** · 2 people · 2 titles · 1.1 a day"
        sections = dict(summary["sections"])
        assert sections["🔥 Trending"] == ["**Severance** · 2 people · 7 plays"]
        assert sections["🆕 New and watched"] == ["🎬 **Heat (1995)** · 1 play"]
        assert sections["🏃 Most active"][0] == "🥇 **Alice** · 6 plays · 6 titles"
        assert sections["🍿 Binge watchers"] == ["**Alice** binged **Severance** (6 episodes)"]
        assert sections["🧭 First to check out"] == ["**Bob** tried **Heat**"]
        assert summary["leader"] == "TV leads: 7 episodes to 1 movie"

    def test_quiet_week_has_no_empty_sections(self):
        summary = hot_summary([_play("alice", "Heat", key="m1", media_type="movie")], [], days=7, name=name)
        assert [heading for heading, _ in summary["sections"]] == ["🏃 Most active"]
        assert summary["leader"] is None


def _run(fn, tautulli):
    async def run():
        bot, cog = build_stats_bot(tautulli)
        return await fn(bot, cog)

    return asyncio.run(run())


class TestCommands:
    def test_top_skips_ignored_and_shows_avatar(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.home_stats = {"top_users": TOP_USERS}
        _run(lambda bot, cog: PlexStats.top.callback(cog, interaction(bot), days=30), tautulli)
        assert tautulli.home_stats_calls[0]["stats_type"] == "duration"
        assert tautulli.home_stats_calls[0]["time_range"] == 30
        embed = recorder.last("followup")["embeds"][0]
        assert "Mallory" not in embed.description
        assert embed.thumbnail.url == "https://plex.tv/a.png"

    def test_top_mentions_linked_viewers(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        monkeypatch.setattr(utilities, "mapped_discord_user",
                            lambda bot, username: ALICE_ON_DISCORD if username == "alice" else None)
        tautulli = FakeTautulli([])
        tautulli.home_stats = {"top_users": TOP_USERS}
        _run(lambda bot, cog: PlexStats.top.callback(cog, interaction(bot), days=30), tautulli)
        embed = recorder.last("followup")["embeds"][0]
        lines = embed.description.split("\n")
        assert lines[2] == "🥇 **<@42>** · 12h 30m"  # linked: their Discord @mention
        assert lines[4] == "🥈 **\\_Bob\\_** · 1h"  # not linked: their Plex name, escaped
        assert embed.thumbnail.url == "https://cdn.example/ali.png"

    def test_top_roles_only_for_the_standard_ranking(self, monkeypatch):
        Recorder(monkeypatch)
        assigned = []

        async def fake_assign(self, guild, rows):
            assigned.append([r["user"] for r in rows])

        monkeypatch.setattr(PlexStats, "_assign_top_roles", fake_assign)
        tautulli = FakeTautulli([])
        tautulli.home_stats = {"top_users": TOP_USERS}

        async def run(bot, cog):
            guild = object()
            ix = interaction(bot)
            monkeypatch.setattr(type(ix), "guild", property(lambda self: guild))
            await PlexStats.top.callback(cog, ix, days=None)
            await PlexStats.top.callback(cog, ix, days=1)

        _run(run, tautulli)
        assert assigned == [["alice", "bob", "cara"]]

    def test_top_tautulli_down(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: PlexStats.top.callback(cog, interaction(bot), days=None),
             FakeTautulli([], reachable=False))
        assert recorder.last("followup")["ephemeral"] is True

    def test_stats_opens_on_the_overview_and_flips_pages(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.home_stats = {
            "popular_movies": [{"title": "Heat", "year": "1995", "media_type": "movie", "total_plays": 3,
                                "users_watched": 2, "thumb": "/t/1"}],
            "popular_tv": [],
            "top_users": TOP_USERS,
        }
        tautulli.libraries_table = [{"section_type": "movie", "count": "10"}]

        async def run(bot, cog):
            await PlexStats.stats.callback(cog, interaction(bot), days=None)
            view = recorder.last("followup")["view"]
            opened = [(b.label, b.style) for b in view.children]
            await view.children[1].callback(interaction(bot))  # Movies
            on_movies = [b.style for b in view.children]
            await view.children[3].callback(interaction(bot))  # Users
            return opened, on_movies

        opened, on_movies = _run(run, tautulli)
        assert {call["stat_id"] for call in tautulli.home_stats_calls} == set(_STATS)
        windows = {call["stat_id"]: call["time_range"] for call in tautulli.home_stats_calls}
        assert windows.pop("top_users") == _ALL_TIME
        assert set(windows.values()) == {30}
        users = recorder.last("edit_message")
        assert users["embeds"][0].title == "👥 User stats"
        assert "Mallory" not in users["embeds"][0].description  # ignored
        assert users["embeds"][0].footer.text == "4 viewers · 13h 31m together"
        assert "files" not in users  # no poster strip, and the Movies page's is dropped
        assert users["attachments"] == []
        sent = recorder.last("followup")
        assert [f.name for f in sent["embeds"][0].fields] == ["🎬 Top movies"]
        assert sent["embeds"][0].footer.text == "10 movies"
        assert [f.filename for f in sent["files"]] == ["posters.png"]
        assert opened == [
            ("Overview", ButtonStyle.primary), ("Movies", ButtonStyle.secondary), ("TV", ButtonStyle.secondary),
            ("Users", ButtonStyle.secondary)]
        flipped = [kwargs for kind, kwargs in recorder.calls if kind == "edit_message"][0]
        assert flipped["embeds"][0].title == "🎬 Movie stats"
        assert flipped["embeds"][0].fields[0].value == "`1.` **Heat (1995)** · 3 plays · 2 people"
        assert [f.filename for f in flipped["files"]] == ["posters.png"]
        assert on_movies[:2] == [ButtonStyle.secondary, ButtonStyle.primary]

    def test_stats_tautulli_down(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: PlexStats.stats.callback(cog, interaction(bot), days=7),
             FakeTautulli([], reachable=False))
        assert recorder.last("followup")["ephemeral"] is True

    def test_shows(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.libraries = [{"section_id": "2", "section_type": "show", "section_name": "TV"},
                              {"section_id": "1", "section_type": "movie"}]
        tautulli.library_user_stats = {"2": [{"username": "alice", "friendly_name": "Alice", "total_time": 7200}]}
        _run(lambda bot, cog: PlexStats.shows.callback(cog, interaction(bot)), tautulli)
        assert recorder.last("followup")["embeds"][0].description == (
            "-# All time, by TV watch time\n🥇 **Alice** · 2h")

    def test_history_for_a_user_filters_in_tautulli(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.users = [{"user_id": 7, "username": "alice", "friendly_name": "Alice"},
                          {"user_id": 9, "username": "mallory", "friendly_name": "Mallory"}]
        tautulli.history = [_play("alice", "Heat", key="m1", media_type="movie")]
        _run(lambda bot, cog: PlexStats.history.callback(cog, interaction(bot), user="ALICE"), tautulli)
        assert tautulli.history_calls[-1]["user_id"] == 7
        embed = recorder.last("followup")["embeds"][0]
        assert embed.author.name == "Alice"
        assert embed.description.startswith("🎬 **Heat** · <t:")  # leads with the title, not the user

    def test_history_unknown_or_ignored_user_is_private(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.users = [{"user_id": 9, "username": "mallory", "friendly_name": "Mallory"}]
        _run(lambda bot, cog: PlexStats.history.callback(cog, interaction(bot), user="mallory"), tautulli)
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True and "No Plex user called **mallory**" in sent["embeds"][0].description

    def test_history_autocomplete(self, monkeypatch):
        tautulli = FakeTautulli([])
        tautulli.users = [{"user_id": 7, "username": "alice", "friendly_name": "Alice"},
                          {"user_id": 8, "username": "bob", "friendly_name": "Bobby"},
                          {"user_id": 9, "username": "mallory", "friendly_name": "Mallory"}]
        sent = []

        async def run(bot, cog):
            ix = interaction(bot)

            async def send_autocomplete(self, choices):
                sent.append(choices)

            monkeypatch.setattr(type(ix.response), "send_autocomplete", send_autocomplete)
            await PlexStats._history_user_autocomplete(cog, ix, "b")
            await PlexStats._history_user_autocomplete(cog, ix, "")

        _run(run, tautulli)
        assert sent[0] == {"Bobby": "bob"}
        assert sent[1] == {"Alice": "alice", "Bobby": "bob"}  # ignored users are never suggested

    def test_hot_empty_week(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = FakeTautulli([])
        tautulli.history = [_play("alice", "Old", key="x", date=NOW - 30 * 86400)]
        _run(lambda bot, cog: PlexStats.hot.callback(cog, interaction(bot), days=None), tautulli)
        assert tautulli.history_calls[-1]["after"]
        assert "Nobody's watched anything in the last 7 days." in recorder.last("followup")["embeds"][0].description


def test_stats_commands_registered_in_help():
    from cogs.utility_commands import collect_help_entries

    async def run(bot, cog):
        return collect_help_entries(bot)

    entries = _run(run, FakeTautulli([]))
    stats = {e.usage for e in entries if e.category == "stats"}
    assert stats == {
        "/plex top [days]", "/plex stats [days]", "/plex shows", "/plex history [user]", "/plex hot [days]",
    }
    assert not any(e.usage.startswith("plex ") for e in entries)  # no prefix stats commands left
