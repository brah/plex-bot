"""Errors nextcord would print to stderr must reach the log file (and hence /plexadmin logs)."""

import asyncio
import logging

import nextcord

from errors import LoggedModal, LoggedView
from plexbot import PlexBot


class FakeResponse:
    def __init__(self, done=False):
        self.done = done
        self.sent = []

    def is_done(self):
        return self.done

    async def send_message(self, content=None, *, ephemeral=False, **kwargs):
        self.sent.append((content, ephemeral))


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, *, ephemeral=False, **kwargs):
        self.sent.append((content, ephemeral))


class FakeInteraction:
    def __init__(self, responded=False):
        self.response = FakeResponse(done=responded)
        self.followup = FakeFollowup()


def test_event_handler_errors_are_logged(caplog):
    async def run():
        bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None)
        try:
            raise RuntimeError("sync blew up")
        except RuntimeError:
            await bot.on_error("on_guild_available")

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    record = next(r for r in caplog.records if "on_guild_available" in r.getMessage())
    assert record.exc_info and "sync blew up" in str(record.exc_info[1])


def test_view_errors_are_logged_and_answered_privately(caplog):
    async def run():
        view = LoggedView()
        interaction = FakeInteraction()
        await view.on_error(ValueError("button broke"), nextcord.ui.Button(label="x"), interaction)
        return interaction

    with caplog.at_level(logging.ERROR):
        interaction = asyncio.run(run())
    record = next(r for r in caplog.records if "LoggedView" in r.getMessage())
    assert record.exc_info and "button broke" in str(record.exc_info[1])
    assert len(interaction.response.sent) == 1 and interaction.response.sent[0][1] is True


def test_view_error_after_response_uses_followup():
    async def run():
        interaction = FakeInteraction(responded=True)
        await LoggedView().on_error(ValueError("late"), nextcord.ui.Button(label="x"), interaction)
        return interaction

    interaction = asyncio.run(run())
    assert interaction.response.sent == []
    assert len(interaction.followup.sent) == 1 and interaction.followup.sent[0][1] is True


def test_modal_errors_are_logged_and_answered_privately(caplog):
    async def run():
        modal = LoggedModal(title="Test")
        interaction = FakeInteraction()
        await modal.on_error(KeyError("form broke"), interaction)
        return interaction

    with caplog.at_level(logging.ERROR):
        interaction = asyncio.run(run())
    record = next(r for r in caplog.records if "LoggedModal" in r.getMessage())
    assert record.exc_info and "form broke" in str(record.exc_info[1])
    assert interaction.response.sent[0][1] is True
