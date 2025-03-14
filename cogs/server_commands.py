# cogs/server_commands.py

import asyncio
import logging
from datetime import timedelta

import aiohttp
import nextcord
from nextcord.ext import commands

from utilities import (
    Config,
    get_git_revision_short_hash,
    get_git_revision_short_hash_latest,
)
from tautulli_wrapper import Tautulli

# Configure logging for this module
logger = logging.getLogger("plexbot.server_commands")
logger.setLevel(logging.INFO)


class ServerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.plex_embed_color = 0xE5A00D
        self.plex_image = "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        await self.bot.wait_until_ready()
        await self.tautulli.initialize()
        self.bot.loop.create_task(self.status_task())

    def cog_unload(self):
        self.bot.loop.create_task(self.tautulli.close())

    async def status_task(self):
        """Background task to update the bot's presence."""
        display_streams = True  # Toggles between showing streams and help command
        while not self.bot.is_closed():
            try:
                response = await self.tautulli.get_activity()
                if response.get("response", {}).get("result") != "success":
                    logger.error("Failed to retrieve activity from Tautulli.")
                    await asyncio.sleep(15)
                    continue
                stream_count = response["response"]["data"]["stream_count"]
                wan_bandwidth_mbps = round((response["response"]["data"]["wan_bandwidth"] / 1000), 1)

                if display_streams:
                    activity_text = f"{stream_count} streams at {wan_bandwidth_mbps} mbps"
                    activity_type = nextcord.ActivityType.playing
                else:
                    activity_text = ": plex help"
                    activity_type = nextcord.ActivityType.listening

                await self.bot.change_presence(
                    activity=nextcord.Activity(type=activity_type, name=activity_text)
                )
                display_streams = not display_streams  # Toggle the display mode
            except Exception as e:
                logger.error(f"Error in status_task(): {e}")

            await asyncio.sleep(15)  # Control how often to update the status

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            r = await self.tautulli.get_home_stats()
            status = r["response"]["result"]

            local_commit = get_git_revision_short_hash()
            latest_commit = get_git_revision_short_hash_latest()
            up_to_date = ""
            if local_commit and latest_commit:
                up_to_date = (
                    "Version outdated. Consider running git pull" if local_commit != latest_commit else ""
                )

            if status == "success":
                logger.info(f"Logged in as {self.bot.user}")
                logger.info("Connection to Tautulli successful")
                logger.info(
                    f"Current PlexBot version ID: {local_commit if local_commit else 'unknown'}; latest: {latest_commit if latest_commit else 'unknown'}; {up_to_date}"
                )
            else:
                logger.critical(f"Connection to Tautulli failed, result {status}")
        except Exception as e:
            logger.error(f"Error during bot initialization: {e}")

    @commands.command()
    async def status(self, ctx):
        """Displays the status of the Plex server and other related information."""
        try:
            # Getting Tautulli server info
            server_info_response = await self.tautulli.get_server_info()
            if server_info_response.get("response", {}).get("result") != "success":
                await ctx.send("Failed to retrieve server info from Tautulli.")
                logger.error("Failed to retrieve server info from Tautulli.")
                return
            server_info = server_info_response["response"]

            # Fetching Plex status from Plex API asynchronously
            async with aiohttp.ClientSession() as session:
                async with session.get("https://status.plex.tv/api/v2/status.json") as response:
                    if response.status == 200:
                        json_response = await response.json()
                        plex_status = json_response["status"]["description"]
                    else:
                        plex_status = "Plex status unavailable"

            # Setting up the embed message with server information and Plex status
            embed = nextcord.Embed(title="Plex Server Details", colour=self.plex_embed_color)
            embed.set_thumbnail(url=self.plex_image)
            embed.add_field(name="Response", value=server_info["result"], inline=True)
            embed.add_field(name="Server Name", value=server_info["data"]["pms_name"], inline=True)
            embed.add_field(
                name="Server Version",
                value=server_info["data"]["pms_version"],
                inline=True,
            )
            embed.add_field(
                name="Server IP",
                value=f"{server_info['data']['pms_ip']}:{server_info['data']['pms_port']}",
            )
            embed.add_field(name="Platform", value=server_info["data"]["pms_platform"])
            embed.add_field(name="Plex Pass", value=server_info["data"]["pms_plexpass"])
            embed.add_field(name="Plex API Status", value=plex_status)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to retrieve server status: {e}")
            await ctx.send("Failed to retrieve server status.")

    @commands.command()
    async def killstream(self, ctx, session_key: str = None, *, message: str = None):
        """Terminates a Plex stream based on the session key."""
        session_keys = []
        if session_key is None:
            activity = await self.tautulli.get_activity()
            sessions = activity["response"]["data"]["sessions"]
            for users in sessions:
                session_keys.append(f"\n**Session key:** {users['session_key']} is: **{users['user']}**,")
            await ctx.send(
                f"You provided no session keys, current users are: {''.join(session_keys)}\nYou can use `plex killstream [session_key] '[message]'` to kill a stream above;"
                "\nMessage will be passed to the user in a pop-up window on their Plex client.\n ⚠️ It is recommended to use 'apostrophes' around the message to avoid errors."
            )
            return
        try:
            r = await self.tautulli.terminate_session(session_key, message=message)
            if r == 400:
                await ctx.send(f"Could not find a stream with **{session_key}** or another error occurred")
                logger.warning(f"Failed to terminate session with key {session_key}.")
            elif r == 200:
                await ctx.send(
                    f"Killed stream with session_key: **{session_key}** and message if provided: **{message}**"
                )
                logger.info(f"Terminated session {session_key} with message: {message}")
            else:
                await ctx.send("An unexpected error occurred - check logs for more information.")
                logger.error(f"Unexpected response code {r} when terminating session {session_key}.")
        except Exception as e:
            logger.error(f"Exception occurred while terminating session {session_key}: {e}")
            await ctx.send("Failed to terminate the session due to an error.")


def setup(bot):
    bot.add_cog(ServerCommands(bot))
