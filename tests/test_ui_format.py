"""ui.format: the text helpers every response uses."""

from datetime import datetime, timezone

import nextcord

from plexbot.config import config
from plexbot.ui.format import file_size, format_ms, is_truthy, join, plural, progress_bar, timestamp, to_int, truncate
from plexbot.ui.theme import Colour, Limits, brand_colour, media_colour


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 10) == "hello"

    def test_exact_limit_unchanged(self):
        assert truncate("x" * 10, 10) == "x" * 10

    def test_long_text_cut_with_ellipsis(self):
        result = truncate("x" * 20, 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_none_becomes_empty(self):
        assert truncate(None, 10) == ""

    def test_non_string_is_stringified(self):
        assert truncate(12345, 3) == "12…"


class TestFormatMs:
    def test_minutes(self):
        assert format_ms(754000) == "12:34"

    def test_hours(self):
        assert format_ms(5025000) == "1:23:45"

    def test_string_and_missing_values(self):
        assert format_ms("754000") == "12:34"
        assert format_ms(None) == "0:00"


class TestProgressBar:
    def test_half(self):
        assert progress_bar(0.5) == "█████░░░░░"

    def test_clamped(self):
        assert progress_bar(1.7) == "██████████"
        assert progress_bar(-1) == "░░░░░░░░░░"

    def test_none_is_empty(self):
        assert progress_bar(None) == ""


class TestTimestamp:
    def test_from_datetime(self):
        moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert timestamp(moment) == f"<t:{int(moment.timestamp())}:R>"

    def test_from_number_with_style(self):
        assert timestamp(1700000000.9, "t") == "<t:1700000000:t>"


class TestSmallHelpers:
    def test_plural(self):
        assert plural(1, "stream") == "1 stream"
        assert plural(2, "stream") == "2 streams"
        assert plural(1234, "movie") == "1,234 movies"
        assert plural(3, "match", "matches") == "3 matches"

    def test_join_skips_empty_parts(self):
        assert join(["a", None, "", "b"]) == "a · b"
        assert join(["a", "b"], " ") == "a b"

    def test_to_int(self):
        assert to_int("42") == 42
        assert to_int("") == 0
        assert to_int(None, default=-1) == -1

    def test_is_truthy(self):
        assert all(is_truthy(v) for v in (True, 1, "1", "true", "True"))
        assert not any(is_truthy(v) for v in (False, 0, "0", "", None, "false"))


class TestTheme:
    def test_media_colour(self):
        assert media_colour("movie") == Colour.MOVIE
        assert media_colour("episode") == Colour.TV
        assert media_colour("track") == Colour.MUSIC
        assert media_colour(None) == Colour.NEUTRAL

    def test_brand_colour_accepts_hex_text(self, monkeypatch):
        values = {}
        monkeypatch.setattr(config, "get", lambda section, key, default=None: values.get(key, default))
        for setting, expected in [(0x112233, 0x112233), ("#E5A00D", 0xE5A00D), ("0x4A90D9", 0x4A90D9),
                                  ("orange", Colour.MOVIE), ("#FFFFFFFF", Colour.MOVIE), (True, Colour.MOVIE)]:
            values["plex_embed_color"] = setting
            assert brand_colour() == expected, setting
        nextcord.Embed(colour=brand_colour())  # an embed accepts it

    def test_limits(self):
        assert Limits.DESCRIPTION == 4096
        assert Limits.SELECT_OPTIONS == 25


def test_file_size_uses_drive_label_units():
    assert file_size(8_100_000_000_000) == "8.1 TB"
    assert file_size(12_449_000_000_000) == "12.4 TB"
    assert file_size(512_000_000_000) == "512 GB"
    assert file_size(999) == "999 B"
    assert file_size(2_500_000_000_000_000) == "2.5 PB"


def test_day_label():
    from datetime import date

    from plexbot.ui.format import day_label

    today = date(2026, 10, 22)
    assert day_label(date(2026, 10, 22), today) == "Today"
    assert day_label(date(2026, 10, 23), today) == "Tomorrow"
    assert day_label(date(2026, 10, 24), today) == "Saturday 24 Oct"
