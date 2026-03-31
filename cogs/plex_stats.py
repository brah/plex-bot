# cogs/plex_stats.py

import logging
from datetime import timedelta, datetime

import nextcord
from nextcord.ext import commands

from utilities import UserMappings
from config import config
from tautulli_wrapper import Tautulli

# Configure logging for this module
logger = logging.getLogger("plexbot.plex_stats")
logger.setLevel(logging.INFO)


def _format_watch_duration(seconds: int) -> str:
    """Format seconds into a compact duration like '2d 5h 30m' or '5h 12m'."""
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


class PlexStats(commands.Cog):
    """Commands for displaying Plex server statistics and user activity."""

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.plex_embed_color = config.get("ui", "plex_embed_color", 0xE5A00D)

    @commands.command()
    async def top(self, ctx, set_default: int = None):
        """Displays top Plex users by watch time, with automatic role assignment.

        Usage:
        plex top [days]

        If days is provided, sets that as the default duration.
        Otherwise, shows the top users based on watch time.
        """
        if set_default is not None:
            if set_default < 1 or set_default > 3650:
                await ctx.send("Please provide a number of days between 1 and 3650.")
                return
            config.set("defaults", "stats_duration", set_default)
            config.save()
            await ctx.send(f"Default duration set to **{set_default}** days.")
            return

        duration = config.get("defaults", "stats_duration", 7)

        response = await self.tautulli.get_home_stats(
            params={
                "stats_type": "duration",
                "stat_id": "top_users",
                "stats_count": "10",
                "time_range": duration,
            }
        )
        if not Tautulli.check_response(response):
            await ctx.send("Failed to retrieve top users.")
            return

        rows = Tautulli.get_response_data(response, {}).get("rows", [])
        user_data = UserMappings.load_user_mappings()
        ignored_users = {u["plex_username"] for u in user_data if u.get("ignore", False)}

        # Build ranked list, skipping ignored users
        ranked = []
        for entry in rows:
            if entry["user"] in ignored_users:
                continue
            ranked.append(entry)

        if not ranked:
            await ctx.send("No top users found.")
            return

        # Build embed
        medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}
        embed = nextcord.Embed(
            title=f"Top Plex Users \u2014 Last {duration} Days",
            color=self.plex_embed_color,
        )

        total_watchtime = 0
        top_users = {}
        lines = []

        for rank, entry in enumerate(ranked, 1):
            username = entry["user"]
            watch_seconds = entry["total_duration"]
            total_watchtime += watch_seconds
            watch_str = _format_watch_duration(watch_seconds)

            # What they've been watching
            media_type = entry.get("media_type")
            if media_type == "movie":
                recent = entry.get("grandchild_title", "")
            else:
                recent = entry.get("grandparent_title", "")

            prefix = medal.get(rank, f"`#{rank}`")
            line = f"{prefix}  **{username}** \u2014 {watch_str}"
            if recent:
                line += f"\n\u2003\u2003\u25B8 {recent}"
            lines.append(line)

            # Track top 3 for role assignment
            if rank <= 3:
                discord_id = next(
                    (u["discord_id"] for u in user_data if u.get("plex_username") == username),
                    None,
                )
                if discord_id:
                    top_users[rank] = int(discord_id)

        embed.description = "\n".join(lines)

        # Footer with totals
        footer_parts = [f"Total: {_format_watch_duration(total_watchtime)}"]
        history_resp = await self.tautulli.get_history()
        if Tautulli.check_response(history_resp):
            all_time = Tautulli.get_response_data(history_resp, {}).get("total_duration")
            if all_time:
                footer_parts.append(f"All time: {all_time}")
        embed.set_footer(text="  \u2022  ".join(footer_parts))

        await ctx.send(embed=embed)

        if top_users:
            await self._assign_top_roles(ctx, top_users)

    async def _assign_top_roles(self, ctx, top_users: dict):
        """Assign/remove Discord roles for the top 3 users."""
        role_ids = [
            config.get("discord", "top_role_id"),
            config.get("discord", "second_role_id"),
            config.get("discord", "third_role_id"),
        ]
        roles = [ctx.guild.get_role(rid) for rid in role_ids if rid]

        if len(roles) != 3 or not all(roles):
            logger.warning("Top roles not fully configured — skipping role assignment.")
            return

        if not all(ctx.guild.me.top_role > role for role in roles):
            logger.warning("Bot role hierarchy too low to manage top user roles.")
            return

        top_user_ids = set(top_users.values())

        # Remove roles from members who are no longer in the top
        members_with_roles = set()
        for role in roles:
            members_with_roles.update(role.members)

        for member in members_with_roles:
            if member.id not in top_user_ids:
                try:
                    await member.remove_roles(*roles, reason="No longer a top user.")
                except Exception as e:
                    logger.error(f"Failed to remove roles from {member.display_name}: {e}")

        # Assign correct role to each top user
        for rank, user_id in top_users.items():
            member = ctx.guild.get_member(int(user_id))
            if not member or rank > len(roles):
                continue

            correct_role = roles[rank - 1]
            wrong_roles = [r for r in roles if r != correct_role]

            try:
                await member.add_roles(correct_role, reason=f"Top #{rank} user.")
                if wrong_roles:
                    await member.remove_roles(*wrong_roles, reason="Clearing other top roles.")
            except Exception as e:
                logger.error(f"Failed to assign role to {member.display_name}: {e}")

    @commands.command()
    async def stats(self, ctx, time: int = 30):
        """Displays Plex server statistics for a given time range.

        Usage:
        plex stats [days]

        Examples:
        plex stats
        plex stats 90
        """
        if time < 1 or time > 3650:
            await ctx.send("Please provide a number of days between 1 and 3650.")
            return

        try:
            import asyncio
            movies_resp, shows_resp, libraries_resp = await asyncio.gather(
                self.tautulli.get_most_watched_movies(time_range=time),
                self.tautulli.get_most_watched_shows(time_range=time),
                self.tautulli.get_libraries_table(),
            )

            # Library totals
            total_movies = 0
            total_shows = 0
            total_episodes = 0
            total_duration_seconds = 0

            if Tautulli.check_response(libraries_resp):
                for lib in Tautulli.get_response_data(libraries_resp, {}).get("data", []):
                    if lib["section_type"] == "movie":
                        total_movies += int(lib["count"])
                    elif lib["section_type"] == "show":
                        total_shows += int(lib["count"])
                        total_episodes += int(lib.get("child_count", 0))
                    total_duration_seconds += int(lib.get("duration", 0))

            library_line = f"{total_movies} movies  \u2022  {total_shows} shows  \u2022  {total_episodes} episodes"
            if total_duration_seconds:
                library_line += f"  \u2022  {_format_watch_duration(total_duration_seconds)} watched"

            embed = nextcord.Embed(
                title=f"Plex Server Stats \u2014 Last {time} Days",
                description=library_line,
                color=self.plex_embed_color,
            )

            # Top movies
            movie_lines = self._format_popular_list(movies_resp)
            if movie_lines:
                embed.add_field(name="Most Watched Movies", value=movie_lines, inline=False)

            # Top shows
            show_lines = self._format_popular_list(shows_resp)
            if show_lines:
                embed.add_field(name="Most Watched Shows", value=show_lines, inline=False)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in stats command: {e}", exc_info=True)
            await ctx.send("An error occurred while fetching Plex stats.")

    @staticmethod
    def _format_popular_list(response, limit: int = 5) -> str:
        """Format a Tautulli popular_movies/popular_tv response into ranked lines."""
        if not Tautulli.check_response(response):
            return ""
        rows = Tautulli.get_response_data(response, {}).get("rows", [])
        if not rows:
            return ""
        lines = []
        for i, item in enumerate(rows[:limit], 1):
            title = item.get("title", "Unknown")
            plays = item.get("total_plays", 0)
            users = item.get("users_watched", 0)
            lines.append(f"`{i}.` **{title}** \u2014 {plays} plays by {users} users")
        return "\n".join(lines)

    @commands.command()
    async def shows(self, ctx):
        """Displays the top users by TV watch time across all libraries.

        Usage:
        plex shows
        """
        try:
            import asyncio

            libs_resp = await self.tautulli.get_libraries()
            if not Tautulli.check_response(libs_resp):
                await ctx.send("Failed to retrieve libraries.")
                return

            tv_libraries = [
                lib for lib in Tautulli.get_response_data(libs_resp, [])
                if lib.get("section_type") == "show"
            ]
            if not tv_libraries:
                await ctx.send("No TV libraries found.")
                return

            # Fetch user stats for all TV libraries in parallel
            stats_responses = await asyncio.gather(*(
                self.tautulli.get_library_user_stats(section_id=lib["section_id"])
                for lib in tv_libraries
            ))

            user_mapping = UserMappings.load_user_mappings()
            ignored_users = {u["plex_username"] for u in user_mapping if u.get("ignore", False)}

            # Aggregate per-user watch time across libraries
            user_totals = {}
            for lib, resp in zip(tv_libraries, stats_responses):
                if not Tautulli.check_response(resp):
                    continue
                for stat in Tautulli.get_response_data(resp, []):
                    username = stat.get("username", "")
                    if username in ignored_users:
                        continue
                    if username not in user_totals:
                        user_totals[username] = {"time": 0, "libraries": []}
                    user_totals[username]["time"] += stat.get("total_time", 0)
                    user_totals[username]["libraries"].append(lib["section_name"])

            ranked = sorted(user_totals.items(), key=lambda x: x[1]["time"], reverse=True)[:10]

            if not ranked:
                await ctx.send("No TV watch data found.")
                return

            medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}
            lines = []
            for i, (username, data) in enumerate(ranked, 1):
                prefix = medal.get(i, f"`#{i}`")
                watch_str = _format_watch_duration(data["time"])
                libs = ", ".join(data["libraries"])
                lines.append(f"{prefix}  **{username}** \u2014 {watch_str}\n\u2003\u2003{libs}")

            embed = nextcord.Embed(
                title="Top TV Watchers",
                description="\n".join(lines),
                color=self.plex_embed_color,
            )

            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in shows command: {e}", exc_info=True)
            await ctx.send("An error occurred while fetching top shows.")

    @commands.command()
    async def history(self, ctx, *, identifier: str = None):
        """Displays recent watch history for a user or the whole server.

        Usage:
        plex history
        plex history @user
        plex history <plex_username>
        """
        member = None
        plex_username = None

        if identifier:
            try:
                member = await commands.MemberConverter().convert(ctx, identifier)
            except commands.MemberNotFound:
                plex_username = identifier

        # Resolve Discord member to Plex username
        if member:
            user_data = UserMappings.load_user_mappings()
            plex_user = next(
                (u for u in user_data if str(u.get("discord_id")) == str(member.id)), None
            )
            if not plex_user:
                await ctx.send(f"**{member.display_name}** is not mapped to a Plex user.")
                return
            plex_username = plex_user.get("plex_username")

        response = await self.tautulli.get_history(
            params={"length": 25, "order_column": "date", "order_dir": "desc"}
        )
        if not Tautulli.check_response(response):
            await ctx.send("Failed to retrieve watch history.")
            return

        entries = Tautulli.get_response_data(response, {}).get("data", [])

        # Filter by user if specified
        ignored = set()
        if not plex_username:
            user_data = UserMappings.load_user_mappings()
            ignored = {u["plex_username"] for u in user_data if u.get("ignore", False)}

        lines = []
        for entry in entries:
            user = entry.get("user", "")
            if plex_username and user != plex_username:
                continue
            if user in ignored:
                continue

            title = entry.get("full_title", entry.get("title", "Unknown"))
            timestamp = entry.get("date")
            duration_s = entry.get("duration", 0)
            duration_str = _format_watch_duration(duration_s) if duration_s else ""

            time_str = f"<t:{timestamp}:R>" if timestamp else ""
            user_suffix = f"  \u2022  {user}" if not plex_username else ""

            line = f"{time_str}  **{title}**"
            if duration_str:
                line += f"  ({duration_str})"
            line += user_suffix
            lines.append(line)

            if len(lines) >= 15:
                break

        # Build embed
        if plex_username:
            title_text = f"Watch History \u2014 {member.display_name if member else plex_username}"
        else:
            title_text = "Recent Watch History"

        embed = nextcord.Embed(title=title_text, color=self.plex_embed_color)

        if member:
            embed.set_thumbnail(url=member.display_avatar.url)

        embed.description = "\n".join(lines) if lines else "No history found."

        if len(lines) >= 15:
            embed.set_footer(text="Showing last 15 entries")

        await ctx.send(embed=embed)

    @commands.command()
    async def hot(self, ctx, time: int = 7):
        """Shows trending content and activity insights from your Plex server.

        Usage:
        plex hot [days]

        Examples:
        plex hot
        plex hot 14
        """
        if time < 1 or time > 365:
            await ctx.send("Please provide a number of days between 1 and 365.")
            return

        try:
            import asyncio
            processing_msg = await ctx.send("Analyzing Plex activity...")

            # Fetch history + recently added in parallel
            history_response, recent_added_response = await asyncio.gather(
                self.tautulli.get_history(
                    params={"length": 1000, "order_column": "date", "order_dir": "desc"}
                ),
                self.tautulli.get_recently_added(count=50),
            )

            if not Tautulli.check_response(history_response):
                await processing_msg.edit(content="Failed to retrieve watch history.")
                return

            history_entries = Tautulli.get_response_data(history_response, {}).get("data", [])
            user_mapping = UserMappings.load_user_mappings()
            ignored_users = {u["plex_username"] for u in user_mapping if u.get("ignore", False)}

            cutoff_timestamp = (datetime.now() - timedelta(days=time)).timestamp()
            recent_history = [
                e for e in history_entries
                if e["date"] >= cutoff_timestamp and e["user"] not in ignored_users
            ]

            if not recent_history:
                await processing_msg.edit(content=f"No watch activity found in the last {time} days.")
                return

            # Parse recently added items
            recent_items = []
            if Tautulli.check_response(recent_added_response):
                all_recent = Tautulli.get_response_data(recent_added_response, {}).get("recently_added", [])
                recent_items = [i for i in all_recent if float(i.get("added_at", 0)) >= cutoff_timestamp]

            # Single-pass aggregation
            rk_plays = {}
            user_episodes = {}
            content_by_users = {}
            user_activity = {}

            for entry in recent_history:
                rk = str(entry.get("rating_key", ""))
                rk_plays[rk] = rk_plays.get(rk, 0) + 1

                key = entry.get("grandparent_rating_key", entry.get("rating_key"))
                title = entry.get("grandparent_title", entry.get("title"))
                if key and title:
                    if key not in content_by_users:
                        content_by_users[key] = {"title": title, "users": set(), "count": 0}
                    content_by_users[key]["users"].add(entry["user"])
                    content_by_users[key]["count"] += 1

                user = entry["user"]
                if user not in user_activity:
                    user_activity[user] = {"plays": 0, "titles": set()}
                user_activity[user]["plays"] += 1
                user_activity[user]["titles"].add(entry.get("title"))

                if entry.get("media_type") == "episode" and entry.get("grandparent_title"):
                    if user not in user_episodes:
                        user_episodes[user] = {}
                    show = entry["grandparent_title"]
                    user_episodes[user][show] = user_episodes[user].get(show, 0) + 1

            # Build summary stats for description
            total_plays = len(recent_history)
            unique_users = len(user_activity)
            unique_titles = len({e.get("title") for e in recent_history if e.get("title")})
            avg_per_day = total_plays / time if time > 0 else 0

            desc_parts = [
                f"{total_plays} plays",
                f"{unique_users} users",
                f"{unique_titles} titles",
                f"{avg_per_day:.1f}/day avg",
            ]

            embed = nextcord.Embed(
                title=f"What's Hot \u2014 Last {time} Days",
                description="  \u2022  ".join(desc_parts),
                color=self.plex_embed_color,
            )

            # Sections
            self._add_hot_additions(embed, rk_plays, recent_items)
            self._add_trending_content(embed, content_by_users)
            self._add_most_active_users(embed, user_activity)
            self._add_binge_watchers(embed, user_activity, user_episodes)
            self._add_discovery_watches(embed, content_by_users, recent_items)

            # Movies vs TV footer
            media_types = {}
            for e in recent_history:
                mt = e.get("media_type", "unknown")
                media_types[mt] = media_types.get(mt, 0) + 1
            movie_count = media_types.get("movie", 0)
            episode_count = media_types.get("episode", 0)
            if movie_count and episode_count:
                if movie_count > episode_count:
                    embed.set_footer(text=f"Movies lead: {movie_count} movies vs {episode_count} episodes")
                else:
                    embed.set_footer(text=f"TV leads: {episode_count} episodes vs {movie_count} movies")

            await processing_msg.edit(content=None, embed=embed)

        except Exception as e:
            logger.error(f"Error in hot command: {e}", exc_info=True)
            await ctx.send("An error occurred while analyzing activity.")

    def _add_hot_additions(self, embed, rk_plays, recent_items):
        """New content that's getting plays."""
        if not recent_items:
            return
        watched = []
        for item in recent_items:
            plays = rk_plays.get(str(item.get("rating_key")), 0)
            if plays > 0:
                watched.append((item.get("full_title", item.get("title")), plays))
        watched.sort(key=lambda x: x[1], reverse=True)
        if watched:
            lines = [f"\u25B8 **{t}** \u2014 {p} play{'s' if p != 1 else ''}" for t, p in watched[:5]]
            embed.add_field(name="Hot New Additions", value="\n".join(lines), inline=False)

    def _add_trending_content(self, embed, content_by_users):
        """Content watched by multiple users."""
        multi = [v for v in content_by_users.values() if len(v["users"]) > 1]
        multi.sort(key=lambda x: (len(x["users"]), x["count"]), reverse=True)
        if multi:
            lines = [
                f"\u25B8 **{item['title']}** \u2014 {len(item['users'])} users, {item['count']} plays"
                for item in multi[:5]
            ]
            embed.add_field(name="Trending", value="\n".join(lines), inline=False)

    def _add_most_active_users(self, embed, user_activity):
        """Users with the most plays."""
        if not user_activity:
            return
        sorted_users = sorted(user_activity.items(), key=lambda x: x[1]["plays"], reverse=True)
        medal = {0: "\U0001f947", 1: "\U0001f948", 2: "\U0001f949"}
        lines = []
        for i, (user, stats) in enumerate(sorted_users[:5]):
            prefix = medal.get(i, f"`#{i+1}`")
            lines.append(f"{prefix}  **{user}** \u2014 {stats['plays']} plays, {len(stats['titles'])} titles")
        embed.add_field(name="Most Active", value="\n".join(lines), inline=False)

    def _add_binge_watchers(self, embed, user_activity, user_episodes):
        """Users who watched 5+ episodes of a single show."""
        binges = []
        for user, stats in user_activity.items():
            if stats["plays"] < 5:
                continue
            shows = user_episodes.get(user, {})
            if shows:
                top_show, count = max(shows.items(), key=lambda x: x[1])
                if count >= 5:
                    binges.append((user, top_show, count))
        binges.sort(key=lambda x: x[2], reverse=True)
        if binges:
            lines = [f"\u25B8 **{u}** binged **{s}** ({c} eps)" for u, s, c in binges[:3]]
            embed.add_field(name="Binge Watchers", value="\n".join(lines), inline=False)

    def _add_discovery_watches(self, embed, content_by_users, recent_items):
        """Recently added content explored by a single user."""
        if not recent_items:
            return
        recent_keys = {str(item.get("rating_key")) for item in recent_items}
        discoveries = []
        for key, item in content_by_users.items():
            if 1 <= item["count"] <= 2 and len(item["users"]) == 1 and str(key) in recent_keys:
                user = next(iter(item["users"]))
                discoveries.append(f"\u25B8 **{user}** checked out **{item['title']}**")
        if discoveries:
            embed.add_field(name="First Discoveries", value="\n".join(discoveries[:3]), inline=False)


def setup(bot):
    bot.add_cog(PlexStats(bot))
