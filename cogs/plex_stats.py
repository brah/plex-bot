# cogs/plex_stats.py

import logging
from datetime import timedelta, datetime

import nextcord
from nextcord.ext import commands

import utilities as utils
from utilities import UserMappings
from config import config
from tautulli_wrapper import Tautulli

# Configure logging for this module
logger = logging.getLogger("plexbot.plex_stats")
logger.setLevel(logging.INFO)


class PlexStats(commands.Cog):
    """Commands for displaying Plex server statistics and user activity."""

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.plex_embed_color = 0xE5A00D

    @commands.command()
    async def top(self, ctx, set_default: int = None):
        """
        Displays top Plex users or sets the default duration for displaying stats.

        Usage:
        plex top [days]

        If days is provided, sets that as the default duration.
        Otherwise, shows the top users based on watch time.
        """
        if set_default is not None:
            # Update with new config system
            config.set("defaults", "stats_duration", set_default)
            # Save the config
            config.save()
            await ctx.send(f"Default duration set to: **{set_default}** days.")
            logger.info(f"Default duration set to {set_default} days.")
            return

        # Use new config system to get duration
        duration = config.get("defaults", "stats_duration", 7)

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
                    # Store discord_id as an integer to ensure proper comparison later
                    top_users[rank] = int(discord_id)

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

        # Log the top users for debugging
        logger.info(f"Top users to assign roles: {top_users}")

        await self.clean_roles(ctx, top_users)

    async def clean_roles(self, ctx, top_users):
        """Remove roles based on new top users and reassign correctly."""
        # Use the new config system to get role IDs
        role_ids = [
            config.get("discord", "top_role_id"),
            config.get("discord", "second_role_id"),
            config.get("discord", "third_role_id"),
        ]

        # Add debug log
        logger.info(f"Role IDs from config: {role_ids}")

        roles = [ctx.guild.get_role(role_id) for role_id in role_ids if role_id]

        # Additional debug logs
        logger.info(f"Retrieved roles: {[role.name if role else None for role in roles]}")

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

        # Log the found members for debugging
        logger.info(f"Found {len(members_with_roles)} members with roles to manage")

        # For debugging, convert top_users values to a list for easier inspection
        top_user_ids = list(top_users.values())
        logger.info(f"Top user IDs (int): {top_user_ids}")

        # Remove all roles from members who should no longer have them
        for member in members_with_roles:
            # Convert member.id to int explicitly for consistency
            member_id = int(member.id)
            logger.info(f"Checking member {member.display_name} (ID: {member_id})")

            if member_id not in top_user_ids:
                try:
                    logger.info(f"Removing roles from {member.display_name} as they're not in top users")
                    await member.remove_roles(*roles, reason="Removing non-top user roles.")
                    logger.info(f"Successfully removed roles from {member.display_name}.")
                except Exception as e:
                    logger.error(f"Failed to remove roles from {member.display_name}: {e}")

        # Assign the correct roles to the new top users
        for rank, user_id in top_users.items():
            # Ensure user_id is an integer
            user_id = int(user_id)

            logger.info(f"Processing top user rank {rank} with ID {user_id}")
            member = ctx.guild.get_member(user_id)

            if not member:
                logger.warning(f"Member with ID {user_id} not found in guild.")
                continue

            if rank <= len(roles):
                correct_role = roles[rank - 1]
                roles_to_remove = [role for role in roles if role != correct_role]

                try:
                    logger.info(f"Adding role {correct_role.name} to {member.display_name}")
                    await member.add_roles(correct_role, reason="Assigning new top user role.")

                    if roles_to_remove:
                        logger.info(f"Removing other top roles from {member.display_name}")
                        await member.remove_roles(*roles_to_remove, reason="Cleaning up other top roles.")

                    logger.info(
                        f"Successfully assigned role '{correct_role.name}' to {member.display_name} and removed other top roles."
                    )
                except Exception as e:
                    logger.error(f"Failed to assign roles to {member.display_name}: {e}")

    @commands.command()
    async def stats(self, ctx, time: int = 30):
        """
        Displays Plex server statistics for a given time range.

        Usage:
        plex stats [days]

        Shows statistics like most watched movies and shows for the specified time period.
        """
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
        """
        Displays the top users by total watch time across all TV libraries.

        Usage:
        plex shows
        """
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
        """
        Displays a user's previously watched media.

        Usage:
        plex history [@user]
        plex history [plex_username]

        Shows the watch history for the specified user or all users if none is specified.
        """
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

    @commands.command()
    async def hot(self, ctx, time: int = 7):
        """
        Shows fun facts and trending content from the last few days on your Plex server.

        Usage:
        plex hot [days]

        Finds insights like:
        - Multiple users watching the same content
        - Popular new releases
        - Most active users and their favorites
        - Unusual viewing patterns

        Default is 7 days if not specified.
        """
        try:
            # Let user know we're working on it
            processing_msg = await ctx.send("📊 Analyzing Plex activity... This might take a moment!")

            # Start with basic data we need for all insights
            history_response = await self.tautulli.get_history(
                params={
                    "length": 1000,  # Get a good amount of history
                    "order_column": "date",
                    "order_dir": "desc",
                }
            )

            if history_response["response"]["result"] != "success":
                await ctx.send("Failed to retrieve watch history from Tautulli.")
                logger.error("Failed to retrieve watch history for hot command.")
                return

            history_entries = history_response["response"]["data"]["data"]

            # Process data for insights
            user_mapping = UserMappings.load_user_mappings()
            ignored_users = {user["plex_username"] for user in user_mapping if user.get("ignore", False)}

            # Filter history to specified time period
            cutoff_time = datetime.now() - timedelta(days=time)
            cutoff_timestamp = cutoff_time.timestamp()
            recent_history = [
                entry
                for entry in history_entries
                if entry["date"] >= cutoff_timestamp and entry["user"] not in ignored_users
            ]

            if not recent_history:
                await processing_msg.edit(content=f"No watch activity found in the last {time} days.")
                return

            # Create main embed
            embed = nextcord.Embed(
                title=f"🔥 What's Hot on Plex - Last {time} Days",
                description=f"Insights and trends from {len(recent_history)} plays",
                color=self.plex_embed_color,
            )

            # Get recently added content that was actually watched
            recently_added_response = await self.tautulli.get_recently_added(count=50)
            if recently_added_response["response"]["result"] == "success":
                recent_items = recently_added_response["response"]["data"]["recently_added"]
                # Filter to only items added in our time period
                recent_items = [
                    item for item in recent_items if float(item.get("added_at", 0)) >= cutoff_timestamp
                ]

                # Find which recent additions were actually watched
                watched_new_content = []
                for item in recent_items:
                    item_rating_key = str(item.get("rating_key"))
                    # Count plays for this item
                    plays = sum(
                        1 for entry in recent_history if str(entry.get("rating_key")) == item_rating_key
                    )
                    if plays > 0:
                        watched_new_content.append(
                            {
                                "title": item.get("title"),
                                "full_title": item.get("full_title", item.get("title")),
                                "plays": plays,
                                "media_type": item.get("media_type"),
                            }
                        )

                # Sort by play count
                watched_new_content.sort(key=lambda x: x["plays"], reverse=True)

                if watched_new_content:
                    hot_additions = "\n".join(
                        [
                            f"• **{item['full_title']}** - {item['plays']} play{'s' if item['plays'] != 1 else ''}"
                            for item in watched_new_content[:3]  # Top 3
                        ]
                    )
                    embed.add_field(
                        name="🆕 Hot New Additions",
                        value=hot_additions or "No recently added content was watched.",
                        inline=False,
                    )

            # Find content watched by multiple users
            content_by_users = {}
            for entry in recent_history:
                # Use grandparent_rating_key for TV shows to group by series
                key = entry.get("grandparent_rating_key", entry.get("rating_key"))
                title = entry.get("grandparent_title", entry.get("title"))

                if not key or not title:
                    continue

                if key not in content_by_users:
                    content_by_users[key] = {
                        "title": title,
                        "users": set(),
                        "count": 0,
                        "media_type": entry.get("media_type", "unknown"),
                    }

                content_by_users[key]["users"].add(entry["user"])
                content_by_users[key]["count"] += 1

            # Find items watched by multiple different users
            multi_user_content = [item for key, item in content_by_users.items() if len(item["users"]) > 1]

            # Sort by number of users then by play count
            multi_user_content.sort(key=lambda x: (len(x["users"]), x["count"]), reverse=True)

            if multi_user_content:
                trending_text = "\n".join(
                    [
                        f"• **{item['title']}** - Watched by {len(item['users'])} users ({item['count']} plays)"
                        for item in multi_user_content[:3]  # Top 3
                    ]
                )
                embed.add_field(name="📈 Trending Content", value=trending_text, inline=False)

            # Find users with unusual activity patterns
            user_activity = {}
            for entry in recent_history:
                user = entry["user"]
                if user not in user_activity:
                    user_activity[user] = {"plays": 0, "titles": set(), "media_types": {}}

                user_activity[user]["plays"] += 1
                user_activity[user]["titles"].add(entry.get("title"))

                media_type = entry.get("media_type", "unknown")
                if media_type not in user_activity[user]["media_types"]:
                    user_activity[user]["media_types"][media_type] = 0
                user_activity[user]["media_types"][media_type] += 1

            # Get interesting user stats
            if user_activity:
                # Most active users
                sorted_users = sorted(user_activity.items(), key=lambda x: x[1]["plays"], reverse=True)

                most_active_users = [
                    f"• **{user}** - {stats['plays']} plays ({len(stats['titles'])} different titles)"
                    for user, stats in sorted_users[:3]  # Top 3
                ]

                if most_active_users:
                    embed.add_field(
                        name="👥 Most Active Users", value="\n".join(most_active_users), inline=False
                    )

                # Find binge watchers (lots of episodes of same show)
                binge_watchers = []
                for user, stats in user_activity.items():
                    # Only look at users with significant activity
                    if stats["plays"] < 5:
                        continue

                    # Count consecutive plays of the same content
                    consecutive_plays = {}
                    for entry in recent_history:
                        if entry["user"] != user:
                            continue

                        # For TV, group by show
                        if entry.get("media_type") == "episode" and entry.get("grandparent_title"):
                            show_title = entry.get("grandparent_title")
                            if show_title not in consecutive_plays:
                                consecutive_plays[show_title] = 0
                            consecutive_plays[show_title] += 1

                    # Find shows with most consecutive plays
                    binge_shows = sorted(consecutive_plays.items(), key=lambda x: x[1], reverse=True)

                    if binge_shows and binge_shows[0][1] >= 5:  # At least 5 episodes
                        show, count = binge_shows[0]
                        binge_watchers.append(f"• **{user}** binged **{show}** ({count} episodes)")

                if binge_watchers:
                    embed.add_field(
                        name="🍿 Binge Watchers", value="\n".join(binge_watchers[:3]), inline=False  # Top 3
                    )

            # Try to find unique or "first time" watches
            first_watches = []
            for key, item in content_by_users.items():
                # Check if this content was added recently and only watched once or twice
                if 1 <= item["count"] <= 2 and len(item["users"]) == 1:
                    # See if we can find when it was added
                    for recent_item in recent_items:
                        if str(recent_item.get("rating_key")) == str(key):
                            # It was added recently and only watched once - likely a first watch
                            user = next(iter(item["users"]))
                            first_watches.append(f"• **{user}** checked out **{item['title']}**")
                            break

            if first_watches:
                embed.add_field(
                    name="🔍 Discovery Watch", value="\n".join(first_watches[:3]), inline=False  # Top 3
                )

            # Generate summary fun fact
            total_plays = len(recent_history)
            unique_titles = len(set(entry.get("title") for entry in recent_history if entry.get("title")))
            unique_users = len(set(entry.get("user") for entry in recent_history if entry.get("user")))

            # Count media types
            media_types = {}
            for entry in recent_history:
                media_type = entry.get("media_type", "unknown")
                if media_type not in media_types:
                    media_types[media_type] = 0
                media_types[media_type] += 1

            # Create fun fact
            fun_facts = []

            # Fact about movies vs TV
            if "movie" in media_types and "episode" in media_types:
                movie_count = media_types.get("movie", 0)
                episode_count = media_types.get("episode", 0)
                if movie_count > episode_count:
                    fun_facts.append(
                        f"Your server is big on movies! {movie_count} movies vs {episode_count} TV episodes."
                    )
                else:
                    fun_facts.append(
                        f"Your server loves TV! {episode_count} episodes vs {movie_count} movies."
                    )

            # Add a random fun fact based on the data
            if total_plays > 0:
                facts = [
                    f"On average, {total_plays/time:.1f} things were watched each day.",
                    f"Users explored {unique_titles} different titles over just {time} days!",
                    f"{unique_users} different users enjoyed content on your server.",
                ]
                import random

                fun_facts.append(random.choice(facts))

            if fun_facts:
                embed.add_field(name="💡 Fun Fact", value=fun_facts[0], inline=False)

            # Send the final result
            await processing_msg.edit(content=None, embed=embed)

        except Exception as e:
            logger.error(f"Error in hot command: {e}", exc_info=True)
            await ctx.send(f"An error occurred while analyzing hot content: {type(e).__name__}")


def setup(bot):
    bot.add_cog(PlexStats(bot))
