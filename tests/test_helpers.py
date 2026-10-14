"""Unit tests for the pure helper functions.

These cover the deterministic, side-effect-free helpers (response parsing,
duration formatting). Importing the cogs here also acts
as an import smoke test for the whole dependency set.
"""

import pytest

from tautulli_wrapper import Tautulli
from utilities import format_duration
import cogs.media_commands  # noqa: F401  (import smoke test)
from ui.format import format_ms as _format_ms


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


class TestFormatMs:
    def test_under_an_hour(self):
        assert _format_ms(754000) == "12:34"

    def test_over_an_hour(self):
        assert _format_ms(5025000) == "1:23:45"

