"""/plex help: entries from command metadata, categories, admin hiding."""

import asyncio
from types import SimpleNamespace

import nextcord

import cogs.utility_commands as help_module
from cogs.utility_commands import (
    HelpEntry, collect_help_entries, help_category_page, help_overview, visible_entries,
)
from fakes import FakeTautulli, build_bot
from ui.images import Attachment

ENTRIES = [
    HelpEntry("/plex watchers [stop] [message]", "See who's watching", "watching", "/plex watchers"),
    HelpEntry("/plex status", "Server health", "watching"),
    HelpEntry("/plex movie <title>", "Look up a movie", "library"),
    HelpEntry("/plexadmin logs [level] [search]", "Bot logs", "admin"),
]


def test_entries_come_from_slash_command_metadata():
    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        bot.add_cog(help_module.UtilityCommands(bot))
        return collect_help_entries(bot)

    entries = {e.usage: e for e in asyncio.run(run())}
    assert entries["/plex watchers [stop] [message]"].category == "watching"
    assert entries["/plex watchers [stop] [message]"].description.startswith("See who's watching")
    assert entries["/plexadmin logs [level] [search]"].category == "admin"
    assert not any(usage.startswith("/plex help") for usage in entries)  # help doesn't list itself


def test_admin_hidden_from_non_admins():
    assert [e.category for e in visible_entries(ENTRIES, is_admin=False)] == [
        "watching", "watching", "library"
    ]
    assert len(visible_entries(ENTRIES, is_admin=True)) == 4


def test_overview_lists_categories_with_counts(monkeypatch):
    monkeypatch.setattr(help_module, "mascot", lambda slot: None)
    embed = help_overview(visible_entries(ENTRIES, is_admin=False)).embeds[0]
    assert "**Watching**" in embed.description and "2 commands" in embed.description
    assert "**Library**" in embed.description and "1 command" in embed.description
    assert "Admin" not in embed.description and "Charts" not in embed.description


def test_category_page(monkeypatch):
    embed = help_category_page(ENTRIES, "watching").embeds[0]
    assert "Watching" in embed.title
    assert "`/plex watchers [stop] [message]`" in embed.description
    assert "-# e.g. `/plex watchers`" in embed.description
    assert "plex movie" not in embed.description


def test_help_command_without_guild_permissions_hides_admin(monkeypatch):
    monkeypatch.setattr(help_module, "mascot", lambda slot: None)
    sent = {}

    class Response:
        def is_done(self):
            return False

        async def send_message(self, **kwargs):
            sent.update(kwargs)
            return "private handle"

    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        utility = help_module.UtilityCommands(bot)
        bot.add_cog(utility)
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), guild_id=123, response=Response())
        await help_module.UtilityCommands.help.callback(utility, interaction)

    asyncio.run(run())
    assert sent["ephemeral"] is True
    assert "Admin" not in sent["embeds"][0].description
    select = next(c for c in sent["view"].children if isinstance(c, nextcord.ui.StringSelect))
    assert "admin" not in [o.value for o in select.options]
    assert sent["view"].message == "private handle"  # what removes the menu when it expires


def test_an_expired_help_menu_loses_its_controls_through_the_latest_token():
    edits = []

    class Interaction(SimpleNamespace):
        async def edit_original_message(self, **kwargs):
            edits.append((self.name, kwargs["view"]))

    class Response:
        async def edit_message(self, **kwargs):
            pass

    async def run():
        view = help_module.HelpView(ENTRIES)
        view.message = nextcord.PartialInteractionMessage(Interaction(name="command"))
        await view.on_timeout()
        select = view.children[0]
        select._selected_values = ["watching"]
        await select.callback(Interaction(name="click", response=Response()))
        await view.on_timeout()

    asyncio.run(run())
    assert edits == [("command", None), ("click", None)]


def _choose(entries, value):
    """Run the dropdown's callback with `value` chosen; returns (view, kwargs passed to edit_message)."""
    edited = {}

    class Response:
        async def edit_message(self, **kwargs):
            edited.update(kwargs)

    async def run():
        view = help_module.HelpView(entries)
        select = view.children[0]
        select._selected_values = [value]
        await select.callback(SimpleNamespace(response=Response()))
        return view

    return asyncio.run(run()), edited


def test_choosing_a_category_edits_to_its_page():
    view, edited = _choose(ENTRIES, "watching")
    assert "Watching" in edited["embeds"][0].title
    assert "plex movie" not in edited["embeds"][0].description
    assert edited["view"] is view
    assert edited["attachments"] == []
    assert "files" not in edited


def test_choosing_overview_returns_to_the_overview_with_its_mascot(monkeypatch):
    art = Attachment("mascot-wave.png", b"png")
    monkeypatch.setattr(help_module, "mascot", lambda slot: art)
    view, edited = _choose(ENTRIES, "overview")
    assert "**Watching**" in edited["embeds"][0].description
    assert [f.filename for f in edited["files"]] == ["mascot-wave.png"]
    assert edited["view"] is view


def test_admins_get_the_admin_category(monkeypatch):
    from config import config

    monkeypatch.setattr(help_module, "mascot", lambda slot: None)
    monkeypatch.setattr(config, "_user_config", {"discord": {"server_id": "123"}})  # admin of this server
    sent = {}

    class Response:
        def is_done(self):
            return False

        async def send_message(self, **kwargs):
            sent.update(kwargs)

    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        utility = help_module.UtilityCommands(bot)
        bot.add_cog(utility)
        user = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(administrator=True))
        interaction = SimpleNamespace(user=user, guild_id=123, response=Response())
        await help_module.UtilityCommands.help.callback(utility, interaction)

    asyncio.run(run())
    assert "Admin" in sent["embeds"][0].description
    select = next(c for c in sent["view"].children if isinstance(c, nextcord.ui.StringSelect))
    assert "admin" in [o.value for o in select.options]
