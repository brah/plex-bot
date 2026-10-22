"""The Sonarr and Radarr clients: the calendar request, and failures."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fakes import FakeHTTPSession
from plexbot.tautulli_wrapper import Sonarr

START = datetime(2026, 10, 22, tzinfo=timezone.utc) - timedelta(hours=2)  # midnight in Berlin
END = START + timedelta(days=7)


def test_calendar_asks_for_the_week_with_series():
    sonarr = Sonarr("http://192.168.1.10:8989/sonarr/", "secret")
    sonarr.session = FakeHTTPSession((200, [{"seasonNumber": 1}]))
    assert asyncio.run(sonarr.calendar(START, END)) == [{"seasonNumber": 1}]
    url, kwargs = sonarr.session.requests[0]
    assert url == "http://192.168.1.10:8989/sonarr/api/v3/calendar"
    assert kwargs["headers"] == {"X-Api-Key": "secret"}
    assert kwargs["params"] == {"start": "2026-10-21T22:00:00Z", "end": "2026-10-28T22:00:00Z",
                                "includeSeries": "true"}


def test_a_refused_key_gives_no_episodes(caplog):
    sonarr = Sonarr("http://sonarr:8989", "wrong")
    sonarr.session = FakeHTTPSession((401, {}))
    with caplog.at_level(logging.ERROR, logger="plexbot.tautulli_wrapper"):
        assert asyncio.run(sonarr.calendar(START, END)) is None
    assert "sonarr.apikey" in caplog.text


def test_plain_http_over_the_internet_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="plexbot.tautulli_wrapper"):
        Sonarr("http://sonarr:8989", "key")
        Sonarr("https://sonarr.example.com", "key")
        assert "plaintext" not in caplog.text
        Sonarr("http://sonarr.example.com", "key")
    assert "plaintext" in caplog.text


def test_an_address_without_a_scheme_gets_http():
    # Like tautulli.ip, or a Docker service name.
    assert Sonarr("sonarr:8989", "key").api_url == "http://sonarr:8989/api/v3/"
    assert Sonarr("https://sonarr.example.com", "key").api_url == "https://sonarr.example.com/api/v3/"


def test_radarr_calendar_asks_for_movies():
    from plexbot.tautulli_wrapper import Radarr

    radarr = Radarr("radarr:7878", "secret")
    radarr.session = FakeHTTPSession((200, [{"title": "Dune: Part Three"}]))
    assert asyncio.run(radarr.calendar(START, END)) == [{"title": "Dune: Part Three"}]
    url, kwargs = radarr.session.requests[0]
    assert url == "http://radarr:7878/api/v3/calendar"
    assert kwargs["headers"] == {"X-Api-Key": "secret"}
    assert kwargs["params"] == {"start": "2026-10-21T22:00:00Z", "end": "2026-10-28T22:00:00Z"}


def test_radarr_names_its_own_setting(caplog):
    from plexbot.tautulli_wrapper import Radarr

    radarr = Radarr("http://radarr:7878", "wrong")
    radarr.session = FakeHTTPSession((401, {}))
    with caplog.at_level(logging.ERROR, logger="plexbot.tautulli_wrapper"):
        assert asyncio.run(radarr.calendar(START, END)) is None
    assert "Radarr refused" in caplog.text and "radarr.apikey" in caplog.text
