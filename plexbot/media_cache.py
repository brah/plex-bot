import asyncio
import json
import logging
import os
import random
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from plexbot.tautulli_wrapper import Tautulli

# Configure logging
logger = logging.getLogger("plexbot.media_cache")


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

    async def initialize(self) -> None:
        """Initialize the cache by loading from disk and updating if needed."""
        # Create cache directory if it doesn't exist
        self.cache_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Load cache from disk
        await self.load_cache_from_disk()

        if self.media_items:
            logger.info(f"Media cache loaded with {len(self.media_items)} items")
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
        elif self._refresh is None or self._refresh.done():
            self._refresh = asyncio.create_task(self.update_cache())

    async def close(self) -> None:
        """Stop a background refresh, so it doesn't outlive the Tautulli session it uses."""
        if self._refresh is not None and not self._refresh.done():
            self._refresh.cancel()
            try:
                await self._refresh
            except asyncio.CancelledError:
                pass

    async def update_cache(self, full: bool = False) -> None:
        """Update the media cache from Tautulli: the title list, plus metadata for new titles.

        full also fetches metadata again for every title already cached (genres or summaries
        changed in Plex); that's one call per title, minutes on a big library.
        """
        async with self.cache_lock:
            if self.is_cache_valid():
                # Another process might have updated the cache while we were waiting for the lock
                return

            logger.info("Updating media cache...")
            try:
                # Check Tautulli connection first
                test_response = await self.tautulli.get_server_info()
                if not Tautulli.check_response(test_response):
                    logger.error("Tautulli connection test failed")
                    return

                # Fetch and process all media items
                new_items = await self._fetch_all_media_items(full)

                # Update the cache with new items
                if new_items:
                    self.media_items = {str(item["rating_key"]): item for item in new_items}
                    self.last_updated = datetime.now()

                    items_snapshot = list(self.media_items.values())
                    await self._write_items_to_disk(items_snapshot)
                    logger.info(f"Media cache updated with {len(self.media_items)} items")
                else:
                    logger.warning("No media items found during update")
            except Exception as e:
                logger.error(f"Failed to update media cache: {e}")

    async def get_items(
        self,
        media_type: Optional[str] = None,
        genres: Optional[List[str]] = None,
        exclude_rating_keys: Optional[Set[str]] = None,
        limit: int = 100,
        random_sort: bool = False,
    ) -> List[Dict]:
        """
        Get filtered media items from the cache.

        Args:
            media_type: Filter by media type (movie, show, episode)
            genres: Filter by genres (case-insensitive)
            exclude_rating_keys: Set of rating keys to exclude
            limit: Maximum number of items to return
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
            for row in media_items:
                cached = None if full else self.media_items.get(str(row["rating_key"]))
                if cached:
                    library_items.append({**cached, "section_id": section_id})
                else:
                    rating_keys.append(row["rating_key"])

            # Process rating keys in smaller batches to improve reliability
            batch_size = 10  # Reduced from 20 to 10 for better reliability
            for i in range(0, len(rating_keys), batch_size):
                batch_keys = rating_keys[i : i + batch_size]
                
                # Log batch progress
                logger.debug(f"Processing batch {(i//batch_size)+1}/{(len(rating_keys)+batch_size-1)//batch_size} in {library['section_name']}")
                
                batch_tasks = [self._fetch_item_metadata(key) for key in batch_keys]
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

                for key, result in zip(batch_keys, batch_results):
                    if isinstance(result, Exception):
                        logger.error(f"Exception while fetching metadata: {result}")
                        result = None
                    # A failed fetch keeps the title we already had rather than dropping it.
                    item = result or self.media_items.get(str(key))
                    if item:
                        # section_id lets a later refresh keep this library's titles if it can't be read.
                        library_items.append({**item, "section_id": section_id})

                # Longer pause between batches to avoid API rate limits
                await asyncio.sleep(0.5)

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

    def suggest(self, query: str, media_type: Optional[str] = None, limit: int = 25) -> List[Dict]:
        """Titles for autocomplete: prefix matches first, then titles containing the query.

        Reads the cache as it is, without refreshing or logging: autocomplete runs on every
        keystroke, and Discord drops an answer that takes longer than three seconds.
        """
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

    async def refresh(self) -> None:
        """Fetch the whole library again now, even if the cache is still fresh.

        The current items stay in use until the new ones arrive. A background refresh already
        under way is waited for instead of starting a second one.
        """
        if self._refresh is not None and not self._refresh.done():
            await self._refresh
            return
        async with self.cache_lock:
            self.last_updated = None
        await self.update_cache(full=True)

    async def _fetch_item_metadata(self, rating_key: str) -> Optional[Dict]:
        """Fetch and process metadata for a single media item."""
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
                    }
                    return item_data
                else:
                    logger.error(f"Failed to fetch metadata for rating_key {rating_key}")
                    return None
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
