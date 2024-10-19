# plex_commands.py

import asyncio
from datetime import timedelta
from io import BytesIO
import json
import random
from functools import lru_cache
from pathlib import Path
import aiohttp  # For asynchronous HTTP requests
import aiofiles  # For asynchronous file operations
import nextcord
from nextcord.ext import commands, tasks
from nextcord import File
import logging

import utilities as utils
from tautulli_wrapper import Tautulli, TMDB

# Load configuration
config = json.load(open("./config.json", "r"))

# Attempt to import qbittorrentapi if qbit_ip is provided
if config["qbit_ip"] != "":
    try:
        import qbittorrentapi
    except Exception as err:
        # Replace print with logging
        logging.error(f"Error importing qbittorrentapi: {err}")

# Configure logging for this module
logger = logging.getLogger('plexbot.plex_commands')
logger.setLevel(logging.INFO)  # Set to INFO level for production

# Ensure handlers are added if not already present
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class plex_bot(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.CONFIG_DATA = config
        self.LOCAL_JSON = "map.json"
        self.CONFIG_JSON = "config.json"
        self.tautulli = Tautulli()
        self.tmdb = TMDB()
        self.plex_embed_color = 0xE5A00D
        self.plex_image = (
            "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
        )
        self.media_cache = []
        self.cache_lock = asyncio.Lock()
        self.cache_file_path = Path("cache/media_cache.json")
        self.bot.loop.create_task(self.initialize())

        logger.info("Plex bot cog initialized.")

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

    @lru_cache(maxsize=1)
    def load_user_mappings(self):
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
        try:
            with open(self.LOCAL_JSON, "w", encoding="utf-8") as json_file:
                json.dump(data, json_file, indent=4)
            self.load_user_mappings.cache_clear()  # Invalidate the cache after updating the file
            logger.info("User mappings saved and cache cleared.")
        except Exception as e:
            logger.exception(f"Failed to save user mappings: {e}")

    async def status_task(self):
        """Background task to update the bot's presence."""
        display_streams = True  # Toggles between showing streams and help command
        while True:
            try:
                response = await self.tautulli.get_activity()
                stream_count = response["response"]["data"]["stream_count"]
                wan_bandwidth_mbps = round(
                    (response["response"]["data"]["wan_bandwidth"] / 1000), 1
                )

                if display_streams:
                    activity_text = (
                        f"{stream_count} streams at {wan_bandwidth_mbps} mbps"
                    )
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
    async def on_ready(self) -> None:
        try:
            r = await self.tautulli.get_home_stats()
            status = r["response"]["result"]

            local_commit, latest_commit = await self.check_version()
            up_to_date = ""
            if local_commit and latest_commit:
                up_to_date = (
                    "Version outdated. Consider running git pull"
                    if local_commit != latest_commit
                    else ""
                )

            if status == "success":
                logger.info(f"Logged in as {self.bot.user}")
                logger.info("Connection to Tautulli successful")
                logger.info(
                    f"Current PlexBot version ID: {local_commit if local_commit else 'unknown'}; latest: {latest_commit if latest_commit else 'unknown'}; {up_to_date}"
                )
                self.bot.loop.create_task(self.status_task())
            else:
                logger.critical(
                    f"Connection to Tautulli failed, result {status}"
                )
        except Exception as e:
            logger.error(f"Error during bot initialization: {e}")

    async def check_version(self):
        try:
            local_commit = utils.get_git_revision_short_hash()
            latest_commit = utils.get_git_revision_short_hash_latest()
            return local_commit, latest_commit
        except FileNotFoundError as err:
            logger.error(f"Failed to check git commit version: {err}")
            return None, None  # Ensure always returning a tuple
        except Exception as e:
            logger.error(f"Unexpected error during version check: {e}")
            return None, None

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
    async def history(self, ctx, *, identifier: str = None):
        """Prints a user's previously watched media. Usable with plex watchlist <@user> or <plex_username>."""
        member = None
        plex_username = None

        if identifier:
            try:
                # Try to convert the identifier to a member
                member = await commands.MemberConverter().convert(ctx, identifier)
            except commands.MemberNotFound:
                # If not a member, assume it's a Plex username
                plex_username = identifier

        response = await self.tautulli.get_history()
        if response["response"]["result"] != "success":
            await ctx.send("Failed to retrieve watch history from Plex.")
            logger.error("Failed to retrieve watch history from Tautulli.")
            return

        dc_plex_json = self.load_user_mappings()  # Use cached results

        if member:
            # Find Plex username by Discord member ID
            plex_user = next(
                (
                    item
                    for item in dc_plex_json
                    if str(item.get("discord_id")) == str(member.id)
                ),
                None,
            )

            if not plex_user:
                await ctx.send("The specified member is not mapped to a Plex user.")
                logger.warning(f"Member {member.display_name} not mapped to a Plex user.")
                return

            plex_username = plex_user.get("plex_username")

        # Show all history if no specific user is specified
        last_watched_list = [
            f"<t:{entry['date']}:t> {entry['full_title']} ({entry['duration'] // 60}m {entry['duration'] % 60}s) by {entry['user']}"
            for entry in response["response"]["data"]["data"]
            if not plex_username or entry["user"] == plex_username
        ]

        embed = nextcord.Embed(title="Plex Stats", color=self.plex_embed_color)
        if member:
            embed.set_author(
                name=f"Last watched by {member.display_name}",
                icon_url=member.display_avatar.url,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
        else:
            embed.set_author(
                name="Recent watch history",
                icon_url=self.bot.user.display_avatar.url,  # Use bot's avatar for universal history
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)  # Use bot's avatar for universal history

        embed.description = (
            "\n".join(last_watched_list)
            if last_watched_list
            else "No history found."
        )

        await ctx.send(embed=embed)

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

    @commands.command()
    async def top(self, ctx, set_default: int = None):
        """Displays top Plex users or sets the default duration for displaying stats."""
        if set_default is not None:
            self.CONFIG_DATA["default_duration"] = set_default
            try:
                async with aiofiles.open(self.CONFIG_JSON, "w", encoding="utf-8") as file:
                    await file.write(json.dumps(self.CONFIG_DATA, indent=4))
                await ctx.send(f"Default duration set to: **{set_default}** days.")
                logger.info(f"Default duration set to {set_default} days.")
            except Exception as e:
                logger.exception(f"Failed to set default duration: {e}")
                await ctx.send("Failed to set default duration.")
            return

        duration = self.CONFIG_DATA.get("default_duration", 7)
        response = await self.tautulli.get_home_stats(
            params={
                "stats_type": "duration",
                "stat_id": "top_users",
                "stats_count": "10",
                "time_range": duration,
            }
        )
        if response["response"]["result"] != "success":
            await ctx.send("Failed to retrieve top users.")
            logger.error("Failed to retrieve top users from Tautulli.")
            return

        embed = nextcord.Embed(
            title=f"Plex Top (last {duration} days)", color=self.plex_embed_color
        )
        total_watchtime = 0
        user_data = self.load_user_mappings()
        ignored_users = {
            user["plex_username"]: user
            for user in user_data
            if user.get("ignore", False)
        }

        top_users = {}
        for rank, entry in enumerate(response["response"]["data"]["rows"], 1):
            username = entry["user"]
            if username in ignored_users:
                continue

            if rank <= 3:
                discord_id = next(
                    (
                        user["discord_id"]
                        for user in user_data
                        if user.get("plex_username") == username
                    ),
                    None,
                )
                if discord_id:
                    top_users[rank] = discord_id
            watch_time_seconds = entry["total_duration"]
            total_watchtime += watch_time_seconds
            watch_time = utils.days_hours_minutes(watch_time_seconds)
            media_type = entry.get("media_type")
            media = (
                entry.get("grandchild_title")
                if media_type == "movie"
                else entry.get("grandparent_title", "No recent activity")
            )

            embed.add_field(
                name=f"#{rank} {username}",
                value=f"{watch_time}\n**{media}**",
                inline=True,
            )

        if not top_users:
            await ctx.send("No top users found or all are ignored.")
            logger.info("No top users found or all are ignored.")
            return

        total_watch_time_str = utils.days_hours_minutes(total_watchtime)
        history_data = await self.tautulli.get_history()
        total_duration_all_time = utils.days_hours_minutes(
            history_data["response"]["data"]["total_duration"]
        )
        embed.set_footer(
            text=f"Total Watchtime: {total_watch_time_str}\nAll time: {total_duration_all_time}"
        )

        await ctx.send(embed=embed)
        await self.clean_roles(ctx, top_users)

    async def clean_roles(self, ctx, top_users):
        """Remove roles based on new top users and reassign correctly."""
        role_ids = [
            self.CONFIG_DATA.get("plex_top"),
            self.CONFIG_DATA.get("plex_two"),
            self.CONFIG_DATA.get("plex_three"),
        ]
        roles = [ctx.guild.get_role(role_id) for role_id in role_ids if role_id]

        if not all(role for role in roles):
            logger.warning("Some roles could not be found. Check configuration.")
            return

        if not all(ctx.guild.me.top_role > role for role in roles if role):
            logger.warning("Bot does not have the necessary role hierarchy to manage all roles.")
            return

        # Fetch all members with any of the roles to ensure comprehensive management
        members_with_roles = set()
        for role in roles:
            if role:
                members_with_roles.update(role.members)
        # Remove all roles from members who should no longer have them
        for member in members_with_roles:
            if member.id not in top_users.values():
                try:
                    await member.remove_roles(*roles, reason="Removing non-top user roles.")
                    logger.info(f"Removed roles from {member.display_name}.")
                except Exception as e:
                    logger.error(f"Failed to remove roles from {member.display_name}: {e}")

        # Assign the correct roles to the new top users
        for rank, user_id in enumerate(top_users.values(), start=1):
            member = ctx.guild.get_member(user_id)
            if not member:
                logger.warning(f"Member with ID {user_id} not found in guild.")
                continue

            if rank <= len(role_ids):
                correct_role = roles[rank - 1]
                roles_to_remove = [role for role in roles if role != correct_role]
                try:
                    await member.add_roles(
                        correct_role, reason="Assigning new top user role."
                    )
                    await member.remove_roles(
                        *roles_to_remove, reason="Cleaning up other top roles."
                    )
                    logger.info(
                        f"Assigned role '{correct_role.name}' to {member.display_name} and removed other top roles."
                    )
                except Exception as e:
                    logger.error(f"Failed to assign roles to {member.display_name}: {e}")

    @commands.command()
    async def recent(self, ctx, amount: int = 10) -> None:
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
    async def watchers(self, ctx) -> None:
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

    @commands.command()
    async def downloading(self, ctx):
        """Display current live downloads from qBittorrent."""
        if not self.CONFIG_DATA.get("qbit_ip"):
            await ctx.send("qBittorrent is not configured.")
            logger.error("qBittorrent configuration missing.")
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
            # e.g. output:
            # debian-11.6.0-amd64-DVD-1.iso Progress: 46.12%, Size: 3.91 GB, ETA: 60 minutes, speed: 10.00 MB/s
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
    async def help(self, ctx, *commands: str):
        """Shows all commands available or detailed information about a specific command."""
        prefix = "plex "
        if not commands:
            embed = nextcord.Embed(
                title="Command List",
                color=self.plex_embed_color,
                description="Here's a list of all my commands:",
            )
            embed.set_thumbnail(url=self.plex_image)

            # Collecting commands and categorizing them by cog
            for cog_name, cog in sorted(
                self.bot.cogs.items(),
                key=lambda x: len(x[1].get_commands()),
                reverse=True,
            ):
                cog_commands = [
                    (
                        f"{prefix}{cmd.name} [{' '.join(cmd.aliases)}]"
                        if cmd.aliases
                        else f"{prefix}{cmd.name}"
                    )
                    for cmd in cog.get_commands()
                    if not cmd.hidden
                ]
                if cog_commands:
                    embed.add_field(
                        name=f"__**{cog_name}**__",
                        value="\n".join(cog_commands),
                        inline=False,
                    )

            embed.set_footer(text="Use plex help <command> for more info on a command.")
            await ctx.send(embed=embed)
        else:
            command_name = commands[0]
            cmd = self.bot.get_command(command_name)
            if not cmd:
                await ctx.send(f"Command not found: {command_name}")
                return

            # Build detailed command information
            embed = nextcord.Embed(
                title=f"{prefix}{cmd.name}",
                description=cmd.help or "No description provided.",
                color=self.plex_embed_color,
            )
            if cmd.aliases:
                embed.add_field(
                    name="Aliases", value=", ".join(cmd.aliases), inline=False
                )

            # Formatting parameters for usage display
            params = [
                f"<{key}>" if param.default is param.empty else f"[{key}]"
                for key, param in cmd.params.items()
                if key not in ("self", "ctx")
            ]
            if params:
                embed.add_field(
                    name="Usage",
                    value=f"{prefix}{cmd.name} {' '.join(params)}",
                    inline=False,
                )

            await ctx.send(embed=embed)

    @commands.command()
    async def status(self, ctx):
        """Displays the status of the Plex server and other related information."""
        try:
            # Getting Tautulli server info
            server_info_response = await self.tautulli.get_server_info()
            server_info = server_info_response["response"]

            # Fetching Plex status from Plex API asynchronously
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://status.plex.tv/api/v2/status.json"
                ) as response:
                    if response.status == 200:
                        json_response = await response.json()
                        plex_status = json_response["status"]["description"]
                    else:
                        plex_status = "Plex status unavailable"

            # Setting up the embed message with server information and Plex status
            embed = nextcord.Embed(
                title="Plex Server Details", colour=self.plex_embed_color
            )
            embed.set_thumbnail(url=self.plex_image)
            embed.add_field(name="Response", value=server_info["result"], inline=True)
            embed.add_field(
                name="Server Name", value=server_info["data"]["pms_name"], inline=True
            )
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
    async def killstream(
        self, ctx, session_key: str = None, message: str = None
    ) -> None:
        """Terminates a Plex stream based on the session key."""
        session_keys = []
        if session_key is None:
            activity = await self.tautulli.get_activity()
            sessions = activity["response"]["data"]["sessions"]
            for users in sessions:
                session_keys.append(
                    f"\n**Session key:** {users['session_key']} is: **{users['user']}**,"
                )
            await ctx.send(
                f"You provided no session keys, current users are: {''.join(session_keys)}\nYou can use `plex killstream [session_key] '[message]'` to kill a stream above;"
                "\nMessage will be passed to the user in a pop-up window on their Plex client.\n ⚠️ It is recommended to use 'apostrophes' around the message to avoid errors."
            )
            return
        try:
            r = await self.tautulli.terminate_session(session_key, message=message)
            if r == 400:
                await ctx.send(
                    f"Could not find a stream with **{session_key}** or another error occurred"
                )
                logger.warning(f"Failed to terminate session with key {session_key}.")
            elif r == 200:
                await ctx.send(
                    f"Killed stream with session_key: **{session_key}** and message if provided: **{message}**"
                )
                logger.info(f"Terminated session {session_key} with message: {message}")
            else:
                await ctx.send(
                    "An unexpected error occurred - check logs for more information."
                )
                logger.error(f"Unexpected response code {r} when terminating session {session_key}.")
        except Exception as e:
            logger.error(f"Exception occurred while terminating session {session_key}: {e}")
            await ctx.send("Failed to terminate the session due to an error.")

    @commands.command()
    async def shows(self, ctx):
        """Displays the top users by total watch time across all TV libraries."""
        try:
            # Get all TV libraries
            response = await self.tautulli.get_libraries()
            libraries = response["response"]["data"]
            tv_libraries = (
                library for library in libraries if library["section_type"] == "show"
            )

            # Get library user stats for each TV library
            top_users = {}
            for library in tv_libraries:
                section_id = library["section_id"]
                library_name = library["section_name"]
                response = await self.tautulli.get_library_user_stats(section_id=section_id)
                data = response["response"]["data"]
                for user_data in data:
                    username = user_data["username"]
                    total_time = user_data["total_time"]
                    if username not in top_users:
                        top_users[username] = {
                            "time": total_time,
                            "count": 1,
                            "libraries": [library_name],
                        }
                    else:
                        top_users[username]["time"] += total_time
                        top_users[username]["count"] += 1
                        top_users[username]["libraries"].append(library_name)

            # Sort users by total time watched and get top 10
            top_users = sorted(top_users.items(), key=lambda x: x[1]["time"], reverse=True)[
                :10
            ]

            # Create embed
            embed = nextcord.Embed(
                title="Top Users by Total Time Watched for All TV Libraries",
                color=self.plex_embed_color,
            )
            embed.set_thumbnail(
                url="https://www.freepnglogos.com/uploads/tv-png/tv-png-the-whole-enchilada-the-whole-enchilada-9.png"
            )
            # Add fields to embed
            for i, (username, data) in enumerate(top_users):
                time = str(timedelta(seconds=data["time"]))
                count = data["count"]
                libraries_str = ", ".join(data["libraries"])
                embed.add_field(
                    name=f"{i+1}. {username}",
                    value=f"**{time}** watched across {count} libraries;\n{libraries_str}",
                    inline=False,
                )

            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error while executing shows: {str(e)}")
            await ctx.send("An error occurred while fetching top shows.")

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

    def construct_image_url(self, thumb_key):
        """Construct the full image URL for thumbnails."""
        if thumb_key:
            tautulli_ip = self.tautulli.tautulli_ip
            return f"http://{tautulli_ip}/pms_image_proxy?img={thumb_key}&width=300&height=450&fallback=poster"
        return ""

    @commands.command()
    async def stats(self, ctx, time: int = 30):
        """Displays Plex server statistics for a given time range."""
        if not time:
            time = 30
        else:
            time = int(time)
        try:
            # Fetching data for the top three most watched movies and shows
            most_watched_movies_response = await self.tautulli.get_most_watched_movies(
                time_range=time
            )
            most_watched_shows_response = await self.tautulli.get_most_watched_shows(
                time_range=time
            )
            libraries_response = await self.tautulli.get_libraries_table()

            total_movies = 0
            total_shows = 0
            total_episodes = 0
            total_duration_seconds = 0  # Total duration in seconds

            if libraries_response.get("response", {}).get("result") == "success":
                library_data = libraries_response["response"]["data"].get("data", [])
                for library in library_data:
                    if library["section_type"] == "movie":
                        total_movies += int(library["count"])
                    elif library["section_type"] == "show":
                        total_shows += int(library["count"])
                        total_episodes += int(library["child_count"])
                    total_duration_seconds += int(library.get("duration", 0))

            total_duration = utils.days_hours_minutes(total_duration_seconds)

            embed = nextcord.Embed(
                title="🎬 Plex Server Stats 🎥",
                description=f"Overview of Plex for last {time} days.",
                color=0x1ABC9C,
            )

            # Handling most watched movies
            if (
                most_watched_movies_response.get("response", {}).get("result")
                == "success"
            ):
                movies = most_watched_movies_response["response"]["data"]["rows"]
                movie_text = ""
                for i, movie in enumerate(movies[:3], 1):  # Display top 3 movies
                    movie_title = movie["title"]
                    plays = movie["total_plays"]
                    unique_users = movie.get("users_watched", "N/A")
                    movie_text += f"{i}. **{movie_title}** | {plays} plays by {unique_users} people\n"
                embed.add_field(
                    name="Most Watched Movies", value=movie_text.strip(), inline=False
                )

            # Handling most watched shows
            if (
                most_watched_shows_response.get("response", {}).get("result")
                == "success"
            ):
                shows = most_watched_shows_response["response"]["data"]["rows"]
                show_text = ""
                for i, show in enumerate(shows[:3], 1):  # Display top 3 shows
                    show_title = show["title"]
                    plays = show["total_plays"]
                    unique_users = show.get("users_watched", "N/A")
                    show_text += f"{i}. **{show_title}** | {plays} plays by {unique_users} people\n"
                embed.add_field(
                    name="Most Watched Shows", value=show_text.strip(), inline=False
                )

            # General library stats
            embed.add_field(
                name="🎬 Total Movies", value=str(total_movies), inline=True
            )
            embed.add_field(
                name="📺 Total TV Shows", value=str(total_shows), inline=True
            )
            embed.add_field(
                name="📋 Total Episodes", value=str(total_episodes), inline=True
            )
            embed.add_field(
                name="⏳ Total Watched Duration", value=total_duration, inline=True
            )

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error while executing stats: {str(e)}")
            await ctx.send("An error occurred while fetching Plex stats.")

def setup(bot):
    bot.add_cog(plex_bot(bot))
