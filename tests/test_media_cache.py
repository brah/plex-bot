"""MediaCache: background refresh of a stale cache, keeping titles a failed fetch missed, and the disk file."""

import asyncio
import json
from datetime import datetime, timedelta

from fakes import FakeTautulli
from media_cache import MediaCache


class LibraryTautulli(FakeTautulli):
    """One movie library; get_metadata fails (no answer) for the rating keys in `failing`."""

    def __init__(self, titles: dict):
        super().__init__()
        self.libraries = [{"section_id": 1, "section_name": "Movies", "section_type": "movie"}]
        self.library_items = {"1": [{"rating_key": key} for key in titles]}
        self.metadata = {key: {"title": title, "media_type": "movie", "genres": ["Drama"]}
                         for key, title in titles.items()}
        self.failing = set()

    async def get_metadata(self, rating_key):
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
    cache = _stale_cache(LibraryTautulli({"1": "New title"}), tmp_path, [_item("1", "Old title")])

    async def run():
        served = await cache.get_items()
        refresh = cache._refresh
        assert refresh is not None and not refresh.done()  # the caller didn't wait for it
        await refresh
        return served

    served = asyncio.run(run())
    assert _titles(served) == ["Old title"]
    assert _titles(cache.media_items.values()) == ["New title"]
    assert cache.is_cache_valid()
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))["items"][0]["title"] == "New title"


def test_empty_cache_waits_for_the_first_fetch(tmp_path):
    cache = _stale_cache(LibraryTautulli({"1": "Only title"}), tmp_path, [])
    assert _titles(asyncio.run(cache.get_items())) == ["Only title"]


def test_a_failed_fetch_keeps_the_title_we_had(tmp_path):
    tautulli = LibraryTautulli({"1": "Refreshed", "2": "Unreachable now"})
    tautulli.failing = {"2"}
    cache = _stale_cache(tautulli, tmp_path, [_item("1", "Old one"), _item("2", "Old two")])
    asyncio.run(cache.refresh())
    assert _titles(cache.media_items.values()) == ["Old two", "Refreshed"]


def test_disk_round_trip_keeps_items_and_freshness(tmp_path):
    cache = _stale_cache(LibraryTautulli({}), tmp_path, [_item("1", "Kept")])
    cache.last_updated = datetime(2026, 10, 1, 12, 0)
    asyncio.run(cache._write_items_to_disk(list(cache.media_items.values())))

    loaded = MediaCache(LibraryTautulli({}), cache_file_path=str(tmp_path / "cache.json"))
    asyncio.run(loaded.load_cache_from_disk())
    assert loaded.media_items == cache.media_items
    assert loaded.last_updated == datetime(2026, 10, 1, 12, 0)


def test_legacy_list_file_loads_with_its_modified_time(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps([_item("7", "Legacy")]), encoding="utf-8")
    cache = MediaCache(LibraryTautulli({}), cache_file_path=str(path))
    asyncio.run(cache.load_cache_from_disk())
    assert list(cache.media_items) == ["7"] and cache.last_updated is not None


def test_close_stops_a_background_refresh(tmp_path):
    cache = _stale_cache(LibraryTautulli({"1": "New"}), tmp_path, [_item("1", "Old")])

    async def run():
        await cache.ensure_cache_valid()
        await cache.close()
        return cache._refresh

    assert asyncio.run(run()).cancelled()
