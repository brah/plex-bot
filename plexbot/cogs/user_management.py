"""/plexadmin users, link, unlink and ignore: who's who between Plex and Discord.

Links let the bot show Plex viewers by their Discord name and avatar, give top-watcher roles and
run /plex recommend; ignored Plex users are left out of stats, charts and lists.
"""

import logging
from typing import Optional

import nextcord
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from plexbot.plex_users import PlexUsers
from plexbot.slash_commands import is_admin, plexadmin
from plexbot.ui.embeds import ErrorKind, Reply, card, error_state, service_down, success
from plexbot.ui.format import join, plural, truncate
from plexbot.ui.theme import Colour, Limits
from plexbot.utilities import MappingsUnavailable, UserMappings

logger = logging.getLogger("plexbot.user_management")

_UNREADABLE = (
    "map.json couldn't be read, so nothing can be changed until it's fixed (saving would wipe it)."
)


def _name(row: dict) -> str:
    """"Alice (alice)", or just "alice" when the Plex name is the username."""
    username, friendly = row["username"], row.get("friendly_name")
    return f"{friendly} ({username})" if friendly and friendly != username else username


def users_card(users: list, mappings: list, *, unreadable: bool) -> nextcord.Embed:
    """Every Plex user with their link and ignore status, plus map.json entries Plex doesn't know."""
    by_plex = {m["plex_username"]: m for m in mappings}
    known = {row["username"] for row in users}
    lines = []
    for row in sorted(users, key=lambda r: _name(r).lower()):
        entry = by_plex.get(row["username"], {})
        status = join([
            f"→ <@{entry['discord_id']}>" if entry.get("discord_id") else "not linked",
            "🙈 ignored" if entry.get("ignore") else None,
        ])
        lines.append(f"**{escape_markdown(_name(row))}** · {status}")
    linked = sum(1 for row in users if by_plex.get(row["username"], {}).get("discord_id"))
    ignored = sum(1 for row in users if by_plex.get(row["username"], {}).get("ignore"))
    header = join([f"**{plural(len(users), 'Plex user')}**", f"{linked:,} linked", f"{ignored:,} ignored"])
    stale = sorted(m["plex_username"] for m in mappings if m["plex_username"] not in known)
    blocks = ([f"⚠️ {_UNREADABLE}"] if unreadable else []) + [header]

    body, used = [], sum(len(b) for b in blocks) + 200
    for index, line in enumerate(lines):
        if used + len(line) + 1 > Limits.DESCRIPTION - 300:
            body.append(f"…and {plural(len(lines) - index, 'more')}")
            break
        body.append(line)
        used += len(line) + 1
    blocks.append("\n".join(body) or "Tautulli doesn't know any Plex users yet.")
    if stale:
        blocks.append("-# In map.json but not on the Plex server: "
                      + escape_markdown(truncate(", ".join(stale), 300)))
    embed = card(title="👥 Plex users", description="\n\n".join(blocks), colour=Colour.NEUTRAL)
    embed.set_footer(text="Change with /plexadmin link, /plexadmin unlink and /plexadmin ignore")
    return embed


def link_message(result, plex_label: str) -> str:
    lines = [f"Linked **{escape_markdown(plex_label)}** to <@{result.discord_id}>."]
    if result.replaced_discord_id:
        lines.append(f"-# It was linked to <@{result.replaced_discord_id}> before.")
    if result.moved_from_plex:
        lines.append(f"-# <@{result.discord_id}> was linked to **{escape_markdown(result.moved_from_plex)}** "
                     "before; that link was removed.")
    return "\n".join(lines)


def _save_failed(error: Exception) -> Reply:
    if isinstance(error, MappingsUnavailable):
        return error_state(ErrorKind.BUG, _UNREADABLE,
                           hint="Fix or restore map.json (a map.json.bak may help), then try again.")
    return error_state(ErrorKind.BUG, f"Couldn't save map.json: {error}",
                       hint="Check the bot can write map.json. Nothing was changed.")


class UserManagement(commands.Cog):
    """Admin tools for Plex <-> Discord links and ignored users (all under /plexadmin)."""

    def __init__(self, bot):
        self.bot = bot
        self.users = PlexUsers(bot, bot.shared_resources.get("tautulli"))

    async def _plex_user(self, interaction: nextcord.Interaction, wanted: str) -> Optional[dict]:
        """The Tautulli user for a plex_user option, or None after sending the private error."""
        users = await self.users.everyone()
        if users is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return None
        wanted = wanted.strip()
        row = self.users.match(users, wanted)  # everyone(): ignored users too, so they can be un-ignored
        if row is None and UserMappings.get_mapping_by_plex_username(wanted):
            row = {"username": wanted}  # in map.json only (left the server): still manageable
        if row is None:
            await error_state(ErrorKind.INPUT, f"No Plex user called **{escape_markdown(truncate(wanted, 80))}**.",
                              hint="Pick one from the suggestions as you type.").send(interaction, ephemeral=True)
        return row

    def _status(self, entry: dict) -> Optional[str]:
        """"linked to Alice · ignored", for an autocomplete label."""
        linked = None
        discord_id = entry.get("discord_id") or ""
        if discord_id:
            member = self.bot.get_user(int(discord_id)) if discord_id.isdigit() else None
            linked = f"linked to {member.display_name}" if member else "linked"
        return join([linked, "ignored" if entry.get("ignore") else None]) or None

    async def _choices(self, typed: str, *, linked_only: bool = False) -> dict:
        typed = (typed or "").strip().lower()
        by_plex = {m["plex_username"]: m for m in UserMappings.load_user_mappings()}
        choices = {}
        for row in await self.users.everyone() or []:
            entry = by_plex.get(row["username"], {})
            if linked_only and not entry.get("discord_id"):
                continue
            label = _name(row)
            if typed and typed not in label.lower():
                continue
            choices.setdefault(truncate(join([label, self._status(entry)], " · "), Limits.SELECT_LABEL),
                               row["username"])
        return dict(sorted(choices.items(), key=lambda kv: kv[0].lower())[:Limits.SELECT_OPTIONS])

    # --- /plexadmin users ----------------------------------------------------------

    @plexadmin.subcommand(name="users", description="Every Plex user, with their Discord link and ignore status")
    async def users_list(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)
        users = await self.users.everyone()
        if users is None:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        embed = users_card(users, UserMappings.load_user_mappings(), unreadable=UserMappings.is_unreadable())
        await Reply([embed]).send(interaction, ephemeral=True)

    # --- /plexadmin link and unlink ---------------------------------------------------

    @plexadmin.subcommand(description="Link a Plex user to a Discord member (their name, avatar and roles)")
    async def link(
        self,
        interaction: nextcord.Interaction,
        plex_user: str = nextcord.SlashOption(description="The Plex user", autocomplete=True, max_length=100),
        member: nextcord.Member = nextcord.SlashOption(description="Their Discord account"),
    ):
        await interaction.response.defer(ephemeral=True)
        row = await self._plex_user(interaction, plex_user)
        if row is None:
            return
        try:
            result = UserMappings.link(row["username"], member.id)
        except (MappingsUnavailable, OSError) as error:
            logger.error(f"Couldn't link {row['username']} to {member}: {error}")
            await _save_failed(error).send(interaction, ephemeral=True)
            return
        logger.info(f"{interaction.user} linked Plex user {row['username']} to {member} ({member.id})")
        await success(link_message(result, _name(row))).send(interaction, ephemeral=True)

    @plexadmin.subcommand(description="Remove a Plex user's link to Discord")
    async def unlink(
        self,
        interaction: nextcord.Interaction,
        plex_user: str = nextcord.SlashOption(description="The Plex user", autocomplete=True, max_length=100),
    ):
        await interaction.response.defer(ephemeral=True)
        wanted = plex_user.strip()
        entry = UserMappings.get_mapping_by_plex_username(wanted)
        if not entry or not entry.get("discord_id"):
            await error_state(ErrorKind.INPUT, f"**{escape_markdown(truncate(wanted, 80))}** isn't linked to anyone.",
                              hint="/plexadmin users shows every link.").send(interaction, ephemeral=True)
            return
        try:
            previous = UserMappings.unlink(wanted)
        except (MappingsUnavailable, OSError) as error:
            logger.error(f"Couldn't unlink {wanted}: {error}")
            await _save_failed(error).send(interaction, ephemeral=True)
            return
        logger.info(f"{interaction.user} unlinked Plex user {wanted} (was {previous})")
        await success(f"Unlinked **{escape_markdown(wanted)}** from <@{previous}>.").send(interaction, ephemeral=True)

    # --- /plexadmin ignore -------------------------------------------------------------

    @plexadmin.subcommand(description="Leave a Plex user out of stats, charts and lists (or bring them back)")
    async def ignore(
        self,
        interaction: nextcord.Interaction,
        plex_user: str = nextcord.SlashOption(description="The Plex user", autocomplete=True, max_length=100),
    ):
        await interaction.response.defer(ephemeral=True)
        row = await self._plex_user(interaction, plex_user)
        if row is None:
            return
        try:
            ignored = UserMappings.toggle_ignore(row["username"])
        except (MappingsUnavailable, OSError) as error:
            logger.error(f"Couldn't toggle ignore for {row['username']}: {error}")
            await _save_failed(error).send(interaction, ephemeral=True)
            return
        logger.info(f"{interaction.user} {'ignored' if ignored else 'unignored'} Plex user {row['username']}")
        name = escape_markdown(_name(row))
        message = (f"**{name}** is now ignored: left out of stats, charts and lists." if ignored
                   else f"**{name}** is no longer ignored.")
        await success(message).send(interaction, ephemeral=True)

    @link.on_autocomplete("plex_user")
    @ignore.on_autocomplete("plex_user")
    async def _plex_user_autocomplete(self, interaction: nextcord.Interaction, plex_user: str):
        await self._autocomplete(interaction, plex_user)

    @unlink.on_autocomplete("plex_user")
    async def _linked_user_autocomplete(self, interaction: nextcord.Interaction, plex_user: str):
        await self._autocomplete(interaction, plex_user, linked_only=True)

    async def _autocomplete(self, interaction: nextcord.Interaction, typed: str, *, linked_only=False):
        # Command checks don't run for autocomplete, so the user list needs its own gate.
        choices = await self._choices(typed, linked_only=linked_only) if is_admin(
            interaction.user, interaction.guild_id) else {}
        await interaction.response.send_autocomplete(choices)


def setup(bot):
    bot.add_cog(UserManagement(bot))
