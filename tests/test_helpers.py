"""Unit tests for the pure helper functions.

These cover the deterministic, side-effect-free helpers (response parsing,
duration/size formatting, argument parsing). Importing the cogs here also acts
as an import smoke test for the whole dependency set.
"""

import pytest

from tautulli_wrapper import Tautulli
from utilities import format_duration
from cogs.media_commands import (
    MediaCommands,
    _format_ms,
    _truncate,
    _format_size,
    _format_bytes_speed,
)


class TestCheckResponse:
    def test_success(self):
        assert Tautulli.check_response({"response": {"result": "success"}}) is True

    def test_error_result(self):
        assert Tautulli.check_response({"response": {"result": "error"}}) is False

    def test_none(self):
        assert Tautulli.check_response(None) is False

    def test_empty_dict(self):
        assert Tautulli.check_response({}) is False


class TestGetResponseData:
    def test_extracts_data(self):
        assert Tautulli.get_response_data({"response": {"data": [1, 2]}}) == [1, 2]

    def test_default_on_none_response(self):
        assert Tautulli.get_response_data(None, []) == []

    def test_default_when_key_missing(self):
        assert Tautulli.get_response_data({"response": {}}, {}) == {}

    def test_explicit_null_data_returns_none(self):
        # 'data' is present but null -> returns None, NOT the default. This is the
        # exact edge the call sites guard against with `(... or {}).get("data", [])`.
        assert Tautulli.get_response_data({"response": {"data": None}}, {}) is None


class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "0m"),
            (59, "0m"),
            (60, "1m"),
            (3600, "1h"),
            (3660, "1h 1m"),
            (86400, "1d"),
            (90000, "1d 1h"),
            (93780, "1d 2h 3m"),
        ],
    )
    def test_format(self, seconds, expected):
        assert format_duration(seconds) == expected


class TestParseRandomArgs:
    def test_empty(self):
        assert MediaCommands._parse_random_args(()) == (None, None)

    def test_type_only(self):
        assert MediaCommands._parse_random_args(("movie",)) == ("movie", None)

    def test_type_and_genre(self):
        assert MediaCommands._parse_random_args(("tv", "comedy")) == ("tv", "comedy")

    def test_genre_only(self):
        assert MediaCommands._parse_random_args(("horror",)) == (None, "horror")

    def test_multiword_genre(self):
        assert MediaCommands._parse_random_args(("science", "fiction")) == (None, "science fiction")

    def test_case_insensitive_type(self):
        assert MediaCommands._parse_random_args(("Movie",)) == ("movie", None)


class TestFormatMs:
    def test_under_an_hour(self):
        assert _format_ms(754000) == "12:34"

    def test_over_an_hour(self):
        assert _format_ms(5025000) == "1:23:45"


class TestMiscFormatters:
    def test_truncate_short(self):
        assert _truncate("hi", 10) == "hi"

    def test_truncate_long(self):
        assert _truncate("hello world", 5) == "hell…"

    def test_format_size_gb(self):
        assert _format_size(2_500_000_000) == "2.50 GB"

    def test_format_size_mb(self):
        assert _format_size(5_000_000) == "5.0 MB"

    def test_bytes_speed_mb(self):
        assert _format_bytes_speed(2_000_000) == "2.0 MB/s"

    def test_bytes_speed_kb(self):
        assert _format_bytes_speed(500_000) == "500 KB/s"
