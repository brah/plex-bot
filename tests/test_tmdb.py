"""The TMDB client's pure helpers: ids from Plex guids, trailer choice, suggestion order and auth."""

from plexbot.tautulli_wrapper import TMDB


def test_id_from_guids():
    assert TMDB.id_from_guids(["imdb://tt0312004", "tmdb://533", "tvdb://981"]) == 533
    assert TMDB.id_from_guids(["imdb://tt1"]) is None
    assert TMDB.id_from_guids(None) is None
    assert TMDB.id_from_guids(["tmdb://abc"]) is None


def test_trailer_url_prefers_official_then_newest():
    videos = [
        {"site": "Vimeo", "type": "Trailer", "key": "v", "official": True,
         "published_at": "2024-01-01T00:00:00.000Z"},
        {"site": "YouTube", "type": "Teaser", "key": "t", "official": True,
         "published_at": "2024-01-01T00:00:00.000Z"},
        {"site": "YouTube", "type": "Trailer", "key": "old", "official": True,
         "published_at": "2020-01-01T00:00:00.000Z"},
        {"site": "YouTube", "type": "Trailer", "key": "new", "official": True,
         "published_at": "2023-01-01T00:00:00.000Z"},
        {"site": "YouTube", "type": "Trailer", "key": "fan", "official": False,
         "published_at": "2025-01-01T00:00:00.000Z"},
    ]
    assert TMDB.trailer_url(videos) == "https://www.youtube.com/watch?v=new"
    assert TMDB.trailer_url([]) is None


def test_suggestion_ids_order_and_dedupe():
    details = {"recommendations": {"results": [{"id": 1}, {"id": 2}]},
               "similar": {"results": [{"id": 2}, {"id": 4}]}}
    assert TMDB.suggestion_ids(details, [{"id": 3}, {"id": 1}]) == [1, 2, 3, 4]
    assert TMDB.suggestion_ids({}, None) == []


def test_api_key_or_read_access_token():
    assert TMDB("abc123")._auth() == ({}, {"api_key": "abc123"})
    assert TMDB("eyJhbGciOi.x.y")._auth() == ({"Authorization": "Bearer eyJhbGciOi.x.y"}, {})
