# plex_bot.py

"""
Entry point for the PlexBot Discord bot.

Initializes the bot, loads configurations, sets up logging, and starts the bot.
"""

import logging
import sys
import traceback
from pathlib import Path

import nextcord
from nextcord.ext import commands

from utilities import Config
from tautulli_wrapper import Tautulli, TMDB

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("plexbot")


def main():
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

    # Initialize shared resources
    tautulli = Tautulli(api_key=config["tautulli_apikey"], tautulli_ip=config["tautulli_ip"])
    tmdb_api_key = config.get("tmdb_apikey")
    tmdb = TMDB(api_key=tmdb_api_key) if tmdb_api_key else None

    # Pass shared resources to cogs upon initialization
    bot.shared_resources = {
        "tautulli": tautulli,
        "tmdb": tmdb,
    }

    @bot.event
    async def on_ready():
        logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        logger.info("------")

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            logger.debug(f"Command not found: {ctx.message.content}")
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
            logger.exception(f"Unhandled exception: {error}")
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    # Dynamically load all cogs from the 'cogs' directory
    cog_directory = Path("cogs")
    for cog_file in cog_directory.glob("*.py"):
        if cog_file.name.startswith("_"):
            continue  # Skip any files that start with an underscore
        cog_name = f"cogs.{cog_file.stem}"
        try:
            bot.load_extension(cog_name)
            logger.info(f"Loaded cog: {cog_name}")
        except Exception as e:
            logger.exception(f"Failed to load cog {cog_name}.")

    # Run the bot
    try:
        bot.run(config["token"])
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user.")
    except Exception as e:
        logger.exception("Failed to run the bot.")


if __name__ == "__main__":
    main()
