# cogs/server_commands.py

import asyncio
import logging

import aiohttp
import nextcord
from nextcord.ext import commands

from config import config
from utilities import (
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
        self.plex_embed_color = config.get("ui", "plex_embed_color", 0xE5A00D)
        self.plex_image = config.get("ui", "plex_image")
        self._status_task = None
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        await self.bot.wait_until_ready()
        self.tautulli.initialize()
        await self._log_startup_info()
        self._status_task = self.bot.loop.create_task(self.status_task())

    async def _log_startup_info(self):
        """Log Tautulli connectivity and the bot's git version, once, at startup."""
        try:
            r = await self.tautulli.get_home_stats()

            # These shell out to git (and the "latest" check hits the network),
            # so run them in threads — concurrently — to avoid blocking the event loop.
            local_commit, latest_commit = await asyncio.gather(
                asyncio.to_thread(get_git_revision_short_hash),
                asyncio.to_thread(get_git_revision_short_hash_latest),
            )
            # The git helpers return the sentinel "unknown" on failure (e.g. offline),
            # which is truthy — exclude it so a failed fetch isn't read as "outdated".
            up_to_date = ""
            known = local_commit not in ("", "unknown") and latest_commit not in ("", "unknown")
            if known and local_commit != latest_commit:
                up_to_date = "Version outdated. Consider running git pull"

            if Tautulli.check_response(r):
                logger.info(f"Logged in as {self.bot.user}")
                logger.info("Connection to Tautulli successful")
                logger.info(
                    f"Current PlexBot version ID: {local_commit or 'unknown'}; "
                    f"latest: {latest_commit or 'unknown'}; {up_to_date}"
                )
            else:
                logger.critical("Connection to Tautulli failed")
        except Exception as e:
            logger.error(f"Error during startup info logging: {e}")

    def cog_unload(self):
        # Stop the background presence loop. The shared Tautulli client is owned by
        # the bot (PlexBot.close()) and must not be closed here.
        if self._status_task is not None:
            self._status_task.cancel()

    async def status_task(self):
        """Background task to update the bot's presence."""
        display_streams = True  # Toggles between showing streams and help command
        while not self.bot.is_closed():
            try:
                response = await self.tautulli.get_activity()
                if not Tautulli.check_response(response):
                    logger.error("Failed to retrieve activity from Tautulli.")
                    await asyncio.sleep(15)
                    continue
                data = Tautulli.get_response_data(response, {})
                stream_count = data.get("stream_count", 0)
                wan_bandwidth_mbps = round(data.get("wan_bandwidth", 0) / 1000, 1)

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

    @commands.command()
    async def status(self, ctx):
        """Displays the status of the Plex server and other related information."""
        try:
            # Getting Tautulli server info
            server_info_response = await self.tautulli.get_server_info()
            if not Tautulli.check_response(server_info_response):
                await ctx.send("Failed to retrieve server info from Tautulli.")
                logger.error("Failed to retrieve server info from Tautulli.")
                return
            server_data = Tautulli.get_response_data(server_info_response, {})

            # Fetching Plex status from Plex API asynchronously
            async with aiohttp.ClientSession() as session:
                async with session.get("https://status.plex.tv/api/v2/status.json") as response:
                    if response.status == 200:
                        json_response = await response.json()
                        plex_status = json_response.get("status", {}).get("description", "Unknown")
                    else:
                        plex_status = "Plex status unavailable"

            # Setting up the embed message with server information and Plex status
            embed = nextcord.Embed(title="Plex Server Details", colour=self.plex_embed_color)
            embed.set_thumbnail(url=self.plex_image)
            embed.add_field(name="Server Name", value=server_data.get("pms_name", "Unknown"), inline=True)
            embed.add_field(
                name="Server Version",
                value=server_data.get("pms_version", "Unknown"),
                inline=True,
            )
            embed.add_field(
                name="Server IP",
                value=f"{server_data.get('pms_ip', '?')}:{server_data.get('pms_port', '?')}",
            )
            embed.add_field(name="Platform", value=server_data.get("pms_platform", "Unknown"))
            embed.add_field(name="Plex Pass", value=server_data.get("pms_plexpass", "Unknown"))
            embed.add_field(name="Plex API Status", value=plex_status)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to retrieve server status: {e}")
            await ctx.send("Failed to retrieve server status.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def killstream(self, ctx, session_key: str = None, *, message: str = None):
        """Terminates a Plex stream based on the session key."""
        session_keys = []
        if session_key is None:
            activity = await self.tautulli.get_activity()
            if not Tautulli.check_response(activity):
                await ctx.send("Failed to retrieve current activity from Tautulli.")
                return
            sessions = Tautulli.get_response_data(activity, {}).get("sessions", [])
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
