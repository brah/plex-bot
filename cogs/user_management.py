# cogs/user_management.py

import json
import logging
from functools import lru_cache

import nextcord
from nextcord.ext import commands

# Configure logging for this module
logger = logging.getLogger('plexbot.user_management')
logger.setLevel(logging.INFO)


class UserManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.LOCAL_JSON = "map.json"
        self.CONFIG_JSON = "config.json"

    @lru_cache(maxsize=1)
    def load_user_mappings(self):
        """Load user mappings from the JSON file."""
        try:
            with open(self.LOCAL_JSON, "r", encoding="utf-8") as json_file:
                return json.load(json_file)
        except json.JSONDecodeError as err:
            logger.error(f"Failed to load or decode JSON: {err}")
            return []
        except FileNotFoundError:
            logger.error("User mappings file not found.")
            return []

    def save_user_mappings(self, data):
        """Save user mappings to the JSON file."""
        try:
            with open(self.LOCAL_JSON, "w", encoding="utf-8") as json_file:
                json.dump(data, json_file, indent=4)
            self.load_user_mappings.cache_clear()  # Invalidate the cache after updating the file
            logger.info("User mappings saved and cache cleared.")
        except Exception as e:
            logger.exception(f"Failed to save user mappings: {e}")

    @commands.command()
    async def mapdiscord(
        self, ctx, plex_username: str, discord_user: nextcord.User = None
    ):
        """Map a Discord user to a Plex username."""
        if not plex_username.strip():
            await ctx.send("Please provide a valid Plex username.")
            return

        discord_user = discord_user or ctx.author
        dc_plex_json = self.load_user_mappings()

        for member in dc_plex_json:
            if str(member.get("discord_id")) == str(discord_user.id):
                if member.get("plex_username") == plex_username:
                    await ctx.send(f"You are already mapped to {plex_username}.")
                    return
                else:
                    member["plex_username"] = plex_username
                    self.save_user_mappings(dc_plex_json)
                    await ctx.send(
                        f"Successfully updated mapping for {discord_user.display_name} to {plex_username}."
                    )
                    logger.info(f"Updated mapping for {discord_user.display_name} to {plex_username}.")
                    return

        # If user is not found, add them
        dc_plex_json.append(
            {"discord_id": discord_user.id, "plex_username": plex_username}
        )
        self.save_user_mappings(dc_plex_json)
        await ctx.send(
            f"Successfully mapped {discord_user.display_name} to {plex_username}."
        )
        logger.info(f"Mapped {discord_user.display_name} to {plex_username}.")

    @commands.command()
    async def ignore(self, ctx, plex_username: str):
        """Toggle ignoring a user's Plex username from appearing in top lists."""
        if not plex_username.strip():
            await ctx.send("Please provide a valid Plex username.")
            return

        data = self.load_user_mappings()  # Use cached data
        found = False

        for member in data:
            if member.get("plex_username") == plex_username:
                member["ignore"] = not member.get("ignore", False)
                found = True
                status = "no longer" if not member["ignore"] else "now"
                await ctx.send(f"{plex_username} is {status} ignored in top lists.")
                logger.info(f"{plex_username} is {status} ignored in top lists.")
                break

        if not found:
            data.append(
                {"discord_id": "", "plex_username": plex_username, "ignore": True}
            )
            await ctx.send(f"{plex_username} is now ignored in top lists.")
            logger.info(f"{plex_username} is now ignored in top lists.")

        self.save_user_mappings(data)  # Save and clear cache


def setup(bot):
    bot.add_cog(UserManagement(bot))
