# cogs/user_management.py

import logging

import nextcord
from nextcord.ext import commands

from utilities import UserMappings

# Configure logging for this module
logger = logging.getLogger("plexbot.user_management")
logger.setLevel(logging.INFO)


class UserManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def mapdiscord(self, ctx, plex_username: str, discord_user: nextcord.User = None):
        """Map a Discord user to a Plex username."""
        if not plex_username.strip():
            await ctx.send("Please provide a valid Plex username.")
            return

        discord_user = discord_user or ctx.author
        mappings = UserMappings.load_user_mappings()

        for member in mappings:
            if str(member.get("discord_id")) == str(discord_user.id):
                if member.get("plex_username") == plex_username:
                    await ctx.send(f"You are already mapped to {plex_username}.")
                    return
                else:
                    member["plex_username"] = plex_username
                    UserMappings.save_user_mappings(mappings)
                    await ctx.send(
                        f"Successfully updated mapping for {discord_user.display_name} to {plex_username}."
                    )
                    logger.info(f"Updated mapping for {discord_user.display_name} to {plex_username}.")
                    return

        # If user is not found, add them
        mappings.append({"discord_id": discord_user.id, "plex_username": plex_username})
        UserMappings.save_user_mappings(mappings)
        await ctx.send(f"Successfully mapped {discord_user.display_name} to {plex_username}.")
        logger.info(f"Mapped {discord_user.display_name} to {plex_username}.")

    @commands.command()
    async def ignore(self, ctx, plex_username: str):
        """Toggle ignoring a user's Plex username from appearing in top lists."""
        if not plex_username.strip():
            await ctx.send("Please provide a valid Plex username.")
            return

        mappings = UserMappings.load_user_mappings()
        found = False

        for member in mappings:
            if member.get("plex_username") == plex_username:
                member["ignore"] = not member.get("ignore", False)
                found = True
                status = "no longer" if not member["ignore"] else "now"
                await ctx.send(f"{plex_username} is {status} ignored in top lists.")
                logger.info(f"{plex_username} is {status} ignored in top lists.")
                break

        if not found:
            mappings.append({"discord_id": "", "plex_username": plex_username, "ignore": True})
            await ctx.send(f"{plex_username} is now ignored in top lists.")
            logger.info(f"{plex_username} is now ignored in top lists.")

        UserMappings.save_user_mappings(mappings)


def setup(bot):
    bot.add_cog(UserManagement(bot))
