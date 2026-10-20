"""The :plex: application emoji: reused when the bot has it, uploaded when not, plain text when Discord refuses."""

import asyncio
import types

import nextcord
import pytest

from plexbot.bot import PLEX_EMOJI, install_plex_emoji
from plexbot.ui.theme import Emoji


class _Emoji:
    def __init__(self, name, emoji_id):
        self.name, self.id = name, emoji_id

    def __str__(self):
        return f"<:{self.name}:{self.id}>"


class _Bot:
    def __init__(self, emojis, refuse=False):
        self.emojis, self.refuse, self.uploads = emojis, refuse, []

    async def wait_until_ready(self):
        pass

    async def fetch_application_emojis(self):
        if self.refuse:
            raise nextcord.HTTPException(types.SimpleNamespace(status=503, reason="Unavailable"), "down")
        return self.emojis

    async def create_application_emoji(self, *, name, image):
        self.uploads.append((name, image))
        return _Emoji(name, 2)


@pytest.fixture(autouse=True)
def _plain_text(monkeypatch):
    monkeypatch.setattr(Emoji, "PLEX", "Plex Media Server")


def test_reuses_the_bots_emoji():
    bot = _Bot([_Emoji("other", 1), _Emoji("plex", 7)])
    asyncio.run(install_plex_emoji(bot))
    assert Emoji.PLEX == "<:plex:7>"
    assert bot.uploads == []


def test_uploads_it_the_first_time():
    bot = _Bot([])
    asyncio.run(install_plex_emoji(bot))
    assert bot.uploads == [("plex", PLEX_EMOJI.read_bytes())]
    assert len(bot.uploads[0][1]) < 256_000  # Discord's emoji size limit
    assert Emoji.PLEX == "<:plex:2>"


def test_plain_text_when_discord_refuses(caplog):
    asyncio.run(install_plex_emoji(_Bot([], refuse=True)))
    assert Emoji.PLEX == "Plex Media Server"
    assert "No :plex: emoji" in caplog.text
