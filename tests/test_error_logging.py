"""Errors nextcord would print to stderr must reach the log file (and hence /plexadmin logs)."""

import asyncio
import logging

import nextcord
import pytest

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


def _patch_main_config(monkeypatch):
    import plexbot

    values = {("core", "token"): "token", ("core", "prefix"): "plex ", ("discord", "server_id"): ""}
    monkeypatch.setattr(plexbot.config, "initialize", lambda: True)
    monkeypatch.setattr(
        plexbot.config, "get", lambda section, key, default=None: values.get((section, key), default)
    )
    return plexbot


def test_crash_exits_non_zero_so_supervisors_restart(monkeypatch):
    plexbot = _patch_main_config(monkeypatch)

    def crash(self, token):
        raise TypeError("gateway handler blew up")

    monkeypatch.setattr(plexbot.PlexBot, "run", crash)
    with pytest.raises(SystemExit) as exc:
        plexbot.main()
    assert exc.value.code == 1


def test_ctrl_c_still_exits_cleanly(monkeypatch):
    plexbot = _patch_main_config(monkeypatch)

    def interrupt(self, token):
        raise KeyboardInterrupt

    monkeypatch.setattr(plexbot.PlexBot, "run", interrupt)
    plexbot.main()  # no SystemExit


@pytest.mark.parametrize("legacy", [None, True])
def test_message_content_intent_only_for_legacy_text_commands(monkeypatch, legacy):
    from types import SimpleNamespace

    plexbot = _patch_main_config(monkeypatch)
    get = plexbot.config.get
    monkeypatch.setattr(plexbot.config, "get", lambda section, key, default=None: (
        legacy if (section, key) == ("core", "legacy_text_commands") else get(section, key, default)))
    seen = {}
    monkeypatch.setattr(plexbot.PlexBot, "run", lambda self, token: seen.update(bot=self))
    plexbot.main()
    bot = seen["bot"]
    assert bot.intents.members and bot.intents.message_content is bool(legacy)
    prefixes = bot.command_prefix(SimpleNamespace(user=SimpleNamespace(id=1)), None)
    assert "<@1> " in prefixes  # "@PlexBot top" is answered either way
    assert ("plex " in prefixes) is bool(legacy)


def test_view_error_card_is_the_oops_state(monkeypatch):
    import ui.embeds

    monkeypatch.setattr(ui.embeds, "mascot", lambda slot: None)
    sent = {}

    class Response(FakeResponse):
        async def send_message(self, content=None, *, ephemeral=False, **kwargs):
            sent.update(kwargs, ephemeral=ephemeral)

    async def run():
        interaction = FakeInteraction()
        interaction.response = Response()
        await LoggedView().on_error(ValueError("x"), nextcord.ui.Button(label="x"), interaction)

    asyncio.run(run())
    assert sent["ephemeral"] is True
    assert "Something went wrong" in sent["embeds"][0].description


def test_slash_error_names_the_command(monkeypatch):
    import ui.embeds
    from errors import ErrorHandler

    monkeypatch.setattr(ui.embeds, "mascot", lambda slot: None)
    sent = {}

    class Response(FakeResponse):
        async def send_message(self, content=None, *, ephemeral=False, **kwargs):
            sent.update(kwargs, ephemeral=ephemeral)

    async def run():
        bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None)
        interaction = FakeInteraction()
        interaction.response = Response()
        interaction.data = {"name": "plex", "options": [{"type": 1, "name": "status"}]}
        error = nextcord.ApplicationInvokeError(RuntimeError("boom"))
        await ErrorHandler(bot).application_error_handler(interaction, error)

    asyncio.run(run())
    assert sent["ephemeral"] is True and "`/plex status`" in sent["embeds"][0].description


def test_slash_error_after_public_defer_replaces_the_placeholder(monkeypatch):
    import ui.embeds
    from errors import ErrorHandler

    monkeypatch.setattr(ui.embeds, "mascot", lambda slot: None)
    deleted = []

    async def delete_original_message():
        deleted.append(True)

    async def run():
        bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None)
        interaction = FakeInteraction(responded=True)
        interaction.delete_original_message = delete_original_message
        interaction.data = {"name": "plex", "options": [{"type": 1, "name": "status"}]}
        error = nextcord.ApplicationInvokeError(RuntimeError("boom"))
        await ErrorHandler(bot).application_error_handler(interaction, error)
        return interaction

    interaction = asyncio.run(run())
    assert deleted == [True]
    assert len(interaction.followup.sent) == 1 and interaction.followup.sent[0][1] is True
