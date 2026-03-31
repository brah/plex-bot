# cogs/media_commands.py

import asyncio
import difflib
import logging
import random
from io import BytesIO
from config import config

import aiohttp
import nextcord
from nextcord import File
from nextcord.ext import commands, menus
import qbittorrentapi

from utilities import (
    UserMappings,
    NoStopButtonMenuPages,
    fetch_plex_image,
    prepare_thumbnail_for_embed,
)
from tautulli_wrapper import Tautulli, TMDB
from media_cache import MediaCache

# Configure logging for this module
logger = logging.getLogger("plexbot.media_commands")
logger.setLevel(logging.INFO)


_TYPE_LABELS = {"movie": "Movie", "show": "TV Show", "episode": "TV Episode", "season": "TV Season"}


def _format_ms(ms: int) -> str:
    """Format milliseconds into H:MM:SS or M:SS."""
    total_seconds = ms // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _format_seconds(seconds: int) -> str:
    """Format seconds into a human-readable duration like '2h 15m' or '45m'."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _format_bytes_speed(bps: int) -> str:
    """Format bytes/s into human-readable speed."""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    return f"{bps / 1_000:.0f} KB/s"


def _format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    if size_bytes >= 1_000_000_000:
        return f"{size_bytes / 1_000_000_000:.2f} GB"
    return f"{size_bytes / 1_000_000:.1f} MB"


def _truncate(text: str, length: int) -> str:
    """Truncate text with ellipsis if it exceeds length."""
    if len(text) <= length:
        return text
    return text[:length - 1] + "\u2026"


def _format_torrent_field(dl) -> str:
    """Format a single torrent into an embed field value with progress bar."""
    pct = dl.progress * 100
    filled = round(pct / 10)
    bar = "\u2588" * filled + "\u2591" * (10 - filled)

    speed_str = _format_bytes_speed(dl.dlspeed)
    size_str = _format_size(dl.size)

    eta = dl.eta
    if eta >= 8_640_000 or eta < 0:
        eta_str = "\u221E"
    elif eta >= 86_400:
        eta_str = f"{eta / 86400:.1f}d"
    elif eta >= 3_600:
        eta_str = f"{eta / 3600:.1f}h"
    elif eta >= 60:
        eta_str = f"{eta // 60}m"
    else:
        eta_str = f"{eta}s"

    seeds = f"{dl.num_seeds}" if hasattr(dl, "num_seeds") else ""
    seed_str = f"  \u2022  {seeds} seeds" if seeds else ""

    return f"`{bar}` {pct:.1f}%\n{speed_str}  \u2022  {size_str}  \u2022  ETA {eta_str}{seed_str}"


class RecentlyAddedPageSource(menus.ListPageSource):
    """One item per page with a rich embed and poster thumbnail."""

    def __init__(self, data, tautulli_ip, embed_color):
        super().__init__(data, per_page=1)
        self.tautulli_ip = tautulli_ip
        self.embed_color = embed_color

    async def format_page(self, menu, page):
        item = page[0] if isinstance(page, list) else page

        embed = nextcord.Embed(
            title=item["display_title"],
            description=item["summary"] or None,
            color=self.embed_color,
        )

        if item["subtitle"]:
            embed.set_author(name=item["subtitle"])

        # Info row
        info_parts = [item["type_label"]]
        if item["content_rating"]:
            info_parts.append(item["content_rating"])
        if item["rating"]:
            info_parts.append(f"Rating: {item['rating']}")
        if item["release_date"]:
            info_parts.append(item["release_date"])
        embed.add_field(name="Info", value="  \u2022  ".join(info_parts), inline=False)

        # Added to Plex
        if item["added_at"]:
            embed.add_field(name="Added", value=f"<t:{item['added_at']}:R>", inline=True)

        page_num = getattr(menu, "current_page", 0)
        embed.set_footer(text=f"Item {page_num + 1} of {self.get_max_pages()}")

        # Poster thumbnail
        thumb = item.get("thumb", "")
        if thumb:
            file, url = await prepare_thumbnail_for_embed(self.tautulli_ip, thumb)
            if file and url:
                embed.set_thumbnail(url=url)
                return {"embed": embed, "file": file}

        return embed


class RandomMediaView(nextcord.ui.View):
    """Interactive view with a 'Roll Again' button for the random command."""

    def __init__(self, ctx, items: list, tautulli_ip: str, embed_color: int, timeout: float = 120):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.items = items
        self.tautulli_ip = tautulli_ip
        self.embed_color = embed_color
        self.message = None
        self.used_indices: set = set()

    def _pick_item(self) -> dict:
        """Pick a random item, avoiding repeats until pool is exhausted."""
        if len(self.used_indices) >= len(self.items):
            self.used_indices.clear()
        remaining = [i for i in range(len(self.items)) if i not in self.used_indices]
        idx = random.choice(remaining)
        self.used_indices.add(idx)
        return self.items[idx]

    def _build_embed(self, item: dict) -> nextcord.Embed:
        title = item.get("title", "Unknown")
        year = item.get("year")
        media_type = (item.get("media_type") or "").lower()

        # Title with year
        display_title = f"{title} ({year})" if year else title

        type_label = _TYPE_LABELS.get(media_type, media_type.title() if media_type else "Media")

        summary = _truncate(item.get("summary", "") or "", 300)

        embed = nextcord.Embed(
            title=display_title,
            description=summary or None,
            color=self.embed_color,
        )
        embed.set_author(name=type_label)

        # Rating
        rating = item.get("rating")
        if rating and str(rating).strip():
            try:
                embed.add_field(name="Rating", value=f"{float(rating):.1f}/10", inline=True)
            except (ValueError, TypeError):
                pass

        # Genres
        genres = item.get("genres")
        if genres:
            embed.add_field(name="Genres", value=", ".join(g.title() for g in genres[:5]), inline=True)

        # Play count — only show if actually watched
        play_count = item.get("play_count", 0)
        if play_count and int(play_count) > 0:
            embed.add_field(name="Plays", value=str(play_count), inline=True)

        # Last played
        last_played = item.get("last_played")
        if last_played:
            try:
                embed.add_field(name="Last Watched", value=f"<t:{int(last_played)}:R>", inline=True)
            except (ValueError, TypeError):
                pass

        pool_note = f"{len(self.items)} items in pool"
        embed.set_footer(text=pool_note)
        return embed

    async def send_initial(self):
        item = self._pick_item()
        embed = self._build_embed(item)

        thumb = item.get("thumb")
        if thumb:
            file, url = await prepare_thumbnail_for_embed(self.tautulli_ip, thumb)
            if file and url:
                embed.set_thumbnail(url=url)
                self.message = await self.ctx.send(file=file, embed=embed, view=self)
                return

        self.message = await self.ctx.send(embed=embed, view=self)

    @nextcord.ui.button(label="Roll Again", style=nextcord.ButtonStyle.primary)
    async def roll_again(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Only the command author can re-roll.", ephemeral=True)
            return

        item = self._pick_item()
        embed = self._build_embed(item)

        thumb = item.get("thumb")
        if thumb:
            file, url = await prepare_thumbnail_for_embed(self.tautulli_ip, thumb)
            if file and url:
                embed.set_thumbnail(url=url)
                await interaction.response.edit_message(embed=embed, view=self, file=file)
                return

        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except nextcord.NotFound:
                pass


class MediaCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.tmdb: TMDB = bot.shared_resources.get("tmdb")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.plex_embed_color = config.get("ui", "plex_embed_color", 0xE5A00D)
        self.plex_image = config.get("ui", "plex_image")
        logger.info("MediaCommands cog initialized.")

    def cog_unload(self):
        """Clean up resources when the cog is unloaded."""
        self.bot.loop.create_task(self.tautulli.close())
        if self.tmdb:
            self.bot.loop.create_task(self.tmdb.close())

    @commands.command()
    async def recent(self, ctx, amount: int = None):
        """Displays recently added media items.

        Usage:
        plex recent [amount]

        Examples:
        plex recent
        plex recent 20
        """
        if amount is None:
            amount = config.get("defaults", "recent_count", 10)
        amount = max(1, min(amount, 25))

        try:
            response = await self.tautulli.get_recently_added(count=amount)
            if not Tautulli.check_response(response):
                await ctx.send("Failed to retrieve recent additions.")
                logger.error("Failed to retrieve recent additions from Tautulli.")
                return

            data = Tautulli.get_response_data(response, {}) or {}
            entries = data.get("recently_added", [])
            if not entries:
                await ctx.send("No recently added items found.")
                return

            items = self._parse_recent_entries(entries)
            if not items:
                await ctx.send("No recently added items found.")
                return

            pages = NoStopButtonMenuPages(
                source=RecentlyAddedPageSource(items, self.tautulli.tautulli_ip, self.plex_embed_color),
            )
            await pages.start(ctx)
        except Exception as e:
            logger.error(f"Failed to retrieve recent additions: {e}", exc_info=True)
            await ctx.send("Failed to retrieve recent additions.")

    @staticmethod
    def _parse_recent_entries(entries: list) -> list:
        """Parse raw Tautulli recently-added entries into display-ready dicts."""
        items = []
        for entry in entries:
            media_type = entry.get("media_type", "")
            title = entry.get("title", "Unknown")
            parent_title = entry.get("parent_title", "")
            grandparent_title = entry.get("grandparent_title", "")

            # Build display title based on media type
            if media_type == "episode":
                season_num = entry.get("parent_media_index")
                ep_num = entry.get("media_index")
                ep_label = ""
                if season_num is not None and ep_num is not None:
                    ep_label = f"S{int(season_num):02d}E{int(ep_num):02d} - "
                display_title = grandparent_title or title
                subtitle = f"{ep_label}{title}"
            elif media_type == "season":
                display_title = grandparent_title or title
                subtitle = parent_title or title
            elif media_type == "movie":
                year = entry.get("year", "")
                display_title = f"{title} ({year})" if year else title
                subtitle = None
            else:
                display_title = title
                subtitle = None

            summary = _truncate(entry.get("summary", "") or "", 250)

            # Rating — only include if actually present
            rating = entry.get("rating")
            if rating and str(rating).strip():
                try:
                    rating = f"{float(rating):.1f}/10"
                except (ValueError, TypeError):
                    rating = None
            else:
                rating = None

            content_rating = entry.get("content_rating") or None

            added_at = entry.get("added_at")
            if added_at:
                try:
                    added_at = int(added_at)
                except (ValueError, TypeError):
                    added_at = None

            release_date = entry.get("originally_available_at") or None

            type_label = _TYPE_LABELS.get(media_type, media_type.title() if media_type else "Media")

            items.append({
                "display_title": display_title,
                "subtitle": subtitle,
                "summary": summary,
                "rating": rating,
                "content_rating": content_rating,
                "added_at": added_at,
                "release_date": release_date,
                "type_label": type_label,
                "thumb": entry.get("thumb", ""),
            })
        return items

    @commands.command()
    async def random(self, ctx, *args):
        """Displays a random media item from the Plex libraries.

        Usage:
        plex random [movie|tv] [genre]

        Examples:
        plex random
        plex random movie
        plex random tv comedy
        plex random horror
        """
        media_type, genre = self._parse_random_args(args)
        logger.info(f"Random command: type={media_type}, genre={genre}")

        try:
            items = await self.media_cache.get_items(
                media_type=media_type, genres=[genre] if genre else None, random_sort=True, limit=100
            )

            if not items:
                # Try refreshing cache if empty
                await self.media_cache.update_cache()
                items = await self.media_cache.get_items(
                    media_type=media_type, genres=[genre] if genre else None, random_sort=True, limit=100
                )

            if not items:
                filter_desc = ""
                if media_type:
                    filter_desc += f" type: **{media_type}**"
                if genre:
                    filter_desc += f" genre: **{genre}**"
                await ctx.send(f"No media items found{filter_desc}." if filter_desc else "No media items found.")
                return

            view = RandomMediaView(ctx, items, self.tautulli.tautulli_ip, self.plex_embed_color)
            await view.send_initial()
        except Exception as e:
            logger.error(f"Error in random command: {e}", exc_info=True)
            await ctx.send("An error occurred while looking for a random item.")

    @staticmethod
    def _parse_random_args(args) -> tuple:
        """Parse args into (media_type, genre). First recognized type keyword wins."""
        if not args:
            return None, None

        args_lower = [a.lower() for a in args]
        known_types = {"movie", "tv", "any"}
        media_type = None

        for i, arg in enumerate(args_lower):
            if arg in known_types:
                media_type = arg
                args_lower.pop(i)
                break

        genre = " ".join(args_lower) if args_lower else None
        return media_type, genre

    @commands.command()
    async def watchers(self, ctx):
        """Display current Plex watchers with details about their activity.

        Usage:
        plex watchers
        """
        try:
            response = await self.tautulli.get_activity()
            if not Tautulli.check_response(response):
                await ctx.send("Failed to retrieve current Plex activity.")
                logger.error("Failed to retrieve activity from Tautulli.")
                return

            activity_data = Tautulli.get_response_data(response, {})
            sessions = activity_data.get("sessions", [])

            if not sessions:
                embed = nextcord.Embed(
                    title="Plex Activity",
                    description="No one is currently watching.",
                    color=self.plex_embed_color,
                )
                embed.set_thumbnail(url=self.plex_image)
                await ctx.send(embed=embed)
                return

            user_data = UserMappings.load_user_mappings()
            ignored_users = {u["plex_username"] for u in user_data if u.get("ignore", False)}

            visible_sessions = [s for s in sessions if s.get("username") not in ignored_users]

            if not visible_sessions:
                embed = nextcord.Embed(
                    title="Plex Activity",
                    description="No one is currently watching.",
                    color=self.plex_embed_color,
                )
                embed.set_thumbnail(url=self.plex_image)
                await ctx.send(embed=embed)
                return

            stream_count = activity_data.get("stream_count", len(visible_sessions))
            total_bw = activity_data.get("total_bandwidth", 0)
            bw_display = f"{round(int(total_bw) / 1000, 1)} Mbps" if total_bw else ""

            header_parts = [f"{stream_count} active stream{'s' if int(stream_count) != 1 else ''}"]
            if bw_display:
                header_parts.append(bw_display)

            embed = nextcord.Embed(
                title="Plex Activity",
                description="  \u2022  ".join(header_parts),
                color=self.plex_embed_color,
            )
            embed.set_thumbnail(url=self.plex_image)

            for session in visible_sessions:
                name, value = self._format_session_field(session)
                embed.add_field(name=name, value=value, inline=False)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to retrieve watchers: {e}", exc_info=True)
            await ctx.send("Failed to retrieve watchers.")

    @staticmethod
    def _format_session_field(session: dict) -> tuple:
        """Format a single Tautulli session into an embed field (name, value)."""
        user = session.get("friendly_name", session.get("username", "Unknown"))
        title = session.get("full_title", session.get("title", "Unknown"))

        # State indicator
        state = session.get("state", "unknown").lower()
        state_icons = {"playing": "\u25B6", "paused": "\u23F8", "buffering": "\u23F3"}
        state_icon = state_icons.get(state, "\u2022")

        # Progress bar
        progress_bar = ""
        try:
            view_offset = int(session.get("view_offset", 0))
            duration = int(session.get("duration", 0))
            if duration > 0:
                pct = min(100, (view_offset / duration) * 100)
                filled = round(pct / 10)
                bar = "\u2588" * filled + "\u2591" * (10 - filled)
                elapsed = _format_ms(view_offset)
                total = _format_ms(duration)
                progress_bar = f"`{bar}` {elapsed} / {total}"
            else:
                elapsed = _format_ms(view_offset)
                progress_bar = f"{elapsed}"
        except (ValueError, TypeError):
            pass

        # Transcode vs direct
        decision = session.get("transcode_decision", "").lower()
        if decision == "transcode":
            stream_type = "Transcode"
        elif decision == "copy":
            stream_type = "Direct Stream"
        elif decision == "direct play":
            stream_type = "Direct Play"
        else:
            stream_type = session.get("quality_profile", "")

        # Player info
        player = session.get("player", "")
        platform = session.get("platform_name") or session.get("platform", "")
        device_parts = [p for p in [player, platform] if p]
        device = " \u2022 ".join(device_parts) if device_parts else None

        # Build value lines
        lines = [f"{state_icon}  **{title}**"]
        if progress_bar:
            lines.append(progress_bar)

        detail_parts = []
        if stream_type:
            detail_parts.append(stream_type)
        if device:
            detail_parts.append(device)
        if detail_parts:
            lines.append("  \u2022  ".join(detail_parts))

        return user, "\n".join(lines)

    async def _lookup_command(self, ctx, query: str, media_type: str, label: str):
        """Shared implementation for tv/movie lookup commands."""
        if not query:
            await ctx.send(f"Please provide a {label.lower()} name to search for.")
            return
        await self.lookup_media(ctx, query, media_type=media_type)

    @commands.command(name="tv")
    async def lookup_tv(self, ctx, *, show_name: str = None):
        """Lookup information about a TV show in your Plex library.

        Usage:
        plex tv <show name>

        Example:
        plex tv Game of Thrones
        """
        await self._lookup_command(ctx, show_name, "tv", "TV show")

    @commands.command(name="movie")
    async def lookup_movie(self, ctx, *, movie_name: str = None):
        """Lookup information about a movie in your Plex library.

        Usage:
        plex movie <movie name>

        Example:
        plex movie The Godfather
        """
        await self._lookup_command(ctx, movie_name, "movie", "Movie")

    async def lookup_media(self, ctx, title: str, media_type: str = None):
        """Search for a movie or TV show and display detailed information."""
        if not title:
            await ctx.send("Please provide a title to search for.")
            return

        search_media_type = {"tv": "show", "movie": "movie"}.get(media_type)
        tautulli_type = search_media_type
        media_type_str = {"tv": "TV Show", "movie": "Movie"}.get(media_type, "Content")

        search_msg = await ctx.send(f"Searching for {media_type_str}: **{title}**...")

        try:
            # --- Phase 1: Direct Tautulli library search ---
            best_match_key = await self._search_libraries(title, tautulli_type)

            # --- Phase 2: Cache fallback ---
            if not best_match_key:
                best_match_key = await self._search_cache(title, media_type, search_media_type)

            if not best_match_key:
                await search_msg.edit(content=f"No {media_type_str.lower()} found matching '**{title}**'.")
                return

            # --- Phase 3: Fetch full metadata + stats in parallel ---
            metadata_resp, user_stats_resp, watch_time_resp = await asyncio.gather(
                self.tautulli.get_metadata(best_match_key),
                self.tautulli.get_item_user_stats(best_match_key),
                self.tautulli.get_item_watch_time_stats(best_match_key),
            )

            if not Tautulli.check_response(metadata_resp):
                await search_msg.edit(content="Failed to retrieve details from Tautulli.")
                return

            metadata = Tautulli.get_response_data(metadata_resp, {})

            # Build and send embed
            embed, file = await self._build_media_embed(metadata, user_stats_resp, watch_time_resp)

            if file:
                await search_msg.delete()
                await ctx.send(file=file, embed=embed)
            else:
                await search_msg.edit(content=None, embed=embed)

        except Exception as e:
            logger.error(f"Error in lookup_media: {e}", exc_info=True)
            await search_msg.edit(content="An error occurred while looking up information.")

    async def _search_libraries(self, title: str, tautulli_type=None):
        """Search Tautulli libraries directly. Returns best match rating_key or None."""
        try:
            libraries_resp = await self.tautulli.get_libraries()
            if not Tautulli.check_response(libraries_resp):
                return None
            libraries = Tautulli.get_response_data(libraries_resp, [])
            if tautulli_type:
                libraries = [lib for lib in libraries if lib.get("section_type") == tautulli_type]
            if not libraries:
                return None

            # Search all libraries in parallel
            responses = await asyncio.gather(*(
                self.tautulli.get_library_media_info(section_id=lib["section_id"], search=title, length=10)
                for lib in libraries
            ))

            all_results = []
            title_lower = title.lower()
            for resp in responses:
                if not Tautulli.check_response(resp):
                    continue
                items = Tautulli.get_response_data(resp, {}).get("data", [])
                for item in items:
                    if item.get("title", "").lower() == title_lower:
                        return item.get("rating_key")
                all_results.extend(items)

            if not all_results:
                return None

            all_results.sort(
                key=lambda x: difflib.SequenceMatcher(None, x.get("title", "").lower(), title_lower).ratio(),
                reverse=True,
            )
            return all_results[0].get("rating_key")
        except Exception as e:
            logger.error(f"Error in direct library search: {e}", exc_info=True)
            return None

    async def _search_cache(self, title: str, media_type=None, search_media_type=None):
        """Search media cache. Returns best match rating_key or None."""
        if not self.media_cache:
            return None
        await self.media_cache.ensure_cache_valid()

        if hasattr(self.media_cache, "enhanced_search"):
            items = await self.media_cache.enhanced_search(title, media_type=media_type, limit=20)
        else:
            items = await self.media_cache.search(title, limit=20)
            if search_media_type:
                items = [i for i in items if i.get("media_type") == search_media_type]

        if not items:
            return None

        # Exact title match first
        for item in items:
            if item.get("title", "").lower() == title.lower():
                return item.get("rating_key")
        return items[0].get("rating_key")

    async def _build_media_embed(self, metadata: dict, user_stats_resp, watch_time_resp):
        """Build a rich embed for a media item. Returns (embed, file_or_none)."""
        title = metadata.get("title", "Unknown Title")
        year = metadata.get("year")
        media_type = metadata.get("media_type", "")
        display_title = f"{title} ({year})" if year else title

        type_label = _TYPE_LABELS.get(media_type, media_type.title() if media_type else "Media")

        summary = _truncate(metadata.get("summary", "") or "", 400)

        embed = nextcord.Embed(title=display_title, description=summary or None, color=self.plex_embed_color)
        embed.set_author(name=type_label)

        # --- Info row ---
        info_parts = []
        if metadata.get("content_rating"):
            info_parts.append(metadata["content_rating"])
        if metadata.get("duration"):
            duration_min = int(metadata["duration"]) // 60000
            if duration_min >= 60:
                info_parts.append(f"{duration_min // 60}h {duration_min % 60}m")
            elif duration_min > 0:
                info_parts.append(f"{duration_min}m")
        if metadata.get("genres"):
            info_parts.append(", ".join(g.title() for g in metadata["genres"][:4]))
        if info_parts:
            embed.add_field(name="Info", value="  \u2022  ".join(info_parts), inline=False)

        # --- TV show specifics ---
        if media_type == "show":
            show_parts = []
            seasons = metadata.get("children_count")
            episodes = metadata.get("grandchildren_count")
            if seasons:
                show_parts.append(f"{seasons} season{'s' if str(seasons) != '1' else ''}")
            if episodes:
                show_parts.append(f"{episodes} episodes")
            status = metadata.get("status")
            if status:
                show_parts.append(status)
            if show_parts:
                embed.add_field(name="Show Details", value="  \u2022  ".join(show_parts), inline=False)

            if metadata.get("originally_available_at"):
                embed.add_field(name="First Aired", value=metadata["originally_available_at"], inline=True)

        # --- Watch stats (from parallel fetch) ---
        user_stats = Tautulli.get_response_data(user_stats_resp) if Tautulli.check_response(user_stats_resp) else None
        watch_time_stats = Tautulli.get_response_data(watch_time_resp) if Tautulli.check_response(watch_time_resp) else None

        if user_stats:
            total_plays = sum(u.get("total_plays", 0) for u in user_stats)
            if total_plays > 0:
                embed.add_field(name="Total Plays", value=str(total_plays), inline=True)

            # Watched By — resolve Discord names
            watchers = self._build_watcher_list(user_stats)
            if watchers:
                display = "\n".join(watchers[:5])
                if len(watchers) > 5:
                    display += f"\n...and {len(watchers) - 5} more"
                embed.add_field(name="Watched By", value=display, inline=False)

        if watch_time_stats and isinstance(watch_time_stats, list) and watch_time_stats:
            stats_item = watch_time_stats[0]
            total_time = stats_item.get("total_time", 0)
            if total_time > 0:
                embed.add_field(name="Total Watch Time", value=_format_seconds(total_time), inline=True)
            last_watch = stats_item.get("last_watch")
            if last_watch:
                try:
                    embed.add_field(name="Last Watched", value=f"<t:{int(last_watch)}:R>", inline=True)
                except (ValueError, TypeError):
                    pass

        # --- Thumbnail ---
        file = None
        thumb = metadata.get("thumb")
        if thumb:
            file, url = await prepare_thumbnail_for_embed(self.tautulli.tautulli_ip, thumb)
            if file and url:
                embed.set_thumbnail(url=url)

        return embed, file

    def _build_watcher_list(self, user_stats: list) -> list:
        """Build a list of watcher display strings, resolving Discord names where possible."""
        watchers = []
        for stat in user_stats:
            plex_username = stat.get("username")
            plays = stat.get("total_plays", 0)
            if plays == 0:
                continue
            mapping = UserMappings.get_mapping_by_plex_username(plex_username)
            display_name = plex_username
            if mapping and not mapping.get("ignore", False):
                discord_id = mapping.get("discord_id")
                if discord_id:
                    try:
                        discord_user = self.bot.get_user(int(discord_id))
                        if discord_user:
                            display_name = discord_user.display_name
                    except (ValueError, TypeError):
                        pass
            watchers.append(f"**{display_name}**: {plays} play{'s' if plays != 1 else ''}")
        return watchers

    @commands.command()
    async def downloading(self, ctx):
        """Display the current downloading torrents in qBittorrent.

        Usage:
        plex downloading
        """
        qbt_client = self._create_qbit_client()
        if not qbt_client:
            await ctx.send("qBittorrent is not configured. Check your config settings.")
            return

        try:
            await asyncio.to_thread(qbt_client.auth_log_in)
        except qbittorrentapi.LoginFailed:
            logger.exception("Login to qBittorrent failed.")
            await ctx.send("Failed to log in to qBittorrent. Check your credentials.")
            return
        except qbittorrentapi.APIConnectionError:
            logger.exception("Connection to qBittorrent failed.")
            await ctx.send("Failed to connect to qBittorrent. Check your network settings.")
            return
        except Exception:
            logger.exception("Unexpected error connecting to qBittorrent.")
            await ctx.send("Failed to connect to qBittorrent.")
            return

        try:
            all_downloading = await asyncio.to_thread(qbt_client.torrents.info.downloading)
        except Exception:
            logger.exception("Error retrieving downloading torrents.")
            await ctx.send("Failed to retrieve downloading torrents.")
            return

        # Filter: exclude paused, and exclude stalled/dead (0 speed + high progress)
        active = []
        stalled = []
        for t in all_downloading:
            if t.state_enum.is_paused:
                continue
            if t.dlspeed == 0 and t.progress > 0.99:
                stalled.append(t)
            elif t.state in ("stalledDL",) and t.dlspeed == 0 and t.num_seeds == 0:
                stalled.append(t)
            else:
                active.append(t)

        qbit_color = config.get("ui", "qbit_embed_color", 0x6C81DF)
        qbit_image = config.get("ui", "qbit_image")

        if not active and not stalled:
            embed = nextcord.Embed(
                title="qBittorrent Downloads",
                description="No torrents are currently downloading.",
                color=qbit_color,
            )
            embed.set_thumbnail(url=qbit_image)
            await ctx.send(embed=embed)
            return

        # Sort active by progress descending
        active.sort(key=lambda x: x.progress, reverse=True)

        embed = nextcord.Embed(title="qBittorrent Downloads", color=qbit_color)
        embed.set_thumbnail(url=qbit_image)

        # Summary line
        summary_parts = []
        if active:
            total_speed = sum(t.dlspeed for t in active)
            summary_parts.append(f"{len(active)} active at {_format_bytes_speed(total_speed)}")
        if stalled:
            summary_parts.append(f"{len(stalled)} stalled")
        embed.description = "  \u2022  ".join(summary_parts)

        # Active downloads (limit to 10 to stay within embed limits)
        for dl in active[:10]:
            embed.add_field(
                name=_truncate(dl.name, 80),
                value=_format_torrent_field(dl),
                inline=False,
            )

        # Collapsed stalled section
        if stalled:
            stalled_names = [_truncate(t.name, 60) for t in stalled[:5]]
            stalled_text = "\n".join(f"\u2022 {n}" for n in stalled_names)
            if len(stalled) > 5:
                stalled_text += f"\n...and {len(stalled) - 5} more"
            embed.add_field(name=f"Stalled ({len(stalled)})", value=stalled_text, inline=False)

        await ctx.send(embed=embed)

    @staticmethod
    def _create_qbit_client():
        """Create a qBittorrent client from config. Returns None if not configured."""
        ip = config.get("qbittorrent", "ip")
        port = config.get("qbittorrent", "port")
        username = config.get("qbittorrent", "username")
        password = config.get("qbittorrent", "password")
        if not all([ip, port, username, password]):
            return None
        return qbittorrentapi.Client(host=ip, port=port, username=username, password=password)

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
