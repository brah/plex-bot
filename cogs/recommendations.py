import logging
import random
from collections import Counter

import nextcord
from nextcord.ext import commands

from config import config
from utilities import UserMappings, prepare_thumbnail_for_embed
from tautulli_wrapper import Tautulli
from media_cache import MediaCache


# Configure logging for this module
logger = logging.getLogger("plexbot.recommendations")
logger.setLevel(logging.INFO)


class RecommendationView(nextcord.ui.View):
    """Interactive recommendation browser: numbered buttons drill into each pick."""

    def __init__(self, ctx, member, recommendations: list, user_counts: list, plex_username: str, cog, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.member = member
        self.recommendations = recommendations
        self.user_counts = user_counts
        self.plex_username = plex_username
        self.cog = cog
        self.message = None

        # One numbered button per recommendation, plus a Back button for the detail view.
        for i in range(len(recommendations)):
            button = nextcord.ui.Button(label=str(i + 1), style=nextcord.ButtonStyle.primary)
            button.callback = self._make_detail_callback(i)
            self.add_item(button)
        self.back_button = nextcord.ui.Button(label="Back to list", style=nextcord.ButtonStyle.secondary, disabled=True)
        self.back_button.callback = self._on_back
        self.add_item(self.back_button)

    def _make_detail_callback(self, index: int):
        async def _callback(interaction: nextcord.Interaction):
            await self._show_detail(interaction, index)
        return _callback

    async def _build_overview(self) -> tuple:
        """Build the recommendations list embed and an optional thumbnail file."""
        embed = nextcord.Embed(
            title=f"Recommendations for {self.member.display_name}",
            description="Use the buttons below to see more details.",
            color=self.cog.plex_embed_color,
        )
        for idx, (item, user_count) in enumerate(zip(self.recommendations, self.user_counts), start=1):
            title = item.get("title") or "Unknown Title"
            overview = item.get("summary") or "No description available."
            if len(overview) > 150:
                overview = overview[:147] + "..."
            year = item.get("year", "Unknown")
            media_type = item.get("media_type", "movie").capitalize()
            genres = ", ".join(genre.title() for genre in item.get("genres", []))
            embed.add_field(
                name=f"{idx}. {title} ({media_type}, {year})",
                value=(
                    f"**Summary**: {overview}\n"
                    f"**Genres**: {genres}\n"
                    f"**Watched by**: {user_count} user{'s' if user_count != 1 else ''}\n"
                ),
                inline=False,
            )

        # Thumbnail: first recommendation with a poster, else the bot's avatar.
        file = None
        for item in self.recommendations:
            thumb = item.get("thumb")
            if thumb and thumb.strip():
                file, url = await prepare_thumbnail_for_embed(
                    self.cog.tautulli.tautulli_ip, thumb, use_https=self.cog.tautulli.use_https, api_key=self.cog.tautulli.api_key
                )
                if file and url:
                    embed.set_thumbnail(url=url)
                    break
                file = None
        if file is None and self.cog.bot.user and self.cog.bot.user.display_avatar:
            embed.set_thumbnail(url=self.cog.bot.user.display_avatar.url)
        return embed, file

    async def _build_detail(self, index: int) -> tuple:
        """Build the detail embed and optional poster file for one recommendation."""
        item = self.recommendations[index]
        title = item.get("title") or "Unknown Title"
        year = item.get("year", "Unknown")
        media_type = item.get("media_type", "movie").capitalize()
        embed = nextcord.Embed(title=f"{title} ({media_type}, {year})", color=self.cog.plex_embed_color)

        overview = item.get("summary") or "No description available."
        rating = item.get("rating") or "N/A"
        genres = ", ".join(genre.title() for genre in item.get("genres", []))
        watched_users = await self.cog.get_watched_users(item.get("rating_key"), exclude_user=self.plex_username)

        field_value = f"**Summary**: {overview}\n**Genres**: {genres}\n**Rating**: {rating}\n"
        field_value += f"**Watched by**: {', '.join(watched_users)}\n" if watched_users else "**Watched by**: No one yet!\n"
        embed.description = field_value
        embed.set_footer(text=f"Recommendation {index + 1} of {len(self.recommendations)}")

        file = None
        thumb = item.get("thumb")
        if thumb and thumb.strip():
            file, url = await prepare_thumbnail_for_embed(
                self.cog.tautulli.tautulli_ip, thumb, use_https=self.cog.tautulli.use_https, api_key=self.cog.tautulli.api_key
            )
            if file and url:
                embed.set_image(url=url)
        return embed, file

    async def _edit(self, interaction: nextcord.Interaction, embed: nextcord.Embed, file):
        # attachments=[] clears the previous poster; pass the new file if any.
        kwargs = {"embed": embed, "view": self, "attachments": []}
        if file:
            kwargs["file"] = file
        try:
            await interaction.edit_original_message(**kwargs)
        except nextcord.HTTPException as e:
            logger.warning(f"Failed to update recommendation view: {e}")
            if file:
                file.close()

    async def send_initial(self):
        embed, file = await self._build_overview()
        if file:
            self.message = await self.ctx.send(embed=embed, view=self, file=file)
        else:
            self.message = await self.ctx.send(embed=embed, view=self)

    async def _show_detail(self, interaction: nextcord.Interaction, index: int):
        # Ack first so the watcher/poster fetch can't blow the 3s interaction window.
        await interaction.response.defer()
        self.back_button.disabled = False
        embed, file = await self._build_detail(index)
        await self._edit(interaction, embed, file)

    async def _on_back(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        self.back_button.disabled = True
        embed, file = await self._build_overview()
        await self._edit(interaction, embed, file)

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Only the command author can use these buttons.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except nextcord.NotFound:
                pass


class Recommendations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.plex_embed_color = config.get("ui", "plex_embed_color", 0xE5A00D)

    @commands.command()
    async def recommend(self, ctx, member: nextcord.Member = None):
        """Recommends movies or shows to a user based on their watch history.

        Usage:
        plex recommend [@member]

        If no member is specified, recommends based on the invoking user's history.
        """
        try:
            member = member or ctx.author
            user_mapping = UserMappings.get_mapping_by_discord_id(str(member.id))

            if not user_mapping:
                await ctx.send(f"{member.display_name} is not mapped to a Plex user.")
                logger.warning(f"{member.display_name} is not mapped to a Plex user.")
                return

            plex_username = user_mapping.get("plex_username")

            # Let the user know we're working on it
            processing_msg = await ctx.send(f"Analyzing watch history for {member.display_name}...")

            # Fetch user's watch history
            params = {
                "user": plex_username,
                "length": 10000,  # Fetch a large number of history entries
                "order_column": "date",
                "order_dir": "desc",
            }
            response = await self.tautulli.get_history(params=params)

            if not Tautulli.check_response(response):
                await ctx.send("Failed to retrieve watch history from Plex.")
                logger.error("Failed to retrieve watch history from Tautulli.")
                return

            history_entries = (Tautulli.get_response_data(response, {}) or {}).get("data", [])

            if not history_entries:
                await ctx.send(f"No watch history found for {member.display_name}.")
                return

            # Collect all possible rating keys from the history entries
            watched_rating_keys = set()
            for entry in history_entries:
                for key in ["rating_key", "parent_rating_key", "grandparent_rating_key"]:
                    if entry.get(key):
                        watched_rating_keys.add(str(entry[key]))

            logger.debug(f"Found {len(watched_rating_keys)} watched rating keys")

            # Analyze watched genres
            await processing_msg.edit(
                content=f"Analyzing {len(watched_rating_keys)} watched items for {member.display_name}..."
            )
            watched_genres = await self.analyze_watched_genres(watched_rating_keys)

            if not watched_genres:
                await ctx.send(f"Could not determine watched genres for {member.display_name}.")
                return

            # Get the top genres
            top_genres = [genre.title() for genre, _ in watched_genres[:3]]  # Capitalize genres

            if not top_genres:
                await ctx.send("No genres found in your watch history.")
                return

            # Inform the user about their top genres
            genres_formatted = ", ".join(top_genres)
            await processing_msg.edit(
                content=f"Based on your favorite genres: **{genres_formatted}**\nFinding recommendations..."
            )

            # Find recommendations based on top genres and unwatched items
            recommendations = await self.get_recommendations(top_genres, watched_rating_keys)

            if not recommendations:
                await ctx.send("No recommendations available at this time.")
                return

            # Shuffle and select up to 3 recommendations
            random.shuffle(recommendations)
            selected_recommendations = recommendations[:3]

            # Get the number of unique users who watched each recommendation
            user_counts = []
            for item in selected_recommendations:
                rating_key = item.get("rating_key")
                watched_users = await self.get_watched_users(rating_key, return_count=True)
                user_counts.append(watched_users)

            # Replace the processing message with an interactive recommendations browser.
            await processing_msg.delete()
            timeout = config.get("commands", "recommendation_timeout", 180)
            view = RecommendationView(
                ctx, member, selected_recommendations, user_counts, plex_username, self, timeout=timeout
            )
            await view.send_initial()

        except Exception as e:
            logger.error(f"Error in recommend command: {e}", exc_info=True)
            await ctx.send(f"An error occurred while generating recommendations: {type(e).__name__}")

    async def analyze_watched_genres(self, watched_rating_keys):
        """Analyze the genres from user's watch history."""
        genre_counter = Counter()
        for rating_key in watched_rating_keys:
            item = await self.media_cache.get_item(rating_key)
            if item and item.get("genres"):
                for genre in item.get("genres", []):
                    genre_counter[genre.lower()] += 1
        return genre_counter.most_common()

    async def get_recommendations(self, top_genres, watched_rating_keys):
        """Get recommendations based on top genres and unwatched items."""
        top_genres_lower = [genre.lower() for genre in top_genres]
        recommendations = await self.media_cache.get_items(
            genres=top_genres_lower, exclude_rating_keys=watched_rating_keys, limit=50, random_sort=True
        )
        return recommendations

    async def get_watched_users(self, rating_key, exclude_user=None, return_count=False):
        """Retrieve a list of Discord usernames who have watched the media item."""
        user_stats_response = await self.tautulli.get_item_user_stats(rating_key)

        if not Tautulli.check_response(user_stats_response):
            logger.error(f"Failed to retrieve user stats for rating_key {rating_key}.")
            return 0 if return_count else []

        user_stats = Tautulli.get_response_data(user_stats_response, []) or []
        watched_users = []
        for user_stat in user_stats:
            plex_username = user_stat.get("username")
            if exclude_user and plex_username == exclude_user:
                continue
            user_mapping = UserMappings.get_mapping_by_plex_username(plex_username)
            if user_mapping and not user_mapping.get("ignore", False):
                discord_id = user_mapping.get("discord_id")
                try:
                    discord_user = self.bot.get_user(int(discord_id))
                    if discord_user is None:
                        discord_user = await self.bot.fetch_user(int(discord_id))
                    if discord_user:
                        watched_users.append(discord_user.display_name)
                except Exception as e:
                    logger.error(f"Failed to fetch Discord user with ID {discord_id}: {e}")
        if return_count:
            return len(watched_users)
        return watched_users


def setup(bot):
    bot.add_cog(Recommendations(bot))
