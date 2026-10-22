"""TMDB on media cards: More like this, Trailer and the next episode."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from fakes import FakeTautulli, FakeTMDB, build_media_bot, patch_images
from plexbot import utilities

TRAILER = {"site": "YouTube", "type": "Trailer", "key": "abc", "official": True,
           "published_at": "2001-01-01T00:00:00.000Z"}
MOVIE = {"rating_key": "1", "media_type": "movie", "title": "Spirited Away", "year": "2001",
         "guids": ["imdb://tt0245429", "tmdb://10"]}
SHOW = {"rating_key": "20", "media_type": "show", "title": "Severance", "year": "2022", "guids": ["tmdb://20"]}
EPISODE = {"rating_key": "21", "media_type": "episode", "title": "Hello, Ms. Cobel", "grandparent_title": "Severance",
           "parent_media_index": "1", "media_index": "2", "guids": ["tmdb://999"],
           "parent_rating_key": "22", "grandparent_rating_key": "20"}
SEASON = {"rating_key": "22", "media_type": "season", "title": "Season 1", "parent_title": "Severance",
          "guids": ["tmdb://888"], "parent_rating_key": "20"}
CACHE = [
    {"rating_key": "1", "media_type": "movie", "title": "Spirited Away", "year": "2001", "tmdb_id": 10},
    {"rating_key": "11", "media_type": "movie", "title": "My Neighbor Totoro", "year": "1988", "tmdb_id": 11},
    {"rating_key": "12", "media_type": "movie", "title": "Princess Mononoke", "year": "1997", "tmdb_id": 12},
    {"rating_key": "13", "media_type": "show", "title": "Spirited, the show", "year": "2020", "tmdb_id": 13},
    {"rating_key": "20", "media_type": "show", "title": "Severance", "year": "2022", "tmdb_id": 77},
]
MOVIE_TMDB = {
    "recommendations": {"results": [{"id": 10}, {"id": 13}, {"id": 12}]},
    "similar": {"results": [{"id": 12}]},
    "videos": {"results": [TRAILER]},
}


def _tomorrow() -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()


@pytest.fixture(autouse=True)
def no_mappings(monkeypatch):
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings", staticmethod(lambda: []))
    patch_images(monkeypatch)


def _tautulli():
    tautulli = FakeTautulli([])
    tautulli.metadata = {"1": MOVIE, "20": SHOW, "21": EPISODE, "22": SEASON, "2": dict(MOVIE, guids=[])}
    return tautulli


def _tmdb(**kwargs):
    return FakeTMDB(titles={("movie", 10): MOVIE_TMDB}, pages={("movie", 10): [{"id": 11}]}, **kwargs)


def _run(coro_fn, tmdb=None, items=CACHE):
    async def run():
        bot, cog = build_media_bot(_tautulli(), items, tmdb=tmdb)
        return await coro_fn(cog.details)

    return asyncio.run(run())


def test_movie_extras():
    extras = _run(lambda details: details.tmdb_extras(MOVIE), _tmdb())
    assert [item["rating_key"] for item in extras.similar] == ["12", "11"]
    assert extras.trailer == "https://www.youtube.com/watch?v=abc"
    assert extras.next_episode is None


def test_similar_capped_at_ten():
    many = [{"rating_key": str(100 + i), "media_type": "movie", "title": f"Film {i}", "tmdb_id": 100 + i}
            for i in range(15)]
    tmdb = FakeTMDB(titles={("movie", 10): {"recommendations": {"results": [{"id": 100 + i} for i in range(15)]}}})
    extras = _run(lambda details: details.tmdb_extras(MOVIE), tmdb, items=CACHE + many)
    assert len(extras.similar) == 10


def test_show_card_has_next_episode_line():
    tmdb = FakeTMDB(titles={("tv", 20): {"next_episode_to_air": {
        "air_date": _tomorrow(), "season_number": 2, "episode_number": 5}}})
    reply = _run(lambda details: details.render("20"), tmdb)
    assert "-# 📅 Next episode: S02E05 · Tomorrow" in reply.embeds[0].description


@pytest.mark.parametrize("item", [EPISODE, SEASON])
def test_episode_and_season_use_their_show(item):
    tmdb = FakeTMDB()
    _run(lambda details: details.tmdb_extras(item), tmdb)
    assert ("title", "tv", 77) in tmdb.calls
    assert not any(888 in call or 999 in call for call in tmdb.calls)


def test_episode_with_uncached_show_has_no_extras():
    tmdb = FakeTMDB()
    extras = _run(lambda details: details.tmdb_extras(EPISODE), tmdb, items=CACHE[:4])
    assert (extras.trailer, extras.similar, extras.next_episode) == (None, [], None)
    assert tmdb.calls == []


def test_movie_without_tmdb_id():
    tmdb = _tmdb()
    extras = _run(lambda details: details.tmdb_extras(dict(MOVIE, guids=[])), tmdb)
    assert extras.similar == [] and tmdb.calls == []


def test_card_without_tmdb_answer():
    plain = _run(lambda details: details.render("1"))
    down = _run(lambda details: details.render("1"), FakeTMDB(reachable=False))
    assert down.embeds[0].description == plain.embeds[0].description
    assert "📅" not in plain.embeds[0].description
