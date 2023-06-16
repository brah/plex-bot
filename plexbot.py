import asyncio
from datetime import timedelta
import json

import nextcord
from nextcord.ext import commands
import requests

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
        self.plex_embed = 0xE5A00D
        self.plex_image = (
            "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
        )

    async def status_task(self):
        var = 0
        while True:
            try:
                response = tautulli.get_activity()
                stream_count = response["response"]["data"]["stream_count"]
                wan_bandwidth_mbps = round(
                    (response["response"]["data"]["wan_bandwidth"] / 1000), 1
                )
                if var == 0:
                    await bot.change_presence(
                        activity=nextcord.Activity(
                            type=nextcord.ActivityType.playing,
                            name=f"{stream_count} streams at {wan_bandwidth_mbps} mbps",
                        )
                    )
                    var += 1
                else:
                    await bot.change_presence(
                        activity=nextcord.Activity(
                            type=nextcord.ActivityType.listening,
                            name=": plex help",
                        )
                    )
                    var -= 1
            except Exception as e:
                print(f"Error in status_task(): {e}")
            await asyncio.sleep(15)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        r = tautulli.get_home_stats()
        status = r["response"]["result"]
        try:
            local_commit, latest_commit = (
                utils.get_git_revision_short_hash(),
                utils.get_git_revision_short_hash_latest(),
            )
            if local_commit != latest_commit:
                up_to_date = "Version outdated. Consider running git pull"
            else:
                up_to_date = ""
        except FileNotFoundError as err:
            print(
                f"Tried to get git commit, but failed: {err}, check in on https://github.com/brah/plex-bot/commits/main once a while for new changes :-)"
            )

        if status == "success":
            print("Connection to Tautulli successful")
        else:
            print(f"-- CRITICAL -- Connection to Tautulli failed, result {status} --")
        print(
            f"Current PlexBot version ID: {local_commit}; latest: {latest_commit}; {up_to_date}\n"
            f"We have logged in as {bot.user}"
        )
        bot.loop.create_task(self.status_task())

    @commands.command()
    async def mapdiscord(
        self, ctx, plex_username: str, discord_user: nextcord.User = None
    ) -> None:
        # Input validation
        if not plex_username.strip():
            await ctx.send("Please provide a valid plex username.")
            return
        try:
            discord_user = discord_user or ctx.author
        except AttributeError:
            await ctx.send("Please provide a valid discord user.")
            return

        with open(self.LOCAL_JSON) as json_file:
            try:
                list_object = json.load(json_file)
            except json.JSONDecodeError:
                list_object = []

            for members in list_object:
                if members["discord_id"] == discord_user.id:
                    if members["plex_username"] == plex_username:
                        await ctx.send(f"You are already mapped to {plex_username}.")
                    else:
                        members["plex_username"] = plex_username
                        with open(self.LOCAL_JSON, "w+") as json_file:
                            json.dump(
                                list_object, json_file, indent=4, separators=(",", ": ")
                            )
                        await ctx.send(
                            f"Successfully updated mapping for {discord_user} to {plex_username}."
                        )
                    return

            # Add new user
            list_object.append(
                {
                    "discord_id": discord_user.id,
                    "plex_username": plex_username,
                }
            )
            with open(self.LOCAL_JSON, "w+") as json_file:
                json.dump(list_object, json_file, indent=4, separators=(",", ": "))
            await ctx.send(f"Successfully mapped {discord_user} to {plex_username}.")

    @commands.command()
    async def watchlist(self, ctx, member: nextcord.Member = None) -> None:
        response = tautulli.get_history()
        embed = nextcord.Embed(description="", color=self.plex_embed)
        embed.set_author(name="Plex Stats")
        last_watched_list = []
        if member is None:
            member = ctx.author
        with open(self.LOCAL_JSON) as json_file:
            try:
                dc_plex_json = json.load(json_file)
            except json.JSONDecodeError as err:
                await ctx.send(
                    f":warning: seems like you have no users mapped? Error: {err}"
                )
            for members in dc_plex_json:
                if member.id != members["discord_id"]:
                    continue
                for entry in response["response"]["data"]["data"]:
                    if entry["user"] == members["plex_username"]:
                        session_duration = entry["duration"] - entry["paused_counter"]
                        duration_str = (
                            f"{session_duration // 60}m {session_duration % 60}s"
                        )
                        last_watched_list.append(
                            f"<t:{entry['date']}:t> {entry['full_title']} ({duration_str})"
                        )
                if len(last_watched_list) <= 0:
                    last_watched_list = ["No history found"]
                discord_member = await bot.fetch_user(members["discord_id"])
        if embed:
            embed.set_thumbnail(url=f"{discord_member.display_avatar.url}")
            embed.add_field(
                name=f"Last watched by: {discord_member.name}",
                value="\n".join(last_watched_list),
                inline=False,
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(
                "You are not mapped, use the command: `plex map_id [plex_username]`"
            )

    @commands.command()
    async def ignore(self, ctx, plex_username) -> None:
        # Don't show the user in the top list
        if not plex_username.strip():
            await ctx.send("Please provide a valid plex username.")
            return

        with open(self.LOCAL_JSON) as json_file:
            try:
                dc_plex_json = json.load(json_file)
            except json.JSONDecodeError:
                dc_plex_json = []

            for members in dc_plex_json:
                if members["plex_username"] == plex_username:
                    if members.get("ignore"):
                        await ctx.send(
                            f":warning: {plex_username} is already ignored, undo with `plex unignore {plex_username}`"
                        )
                    else:
                        members["ignore"] = True
                        await ctx.send(
                            f"{plex_username} will be ignored on plex top now, to undo this use the command: `plex unignore {plex_username}`"
                        )
                    break
            else:
                dc_plex_json.append(
                    {
                        "discord_id": "",
                        "plex_username": plex_username,
                        "ignore": True,
                    }
                )
                await ctx.send(
                    f"**{plex_username}** was not mapped, they have been inserted with null values and will be ignored on plex top now, to undo this use the command: `plex unignore {plex_username}`"
                )

            with open(self.LOCAL_JSON, "w") as json_file:
                json.dump(dc_plex_json, json_file, indent=4, separators=(",", ": "))

    @commands.command()
    async def unignore(self, ctx, plex_username) -> None:
        if not plex_username.strip():
            await ctx.send("Please provide a valid plex username.")
            return

        with open(self.LOCAL_JSON) as json_file:
            try:
                dc_plex_json = json.load(json_file)
            except json.JSONDecodeError:
                dc_plex_json = []

            for members in dc_plex_json:
                if members["plex_username"] == plex_username and members.get("ignore"):
                    members["ignore"] = False
                    await ctx.send(
                        f"{plex_username} will no longer be ignored on plex top now, to undo this use the command: `plex ignore {plex_username}`"
                    )
                    with open(self.LOCAL_JSON, "w") as json_file:
                        json.dump(
                            dc_plex_json, json_file, indent=4, separators=(",", ": ")
                        )
                    break
            else:
                await ctx.send(f"{plex_username} is not ignored, or not mapped")

    async def clean_roles(self, guild, top_users) -> None:
        role_ids = [
            self.CONFIG_DATA["plex_top"],
            self.CONFIG_DATA["plex_two"],
            self.CONFIG_DATA["plex_three"],
        ]
        roles = [guild.get_role(role_id) for role_id in role_ids]
        if any(role is None for role in roles):
            return
        for i, role in enumerate(roles):
            for member in role.members:
                if member.id != top_users[i + 1]:
                    await member.remove_roles(role)

    @commands.command()
    async def assign_role(self, ctx, rank, user_id) -> None:
        guild = ctx.guild
        nextcord_user = guild.get_member(user_id)
        if rank == 1:
            role = guild.get_role(self.CONFIG_DATA["plex_top"])
        elif rank == 2:
            role = guild.get_role(self.CONFIG_DATA["plex_two"])
        elif rank == 3:
            role = guild.get_role(self.CONFIG_DATA["plex_three"])
        else:
            return

        await nextcord_user.add_roles(role)

    @commands.command()
    async def top(self, ctx, set_default: int = None) -> None:
        try:
            duration = self.CONFIG_DATA["default_duration"]
        except KeyError:
            if set_default is None:
                duration = 7
            with open(self.CONFIG_JSON, "w") as json_file:
                self.CONFIG_DATA["default_duration"] = duration
                json.dump(self.CONFIG_DATA, json_file, indent=4, separators=(",", ": "))
                print("No duration passed and no default duration set, setting to 7")
        if set_default is not None:
            try:
                if self.CONFIG_DATA["default_duration"] == set_default:
                    await ctx.send(f"Default duration is already set to {set_default}")
                else:
                    self.CONFIG_DATA["default_duration"] = set_default
                    with open(self.CONFIG_JSON, "w") as json_file:
                        json.dump(
                            self.CONFIG_DATA,
                            json_file,
                            indent=4,
                            separators=(",", ": "),
                        )
                        await ctx.send(
                            f"Default duration set to: **{set_default}**; to revert use `plex top 7`"
                        )
            except KeyError:  #  it doesn't exist, but the user passed a value, let's create and set it
                with open(self.CONFIG_JSON, "w") as json_file:
                    self.CONFIG_DATA["default_duration"] = set_default
                    json.dump(
                        self.CONFIG_DATA, json_file, indent=4, separators=(",", ": ")
                    )
        duration = self.CONFIG_DATA["default_duration"]
        params_home_stats = {
            "stats_type": "duration",
            "stat_id": "top_users",
            "stats_count": "10",
            "time_range": duration,
        }
        response = tautulli.get_home_stats(params=params_home_stats)
        i = 0
        embed_file = nextcord.File("img/plexcrown.png")
        embed = nextcord.Embed(color=self.plex_embed)
        embed.set_author(name=f"Plex Top (last {duration} days watchtime)")
        embed.set_thumbnail(url=f"attachment://{embed_file.filename}")
        top_users = {1: None, 2: None, 3: None}
        for entries in response["response"]["data"]["rows"]:
            username = entries["user"]
            try:
                for members in json.load(open(self.LOCAL_JSON)):
                    if (
                        members.get("ignore") and members["ignore"] == False
                    ) or not members.get("ignore"):
                        if members["plex_username"] == username:
                            i = i + 1
                            duration = entries["total_duration"]
                            movie_or_show = entries["media_type"]
                            media = (
                                entries["grandchild_title"]
                                if (movie_or_show == "movie")
                                else entries["grandparent_title"]
                            )
                            embed.add_field(
                                name=f"#{i}. {username}",
                                value=f"{utils.days_hours_minutes(duration)}\n **{media}**",
                                inline=True,
                            )
                            if i <= 3:
                                if members["discord_id"] is not None:
                                    top_users[i] = members["discord_id"]
                                    await self.assign_role(
                                        ctx, i, members["discord_id"]
                                    )
                            break
            except FileNotFoundError as err:
                print(f"File not found: {err}")
            else:
                # non mapped users (i.e. those without Discord)
                if (
                    members.get("ignore") and members["ignore"] == False
                ) or username not in members["plex_username"]:
                    i = i + 1
                    duration = entries["total_duration"]
                    movie_or_show = entries["media_type"]
                    media = (
                        entries["grandchild_title"]
                        if (movie_or_show == "movie")
                        else entries["grandparent_title"]
                    )
                    embed.add_field(
                        name=f"#{i}. {username}",
                        value=f"{utils.days_hours_minutes(duration)}\n **{media}**",
                        inline=True,
                    )
        history_data = tautulli.get_history()
        embed.set_footer(
            text=f"Total Plex watchtime (all time): {history_data['response']['data']['total_duration']}"
        )
        if i < 1:
            await ctx.send(
                "No users found; use `plex mapdiscord plex_username @discord_user` to map yourself or other users."
            )
        else:
            await self.clean_roles(ctx.guild, top_users=top_users)
            await ctx.send(embed=embed, file=embed_file)

    # much bigger plans for this command, but nextcord/discord's buttons/paginations are really harsh to implement freely :\
    # https://menus.docs.nextcord.dev/en/latest/ext/menus/pagination_examples/#paginated-embeds-using-descriptions
    @commands.command()
    async def recent(self, ctx, amount: int = 10) -> None:
        fields = []
        response = tautulli.get_recently_added(count=amount)
        for entry in response["response"]["data"]["recently_added"]:
            if entry["originally_available_at"] == "":
                continue
            # work around to show full show name alongside episode name
            if entry["grandparent_title"] != "":
                entry["title"] = f"{entry['grandparent_title']} - {entry['title']}"
            if entry["rating"] == "":
                entry["rating"] = "nil"
            fields.append(
                (
                    f"**🎥 {entry['title']}** 🕗 {entry['originally_available_at']} 🍅: {entry['rating']}/10\n{entry['summary']}\n"
                )
            )
        pages = utils.NoStopButtonMenuPages(
            source=utils.MyEmbedDescriptionPageSource(fields),
        )
        await pages.start(ctx)

    @commands.command()
    async def watchers(self, ctx) -> None:
        # todo: add ignored users check
        sessions = tautulli.get_activity()["response"]["data"]["sessions"]
        total_watchers = 0
        msg_list = []
        for users in sessions:
            total_watchers += 1
            msg_list.append(
                f"User **{users['friendly_name']}** is watching **{users['full_title']}** with quality: **{users['quality_profile']}**"
            )
        msg_list.insert(
            0, f"**{total_watchers}** users are currently watching Plex 🐒\n"
        )
        await ctx.send("\n".join(msg_list))

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
    async def help(self, ctx) -> None:
        help_embed = nextcord.Embed(
            title="Plex Utility Bot Commands - prefix is `plex`!",
            colour=nextcord.Colour(self.plex_embed),
        )
        help_embed.set_thumbnail(url=self.plex_image)
        help_embed.add_field(
            name="🎥 Commands",
            value="**`plex top`** - ranks top users by watchtime\n**`plex recent`** - shows most recent additions to Plex(WIP)\n**`plex watchers`** - shows who is currently watching Plex\n"
            "**`plex downloading`** - shows what is currently downloading\n**`plex watchlist [user_tag]`** - shows [user_tag]'s recent watches\n**`plex help`** - shows this message\n**`plex status`** - shows some details of Plex + Plex's own server status",
        )
        await ctx.send(embed=help_embed)

    @commands.command()
    async def status(self, ctx) -> None:
        r = self.tautulli.get_server_info()
        # can put this in utils.py
        plex_servers = requests.get("https://status.plex.tv/api/v2/status.json").json()['status']['description']
        server_info = r["response"]
        server_embed = nextcord.Embed(
            title="Plex Server Details",
            colour=nextcord.Colour(self.plex_embed),
        )
        server_embed.set_thumbnail(url=self.plex_image)
        server_embed.add_field(
            name="Response", value=f"{server_info['result']}", inline=True
        )
        server_embed.add_field(
            name="Server Name", value=f"{server_info['data']['pms_name']}", inline=True
        )
        server_embed.add_field(
            name="Server Version",
            value=f"{server_info['data']['pms_version']}",
            inline=True,
        )
        server_embed.add_field(
            name="Server IP",
            value=f"{server_info['data']['pms_ip']}:{server_info['data']['pms_port']}",
        )
        server_embed.add_field(
            name="Platform", value=f"{server_info['data']['pms_platform']}"
        )
        server_embed.add_field(
            name="Plex Pass", value=f"{server_info['data']['pms_plexpass']}"
        )
        server_embed.add_field(
            name="Plex API Status", value=f"{plex_servers}"
        )
        await ctx.send(embed=server_embed)

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
            color=self.plex_embed,
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


bot.add_cog(plex_bot(bot))
bot.run(config["token"])
