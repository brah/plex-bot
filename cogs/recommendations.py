# cogs/recommendations.py

import logging
import random
import asyncio

import nextcord
from nextcord.ext import commands

from utilities import UserMappings
from tautulli_wrapper import Tautulli

# Configure logging for this module
logger = logging.getLogger('plexbot.recommendations')
logger.setLevel(logging.INFO)


class Recommendations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get('tautulli')
        self.plex_embed_color = 0xE5A00D

    @commands.command()
    async def recommend(self, ctx, member: nextcord.Member = None):
        """Recommends movies or shows to a user based on their watch history.

        Usage:
        plex recommend [@member]

        If no member is specified, recommends based on the invoking user's history.
        """
        # Access the media cache and lock from the MediaCommands cog
        media_commands_cog = self.bot.get_cog('MediaCommands')
        if media_commands_cog:
            media_cache = media_commands_cog.media_cache
            cache_lock = media_commands_cog.cache_lock
        else:
            await ctx.send("Media cache is not available. Please try again later.")
            logger.warning("Media cache is not available.")
            return

        if not media_cache:
            await ctx.send("Media cache is currently empty. Please try again later.")
            logger.warning("Media cache is empty.")
            return

        member = member or ctx.author
        user_mapping = UserMappings.get_mapping_by_discord_id(str(member.id))

        if not user_mapping:
            await ctx.send(f"{member.display_name} is not mapped to a Plex user.")
            logger.warning(f"{member.display_name} is not mapped to a Plex user.")
            return

        plex_username = user_mapping.get('plex_username')

        # Fetch user's watch history
        params = {
            'user': plex_username,
            'length': 10000,  # Fetch a large number of history entries
            'order_column': 'date',
            'order_dir': 'desc',
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
            for key in ['rating_key', 'parent_rating_key', 'grandparent_rating_key']:
                if entry.get(key):
                    watched_rating_keys.add(str(entry[key]))

        logger.debug(f"Watched rating keys: {watched_rating_keys}")

        # Collect genres from watch history using media cache
        watched_genres = []
        async with cache_lock:
            for item in media_cache:
                item_keys = [
                    str(item.get('rating_key')),
                    str(item.get('parent_rating_key')),
                    str(item.get('grandparent_rating_key')),
                ]
                if any(key in watched_rating_keys for key in item_keys) and item.get('genres'):
                    watched_genres.extend(item['genres'])

        logger.debug(f"Watched genres: {watched_genres}")

        if not watched_genres:
            await ctx.send(f"Could not determine watched genres for {member.display_name}.")
            return

        # Identify top genres
        genre_counts = {}
        for genre in watched_genres:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1

        sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
        top_genres = [genre.title() for genre, count in sorted_genres[:3]]  # Capitalize genres

        if not top_genres:
            await ctx.send("No genres found in your watch history.")
            return

        # Inform the user about their top genres
        genres_formatted = ', '.join(top_genres)
        await ctx.send(f"Based on your favorite genres: **{genres_formatted}**")

        # Find media items in the top genres that the user hasn't watched yet
        recommendations = []
        async with cache_lock:
            for item in media_cache:
                item_keys = [
                    str(item.get('rating_key')),
                    str(item.get('parent_rating_key')),
                    str(item.get('grandparent_rating_key')),
                ]
                item_genres = [genre.title() for genre in item.get('genres', [])]
                if (
                    any(genre in top_genres for genre in item_genres) and
                    not any(key in watched_rating_keys for key in item_keys)
                ):
                    recommendations.append(item)

        if not recommendations:
            await ctx.send("No recommendations available at this time.")
            return

        # Select a few random recommendations
        selected_recommendations = random.sample(recommendations, min(3, len(recommendations)))

        # Create and send an embed with recommendations
        embed = nextcord.Embed(
            title=f"Recommendations for {member.display_name}",
            color=self.plex_embed_color,
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        for item in selected_recommendations:
            title = item.get('title') or 'Unknown Title'
            overview = item.get('summary', 'No description available.')
            year = item.get('year', 'Unknown')
            media_type = item.get('media_type', 'movie').capitalize()
            rating = item.get('rating') or 'N/A'

            # Get the list of Discord usernames of users who watched this item
            rating_key = item.get('rating_key')
            watched_users = await self.get_watched_users(rating_key)

            field_value = (
                f"**Summary**: {overview}\n"
                f"**Rating**: {rating}\n"
            )

            if watched_users:
                field_value += f"**Watched by**: {', '.join(watched_users)}\n"
            else:
                field_value += f"**Watched by**: No one yet!\n"

            embed.add_field(
                name=f"{title} ({media_type}, {year})",
                value=field_value,
                inline=False,
            )

        message = await ctx.send(embed=embed)

        # Add reactions for interactivity
        await message.add_reaction('👍')
        await message.add_reaction('👎')

        # Define a check function for reaction events
        def check(reaction, user):
            return (
                user == ctx.author and
                str(reaction.emoji) in ['👍', '👎'] and
                reaction.message.id == message.id
            )

        try:
            # Wait for a reaction from the user
            reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
            if str(reaction.emoji) == '👍':
                await ctx.send("Glad you like the recommendation! 🎉")
            elif str(reaction.emoji) == '👎':
                await ctx.send("Sorry to hear that. I'll try to improve future recommendations.")
        except asyncio.TimeoutError:
            await ctx.send("No reaction received. Hope you check out the recommendations!")

    async def get_watched_users(self, rating_key):
        """Retrieve a list of Discord usernames who have watched the media item."""
        user_stats_response = await self.tautulli.get_item_user_stats(rating_key)

        if user_stats_response["response"]["result"] != "success":
            logger.error(f"Failed to retrieve user stats for rating_key {rating_key}.")
            return []

        user_stats = user_stats_response["response"]["data"]

        watched_users = []
        for user_stat in user_stats:
            plex_username = user_stat.get('username')
            user_mapping = UserMappings.get_mapping_by_plex_username(plex_username)
            if user_mapping and not user_mapping.get('ignore', False):
                discord_id = user_mapping.get('discord_id')
                try:
                    discord_user = self.bot.get_user(int(discord_id))
                    if discord_user is None:
                        discord_user = await self.bot.fetch_user(int(discord_id))
                    if discord_user:
                        watched_users.append(discord_user.display_name)
                except Exception as e:
                    logger.error(f"Failed to fetch Discord user with ID {discord_id}: {e}")
        return watched_users

    def cog_unload(self):
        pass  # Add any cleanup code here if necessary


def setup(bot):
    bot.add_cog(Recommendations(bot))
