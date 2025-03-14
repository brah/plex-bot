# errors/__init__.py

import logging
import traceback
import sys
from enum import Enum
from typing import Dict, Optional, Any, Union, Callable, TypeVar, Awaitable

import nextcord
from nextcord.ext import commands

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
        log_level: int = logging.ERROR
    ):
        self.title = title
        self.color = color
        self.emoji = emoji
        self.show_help = show_help
        self.log_level = log_level


# Define standard error styles for different categories
ERROR_STYLES: Dict[ErrorCategory, ErrorStyle] = {
    ErrorCategory.COMMAND_NOT_FOUND: ErrorStyle(
        "Command Not Found", 
        color=0xFFA500, 
        emoji="❓", 
        log_level=logging.INFO
    ),
    ErrorCategory.MISSING_PERMISSIONS: ErrorStyle(
        "Permission Denied", 
        color=0xFF5555, 
        emoji="🔒", 
        log_level=logging.WARNING
    ),
    ErrorCategory.BOT_MISSING_PERMISSIONS: ErrorStyle(
        "Bot Permissions Error", 
        color=0xFF5555, 
        emoji="🤖", 
        log_level=logging.WARNING
    ),
    ErrorCategory.INVALID_ARGUMENT: ErrorStyle(
        "Invalid Argument", 
        color=0xFFA500, 
        emoji="⚠️", 
        show_help=True, 
        log_level=logging.INFO
    ),
    ErrorCategory.MISSING_ARGUMENT: ErrorStyle(
        "Missing Argument", 
        color=0xFFA500, 
        emoji="⚠️", 
        show_help=True, 
        log_level=logging.INFO
    ),
    ErrorCategory.API_ERROR: ErrorStyle(
        "API Error", 
        color=0xFF5555, 
        emoji="🌐", 
        log_level=logging.ERROR
    ),
    ErrorCategory.NETWORK_ERROR: ErrorStyle(
        "Network Error", 
        color=0xFF5555, 
        emoji="📡", 
        log_level=logging.ERROR
    ),
    ErrorCategory.DATA_ERROR: ErrorStyle(
        "Data Error", 
        color=0xFF5555, 
        emoji="📊", 
        log_level=logging.ERROR
    ),
    ErrorCategory.USER_ERROR: ErrorStyle(
        "User Error", 
        color=0xFFA500, 
        emoji="👤", 
        log_level=logging.INFO
    ),
    ErrorCategory.CONFIG_ERROR: ErrorStyle(
        "Configuration Error", 
        color=0xFF5555, 
        emoji="⚙️", 
        log_level=logging.ERROR
    ),
    ErrorCategory.INTERNAL_ERROR: ErrorStyle(
        "Internal Error", 
        color=0xFF0000, 
        emoji="⚠️", 
        log_level=logging.ERROR
    ),
    ErrorCategory.UNCATEGORIZED: ErrorStyle(
        "Error", 
        color=0xFF5555, 
        emoji="❌", 
        log_level=logging.ERROR
    )
}


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
        
    async def handle_error(self, ctx: commands.Context, error: Exception, message: Optional[str] = None) -> None:
        """Handle an error that occurred during command execution."""
        # Get the error category and style
        category = self.categorize_error(error)
        style = ERROR_STYLES.get(category, ERROR_STYLES[ErrorCategory.UNCATEGORIZED])
        
        # Log the error with the appropriate level
        self.log_error(error, category, style)
        
        # If it's a CommandNotFound error, we typically don't respond
        if category == ErrorCategory.COMMAND_NOT_FOUND:
            return
            
        # Create an appropriate error message
        error_message = message or str(error)
        
        # Create an embed for the error response
        embed = nextcord.Embed(
            title=f"{style.emoji} {style.title}",
            description=error_message,
            color=style.color
        )
        
        # Add command help if appropriate
        if style.show_help and ctx.command:
            if ctx.command.help:
                embed.add_field(
                    name="Command Help",
                    value=ctx.command.help.split('\n', 1)[0],  # First line of help
                    inline=False
                )
            embed.add_field(
                name="Usage",
                value=f"`{ctx.prefix}{ctx.command.name} {ctx.command.signature}`",
                inline=False
            )
            
        # Send the error response to the user
        await ctx.send(embed=embed)
        
    def log_error(self, error: Exception, category: ErrorCategory, style: ErrorStyle) -> None:
        """Log an error with the appropriate level of detail based on its category."""
        if style.log_level >= logging.ERROR:
            logger.error(
                f"{category.value.upper()}: {error}",
                exc_info=True
            )
        elif style.log_level >= logging.WARNING:
            logger.warning(
                f"{category.value.upper()}: {error}"
            )
        else:
            logger.info(
                f"{category.value.upper()}: {error}"
            )
            
    async def global_error_handler(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Global error handler for command errors."""
        # Unwrap the original error from CommandInvokeError
        if isinstance(error, commands.CommandInvokeError):
            error = error.original
            
        await self.handle_error(ctx, error)
        
    def setup(self) -> None:
        """Set up the error handler with the bot."""
        @self.bot.event
        async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
            await self.global_error_handler(ctx, error)


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