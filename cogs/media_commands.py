# cogs/media_commands.py

import asyncio
from datetime import timedelta
import json
import logging
import random
from pathlib import Path
from io import BytesIO
from functools import lru_cache

import aiofiles
import aiohttp
import nextcord
from nextcord.ext import commands, tasks
from nextcord import File

import utilities as utils
from tautulli_wrapper import Tautulli
from tautulli_wrapper import TMDB

# Configure logging for this module
logger = logging.getLogger('plexbot.media_commands')
logger.setLevel(logging.INFO)

class MediaCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.CONFIG_DATA = json.load(open("./config.json", "r"))
        self.tautulli = Tautulli()
        self.tmdb = TMDB()  # Assuming you use TMDB in your commands
        self.plex_embed_color = 0xE5A00D
        self.plex_image = (
            "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
        )
        self.media_cache = []
        self.cache_lock = asyncio.Lock()
        self.cache_file_path = Path("cache/media_cache.json")
        self.bot.loop.create_task(self.initialize())

        logger.info("MediaCommands cog initialized.")

    async def initialize(self):
        """Asynchronous initializer for the cog."""
        await self.bot.wait_until_ready()
        await self.tautulli.initialize()
        await self.tmdb.initialize()
        await self.load_cache_from_disk()
        self.update_media_cache.start()

    def cog_unload(self):
        self.update_media_cache.cancel()
        self.bot.loop.create_task(self.save_cache_to_disk())
        self.bot.loop.create_task(self.tautulli.close())
        self.bot.loop.create_task(self.tmdb.close())

    @tasks.loop(hours=1)
    async def update_media_cache(self):
        """Background task to update the media cache every hour."""
        async with self.cache_lock:
            logger.info("Updating media cache...")
            self.media_cache = await self.fetch_all_media_items()
            await self.save_cache_to_disk()
            logger.info("Media cache updated and saved to disk.")

    async def fetch_all_media_items(self):
        """Fetch all media items and their metadata, and store them in the cache."""
        all_media_items = []
        libraries = await self.get_libraries()
        logger.info(f"Starting to fetch media items from {len(libraries)} libraries.")

        for library in libraries:
            try:
                logger.info(
                    f"Fetching media items for library: {library['section_name']} (ID: {library['section_id']})"
                )
                response = await self.tautulli.get_library_media_info(
                    section_id=library["section_id"],
                    length=10000,  # Adjust as needed
                    include_metadata=0  # Since it doesn't include genres
                )
                if response.get("response", {}).get("result") != "success":
                    logger.error(
                        f"Failed to fetch media info for library {library['section_id']}"
                    )
                    continue

                media_items = response.get("response", {}).get("data", {}).get("data", [])
                if not media_items:
                    logger.info(
                        f"No media items found in library {library['section_name']}"
                    )
                    continue

                logger.info(
                    f"Processing {len(media_items)} items from library '{library['section_name']}'"
                )

                # Collect the rating keys
                rating_keys = [item["rating_key"] for item in media_items]

                # Limit the number of concurrent requests
                semaphore = asyncio.Semaphore(10)  # Limit to 10 concurrent requests

                # Define an async function to fetch metadata for an item
                async def fetch_item_metadata(rating_key):
                    async with semaphore:
                        logger.debug(f"Fetching metadata for rating_key: {rating_key}")
                        try:
                            metadata_response = await self.tautulli.get_metadata(rating_key=rating_key)
                            if metadata_response and metadata_response.get("response", {}).get("result") == "success":
                                metadata = metadata_response.get("response", {}).get("data", {})
                                genres = [genre.lower() for genre in metadata.get("genres", [])]

                                item_data = {
                                    "rating_key": rating_key,
                                    "title": metadata.get("title") or "Unknown Title",
                                    "media_type": (metadata.get("media_type") or "unknown").lower(),
                                    "genres": genres,
                                    "thumb": metadata.get("thumb"),
                                    "year": metadata.get("year"),
                                    "play_count": metadata.get("play_count", 0),
                                    "last_played": metadata.get("last_played"),
                                    "summary": metadata.get("summary", ""),
                                    "rating": metadata.get("rating", ""),
                                }
                                logger.debug(f"Metadata fetched for rating_key: {rating_key}")
                                return item_data
                            else:
                                logger.error(f"Failed to fetch metadata for rating_key {rating_key}")
                                return None
                        except Exception as e:
                            logger.error(f"Exception while fetching metadata for {rating_key}: {e}")
                            return None

                # Use asyncio.gather to fetch metadata concurrently with exception handling
                tasks = [fetch_item_metadata(rating_key) for rating_key in rating_keys]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Handle exceptions and filter out None results
                for idx, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(
                            f"Exception occurred while fetching metadata for rating_key {rating_keys[idx]}: {result}"
                        )
                    elif result:
                        all_media_items.append(result)

                # Yield control to the event loop
                await asyncio.sleep(0)

            except Exception as e:
                logger.exception(f"Error processing library {library['section_name']}: {e}")

        logger.info(f"Fetched total {len(all_media_items)} media items.")
        return all_media_items

    async def save_cache_to_disk(self):
        """Save the media cache to disk asynchronously."""
        cache_dir = self.cache_file_path.parent
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            logger.info(f"Saving media cache to {self.cache_file_path}")
            async with aiofiles.open(self.cache_file_path, "w", encoding="utf-8") as f:
                data_to_write = json.dumps(self.media_cache, ensure_ascii=False, indent=4)
                await f.write(data_to_write)
                logger.debug(f"Data to write: {data_to_write[:100]}...")  # Log first 100 chars
            logger.info(f"Media cache saved to {self.cache_file_path}")
        except Exception as e:
            logger.exception("Failed to save media cache to disk.")

    async def load_cache_from_disk(self):
        """Load the media cache from disk asynchronously."""
        if self.cache_file_path.exists():
            async with self.cache_lock:
                try:
                    async with aiofiles.open(self.cache_file_path, "r", encoding="utf-8") as f:
                        contents = await f.read()
                        self.media_cache = json.loads(contents)
                    logger.info(f"Media cache loaded from {self.cache_file_path}")
                except Exception as e:
                    logger.exception("Failed to load media cache from disk.")
                    self.media_cache = []
        else:
            logger.info("No media cache file found. Starting with an empty cache.")
            self.media_cache = []
            # Optionally, trigger an immediate cache update
            self.bot.loop.create_task(self.update_media_cache())

    @commands.command()
    async def recent(self, ctx, amount: int = 10):
        """Displays recently added media items."""
        fields = []
        try:
            response = await self.tautulli.get_recently_added(count=amount)
            for entry in response["response"]["data"]["recently_added"]:
                if entry.get("originally_available_at") == "":
                    continue
                # Work around to show full show name alongside episode name
                if entry.get("grandparent_title"):
                    entry["title"] = f"{entry['grandparent_title']} - {entry['title']}"
                if entry.get("rating") == "":
                    entry["rating"] = "nil"
                entry_data = {
                    "description": f"**🎥 {entry['title']}** 🕗 {entry['originally_available_at']} 🍅: {entry['rating']}/10\n{entry.get('summary', '')}\n",
                    "thumb_key": entry.get("thumb", ""),
                }
                fields.append(entry_data)

            tautulli_ip = self.tautulli.tautulli_ip  # Tautulli webserver IP
            pages = utils.NoStopButtonMenuPages(
                source=utils.MyEmbedDescriptionPageSource(fields, tautulli_ip),
            )
            await pages.start(ctx)
        except Exception as e:
            logger.error(f"Failed to retrieve recent additions: {e}")
            await ctx.send("Failed to retrieve recent additions.")

    @commands.command()
    async def random(self, ctx, *args):
        """Displays a random media item from the Plex libraries, optionally filtered by media type and genre.

        Usage:
        plex random [media_type] [genre]

        Examples:
        plex random
        plex random movie
        plex random tv comedy
        plex random movie horror
        """
        # Parse arguments
        media_type = None
        genre = None

        if args:
            # If first argument is 'movie', 'tv', or 'any', it's the media_type
            if args[0].lower() in ['movie', 'tv', 'any']:
                media_type = args[0].lower()
                if len(args) > 1:
                    genre = ' '.join(args[1:]).lower()
            else:
                # No media_type specified, treat all args as genre
                genre = ' '.join(args).lower()
        logger.info(f"Searching for {genre} of mediatype {media_type}")

        # Use the cached media items
        async with self.cache_lock:
            media_items = self.media_cache.copy()

        if not media_items:
            await ctx.send("Media cache is empty. Please try again later.")
            return

        # Filter media items by media type
        if media_type and media_type != 'any':
            if media_type == 'tv':
                valid_media_types = ['show', 'episode']
            elif media_type == 'movie':
                valid_media_types = ['movie']
            else:
                valid_media_types = [media_type]
            media_items = [
                item for item in media_items
                if item.get("media_type", "unknown").lower() in valid_media_types
            ]

        # Filter media items by genre
        if genre:
            media_items = [
                item for item in media_items
                if genre.lower() in [g.lower() for g in item.get("genres", [])]
            ]

        if not media_items:
            await ctx.send("No media items found matching the criteria.")
            return

        # Select a random media item
        random_item = random.choice(media_items)

        await self.send_movie_embed(ctx, random_item)

    async def send_movie_embed(self, ctx, item):
        """Send an embed with the media item's details."""
        try:
            # Construct the embed using item data
            embed = nextcord.Embed(
                title=f"{item['title']} ({item['year']})",
                color=nextcord.Color.random()
            )

            # Add summary
            if item.get("summary"):
                embed.add_field(name="Summary", value=item["summary"], inline=False)

            # Add rating
            if item.get("rating"):
                embed.add_field(name="Rating", value=item["rating"], inline=True)

            # Add genres
            if item.get("genres"):
                genres_formatted = ', '.join([g.title() for g in item["genres"]])
                embed.add_field(name="Genres", value=genres_formatted, inline=True)

            # Add play count
            play_count = item.get("play_count", 0)
            play_count = "Never" if play_count == 0 else str(play_count)
            embed.add_field(name="Play Count", value=play_count, inline=True)

            # Add last played
            if item.get("last_played"):
                last_played = f"<t:{item.get('last_played', '')}:D>"
                embed.add_field(name="Last Played", value=last_played, inline=True)

            # Add thumbnail if available
            if item.get("thumb"):
                thumb_url = self.construct_image_url(item["thumb"])
                if thumb_url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(thumb_url) as response:
                                if response.status == 200:
                                    image_data = BytesIO(await response.read())
                                    file = File(fp=image_data, filename="image.jpg")
                                    embed.set_image(url="attachment://image.jpg")
                                    await ctx.send(file=file, embed=embed)
                                    return
                                else:
                                    embed.add_field(
                                        name="Image",
                                        value="Failed to retrieve image.",
                                        inline=False,
                                    )
                    except Exception as e:
                        logger.error(f"Failed to retrieve thumbnail image: {e}")
                        embed.add_field(
                            name="Image",
                            value="Failed to retrieve image.",
                            inline=False,
                        )

            # If no image was sent
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send movie embed: {e}")
            await ctx.send("Failed to display the media item.")

    def construct_image_url(self, thumb_key):
        """Construct the full image URL for thumbnails."""
        if thumb_key:
            tautulli_ip = self.tautulli.tautulli_ip
            return f"http://{tautulli_ip}/pms_image_proxy?img={thumb_key}&width=300&height=450&fallback=poster"
        return ""

    async def get_libraries(self, media_type=None):
        """Fetch all libraries from Tautulli and filter them by media type.

        Args:
            media_type (str, optional): 'movie', 'tv', or None

        Returns:
            list: A list of library dictionaries
        """
        response = await self.tautulli.get_libraries()
        if response.get("response", {}).get("result") == "success":
            libraries = response.get("response", {}).get("data", [])
            # Filter to include only libraries of the specified media_type
            if media_type == 'movie':
                filtered_libraries = [lib for lib in libraries if lib["section_type"] == "movie"]
            elif media_type == 'tv':
                filtered_libraries = [lib for lib in libraries if lib["section_type"] in ("show", "episode")]
            else:
                filtered_libraries = [lib for lib in libraries if lib["section_type"] in ("movie", "show", "episode")]
            logger.debug(f"Filtered libraries based on media_type '{media_type}': {[lib['section_name'] for lib in filtered_libraries]}")
            return filtered_libraries
        logger.error("Failed to fetch libraries from Tautulli.")
        return []

    @commands.command()
    async def watchers(self, ctx):
        """Display current Plex watchers with details about their activity."""
        try:
            response = await self.tautulli.get_activity()
            sessions = response["response"]["data"]["sessions"]
            if not sessions:
                await ctx.send("No one is currently watching Plex.")
                return

            # Load ignored users list using caching
            user_data = self.load_user_mappings()
            ignored_users = {
                user["plex_username"] for user in user_data if user.get("ignore", False)
            }

            total_watchers = 0
            embed = nextcord.Embed(title="Plex Watchers", color=self.plex_embed_color)
            embed.set_thumbnail(url=self.plex_image)

            for user in sessions:
                if user["username"] in ignored_users:
                    continue  # Skip ignored users

                total_watchers += 1
                state = user.get("state", "unknown").capitalize()
                state_symbol = {
                    "Playing": "▶️",
                    "Paused": "⏸️",
                }.get(state, state)

                view_offset = int(user.get("view_offset", 0))
                elapsed_time = str(timedelta(milliseconds=view_offset))

                embed.add_field(
                    name=user["friendly_name"],
                    value=f"Watching **{user['full_title']}**\nQuality: **{user['quality_profile']}**\nState: **{state_symbol}**\nElapsed Time: **{elapsed_time}**",
                    inline=False,
                )

            embed.description = (
                f"**{total_watchers}** users are currently watching Plex 🐒"
                if total_watchers > 0
                else "No one is active on Plex at the moment. 😔✊"
            )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to retrieve watchers: {e}")
            await ctx.send("Failed to retrieve watchers.")

    @lru_cache(maxsize=1)
    def load_user_mappings(self):
        """Load user mappings from the JSON file."""
        try:
            with open("map.json", "r", encoding="utf-8") as json_file:
                return json.load(json_file)
        except (json.JSONDecodeError, FileNotFoundError) as err:
            logger.error(f"Failed to load or decode JSON: {err}")
            return []

    @commands.command()
    async def downloading(self, ctx):
        """Display current live downloads from qBittorrent."""
        if not self.CONFIG_DATA.get("qbit_ip"):
            await ctx.send("qBittorrent is not configured.")
            logger.error("qBittorrent configuration missing.")
            return

        try:
            import qbittorrentapi
        except ImportError as err:
            logger.error(f"Error importing qbittorrentapi: {err}")
            await ctx.send("qbittorrentapi module is not installed.")
            return

        try:
            qbt_client = qbittorrentapi.Client(
                host=f"{self.CONFIG_DATA['qbit_ip']}",
                port=f"{self.CONFIG_DATA['qbit_port']}",
                username=f"{self.CONFIG_DATA['qbit_username']}",
                password=f"{self.CONFIG_DATA['qbit_password']}",
            )
            qbt_client.auth_log_in()
        except Exception as err:
            logger.error(
                f"Couldn't open connection to qbittorrent, check qBit related JSON values: {err}"
            )
            await ctx.send("Failed to connect to qBittorrent. Check configuration.")
            return

        try:
            torrents = qbt_client.torrents_info(status_filter='downloading')
            num_downloads = 0
            downloads_embed = nextcord.Embed(
                title="qBittorrent Live Downloads",
                color=0x6C81DF,
            )
            downloads_embed.set_thumbnail(
                url="https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/New_qBittorrent_Logo.svg/1200px-New_qBittorrent_Logo.svg.png"
            )
            for torrent in torrents:
                downloads_embed.add_field(
                    name=f"⏳ {torrent.name}",
                    value=f"**Progress**: {torrent.progress * 100:.2f}%, **Size:** {torrent.size * 1e-9:.2f} GB, **ETA:** {torrent.eta / 60:.0f} minutes, **DL:** {torrent.dlspeed * 1.0e-6:.2f} MB/s",
                    inline=False,
                )
                num_downloads += 1
            if num_downloads < 1:
                downloads_embed.add_field(
                    name="\u200b",
                    value="There is no movie currently downloading!",
                    inline=False,
                )
            await ctx.send(embed=downloads_embed)
        except Exception as e:
            logger.error(f"Failed to retrieve downloads from qBittorrent: {e}")
            await ctx.send("Failed to retrieve downloads from qBittorrent.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def refresh_cache(self, ctx):
        """Manually refresh the media cache."""
        await ctx.send("Refreshing media cache...")
        async with self.cache_lock:
            self.media_cache = await self.fetch_all_media_items()
            await self.save_cache_to_disk()
        await ctx.send("Media cache has been refreshed.")
        logger.info("Media cache has been manually refreshed.")

def setup(bot):
    bot.add_cog(MediaCommands(bot))
