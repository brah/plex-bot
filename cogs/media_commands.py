# cogs/media_commands.py

import asyncio
import logging
import random
from datetime import timedelta
from io import BytesIO
from config import config

import aiohttp
import nextcord
from nextcord import File
from nextcord.ext import commands
import qbittorrentapi

from utilities import (
    Config,
    UserMappings,
    NoStopButtonMenuPages,
    MyEmbedDescriptionPageSource,
    fetch_plex_image,
    prepare_thumbnail_for_embed,
)
from tautulli_wrapper import Tautulli, TMDB
from media_cache import MediaCache
from bot_config import BotConfig

# Configure logging for this module
logger = logging.getLogger("plexbot.media_commands")
logger.setLevel(logging.INFO)


class MediaCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.tmdb: TMDB = bot.shared_resources.get("tmdb")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.plex_embed_color = BotConfig.PLEX_EMBED_COLOR
        self.plex_image = BotConfig.PLEX_IMAGE
        logger.info("MediaCommands cog initialized.")

    def cog_unload(self):
        """Clean up resources when the cog is unloaded."""
        self.bot.loop.create_task(self.tautulli.close())
        if self.tmdb:
            self.bot.loop.create_task(self.tmdb.close())

    @commands.command()
    async def recent(self, ctx, amount: int = BotConfig.DEFAULT_RECENT_COUNT):
        """Displays recently added media items.

        Usage:
        plex recent [amount]

        Examples:
        plex recent
        plex recent 20
        """
        fields = []
        try:
            response = await self.tautulli.get_recently_added(count=amount)
            if response.get("response", {}).get("result") != "success":
                await ctx.send("Failed to retrieve recent additions.")
                logger.error("Failed to retrieve recent additions from Tautulli.")
                return

            for entry in response["response"]["data"]["recently_added"]:
                if entry.get("originally_available_at") == "":
                    continue
                # Work around to show full show name alongside episode name
                if entry.get("grandparent_title"):
                    entry["title"] = f"{entry['grandparent_title']} - {entry['title']}"
                if entry.get("rating") == "":
                    entry["rating"] = "N/A"
                entry_data = {
                    "description": f"**🎥 {entry['title']}** 🕗 {entry['originally_available_at']} 🍅: {entry['rating']}/10\n{entry.get('summary', '')}\n",
                    "thumb_key": entry.get("thumb", ""),
                }
                fields.append(entry_data)

            tautulli_ip = self.tautulli.tautulli_ip  # Tautulli webserver IP
            pages = NoStopButtonMenuPages(
                source=MyEmbedDescriptionPageSource(fields, tautulli_ip),
            )
            await pages.start(ctx)
        except Exception as e:
            logger.error(f"Failed to retrieve recent additions: {e}")
            await ctx.send("Failed to retrieve recent additions.")

    @commands.command()
    async def random(self, ctx, *args):
        """Displays a random media item from the Plex libraries."""
        # Parse arguments
        media_type, genre = self.parse_random_args(args)
        logger.info(f"Searching for {genre} of mediatype {media_type}")

        try:
            # Check if the cache is empty
            cache_count = len(await self.media_cache.get_items(limit=1))
            if cache_count == 0:
                # Cache is empty, try refreshing it
                logger.warning("Media cache is empty, attempting to refresh...")
                await ctx.send("The media cache appears to be empty. I'll try to refresh it now...")

                await self.media_cache.update_cache()

                # Check again after refresh
                cache_count = len(await self.media_cache.get_items(limit=1))
                if cache_count == 0:
                    await ctx.send(
                        "I couldn't find any media items. Please check the Tautulli connection and try again later."
                    )
                    return
                else:
                    await ctx.send(f"Cache has been refreshed with {cache_count} items.")

            # Get filtered media items from cache
            items = await self.media_cache.get_items(
                media_type=media_type, genres=[genre] if genre else None, random_sort=True, limit=100
            )

            logger.debug(f"Found {len(items)} items matching the criteria")

            if not items:
                await ctx.send("No media items found matching the criteria.")
                return

            # Select a random item
            random_item = random.choice(items)
            await self.send_movie_embed(ctx, random_item)
        except Exception as e:
            logger.error(f"Error in random command: {e}", exc_info=True)
            await ctx.send(f"An error occurred while looking for a random item: {type(e).__name__}")

    def parse_random_args(self, args):
        """Parse the arguments for the random command."""
        media_type = None
        genre = None

        if args:
            # Lowercase all arguments for case-insensitive comparison
            args_lower = [arg.lower() for arg in args]
            # Possible media types
            media_types = ["movie", "tv", "any"]
            # Check for media type in args
            media_type_in_args = set(args_lower) & set(media_types)
            if media_type_in_args:
                media_type = media_type_in_args.pop()
                # Remove the media type from args
                args_lower.remove(media_type)
            # The remaining args are genre
            if args_lower:
                genre = " ".join(args_lower).lower()
        return media_type, genre

    async def send_movie_embed(self, ctx, item):
        """Send an embed with the media item's details."""
        try:
            # Construct the embed using item data
            embed = nextcord.Embed(
                title=f"{item['title']} ({item['year']})",
                color=nextcord.Color.random(),
                description=item.get("summary", "No summary available."),
            )

            # Add rating
            if item.get("rating"):
                embed.add_field(name="Rating", value=f"{item['rating']}/10", inline=True)

            # Add genres
            if item.get("genres"):
                genres_formatted = ", ".join([g.title() for g in item["genres"]])
                embed.add_field(name="Genres", value=genres_formatted, inline=True)

            # Add play count
            play_count = item.get("play_count", 0)
            if play_count == 0:
                play_count_text = "Never watched"
            else:
                play_count_text = f"{play_count} time{'s' if play_count != 1 else ''}"
            embed.add_field(name="Play Count", value=play_count_text, inline=True)

            # Add last played
            if item.get("last_played"):
                last_played = f"<t:{item.get('last_played', '')}:D>"
                embed.add_field(name="Last Played", value=last_played, inline=True)

            # Add thumbnail if available
            if item.get("thumb"):
                file, attachment_url = await prepare_thumbnail_for_embed(
                    self.tautulli.tautulli_ip, item["thumb"]
                )
                if file and attachment_url:
                    embed.set_image(url=attachment_url)
                    await ctx.send(file=file, embed=embed)
                    return

            # If we got here, either there's no thumbnail or we failed to retrieve it
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send movie embed: {e}")
            await ctx.send("Failed to display the media item.")

    # The construct_image_url method has been replaced by the utility functions in utilities.py

    @commands.command()
    async def watchers(self, ctx):
        """Display current Plex watchers with details about their activity."""
        try:
            response = await self.tautulli.get_activity()
            if not response or response.get("response", {}).get("result") != "success":
                await ctx.send("Failed to retrieve current Plex activity.")
                logger.error("Failed to retrieve activity from Tautulli.")
                return

            sessions = response["response"]["data"]["sessions"]
            if not sessions:
                await ctx.send("No one is currently watching Plex.")
                return

            # Load ignored users list
            user_data = UserMappings.load_user_mappings()
            ignored_users = {user["plex_username"] for user in user_data if user.get("ignore", False)}

            embed = nextcord.Embed(title="Plex Watchers", color=self.plex_embed_color)
            embed.set_thumbnail(url=self.plex_image)

            active_users = 0

            for user in sessions:
                if user["username"] in ignored_users:
                    continue  # Skip ignored users

                active_users += 1
                try:
                    # Process user state
                    state = user.get("state", "unknown").capitalize()
                    state_symbol = {
                        "Playing": "▶️",
                        "Paused": "⏸️",
                        "Buffering": "⏳",
                    }.get(state, state)

                    try:
                        view_offset_ms = int(user.get("view_offset", 0))
                        duration_ms = int(user.get("duration", 0))
                        elapsed_time = str(timedelta(milliseconds=view_offset_ms))

                        # Calculate progress percentage only if we have valid duration
                        progress_text = ""
                        if duration_ms > 0:
                            percentage = min(100, (view_offset_ms / duration_ms) * 100)
                            progress_text = f" ({percentage:.1f}%)"
                    except (ValueError, TypeError):
                        elapsed_time = "Unknown"
                        progress_text = ""
                        logger.warning(f"Invalid time data for user {user['friendly_name']}")

                    # Build user field with detailed information
                    user_field = (
                        f"Watching **{user['full_title']}**\n"
                        f"Quality: **{user.get('quality_profile', 'Unknown')}**\n"
                        f"State: **{state_symbol}**\n"
                        f"Elapsed Time: **{elapsed_time}**{progress_text}"
                    )

                    embed.add_field(
                        name=user["friendly_name"],
                        value=user_field,
                        inline=False,
                    )
                except Exception as e:
                    logger.error(f"Error processing user {user.get('friendly_name', 'unknown')}: {e}")
                    continue

            # Set appropriate description based on number of active watchers
            if active_users > 0:
                embed.description = f"**{active_users}** users are currently watching Plex 🐒"
            else:
                embed.description = "No one is active on Plex at the moment. 😔✊"

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to retrieve watchers: {e}", exc_info=True)
            await ctx.send("Failed to retrieve watchers.")

    @commands.command(name="tv")
    async def lookup_tv(self, ctx, *, show_name: str = None):
        """
        Lookup information about a TV show in your Plex library.

        Usage:
        plex tv <show name>

        Example:
        plex tv Game of Thrones
        """
        logger.info(f"TV lookup command invoked by {ctx.author.name} with query: '{show_name}'")
        
        if not show_name:
            await ctx.send("Please provide a TV show name to search for.")
            logger.warning("TV lookup command used without a show name")
            return
        
        await self.lookup_media(ctx, show_name, media_type="tv")

    @commands.command(name="movie")
    async def lookup_movie(self, ctx, *, movie_name: str = None):
        """
        Lookup information about a movie in your Plex library.

        Usage:
        plex movie <movie name>

        Example:
        plex movie The Godfather
        """
        logger.info(f"Movie lookup command invoked by {ctx.author.name} with query: '{movie_name}'")
        
        if not movie_name:
            await ctx.send("Please provide a movie name to search for.")
            logger.warning("Movie lookup command used without a movie name")
            return
        
        await self.lookup_media(ctx, movie_name, media_type="movie")


    async def lookup_media(self, ctx, title: str, media_type: str = None):
        """
        Common implementation for looking up TV shows and movies.

        Args:
            ctx: Command context
            title: Title to search for
            media_type: "tv" or "movie" to filter results
        """
        if not title:
            await ctx.send("Please provide a title to search for.")
            logger.warning(f"lookup_media called with empty title by {ctx.author.name}")
            return

        # Set media type for filtering
        search_media_type = None
        if media_type == "tv":
            search_media_type = "show"
            media_type_str = "TV Show"
            tautulli_type = "show"
        elif media_type == "movie":
            search_media_type = "movie"
            media_type_str = "Movie"
            tautulli_type = "movie"
        else:
            media_type_str = "Content"
            tautulli_type = None
        
        logger.info(f"Looking up {media_type_str}: '{title}' for {ctx.author.name}")

        # Let the user know we're searching
        search_msg = await ctx.send(
            f"🔍 Searching for {media_type_str}: **{title}**..."
        )

        try:
            # First, try direct API search through Tautulli (more reliable for exact titles)
            direct_results = []
            best_direct_match = None
            
            try:
                await search_msg.edit(content=f"🔍 Searching for {media_type_str}: **{title}** (Direct API search...)")
                
                # Get all libraries of the appropriate type
                libraries_response = await self.tautulli.get_libraries()
                if libraries_response.get("response", {}).get("result") == "success":
                    libraries = libraries_response["response"]["data"]
                    
                    # Filter libraries by type if needed
                    if tautulli_type:
                        libraries = [lib for lib in libraries if lib.get("section_type") == tautulli_type]
                    
                    logger.info(f"Searching for '{title}' in {len(libraries)} libraries via direct API")
                    
                    # Search each library
                    for library in libraries:
                        search_params = {
                            "section_id": library["section_id"],
                            "search": title,
                            "length": 10
                        }
                        
                        search_response = await self.tautulli.get_library_media_info(**search_params)
                        if search_response.get("response", {}).get("result") == "success":
                            library_results = search_response["response"]["data"]["data"]
                            if library_results:
                                logger.info(f"Found {len(library_results)} results via direct API search in library {library['section_name']}")
                                direct_results.extend(library_results)
                                
                                # Check for exact match in this library
                                for item in library_results:
                                    if item.get("title", "").lower() == title.lower():
                                        logger.info(f"Found exact direct match: {item.get('title')} in {library['section_name']}")
                                        best_direct_match = item
                                        break
                                
                                # If we found an exact match, no need to check other libraries
                                if best_direct_match:
                                    break
                
                # If no exact match but we have results, use the first one
                if not best_direct_match and direct_results:
                    # Try to find closest match
                    import difflib
                    
                    # Sort by similarity score
                    direct_results.sort(
                        key=lambda x: difflib.SequenceMatcher(None, x.get("title", "").lower(), title.lower()).ratio(),
                        reverse=True
                    )
                    
                    best_direct_match = direct_results[0]
                    similarity = difflib.SequenceMatcher(None, best_direct_match.get("title", "").lower(), title.lower()).ratio()
                    logger.info(f"Best direct match: {best_direct_match.get('title')} (similarity: {similarity:.2f})")
                    
            except Exception as e:
                logger.error(f"Error in direct API search: {e}", exc_info=True)
                # Continue to cache search even if direct search fails
            
            # If we got a direct match, use it
            if best_direct_match:
                logger.info(f"Using direct API result: {best_direct_match.get('title')}")
                
                # Get full metadata for this direct match
                rating_key = best_direct_match.get("rating_key")
                metadata_response = await self.tautulli.get_metadata(rating_key)
                
                if metadata_response.get("response", {}).get("result") == "success":
                    logger.info(f"Successfully retrieved metadata for direct match: {best_direct_match.get('title')}")
                    metadata = metadata_response["response"]["data"]
                    
                    # Now we can build the embed and display the result
                    await self._display_media_info(ctx, search_msg, metadata, best_direct_match.get("title"))
                    return
                else:
                    logger.warning(f"Failed to get metadata for direct match, falling back to cache search")
            else:
                logger.info(f"No direct API match found for '{title}', trying cache search")
            
            # If direct search didn't work or failed, fall back to cache search
            await search_msg.edit(content=f"🔍 Searching for {media_type_str}: **{title}** (Checking media cache...)")
            
            # Check if the media cache is initialized
            if not self.media_cache:
                logger.error("Media cache is not available - shared resource missing")
                await search_msg.edit(content="Error: Media cache service is not available.")
                return
                
            # Check if the cache is valid
            logger.debug("Ensuring cache is valid before search")
            await self.media_cache.ensure_cache_valid()
            logger.info(f"Cache validation complete, proceeding with search for '{title}'")

            # Use enhanced search if available
            if hasattr(self.media_cache, "enhanced_search"):
                logger.info("Using enhanced search method")
                items = await self.media_cache.enhanced_search(title, media_type=media_type, limit=20)
            else:
                # Fall back to standard search
                logger.info("Using standard search method")
                items = await self.media_cache.search(title, limit=20)
                
                # Filter by media type if specified
                if search_media_type:
                    filtered_items = [item for item in items if item.get("media_type") == search_media_type]
                    logger.info(f"After filtering for {search_media_type}: {len(filtered_items)} results")
                    items = filtered_items

            if not items:
                # If we got here, both direct search and cache search failed
                logger.warning(f"No {media_type_str.lower()} found matching '{title}'")
                await search_msg.edit(
                    content=f"No {media_type_str.lower()} found matching '**{title}**'."
                )
                return

            # Log first few items for debugging
            for i, item in enumerate(items[:3]):
                logger.debug(f"Result {i+1}: {item.get('title')} ({item.get('media_type')}, key: {item.get('rating_key')})")

            # If we have multiple matches, try to find the best one
            best_match = None

            # Exact title match gets priority
            for item in items:
                if item.get("title", "").lower() == title.lower():
                    logger.info(f"Found exact title match: {item.get('title')} (rating_key: {item.get('rating_key')})")
                    best_match = item
                    break

            # If no exact match, use the first result
            if not best_match:
                best_match = items[0]
                logger.info(f"No exact match found, using first result: {best_match.get('title')}")

            # Get detailed metadata for this item
            rating_key = best_match.get("rating_key")
            if not rating_key:
                logger.error(f"Selected item has no rating_key: {best_match}")
                await search_msg.edit(
                    content="Found a match but couldn't retrieve its details. Try a different search."
                )
                return

            # Get full metadata from Tautulli
            logger.debug(f"Fetching metadata for rating_key: {rating_key}")
            metadata_response = await self.tautulli.get_metadata(rating_key)
            
            if not metadata_response:
                logger.error(f"No response from Tautulli when fetching metadata for rating_key: {rating_key}")
                await search_msg.edit(content="Error connecting to Tautulli. Please try again later.")
                return
                
            if metadata_response.get("response", {}).get("result") != "success":
                error_msg = metadata_response.get("response", {}).get("message", "Unknown error")
                logger.error(f"Failed to get metadata: {error_msg}")
                await search_msg.edit(
                    content=f"Error retrieving details: {error_msg}"
                )
                return

            logger.info(f"Successfully retrieved metadata for {best_match.get('title')}")
            metadata = metadata_response["response"]["data"]
            
            # Display the media information
            await self._display_media_info(ctx, search_msg, metadata, best_match.get("title"))
            
            # If we had multiple matches, mention this
            if len(items) > 1:
                other_titles = [
                    item.get("title") for item in items[:5] if item.get("rating_key") != rating_key
                ]
                if other_titles:
                    other_matches = ", ".join([f"**{title}**" for title in other_titles])
                    await ctx.send(
                        f"📌 Other possible matches: {other_matches}"
                        + (f" and {len(items) - 5} more..." if len(items) > 5 else "")
                    )

        except Exception as e:
            logger.error(f"Error in lookup_media: {e}", exc_info=True)
            await search_msg.edit(
                content=f"An error occurred while looking up information: {type(e).__name__}\n{str(e)}"
            )

    async def _display_media_info(self, ctx, search_msg, metadata, title):
        """Helper method to display media information in an embed."""
        try:
            # Get user watch statistics
            logger.debug(f"Fetching user statistics for rating_key: {metadata.get('rating_key')}")
            user_stats_response = await self.tautulli.get_item_user_stats(metadata.get('rating_key'))
            watch_time_response = await self.tautulli.get_item_watch_time_stats(metadata.get('rating_key'))

            # Create the embed with all the gathered information
            embed = nextcord.Embed(
                title=metadata.get("title", "Unknown Title"),
                description=metadata.get("summary", "No summary available."),
                color=self.plex_embed_color,
            )

            # Add basic metadata
            if metadata.get("year"):
                embed.title += f" ({metadata.get('year')})"

            if metadata.get("content_rating"):
                embed.add_field(name="Rating", value=metadata.get("content_rating"), inline=True)

            if metadata.get("duration"):
                # Convert duration from milliseconds to minutes
                duration_ms = int(metadata.get("duration", 0))
                logger.debug(f"Raw duration value: {duration_ms}")
                
                if duration_ms > 0:
                    duration_min = int(duration_ms / 60000)
                    embed.add_field(name="Duration", value=f"{duration_min} minutes", inline=True)
                    logger.debug(f"Converted duration: {duration_min} minutes")

            if metadata.get("genres"):
                genres = ", ".join([g.title() for g in metadata.get("genres", [])])
                embed.add_field(name="Genres", value=genres or "None listed", inline=True)

            # Add TV show specific metadata
            if metadata.get("media_type") == "show":
                embed.add_field(
                    name="Seasons", value=metadata.get("children_count", "Unknown"), inline=True
                )

                # Try to get total episodes too
                if metadata.get("grandchildren_count"):
                    embed.add_field(
                        name="Episodes", value=metadata.get("grandchildren_count", "Unknown"), inline=True
                    )

                # Add air dates if available
                if metadata.get("originally_available_at"):
                    embed.add_field(
                        name="First Aired", value=metadata.get("originally_available_at"), inline=True
                    )

                # Add status if available (Continuing or Ended)
                if "status" in metadata:
                    embed.add_field(name="Status", value=metadata.get("status", "Unknown"), inline=True)

            # Add watch statistics from user_stats
            if user_stats_response and user_stats_response.get("response", {}).get("result") == "success":
                user_stats = user_stats_response["response"]["data"]
                logger.debug(f"User stats data available: {len(user_stats) if user_stats else 0} records")

                if user_stats:
                    # Total play count
                    total_plays = sum(user.get("total_plays", 0) for user in user_stats)
                    embed.add_field(name="Total Plays", value=str(total_plays), inline=True)

                    # Get Discord usernames where possible
                    user_list = []
                    for user_stat in user_stats:
                        plex_username = user_stat.get("username")
                        play_count = user_stat.get("total_plays", 0)
                        user_mapping = UserMappings.get_mapping_by_plex_username(plex_username)

                        if user_mapping and not user_mapping.get("ignore", False):
                            discord_id = user_mapping.get("discord_id")
                            try:
                                discord_user = self.bot.get_user(int(discord_id))
                                if discord_user:
                                    user_list.append(f"**{discord_user.display_name}**: {play_count} plays")
                                else:
                                    user_list.append(f"**{plex_username}**: {play_count} plays")
                            except Exception as e:
                                logger.error(f"Error getting Discord user for ID {discord_id}: {e}")
                                user_list.append(f"**{plex_username}**: {play_count} plays")
                        else:
                            user_list.append(f"**{plex_username}**: {play_count} plays")

                    # Only show the top 5 users
                    if user_list:
                        embed.add_field(
                            name="Watched By",
                            value="\n".join(user_list[:5])
                            + (f"\n...and {len(user_list) - 5} more" if len(user_list) > 5 else ""),
                            inline=False,
                        )

            # Add watch time statistics
            if watch_time_response and watch_time_response.get("response", {}).get("result") == "success":
                watch_time_stats = watch_time_response["response"]["data"]
                logger.debug(f"Watch time stats available: {len(watch_time_stats) if watch_time_stats else 0} records")
                
                if watch_time_stats and isinstance(watch_time_stats, list) and len(watch_time_stats) > 0:
                    # It's a list, so use the first item (usually there's only one)
                    stats_item = watch_time_stats[0]

                    # Total watch time
                    total_time = stats_item.get("total_time", 0)
                    if total_time > 0:
                        import datetime

                        # Format total_time (seconds) into a readable format
                        total_time_str = str(datetime.timedelta(seconds=total_time))
                        embed.add_field(name="Total Watch Time", value=total_time_str, inline=True)

                    # Add when it was last watched
                    if stats_item.get("last_watch"):
                        last_watch = int(stats_item.get("last_watch"))
                        embed.add_field(name="Last Watched", value=f"<t:{last_watch}:R>", inline=True)

            # Try to get the thumbnail
            thumb = metadata.get("thumb")
            file = None

            if thumb:
                logger.debug(f"Attempting to fetch thumbnail: {thumb}")
                try:
                    file, attachment_url = await prepare_thumbnail_for_embed(self.tautulli.tautulli_ip, thumb)
                    if file and attachment_url:
                        embed.set_image(url=attachment_url)
                        logger.debug("Successfully added thumbnail to embed")
                    else:
                        logger.warning(f"Failed to get thumbnail for {metadata.get('title')}")
                except Exception as e:
                    logger.error(f"Error preparing thumbnail: {e}")

            # Send the result
            try:
                if file:
                    await search_msg.edit(content=None, embed=embed, file=file)
                else:
                    await search_msg.edit(content=None, embed=embed)
                    
                logger.info(f"Successfully displayed information for {title}")
            except Exception as e:
                logger.error(f"Error sending embed: {e}")
                await ctx.send(f"Error displaying information: {str(e)}")
        except Exception as e:
            logger.error(f"Error in _display_media_info: {e}", exc_info=True)
            await search_msg.edit(content=f"Error displaying media information: {type(e).__name__}")

    @commands.command()
    async def downloading(self, ctx):
        """Display the current downloading torrents in qBittorrent."""
        # Try to instantiate the qBittorrent client
        try:
            qbit_ip = config.get("qbittorrent", "ip")
            qbit_port = config.get("qbittorrent", "port")
            qbit_username = config.get("qbittorrent", "username")
            qbit_password = config.get("qbittorrent", "password")

            # Check if all required configurations are present
            if not all([qbit_ip, qbit_port, qbit_username, qbit_password]):
                missing_config = []
                if not qbit_ip:
                    missing_config.append("IP")
                if not qbit_port:
                    missing_config.append("Port")
                if not qbit_username:
                    missing_config.append("Username")
                if not qbit_password:
                    missing_config.append("Password")

                await ctx.send(
                    f"qBittorrent is not properly configured. Missing: {', '.join(missing_config)}"
                )
                logger.error(f"Missing qBittorrent configuration: {', '.join(missing_config)}")
                return

            logger.debug(
                "Creating qbittorrent Client with IP=%s, Port=%s, Username=%s",
                qbit_ip,
                qbit_port,
                qbit_username,
            )

            qbt_client = qbittorrentapi.Client(
                host=qbit_ip,
                port=qbit_port,
                username=qbit_username,
                password=qbit_password,
            )

            # Login to qBittorrent
            qbt_client.auth_log_in()
            logger.debug("Successfully logged into qBittorrent? %s", qbt_client.is_logged_in)

        except qbittorrentapi.LoginFailed:
            logger.exception("Login to qBittorrent failed. Check username and password.")
            await ctx.send("Failed to log in to qBittorrent. Check your credentials.")
            return
        except qbittorrentapi.APIConnectionError:
            logger.exception("Connection to qBittorrent failed. Check network and host settings.")
            await ctx.send("Failed to connect to qBittorrent. Check your network settings.")
            return
        except Exception as err:
            logger.exception("Unexpected error connecting to qBittorrent.")
            await ctx.send("I'm having trouble connecting to qBittorrent right now.")
            return

        # Pull downloading torrents
        downloads_embed = nextcord.Embed(
            title="qBittorrent Live Downloads",
            color=BotConfig.QBIT_EMBED_COLOR,
        )
        downloads_embed.set_thumbnail(url=BotConfig.QBIT_IMAGE)

        try:
            # Get all downloading torrents
            torrents_downloading = [
                t
                for t in qbt_client.torrents.info.downloading()
                if t.state not in ["pausedDL", "pausedUP", "stopped"] and not t.state_enum.is_paused
            ]
            logger.debug("Fetched %d torrents in downloading status.", len(torrents_downloading))
        except Exception as e:
            logger.exception("Error retrieving downloading torrents.")
            await ctx.send("I'm having trouble retrieving the downloading torrents.")
            return

        if not torrents_downloading:
            downloads_embed.description = "There are no torrents currently downloading."
            await ctx.send(embed=downloads_embed)
            return

        # Sort torrents by progress (descending)
        torrents_downloading.sort(key=lambda x: x.progress, reverse=True)

        for download in torrents_downloading:
            # Format download speed with appropriate units
            dl_speed = download.dlspeed
            if dl_speed > 1_000_000:  # If more than 1 MB/s
                dl_speed_str = f"{dl_speed * 1.0e-6:.2f} MB/s"
            else:
                dl_speed_str = f"{dl_speed * 1.0e-3:.0f} KB/s"

            # Format ETA
            eta = download.eta
            if eta == 8640000:  # qBittorrent uses this value for unknown ETA
                eta_str = "unknown"
            elif eta > 86400:  # More than a day
                eta_str = f"{eta / 86400:.1f} days"
            elif eta > 3600:  # More than an hour
                eta_str = f"{eta / 3600:.1f} hours"
            else:
                eta_str = f"{eta / 60:.0f} minutes"

            downloads_embed.add_field(
                name=f"⏳ {download.name}",
                value=(
                    f"**Progress**: {download.progress * 100:.2f}%, "
                    f"**Size:** {download.size * 1e-9:.2f} GB, "
                    f"**ETA:** {eta_str}, "
                    f"**DL:** {dl_speed_str}"
                ),
                inline=False,
            )

        await ctx.send(embed=downloads_embed)

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def refresh_cache(self, ctx):
        """Manually refresh the media cache."""
        refresh_message = await ctx.send("Refreshing media cache...")
        try:
            # Use our MediaCache class to update the cache
            await self.media_cache.update_cache()
            await refresh_message.edit(content="Media cache has been refreshed successfully.")
            logger.info("Media cache has been manually refreshed.")
        except Exception as e:
            logger.error(f"Failed to refresh media cache: {e}")
            await refresh_message.edit(content="Failed to refresh media cache. Check logs for details.")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def check_media_cache(self, ctx):
        """Debug command to check media cache content."""
        try:
            # First check if cache is initialized
            if not self.media_cache:
                await ctx.send("Error: Media cache is not initialized.")
                return
                
            # Get basic cache stats
            cache_size = len(await self.media_cache.get_items(limit=10000))
            await ctx.send(f"Media cache contains {cache_size} items.")
            
            # Show cache validity status
            is_valid = self.media_cache.is_cache_valid()
            await ctx.send(f"Cache validity: {'Valid' if is_valid else 'Invalid'}")
            
            # Show cache last update time
            last_updated = self.media_cache.last_updated
            if last_updated:
                await ctx.send(f"Cache last updated: {last_updated}")
            else:
                await ctx.send("Cache has not been updated yet.")
            
            # Count items by media type
            movies = len(await self.media_cache.get_items(media_type="movie", limit=10000))
            tv_shows = len(await self.media_cache.get_items(media_type="tv", limit=10000))
            
            await ctx.send(f"Media types in cache: {movies} movies, {tv_shows} TV shows")
            
            # Sample some titles from different media types
            movie_samples = await self.media_cache.get_items(media_type="movie", limit=5, random_sort=True)
            tv_samples = await self.media_cache.get_items(media_type="tv", limit=5, random_sort=True)
            
            if movie_samples:
                movie_titles = [f"{item.get('title')} ({item.get('year', 'Unknown')})" for item in movie_samples]
                await ctx.send(f"Sample movies: {', '.join(movie_titles)}")
            else:
                await ctx.send("No movie samples found in cache.")
                
            if tv_samples:
                tv_titles = [f"{item.get('title')} ({item.get('year', 'Unknown')})" for item in tv_samples]
                await ctx.send(f"Sample TV shows: {', '.join(tv_titles)}")
            else:
                await ctx.send("No TV samples found in cache.")
            
            # Check on exact search functionality
            sample_movie = next((item for item in movie_samples if item), None) if movie_samples else None
            sample_tv = next((item for item in tv_samples if item), None) if tv_samples else None
            
            if sample_movie:
                movie_title = sample_movie.get('title', '')
                search_results = await self.media_cache.search(movie_title, limit=5)
                search_count = len(search_results)
                await ctx.send(f"Search for exact movie title '{movie_title}' returned {search_count} results.")
            
            if sample_tv:
                tv_title = sample_tv.get('title', '')
                search_results = await self.media_cache.search(tv_title, limit=5)
                search_count = len(search_results)
                await ctx.send(f"Search for exact TV title '{tv_title}' returned {search_count} results.")
            
            # Attempt partial matches
            if sample_movie and len(sample_movie.get('title', '')) > 3:
                # Use first 3 characters as partial search
                partial_query = sample_movie.get('title', '')[:3]
                search_results = await self.media_cache.search(partial_query, limit=5)
                search_count = len(search_results)
                await ctx.send(f"Partial search for '{partial_query}' returned {search_count} results.")
            
            logger.info("Media cache check completed successfully")
            
        except Exception as e:
            logger.error(f"Error in check_media_cache command: {e}", exc_info=True)
            await ctx.send(f"An error occurred while checking the media cache: {type(e).__name__}: {str(e)}")

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def force_refresh_cache(self, ctx):
        """Force invalidate and refresh the media cache completely."""
        message = await ctx.send("🔄 Forcing complete media cache refresh...")
        
        try:
            # First invalidate the cache
            async with self.media_cache.cache_lock:
                self.media_cache.media_items = {}
                self.media_cache.last_updated = None
                logger.info("Media cache has been forcibly invalidated")
                
            await message.edit(content="Cache invalidated. Starting full refresh (this may take a while)...")
            
            # Now perform a full update
            await self.media_cache.update_cache()
            
            # Get new statistics
            cache_size = len(await self.media_cache.get_items(limit=10000))
            movies = len(await self.media_cache.get_items(media_type="movie", limit=10000))
            tv_shows = len(await self.media_cache.get_items(media_type="tv", limit=10000))
            
            await message.edit(content=f"✅ Media cache has been completely refreshed!\n"
                                    f"Cache now contains {cache_size} items "
                                    f"({movies} movies, {tv_shows} TV shows)\n\n"
                                    f"Last Updated: {self.media_cache.last_updated}")
            
            logger.info(f"Force refresh complete. New cache contains {cache_size} items "
                    f"({movies} movies, {tv_shows} TV shows)")
                    
        except Exception as e:
            logger.error(f"Error in force_refresh_cache command: {e}", exc_info=True)
            await message.edit(content=f"❌ Error during cache refresh: {type(e).__name__}: {str(e)}")

def setup(bot):
    bot.add_cog(MediaCommands(bot))
