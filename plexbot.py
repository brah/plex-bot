# plexbot.py

"""
Entry point for the PlexBot Discord bot.

Initializes the bot, loads configurations, sets up logging, and starts the bot.
"""

import asyncio
import logging
import sys
import traceback
from pathlib import Path

import nextcord
from nextcord.ext import commands

from utilities import Config
from tautulli_wrapper import Tautulli, TMDB
from media_cache import MediaCache
from bot_config import BotConfig

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


async def initialize_resources(config):
    """Initialize shared resources that require async initialization."""
    resources = {}

    # Initialize Tautulli client
    logger.info("Initializing Tautulli client...")
    tautulli = Tautulli(api_key=config["tautulli_apikey"], tautulli_ip=config["tautulli_ip"])
    await tautulli.initialize()
    resources["tautulli"] = tautulli

    # Test Tautulli connection
    test_response = await tautulli.get_server_info()
    if test_response and test_response.get("response", {}).get("result") == "success":
        server_name = test_response.get("response", {}).get("data", {}).get("pms_name", "Unknown")
        logger.info(f"Tautulli connection established to server: {server_name}")
    else:
        logger.error("Tautulli connection test failed. Please check your API key and host settings.")

    # Initialize TMDB client if API key is provided
    tmdb_api_key = config.get("tmdb_apikey")
    if tmdb_api_key:
        logger.info("Initializing TMDB client...")
        tmdb = TMDB(api_key=tmdb_api_key)
        await tmdb.initialize()
        resources["tmdb"] = tmdb
    else:
        resources["tmdb"] = None
        logger.info("No TMDB API key provided, TMDB features will be disabled.")

    # Initialize media cache
    logger.info("Initializing media cache...")
    media_cache = MediaCache(tautulli, cache_file_path=BotConfig.MEDIA_CACHE_PATH)
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


def main():
    """Main entry point for the bot."""
    logger.info("Starting PlexBot...")

    # Load configuration
    config = Config.load_config()
    if not config:
        logger.error("Failed to load configuration.")
        return

    # Validate required configuration keys
    required_keys = ["token", "tautulli_ip", "tautulli_apikey"]
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        logger.error(f"Missing required configuration keys: {', '.join(missing_keys)}")
        return

    # Create the bot and configure intents
    intents = nextcord.Intents.default()
    intents.message_content = True
    intents.members = True

    # Initialize bot with the prefix `plex ` and intents
    bot = commands.Bot(command_prefix=["plex ", "Plex "], intents=intents, help_command=None)

    @bot.event
    async def on_ready():
        """Event triggered when the bot is ready to start."""
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")

        # Initialize shared resources asynchronously after bot is ready
        bot.shared_resources = await initialize_resources(config)
        logger.info("Shared resources initialized")

        # Dynamically load all cogs from the 'cogs' directory
        await load_cogs(bot)

        logger.info(f"PlexBot is now ready to serve {len(bot.guilds)} servers")

    @bot.event
    async def on_command_error(ctx, error):
        """Event triggered when a command raises an error."""
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"*{error}*\nTry `{ctx.prefix}help {ctx.command}`")
            logger.warning(f"Missing required argument: {error}")
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("You do not have the appropriate permissions to run this command.")
            logger.warning(f"Missing permissions: {error}")
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("I don't have sufficient permissions!")
            logger.warning(f"Bot missing permissions: {error}")
        else:
            logger.error(f"Unhandled exception in command {ctx.command}: {error}")
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
            await ctx.send(
                "An error occurred while processing this command. Please check the logs for details."
            )

    # Run the bot
    try:
        bot.run(config["token"])
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user.")
    except Exception as e:
        logger.exception(f"Failed to run the bot: {e}")


if __name__ == "__main__":
    main()
