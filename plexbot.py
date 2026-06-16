# plexbot.py

"""
Entry point for the PlexBot Discord bot.

Initializes the bot, loads configurations, sets up logging, and starts the bot.
This version uses the new unified configuration and error handling systems.
"""

import asyncio
import logging
import sys
from pathlib import Path

import nextcord
from nextcord.ext import commands

# Import our new configuration and error handling systems
from config import config
from errors import ErrorHandler

from tautulli_wrapper import Tautulli, TMDB
from media_cache import MediaCache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler("plexbot.log")],
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
    tautulli = Tautulli(api_key=tautulli_apikey, tautulli_ip=tautulli_ip, use_https=tautulli_https)
    tautulli.initialize()
    resources["tautulli"] = tautulli

    # Test Tautulli connection
    test_response = await tautulli.get_server_info()
    if test_response and test_response.get("response", {}).get("result") == "success":
        server_name = test_response.get("response", {}).get("data", {}).get("pms_name", "Unknown")
        logger.info(f"Tautulli connection established to server: {server_name}")
    else:
        logger.error("Tautulli connection test failed. Please check your API key and host settings.")

    # Initialize TMDB client if API key is provided
    tmdb_api_key = config.get("tmdb", "apikey")
    if tmdb_api_key:
        logger.info("Initializing TMDB client...")
        tmdb = TMDB(api_key=tmdb_api_key)
        tmdb.initialize()
        resources["tmdb"] = tmdb
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
    """Load all cogs from the cogs directory with proper dependency ordering."""
    logger.info("Starting to load cogs with prioritized ordering")

    # Priority loading order - these cogs must be loaded first
    priority_cogs = [
        "cogs.plex_data",  # Load this first as it's a dependency
    ]

    # Load priority cogs first
    for cog_name in priority_cogs:
        try:
            bot.load_extension(cog_name)
            logger.info(f"Loaded priority cog: {cog_name}")
        except Exception as e:
            logger.exception(f"Failed to load priority cog {cog_name}: {e}")

    # Then load all remaining cogs
    cog_directory = Path("cogs")
    for cog_file in cog_directory.glob("*.py"):
        if cog_file.name.startswith("_"):
            continue  # Skip any files that start with an underscore

        cog_name = f"cogs.{cog_file.stem}"
        # Skip cogs that were already loaded in priority list
        if cog_name in priority_cogs:
            continue

        try:
            bot.load_extension(cog_name)
            logger.info(f"Loaded cog: {cog_name}")
        except Exception as e:
            logger.exception(f"Failed to load cog {cog_name}: {e}")


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
        await load_cogs(self)

    async def start(self, *args, **kwargs) -> None:
        # nextcord (unlike discord.py) never invokes setup_hook(); call it
        # ourselves so shared resources + cogs initialize once before connecting.
        await self.setup_hook()
        await super().start(*args, **kwargs)

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
        for name in ("tautulli", "tmdb"):
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

    # Get core configuration
    token = config.get("core", "token")
    if not token:
        logger.error("Discord bot token is missing. Check your configuration.")
        return

    # Create the bot and configure intents
    intents = nextcord.Intents.default()
    intents.message_content = True
    intents.members = True

    # Initialize bot with the prefix from configuration
    bot_prefix = config.get("core", "prefix")
    bot = PlexBot(command_prefix=[bot_prefix, bot_prefix.title()], intents=intents, help_command=None)

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


if __name__ == "__main__":
    main()
