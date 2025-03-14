# cogs/plex_data.py

import logging
import asyncio
import pytz
import tzlocal
import datetime
import pandas as pd
from typing import Optional, Dict, List, Any, Tuple

import nextcord
from nextcord.ext import commands

from tautulli_wrapper import Tautulli
from media_cache import MediaCache
from utilities import UserMappings
from bot_config import BotConfig

# Configure logging for this module
logger = logging.getLogger("plexbot.data")
logger.setLevel(logging.INFO)


class PlexData(commands.Cog):
    """Handles data collection and processing from Plex/Tautulli."""

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.timezone = None  # Timezone will be fetched from Tautulli or local timezone
    
    async def get_tautulli_timezone(self) -> pytz.timezone:
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

    def get_utc_offset_str(self) -> str:
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

    async def parse_member_and_days(self, ctx, args) -> Tuple[Optional[nextcord.Member], Optional[int]]:
        """Parse command arguments to extract member and days parameters."""
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

    async def fetch_watch_history_with_genres(
        self, 
        ctx, 
        member: Optional[nextcord.Member] = None, 
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Fetches the watch history and pairs it with genre data from the media cache.
        
        Args:
            ctx: Command context
            member: Discord member to filter by (optional)
            days: Number of days to look back
            
        Returns:
            List of watch history entries with genre data
        """
        # Get the timezone if not already set
        if self.timezone is None:
            self.timezone = await self.get_tautulli_timezone()

        # Ensure media cache is valid
        await self.media_cache.ensure_cache_valid()

        # Fetch watch history
        params = {
            "length": BotConfig.DEFAULT_HISTORY_LENGTH,
            "order_column": "date",
            "order_dir": "desc",
        }
        response = await self.tautulli.get_history(params=params)

        if response["response"]["result"] != "success":
            logger.error("Failed to retrieve watch history from Tautulli.")
            await ctx.send("Failed to retrieve watch history.")
            return []

        history_entries = response["response"]["data"]["data"]

        # Filter data by date range
        cutoff_timestamp = pd.Timestamp.now(tz=self.timezone) - pd.Timedelta(days=days)

        # Prepare user mapping if member is specified
        user_mapping = None
        if member:
            user_mapping = UserMappings.get_mapping_by_discord_id(str(member.id))
            if not user_mapping:
                await ctx.send(f"{member.display_name} is not mapped to a Plex user.")
                return []
            plex_username = user_mapping.get("plex_username")
        else:
            plex_username = None

        # Pair history entries with genres from media cache
        data = []
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

            # Try each rating key until we find a matching item in the cache
            for key in rating_keys:
                if key != "None":  # Skip None values that were converted to strings
                    media_item = await self.media_cache.get_item(key)
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

    async def calculate_hour_counts(self, data: List[Dict]) -> pd.Series:
        """Calculates the counts of hours from the watch history."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        df["hour"] = df["started"].dt.hour
        hour_counts = df["hour"].value_counts().sort_index()
        return hour_counts

    async def calculate_day_counts(self, data: List[Dict]) -> pd.Series:
        """Calculates the counts of days from the watch history."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        df["day"] = df["started"].dt.day_name()
        day_counts = (
            df["day"]
            .value_counts()
            .reindex(
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], 
                fill_value=0
            )
        )
        return day_counts

    async def calculate_user_counts(self, data: List[Dict]) -> pd.Series:
        """Calculates the counts of users from the watch history."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        user_counts = df["user"].value_counts().head(10)
        return user_counts

    async def calculate_media_type_by_day(self, data: List[Dict]) -> pd.DataFrame:
        """Calculates the counts of media types per day."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame()

        df["date"] = df["started"].dt.date
        media_type_counts = df.groupby(["date", "media_type"]).size().reset_index(name="count")
        return media_type_counts

    async def calculate_play_count_by_month(self, data: List[Dict]) -> pd.Series:
        """Calculates the total play counts per month."""
        df = pd.DataFrame(data)
        if df.empty:
            return pd.Series(dtype=int)

        # Use dt.strftime to extract month and year without dropping timezone information
        df["month"] = df["started"].dt.strftime("%Y-%m")
        month_counts = df["month"].value_counts().sort_index()
        return month_counts


def setup(bot):
    bot.add_cog(PlexData(bot))