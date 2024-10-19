"""
Entry point for the PlexBot Discord bot.

Initializes the bot, loads configurations, sets up logging, and starts the bot.
"""

import json
import logging
import os
import sys
import traceback
from pathlib import Path

import nextcord
from nextcord.ext import commands

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
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        logger.info("Configuration loaded successfully.")
    except Exception as e:
        logger.exception("Failed to load configuration.")
        return

    # Validate required configuration keys
    required_keys = ["token", "tautulli_ip", "tautulli_apikey"]
    missing_keys = [key for key in required_keys if key not in config]
    if missing_keys:
        logger.error(f"Missing required configuration keys: {', '.join(missing_keys)}")
        return

    # Create the bot and configure intents
    intents = nextcord.Intents.default()
    # Need message_content for prefix commands
    intents.message_content = True
    # Need members for role changes in plex_top
    intents.members = True

    # Initialize bot with the prefix `plex ` and intents
    bot = commands.Bot(
        command_prefix=["plex ", "Plex "], intents=intents, help_command=None
    )

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
            await ctx.send(
                "You do not have the appropriate permissions to run this command."
            )
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
