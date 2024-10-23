# cogs/plex_stats.py

import logging
from datetime import timedelta

import nextcord
from nextcord.ext import commands

import utilities as utils
from utilities import Config, UserMappings
from tautulli_wrapper import Tautulli

# Configure logging for this module
logger = logging.getLogger("plexbot.plex_stats")
logger.setLevel(logging.INFO)


class PlexStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.plex_embed_color = 0xE5A00D

    @commands.command()
    async def top(self, ctx, set_default: int = None):
        """Displays top Plex users or sets the default duration for displaying stats."""
        if set_default is not None:
            config_data = Config.load_config()
            config_data["default_duration"] = set_default
            Config.save_config(config_data)
            await ctx.send(f"Default duration set to: **{set_default}** days.")
            logger.info(f"Default duration set to {set_default} days.")
            return

        config_data = Config.load_config()
        duration = config_data.get("default_duration", 7)
        response = await self.tautulli.get_home_stats(
            params={
                "stats_type": "duration",
                "stat_id": "top_users",
                "stats_count": "10",
                "time_range": duration,
            }
        )
        if not response or response.get("response", {}).get("result") != "success":
            await ctx.send("Failed to retrieve top users.")
            logger.error("Failed to retrieve top users from Tautulli.")
            return

        embed = nextcord.Embed(title=f"Plex Top (last {duration} days)", color=self.plex_embed_color)
        total_watchtime = 0
        user_data = UserMappings.load_user_mappings()
        ignored_users = {user["plex_username"]: user for user in user_data if user.get("ignore", False)}

        top_users = {}
        for rank, entry in enumerate(response["response"]["data"]["rows"], 1):
            username = entry["user"]
            if username in ignored_users:
                continue

            if rank <= 3:
                discord_id = next(
                    (user["discord_id"] for user in user_data if user.get("plex_username") == username),
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
        total_duration_all_time = history_data["response"]["data"]["total_duration"]
        embed.set_footer(
            text=f"Total Watchtime: {total_watch_time_str}\nAll time: {total_duration_all_time}"
        )

        await ctx.send(embed=embed)
        await self.clean_roles(ctx, top_users)

    async def clean_roles(self, ctx, top_users):
        """Remove roles based on new top users and reassign correctly."""
        config_data = Config.load_config()
        role_ids = [
            config_data.get("plex_top"),
            config_data.get("plex_two"),
            config_data.get("plex_three"),
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
        for rank, user_id in top_users.items():
            member = ctx.guild.get_member(int(user_id))
            if not member:
                logger.warning(f"Member with ID {user_id} not found in guild.")
                continue

            if rank <= len(roles):
                correct_role = roles[rank - 1]
                roles_to_remove = [role for role in roles if role != correct_role]
                try:
                    await member.add_roles(correct_role, reason="Assigning new top user role.")
                    await member.remove_roles(*roles_to_remove, reason="Cleaning up other top roles.")
                    logger.info(
                        f"Assigned role '{correct_role.name}' to {member.display_name} and removed other top roles."
                    )
                except Exception as e:
                    logger.error(f"Failed to assign roles to {member.display_name}: {e}")

    @commands.command()
    async def stats(self, ctx, time: int = 30):
        """Displays Plex server statistics for a given time range."""
        if not time:
            time = 30
        else:
            time = int(time)
        try:
            # Fetching data for the top three most watched movies and shows
            most_watched_movies_response = await self.tautulli.get_most_watched_movies(time_range=time)
            most_watched_shows_response = await self.tautulli.get_most_watched_shows(time_range=time)
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
            if most_watched_movies_response.get("response", {}).get("result") == "success":
                movies = most_watched_movies_response["response"]["data"]["rows"]
                movie_text = ""
                for i, movie in enumerate(movies[:3], 1):  # Display top 3 movies
                    movie_title = movie["title"]
                    plays = movie["total_plays"]
                    unique_users = movie.get("users_watched", "N/A")
                    movie_text += f"{i}. **{movie_title}** | {plays} plays by {unique_users} people\n"
                embed.add_field(name="Most Watched Movies", value=movie_text.strip(), inline=False)

            # Handling most watched shows
            if most_watched_shows_response.get("response", {}).get("result") == "success":
                shows = most_watched_shows_response["response"]["data"]["rows"]
                show_text = ""
                for i, show in enumerate(shows[:3], 1):  # Display top 3 shows
                    show_title = show["title"]
                    plays = show["total_plays"]
                    unique_users = show.get("users_watched", "N/A")
                    show_text += f"{i}. **{show_title}** | {plays} plays by {unique_users} people\n"
                embed.add_field(name="Most Watched Shows", value=show_text.strip(), inline=False)

            # General library stats
            embed.add_field(name="🎬 Total Movies", value=str(total_movies), inline=True)
            embed.add_field(name="📺 Total TV Shows", value=str(total_shows), inline=True)
            embed.add_field(name="📋 Total Episodes", value=str(total_episodes), inline=True)
            embed.add_field(name="⏳ Total Watched Duration", value=total_duration, inline=True)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error while executing stats: {str(e)}")
            await ctx.send("An error occurred while fetching Plex stats.")

    @commands.command()
    async def shows(self, ctx):
        """Displays the top users by total watch time across all TV libraries."""
        try:
            # Get all TV libraries
            response = await self.tautulli.get_libraries()
            libraries = response["response"]["data"]
            tv_libraries = (library for library in libraries if library["section_type"] == "show")

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
            top_users = sorted(top_users.items(), key=lambda x: x[1]["time"], reverse=True)[:10]

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
    async def history(self, ctx, *, identifier: str = None):
        """Prints a user's previously watched media. Usable with plex history <@user> or <plex_username>."""
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

        user_data = UserMappings.load_user_mappings()

        if member:
            # Find Plex username by Discord member ID
            plex_user = next(
                (item for item in user_data if str(item.get("discord_id")) == str(member.id)),
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
            embed.set_thumbnail(
                url=self.bot.user.display_avatar.url
            )  # Use bot's avatar for universal history

        embed.description = "\n".join(last_watched_list) if last_watched_list else "No history found."

        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(PlexStats(bot))
