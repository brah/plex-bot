import logging
import random
import asyncio
from collections import Counter

import nextcord
from nextcord.ext import commands

from utilities import UserMappings, fetch_plex_image, prepare_thumbnail_for_embed
from tautulli_wrapper import Tautulli
from media_cache import MediaCache
from bot_config import BotConfig

import aiohttp
from io import BytesIO
from nextcord import File

# Configure logging for this module
logger = logging.getLogger("plexbot.recommendations")
logger.setLevel(logging.INFO)


class Recommendations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.plex_embed_color = BotConfig.PLEX_EMBED_COLOR

        # Mapping from number emoji to integer
        self.number_emojis = {
            "1️⃣": 0,
            "2️⃣": 1,
            "3️⃣": 2,
        }

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

            if response["response"]["result"] != "success":
                await ctx.send("Failed to retrieve watch history from Plex.")
                logger.error("Failed to retrieve watch history from Tautulli.")
                return

            history_entries = response["response"]["data"]["data"]

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

            # Create and send an embed with recommendations
            embed = nextcord.Embed(
                title=f"Recommendations for {member.display_name}",
                description="React with a number to see more details.",
                color=self.plex_embed_color,
            )

            # Try to set thumbnail from the first recommendation
            thumbnail_set = False
            for rec_item in selected_recommendations:
                thumb = rec_item.get("thumb")
                if thumb and thumb.strip():
                    file, attachment_url = await prepare_thumbnail_for_embed(
                        self.tautulli.tautulli_ip, thumb
                    )
                    if file and attachment_url:
                        embed.set_thumbnail(url=attachment_url)
                        thumbnail_set = True
                        break

            if not thumbnail_set and self.bot.user and self.bot.user.display_avatar:
                embed.set_thumbnail(url=self.bot.user.display_avatar.url)

            for idx, (item, user_count) in enumerate(zip(selected_recommendations, user_counts), start=1):
                title = item.get("title") or "Unknown Title"
                overview = item.get("summary", "No description available.")
                if len(overview) > 150:
                    overview = overview[:147] + "..."
                year = item.get("year", "Unknown")
                media_type = item.get("media_type", "movie").capitalize()
                genres = ", ".join([genre.title() for genre in item.get("genres", [])])

                field_value = (
                    f"**Summary**: {overview}\n"
                    f"**Genres**: {genres}\n"
                    f"**Watched by**: {user_count} user{'s' if user_count != 1 else ''}\n"
                )

                embed.add_field(
                    name=f"{idx}. {title} ({media_type}, {year})",
                    value=field_value,
                    inline=False,
                )

            # Replace the processing message with the recommendations
            await processing_msg.delete()

            # Send the embed with or without the thumbnail file
            if thumbnail_set:
                message = await ctx.send(embed=embed, file=file)
            else:
                message = await ctx.send(embed=embed)

            # Add number reactions with a delay between each to avoid rate limiting
            for emoji in list(self.number_emojis.keys())[: len(selected_recommendations)]:
                try:
                    await message.add_reaction(emoji)
                    await asyncio.sleep(1.0)
                except Exception as e:
                    logger.error(f"Failed to add reaction {emoji}: {e}")

            interaction_timeout = BotConfig.RECOMMENDATION_TIMEOUT  # 3 minutes
            detailed_message = None

            # Set up the event handlers for reactions
            def check_reaction(reaction, user):
                return (
                    reaction.message.id == message.id
                    and user == ctx.author
                    and str(reaction.emoji) in self.number_emojis
                )

            end_time = asyncio.get_event_loop().time() + interaction_timeout
            while asyncio.get_event_loop().time() < end_time:
                try:
                    reaction_event = await self.bot.wait_for(
                        "reaction_add", timeout=30.0, check=check_reaction
                    )

                    reaction, _ = reaction_event
                    emoji = str(reaction.emoji)
                    selected_index = self.number_emojis[emoji]
                    selected_item = selected_recommendations[selected_index]

                    # Show detailed info
                    detailed_message = await self.show_detailed_info(
                        ctx, selected_item, plex_username, detailed_message
                    )

                except asyncio.TimeoutError:
                    if asyncio.get_event_loop().time() >= end_time:
                        break
                except Exception as e:
                    logger.error(f"Error handling reaction: {e}")

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

    async def show_detailed_info(self, ctx, item, plex_username, detailed_message=None):
        """Shows detailed information for the selected media item."""
        try:
            title = item.get("title") or "Unknown Title"
            year = item.get("year", "Unknown")
            media_type = item.get("media_type", "movie").capitalize()

            embed = nextcord.Embed(
                title=f"{title} ({media_type}, {year})",
                color=self.plex_embed_color,
            )

            overview = item.get("summary", "No description available.")
            rating = item.get("rating") or "N/A"
            genres = ", ".join([genre.title() for genre in item.get("genres", [])])

            rating_key = item.get("rating_key")
            watched_users = await self.get_watched_users(rating_key, exclude_user=plex_username)

            field_value = f"**Summary**: {overview}\n**Genres**: {genres}\n**Rating**: {rating}\n"
            if watched_users:
                field_value += f"**Watched by**: {', '.join(watched_users)}\n"
            else:
                field_value += "**Watched by**: No one yet!\n"

            embed.description = field_value

            thumb = item.get("thumb")
            file = None

            if thumb and thumb.strip():
                logger.info(f"Processing thumbnail for {title}: {thumb}")
                file, attachment_url = await prepare_thumbnail_for_embed(self.tautulli.tautulli_ip, thumb)

                if file and attachment_url:
                    embed.set_image(url=attachment_url)
                else:
                    logger.warning(f"Failed to retrieve thumbnail image for {title}")
                    embed.add_field(
                        name="Image",
                        value="Failed to retrieve image.",
                        inline=False,
                    )
            else:
                logger.warning(f"No thumbnail available for {title}")
                embed.add_field(name="Image", value="No poster available.", inline=False)

            if detailed_message:
                try:
                    await detailed_message.delete()
                except Exception as e:
                    logger.error(f"Failed to delete previous detailed message: {e}")

            if file:
                detailed_message = await ctx.send(embed=embed, file=file)
            else:
                detailed_message = await ctx.send(embed=embed)

            return detailed_message

        except Exception as e:
            logger.error(f"Error in show_detailed_info: {e}", exc_info=True)
            await ctx.send(f"An error occurred while showing detailed information: {type(e).__name__}")
            return detailed_message

    async def get_watched_users(self, rating_key, exclude_user=None, return_count=False):
        """Retrieve a list of Discord usernames who have watched the media item."""
        user_stats_response = await self.tautulli.get_item_user_stats(rating_key)

        if user_stats_response["response"]["result"] != "success":
            logger.error(f"Failed to retrieve user stats for rating_key {rating_key}.")
            return [] if not return_count else 0

        user_stats = user_stats_response["response"]["data"]
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
