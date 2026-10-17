# plexbot.py

"""
Entry point for the PlexBot Discord bot.

Initializes the bot, loads configurations, sets up logging, and starts the bot.
This version uses the new unified configuration and error handling systems.
"""

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import nextcord
from nextcord.ext import commands

# Import our new configuration and error handling systems
from config import config
from errors import ErrorHandler
from slash_commands import SlashGroups, admin_check

from tautulli_wrapper import Tautulli, TMDB
from media_cache import MediaCache
from ui.format import is_truthy
from utilities import RedactSecrets

# Configure logging
_log_handlers = [
    logging.StreamHandler(),
    # Capped so `plex logs` can always DM the full file within Discord's 10 MB
    # upload limit (alongside a matches file that is at most as large).
    RotatingFileHandler("plexbot.log", maxBytes=4_000_000, backupCount=3, encoding="utf-8"),
]
for _handler in _log_handlers:
    _handler.addFilter(RedactSecrets())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=_log_handlers,
)
logger = logging.getLogger("plexbot")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def initialize_resources():
    """Initialize shared resources that require async initialization."""
    resources = {}

    # Initialize Tautulli client
    logger.info("Initializing Tautulli client...")
    tautulli_ip = config.get("tautulli", "ip")
    tautulli_apikey = config.get("tautulli", "apikey")
    tautulli_https = config.get("tautulli", "use_https", False)
    # Each client opens its shared HTTP session on its first request.
    tautulli = Tautulli(api_key=tautulli_apikey, tautulli_ip=tautulli_ip, use_https=tautulli_https)
    resources["tautulli"] = tautulli

    # Test Tautulli connection
    test_response = await tautulli.get_server_info()
    if Tautulli.check_response(test_response):
        server_name = Tautulli.get_response_data(test_response, {}).get("pms_name", "Unknown")
        logger.info(f"Tautulli connection established to server: {server_name}")
    else:
        logger.error("Tautulli connection test failed. Please check your API key and host settings.")

    # Initialize TMDB client if API key is provided
    tmdb_api_key = config.get("tmdb", "apikey")
    if tmdb_api_key:
        logger.info("Initializing TMDB client...")
        resources["tmdb"] = TMDB(api_key=tmdb_api_key)
    else:
        resources["tmdb"] = None
        logger.info("No TMDB API key provided, TMDB features will be disabled.")

    # Initialize media cache
    logger.info("Initializing media cache...")
    media_cache_path = config.get("cache", "media_cache_path")
    cache_update_interval = config.get("cache", "update_interval")
    media_cache = MediaCache(
        tautulli, cache_file_path=media_cache_path, update_interval=cache_update_interval
    )
    await media_cache.initialize()
    resources["media_cache"] = media_cache

    return resources


async def load_cogs(bot):
    """Load every cog in the cogs directory."""
    for cog_file in sorted(Path("cogs").glob("*.py")):
        if cog_file.name.startswith("_"):
            continue
        cog_name = f"cogs.{cog_file.stem}"
        try:
            bot.load_extension(cog_name)
            logger.info(f"Loaded cog: {cog_name}")
        except Exception as e:
            logger.exception(f"Failed to load cog {cog_name}: {e}")


def install_slash_groups(bot) -> None:
    """Add the cog owning the /plex and /plexadmin roots.

    Subcommands bind to their own cogs when those are instantiated, so this can run
    before or after load_cogs; being a cog is what keeps the groups registered
    across nextcord's READY state reset and every reconnect.
    """
    bot.add_cog(SlashGroups())
    bot.add_application_command_check(admin_check)


class PlexBot(commands.Bot):
    """Bot subclass that owns one-time async initialization and graceful teardown.

    All setup lives in ``setup_hook`` rather than ``on_ready`` because ``on_ready``
    can fire multiple times (on every gateway reconnect/resume); doing heavy
    initialization there would re-create clients, leak aiohttp sessions, and
    re-populate the media cache on every reconnect.
    """

    async def setup_hook(self) -> None:
        """One-time async initialization. Invoked explicitly from start() below,
        because nextcord — unlike discord.py — never calls setup_hook() itself."""
        self.shared_resources = await initialize_resources()
        logger.info("Shared resources initialized")
        install_slash_groups(self)
        await load_cogs(self)

    async def start(self, *args, **kwargs) -> None:
        # nextcord (unlike discord.py) never invokes setup_hook(); call it
        # ourselves so shared resources + cogs initialize once before connecting.
        await self.setup_hook()
        await super().start(*args, **kwargs)

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        """Log exceptions from event handlers, such as the command sync on connect.

        nextcord's default prints them to stderr, so they never reach plexbot.log.
        Called from inside the failing handler's except block, so the traceback is live.
        """
        logger.exception(f"Unhandled exception in {event_method}")

    async def close(self) -> None:
        """Shut down the gateway first, then close shared aiohttp sessions.

        super().close() unloads the cogs (cancelling background loops like
        ServerCommands.status_task via cog_unload) and stops the gateway before we
        close the shared Tautulli/TMDB sessions here, so we don't tear those sessions
        down while a task is still mid-request — which would otherwise error or
        silently re-open a new, never-closed session via Tautulli._ensure_session().
        """
        await super().close()

        logger.info("Closing shared resources...")
        resources = getattr(self, "shared_resources", None) or {}
        for name in ("media_cache", "tautulli", "tmdb"):
            client = resources.get(name)
            if client is not None:
                try:
                    await client.close()
                except Exception as e:
                    logger.error(f"Error closing {name} client during shutdown: {e}")


def main():
    """Main entry point for the bot."""
    logger.info("Starting PlexBot...")

    # Initialize configuration system
    if not config.initialize():
        logger.error("Failed to initialize configuration system. Run migrate_config.py first if needed.")
        return

    log_level = str(config.get("core", "log_level", "INFO")).upper()
    if log_level not in logging.getLevelNamesMapping():
        logger.warning(f"Unknown core.log_level {log_level!r}; using INFO.")
        log_level = "INFO"
    # The bot's own loggers only: DEBUG on the root would also flood the log with nextcord's gateway chatter.
    logging.getLogger("plexbot").setLevel(log_level)

    # Get core configuration
    token = config.get("core", "token")
    if not token:
        logger.error("Discord bot token is missing. Check your configuration.")
        return

    # Create the bot and configure intents
    intents = nextcord.Intents.default()
    intents.members = True
    # Message content (a privileged intent) only serves the old prefix commands' redirect. Without it,
    # Discord still sends the text of messages that mention the bot, so "@PlexBot status" is answered.
    legacy = is_truthy(config.get("core", "legacy_text_commands", False))
    intents.message_content = legacy
    bot_prefix = config.get("core", "prefix")

    # Register slash commands to the configured server: guild commands update
    # instantly, whereas global ones can take up to an hour to appear.
    server_id = str(config.get("discord", "server_id") or "").strip()
    if server_id.isdigit():
        guild_ids = [int(server_id)]
        logger.info(f"Slash commands will register to server {server_id}.")
    else:
        guild_ids = None
        logger.info("No discord.server_id set; slash commands register globally (may take up to an hour).")
        if not config.get("discord", "admin_ids", []):
            logger.warning("Neither discord.server_id nor discord.admin_ids is set, so nobody can use /plexadmin.")

    prefix = commands.when_mentioned_or(bot_prefix, bot_prefix.title()) if legacy else commands.when_mentioned
    bot = PlexBot(
        command_prefix=prefix,
        intents=intents,
        help_command=None,
        default_guild_ids=guild_ids,
    )

    # Create and register the error handler (only adds a listener; no gateway needed).
    error_handler = ErrorHandler(bot)
    error_handler.setup()
    logger.info("Error handler initialized.")

    @bot.event
    async def on_ready():
        """Triggered whenever the gateway (re)connects. Keep this idempotent and cheap."""
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        logger.info(f"PlexBot is now ready to serve {len(bot.guilds)} servers")

    # Run the bot
    try:
        bot.run(token)
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user.")
    except Exception as e:
        logger.exception(f"Failed to run the bot: {e}")
        # Non-zero exit so systemd's Restart=on-failure (and Docker's restart policy) bring it back.
        sys.exit(1)


if __name__ == "__main__":
    main()
