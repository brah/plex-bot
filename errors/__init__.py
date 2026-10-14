# errors/__init__.py

import logging
from enum import Enum
from typing import Dict, Iterable, Optional

import nextcord
from nextcord.ext import commands

from slash_commands import command_paths

# Configure logging
logger = logging.getLogger("plexbot.errors")
logger.setLevel(logging.INFO)


class ErrorCategory(Enum):
    """Categories of errors for standardized handling."""

    COMMAND_NOT_FOUND = "command_not_found"
    MISSING_PERMISSIONS = "missing_permissions"
    BOT_MISSING_PERMISSIONS = "bot_missing_permissions"
    INVALID_ARGUMENT = "invalid_argument"
    MISSING_ARGUMENT = "missing_argument"
    API_ERROR = "api_error"
    NETWORK_ERROR = "network_error"
    DATA_ERROR = "data_error"
    USER_ERROR = "user_error"
    CONFIG_ERROR = "config_error"
    INTERNAL_ERROR = "internal_error"
    UNCATEGORIZED = "uncategorized"


class ErrorStyle:
    """Styling information for an error response."""

    def __init__(
        self,
        title: str,
        color: int = 0xFF5555,
        emoji: str = "❌",
        show_help: bool = False,
        log_level: int = logging.ERROR,
    ):
        self.title = title
        self.color = color
        self.emoji = emoji
        self.show_help = show_help
        self.log_level = log_level


# Define standard error styles for different categories
ERROR_STYLES: Dict[ErrorCategory, ErrorStyle] = {
    ErrorCategory.COMMAND_NOT_FOUND: ErrorStyle(
        "Command Not Found", color=0xFFA500, emoji="❓", log_level=logging.INFO
    ),
    ErrorCategory.MISSING_PERMISSIONS: ErrorStyle(
        "Permission Denied", color=0xFF5555, emoji="🔒", log_level=logging.WARNING
    ),
    ErrorCategory.BOT_MISSING_PERMISSIONS: ErrorStyle(
        "Bot Permissions Error", color=0xFF5555, emoji="🤖", log_level=logging.WARNING
    ),
    ErrorCategory.INVALID_ARGUMENT: ErrorStyle(
        "Invalid Argument", color=0xFFA500, emoji="⚠️", show_help=True, log_level=logging.INFO
    ),
    ErrorCategory.MISSING_ARGUMENT: ErrorStyle(
        "Missing Argument", color=0xFFA500, emoji="⚠️", show_help=True, log_level=logging.INFO
    ),
    ErrorCategory.API_ERROR: ErrorStyle("API Error", color=0xFF5555, emoji="🌐", log_level=logging.ERROR),
    ErrorCategory.NETWORK_ERROR: ErrorStyle(
        "Network Error", color=0xFF5555, emoji="📡", log_level=logging.ERROR
    ),
    ErrorCategory.DATA_ERROR: ErrorStyle("Data Error", color=0xFF5555, emoji="📊", log_level=logging.ERROR),
    ErrorCategory.USER_ERROR: ErrorStyle("User Error", color=0xFFA500, emoji="👤", log_level=logging.INFO),
    ErrorCategory.CONFIG_ERROR: ErrorStyle(
        "Configuration Error", color=0xFF5555, emoji="⚙️", log_level=logging.ERROR
    ),
    ErrorCategory.INTERNAL_ERROR: ErrorStyle(
        "Internal Error", color=0xFF0000, emoji="⚠️", log_level=logging.ERROR
    ),
    ErrorCategory.UNCATEGORIZED: ErrorStyle("Error", color=0xFF5555, emoji="❌", log_level=logging.ERROR),
}


# Prefix commands that became a slash command under a different name.
SLASH_RENAMES: Dict[str, str] = {"killstream": "watchers"}


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
    """Centralized error handling for PlexBot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def categorize_error(self, error: Exception) -> ErrorCategory:
        """Categorize an error to determine the appropriate handling style."""
        if isinstance(error, commands.CommandNotFound):
            return ErrorCategory.COMMAND_NOT_FOUND
        elif isinstance(error, commands.MissingPermissions):
            return ErrorCategory.MISSING_PERMISSIONS
        elif isinstance(error, commands.BotMissingPermissions):
            return ErrorCategory.BOT_MISSING_PERMISSIONS
        elif isinstance(error, commands.BadArgument):
            return ErrorCategory.INVALID_ARGUMENT
        elif isinstance(error, commands.MissingRequiredArgument):
            return ErrorCategory.MISSING_ARGUMENT
        elif isinstance(error, commands.UserInputError):
            return ErrorCategory.USER_ERROR
        elif isinstance(error, APIError):
            return ErrorCategory.API_ERROR
        elif isinstance(error, NetworkError):
            return ErrorCategory.NETWORK_ERROR
        elif isinstance(error, DataError):
            return ErrorCategory.DATA_ERROR
        elif isinstance(error, ConfigError):
            return ErrorCategory.CONFIG_ERROR

        # Check for custom error types related to your application
        error_name = error.__class__.__name__.lower()
        if "api" in error_name or "request" in error_name:
            return ErrorCategory.API_ERROR
        elif "network" in error_name or "connection" in error_name or "timeout" in error_name:
            return ErrorCategory.NETWORK_ERROR
        elif "data" in error_name or "parse" in error_name or "json" in error_name:
            return ErrorCategory.DATA_ERROR
        elif "config" in error_name:
            return ErrorCategory.CONFIG_ERROR

        # Default category for unrecognized errors
        return ErrorCategory.UNCATEGORIZED

    async def handle_error(
        self, ctx: commands.Context, error: Exception, message: Optional[str] = None
    ) -> None:
        """Handle an error that occurred during command execution."""
        # Get the error category and style
        category = self.categorize_error(error)
        style = ERROR_STYLES.get(category, ERROR_STYLES[ErrorCategory.UNCATEGORIZED])

        # Log the error with the appropriate level
        self.log_error(error, category, style)

        # Unknown commands get no reply, except ones that moved to a slash command.
        if category == ErrorCategory.COMMAND_NOT_FOUND:
            replacement = slash_equivalent(ctx.invoked_with, self.slash_command_paths())
            if replacement:
                await ctx.send(
                    f"`{ctx.prefix}{ctx.invoked_with}` is now `{replacement}` — "
                    "type `/plex` to see all commands."
                )
            return

        # Create an appropriate error message
        error_message = message or str(error)

        # Create an embed for the error response
        embed = nextcord.Embed(
            title=f"{style.emoji} {style.title}", description=error_message, color=style.color
        )

        # Add command help if appropriate
        if style.show_help and ctx.command:
            if ctx.command.help:
                embed.add_field(
                    name="Command Help",
                    value=ctx.command.help.split("\n", 1)[0],  # First line of help
                    inline=False,
                )
            embed.add_field(
                name="Usage",
                value=f"`{ctx.prefix}{ctx.command.name} {ctx.command.signature}`",
                inline=False,
            )

        # Send the error response to the user
        await ctx.send(embed=embed)

    def log_error(self, error: Exception, category: ErrorCategory, style: ErrorStyle) -> None:
        """Log an error with the appropriate level of detail based on its category."""
        if style.log_level >= logging.ERROR:
            logger.error(f"{category.value.upper()}: {error}", exc_info=True)
        elif style.log_level >= logging.WARNING:
            logger.warning(f"{category.value.upper()}: {error}")
        else:
            logger.info(f"{category.value.upper()}: {error}")

    async def global_error_handler(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Global error handler for command errors."""
        # Unwrap the original error from CommandInvokeError
        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        await self.handle_error(ctx, error)

    def slash_command_paths(self) -> list:
        return [path for path, _ in command_paths(self.bot)]

    async def application_error_handler(
        self, interaction: nextcord.Interaction, error: nextcord.ApplicationError
    ) -> None:
        """Log a failed slash command and tell the user privately, instead of a silent timeout."""
        if isinstance(error, nextcord.ApplicationInvokeError):
            error = error.original
        category = self.categorize_error(error)
        style = ERROR_STYLES.get(category, ERROR_STYLES[ErrorCategory.UNCATEGORIZED])
        self.log_error(error, category, style)

        from ui.embeds import ErrorKind, error_state, unexpected_error

        path = invoked_command_path(interaction)
        if category in (ErrorCategory.API_ERROR, ErrorCategory.NETWORK_ERROR):
            reply = error_state(
                ErrorKind.SERVICE_DOWN,
                f"Couldn't reach a service `{path}` depends on.",
                hint="Try again in a minute.",
            )
        else:
            reply = unexpected_error(path)
        try:
            await reply.send(interaction, ephemeral=True)
        except nextcord.HTTPException as e:
            logger.warning(f"Could not send error reply for {path}: {e}")

    def setup(self) -> None:
        """Set up the error handler with the bot."""

        @self.bot.event
        async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
            await self.global_error_handler(ctx, error)

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
    """View whose callback errors are logged and answered privately.

    nextcord's default prints them to stderr, so they never reach plexbot.log and the
    user just sees "This interaction failed".
    """

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


# Custom exception classes for better error categorization
class PlexBotError(Exception):
    """Base exception class for PlexBot errors."""

    pass


class APIError(PlexBotError):
    """Error occurred when interacting with an external API."""

    pass


class TautulliAPIError(APIError):
    """Error occurred when interacting with the Tautulli API."""

    pass


class TMDBAPIError(APIError):
    """Error occurred when interacting with the TMDB API."""

    pass


class QBittorrentAPIError(APIError):
    """Error occurred when interacting with the qBittorrent API."""

    pass


class NetworkError(PlexBotError):
    """Error occurred during network operations."""

    pass


class DataError(PlexBotError):
    """Error occurred during data processing."""

    pass


class ConfigError(PlexBotError):
    """Error related to configuration."""

    pass


class UserMappingError(PlexBotError):
    """Error related to user mappings."""

    pass


class CacheError(PlexBotError):
    """Error related to caching."""

    pass


class MediaProcessingError(PlexBotError):
    """Error related to media processing."""

    pass
