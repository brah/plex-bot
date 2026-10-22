"""TMDB on media cards: More like this, Trailer and the next episode."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from fakes import (
    MESSAGE_ID, FakeTautulli, FakeTMDB, Recorder, build_media_bot, component_payload, interaction,
    patch_images, settle,
)
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
    {"rating_key": "1", "media_type": "movie", "title": "Spirited Away", "year": "2001", "tmdb_id": 10,
     "genres": ["animation"]},
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
    tautulli.metadata = {"1": MOVIE, "20": SHOW, "21": EPISODE, "22": SEASON, "2": dict(MOVIE, guids=[]),
                         "12": {"rating_key": "12", "media_type": "movie", "title": "Princess Mononoke",
                                "year": "1997"}}
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


def _run_cog(coro_fn, tmdb=None):
    async def run():
        bot, cog = build_media_bot(_tautulli(), CACHE, tmdb=tmdb)
        return await coro_fn(bot, cog)

    return asyncio.run(run())


def _movie(bot, cog):
    from plexbot.cogs.media_commands import MediaCommands

    return MediaCommands.movie.callback(cog, interaction(bot), title="key:1")


def test_lookup_card_has_trailer_and_more_like_this(monkeypatch):
    from plexbot.media_details import MoreLikeThisSelect

    recorder = Recorder(monkeypatch)
    _run_cog(_movie, _tmdb())
    view = recorder.last("followup")["view"]
    links = [item.url for item in view.children if getattr(item, "url", None)]
    assert links == ["https://www.youtube.com/watch?v=abc"]
    select = next(item for item in view.children if isinstance(item, MoreLikeThisSelect))
    assert [option.value for option in select.options] == ["12", "11"]
    assert select.placeholder == "More like this on the server…"


def test_no_controls_without_tmdb(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run_cog(_movie)
    assert "view" not in recorder.last("followup")


def test_more_like_this_pick_answers_privately(monkeypatch):
    from plexbot.media_details import MoreLikeThisSelect

    recorder = Recorder(monkeypatch)

    async def run(bot, cog):
        await _movie(bot, cog)
        view = recorder.last("followup")["view"]
        bot._connection.store_view(view, MESSAGE_ID)
        select = next(item for item in view.children if isinstance(item, MoreLikeThisSelect))
        recorder.calls.clear()
        bot._connection.parse_interaction_create(
            component_payload(select.custom_id, component_type=3, values=["12"]))
        await settle()
        return view

    view = _run_cog(run, _tmdb())
    assert recorder.kinds() == ["edit_message", "followup"]
    assert recorder.calls[0][1]["view"] is view  # the dropdown is reset, so it can be used again
    sent = recorder.last("followup")
    assert sent["ephemeral"] is True
    assert sent["embeds"][0].title == "Princess Mononoke (1997)"


def test_roll_again_swaps_controls(monkeypatch):
    from plexbot.cogs.media_commands import MediaCommands
    from plexbot.media_details import MoreLikeThisSelect

    recorder = Recorder(monkeypatch)

    async def run(bot, cog):
        await MediaCommands.random_pick.callback(cog, interaction(bot), kind="movie", genre="animation")
        view = recorder.last("followup")["view"]
        bot._connection.store_view(view, MESSAGE_ID)
        bot._connection.parse_interaction_create(component_payload(view.roll_again.custom_id))
        await settle()
        return view

    view = _run_cog(run, _tmdb())
    assert "edit_original_message" in recorder.kinds()
    assert view.children[0] is view.roll_again
    assert sum(isinstance(item, MoreLikeThisSelect) for item in view.children) == 1
