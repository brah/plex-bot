"""/plex help: entries from command metadata, categories, admin hiding."""

import asyncio
from types import SimpleNamespace

import nextcord
from nextcord.ext import commands

import cogs.utility_commands as help_module
from cogs.utility_commands import (
    HelpEntry, collect_help_entries, help_category_page, help_overview, visible_entries,
)
from fakes import FakeTautulli, build_bot

ENTRIES = [
    HelpEntry("/plex watchers [stop] [message]", "See who's watching", "watching", "/plex watchers"),
    HelpEntry("/plex status", "Server health", "watching"),
    HelpEntry("plex movie <movie_name>", "Look up a movie", "library"),
    HelpEntry("/plexadmin logs [level] [search]", "Bot logs", "admin"),
]


def _build_with_prefix_cog():
    class Library(commands.Cog):
        help_category = "library"

        @commands.command()
        async def movie(self, ctx, *, movie_name: str):
            """Lookup a movie in your Plex library.

            More detail that should not appear."""

        @commands.command(hidden=True)
        async def secret(self, ctx):
            """Hidden."""

        @commands.command()
        @commands.has_permissions(administrator=True)
        async def refresh(self, ctx):
            """Refresh the cache."""

    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        bot.add_cog(Library())
        bot.add_cog(help_module.UtilityCommands(bot))
        return collect_help_entries(bot, "plex ")

    return asyncio.run(run())


def test_entries_come_from_slash_and_prefix_metadata():
    entries = {e.usage: e for e in _build_with_prefix_cog()}
    assert entries["/plex watchers [stop] [message]"].category == "watching"
    assert entries["/plex watchers [stop] [message]"].description.startswith("See who's watching")
    assert entries["/plexadmin logs [level] [search]"].category == "admin"
    movie = entries["plex movie <movie_name>"]
    assert movie.category == "library" and movie.description == "Lookup a movie in your Plex library."
    assert not any(usage.startswith("plex secret") for usage in entries)
    assert not any(usage.startswith("/plex help") for usage in entries)  # help doesn't list itself


def test_admin_only_prefix_command_in_a_public_cog_is_admin():
    entries = _build_with_prefix_cog()
    refresh = next(e for e in entries if e.usage.startswith("plex refresh"))
    assert refresh.category == "admin"
    assert next(e for e in entries if e.usage.startswith("plex movie")).category == "library"
    assert refresh not in visible_entries(entries, is_admin=False)
    assert refresh in visible_entries(entries, is_admin=True)


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

    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        utility = help_module.UtilityCommands(bot)
        bot.add_cog(utility)
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), response=Response())
        await help_module.UtilityCommands.help.callback(utility, interaction)

    asyncio.run(run())
    assert sent["ephemeral"] is True
    assert "Admin" not in sent["embeds"][0].description
    select = next(c for c in sent["view"].children if isinstance(c, nextcord.ui.StringSelect))
    assert "admin" not in [o.value for o in select.options]
