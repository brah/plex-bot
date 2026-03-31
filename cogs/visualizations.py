# cogs/visualizations.py

import logging
import asyncio
from io import BytesIO
from typing import Optional, Dict, List, Any, Union

import nextcord
from nextcord.ext import commands
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import pytz

from config import config
from media_cache import MediaCache
from tautulli_wrapper import Tautulli
from utilities import UserMappings

# Configure logging for this module
logger = logging.getLogger("plexbot.visualizations")
logger.setLevel(logging.INFO)


_MSG_NO_PLEX_DATA = "The PlexData cog is not loaded. Please contact the administrator."
_MSG_NO_DATA = "No data available for the specified criteria."
_MSG_CHART_FAILED = "Failed to generate chart."


class Visualizations(commands.Cog):
    """Commands for creating visual charts and graphs from Plex/Tautulli data."""

    def __init__(self, bot):
        """Initialize the Visualizations cog."""
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.plex_data = self.bot.get_cog("PlexData")
        # Defer timezone handling to PlexData cog

        # Theme colors from config
        self.plex_orange = config.get("colors", "plex_orange", "#E5A00D")
        self.plex_embed_color = int(self.plex_orange.lstrip("#"), 16)
        self.plex_grey_dark = config.get("colors", "plex_grey_dark", "#1B1B1B")
        self.plex_colors = config.get("colors", "media_types", {
            "Movie": "#E5A00D", "TV": "#F6E0B6", "Other": "#F3D38A", "Unknown": "#F0C75E"
        })

        # Chart dimensions from config
        self.chart_width = config.get("charts", "width", 14)
        self.chart_height = config.get("charts", "height", 6)
        self.chart_dpi = config.get("charts", "dpi", 100)
        self.chart_month_format = config.get("charts", "month_format", "%b %Y")

    def _ensure_plex_data(self):
        """Ensure PlexData cog is available, fetching lazily if needed."""
        if not self.plex_data:
            self.plex_data = self.bot.get_cog("PlexData")
        return self.plex_data is not None

    @commands.group(invoke_without_command=True)
    async def chart(self, ctx):
        """
        Create various charts and visualizations of Plex data.

        Available charts:
        - hours: Most watched hours of the day
        - days: Most watched days of the week
        - users: Most active users
        - media: Media types watched per day
        - months: Play count by month

        Usage: plex chart [type] [@user] [days]

        Examples:
        plex chart hours - Shows hours watched for the last 30 days
        plex chart days @user - Shows days watched for a specific user
        plex chart users 60 - Shows most active users for the last 60 days
        """
        embed = nextcord.Embed(
            title="📊 Plex Chart Commands",
            description="Generate visual charts from your Plex data",
            color=self.plex_embed_color,
        )

        embed.add_field(
            name="Available Charts",
            value=(
                "**hours** - Most watched hours of the day\n"
                "**days** - Most watched days of the week\n"
                "**users** - Most active users\n"
                "**media** - Media types watched per day\n"
                "**months** - Play count by month"
            ),
            inline=False,
        )

        embed.add_field(
            name="Usage",
            value=(
                "`plex chart [type] [@user] [days]`\n\n"
                "You can specify a user to see their stats, and/or a number of days to look back."
            ),
            inline=False,
        )

        embed.add_field(
            name="Examples",
            value=(
                "`plex chart hours` - Your viewing hours for last 30 days\n"
                "`plex chart days @user` - When a specific user watches\n"
                "`plex chart users 60` - Most active users over 60 days\n"
                "`plex chart media 90` - Media types watched over 90 days\n"
                "`plex chart months @user 365` - Monthly activity for a user over a year"
            ),
            inline=False,
        )

        await ctx.send(embed=embed)

    @chart.command(name="hours")
    async def chart_hours(self, ctx, *args):
        """
        Displays a chart of the most-watched hours of the day.

        Usage:
        plex chart hours [@member] [days]

        Examples:
        plex chart hours - Shows your own statistics for the last 30 days
        plex chart hours @user - Shows another user's statistics
        plex chart hours 60 - Shows your statistics for the last 60 days
        """
        await ctx.trigger_typing()

        if not self._ensure_plex_data():
            await ctx.send(_MSG_NO_PLEX_DATA)
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return  # Invalid argument handled in parse_member_and_days

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send(_MSG_NO_DATA)
            return

        # Process data
        hour_counts = await self.plex_data.calculate_hour_counts(data)
        if hour_counts.empty:
            await ctx.send("No data available for hours watched.")
            return

        # Get user name for personalized chart only if member was explicitly specified
        user_name = member.display_name if member else None

        # Generate chart with user name in title (will be None if user wasn't explicitly provided)
        image = await self.generate_hour_chart(hour_counts, days, user_name)

        # Send image
        if image:
            # Add user info to the filename only when specifically looking at someone's data
            filename = f"hours_{member.display_name if member else 'server'}.png"
            await ctx.send(file=nextcord.File(fp=image, filename=filename))
        else:
            await ctx.send(_MSG_CHART_FAILED)

    @chart.command(name="days")
    async def chart_days(self, ctx, *args):
        """
        Displays a chart of the most-watched days of the week.

        Usage:
        plex chart days [@member] [days]

        Examples:
        plex chart days - Shows your own statistics for the last 30 days
        plex chart days @user - Shows another user's statistics
        plex chart days 60 - Shows your statistics for the last 60 days
        """
        await ctx.trigger_typing()

        if not self._ensure_plex_data():
            await ctx.send(_MSG_NO_PLEX_DATA)
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send(_MSG_NO_DATA)
            return

        # Process data
        day_counts = await self.plex_data.calculate_day_counts(data)
        if day_counts.empty:
            await ctx.send("No data available for days watched.")
            return

        # Get user name for personalized chart only if member was explicitly specified
        user_name = member.display_name if member else None

        # Generate chart with user name in title (will be None if user wasn't explicitly provided)
        image = await self.generate_day_chart(day_counts, days, user_name)

        # Send image
        if image:
            filename = f"days_{member.display_name if member else 'server'}.png"
            await ctx.send(file=nextcord.File(fp=image, filename=filename))
        else:
            await ctx.send(_MSG_CHART_FAILED)

    @chart.command(name="users")
    async def chart_users(self, ctx, *args):
        """
        Displays a chart of the most active users.

        Usage:
        plex chart users [days]

        Examples:
        plex chart users - Shows activity for the last 30 days
        plex chart users 60 - Shows activity for the last 60 days
        """
        await ctx.trigger_typing()

        if not self._ensure_plex_data():
            await ctx.send(_MSG_NO_PLEX_DATA)
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is not None:
            await ctx.send("This command does not support specifying a user.")
            return
        if days is None:
            return

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, None, days)
        if not data:
            await ctx.send(_MSG_NO_DATA)
            return

        # Process data
        user_counts = await self.plex_data.calculate_user_counts(data)
        if user_counts.empty:
            await ctx.send("No user data available.")
            return

        # Generate chart
        image = await self.generate_user_chart(user_counts, days)

        # Send image
        if image:
            await ctx.send(file=nextcord.File(fp=image, filename="active_users.png"))
        else:
            await ctx.send(_MSG_CHART_FAILED)

    @chart.command(name="media")
    async def chart_media(self, ctx, *args):
        """
        Displays a line graph of media types watched per day.

        Usage:
        plex chart media [@member] [days]

        Examples:
        plex chart media - Shows your own statistics for the last 30 days
        plex chart media @user - Shows another user's statistics
        plex chart media 60 - Shows your statistics for the last 60 days
        """
        await ctx.trigger_typing()

        if not self._ensure_plex_data():
            await ctx.send(_MSG_NO_PLEX_DATA)
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send(_MSG_NO_DATA)
            return

        # Process data
        media_type_data = await self.plex_data.calculate_media_type_by_day(data)
        if media_type_data.empty:
            await ctx.send("No data available.")
            return

        # Get user name for personalized chart only if member was explicitly specified
        user_name = member.display_name if member else None

        # Generate chart with user name in title (will be None if user wasn't explicitly provided)
        image = await self.generate_media_type_by_day_chart(media_type_data, days, user_name)

        # Send image
        if image:
            filename = f"media_{member.display_name if member else 'server'}.png"
            await ctx.send(file=nextcord.File(fp=image, filename=filename))
        else:
            await ctx.send(_MSG_CHART_FAILED)

    @chart.command(name="months")
    async def chart_months(self, ctx, *args):
        """
        Displays a bar chart of total play count by month.

        Usage:
        plex chart months [@member] [days]

        Examples:
        plex chart months - Shows your own statistics for the last 365 days
        plex chart months @user - Shows another user's statistics
        plex chart months 90 - Shows your statistics for the last 90 days
        """
        await ctx.trigger_typing()

        if not self._ensure_plex_data():
            await ctx.send(_MSG_NO_PLEX_DATA)
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return
        if days == 30:
            days = 365  # Default to last 12 months if not specified

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send(_MSG_NO_DATA)
            return

        # Process data
        month_counts = await self.plex_data.calculate_play_count_by_month(data)
        if month_counts.empty:
            await ctx.send("No data available.")
            return

        # Get user name for personalized chart only if member was explicitly specified
        user_name = member.display_name if member else None

        # Generate chart with user name in title (will be None if user wasn't explicitly provided)
        image = await self.generate_play_count_by_month_chart(month_counts, days, user_name)

        # Send image
        if image:
            filename = f"months_{member.display_name if member else 'server'}.png"
            await ctx.send(file=nextcord.File(fp=image, filename=filename))
        else:
            await ctx.send(_MSG_CHART_FAILED)

    def _render_bar_chart(self, title: str, xlabel: str, ylabel: str,
                          x_data, y_data, horizontal: bool = False,
                          extra_xticks=None, xtick_rotation=None) -> Optional[BytesIO]:
        """Shared bar chart renderer — handles style, save, and cleanup."""
        self.set_custom_style()
        plt.figure(figsize=(self.chart_width, self.chart_height))
        try:
            if horizontal:
                sns.barplot(x=y_data, y=x_data, color=self.plex_orange)
            else:
                sns.barplot(x=x_data, y=y_data, color=self.plex_orange)

            plt.title(title, color="white")
            plt.xlabel(xlabel, color="white")
            plt.ylabel(ylabel, color="white")
            if extra_xticks is not None:
                plt.xticks(extra_xticks)
            if xtick_rotation is not None:
                plt.xticks(rotation=xtick_rotation, ha="right")
            plt.tight_layout()

            image_stream = BytesIO()
            plt.savefig(
                image_stream, format="png", facecolor=plt.gcf().get_facecolor(), dpi=self.chart_dpi
            )
            image_stream.seek(0)
            return image_stream
        except Exception as e:
            logger.error(f"Error generating chart: {e}")
            return None
        finally:
            plt.close()

    def _user_title_suffix(self, user_name: str = None, days: int = 0,
                           include_tz: bool = False) -> str:
        """Build a common title suffix like ' for UserName (UTC+10) (past 30d)'."""
        parts = ""
        if user_name:
            parts += f" for {user_name}"
        if include_tz and self.plex_data and self.plex_data.timezone:
            parts += f" {self.plex_data.get_utc_offset_str()}"
        parts += f" (past {days}d)"
        return parts

    async def generate_hour_chart(
        self, hour_counts: pd.Series, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """Generates a bar chart for hour counts."""
        suffix = self._user_title_suffix(user_name, days, include_tz=True)
        return self._render_bar_chart(
            title=f"Most Watched Hours of the Day{suffix}",
            xlabel="Hour of Day", ylabel="Watch Count",
            x_data=hour_counts.index, y_data=hour_counts.values,
            extra_xticks=range(0, 24),
        )

    async def generate_day_chart(
        self, day_counts: pd.Series, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """Generates a bar chart for day counts."""
        suffix = self._user_title_suffix(user_name, days)
        return self._render_bar_chart(
            title=f"Most Watched Days of the Week{suffix}",
            xlabel="Days", ylabel="Watch Count",
            x_data=day_counts.index, y_data=day_counts.values,
        )

    async def generate_user_chart(self, user_counts: pd.Series, days: int) -> Optional[BytesIO]:
        """Generates a horizontal bar chart for user counts."""
        return self._render_bar_chart(
            title=f"Top {len(user_counts)} Most Active Users (past {days}d)",
            xlabel="Watch Count", ylabel="Users",
            x_data=user_counts.index, y_data=user_counts.values,
            horizontal=True,
        )

    async def generate_media_type_by_day_chart(
        self, media_type_data: pd.DataFrame, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """Generates a line graph for media types per day."""
        self.set_custom_style()
        plt.figure(figsize=(self.chart_width, self.chart_height))
        try:
            media_type_pivot = media_type_data.pivot(
                index="date", columns="media_type", values="count"
            ).fillna(0)
            media_type_pivot.index = pd.to_datetime(media_type_pivot.index)
            media_type_pivot = media_type_pivot.sort_index()

            media_type_pivot.plot(
                kind="line",
                marker="o",
                color=[self.plex_colors.get(col, "#FFFFFF") for col in media_type_pivot.columns],
                figsize=(self.chart_width, self.chart_height),
            )

            suffix = self._user_title_suffix(user_name, days, include_tz=True)
            plt.title(f"Media Types Watched Per Day{suffix}", color="white")
            plt.xlabel("Date", color="white")
            plt.ylabel("Watch Count", color="white")
            plt.legend(title="Media Type")
            plt.tight_layout()

            image_stream = BytesIO()
            plt.savefig(
                image_stream, format="png", facecolor=plt.gcf().get_facecolor(), dpi=self.chart_dpi
            )
            image_stream.seek(0)
            return image_stream
        except Exception as e:
            logger.error(f"Error generating media type chart: {e}")
            return None
        finally:
            plt.close()

    async def generate_play_count_by_month_chart(
        self, month_counts: pd.Series, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """Generates a bar chart for play counts by month."""
        months = pd.to_datetime(month_counts.index, format="%Y-%m")
        month_labels = months.strftime(self.chart_month_format)
        suffix = self._user_title_suffix(user_name, days)
        return self._render_bar_chart(
            title=f"Total Play Count by Month{suffix}",
            xlabel="Month", ylabel="Play Count",
            x_data=month_labels, y_data=month_counts.values,
            xtick_rotation=45 if len(month_labels) > 6 else None,
        )

    def set_custom_style(self) -> None:
        """Sets a custom Seaborn style to match the Plex theme."""
        custom_style = {
            "axes.facecolor": self.plex_grey_dark,
            "figure.facecolor": self.plex_grey_dark,
            "axes.edgecolor": self.plex_orange,  # Plex orange for axes edges
            "axes.labelcolor": "white",
            "xtick.color": "white",
            "ytick.color": "white",
            "text.color": "white",
            "grid.color": "#2A2A2A",
            "axes.grid": True,
            "axes.titlecolor": "white",
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "axes.labelsize": 12,
            "axes.titlesize": 14,
            "legend.facecolor": self.plex_grey_dark,
            "legend.edgecolor": "white",
            "legend.labelcolor": "white",
            "legend.title_fontsize": 12,
            "legend.fontsize": 10,
        }
        sns.set_theme(style="darkgrid", rc=custom_style)


def setup(bot):
    bot.add_cog(Visualizations(bot))
