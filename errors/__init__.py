# errors/__init__.py

import logging
from typing import Dict, Iterable, Optional

import aiohttp
import nextcord
from nextcord.ext import commands

from slash_commands import command_paths

logger = logging.getLogger("plexbot.errors")

# Raised talking to Tautulli, qBittorrent or plex.tv: the user is told to try again shortly.
_SERVICE_ERRORS = (aiohttp.ClientError, TimeoutError, ConnectionError)


# Prefix commands that became a slash command under a different name.
SLASH_RENAMES: Dict[str, str] = {
    "killstream": "watchers",
    "mapdiscord": "link",
    "refresh_cache": "cache",
    "force_refresh_cache": "cache",
    "check_media_cache": "cache",
}


def slash_equivalent(invoked: Optional[str], slash_paths: Iterable[str]) -> Optional[str]:
    """Return the slash command replacing a removed prefix command, e.g. "status" -> "/plex status".

    Matches the first word under each group, so a nested group like "/plex chart hours"
    answers for "chart" with "/plex chart".
    """
    if not invoked:
        return None
    name = invoked.lower()
    name = SLASH_RENAMES.get(name, name)
    for path in slash_paths:
        parts = path.lstrip("/").split()
        head = parts[:2] if len(parts) > 1 else parts
        if head[-1] == name:
            return "/" + " ".join(head)
    return None


def invoked_command_path(interaction: nextcord.Interaction) -> str:
    """The full invoked slash command, e.g. "/plex status", from the interaction payload."""
    data = interaction.data or {}
    parts = [data.get("name", "command")]
    options = data.get("options", [])
    # Subcommand (type 1) and subcommand-group (type 2) options carry the nested name.
    while options and options[0].get("type") in (1, 2):
        parts.append(options[0]["name"])
        options = options[0].get("options", [])
    return "/" + " ".join(parts)


class ErrorHandler:
    """Answers failed commands: old prefix commands point at their slash command, slash errors are
    logged and answered privately."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def slash_command_paths(self) -> list:
        return [path for path, _ in command_paths(self.bot)]

    async def command_error_handler(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Every prefix command is gone; a message that names one gets pointed at its replacement."""
        if not isinstance(error, commands.CommandNotFound):
            logger.error(f"Prefix command error: {error}", exc_info=error)
            return
        replacement = slash_equivalent(ctx.invoked_with, self.slash_command_paths())
        if replacement:
            await ctx.send(
                f"`{ctx.prefix}{ctx.invoked_with}` is now `{replacement}` — type `/plex` to see all commands."
            )

    async def application_error_handler(
        self, interaction: nextcord.Interaction, error: nextcord.ApplicationError
    ) -> None:
        """Log a failed slash command and tell the user privately, instead of a silent timeout."""
        from ui.embeds import ErrorKind, error_state, unexpected_error

        if isinstance(error, nextcord.ApplicationInvokeError):
            error = error.original
        path = invoked_command_path(interaction)
        if isinstance(error, nextcord.ApplicationCheckFailure):
            logger.warning(f"{interaction.user} ({interaction.user.id}) was refused {path}")
            reply = error_state(
                ErrorKind.PERMISSION, f"Only bot admins can use `{path}`.",
                hint="An admin can add your Discord ID to discord.admin_ids in config.json.",
            )
        elif isinstance(error, _SERVICE_ERRORS):
            logger.error(f"{path} couldn't reach a service: {error}", exc_info=error)
            reply = error_state(
                ErrorKind.SERVICE_DOWN, f"Couldn't reach a service `{path}` depends on.",
                hint="Try again in a minute.",
            )
        else:
            logger.error(f"{path} failed: {error}", exc_info=error)
            reply = unexpected_error(path)
        try:
            await reply.send(interaction, ephemeral=True, replace_placeholder=True)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not send error reply for {path}: {e}")

    def setup(self) -> None:
        """Set up the error handler with the bot."""

        @self.bot.event
        async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
            await self.command_error_handler(ctx, error)

        @self.bot.event
        async def on_application_command_error(
            interaction: nextcord.Interaction, error: nextcord.ApplicationError
        ) -> None:
            await self.application_error_handler(interaction, error)


async def _reply_privately(interaction: nextcord.Interaction, reply) -> None:
    try:
        await reply.send(interaction, ephemeral=True)
    except nextcord.HTTPException as e:
        logger.warning(f"Could not send error reply: {e}")


class LoggedView(nextcord.ui.View):
    """View whose callback errors are logged and answered privately, and whose controls are
    removed from `message` when it times out (rather than left greyed out).

    nextcord's default prints callback errors to stderr, so they never reach plexbot.log and the
    user just sees "This interaction failed".
    """

    # The message the controls sit under, once sent: ideally a bot-token handle (bot_token_handle),
    # since the command's interaction webhook expires after 15 minutes.
    message = None

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except nextcord.HTTPException:
                pass

    async def on_error(self, error: Exception, item: nextcord.ui.Item, interaction: nextcord.Interaction):
        from ui.embeds import unexpected_error

        logger.error(f"Error in {type(self).__name__} ({type(item).__name__}): {error}", exc_info=error)
        await _reply_privately(interaction, unexpected_error())


class LoggedModal(nextcord.ui.Modal):
    """Modal whose submit errors are logged and answered privately (see LoggedView)."""

    async def on_error(self, error: Exception, interaction: nextcord.Interaction):
        from ui.embeds import unexpected_error

        logger.error(f"Error in {type(self).__name__}: {error}", exc_info=error)
        await _reply_privately(interaction, unexpected_error())
