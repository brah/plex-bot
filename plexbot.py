import asyncio
from datetime import timedelta
from io import BytesIO
import json
import random
from functools import lru_cache
import aiohttp
import nextcord
from nextcord.ext import commands
from nextcord import File, Embed

import utilities as utils
import tautulli_wrapper as tautulli

intents = nextcord.Intents.default()
# Need message_content for prefix commands
intents.message_content = True
# Need members for role changes in plex_top
intents.members = True

# Initialize bot with the prefix `plex ` and intents
bot = commands.Bot(
    command_prefix=["plex ", "Plex "], intents=intents, help_command=None
)
config = json.load(open("config.json", "r"))

if config["qbit_ip"] != "":
    try:
        import qbittorrentapi
    except Exception as err:
        print(f"Error importing qbittorrentapi: {err}")

tautulli = tautulli.Tautulli()


class plex_bot(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.CONFIG_DATA = config
        self.LOCAL_JSON = "map.json"
        self.CONFIG_JSON = "config.json"
        self.tautulli = tautulli
        self.tmdb = self.tautulli.TMDB()
        self.plex_embed_color = 0xE5A00D
        self.plex_image = (
            "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
        )

    @lru_cache(maxsize=1)
    def load_user_mappings(self):
        try:
            with open(self.LOCAL_JSON, "r") as json_file:
                return json.load(json_file)
        except json.JSONDecodeError as err:
            print(f"Failed to load or decode JSON: {err}")  # Log error for debugging
            return []

    def save_user_mappings(self, data):
        with open(self.LOCAL_JSON, "w") as json_file:
            json.dump(data, json_file, indent=4)
        self.load_user_mappings.cache_clear()  # Invalidate the cache after updating the file

    async def status_task(self):
        display_streams = True  # Toggles between showing streams and help command
        while True:
            try:
                response = tautulli.get_activity()
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
                print(f"Error in status_task(): {e}")

            await asyncio.sleep(15)  # Control how often to update the status

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        try:
            r = tautulli.get_home_stats()
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
                print(f"Logged in as {self.bot.user}")
                print("Connection to Tautulli successful")
                print(
                    f"Current PlexBot version ID: {local_commit if local_commit else 'unknown'}; latest: {latest_commit if latest_commit else 'unknown'}; {up_to_date}"
                )
                self.bot.loop.create_task(self.status_task())
            else:
                print(
                    f"-- CRITICAL -- Connection to Tautulli failed, result {status} --"
                )
        except Exception as e:
            print(f"Error during bot initialization: {e}")

    async def check_version(self):
        try:
            local_commit = utils.get_git_revision_short_hash()
            latest_commit = utils.get_git_revision_short_hash_latest()
            return local_commit, latest_commit
        except FileNotFoundError as err:
            print(f"Failed to check git commit version: {err}")
            return None, None  # Ensure always returning a tuple

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
            if str(member["discord_id"]) == str(discord_user.id):
                if member["plex_username"] == plex_username:
                    await ctx.send(f"You are already mapped to {plex_username}.")
                    return
                else:
                    member["plex_username"] = plex_username
                    self.save_user_mappings(dc_plex_json)
                    await ctx.send(
                        f"Successfully updated mapping for {discord_user.display_name} to {plex_username}."
                    )
                    return

        # If user is not found, add them
        dc_plex_json.append(
            {"discord_id": discord_user.id, "plex_username": plex_username}
        )
        self.save_user_mappings(dc_plex_json)
        await ctx.send(
            f"Successfully mapped {discord_user.display_name} to {plex_username}."
        )

    @commands.command()
    async def watchlist(self, ctx, member: nextcord.Member = None):
        """Prints a user's previously watched media. Usable with plex watchlist <@user> (if mapped)."""
        if member is None:
            member = ctx.author

        response = tautulli.get_history()
        if response["response"]["result"] != "success":
            await ctx.send("Failed to retrieve watch history from Plex.")
            return

        dc_plex_json = self.load_user_mappings()  # Use cached results
        plex_user = next(
            (
                item
                for item in dc_plex_json
                if str(member.id) == str(item["discord_id"])
            ),
            None,
        )

        if not plex_user:
            await ctx.send("The specified member is not mapped to a Plex user.")
            return

        plex_username = plex_user["plex_username"]
        last_watched_list = [
            f"<t:{entry['date']}:t> {entry['full_title']} ({entry['duration'] // 60}m {entry['duration'] % 60}s)"
            for entry in response["response"]["data"]["data"]
            if entry["user"] == plex_username
        ]

        embed = nextcord.Embed(title="Plex Stats", color=self.plex_embed_color)
        embed.set_author(
            name=f"Last watched by {member.display_name}",
            icon_url=member.display_avatar.url,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.description = (
            "\n".join(last_watched_list)
            if last_watched_list
            else "No history found for this user."
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
            if member["plex_username"] == plex_username:
                member["ignore"] = not member.get("ignore", False)
                found = True
                status = "no longer" if not member["ignore"] else "now"
                await ctx.send(f"{plex_username} is {status} ignored in top lists.")
                break

        if not found:
            data.append(
                {"discord_id": "", "plex_username": plex_username, "ignore": True}
            )
            await ctx.send(f"{plex_username} is now ignored in top lists.")

        self.save_user_mappings(data)  # Save and clear cache

    @commands.command()
    async def top(self, ctx, set_default: int = None):
        """Displays top Plex users or sets the default duration for displaying stats."""
        if set_default is not None:
            self.CONFIG_DATA["default_duration"] = set_default
            with open(self.CONFIG_JSON, "w") as file:
                json.dump(self.CONFIG_DATA, file, indent=4)
            await ctx.send(f"Default duration set to: **{set_default}** days.")
            return

        duration = self.CONFIG_DATA.get("default_duration", 7)
        response = tautulli.get_home_stats(
            params={
                "stats_type": "duration",
                "stat_id": "top_users",
                "stats_count": "10",
                "time_range": duration,
            }
        )
        if response["response"]["result"] != "success":
            await ctx.send("Failed to retrieve top users.")
            return

        embed = nextcord.Embed(
            title=f"Plex Top (last {duration} days)", color=self.plex_embed_color
        )
        total_watchtime = 0
        top_users = {}
        user_data = self.load_user_mappings()
        ignored_users = {
            user["plex_username"]: user
            for user in user_data
            if user.get("ignore", False)
        }

        # Limit the number of top users processed
        rank = 0
        for entry in response["response"]["data"]["rows"]:
            if rank >= 3:  # Only process the top 3 users
                break
            username = entry["user"]
            if username in ignored_users:
                continue

            rank += 1
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

            user_info = next(
                (user for user in user_data if user["plex_username"] == username), None
            )
            if user_info and user_info.get("discord_id"):
                top_users[rank] = int(user_info["discord_id"])

        if not top_users:
            await ctx.send("No top users found or all are ignored.")
            return

        total_watch_time_str = utils.days_hours_minutes(total_watchtime)
        history_data = tautulli.get_history()
        embed.set_footer(
            text=f"Total Watchtime: {total_watch_time_str}\nAll time: {history_data['response']['data']['total_duration']}"
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
            print("Some roles could not be found. Check configuration.")
            return

        if not all(ctx.guild.me.top_role > role for role in roles if role):
            print("Bot does not have the necessary role hierarchy to manage all roles.")
            return

        # Fetch all members with any of the roles to ensure comprehensive management
        members_with_roles = set()
        for role in roles:
            if role:
                members_with_roles.update(role.members)
        print(top_users.values())
        # Remove all roles from members who should no longer have them
        for member in members_with_roles:
            if member.id not in top_users.values():
                await member.remove_roles(*roles, reason="Removing non-top user roles.")

        # Assign the correct roles to the new top users
        for rank, user_id in enumerate(top_users.values(), start=1):
            member = ctx.guild.get_member(user_id)
            if not member:
                print(f"Member with ID {user_id} not found in guild.")
                continue

            if rank <= len(role_ids):
                correct_role = roles[rank - 1]
                roles_to_remove = [role for role in roles if role != correct_role]
                await member.add_roles(
                    correct_role, reason="Assigning new top user role."
                )
                await member.remove_roles(
                    *roles_to_remove, reason="Cleaning up other top roles."
                )

    # much bigger plans for this command, but nextcord/discord's buttons/paginations are really harsh to implement freely :\
    # https://menus.docs.nextcord.dev/en/latest/ext/menus/pagination_examples/#paginated-embeds-using-descriptions
    @commands.command()
    async def recent(self, ctx, amount: int = 10) -> None:
        fields = []
        response = self.tautulli.get_recently_added(count=amount)
        for entry in response["response"]["data"]["recently_added"]:
            if entry["originally_available_at"] == "":
                continue
            # work around to show full show name alongside episode name
            if entry["grandparent_title"] != "":
                entry["title"] = f"{entry['grandparent_title']} - {entry['title']}"
            if entry["rating"] == "":
                entry["rating"] = "nil"
            entry["thumb_key"] = entry.get("thumb", "")
            entry_data = {
                "description": f"**🎥 {entry['title']}** 🕗 {entry['originally_available_at']} 🍅: {entry['rating']}/10\n{entry['summary']}\n",
                "thumb_key": entry.get("thumb", ""),
            }
            fields.append(entry_data)

        tautulli_ip = self.tautulli.tautulli_ip  # Tautulli webserver IP
        pages = utils.NoStopButtonMenuPages(
            source=utils.MyEmbedDescriptionPageSource(fields, tautulli_ip),
        )
        await pages.start(ctx)

    @commands.command()
    async def watchers(self, ctx) -> None:
        """Display current Plex watchers with details about their activity."""
        try:
            sessions = tautulli.get_activity()["response"]["data"]["sessions"]
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
                    "playing": "▶️",
                    "paused": "⏸️",
                }.get(state.lower(), state)

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
            await ctx.send(f"Failed to retrieve watchers: {e}")

    # need to start using cogs soon hehe
    @commands.command()
    async def downloading(self, ctx):
        try:
            qbt_client = qbittorrentapi.Client(
                host=f"{self.CONFIG_DATA['qbit_ip']}",
                port=f"{self.CONFIG_DATA['qbit_port']}",
                username=f"{self.CONFIG_DATA['qbit_username']}",
                password=f"{self.CONFIG_DATA['qbit_password']}",
            )
        except Exception as err:
            print(
                f"Couldn't open connection to qbittorrent, check qBit related JSON values {err}"
            )
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
        for downloads in qbt_client.torrents.info.downloading():
            downloads_embed.add_field(
                name=f"⏳ {downloads.name}",
                value=f"**Progress**: {downloads.progress * 100:.2f}%, **Size:** {downloads.size * 1e-9:.2f} GB, **ETA:** {downloads.eta / 60:.0f} minutes, **DL:** {downloads.dlspeed * 1.0e-6:.2f} MB/s",
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
            server_info = self.tautulli.get_server_info()["response"]

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
            await ctx.send(f"Failed to retrieve server status: {e}")

    @commands.command()
    async def killstream(
        self, ctx, session_key: str = None, message: str = None
    ) -> None:
        session_keys = []
        if session_key is None:
            activity = self.tautulli.get_activity()
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
        r = self.tautulli.terminate_session(session_key, message=message)
        if r == 400:
            await ctx.send(
                f"Could not find a stream with **{session_key}** or another error occured"
            )
        elif r == 200:
            await ctx.send(
                f"Killed stream with session_key: **{session_key}** and message if provided: **{message}**"
            )
        else:
            await ctx.send(
                "Something unaccounted for occured - Check console for some more info"
            )

    @commands.command()
    async def shows(self, ctx):
        # Get all TV libraries
        response = tautulli.get_libraries()
        libraries = response["response"]["data"]
        tv_libraries = (
            library for library in libraries if library["section_type"] == "show"
        )

        # Get library user stats for each TV library
        top_users = {}
        for library in tv_libraries:
            section_id = library["section_id"]
            library_name = library["section_name"]
            response = tautulli.get_library_user_stats(section_id=section_id)
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

    @commands.command()
    async def random(self, ctx, library_id=None):
        """Displays a random media item from a specified or random Plex library of type 'Movies' or 'TV Shows'."""
        libraries = await self.get_libraries()
        if not libraries:
            await ctx.send(
                "No suitable libraries found or failed to retrieve libraries."
            )
            return

        # If no library_id is specified, pick a random library from the filtered list
        if not library_id:
            library = random.choice(libraries)
        else:
            library = next(
                (lib for lib in libraries if str(lib["section_id"]) == str(library_id)),
                None,
            )
            if not library:
                await ctx.send(
                    f"No library found with ID {library_id} or it is not a 'Movie' or 'TV Show' library."
                )
                return

        response = self.tautulli.get_library_media_info(library["section_id"])
        if response.get("response", {}).get("result") != "success":
            await ctx.send("Failed to retrieve media from the library.")
            return

        movies = response.get("response", {}).get("data", {}).get("data", [])
        if not movies:
            await ctx.send("No media found in the selected library.")
            return

        random_movie = random.choice(movies)
        await self.send_movie_embed(ctx, random_movie, libraries)

    async def get_libraries(self):
        """Fetch all libraries from Tautulli and filter for only Movies and TV Shows."""
        response = self.tautulli.get_libraries()
        if response.get("response", {}).get("result") == "success":
            libraries = response.get("response", {}).get("data", [])
            # Filter to include only libraries of type 'movie' or 'show'
            filtered_libraries = [
                lib for lib in libraries if lib["section_type"] in ("movie", "show")
            ]
            return filtered_libraries
        return []

    async def send_movie_embed(self, ctx, movie, libraries):
        title = movie.get("title", "Unknown Title")
        year = movie.get("year", "Unknown")
        play_count = movie.get("play_count", 0)
        play_count = "Never" if play_count == 0 else str(play_count)
        last_played = (
            f"<t:{movie.get('last_played', '')}:D>" if movie.get("last_played") else ""
        )

        thumb_url = self.construct_image_url(movie.get("thumb", ""))
        embed = Embed(title=f"{title} ({year})", color=nextcord.Color.random())
        embed.add_field(name="Last Played", value=last_played)
        embed.add_field(name="Play Count", value=play_count)

        if thumb_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumb_url) as response:
                    if response.status == 200:
                        image_data = BytesIO(await response.read())
                        file = File(fp=image_data, filename="image.jpg")
                        embed.set_image(url="attachment://image.jpg")
                    else:
                        embed.add_field(
                            name="Image",
                            value="Failed to retrieve image.",
                            inline=False,
                        )

        # Display available libraries after the media embed
        # Sort libraries by section ID and format names in bold
        library_list = "\n".join(
            f"{lib['section_id']} - **{lib['section_name']}**"
            for lib in sorted(libraries, key=lambda x: int(x["section_id"]))
        )
        embed.add_field(
            name="Explore More Sections",
            value=f"Try `plex random <ID>`\n{library_list}",
            inline=False,
        )

        if thumb_url and "file" in locals():
            await ctx.send(file=file, embed=embed)
        else:
            await ctx.send(embed=embed)

    def construct_image_url(self, thumb_key):
        if thumb_key:
            tautulli_ip = self.tautulli.tautulli_ip
            return f"http://{tautulli_ip}/pms_image_proxy?img={thumb_key}&width=300&height=450&fallback=poster"
        return ""


bot.add_cog(plex_bot(bot))
bot.run(config["token"])
