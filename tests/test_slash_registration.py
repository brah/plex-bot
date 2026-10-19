"""The /plex and /plexadmin groups must survive nextcord's gateway READY handling.

On READY, nextcord clears its whole application-command state, then on connect it
re-adds only commands decorated on the bot or defined in a cog. Anything registered
any other way is dropped locally and then deleted from Discord by the next sync.
"""

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import nextcord
from nextcord.ext import commands

from plexbot.bot import PlexBot, cog_names, install_slash_groups
from plexbot.cogs.server_commands import ServerCommands


def _local_command_names(bot):
    return {cmd.name for cmd in bot._connection._application_commands}


def _build_bot():
    bot = PlexBot(
        command_prefix=commands.when_mentioned,
        intents=nextcord.Intents.default(),
        help_command=None,
        default_guild_ids=[123],
    )
    bot.shared_resources = {"tautulli": SimpleNamespace()}
    install_slash_groups(bot)  # before the cogs, as in PlexBot.setup_hook
    cog = ServerCommands(bot)
    cog.status_task.cancel()
    bot.add_cog(cog)
    return bot


def test_groups_registered_for_server():
    async def run():
        bot = _build_bot()
        assert {"plex", "plexadmin"} <= _local_command_names(bot)
        for cmd in bot._connection._application_commands:
            if cmd.name in ("plex", "plexadmin"):
                assert cmd.guild_ids_to_rollout == {123}

    asyncio.run(run())


def test_groups_survive_ready_clear_and_reconnect():
    async def run():
        bot = _build_bot()
        for _ in range(2):  # first connect, then a reconnect
            bot._connection.clear(views=False)  # what parse_ready does
            bot.add_all_application_commands()  # what on_connect does
            assert {"plex", "plexadmin"} <= _local_command_names(bot)

        plex = next(c for c in bot._connection._application_commands if c.name == "plex")
        assert {"status", "watchers"} <= set(plex.children)
        # Subcommands stay bound to the cog that defines them.
        assert type(plex.children["status"].parent_cog) is ServerCommands

    asyncio.run(run())



def test_cog_names_import_as_extensions():
    """load_cogs logs a cog that fails to load instead of raising, so a wrong name would go unnoticed."""
    names = cog_names()
    cogs_dir = Path(__file__).resolve().parent.parent / "plexbot" / "cogs"
    assert len(names) == len(list(cogs_dir.glob("*.py")))
    for name in names:
        assert callable(importlib.import_module(name).setup), name
