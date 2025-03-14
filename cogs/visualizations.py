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

from bot_config import BotConfig
from media_cache import MediaCache
from tautulli_wrapper import Tautulli
from utilities import UserMappings

# Configure logging for this module
logger = logging.getLogger("plexbot.visualizations")
logger.setLevel(logging.INFO)


class Visualizations(commands.Cog):
    """Commands for creating visual charts and graphs from Plex/Tautulli data."""

    def __init__(self, bot):
        """Initialize the Visualizations cog."""
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.plex_data = self.bot.get_cog("PlexData")
        # Defer timezone handling to PlexData cog

        # Theme colors
        self.plex_orange = BotConfig.PLEX_ORANGE
        self.plex_grey_dark = BotConfig.PLEX_GREY_DARK
        self.plex_colors = BotConfig.PLEX_COLORS

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
            color=0xE5A00D,
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

        if not self.plex_data:
            await ctx.send("The PlexData cog is not loaded. Please contact the administrator.")
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return  # Invalid argument handled in parse_member_and_days

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send("No data available for the specified criteria.")
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
            await ctx.send("Failed to generate chart.")

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

        if not self.plex_data:
            await ctx.send("The PlexData cog is not loaded. Please contact the administrator.")
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send("No data available for the specified criteria.")
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
            await ctx.send("Failed to generate chart.")

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

        if not self.plex_data:
            await ctx.send("The PlexData cog is not loaded. Please contact the administrator.")
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is not None:
            await ctx.send("This command does not support specifying a user.")
            return
        if days is None:
            return

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, None, days)
        if not data:
            await ctx.send("No data available for the specified criteria.")
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
            await ctx.send("Failed to generate chart.")

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

        if not self.plex_data:
            await ctx.send("The PlexData cog is not loaded. Please contact the administrator.")
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send("No data available for the specified criteria.")
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
            await ctx.send("Failed to generate chart.")

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

        if not self.plex_data:
            await ctx.send("The PlexData cog is not loaded. Please contact the administrator.")
            return

        member, days = await self.plex_data.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return
        if days == 30:
            days = 365  # Default to last 12 months if not specified

        data = await self.plex_data.fetch_watch_history_with_genres(ctx, member, days)
        if not data:
            await ctx.send("No data available for the specified criteria.")
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
            await ctx.send("Failed to generate chart.")

    # Legacy command support - redirect to new command format
    @commands.command()
    async def most_watched_hours(self, ctx, *args):
        """Legacy command - redirects to 'plex chart hours'"""
        await ctx.send("ℹ️ This command is being moved to the new format. Try `plex chart hours` instead!")
        await self.chart_hours(ctx, *args)

    @commands.command()
    async def most_watched_days(self, ctx, *args):
        """Legacy command - redirects to 'plex chart days'"""
        await ctx.send("ℹ️ This command is being moved to the new format. Try `plex chart days` instead!")
        await self.chart_days(ctx, *args)

    @commands.command()
    async def most_active_users(self, ctx, *args):
        """Legacy command - redirects to 'plex chart users'"""
        await ctx.send("ℹ️ This command is being moved to the new format. Try `plex chart users` instead!")
        await self.chart_users(ctx, *args)

    @commands.command()
    async def media_type_by_day(self, ctx, *args):
        """Legacy command - redirects to 'plex chart media'"""
        await ctx.send("ℹ️ This command is being moved to the new format. Try `plex chart media` instead!")
        await self.chart_media(ctx, *args)

    @commands.command()
    async def play_count_by_month(self, ctx, *args):
        """Legacy command - redirects to 'plex chart months'"""
        await ctx.send("ℹ️ This command is being moved to the new format. Try `plex chart months` instead!")
        await self.chart_months(ctx, *args)

    async def generate_hour_chart(
        self, hour_counts: pd.Series, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """
        Generates a bar chart for hour counts using Seaborn.

        Args:
            hour_counts: Series containing hour counts
            days: Number of days the data covers
            user_name: Optional name of the user for personalized title

        Returns:
            BytesIO object containing the chart image
        """
        self.set_custom_style()
        plt.figure(figsize=(BotConfig.CHART_WIDTH, BotConfig.CHART_HEIGHT))

        # Create the bar plot
        sns.barplot(x=hour_counts.index, y=hour_counts.values, color=self.plex_orange)

        # Get UTC offset string from PlexData cog if available
        utc_offset_str = ""
        if self.plex_data and self.plex_data.timezone:
            utc_offset_str = self.plex_data.get_utc_offset_str()

        # Personalize title if user is specified
        user_str = f" for {user_name}" if user_name else ""

        plt.title(
            f"Most Watched Hours of the Day{user_str} {utc_offset_str} (past {days}d)",
            color="white",
        )
        plt.xlabel("Hour of Day", color="white")
        plt.ylabel("Watch Count", color="white")
        plt.xticks(range(0, 24))
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(
            image_stream, format="png", facecolor=plt.gcf().get_facecolor(), dpi=BotConfig.CHART_DPI
        )
        plt.close()
        image_stream.seek(0)
        return image_stream

    async def generate_day_chart(
        self, day_counts: pd.Series, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """
        Generates a bar chart for day counts using Seaborn.

        Args:
            day_counts: Series containing day counts
            days: Number of days the data covers
            user_name: Optional name of the user for personalized title

        Returns:
            BytesIO object containing the chart image
        """
        self.set_custom_style()
        plt.figure(figsize=(BotConfig.CHART_WIDTH, BotConfig.CHART_HEIGHT))

        # Create the bar plot
        sns.barplot(x=day_counts.index, y=day_counts.values, color=self.plex_orange)

        # Personalize title if user is specified
        user_str = f" for {user_name}" if user_name else ""

        plt.title(f"Most Watched Days of the Week{user_str} (past {days}d)", color="white")
        plt.xlabel("Days", color="white")
        plt.ylabel("Watch Count", color="white")
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(
            image_stream, format="png", facecolor=plt.gcf().get_facecolor(), dpi=BotConfig.CHART_DPI
        )
        plt.close()
        image_stream.seek(0)
        return image_stream

    async def generate_user_chart(self, user_counts: pd.Series, days: int) -> Optional[BytesIO]:
        """
        Generates a bar chart for user counts using Seaborn.

        Args:
            user_counts: Series containing user counts
            days: Number of days the data covers

        Returns:
            BytesIO object containing the chart image
        """
        self.set_custom_style()
        plt.figure(figsize=(BotConfig.CHART_WIDTH, BotConfig.CHART_HEIGHT))

        # Create the horizontal bar plot for users
        sns.barplot(x=user_counts.values, y=user_counts.index, color=self.plex_orange)
        plt.title(f"Top {len(user_counts)} Most Active Users (past {days}d)", color="white")
        plt.xlabel("Watch Count", color="white")
        plt.ylabel("Users", color="white")
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(
            image_stream, format="png", facecolor=plt.gcf().get_facecolor(), dpi=BotConfig.CHART_DPI
        )
        plt.close()
        image_stream.seek(0)
        return image_stream

    async def generate_media_type_by_day_chart(
        self, media_type_data: pd.DataFrame, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """
        Generates a line graph for media types per day using Seaborn.

        Args:
            media_type_data: DataFrame containing media type counts by day
            days: Number of days the data covers
            user_name: Optional name of the user for personalized title

        Returns:
            BytesIO object containing the chart image
        """
        self.set_custom_style()
        plt.figure(figsize=(BotConfig.CHART_WIDTH, BotConfig.CHART_HEIGHT))

        # Create the pivot table for the line chart
        try:
            media_type_pivot = media_type_data.pivot(
                index="date", columns="media_type", values="count"
            ).fillna(0)
            media_type_pivot.index = pd.to_datetime(media_type_pivot.index)
            media_type_pivot = media_type_pivot.sort_index()

            # Create line plot with markers
            ax = media_type_pivot.plot(
                kind="line",
                marker="o",
                color=[self.plex_colors.get(col, "#FFFFFF") for col in media_type_pivot.columns],
                figsize=(BotConfig.CHART_WIDTH, BotConfig.CHART_HEIGHT),
            )

            # Get UTC offset string from PlexData cog if available
            utc_offset_str = ""
            if self.plex_data and self.plex_data.timezone:
                utc_offset_str = self.plex_data.get_utc_offset_str()

            # Personalize title if user is specified
            user_str = f" for {user_name}" if user_name else ""

            plt.title(
                f"Media Types Watched Per Day{user_str} {utc_offset_str} (past {days}d)",
                color="white",
            )
            plt.xlabel("Date", color="white")
            plt.ylabel("Watch Count", color="white")
            plt.legend(title="Media Type")
            plt.tight_layout()

            image_stream = BytesIO()
            plt.savefig(
                image_stream, format="png", facecolor=plt.gcf().get_facecolor(), dpi=BotConfig.CHART_DPI
            )
            plt.close()
            image_stream.seek(0)
            return image_stream
        except Exception as e:
            logger.error(f"Error generating media type chart: {e}")
            plt.close()
            return None

    async def generate_play_count_by_month_chart(
        self, month_counts: pd.Series, days: int, user_name: str = None
    ) -> Optional[BytesIO]:
        """
        Generates a bar chart for play counts by month using Seaborn.

        Args:
            month_counts: Series containing month counts
            days: Number of days the data covers
            user_name: Optional name of the user for personalized title

        Returns:
            BytesIO object containing the chart image
        """
        self.set_custom_style()
        plt.figure(figsize=(BotConfig.CHART_WIDTH, BotConfig.CHART_HEIGHT))

        try:
            # Convert month_counts.index to datetime for proper ordering
            months = pd.to_datetime(month_counts.index, format="%Y-%m")
            month_labels = months.strftime(BotConfig.CHART_MONTH_FORMAT)

            # Create the bar plot
            bar_plot = sns.barplot(x=month_labels, y=month_counts.values, color=self.plex_orange)

            # Adjust x-axis labels if there are many months
            if len(month_labels) > 6:
                plt.xticks(rotation=45, ha="right")

            # Personalize title if user is specified
            user_str = f" for {user_name}" if user_name else ""

            plt.title(f"Total Play Count by Month{user_str} (past {days}d)", color="white")
            plt.xlabel("Month", color="white")
            plt.ylabel("Play Count", color="white")
            plt.tight_layout()

            image_stream = BytesIO()
            plt.savefig(
                image_stream, format="png", facecolor=plt.gcf().get_facecolor(), dpi=BotConfig.CHART_DPI
            )
            plt.close()
            image_stream.seek(0)
            return image_stream
        except Exception as e:
            logger.error(f"Error generating month chart: {e}")
            plt.close()
            return None

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
