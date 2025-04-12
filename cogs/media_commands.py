# cogs/media_commands.py

import asyncio
import logging
import random
from datetime import timedelta
from io import BytesIO
from config import config

import aiohttp
import nextcord
from nextcord import File
from nextcord.ext import commands
import qbittorrentapi

from utilities import (
    Config,
    UserMappings,
    NoStopButtonMenuPages,
    MyEmbedDescriptionPageSource,
    fetch_plex_image,
    prepare_thumbnail_for_embed,
)
from tautulli_wrapper import Tautulli, TMDB
from media_cache import MediaCache
from bot_config import BotConfig

# Configure logging for this module
logger = logging.getLogger("plexbot.media_commands")
logger.setLevel(logging.INFO)


class MediaCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.tmdb: TMDB = bot.shared_resources.get("tmdb")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.plex_embed_color = BotConfig.PLEX_EMBED_COLOR
        self.plex_image = BotConfig.PLEX_IMAGE
        logger.info("MediaCommands cog initialized.")

    def cog_unload(self):
        """Clean up resources when the cog is unloaded."""
        self.bot.loop.create_task(self.tautulli.close())
        if self.tmdb:
            self.bot.loop.create_task(self.tmdb.close())

    @commands.command()
    async def recent(self, ctx, amount: int = BotConfig.DEFAULT_RECENT_COUNT):
        """Displays recently added media items.

        Usage:
        plex recent [amount]

        Examples:
        plex recent
        plex recent 20
        """
        fields = []
        try:
            response = await self.tautulli.get_recently_added(count=amount)
            if response.get("response", {}).get("result") != "success":
                await ctx.send("Failed to retrieve recent additions.")
                logger.error("Failed to retrieve recent additions from Tautulli.")
                return

            for entry in response["response"]["data"]["recently_added"]:
                if entry.get("originally_available_at") == "":
                    continue
                # Work around to show full show name alongside episode name
                if entry.get("grandparent_title"):
                    entry["title"] = f"{entry['grandparent_title']} - {entry['title']}"
                if entry.get("rating") == "":
                    entry["rating"] = "N/A"
                entry_data = {
                    "description": f"**🎥 {entry['title']}** 🕗 {entry['originally_available_at']} 🍅: {entry['rating']}/10\n{entry.get('summary', '')}\n",
                    "thumb_key": entry.get("thumb", ""),
                }
                fields.append(entry_data)

            tautulli_ip = self.tautulli.tautulli_ip  # Tautulli webserver IP
            pages = NoStopButtonMenuPages(
                source=MyEmbedDescriptionPageSource(fields, tautulli_ip),
            )
            await pages.start(ctx)
        except Exception as e:
            logger.error(f"Failed to retrieve recent additions: {e}")
            await ctx.send("Failed to retrieve recent additions.")

    @commands.command()
    async def random(self, ctx, *args):
        """Displays a random media item from the Plex libraries."""
        # Parse arguments
        media_type, genre = self.parse_random_args(args)
        logger.info(f"Searching for {genre} of mediatype {media_type}")

        try:
            # Check if the cache is empty
            cache_count = len(await self.media_cache.get_items(limit=1))
            if cache_count == 0:
                # Cache is empty, try refreshing it
                logger.warning("Media cache is empty, attempting to refresh...")
                await ctx.send("The media cache appears to be empty. I'll try to refresh it now...")

                await self.media_cache.update_cache()

                # Check again after refresh
                cache_count = len(await self.media_cache.get_items(limit=1))
                if cache_count == 0:
                    await ctx.send(
                        "I couldn't find any media items. Please check the Tautulli connection and try again later."
                    )
                    return
                else:
                    await ctx.send(f"Cache has been refreshed with {cache_count} items.")

            # Get filtered media items from cache
            items = await self.media_cache.get_items(
                media_type=media_type, genres=[genre] if genre else None, random_sort=True, limit=100
            )

            logger.debug(f"Found {len(items)} items matching the criteria")

            if not items:
                await ctx.send("No media items found matching the criteria.")
                return

            # Select a random item
            random_item = random.choice(items)
            await self.send_movie_embed(ctx, random_item)
        except Exception as e:
            logger.error(f"Error in random command: {e}", exc_info=True)
            await ctx.send(f"An error occurred while looking for a random item: {type(e).__name__}")

    def parse_random_args(self, args):
        """Parse the arguments for the random command."""
        media_type = None
        genre = None

        if args:
            # Lowercase all arguments for case-insensitive comparison
            args_lower = [arg.lower() for arg in args]
            # Possible media types
            media_types = ["movie", "tv", "any"]
            # Check for media type in args
            media_type_in_args = set(args_lower) & set(media_types)
            if media_type_in_args:
                media_type = media_type_in_args.pop()
                # Remove the media type from args
                args_lower.remove(media_type)
            # The remaining args are genre
            if args_lower:
                genre = " ".join(args_lower).lower()
        return media_type, genre

    async def send_movie_embed(self, ctx, item):
        """Send an embed with the media item's details."""
        try:
            # Construct the embed using item data
            embed = nextcord.Embed(
                title=f"{item['title']} ({item['year']})",
                color=nextcord.Color.random(),
                description=item.get("summary", "No summary available."),
            )

            # Add rating
            if item.get("rating"):
                embed.add_field(name="Rating", value=f"{item['rating']}/10", inline=True)

            # Add genres
            if item.get("genres"):
                genres_formatted = ", ".join([g.title() for g in item["genres"]])
                embed.add_field(name="Genres", value=genres_formatted, inline=True)

            # Add play count
            play_count = item.get("play_count", 0)
            if play_count == 0:
                play_count_text = "Never watched"
            else:
                play_count_text = f"{play_count} time{'s' if play_count != 1 else ''}"
            embed.add_field(name="Play Count", value=play_count_text, inline=True)

            # Add last played
            if item.get("last_played"):
                last_played = f"<t:{item.get('last_played', '')}:D>"
                embed.add_field(name="Last Played", value=last_played, inline=True)

            # Add thumbnail if available
            if item.get("thumb"):
                file, attachment_url = await prepare_thumbnail_for_embed(
                    self.tautulli.tautulli_ip, item["thumb"]
                )
                if file and attachment_url:
                    embed.set_image(url=attachment_url)
                    await ctx.send(file=file, embed=embed)
                    return

            # If we got here, either there's no thumbnail or we failed to retrieve it
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send movie embed: {e}")
            await ctx.send("Failed to display the media item.")

    # The construct_image_url method has been replaced by the utility functions in utilities.py

    @commands.command()
    async def watchers(self, ctx):
        """Display current Plex watchers with details about their activity."""
        try:
            response = await self.tautulli.get_activity()
            if not response or response.get("response", {}).get("result") != "success":
                await ctx.send("Failed to retrieve current Plex activity.")
                logger.error("Failed to retrieve activity from Tautulli.")
                return

            sessions = response["response"]["data"]["sessions"]
            if not sessions:
                await ctx.send("No one is currently watching Plex.")
                return

            # Load ignored users list
            user_data = UserMappings.load_user_mappings()
            ignored_users = {user["plex_username"] for user in user_data if user.get("ignore", False)}

            embed = nextcord.Embed(title="Plex Watchers", color=self.plex_embed_color)
            embed.set_thumbnail(url=self.plex_image)

            active_users = 0

            for user in sessions:
                if user["username"] in ignored_users:
                    continue  # Skip ignored users

                active_users += 1
                try:
                    # Process user state
                    state = user.get("state", "unknown").capitalize()
                    state_symbol = {
                        "Playing": "▶️",
                        "Paused": "⏸️",
                        "Buffering": "⏳",
                    }.get(state, state)

                    try:
                        view_offset_ms = int(user.get("view_offset", 0))
                        duration_ms = int(user.get("duration", 0))
                        elapsed_time = str(timedelta(milliseconds=view_offset_ms))

                        # Calculate progress percentage only if we have valid duration
                        progress_text = ""
                        if duration_ms > 0:
                            percentage = min(100, (view_offset_ms / duration_ms) * 100)
                            progress_text = f" ({percentage:.1f}%)"
                    except (ValueError, TypeError):
                        elapsed_time = "Unknown"
                        progress_text = ""
                        logger.warning(f"Invalid time data for user {user['friendly_name']}")

                    # Build user field with detailed information
                    user_field = (
                        f"Watching **{user['full_title']}**\n"
                        f"Quality: **{user.get('quality_profile', 'Unknown')}**\n"
                        f"State: **{state_symbol}**\n"
                        f"Elapsed Time: **{elapsed_time}**{progress_text}"
                    )

                    embed.add_field(
                        name=user["friendly_name"],
                        value=user_field,
                        inline=False,
                    )
                except Exception as e:
                    logger.error(f"Error processing user {user.get('friendly_name', 'unknown')}: {e}")
                    continue

            # Set appropriate description based on number of active watchers
            if active_users > 0:
                embed.description = f"**{active_users}** users are currently watching Plex 🐒"
            else:
                embed.description = "No one is active on Plex at the moment. 😔✊"

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to retrieve watchers: {e}", exc_info=True)
            await ctx.send("Failed to retrieve watchers.")

    @commands.command()
    async def downloading(self, ctx):
        """Display the current downloading torrents in qBittorrent."""
        # Try to instantiate the qBittorrent client
        try:
            qbit_ip = config.get("qbittorrent", "ip")
            qbit_port = config.get("qbittorrent", "port")
            qbit_username = config.get("qbittorrent", "username")
            qbit_password = config.get("qbittorrent", "password")

            # Check if all required configurations are present
            if not all([qbit_ip, qbit_port, qbit_username, qbit_password]):
                missing_config = []
                if not qbit_ip:
                    missing_config.append("IP")
                if not qbit_port:
                    missing_config.append("Port")
                if not qbit_username:
                    missing_config.append("Username")
                if not qbit_password:
                    missing_config.append("Password")

                await ctx.send(
                    f"qBittorrent is not properly configured. Missing: {', '.join(missing_config)}"
                )
                logger.error(f"Missing qBittorrent configuration: {', '.join(missing_config)}")
                return

            logger.debug(
                "Creating qbittorrent Client with IP=%s, Port=%s, Username=%s",
                qbit_ip,
                qbit_port,
                qbit_username,
            )

            qbt_client = qbittorrentapi.Client(
                host=qbit_ip,
                port=qbit_port,
                username=qbit_username,
                password=qbit_password,
            )

            # Login to qBittorrent
            qbt_client.auth_log_in()
            logger.debug("Successfully logged into qBittorrent? %s", qbt_client.is_logged_in)

        except qbittorrentapi.LoginFailed:
            logger.exception("Login to qBittorrent failed. Check username and password.")
            await ctx.send("Failed to log in to qBittorrent. Check your credentials.")
            return
        except qbittorrentapi.APIConnectionError:
            logger.exception("Connection to qBittorrent failed. Check network and host settings.")
            await ctx.send("Failed to connect to qBittorrent. Check your network settings.")
            return
        except Exception as err:
            logger.exception("Unexpected error connecting to qBittorrent.")
            await ctx.send("I'm having trouble connecting to qBittorrent right now.")
            return

        # Pull downloading torrents
        downloads_embed = nextcord.Embed(
            title="qBittorrent Live Downloads",
            color=BotConfig.QBIT_EMBED_COLOR,
        )
        downloads_embed.set_thumbnail(url=BotConfig.QBIT_IMAGE)

        try:
            # Get all downloading torrents
            torrents_downloading = [
                t
                for t in qbt_client.torrents.info.downloading()
                if t.state not in ["pausedDL", "pausedUP", "stopped"] and not t.state_enum.is_paused
            ]
            logger.debug("Fetched %d torrents in downloading status.", len(torrents_downloading))
        except Exception as e:
            logger.exception("Error retrieving downloading torrents.")
            await ctx.send("I'm having trouble retrieving the downloading torrents.")
            return

        if not torrents_downloading:
            downloads_embed.description = "There are no torrents currently downloading."
            await ctx.send(embed=downloads_embed)
            return

        # Sort torrents by progress (descending)
        torrents_downloading.sort(key=lambda x: x.progress, reverse=True)

        for download in torrents_downloading:
            # Format download speed with appropriate units
            dl_speed = download.dlspeed
            if dl_speed > 1_000_000:  # If more than 1 MB/s
                dl_speed_str = f"{dl_speed * 1.0e-6:.2f} MB/s"
            else:
                dl_speed_str = f"{dl_speed * 1.0e-3:.0f} KB/s"

            # Format ETA
            eta = download.eta
            if eta == 8640000:  # qBittorrent uses this value for unknown ETA
                eta_str = "unknown"
            elif eta > 86400:  # More than a day
                eta_str = f"{eta / 86400:.1f} days"
            elif eta > 3600:  # More than an hour
                eta_str = f"{eta / 3600:.1f} hours"
            else:
                eta_str = f"{eta / 60:.0f} minutes"

            downloads_embed.add_field(
                name=f"⏳ {download.name}",
                value=(
                    f"**Progress**: {download.progress * 100:.2f}%, "
                    f"**Size:** {download.size * 1e-9:.2f} GB, "
                    f"**ETA:** {eta_str}, "
                    f"**DL:** {dl_speed_str}"
                ),
                inline=False,
            )

        await ctx.send(embed=downloads_embed)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def refresh_cache(self, ctx):
        """Manually refresh the media cache."""
        refresh_message = await ctx.send("Refreshing media cache...")
        try:
            # Use our MediaCache class to update the cache
            await self.media_cache.update_cache()
            await refresh_message.edit(content="Media cache has been refreshed successfully.")
            logger.info("Media cache has been manually refreshed.")
        except Exception as e:
            logger.error(f"Failed to refresh media cache: {e}")
            await refresh_message.edit(content="Failed to refresh media cache. Check logs for details.")


def setup(bot):
    bot.add_cog(MediaCommands(bot))
