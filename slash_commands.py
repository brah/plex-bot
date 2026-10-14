# slash_commands.py

"""Top-level slash command groups shared by every cog.

User-facing commands are subcommands of ``/plex`` and admin commands of
``/plexadmin``, so typing ``/plex`` lists the whole bot. Admin commands get their
own group because Discord only applies member permissions to top-level commands:
inside ``/plex`` they couldn't be hidden from non-admins.

Cogs attach subcommands in their class body; nextcord binds each subcommand to
the cog whose class defines it::

    from slash_commands import plex

    class MyCog(commands.Cog):
        @plex.subcommand(description="...")
        async def thing(self, interaction: nextcord.Interaction): ...
"""

import nextcord


@nextcord.slash_command(name="plex", description="PlexBot commands")
async def plex(interaction: nextcord.Interaction):
    """Group root; Discord only ever invokes its subcommands."""


@nextcord.slash_command(
    name="plexadmin",
    description="PlexBot admin commands",
    default_member_permissions=nextcord.Permissions(administrator=True),
    # Guild-only: member permissions don't apply in DMs, so DM use would be open to anyone.
    contexts=[nextcord.InteractionContextType.guild],
)
async def plexadmin(interaction: nextcord.Interaction):
    """Group root; Discord only ever invokes its subcommands."""


GROUPS = (plex, plexadmin)


def register_groups(bot) -> None:
    """Register the groups with the bot.

    Call after the cogs are loaded: each cog binds its subcommands when it is
    instantiated, and a group with no subcommands isn't registered at all.
    """
    for group in GROUPS:
        if group.children:
            group.from_callback(group.callback, call_children=False)
            bot.add_application_command(group, use_rollout=True)


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
