"""The Sonarr client: the calendar request, and failures."""

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
