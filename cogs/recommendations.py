# cogs/recommendations.py

import logging
import random
import asyncio

import nextcord
from nextcord.ext import commands

from utilities import UserMappings
from tautulli_wrapper import Tautulli

import aiohttp
from io import BytesIO
from nextcord import File

# Configure logging for this module
logger = logging.getLogger('plexbot.recommendations')
logger.setLevel(logging.INFO)


class Recommendations(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get('tautulli')
        self.plex_embed_color = 0xE5A00D

        # Mapping from number emoji to integer
        self.number_emojis = {
            '1️⃣': 0,
            '2️⃣': 1,
            '3️⃣': 2,
        }

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

        # Shuffle and select up to 3 recommendations
        random.shuffle(recommendations)
        selected_recommendations = recommendations[:3]

        # Get the number of unique users who watched each recommendation
        user_counts = []
        for item in selected_recommendations:
            rating_key = item.get('rating_key')
            watched_users = await self.get_watched_users(rating_key, return_count=True)
            user_counts.append(watched_users)

        # Create and send an embed with recommendations
        embed = nextcord.Embed(
            title=f"Recommendations for {member.display_name}",
            description="React with a number to see more details.",
            color=self.plex_embed_color,
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        for idx, (item, user_count) in enumerate(zip(selected_recommendations, user_counts), start=1):
            title = item.get('title') or 'Unknown Title'
            overview = item.get('summary', 'No description available.')
            year = item.get('year', 'Unknown')
            media_type = item.get('media_type', 'movie').capitalize()
            genres = ', '.join([genre.title() for genre in item.get('genres', [])])

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

        message = await ctx.send(embed=embed)

        # Add number reactions
        for emoji in list(self.number_emojis.keys())[:len(selected_recommendations)]:
            await message.add_reaction(emoji)

        interaction_timeout = 180  # 3 minutes
        end_time = asyncio.get_event_loop().time() + interaction_timeout

        def add_check(reaction, user):
            return reaction.message.id == message.id and user == ctx.author and str(reaction.emoji) in self.number_emojis

        def remove_check(reaction, user):
            return reaction.message.id == message.id and user == ctx.author and str(reaction.emoji) in self.number_emojis

        # Keep a copy of the original embed
        original_embed = embed.copy()
        detailed_message = None

        while True:
            time_remaining = end_time - asyncio.get_event_loop().time()
            if time_remaining <= 0:
                break

            tasks = [
                self.bot.wait_for('reaction_add', timeout=time_remaining, check=add_check),
                self.bot.wait_for('reaction_remove', timeout=time_remaining, check=remove_check)
            ]

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for future in pending:
                future.cancel()

            if not done:
                break

            try:
                result = done.pop().result()
                reaction, user = result

                emoji = str(reaction.emoji)
                selected_index = self.number_emojis[emoji]
                selected_item = selected_recommendations[selected_index]

                # Check if it's a reaction add or remove
                if reaction.count > 1:
                    # Reaction added
                    detailed_message = await self.show_detailed_info(ctx, selected_item, plex_username, detailed_message)
                else:
                    # Reaction removed
                    # Delete the detailed message
                    if detailed_message:
                        await detailed_message.delete()
                        detailed_message = None
                    # Check if user has any other reactions
                    user_reactions = []
                    for reaction in message.reactions:
                        if str(reaction.emoji) in self.number_emojis:
                            users = await reaction.users().flatten()
                            if ctx.author in users:
                                user_reactions.append(str(reaction.emoji))
                    if user_reactions:
                        # User has another reaction, show that movie
                        emoji = user_reactions[0]
                        selected_index = self.number_emojis[emoji]
                        selected_item = selected_recommendations[selected_index]
                        detailed_message = await self.show_detailed_info(ctx, selected_item, plex_username, detailed_message)
                    else:
                        # No reactions left from the user, remove detailed message
                        if detailed_message:
                            await detailed_message.delete()
                            detailed_message = None
                        # Do not send any message when the timeout/expires
            except asyncio.TimeoutError:
                break

    async def show_detailed_info(self, ctx, item, plex_username, detailed_message=None):
        """Shows detailed information for the selected media item."""
        title = item.get('title') or 'Unknown Title'
        year = item.get('year', 'Unknown')
        media_type = item.get('media_type', 'movie').capitalize()
        thumb = item.get('thumb')  # Use 'thumb' key as in media_commands.py

        # Create a new embed with the poster and additional details
        embed = nextcord.Embed(
            title=f"{title} ({media_type}, {year})",
            color=self.plex_embed_color,
        )

        # Include additional details
        overview = item.get('summary', 'No description available.')
        rating = item.get('rating') or 'N/A'
        genres = ', '.join([genre.title() for genre in item.get('genres', [])])

        # Get the list of Discord usernames of users who watched this item
        rating_key = item.get('rating_key')
        watched_users = await self.get_watched_users(rating_key, exclude_user=plex_username)

        field_value = (
            f"**Summary**: {overview}\n"
            f"**Genres**: {genres}\n"
            f"**Rating**: {rating}\n"
        )

        if watched_users:
            field_value += f"**Watched by**: {', '.join(watched_users)}\n"
        else:
            field_value += f"**Watched by**: None of your friends have watched this yet!\n"

        embed.description = field_value

        if thumb:
            thumb_url = self.construct_image_url(thumb)
            if thumb_url:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(thumb_url) as response:
                            if response.status == 200:
                                image_data = BytesIO(await response.read())
                                file = nextcord.File(fp=image_data, filename="image.jpg")
                                embed.set_image(url="attachment://image.jpg")
                                # Send a new message with the embed and file
                                if detailed_message:
                                    await detailed_message.delete()
                                detailed_message = await ctx.send(embed=embed, file=file)
                                return detailed_message
                            else:
                                embed.add_field(
                                    name="Image",
                                    value="Failed to retrieve image.",
                                    inline=False,
                                )
                except Exception as e:
                    logger.error(f"Failed to retrieve thumbnail image: {e}")
                    embed.add_field(
                        name="Image",
                        value="Failed to retrieve image.",
                        inline=False,
                    )
        else:
            embed.description += "\nNo poster available."

        # Send the embed without the image
        if detailed_message:
            await detailed_message.delete()
        detailed_message = await ctx.send(embed=embed)
        return detailed_message

    def construct_image_url(self, thumb_key):
        """Construct the full image URL for thumbnails."""
        if thumb_key:
            tautulli_ip = self.tautulli.tautulli_ip
            return f"http://{tautulli_ip}/pms_image_proxy?img={thumb_key}&width=300&height=450&fallback=poster"
        return ""

    async def get_watched_users(self, rating_key, exclude_user=None, return_count=False):
        """Retrieve a list of Discord usernames who have watched the media item."""
        user_stats_response = await self.tautulli.get_item_user_stats(rating_key)

        if user_stats_response["response"]["result"] != "success":
            logger.error(f"Failed to retrieve user stats for rating_key {rating_key}.")
            return [] if not return_count else 0

        user_stats = user_stats_response["response"]["data"]

        watched_users = []
        for user_stat in user_stats:
            plex_username = user_stat.get('username')
            if exclude_user and plex_username == exclude_user:
                continue  # Exclude the requesting user
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
        if return_count:
            return len(watched_users)
        return watched_users

def setup(bot):
    bot.add_cog(Recommendations(bot))
