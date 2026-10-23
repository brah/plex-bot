"""A local copy of the Plex library's metadata, refreshed from Tautulli in the background and saved
under cache/. Title autocomplete, /plex random and /plex recommend read from it."""

import asyncio
import json
import logging
import os
import random
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from plexbot.tautulli_wrapper import TMDB, Tautulli
from plexbot.ui.format import plural

# Configure logging
logger = logging.getLogger("plexbot.media_cache")

_RETRY_UNREADABLE = 24 * 3600  # seconds before a title Tautulli couldn't describe is asked about again


class MediaCache:
    """
    A centralized cache system for Plex media items.

    This class manages caching of media metadata to reduce API calls to Tautulli,
    implements efficient filtering and searching, and handles periodic updates.
    """

    def __init__(
        self, tautulli_client, cache_file_path: str = "cache/media_cache.json", update_interval: int = 3600
    ):
        """
        Initialize the media cache system.

        Args:
            tautulli_client: The Tautulli API client instance
            cache_file_path: Path to store the cache file
            update_interval: Cache update interval in seconds (default: 1 hour)
        """
        self.tautulli = tautulli_client
        self.update_interval = update_interval
        self.cache_file_path = Path(cache_file_path)
        self.media_items: Dict[str, Dict] = {}  # Using rating_key as the dictionary key
        self.last_updated: Optional[datetime] = None
        self.cache_lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(10)  # Limit concurrent API requests
        self._refresh: Optional[asyncio.Task] = None  # a background update_cache, while it runs
        self._updating: Optional[asyncio.Task] = None  # whichever task is inside update_cache
        self._closed = False  # set by close(): no update starts once the bot is shutting down
        # rating key -> when Tautulli last refused to describe it (usually removed from Plex but still listed)
        self._unreadable: Dict[str, float] = {}

    async def initialize(self) -> None:
        """Initialize the cache by loading from disk and updating if needed."""
        # Create cache directory if it doesn't exist
        self.cache_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Load cache from disk
        await self.load_cache_from_disk()

        if self.media_items:
            logger.info(f"Media cache loaded with {len(self.media_items)} items")
            if any("tmdb_id" not in item for item in self.media_items.values()):
                # Cached titles keep their metadata between updates, so only a full one adds the ids.
                logger.info("Reading every title again once, to add TMDB ids")
                self._refresh = asyncio.create_task(self.update_cache(full=True))
        else:
            # A first fill reads the whole library (minutes on a big one): don't hold up the Discord login.
            logger.info("Cache is empty, filling it in the background")
            self._refresh = asyncio.create_task(self.update_cache())

    def is_cache_valid(self) -> bool:
        """Check if the cache is still valid based on the update interval and content."""
        # Always consider empty cache as invalid regardless of timestamp
        if not self.media_items:
            return False

        if not self.last_updated:
            return False

        elapsed = (datetime.now() - self.last_updated).total_seconds()
        return elapsed < self.update_interval

    async def ensure_cache_valid(self) -> None:
        """Refresh a stale cache. Callers only wait when there's nothing to serve yet.

        A full refresh asks Tautulli about every title, which takes minutes on a big library, so a
        stale cache keeps serving its items while the refresh runs in the background.
        """
        if self.is_cache_valid():
            return
        if not self.media_items:
            await self.update_cache()
        else:
            self._refresh_in_background()

    def _refresh_in_background(self) -> None:
        """Start update_cache without waiting for it, unless one is already running."""
        if self._refresh is None or self._refresh.done():
            self._refresh = asyncio.create_task(self.update_cache())

    async def wait_for_refresh(self) -> None:
        """Wait for a background refresh under way (like the startup fill), if there is one."""
        if self._refresh is not None and not self._refresh.done():
            await asyncio.wait({self._refresh})

    async def close(self) -> None:
        """Stop any update (in the background, or Refresh now), so it doesn't outlive the Tautulli
        session it uses: a late call would open a new session that's never closed."""
        self._closed = True
        for task in {self._refresh, self._updating}:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def update_cache(self, full: bool = False) -> bool:
        """Update the media cache from Tautulli: the title list, plus metadata for new titles.
        Returns whether the cache is now fresh.

        full also fetches metadata again for every title already cached (genres or summaries
        changed in Plex), even when the cache is fresh; that's one call per title, minutes on a big library.
        """
        async with self.cache_lock:
            if self._closed:
                return False
            if not full and self.is_cache_valid():
                return True  # another task updated the cache while this one waited for the lock

            logger.info("Updating media cache...")
            self._updating = asyncio.current_task()
            try:
                # Check Tautulli connection first
                test_response = await self.tautulli.get_server_info()
                if not Tautulli.check_response(test_response):
                    logger.error("Tautulli connection test failed")
                    return False

                # Fetch and process all media items
                new_items = await self._fetch_all_media_items(full)

                # Update the cache with new items
                if new_items:
                    self.media_items = {str(item["rating_key"]): item for item in new_items}
                    self.last_updated = datetime.now()

                    items_snapshot = list(self.media_items.values())
                    await self._write_items_to_disk(items_snapshot)
                    logger.info(f"Media cache updated with {len(self.media_items)} items")
                    return True
                logger.warning("No media items found during update")
            except Exception as e:
                logger.error(f"Failed to update media cache: {e}")
            finally:
                self._updating = None
            return False

    async def get_items(
        self,
        media_type: Optional[str] = None,
        genres: Optional[List[str]] = None,
        exclude_rating_keys: Optional[Set[str]] = None,
        limit: Optional[int] = None,
        random_sort: bool = False,
    ) -> List[Dict]:
        """
        Get filtered media items from the cache.

        Args:
            media_type: Filter by media type (movie, show, episode)
            genres: Filter by genres (case-insensitive)
            exclude_rating_keys: Set of rating keys to exclude
            limit: Maximum number of items to return (default: all)
            random_sort: Whether to randomly shuffle results

        Returns:
            List of media items matching the filters
        """
        await self.ensure_cache_valid()

        # Create a copy of all items as a list
        items = list(self.media_items.values())

        types = self._types_for(media_type)
        if types:
            items = [item for item in items if item.get("media_type", "unknown").lower() in types]

        # Apply genre filter
        if genres:
            genres_lower = [genre.lower() for genre in genres]
            items = [
                item for item in items if any(g.lower() in genres_lower for g in item.get("genres", []))
            ]

        # Exclude rating keys
        if exclude_rating_keys:
            exclude_keys = {str(key) for key in exclude_rating_keys}
            items = [item for item in items if str(item.get("rating_key")) not in exclude_keys]

        if random_sort:
            random.shuffle(items)
        return items[:limit]

    async def load_cache_from_disk(self) -> None:
        """Load the media cache from disk."""
        if not self.cache_file_path.exists():
            logger.info("No media cache file found. Starting with an empty cache.")
            self.media_items = {}
            return

        try:
            data = json.loads(await asyncio.to_thread(self.cache_file_path.read_text, encoding="utf-8"))
            # Restoring the real timestamp is what lets cache freshness (update_interval) survive a restart.
            items = data["items"]
            self.last_updated = self._parse_timestamp(data.get("updated_at"))
            self.media_items = {str(item["rating_key"]): item for item in items}
            logger.info(f"Media cache loaded from {self.cache_file_path} with {len(self.media_items)} items")
        except Exception as e:
            logger.error(f"Failed to load media cache from disk: {e}")
            self.media_items = {}

    @staticmethod
    def _parse_timestamp(value) -> Optional[datetime]:
        """Parse an ISO-8601 timestamp string, or return None if absent/invalid."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None

    async def _write_items_to_disk(self, items_list: List[Dict]) -> None:
        """Write a snapshot of media items to disk, atomically (a temp file swapped in).

        Takes no lock, so update_cache can call it while holding cache_lock.
        """
        if not items_list:
            logger.warning("Attempted to write empty cache to disk")
            return
        # Persist the timestamp alongside the items so freshness survives a restart.
        data = {"updated_at": (self.last_updated or datetime.now()).isoformat(), "items": items_list}
        try:
            await asyncio.to_thread(self._replace_file, data)
            logger.info(f"Media cache saved to {self.cache_file_path}")
        except Exception as e:
            logger.error(f"Failed to save media cache to disk: {e}")

    def _replace_file(self, data: dict) -> None:
        self.cache_file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.cache_file_path.with_suffix(".tmp")
        temp_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_file, self.cache_file_path)

    async def _fetch_all_media_items(self, full: bool = False) -> List[Dict]:
        """
        Fetch all media items from Tautulli and gather their metadata.

        This is an internal method used during cache updates.
        """
        all_media_items = []
        libraries = await self._get_libraries()
        logger.info(f"Starting to fetch media items from {len(libraries)} libraries.")

        # Process all libraries concurrently with limits (each logs its own name as it starts)
        results = await asyncio.gather(*(self._process_library(lib, full) for lib in libraries),
                                       return_exceptions=True)

        failed = set()
        for library, result in zip(libraries, results):
            if isinstance(result, list):
                all_media_items.extend(result)
            else:
                failed.add(str(library["section_id"]))
                logger.error(f"Library {library['section_name']} couldn't be read"
                             + (f": {result}" if isinstance(result, Exception) else ""))
        if failed and len(failed) == len(libraries):
            return []  # nothing new at all: update_cache keeps the old cache and stays stale
        if failed:
            # Keep the titles we had for libraries that couldn't be read, rather than dropping them.
            # Items cached before section_id was recorded count as belonging to a failed library.
            fresh = {str(item["rating_key"]) for item in all_media_items}
            kept = [item for key, item in self.media_items.items() if key not in fresh
                    and (item.get("section_id") is None or str(item["section_id"]) in failed)]
            logger.info(f"Kept {len(kept)} cached titles from {len(failed)} unreadable libraries")
            all_media_items.extend(kept)

        # Filter out low-quality/empty entries before returning
        filtered_items = self._filter_quality_items(all_media_items)

        items_filtered = len(all_media_items) - len(filtered_items)
        if items_filtered > 0:
            logger.info(f"Filtered out {items_filtered} low-quality media items")

        by_type = dict(Counter(item.get("media_type", "unknown") for item in filtered_items))
        logger.info(f"Fetched a total of {len(filtered_items)} quality media items: {by_type}")
        return filtered_items

    def _filter_quality_items(self, items: List[Dict]) -> List[Dict]:
        """
        Filter out low-quality or empty media items.

        Returns items that have meaningful metadata we can use.
        """
        filtered_items = []

        for item in items:
            # Skip items that are just "Unknown Title" with no useful metadata
            if (
                item.get("title") == "Unknown Title"
                and not item.get("genres")
                and not item.get("year")
                and not item.get("play_count")
                and not item.get("summary")
                and not item.get("rating")
            ):
                continue

            # Skip items with "unknown" media type and no genres
            if item.get("media_type") == "unknown" and not item.get("genres"):
                continue

            # Keep all other items
            filtered_items.append(item)

        return filtered_items

    async def _process_library(self, library: Dict, full: bool = False) -> Optional[List[Dict]]:
        """A library's items (metadata fetched for new titles, or all of them when full); None when
        its title list couldn't be read."""
        library_items = []
        section_id = str(library["section_id"])
        try:
            logger.info(
                f"Fetching media items for library: {library['section_name']} (ID: {library['section_id']})"
            )

            # Every title in the library, a page at a time (one call would cap big libraries).
            media_items = await self.tautulli.get_library_media_rows(library["section_id"])
            if media_items is None:
                logger.error(f"Failed to fetch media info for library {library['section_id']}")
                return None

            if not media_items:
                logger.info(f"No media items found in library {library['section_name']}")
                return library_items

            logger.info(f"Processing {len(media_items)} items from library '{library['section_name']}'")

            # Titles already cached keep their metadata; only new ones (or all, when full) are fetched.
            rating_keys = []
            now = time.monotonic()
            for row in media_items:
                key = str(row["rating_key"])
                cached = None if full else self.media_items.get(key)
                # Titles cached before TMDB ids are read again, in case the startup refresh failed.
                if cached and "tmdb_id" in cached:
                    library_items.append({**cached, "section_id": section_id})
                elif full or now - self._unreadable.get(key, float("-inf")) > _RETRY_UNREADABLE:
                    rating_keys.append(key)
            skipped = len(media_items) - len(library_items) - len(rating_keys)
            if skipped:
                logger.info(f"Skipped {plural(skipped, 'title')} in '{library['section_name']}' that Tautulli "
                            "couldn't describe in the last day; Refresh now asks again at once")

            # self.semaphore keeps this to 10 calls at a time, across every library.
            results = await asyncio.gather(*(self._fetch_item_metadata(key) for key in rating_keys))
            for key, result in zip(rating_keys, results):
                # Refusals only: a title that got no answer is asked again next update.
                if result == {} and key not in self.media_items:
                    self._unreadable[key] = now
                else:
                    self._unreadable.pop(key, None)
                # No answer keeps the title we had. A refusal drops it, unmarked, so the next update
                # asks again and a Plex restart during this refresh loses nothing for long.
                item = self.media_items.get(key) if result is None else result
                if item:
                    # section_id lets a later refresh keep this library's titles if it can't be read.
                    library_items.append({**item, "section_id": section_id})

            logger.info(
                f"Completed processing library '{library['section_name']}' with {len(library_items)} items"
            )
            return library_items

        except Exception as e:
            # A half-read library would drop the rest of its titles; treat it as unreadable instead.
            logger.error(f"Error processing library {library.get('section_name', 'unknown')}: {e}")
            return None

    @staticmethod
    def _types_for(media_type: Optional[str]) -> Set[str]:
        if not media_type:
            return set()
        return {"tv": {"show", "episode"}, "movie": {"movie"}}.get(media_type.lower(), {media_type.lower()})

    def by_tmdb_ids(self, media_type: str, tmdb_ids: Iterable[int]) -> List[Dict]:
        """Cached titles of one media type with these TMDB ids, in the ids' order, each once."""
        by_id = {item["tmdb_id"]: item for item in self.media_items.values()
                 if item.get("media_type") == media_type and item.get("tmdb_id") is not None}
        return [by_id[tmdb_id] for tmdb_id in dict.fromkeys(tmdb_ids) if tmdb_id in by_id]

    def suggest(self, query: str, media_type: Optional[str] = None, limit: int = 25) -> List[Dict]:
        """Titles for autocomplete: prefix matches first, then titles containing the query.

        Reads the cache as it is, without waiting or logging: autocomplete runs on every keystroke,
        and Discord drops an answer that takes longer than three seconds. A stale cache starts
        refreshing in the background, so new titles turn up even if nobody runs /plex random.
        """
        if not self.is_cache_valid():
            self._refresh_in_background()
        query = (query or "").strip().lower()
        types = self._types_for(media_type)
        prefix, inner = [], []
        for item in self.media_items.values():
            if types and item.get("media_type") not in types:
                continue
            title = (item.get("title") or "").lower()
            if title.startswith(query):
                prefix.append(item)
            elif query in title:
                inner.append(item)

        def by_title(item):
            return (item.get("title") or "").lower()

        return (sorted(prefix, key=by_title) + sorted(inner, key=by_title))[:limit]

    def genres(self, media_type: Optional[str] = None) -> List[str]:
        """Every genre in the cache (lower-case), most common first."""
        types = self._types_for(media_type)
        counts = Counter(
            genre
            for item in self.media_items.values()
            if not types or item.get("media_type") in types
            for genre in item.get("genres") or []
        )
        return [genre for genre, _count in counts.most_common()]

    async def refresh(self) -> bool:
        """Fetch the whole library again now, even if the cache is still fresh; returns whether it worked.

        The current items stay in use until the new ones arrive. A background refresh already
        under way finishes first (it holds the lock), then every title is read again.
        """
        return await self.update_cache(full=True)

    async def _fetch_item_metadata(self, rating_key: str) -> Optional[Dict]:
        """A title's cache entry; {} when Tautulli refused (Plex no longer has it), None on no answer."""
        async with self.semaphore:
            try:
                metadata_response = await self.tautulli.get_metadata(rating_key=rating_key)

                if not metadata_response:
                    logger.error(f"No response for metadata request on rating_key {rating_key}")
                    return None

                if Tautulli.check_response(metadata_response):
                    metadata = Tautulli.get_response_data(metadata_response, {})
                    genres = [genre.lower() for genre in metadata.get("genres", [])]

                    # Create a standardized item structure with only necessary fields
                    item_data = {
                        "rating_key": rating_key,
                        "title": metadata.get("title") or "Unknown Title",
                        "media_type": (metadata.get("media_type") or "unknown").lower(),
                        "genres": genres,
                        "thumb": metadata.get("thumb"),
                        "year": metadata.get("year"),
                        "play_count": metadata.get("play_count", 0),
                        "last_played": metadata.get("last_played"),
                        "summary": metadata.get("summary", ""),
                        "rating": metadata.get("rating", ""),
                        "parent_rating_key": metadata.get("parent_rating_key"),
                        "grandparent_rating_key": metadata.get("grandparent_rating_key"),
                        "tmdb_id": TMDB.id_from_guids(metadata.get("guids")),
                    }
                    return item_data
                else:
                    logger.error(f"Failed to fetch metadata for rating_key {rating_key}")
                    return {}
            except Exception as e:
                logger.error(f"Exception while fetching metadata for {rating_key}: {e}")
                return None

    async def _get_libraries(self) -> List[Dict]:
        """Fetch all libraries from Tautulli."""
        response = await self.tautulli.get_libraries()

        if not Tautulli.check_response(response):
            logger.error("Failed to fetch libraries from Tautulli")
            return []

        libraries = Tautulli.get_response_data(response, [])
        # Only include libraries with supported media types
        filtered_libraries = [
            lib for lib in libraries if lib["section_type"] in ("movie", "show", "episode")
        ]
        return filtered_libraries
