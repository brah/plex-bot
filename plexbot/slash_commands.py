"""Top-level slash command groups shared by every cog.

User-facing commands are subcommands of ``/plex`` and admin commands of
``/plexadmin``, so typing ``/plex`` lists the whole bot. Admin commands get their
own group because Discord only applies member permissions to top-level commands:
inside ``/plex`` they couldn't be hidden from non-admins.

Cogs attach subcommands in their class body; nextcord binds each subcommand to
the cog whose class defines it::

    from plexbot.slash_commands import plex

    class MyCog(commands.Cog):
        @plex.subcommand(description="...")
        async def thing(self, interaction: nextcord.Interaction): ...
"""

import nextcord
from nextcord.ext import commands

from plexbot.config import config


def listed(user, ids) -> bool:
    """Whether the user, or one of their roles, is in a list of Discord IDs (ints or digit strings)."""
    wanted = {int(raw) for raw in ids or [] if str(raw).strip().isdigit()}
    return user.id in wanted or any(role.id in wanted for role in getattr(user, "roles", []))


def is_admin(user, guild_id) -> bool:
    """Bot admins: anyone in discord.admin_ids, plus Discord administrators of discord.server_id.

    Administrator of any other server doesn't count: anyone can invite a public bot to their own
    server, so with no server_id set only discord.admin_ids grants admin.
    """
    if listed(user, config.get("discord", "admin_ids")):
        return True
    server_id = str(config.get("discord", "server_id") or "").strip()
    permissions = getattr(user, "guild_permissions", None)
    return bool(server_id) and str(guild_id) == server_id and bool(permissions and permissions.administrator)


def admin_check(interaction: nextcord.Interaction) -> bool:
    """Global check: /plexadmin needs a bot admin. Discord's own permission only hides the command."""
    if (interaction.data or {}).get("name") != "plexadmin":
        return True
    return is_admin(interaction.user, interaction.guild_id)


class SlashGroups(commands.Cog):
    """Owns the /plex and /plexadmin group roots.

    The roots must live in a cog: on gateway READY nextcord clears its application
    command state, and each connect re-adds only commands defined on the bot or in a
    cog. A group registered any other way is dropped, and the following sync then
    deletes it from Discord.
    """

    @nextcord.slash_command(name="plex", description="PlexBot commands")
    async def plex(self, interaction: nextcord.Interaction):
        """Group root; Discord only ever invokes its subcommands."""

    @nextcord.slash_command(
        name="plexadmin",
        description="PlexBot admin commands",
        default_member_permissions=nextcord.Permissions(administrator=True),
        # Guild-only: member permissions don't apply in DMs, so DM use would be open to anyone.
        contexts=[nextcord.InteractionContextType.guild],
    )
    async def plexadmin(self, interaction: nextcord.Interaction):
        """Group root; Discord only ever invokes its subcommands."""


# Shorthands for cogs attaching subcommands: @plex.subcommand(...)
plex = SlashGroups.plex
plexadmin = SlashGroups.plexadmin


def command_paths(bot) -> list:
    """(path, command) for every invocable slash command, e.g. ("/plex status", <subcommand>)."""
    paths = []

    def walk(path: str, command) -> None:
        children = getattr(command, "children", None)
        if children:
            for child in children.values():
                walk(f"{path} {child.name}", child)
        else:
            paths.append((path, command))

    for root in bot.get_all_application_commands():
        walk(f"/{root.name}", root)
    return paths
