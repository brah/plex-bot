import asyncio
import datetime
import json
import pprint

import nextcord
import requests
from nextcord.ext import commands


def days_hours_minutes(seconds):
    td = datetime.timedelta(seconds=seconds)
    days, hours, minutes = td.days, td.seconds // 3600, td.seconds // 60 % 60
    if days == 1:
        return f"{days} day, {hours} hours, {minutes} minutes"
    if days > 0:
        return f"{days} days, {hours} hours, {minutes} minutes"
    elif hours < 1 and days < 1:
        return f"{minutes} minutes"
    else:
        return f"{hours} hours, {minutes} minutes"


CONFIG_DATA = json.load(open("config.json", "r"))
LOCAL_JSON = "map.json"

intents = nextcord.Intents.all()

bot = commands.Bot(command_prefix=["plex ", "Plex "], intents=intents)

params_home_stats = {
    "stats_type": "duration",
    "stat_id": "top_users",
    "stats_count": "10",
    "time_range": "7",
}
params_get_history = {"include_activity": "0"}
params_recently_added = {"count": "5"}
API_URL = f"http://{CONFIG_DATA['tautulli_ip']}/api/v2?apikey={CONFIG_DATA['tautulli_apikey']}&cmd="
HOME_STATS = "get_home_stats"
GET_ACTIVITY = "get_activity"
RECENTLY_ADDED = "get_recently_added"
GET_HISTORY = "get_history"


async def status_task():
    while True:
        sessions = requests.get(API_URL + GET_ACTIVITY).json()
        stream_count = sessions["response"]["data"]["stream_count"]
        wan_bandwidth_mbps = round(
            (sessions["response"]["data"]["wan_bandwidth"] / 1000), 1
        )
        await bot.change_presence(
            activity=nextcord.Activity(
                type=nextcord.ActivityType.playing,
                name=f"{stream_count} streams at {wan_bandwidth_mbps} mbps",
            )
        )
        await asyncio.sleep(30)


@bot.event
async def on_ready() -> None:
    print(f"We have logged in as {bot.user}")
    bot.loop.create_task(status_task())


@bot.command()
async def mapd(ctx, plex_username: str, discord_user: nextcord.User = None) -> None:
    if discord_user is None:
        print(ctx.author)
        discord_user = ctx.author
    with open(LOCAL_JSON) as json_file:
        try:
            list_object = json.load(json_file)
        except json.JSONDecodeError:
            list_object = []
        for members in list_object:
            if members["discord_id"] == discord_user.id:
                await ctx.send(
                    f"You are already mapped, with the username: {members['plex_username']}"
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
    request = requests.get(API_URL + GET_HISTORY, params=GET_HISTORY).json()
    embed = nextcord.Embed(description="", color=0x9B59B6)
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
            for entries in request["response"]["data"]["data"]:
                if entries["user"] == members["plex_username"]:
                    last_watched_list.append(entries["full_title"])
            discord_member = await bot.fetch_user(members["discord_id"])
            if len(last_watched_list) <= 0:
                last_watched_list = ["No history found"]

    if embed:
        embed.set_thumbnail(url=f"{discord_member.display_avatar.url}")
        print("".join(last_watched_list))
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


@bot.command()
async def assign_role(ctx, rank, user_id) -> None:
    guild = ctx.guild
    # currently, if a non discord plex user is in the top 3,
    # the last user with the role will keep the role despite not being that position
    # todo :: fix this
    if rank == 1 and user_id is not None:
        nextcord_user = guild.get_member(user_id)
        role_one = guild.get_role(CONFIG_DATA["plex_top"])
        await nextcord_user.add_roles(role_one)
        for members in role_one.members:
            if members.id != user_id:
                await members.remove_roles(role_one)
    elif rank == 2 and user_id is not None:
        nextcord_user = guild.get_member(user_id)
        role_two = guild.get_role(CONFIG_DATA["plex_two"])
        await nextcord_user.add_roles(role_two)
        for members in role_two.members:
            if members.id != user_id:
                await members.remove_roles(role_two)
    elif rank == 3 and user_id is not None:
        nextcord_user = guild.get_member(user_id)
        role_three = guild.get_role(CONFIG_DATA["plex_three"])
        await nextcord_user.add_roles(role_three)
        for members in role_three.members:
            if members.id != user_id:
                await members.remove_roles(role_three)


@bot.command()
async def top(ctx) -> None:
    request = requests.get(API_URL + HOME_STATS, params=params_home_stats).json()
    i = 0
    embed = nextcord.Embed(color=0x9B59B6)
    embed.set_author(name="Plex Top (last 7 days watchtime)")
    embed.set_thumbnail(
        url="https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
    )
    for entries in request["response"]["data"]["rows"]:
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
                        value=f"{days_hours_minutes(duration)}\n **{media}**",
                        inline=True,
                    )
                    if i <= 3:
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
                    value=f"{days_hours_minutes(duration)}\n **{media}**",
                    inline=True,
                )
        history_data = requests.get(
            API_URL + GET_HISTORY, params=params_get_history
        ).json()
    embed.set_footer(
        text=f"Total Plex watchtime (all time): {history_data['response']['data']['total_duration']}"
    )

    await ctx.send(embed=embed)
    if i == 0:
        await ctx.send("No users found")


@bot.command()
# WIP
async def recent(ctx):
    request = requests.get(
        API_URL + RECENTLY_ADDED, params=params_recently_added
    ).json()
    pprint.pprint(request)


bot.run(CONFIG_DATA["token"])
