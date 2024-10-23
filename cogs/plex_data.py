# cogs/plex_data.py

import logging
import asyncio
import nextcord
from nextcord.ext import commands
from io import BytesIO

import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import pytz
import tzlocal
import datetime

from tautulli_wrapper import Tautulli
from utilities import UserMappings

# Configure logging for this module
logger = logging.getLogger("plexbot.data")
logger.setLevel(logging.INFO)


class Data(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.plex_orange = "#E5A00D"  # Plex orange color
        self.plex_grey_dark = "#1B1B1B"  # Dark grey background
        self.timezone = None  # Timezone will be fetched from Tautulli or local timezone

    async def get_tautulli_timezone(self):
        """Retrieve the timezone from Tautulli settings."""
        response = await self.tautulli.api_call("get_settings")
        if response["response"]["result"] != "success":
            logger.warning("Failed to retrieve Tautulli settings. Using local timezone.")
            return tzlocal.get_localzone()
        else:
            settings = response["response"]["data"]
            timezone_str = settings.get("default_timezone")
            if timezone_str:
                try:
                    return pytz.timezone(timezone_str)
                except pytz.UnknownTimeZoneError:
                    logger.warning(f"Unknown timezone '{timezone_str}'. Using local timezone.")
                    return tzlocal.get_localzone()
            else:
                logger.warning("Timezone not found in Tautulli settings. Using local timezone.")
                return tzlocal.get_localzone()

    def get_utc_offset_str(self):
        """Returns a string representation of the UTC offset, e.g., '(UTC+10)'."""
        now = datetime.datetime.now(self.timezone)
        offset = now.utcoffset()
        total_minutes = int(offset.total_seconds() / 60)
        hours, minutes = divmod(abs(total_minutes), 60)
        sign = "+" if total_minutes >= 0 else "-"
        if minutes == 0:
            return f"(UTC{sign}{hours})"
        else:
            return f"(UTC{sign}{hours}:{minutes:02d})"

    # Utility function to parse arguments
    async def parse_member_and_days(self, ctx, args):
        member = None
        days = 30  # Default value

        member_converter = commands.MemberConverter()
        for arg in args:
            try:
                potential_member = await member_converter.convert(ctx, arg)
                member = potential_member
            except commands.BadArgument:
                if arg.isdigit():
                    days = int(arg)
                else:
                    await ctx.send(f"Invalid argument: {arg}")
                    return None, None
        return member, days

    @commands.command()
    async def most_watched_hours(self, ctx, *args):
        """Displays a chart of the most-watched hours of the day."""
        await ctx.trigger_typing()
        member, days = await self.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return  # Invalid argument handled in parse_member_and_days

        data = await self.fetch_watch_history_with_genres(ctx, member, days)
        if data is None:
            return

        # Process data
        hour_counts = self.calculate_hour_counts(data)
        if hour_counts.empty:
            await ctx.send("No data available.")
            return

        # Generate chart
        image = self.generate_hour_chart(hour_counts, days)

        # Send image
        if image:
            await ctx.send(file=nextcord.File(fp=image, filename="most_watched_hours.png"))
        else:
            await ctx.send("Failed to generate chart.")

    @commands.command()
    async def most_watched_days(self, ctx, *args):
        """Displays a chart of the most-watched days of the week."""
        await ctx.trigger_typing()
        member, days = await self.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return

        data = await self.fetch_watch_history_with_genres(ctx, member, days)
        if data is None:
            return

        # Process data
        day_counts = self.calculate_day_counts(data)
        if day_counts.empty:
            await ctx.send("No data available.")
            return

        # Generate chart
        image = self.generate_day_chart(day_counts, days)

        # Send image
        if image:
            await ctx.send(file=nextcord.File(fp=image, filename="most_watched_days.png"))
        else:
            await ctx.send("Failed to generate chart.")

    @commands.command()
    async def most_active_users(self, ctx, *args):
        """Displays a chart of the most active users."""
        await ctx.trigger_typing()
        member, days = await self.parse_member_and_days(ctx, args)
        if member is not None:
            await ctx.send("This command does not support specifying a user.")
            return
        if days is None:
            return

        data = await self.fetch_watch_history_with_genres(ctx, None, days)
        if data is None:
            return

        # Process data
        user_counts = self.calculate_user_counts(data)
        if user_counts.empty:
            await ctx.send("No user data available.")
            return

        # Generate chart
        image = self.generate_user_chart(user_counts, days)

        # Send image
        if image:
            await ctx.send(file=nextcord.File(fp=image, filename="most_active_users.png"))
        else:
            await ctx.send("Failed to generate chart.")

    @commands.command()
    async def media_type_by_day(self, ctx, *args):
        """Displays a line graph of media types watched per day."""
        await ctx.trigger_typing()
        member, days = await self.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return

        data = await self.fetch_watch_history_with_genres(ctx, member, days)
        if data is None:
            return

        # Process data
        media_type_data = self.calculate_media_type_by_day(data)
        if media_type_data.empty:
            await ctx.send("No data available.")
            return

        # Generate chart
        image = self.generate_media_type_by_day_chart(media_type_data, days)

        # Send image
        if image:
            await ctx.send(file=nextcord.File(fp=image, filename="media_type_by_day.png"))
        else:
            await ctx.send("Failed to generate chart.")

    @commands.command()
    async def play_count_by_month(self, ctx, *args):
        """Displays a bar chart of total play count by month for the last 12 months."""
        await ctx.trigger_typing()
        member, days = await self.parse_member_and_days(ctx, args)
        if member is None and days is None:
            return
        if days == 30:
            days = 365  # Default to last 12 months if not specified

        data = await self.fetch_watch_history_with_genres(ctx, member, days)
        if data is None:
            return

        # Process data
        month_counts = self.calculate_play_count_by_month(data)
        if month_counts.empty:
            await ctx.send("No data available.")
            return

        # Generate chart
        image = self.generate_play_count_by_month_chart(month_counts, days)

        # Send image
        if image:
            await ctx.send(file=nextcord.File(fp=image, filename="play_count_by_month.png"))
        else:
            await ctx.send("Failed to generate chart.")

    async def fetch_watch_history_with_genres(self, ctx, member: nextcord.Member = None, days: int = 30):
        """Fetches the watch history and pairs it with genre data from the media cache."""
        # Get the timezone if not already set
        if self.timezone is None:
            self.timezone = await self.get_tautulli_timezone()

        # Access the media cache and lock from the MediaCommands cog
        media_commands_cog = self.bot.get_cog("MediaCommands")
        if media_commands_cog:
            media_cache = media_commands_cog.media_cache
            cache_lock = media_commands_cog.cache_lock
        else:
            logger.warning("Media cache is not available.")
            await ctx.send("Media cache is not available. Please try again later.")
            return None

        # Fetch watch history
        params = {
            "length": 10000,  # Adjust as needed
            "order_column": "date",
            "order_dir": "desc",
        }
        response = await self.tautulli.get_history(params=params)

        if response["response"]["result"] != "success":
            logger.error("Failed to retrieve watch history from Tautulli.")
            await ctx.send("Failed to retrieve watch history.")
            return None

        history_entries = response["response"]["data"]["data"]

        # Filter data by date range
        cutoff_timestamp = pd.Timestamp.now(tz=self.timezone) - pd.Timedelta(days=days)

        # Prepare user mapping if member is specified
        user_mapping = None
        if member:
            user_mapping = UserMappings.get_mapping_by_discord_id(str(member.id))
            if not user_mapping:
                await ctx.send(f"{member.display_name} is not mapped to a Plex user.")
                return None
            plex_username = user_mapping.get("plex_username")
        else:
            plex_username = None

        # Pair history entries with genres from media cache
        data = []
        async with cache_lock:
            for entry in history_entries:
                # Get the timestamp and localize it
                timestamp = entry.get("started")
                if timestamp:
                    entry_time = (
                        pd.to_datetime(timestamp, unit="s").tz_localize(pytz.utc).astimezone(self.timezone)
                    )
                    if entry_time < cutoff_timestamp:
                        continue  # Skip entries older than the cutoff
                else:
                    continue  # Skip entries without a timestamp

                # Filter by user if specified
                if plex_username and entry.get("user") != plex_username:
                    continue  # Skip entries not matching the specified user

                # Determine media type from the entry
                media_type_raw = entry.get("media_type", "unknown").lower()
                if media_type_raw == "movie":
                    media_type = "Movie"
                elif media_type_raw == "episode":
                    media_type = "TV"
                else:
                    media_type = "Other"

                # Try to get genres from the cache
                # For episodes, use grandparent_rating_key to get the show's genres
                rating_keys = [
                    str(entry.get("rating_key")),
                    str(entry.get("parent_rating_key")),
                    str(entry.get("grandparent_rating_key")),
                ]
                genres = []
                media_item = None
                for key in rating_keys:
                    if key:
                        media_item = next(
                            (item for item in media_cache if str(item.get("rating_key")) == key), None
                        )
                        if media_item:
                            genres = media_item.get("genres", [])
                            break

                data.append(
                    {
                        "title": entry.get("full_title") or entry.get("title"),
                        "started": entry_time,  # Localized datetime
                        "user": entry.get("user"),
                        "genres": genres,
                        "media_type": media_type,
                    }
                )
        return data

    def calculate_hour_counts(self, data):
        """Calculates the counts of hours from the watch history."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        df["hour"] = df["started"].dt.hour
        hour_counts = df["hour"].value_counts().sort_index()
        return hour_counts

    def generate_hour_chart(self, hour_counts, days):
        """Generates a bar chart for hour counts using Seaborn."""
        self.set_custom_style()
        plt.figure(figsize=(14, 6))  # Increased width
        plex_color = self.plex_orange
        sns.barplot(x=hour_counts.index, y=hour_counts.values, color=plex_color)
        utc_offset_str = self.get_utc_offset_str()
        plt.title(
            f"Most Watched Hours of the Day {utc_offset_str} (past {days}d)",
            color="white",
        )
        plt.xlabel("Hour of Day", color="white")
        plt.ylabel("Watch Count", color="white")
        plt.xticks(range(0, 24))
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(image_stream, format="png", facecolor=plt.gcf().get_facecolor())
        plt.close()
        image_stream.seek(0)
        return image_stream

    def calculate_day_counts(self, data):
        """Calculates the counts of days from the watch history."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        df["day"] = df["started"].dt.day_name()
        day_counts = (
            df["day"]
            .value_counts()
            .reindex(
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], fill_value=0
            )
        )
        return day_counts

    def generate_day_chart(self, day_counts, days):
        """Generates a bar chart for day counts using Seaborn."""
        self.set_custom_style()
        plt.figure(figsize=(14, 6))  # Increased width
        plex_color = self.plex_orange
        sns.barplot(x=day_counts.index, y=day_counts.values, color=plex_color)
        plt.title(f"Most Watched Days of the Week (past {days}d)", color="white")
        plt.xlabel("Days", color="white")
        plt.ylabel("Watch Count", color="white")
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(image_stream, format="png", facecolor=plt.gcf().get_facecolor())
        plt.close()
        image_stream.seek(0)
        return image_stream

    def calculate_user_counts(self, data):
        """Calculates the counts of users from the watch history."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        user_counts = df["user"].value_counts().head(10)
        return user_counts

    def generate_user_chart(self, user_counts, days):
        """Generates a bar chart for user counts using Seaborn."""
        self.set_custom_style()
        plt.figure(figsize=(14, 6))  # Increased width
        plex_color = self.plex_orange
        sns.barplot(x=user_counts.values, y=user_counts.index, color=plex_color)
        plt.title(f"Top 10 Most Active Users (past {days}d)", color="white")
        plt.xlabel("Watch Count", color="white")
        plt.ylabel("Users", color="white")
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(image_stream, format="png", facecolor=plt.gcf().get_facecolor())
        plt.close()
        image_stream.seek(0)
        return image_stream

    def calculate_media_type_by_day(self, data):
        """Calculates the counts of media types per day."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame()

        df["date"] = df["started"].dt.date
        media_type_counts = df.groupby(["date", "media_type"]).size().reset_index(name="count")
        return media_type_counts

    def generate_media_type_by_day_chart(self, media_type_data, days):
        """Generates a line graph for media types per day using Seaborn."""
        self.set_custom_style()
        plt.figure(figsize=(14, 6))  # Increased width
        media_type_pivot = media_type_data.pivot(index="date", columns="media_type", values="count").fillna(
            0
        )
        media_type_pivot.index = pd.to_datetime(media_type_pivot.index)
        media_type_pivot = media_type_pivot.sort_index()
        plex_colors = {
            "Movie": self.plex_orange,
            "TV": "#F6E0B6",
            "Other": "#F3D38A",
            "Unknown": "#F0C75E",
        }
        ax = media_type_pivot.plot(
            kind="line",
            marker="o",
            color=[plex_colors.get(col, "#FFFFFF") for col in media_type_pivot.columns],
        )

        utc_offset_str = self.get_utc_offset_str()
        plt.title(
            f"Media Types Watched Per Day {utc_offset_str} (past {days}d)",
            color="white",
        )
        plt.xlabel("Date", color="white")
        plt.ylabel("Watch Count", color="white")
        plt.legend(title="Media Type")
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(image_stream, format="png", facecolor=plt.gcf().get_facecolor())
        plt.close()
        image_stream.seek(0)
        return image_stream

    def calculate_play_count_by_month(self, data):
        """Calculates the total play counts per month."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        # Use dt.strftime to extract month and year without dropping timezone information
        df["month"] = df["started"].dt.strftime("%Y-%m")
        month_counts = df["month"].value_counts().sort_index()
        return month_counts

    def generate_play_count_by_month_chart(self, month_counts, days):
        """Generates a bar chart for play counts by month using Seaborn."""
        self.set_custom_style()
        plt.figure(figsize=(14, 6))  # Increased width
        plex_color = self.plex_orange

        # Convert month_counts.index to datetime for proper ordering
        months = pd.to_datetime(month_counts.index, format="%Y-%m")
        month_labels = months.strftime("%b")  # 'Jan', 'Feb', etc.

        sns.barplot(x=month_labels, y=month_counts.values, color=plex_color)
        plt.title(f"Total Play Count by Month (past {days}d)", color="white")
        plt.xlabel("Month", color="white")
        plt.ylabel("Play Count", color="white")
        plt.xticks(rotation=45)
        plt.tight_layout()

        image_stream = BytesIO()
        plt.savefig(image_stream, format="png", facecolor=plt.gcf().get_facecolor())
        plt.close()
        image_stream.seek(0)
        return image_stream

    def set_custom_style(self):
        """Sets a custom Seaborn style to match the Plex theme."""
        custom_style = {
            "axes.facecolor": self.plex_grey_dark,
            "figure.facecolor": self.plex_grey_dark,
            "axes.edgecolor": "#E5A00D",  # Plex orange for axes edges
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
    bot.add_cog(Data(bot))
