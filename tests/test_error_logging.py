"""Errors nextcord would print to stderr must reach the log file (and hence /plexadmin logs)."""

import asyncio
import logging

import nextcord
from nextcord.ext import commands
import pytest

from plexbot.errors import LoggedModal, LoggedView
from plexbot.bot import PlexBot


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
        bot = PlexBot(command_prefix=commands.when_mentioned, intents=nextcord.Intents.default(), help_command=None)
        try:
            raise RuntimeError("sync blew up")
        except RuntimeError:
            await bot.on_error("on_guild_available")

    with caplog.at_level(logging.ERROR):
        asyncio.run(run())
    record = next(r for r in caplog.records if "on_guild_available" in r.getMessage())
    assert record.exc_info
    assert "sync blew up" in str(record.exc_info[1])


def test_view_errors_are_logged_and_answered_privately(caplog):
    async def run():
        view = LoggedView()
        interaction = FakeInteraction()
        await view.on_error(ValueError("button broke"), nextcord.ui.Button(label="x"), interaction)
        return interaction

    with caplog.at_level(logging.ERROR):
        interaction = asyncio.run(run())
    record = next(r for r in caplog.records if "LoggedView" in r.getMessage())
    assert record.exc_info
    assert "button broke" in str(record.exc_info[1])
    assert len(interaction.response.sent) == 1
    assert interaction.response.sent[0][1] is True


def test_view_error_after_response_uses_followup():
    async def run():
        interaction = FakeInteraction(responded=True)
        await LoggedView().on_error(ValueError("late"), nextcord.ui.Button(label="x"), interaction)
        return interaction

    interaction = asyncio.run(run())
    assert interaction.response.sent == []
    assert len(interaction.followup.sent) == 1
    assert interaction.followup.sent[0][1] is True


def test_modal_errors_are_logged_and_answered_privately(caplog):
    async def run():
        modal = LoggedModal(title="Test")
        interaction = FakeInteraction()
        await modal.on_error(KeyError("form broke"), interaction)
        return interaction

    with caplog.at_level(logging.ERROR):
        interaction = asyncio.run(run())
    record = next(r for r in caplog.records if "LoggedModal" in r.getMessage())
    assert record.exc_info
    assert "form broke" in str(record.exc_info[1])
    assert interaction.response.sent[0][1] is True


def _patch_main_config(monkeypatch):
    from plexbot import bot

    values = {("core", "token"): "token", ("core", "prefix"): "plex ", ("discord", "server_id"): ""}
    monkeypatch.setattr(bot.config, "initialize", lambda: True)
    monkeypatch.setattr(
        bot.config, "get", lambda section, key, default=None: values.get((section, key), default)
    )
    return bot


def test_crash_exits_non_zero_so_supervisors_restart(monkeypatch):
    bot = _patch_main_config(monkeypatch)

    def crash(self, token):
        self.loop.close()  # as the real run() does on its way out
        raise TypeError("gateway handler blew up")

    monkeypatch.setattr(bot.PlexBot, "run", crash)
    with pytest.raises(SystemExit) as exc:
        bot.main()
    assert exc.value.code == 1


def test_ctrl_c_still_exits_cleanly(monkeypatch):
    bot = _patch_main_config(monkeypatch)

    def interrupt(self, token):
        self.loop.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(bot.PlexBot, "run", interrupt)
    bot.main()  # no SystemExit


@pytest.mark.parametrize("legacy", [None, True])
def test_message_content_intent_only_for_legacy_text_commands(monkeypatch, legacy):
    from types import SimpleNamespace

    bot = _patch_main_config(monkeypatch)
    get = bot.config.get
    monkeypatch.setattr(bot.config, "get", lambda section, key, default=None: (
        legacy if (section, key) == ("core", "legacy_text_commands") else get(section, key, default)))
    seen = {}
    monkeypatch.setattr(bot.PlexBot, "run", lambda self, token: seen.update(bot=self))
    bot.main()
    instance = seen["bot"]
    instance.loop.close()
    assert instance.intents.members
    assert instance.intents.message_content is bool(legacy)
    prefixes = instance.command_prefix(SimpleNamespace(user=SimpleNamespace(id=1)), None)
    assert "<@1> " in prefixes  # "@PlexBot top" is answered either way
    assert ("plex " in prefixes) is bool(legacy)


def test_view_error_card_is_the_oops_state(monkeypatch):
    from plexbot.ui import embeds

    monkeypatch.setattr(embeds, "mascot", lambda slot: None)
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
    from plexbot.ui import embeds
    from plexbot.errors import ErrorHandler

    monkeypatch.setattr(embeds, "mascot", lambda slot: None)
    sent = {}

    class Response(FakeResponse):
        async def send_message(self, content=None, *, ephemeral=False, **kwargs):
            sent.update(kwargs, ephemeral=ephemeral)

    async def run():
        bot = PlexBot(command_prefix=commands.when_mentioned, intents=nextcord.Intents.default(), help_command=None)
        interaction = FakeInteraction()
        interaction.response = Response()
        interaction.data = {"name": "plex", "options": [{"type": 1, "name": "status"}]}
        error = nextcord.ApplicationInvokeError(RuntimeError("boom"))
        await ErrorHandler(bot).application_error_handler(interaction, error)

    asyncio.run(run())
    assert sent["ephemeral"] is True
    assert "`/plex status`" in sent["embeds"][0].description


def test_slash_error_after_public_defer_replaces_the_placeholder(monkeypatch):
    from plexbot.ui import embeds
    from plexbot.errors import ErrorHandler

    monkeypatch.setattr(embeds, "mascot", lambda slot: None)
    deleted = []

    async def delete_original_message():
        deleted.append(True)

    async def run():
        bot = PlexBot(command_prefix=commands.when_mentioned, intents=nextcord.Intents.default(), help_command=None)
        interaction = FakeInteraction(responded=True)
        interaction.delete_original_message = delete_original_message
        interaction.data = {"name": "plex", "options": [{"type": 1, "name": "status"}]}
        error = nextcord.ApplicationInvokeError(RuntimeError("boom"))
        await ErrorHandler(bot).application_error_handler(interaction, error)
        return interaction

    interaction = asyncio.run(run())
    assert deleted == [True]
    assert len(interaction.followup.sent) == 1
    assert interaction.followup.sent[0][1] is True


def test_a_sigterm_shutdown_closes_the_sessions_before_run_cleans_up(monkeypatch):
    """docker stop: nextcord's SIGTERM handler runs close() as a task, and run() cancels every task 0.1 s
    after start() returns. close() must finish first, or the HTTP sessions log "Unclosed client session"."""
    closed = []

    class Session:
        async def close(self):
            closed.append("tautulli")

    async def setup_hook(self):
        self.shared_resources = {"tautulli": Session()}

    async def gateway(self, *args, **kwargs):
        while not self.is_closed():  # the gateway loop ends as soon as close() starts
            await asyncio.sleep(0)

    async def disconnect(self):
        self._closed = True
        await asyncio.sleep(0.05)  # closing the websocket and Discord's HTTP session takes a moment

    monkeypatch.setattr(PlexBot, "setup_hook", setup_hook)
    monkeypatch.setattr(commands.Bot, "start", gateway)
    monkeypatch.setattr(commands.Bot, "close", disconnect)

    async def run():
        bot = PlexBot(command_prefix=commands.when_mentioned, intents=nextcord.Intents.default(), help_command=None)
        running = asyncio.ensure_future(bot.start("token"))
        await asyncio.sleep(0)
        asyncio.get_running_loop().create_task(bot.close())  # what nextcord's SIGTERM handler does
        await running
        return list(closed)  # what's closed by the time run() starts cancelling tasks

    assert asyncio.run(run()) == ["tautulli"]


class _EditedMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


def test_timeout_keeps_link_buttons():
    async def run():
        trailer = nextcord.ui.Button(label="Trailer", url="https://www.youtube.com/watch?v=abc")
        view = LoggedView()
        view.add_item(nextcord.ui.Button(label="Roll again"))
        view.add_item(trailer)
        view.message = _EditedMessage()
        plain = LoggedView()
        plain.add_item(nextcord.ui.Button(label="Refresh"))
        plain.message = _EditedMessage()
        await view.on_timeout()
        await plain.on_timeout()
        return trailer, view.message.edits, plain.message.edits

    trailer, edits, plain_edits = asyncio.run(run())
    kept = edits[0]["view"]
    assert kept.children == [trailer] and kept.is_finished()
    assert plain_edits == [{"view": None}]
