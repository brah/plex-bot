"""Unit tests for the `plex logs` helpers (parsing, filtering, redaction, preview fitting)."""

import logging
from pathlib import Path

from cogs.server_commands import (
    _filter_records,
    _find_log_path,
    _fit_tail,
    _parse_logs_query,
    _record_level,
    _redact,
    _split_records,
)

INFO_REC = "2026-10-14 17:51:36 INFO plexbot: Bot is ready"
WARN_REC = "2026-10-14 17:52:00 WARNING plexbot.tautulli: Tautulli connection using HTTP"
ERROR_REC = (
    "2026-10-14 17:53:10 ERROR plexbot.server_commands: Failed to retrieve server status\n"
    "Traceback (most recent call last):\n"
    '  File "cogs/server_commands.py", line 1, in status\n'
    "ValueError: boom"
)
CRIT_REC = "2026-10-14 17:54:00 CRITICAL plexbot: Tautulli timed out completely"
LOG_TEXT = "\n".join([INFO_REC, WARN_REC, ERROR_REC, CRIT_REC]) + "\n"


class TestParseLogsQuery:
    def test_none(self):
        assert _parse_logs_query(None) == (None, None)

    def test_blank(self):
        assert _parse_logs_query("   ") == (None, None)

    def test_level_only(self):
        assert _parse_logs_query("error") == (logging.ERROR, None)

    def test_level_case_insensitive(self):
        assert _parse_logs_query("WARNING") == (logging.WARNING, None)

    def test_warn_alias(self):
        assert _parse_logs_query("warn") == (logging.WARNING, None)

    def test_search_only(self):
        assert _parse_logs_query("tautulli timed out") == (None, "tautulli timed out")

    def test_level_and_search(self):
        assert _parse_logs_query("error tautulli") == (logging.ERROR, "tautulli")

    def test_level_and_quoted_search(self):
        assert _parse_logs_query('error "timed out"') == (logging.ERROR, "timed out")

    def test_quoted_level_word_is_search(self):
        assert _parse_logs_query('"error"') == (None, "error")

    def test_single_quoted_search(self):
        assert _parse_logs_query("'info'") == (None, "info")


class TestSplitRecords:
    def test_traceback_stays_with_its_record(self):
        records = _split_records(LOG_TEXT)
        assert records == [INFO_REC, WARN_REC, ERROR_REC, CRIT_REC]

    def test_empty(self):
        assert _split_records("") == []

    def test_leading_lines_without_timestamp(self):
        records = _split_records("stray line\n" + INFO_REC + "\n")
        assert records == ["stray line", INFO_REC]


class TestRecordLevel:
    def test_levels(self):
        assert _record_level(INFO_REC) == logging.INFO
        assert _record_level(ERROR_REC) == logging.ERROR

    def test_no_timestamp(self):
        assert _record_level("stray line") is None


class TestFilterRecords:
    records = [INFO_REC, WARN_REC, ERROR_REC, CRIT_REC]

    def test_no_filter(self):
        assert _filter_records(self.records, None, None) == self.records

    def test_min_level(self):
        assert _filter_records(self.records, logging.ERROR, None) == [ERROR_REC, CRIT_REC]

    def test_search_case_insensitive(self):
        assert _filter_records(self.records, None, "TAUTULLI") == [WARN_REC, CRIT_REC]

    def test_search_matches_traceback_lines(self):
        assert _filter_records(self.records, None, "valueerror") == [ERROR_REC]

    def test_level_and_search(self):
        assert _filter_records(self.records, logging.ERROR, "tautulli") == [CRIT_REC]

    def test_level_filter_drops_unlevelled_records(self):
        assert _filter_records(["stray line", INFO_REC], logging.DEBUG, None) == [INFO_REC]


class TestRedact:
    def test_configured_secrets(self):
        text = "token abc123secret and key KEY999"
        assert _redact(text, ["abc123secret", "KEY999"]) == "token [REDACTED] and key [REDACTED]"

    def test_empty_and_missing_secrets_ignored(self):
        assert _redact("nothing here", ["", None]) == "nothing here"

    def test_non_string_secret(self):
        assert _redact("pin 123456", [123456]) == "pin [REDACTED]"

    def test_query_params(self):
        text = "failed: 500, url='http://host:8181/api/v2?apikey=deadbeef&cmd=get_activity'"
        assert _redact(text, []) == (
            "failed: 500, url='http://host:8181/api/v2?apikey=[REDACTED]&cmd=get_activity'"
        )

    def test_query_params_case_insensitive(self):
        assert _redact("?API_KEY=abc&x=1", []) == "?API_KEY=[REDACTED]&x=1"


class TestFitTail:
    def test_all_fit(self):
        assert _fit_tail(["a", "b", "c"], 100) == ["a", "b", "c"]

    def test_keeps_most_recent(self):
        # "bb\ncc" is 5 chars; adding "aa" would need 8.
        assert _fit_tail(["aa", "bb", "cc"], 6) == ["bb", "cc"]

    def test_long_last_record_is_truncated(self):
        shown = _fit_tail(["x" * 50], 20)
        assert len(shown) == 1
        assert len(shown[0]) <= 20
        assert shown[0].endswith("…")

    def test_empty(self):
        assert _fit_tail([], 100) == []


class TestFindLogPath:
    def test_finds_file_handler(self, tmp_path):
        log = logging.getLogger("test_logs_find")
        handler = logging.FileHandler(tmp_path / "bot.log", delay=True)
        log.addHandler(handler)
        try:
            assert _find_log_path(log) == Path(handler.baseFilename)
        finally:
            log.removeHandler(handler)
            handler.close()

    def test_none_without_file_handler(self):
        log = logging.getLogger("test_logs_none")
        log.propagate = False
        assert _find_log_path(log) is None
