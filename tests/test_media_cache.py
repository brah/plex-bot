"""MediaCache: background refresh of a stale cache, keeping titles a failed fetch missed, and the disk file."""

import asyncio
import json
from datetime import datetime, timedelta

from fakes import FakeTautulli
from plexbot.media_cache import MediaCache


class LibraryTautulli(FakeTautulli):
    """One movie library; get_metadata fails (no answer) for the rating keys in `failing`."""

    def __init__(self, titles: dict):
        super().__init__()
        self.libraries = [{"section_id": 1, "section_name": "Movies", "section_type": "movie"}]
        self.library_items = {"1": [{"rating_key": key} for key in titles]}
        self.metadata = {key: {"title": title, "media_type": "movie", "genres": ["Drama"]}
                         for key, title in titles.items()}
        self.failing = set()
        self.asked = []  # rating keys get_metadata was called for

    async def get_metadata(self, rating_key):
        self.asked.append(str(rating_key))
        if str(rating_key) in self.failing:
            return None
        return await super().get_metadata(rating_key)


def _item(key, title):
    return {"rating_key": key, "title": title, "media_type": "movie", "genres": ["drama"]}


def _stale_cache(tautulli, tmp_path, items):
    cache = MediaCache(tautulli, cache_file_path=str(tmp_path / "cache.json"), update_interval=3600)
    cache.media_items = {item["rating_key"]: item for item in items}
    cache.last_updated = datetime.now() - timedelta(hours=2)
    return cache


def _titles(items):
    return sorted(item["title"] for item in items)


def test_stale_cache_answers_at_once_and_refreshes_in_the_background(tmp_path):
    tautulli = LibraryTautulli({"1": "Renamed in Plex", "2": "Added"})
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Old title")])

    async def run():
        served = await cache.get_items()
        refresh = cache._refresh
        assert refresh is not None
        assert not refresh.done()  # the caller didn't wait for it
        await refresh
        return served

    served = asyncio.run(run())
    assert _titles(served) == ["Old title"]
    # Only the new title is looked up; a cached one keeps its metadata until Refresh now.
    assert tautulli.asked == ["2"]
    assert _titles(cache.media_items.values()) == ["Added", "Old title"]
    assert cache.is_cache_valid()
    saved = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))["items"]
    assert _titles(saved) == ["Added", "Old title"]


def test_refresh_now_reads_every_title_again_and_drops_removed_ones(tmp_path):
    tautulli = LibraryTautulli({"1": "Renamed in Plex"})
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Old title"), _item("2", "Deleted from Plex")])
    asyncio.run(cache.refresh())
    assert tautulli.asked == ["1"]
    assert _titles(cache.media_items.values()) == ["Renamed in Plex"]


def test_refresh_now_drops_a_title_tautulli_refuses_but_keeps_one_it_got_no_answer_for(tmp_path):
    tautulli = LibraryTautulli({"1": "Kept"})
    tautulli.library_items = {"1": [{"rating_key": key} for key in ("1", "2", "3")]}  # 2 is gone from Plex
    tautulli.failing = {"3"}
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Kept"), _item("2", "Removed"), _item("3", "No answer")])
    assert asyncio.run(cache.refresh())
    assert _titles(cache.media_items.values()) == ["Kept", "No answer"]
    assert "2" not in cache._unreadable  # so the next update asks again, in case Plex was only restarting


def test_a_failed_update_says_so(tmp_path):
    tautulli = LibraryTautulli({"1": "Kept"})
    tautulli.reachable = False
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Kept")])
    assert not asyncio.run(cache.refresh())
    assert _titles(cache.media_items.values()) == ["Kept"]


def test_empty_cache_waits_for_the_first_fetch(tmp_path):
    cache = _stale_cache(LibraryTautulli({"1": "Only title"}), tmp_path, [])
    assert _titles(asyncio.run(cache.get_items())) == ["Only title"]


def test_a_failed_fetch_keeps_the_title_we_had(tmp_path):
    tautulli = LibraryTautulli({"1": "Refreshed", "2": "Unreachable now"})
    tautulli.failing = {"2"}
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Old one"), _item("2", "Old two")])
    asyncio.run(cache.refresh())
    assert _titles(cache.media_items.values()) == ["Old two", "Refreshed"]


def test_an_unreadable_library_keeps_its_titles_and_the_others_refresh(tmp_path):
    tautulli = LibraryTautulli({"1": "Refreshed movie"})
    tautulli.libraries.append({"section_id": 2, "section_name": "TV", "section_type": "show"})

    async def media_info(section_id=None, **kwargs):
        if str(section_id) == "2":
            return None  # e.g. Tautulli timed out on the TV library
        return await LibraryTautulli.get_library_media_info(tautulli, section_id=section_id, **kwargs)

    tautulli.get_library_media_info = media_info
    show = {**_item("9", "Cached show"), "media_type": "show", "section_id": "2"}
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Old movie"), show])
    asyncio.run(cache.refresh())
    assert _titles(cache.media_items.values()) == ["Cached show", "Refreshed movie"]


def test_a_library_bigger_than_one_page_is_read_whole():
    tautulli = LibraryTautulli({str(key): f"Movie {key}" for key in range(1, 6)})
    rows = asyncio.run(tautulli.get_library_media_rows(1, page=2))
    assert [row["rating_key"] for row in rows] == ["1", "2", "3", "4", "5"]


def test_an_empty_cache_fills_in_the_background_at_startup(tmp_path):
    cache = MediaCache(LibraryTautulli({"1": "First"}), cache_file_path=str(tmp_path / "cache.json"))

    async def run():
        await cache.initialize()
        assert cache._refresh is not None
        assert not cache._refresh.done()  # startup didn't wait
        await cache._refresh

    asyncio.run(run())
    assert _titles(cache.media_items.values()) == ["First"]


def test_disk_round_trip_keeps_items_and_freshness(tmp_path):
    cache = _stale_cache(LibraryTautulli({}), tmp_path, [_item("1", "Kept")])
    cache.last_updated = datetime(2026, 10, 1, 12, 0)
    asyncio.run(cache._write_items_to_disk(list(cache.media_items.values())))

    loaded = MediaCache(LibraryTautulli({}), cache_file_path=str(tmp_path / "cache.json"))
    asyncio.run(loaded.load_cache_from_disk())
    assert loaded.media_items == cache.media_items
    assert loaded.last_updated == datetime(2026, 10, 1, 12, 0)


def test_unreadable_file_starts_empty_and_stale_so_it_rebuilds(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps([_item("7", "Old bare-list format")]), encoding="utf-8")
    cache = MediaCache(LibraryTautulli({}), cache_file_path=str(path))
    asyncio.run(cache.load_cache_from_disk())
    assert cache.media_items == {}
    assert cache.last_updated is None


def test_close_stops_a_background_refresh(tmp_path):
    cache = _stale_cache(LibraryTautulli({"1": "New"}), tmp_path, [_item("1", "Old")])

    async def run():
        await cache.ensure_cache_valid()
        await cache.close()
        return cache._refresh

    assert asyncio.run(run()).cancelled()


def test_typing_a_title_starts_a_refresh_of_a_stale_cache(tmp_path):
    tautulli = LibraryTautulli({"1": "Old title", "2": "Added today"})
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Old title")])

    async def run():
        first = cache.suggest("", "movie")  # answers at once from what's cached
        await cache._refresh
        return first, cache.suggest("", "movie")

    before, after = asyncio.run(run())
    assert _titles(before) == ["Old title"]
    assert _titles(after) == ["Added today", "Old title"]
    assert tautulli.asked == ["2"]


def test_refresh_now_rereads_every_title_even_during_a_background_refresh(tmp_path):
    tautulli = LibraryTautulli({"1": "Renamed in Plex", "2": "Added"})
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Old title")])

    async def run():
        await cache.get_items()  # /plex random on a stale cache starts a background refresh
        await asyncio.sleep(0)  # which is under way
        await cache.refresh()  # when an admin presses Refresh now

    asyncio.run(run())
    assert _titles(cache.media_items.values()) == ["Added", "Renamed in Plex"]


def test_a_title_tautulli_cant_describe_is_asked_again_daily_or_on_refresh_now(tmp_path):
    tautulli = LibraryTautulli({"1": "Kept", "2": "Removed from Plex"})
    tautulli.failing = {"2"}
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Kept")])

    def stale_refresh():
        cache.last_updated = None
        return cache.update_cache()

    async def run():
        await stale_refresh()  # asks about it
        await stale_refresh()  # an hour later: skipped
        cache._unreadable["2"] -= 25 * 3600
        await stale_refresh()  # a day later: asked again
        await cache.refresh()  # Refresh now asks about every title

    asyncio.run(run())
    assert tautulli.asked == ["2", "2", "1", "2"]
    assert _titles(cache.media_items.values()) == ["Kept"]


def test_close_stops_refresh_now_and_any_later_update(tmp_path):
    tautulli = LibraryTautulli({"1": "Title"})
    cache = _stale_cache(tautulli, tmp_path, [])
    asked_server = []
    server_info = tautulli.get_server_info

    async def counted_server_info():
        asked_server.append(1)
        return await server_info()

    tautulli.get_server_info = counted_server_info

    async def run():
        started, never = asyncio.Event(), asyncio.Event()

        async def stuck(rating_key):  # Tautulli still working through the library
            started.set()
            await never.wait()

        tautulli.get_metadata = stuck
        click = asyncio.create_task(cache.refresh())  # an admin pressed Refresh now
        await started.wait()
        await cache.close()  # the bot shuts down
        asked_server.clear()
        await asyncio.wait_for(cache.update_cache(full=True), timeout=2)  # a late caller
        return click

    click = asyncio.run(run())
    assert click.cancelled()
    assert asked_server == []  # nothing reopened the Tautulli session after close
