"""The bot's version: from the image or git describe, compared with GitHub's latest release."""

import asyncio
import logging
import subprocess

import pytest

import plexbot.cogs.server_commands as server_commands
from fakes import FakeTautulli, build_bot
from plexbot import version


@pytest.fixture(autouse=True)
def fresh_version(monkeypatch):
    monkeypatch.delenv("BOT_VERSION", raising=False)
    version.bot_version.cache_clear()
    yield
    version.bot_version.cache_clear()


def test_image_version_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("BOT_VERSION", "1.2.0")
    assert version.bot_version() == "1.2.0"


def test_checkout_version_comes_from_git_describe_without_the_v(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: b"v1.1.0-3-gabc1234\n")
    assert version.bot_version() == "1.1.0-3-gabc1234"


def test_a_non_ascii_tag_doesnt_stop_the_bot_starting(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "v1.0.0-é\n".encode("utf-8"))
    assert version.bot_version() == "1.0.0-é"


def test_no_git_means_unknown(monkeypatch):
    def no_git(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "check_output", no_git)
    assert version.bot_version() == "unknown"


def test_this_checkout_has_a_version():
    # No tags yet, or a shallow CI clone: --always falls back to the commit hash.
    assert version.bot_version()
    assert not version.bot_version().startswith("v")


@pytest.mark.parametrize("latest, current, expected", [
    ("1.2.0", "1.1.0", True),
    ("1.10.0", "1.9.3", True),  # numbers, not strings
    ("1.1.0", "1.1.0", False),
    ("1.1.0", "1.1.0-3-gabc1234", False),  # main, ahead of the release
    ("1.2.0", "1.1.0-3-gabc1234", True),
    ("1.2.0", "dev", False),
    ("1.2.0", "unknown", False),
    ("1.2.0", "abc1234", False),  # a commit hash before the first tag
    (None, "1.1.0", False),
])
def test_is_newer(latest, current, expected):
    assert version.is_newer(latest, current) is expected


def test_unreachable_github_gives_no_release(monkeypatch):
    monkeypatch.setattr(version, "RELEASES_URL", "http://127.0.0.1:9/releases/latest")
    assert asyncio.run(version.latest_release()) is None


def test_startup_logs_a_newer_release(monkeypatch, caplog):
    monkeypatch.setattr(server_commands, "bot_version", lambda: "1.0.0")

    async def newer():
        return "1.1.0"

    async def run():
        _bot, cog = build_bot(FakeTautulli())
        await cog._log_startup_info()

    monkeypatch.setattr(server_commands, "latest_release", newer)
    with caplog.at_level(logging.INFO, logger="plexbot"):
        asyncio.run(run())
    assert "PlexBot 1.1.0 is out (running 1.0.0)" in caplog.text
