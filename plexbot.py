import asyncio
import json

import nextcord
from nextcord.ext import commands

import utilities as utils
import tautulli_wrapper as tautulli


# Load/create references to configs
CONFIG_DATA = json.load(open("config.json", "r"))
LOCAL_JSON = "map.json"

# Initialize Tautulli wrapper as tautulli
tautulli = tautulli.Tautulli()
tmdb = tautulli.TMDB()

# Initialize qbittorrentapi if qbit_ip is set
if CONFIG_DATA["qbit_ip"] != "":
    try:
        import qbittorrentapi
    except Exception as err:
        print(f"Error importing qbittorrentapi: {err}")


intents = nextcord.Intents.default()
# Need message_content for prefix commands
intents.message_content = True
# Need members for role changes in plex_top
intents.members = True

# Initialize bot with the prefix `plex ` and intents
# todo: work out which intents are ACTUALLY needed...
bot = commands.Bot(
    command_prefix=["plex ", "Plex "], intents=intents, help_command=None
)


async def status_task():
    var = 0
    while True:
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
                    name=f": plex help",
                )
            )
            var -= 1
        await asyncio.sleep(15)


@bot.event
async def on_ready() -> None:
    print(f"We have logged in as {bot.user}")
    bot.loop.create_task(status_task())


@bot.command()
async def mapdiscord(
    ctx, plex_username: str, discord_user: nextcord.User = None
) -> None:
    if plex_username is None or discord_user is None:
        await ctx.send(
            f"Please provide a plex username and discord user to map. Example: `plex mapd username @user`. Leave the discord user blank to map yourself."
        )
        return
    if discord_user is None:
        discord_user = ctx.author
    with open(LOCAL_JSON) as json_file:
        try:
            list_object = json.load(json_file)
        except json.JSONDecodeError:
            list_object = []
        for members in list_object:
            if members["discord_id"] == discord_user.id:
                await ctx.send(
                    f"You are already mapped, with the username: {members['plex_username']}."
                )
                # todo: allow users to be removed from the list
                return
            else:
                try:
                    list_object.append(
                        {"discord_id": discord_user.id, "plex_username": plex_username}
                    )
                    await ctx.send(
                        f"Successfully mapped {discord_user} to {plex_username}"
                    )
                    break
                except Exception as err:
                    await ctx.send(
                        f"Something went wrong, please try again or pass on this error {err}"
                    )

        with open(LOCAL_JSON, "w") as json_file:
            json.dump(list_object, json_file, indent=4, separators=(",", ": "))


@bot.command()
async def watchlist(ctx, member: nextcord.Member = None) -> None:
    response = tautulli.get_history()
    embed = nextcord.Embed(description="", color=0xE5A00D)
    embed.set_author(name="Plex Stats")
    last_watched_list = []
    if member is None:
        member = ctx.author
    with open(LOCAL_JSON) as json_file:
        try:
            dc_plex_json = json.load(json_file)
        except json.JSONDecodeError as err:
            await ctx.send(
                f":warning: seems like you have no users mapped? Error: {err}"
            )
        for members in dc_plex_json:
            if member.id != members["discord_id"]:
                continue
            for entries in response["response"]["data"]["data"]:
                if entries["user"] == members["plex_username"]:
                    last_watched_list.append(entries["full_title"])
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
            f"You are not mapped, use the command: `plex map_id [plex_username]`"
        )


@bot.command()
async def ignore(ctx, plex_username) -> None:
    # Don't show the user in the top list
    with open(LOCAL_JSON) as json_file:
        try:
            dc_plex_json = json.load(json_file)
        except json.JSONDecodeError:
            print(f"empty json")
        for members in dc_plex_json:
            if members["plex_username"] == plex_username:
                if members["ignore"] == True:
                    await ctx.send(
                        f":warning: {plex_username} is already ignored, undo with `plex unignore {plex_username}`"
                    )
                    break
                try:
                    members["ignore"] = True
                except Exception as err:
                    await ctx.send(f":warning: PROBLEM: {err}")
                await ctx.send(
                    f"{plex_username} will be ignored on plex top now, to undo this use the command: plex unignore"
                )
                with open(LOCAL_JSON, "w") as json_file:
                    json.dump(dc_plex_json, json_file, indent=4, separators=(",", ": "))
                break
        else:
            try:
                dc_plex_json.append(
                    {"discord_id": "", "plex_username": plex_username, "ignore": True}
                )
            except Exception as err:
                await ctx.send(
                    f":warning: TRIED TO ADD {plex_username} WITH NULL VALUES (NOT MAPPED) - PROBLEM: {err}"
                )
            await ctx.send(
                f"**{plex_username}** was not mapped, map, they have been inserted with null values and will be ignored on plex top now, to undo this use the command: `plex unignore {plex_username}`"
            )
        with open(LOCAL_JSON, "w") as json_file:
            json.dump(dc_plex_json, json_file, indent=4, separators=(",", ": "))


@bot.command()
async def unignore(ctx, plex_username) -> None:
    with open(LOCAL_JSON) as json_file:
        try:
            dc_plex_json = json.load(json_file)
        except json.JSONDecodeError:
            print(f"empty json")
        for members in dc_plex_json:
            if members["plex_username"] == plex_username and members["ignore"] == True:
                try:
                    members["ignore"] = False
                except Exception as err:
                    await ctx.send(f":warning: PROBLEM: {err}")
                await ctx.send(
                    f"{plex_username} will no longer be ignored on plex top now, to undo this use the command: `plex ignore {plex_username}`"
                )
                with open(LOCAL_JSON, "w") as json_file:
                    json.dump(dc_plex_json, json_file, indent=4, separators=(",", ": "))
                break
        else:
            await ctx.send(f"{plex_username} is not ignored, or not mapped")


async def clean_roles(ctx, top_users) -> None:
    guild = ctx.guild
    role_one, role_two, role_three = [
        guild.get_role(CONFIG_DATA["plex_top"]),
        guild.get_role(CONFIG_DATA["plex_two"]),
        guild.get_role(CONFIG_DATA["plex_three"]),
    ]
    for members in role_one.members:
        if top_users[1] == members.id:
            continue
        await members.remove_roles(role_one)
    for members in role_two.members:
        if top_users[2] == members.id:
            continue
        await members.remove_roles(role_two)
    for members in role_three.members:
        if top_users[3] == members.id:
            continue
        await members.remove_roles(role_three)


@bot.command()
async def assign_role(ctx, rank, user_id) -> None:
    guild = ctx.guild
    if rank == 1 and user_id is not None:
        nextcord_user = guild.get_member(user_id)
        role_one = guild.get_role(CONFIG_DATA["plex_top"])
        await nextcord_user.add_roles(role_one)
    elif rank == 2 and user_id is not None:
        nextcord_user = guild.get_member(user_id)
        role_two = guild.get_role(CONFIG_DATA["plex_two"])
        await nextcord_user.add_roles(role_two)
    elif rank == 3 and user_id is not None:
        nextcord_user = guild.get_member(user_id)
        role_three = guild.get_role(CONFIG_DATA["plex_three"])
        await nextcord_user.add_roles(role_three)


@bot.command()
async def top(ctx) -> None:
    params_home_stats = {
        "stats_type": "duration",
        "stat_id": "top_users",
        "stats_count": "10",
        "time_range": "7",
    }
    response = tautulli.get_home_stats(params=params_home_stats)
    i = 0
    embed = nextcord.Embed(color=0x9B59B6)
    embed.set_author(name="Plex Top (last 7 days watchtime)")
    embed.set_thumbnail(
        url="https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
    )
    top_users = {1: None, 2: None, 3: None}
    for entries in response["response"]["data"]["rows"]:
        username = entries["user"]
        for members in json.load(open(LOCAL_JSON)):
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
                            await assign_role(ctx, i, members["discord_id"])
                    break
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
    await clean_roles(ctx, top_users=top_users)
    await ctx.send(embed=embed)
    if i == 0:
        await ctx.send("No users found")


@bot.command()
# todo
# attention span = 0, I will fix this later :-)
async def recent(ctx, amount: int = None) -> None:
    fields = []
    # can't do above 15
    if amount is None or amount >= 15:
        if amount is None:
            amount = 4
            await ctx.send(
                f"Got no amount, defaulting to {amount} most recent additions"
            )
        else:
            amount = 14
            await ctx.szend(f"Can't do above 15, limiting to 14 🫡")
    response = tautulli.get_recently_added(count=amount)
    for entry in response["response"]["data"]["recently_added"]:
        fields.append(
            (
                f"{entry['title']}\nReleased: {entry['originally_available_at']}",
                f"**{entry['studio']}, RT rating: {entry['rating']}**\n{entry['summary']}",
            )
        )
    pages = utils.NoStopButtonMenuPages(
        source=utils.MyEmbedFieldPageSource(fields),
    )
    await pages.start(ctx)


@bot.command()
async def watchers(ctx) -> None:
    # todo: add ignored users check
    sessions = tautulli.get_activity()["response"]["data"]["sessions"]
    total_watchers = 0
    msg_list = []
    for users in sessions:
        total_watchers += 1
        msg_list.append(
            f"User **{users['friendly_name']}** is watching **{users['full_title']}** with quality: **{users['quality_profile']}**"
        )
    msg_list.insert(0, f"**{total_watchers}** users are currently watching Plex 🐒\n")
    await ctx.send(f"\n".join(msg_list))


# need to start using cogs soon hehe
@bot.command()
async def downloading(ctx):
    try:
        qbt_client = qbittorrentapi.Client(
            host=f"{CONFIG_DATA['qbit_ip']}",
            port=f"{CONFIG_DATA['qbit_port']}",
            username=f"{CONFIG_DATA['qbit_username']}",
            password=f"{CONFIG_DATA['qbit_password']}",
        )
    except Exception as err:
        print(
            f"Couldn't open connection to qbittorrent, check qBit related JSON values {err}"
        )
    dl_info = []
    # e.g. output:
    # Currently downloading:
    # debian-11.6.0-amd64-DVD-1.iso Progress: 46.12%, Size: 3.91 GB, ETA: 60 minutes, speed: 10.00 MB/s
    # todo: make an embed
    for downloads in qbt_client.torrents.info.downloading():
        str_ = f"`{downloads.name}` **Progress:** `{(downloads.progress * 100):.2f}%`, **Size:** `{downloads.size * 1e-9:.2f}` GB, **ETA:** `{downloads.eta / 60:.0f}` minutes, **speed:** `{downloads.dlspeed * 1.0e-6:.2f}` MB/s"
        dl_info.append(str_)
    dl_info.insert(0, "**Currently downloading:**")
    await ctx.send(f"\n\n".join(dl_info))


@bot.command()
async def help(ctx) -> None:
    help_embed = nextcord.Embed(
        title="Plex Utility Bot Commands - prefix is `plex`!",
        colour=nextcord.Colour(0xE5A00D),
    )
    help_embed.set_thumbnail(
        url="https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
    )
    help_embed.add_field(
        name="🎥 Commands",
        value="**`plex top`** - ranks top users by watchtime\n**`plex recent`** - shows most recent additions to Plex(WIP)\n**`plex watchers`** - shows who is currently watching Plex\n"
        "**`plex downloading`** - shows what is currently downloading\n**`plex watchlist [user_tag]`** - shows [user_tag]'s recent watches\n**`plex help`** - shows this message",
    )
    await ctx.send(embed=help_embed)


bot.run(CONFIG_DATA["token"])
