"""/plex upcoming: next episodes from TMDB for shows on the server."""

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from fakes import FakeTautulli, FakeTMDB, Recorder, build_upcoming_bot, interaction
from plexbot.cogs.upcoming import Upcoming, upcoming_card, upcoming_rows
from plexbot.ui.theme import Limits

TODAY = date(2026, 10, 22)


@pytest.fixture(autouse=True)
def no_rate_wait(monkeypatch):
    monkeypatch.setattr("plexbot.cogs.upcoming._MIN_REQUEST_SECONDS", 0)


def _show(key, title, tmdb_id):
    return {"rating_key": key, "media_type": "show", "title": title, "tmdb_id": tmdb_id}


def _entry(air_date, season=1, episode=1, name=None, status="Returning Series"):
    next_up = None if air_date is False else {"air_date": air_date, "season_number": season,
                                              "episode_number": episode, "name": name}
    return {"status": status, "next": next_up, "fetched": time.monotonic()}


def test_upcoming_rows_window_and_order():
    shows = [_show("1", "b", 1), _show("2", "A", 2), _show("3", "C", 3), _show("4", "D", 4), _show("5", "E", 5)]
    entries = {1: _entry("2026-10-22"), 2: _entry("2026-10-22"), 3: _entry("2026-10-28"),
               4: _entry("2026-10-29"), 5: _entry("2026-10-21")}
    rows = upcoming_rows(shows, entries, TODAY)
    assert [(day.isoformat(), show["title"]) for day, show, _ in rows] == [
        ("2026-10-22", "A"), ("2026-10-22", "b"), ("2026-10-28", "C")]


def test_upcoming_rows_skip_bad_dates():
    shows = [_show("1", "No next", 1), _show("2", "No date", 2), _show("3", "TBA", 3), _show("4", "Unasked", 4)]
    entries = {1: _entry(False), 2: _entry(None), 3: _entry("TBA")}
    assert upcoming_rows(shows, entries, TODAY) == []


def test_upcoming_card_groups_by_day():
    rows = [
        (TODAY, _show("200", "Severance", 1), {"season_number": 2, "episode_number": 5, "name": "Trojan's Horse"}),
        (TODAY + timedelta(days=1), _show("201", "**Bold**", 2), {"season_number": 1, "episode_number": 1}),
    ]
    embed = upcoming_card(rows, TODAY, "srv1").embeds[0]
    text = embed.description
    assert embed.title == "📅 Airing this week"
    assert text.index("**Today**") < text.index("**Tomorrow**")
    assert "[Severance](https://app.plex.tv/desktop/#!/server/srv1/details" in text
    assert "S02E05 · Trojan's Horse" in text
    assert "\\*\\*Bold\\*\\*" in text


def test_upcoming_card_truncates():
    rows = [(TODAY + timedelta(days=i % 7), _show(str(i), f"A rather long show title number {i}", i),
             {"season_number": 1, "episode_number": i, "name": "An episode with a fairly long name"})
            for i in range(400)]
    rows.sort(key=lambda row: row[0])
    text = upcoming_card(rows, TODAY, "srv1").embeds[0].description
    assert len(text) <= Limits.DESCRIPTION
    assert text.splitlines()[-1].startswith("-# and ") and text.endswith(" more")


def _cog(tmdb):
    async def run():
        bot, cog = build_upcoming_bot(FakeTautulli([]), tmdb)
        return cog

    return asyncio.run(run())


def test_refresh_skips_finished_and_fresh():
    tmdb = FakeTMDB(shows={1: {"status": "Returning Series", "next_episode_to_air": {"air_date": "2026-10-23"}},
                           2: {"status": "Ended", "next_episode_to_air": None}})
    cog = _cog(tmdb)
    shows = [_show("1", "Running", 1), _show("2", "Over", 2)]

    async def run():
        await cog.refresh(shows)
        first = len(tmdb.calls)
        await cog.refresh(shows)
        second = len(tmdb.calls) - first
        for entry in cog.entries.values():
            entry["fetched"] -= 13 * 3600
        del tmdb.shows[1]  # TMDB doesn't answer about show 1 this time
        await cog.refresh(shows)
        return first, second

    first, second = asyncio.run(run())
    assert (first, second) == (2, 0)
    assert tmdb.calls[-1] == ("show", 1) and len(tmdb.calls) == 3
    assert cog.entries[1]["next"] == {"air_date": "2026-10-23"}  # kept from the last answer


def _command(tmdb, items):
    async def run():
        bot, cog = build_upcoming_bot(FakeTautulli([]), tmdb, items)
        await Upcoming.upcoming.callback(cog, interaction(bot))

    asyncio.run(run())


def test_command_without_tmdb(monkeypatch):
    recorder = Recorder(monkeypatch)
    _command(None, [_show("1", "Severance", 1)])
    sent = recorder.last("send_message")
    assert sent["ephemeral"] is True
    assert sent["embeds"][0].description == (
        "TMDB isn't set up.\n-# An admin can add a TMDB API key under tmdb in config.json or .env.")


def test_command_tmdb_down_first_time(monkeypatch):
    recorder = Recorder(monkeypatch)
    _command(FakeTMDB(reachable=False), [_show("1", "Severance", 1)])
    sent = recorder.last("followup")
    assert sent["ephemeral"] is True
    assert "Couldn't reach **TMDB**." in sent["embeds"][0].description


def test_command_nothing_airing(monkeypatch):
    recorder = Recorder(monkeypatch)
    _command(FakeTMDB(shows={1: {"status": "Ended"}}), [_show("1", "Severance", 1)])
    sent = recorder.last("followup")
    assert sent["ephemeral"] is False
    assert sent["embeds"][0].description.startswith("Nothing's airing in the next week.")


def test_command_lists_this_weeks_episodes(monkeypatch):
    recorder = Recorder(monkeypatch)
    today = datetime.now(timezone.utc).date().isoformat()  # FakeTautulli's server is on UTC
    tmdb = FakeTMDB(shows={1: {"status": "Returning Series", "next_episode_to_air": {
        "air_date": today, "season_number": 2, "episode_number": 5, "name": "Hello"}}})
    _command(tmdb, [_show("1", "Severance", 1), _show("2", "Not on TMDB", None)])
    sent = recorder.last("followup")
    assert sent["ephemeral"] is False
    assert "**Today**" in sent["embeds"][0].description
    assert ("show", 1) in tmdb.calls and len(tmdb.calls) == 1


def test_registered_in_help():
    from plexbot.cogs.utility_commands import collect_help_entries

    async def run():
        bot, cog = build_upcoming_bot(FakeTautulli([]), None)
        return {entry.usage: entry.category for entry in collect_help_entries(bot)}

    assert asyncio.run(run())["/plex upcoming"] == "library"


def test_a_show_in_two_libraries_is_listed_once():
    shows = [_show("1", "Severance", 7), _show("2", "Severance", 7)]  # an HD and a 4K library
    assert len(upcoming_rows(shows, {7: _entry("2026-10-22")}, TODAY)) == 1


def test_known_episodes_answer_at_once_and_refresh_in_the_background(monkeypatch):
    recorder = Recorder(monkeypatch)
    today = datetime.now(timezone.utc).date()
    tmdb = FakeTMDB(shows={1: {"status": "Returning Series", "next_episode_to_air": {
        "air_date": (today + timedelta(days=1)).isoformat(), "season_number": 1, "episode_number": 2}}})

    async def run():
        bot, cog = build_upcoming_bot(FakeTautulli([]), tmdb, [_show("1", "Severance", 1)])
        cog.entries = {1: dict(_entry(today.isoformat()), fetched=time.monotonic() - 13 * 3600)}
        await Upcoming.upcoming.callback(cog, interaction(bot))
        await cog._refreshing
        return cog.entries[1]["next"]["episode_number"]

    assert asyncio.run(run()) == 2  # updated for next time
    assert "S01E01" in recorder.last("followup")["embeds"][0].description  # answered with what it knew


def test_warm_up_waits_for_the_media_cache_then_asks_tmdb():
    tmdb = FakeTMDB(shows={1: {"status": "Ended"}})

    async def run():
        bot, cog = build_upcoming_bot(FakeTautulli([]), tmdb, [_show("1", "Severance", 1)])
        filled = asyncio.Event()
        cog.media_cache._refresh = asyncio.create_task(filled.wait())  # the startup fill, still running
        cog.start_warm_up()
        await asyncio.sleep(0.01)
        asked_early = list(tmdb.calls)
        filled.set()
        await cog._refreshing
        return asked_early, list(tmdb.calls)

    assert asyncio.run(run()) == ([], [("show", 1)])


def test_unloading_stops_a_refresh():
    class SlowTMDB(FakeTMDB):
        async def show(self, tmdb_id):
            await asyncio.sleep(60)

    async def run():
        bot, cog = build_upcoming_bot(FakeTautulli([]), SlowTMDB(), [_show("1", "Severance", 1)])
        cog.start_warm_up()
        await asyncio.sleep(0.01)
        task = cog._refreshing
        cog.cog_unload()
        await asyncio.wait({task})
        return task

    assert asyncio.run(run()).cancelled()


def test_lookups_stay_under_tmdbs_rate_limit(monkeypatch):
    import plexbot.cogs.upcoming as upcoming

    monkeypatch.setattr(upcoming, "_MIN_REQUEST_SECONDS", 0.05)
    tmdb = FakeTMDB(shows={i: {"status": "Ended"} for i in range(32)})  # answers at once, like a fast host
    cog = _cog(tmdb)

    async def run():
        started = time.monotonic()
        await cog.refresh([_show(str(i), f"Show {i}", i) for i in range(32)])
        return time.monotonic() - started

    assert asyncio.run(run()) >= 0.1  # 32 lookups, 16 at a time, each holding its slot 0.05 s
