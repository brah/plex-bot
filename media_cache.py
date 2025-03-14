# media_cache.py

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
import aiofiles

# Configure logging
logger = logging.getLogger("plexbot.media_cache")
logger.setLevel(logging.INFO)


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

    async def initialize(self) -> None:
        """Initialize the cache by loading from disk and updating if needed."""
        # Create cache directory if it doesn't exist
        self.cache_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Load cache from disk
        await self.load_cache_from_disk()

        # Always force an update if the cache is empty
        if not self.media_items:
            logger.info("Cache is empty, triggering initial population")
            try:
                # Fetch and process all media items
                new_items = await self._fetch_all_media_items()

                if new_items:
                    # Update the cache with new items
                    self.media_items = {str(item["rating_key"]): item for item in new_items}
                    self.last_updated = datetime.now()

                    # Save the updated cache to disk
                    await self.save_cache_to_disk()
                    logger.info(f"Media cache initialized with {len(self.media_items)} items")
                else:
                    logger.warning("No media items found during initialization")
            except Exception as e:
                logger.error(f"Failed to initialize media cache: {e}")
        else:
            logger.info(f"Media cache loaded with {len(self.media_items)} items")

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
        """Check if cache needs updating and update if necessary."""
        if not self.is_cache_valid():
            await self.update_cache()

    async def update_cache(self) -> None:
        """Update the media cache with fresh data from Tautulli."""
        async with self.cache_lock:
            if self.is_cache_valid():
                # Another process might have updated the cache while we were waiting for the lock
                return

            logger.info("Updating media cache...")
            try:
                # Check Tautulli connection first
                test_response = await self.tautulli.get_server_info()
                if not test_response or test_response.get("response", {}).get("result") != "success":
                    logger.error("Tautulli connection test failed")
                    return

                # Fetch and process all media items
                new_items = await self._fetch_all_media_items()

                # Update the cache with new items
                if new_items:
                    self.media_items = {str(item["rating_key"]): item for item in new_items}
                    self.last_updated = datetime.now()

                    # Save the updated cache to disk
                    await self.save_cache_to_disk()
                    logger.info(f"Media cache updated with {len(self.media_items)} items")
                else:
                    logger.warning("No media items found during update")
            except Exception as e:
                logger.error(f"Failed to update media cache: {e}")

    async def get_item(self, rating_key: str) -> Optional[Dict]:
        """
        Get a specific media item by its rating key.

        Args:
            rating_key: The rating key of the media item

        Returns:
            The media item dictionary or None if not found
        """
        await self.ensure_cache_valid()
        return self.media_items.get(str(rating_key))

    async def get_items(
        self,
        media_type: Optional[str] = None,
        genres: Optional[List[str]] = None,
        exclude_rating_keys: Optional[Set[str]] = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = None,
        random_sort: bool = False,
    ) -> List[Dict]:
        """
        Get filtered media items from the cache.

        Args:
            media_type: Filter by media type (movie, show, episode)
            genres: Filter by genres (case-insensitive)
            exclude_rating_keys: Set of rating keys to exclude
            limit: Maximum number of items to return
            offset: Number of items to skip
            sort_by: Sort by this field
            random_sort: Whether to randomly shuffle results

        Returns:
            List of media items matching the filters
        """
        import random

        await self.ensure_cache_valid()

        # Create a copy of all items as a list
        items = list(self.media_items.values())

        # Apply media type filter
        if media_type:
            if media_type.lower() == "tv":
                valid_media_types = ["show", "episode"]
            elif media_type.lower() == "movie":
                valid_media_types = ["movie"]
            else:
                valid_media_types = [media_type.lower()]

            items = [
                item for item in items if item.get("media_type", "unknown").lower() in valid_media_types
            ]

        # Apply genre filter
        if genres:
            genres_lower = [genre.lower() for genre in genres]
            items = [
                item for item in items if any(g.lower() in genres_lower for g in item.get("genres", []))
            ]

        # Exclude rating keys
        if exclude_rating_keys:
            exclude_keys = set(str(key) for key in exclude_rating_keys)
            items = [item for item in items if str(item.get("rating_key")) not in exclude_keys]

        # Sort the results
        if random_sort:
            random.shuffle(items)
        elif sort_by:
            items.sort(key=lambda x: x.get(sort_by, ""), reverse=True)

        # Apply pagination
        paginated_items = items[offset : offset + limit]

        return paginated_items

    async def search(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Search for media items by title.

        Args:
            query: The search query
            limit: Maximum number of results to return

        Returns:
            List of media items matching the search query
        """
        query = query.lower()
        await self.ensure_cache_valid()

        matching_items = []
        for item in self.media_items.values():
            title = item.get("title", "").lower()
            if query in title:
                matching_items.append(item)
                if len(matching_items) >= limit:
                    break

        return matching_items

    async def load_cache_from_disk(self) -> None:
        """Load the media cache from disk."""
        if not self.cache_file_path.exists():
            logger.info("No media cache file found. Starting with an empty cache.")
            self.media_items = {}
            return

        try:
            async with self.cache_lock:
                async with aiofiles.open(self.cache_file_path, "r", encoding="utf-8") as f:
                    contents = await f.read()
                    data = json.loads(contents)

                    # Convert the list to a dictionary for faster lookups
                    self.media_items = {str(item["rating_key"]): item for item in data}
                    self.last_updated = datetime.now()

                logger.info(
                    f"Media cache loaded from {self.cache_file_path} with {len(self.media_items)} items"
                )
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error loading cache: {e}")
            self.media_items = {}
        except Exception as e:
            logger.error(f"Failed to load media cache from disk: {e}")
            self.media_items = {}

    async def save_cache_to_disk(self) -> None:
        """Save the media cache to disk with improved efficiency."""
        self.cache_file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            logger.info(f"Saving media cache to {self.cache_file_path}")
            async with self.cache_lock:
                if not self.media_items:
                    logger.warning("Attempted to save empty cache to disk")
                    return

                # Use a temporary file for safety
                temp_file = self.cache_file_path.with_suffix(".tmp")

                # Convert to a list first to avoid holding the lock during file writes
                items_list = list(self.media_items.values())

            # Release the lock before file operations
            # Write in chunks to avoid memory issues
            chunk_size = 100
            async with aiofiles.open(temp_file, "w", encoding="utf-8") as f:
                # Write opening bracket
                await f.write("[\n")

                # Write items in chunks
                for i, item in enumerate(items_list):
                    json_str = json.dumps(item, ensure_ascii=False)
                    if i < len(items_list) - 1:
                        json_str += ","
                    await f.write(json_str + "\n")

                    # Periodically yield control back to the event loop
                    if i % chunk_size == 0 and i > 0:
                        await asyncio.sleep(0)

                # Write closing bracket
                await f.write("]\n")

            # Rename temp file to final file
            if temp_file.exists():
                if self.cache_file_path.exists():
                    self.cache_file_path.unlink()
                temp_file.rename(self.cache_file_path)

            logger.info(f"Media cache saved to {self.cache_file_path}")
        except Exception as e:
            logger.error(f"Failed to save media cache to disk: {e}")

    async def _fetch_all_media_items(self) -> List[Dict]:
        """
        Fetch all media items from Tautulli and gather their metadata.

        This is an internal method used during cache updates.
        """
        all_media_items = []
        libraries = await self._get_libraries()
        logger.info(f"Starting to fetch media items from {len(libraries)} libraries.")

        tasks = []
        for library in libraries:
            tasks.append(self._process_library(library))

        # Process all libraries concurrently with limits
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten the results and filter out any exceptions
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Error processing library: {result}")
            elif isinstance(result, list):
                all_media_items.extend(result)

        # Filter out low-quality/empty entries before returning
        filtered_items = self._filter_quality_items(all_media_items)

        items_filtered = len(all_media_items) - len(filtered_items)
        if items_filtered > 0:
            logger.info(f"Filtered out {items_filtered} low-quality media items")

        logger.info(f"Fetched a total of {len(filtered_items)} quality media items.")
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
                and (not item.get("genres") or len(item.get("genres", [])) == 0)
                and not item.get("year")
                and not item.get("play_count")
                and not item.get("summary")
                and not item.get("rating")
            ):
                continue

            # Skip items with "unknown" media type and no genres
            if item.get("media_type") == "unknown" and (
                not item.get("genres") or len(item.get("genres", [])) == 0
            ):
                continue

            # Keep all other items
            filtered_items.append(item)

        return filtered_items

    async def _process_library(self, library: Dict) -> List[Dict]:
        """Process a single library to extract media items."""
        library_items = []
        try:
            logger.info(
                f"Fetching media items for library: {library['section_name']} (ID: {library['section_id']})"
            )

            # Fetch basic media info for this library
            response = await self.tautulli.get_library_media_info(
                section_id=library["section_id"],
                length=10000,  # Maximum items to fetch
                include_metadata=0,  # Don't include full metadata in this call
            )

            if not response or response.get("response", {}).get("result") != "success":
                logger.error(f"Failed to fetch media info for library {library['section_id']}")
                return library_items

            media_items = response.get("response", {}).get("data", {}).get("data", [])
            if not media_items:
                logger.info(f"No media items found in library {library['section_name']}")
                return library_items

            logger.info(f"Processing {len(media_items)} items from library '{library['section_name']}'")

            # Collect all rating keys for the metadata fetch
            rating_keys = [item["rating_key"] for item in media_items]

            # Process rating keys in batches to avoid overwhelming the API
            batch_size = 20
            for i in range(0, len(rating_keys), batch_size):
                batch_keys = rating_keys[i : i + batch_size]
                batch_tasks = [self._fetch_item_metadata(key) for key in batch_keys]
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

                for result in batch_results:
                    if isinstance(result, Exception):
                        logger.error(f"Exception while fetching metadata: {result}")
                    elif result:
                        library_items.append(result)

                # Small pause to avoid API rate limits
                await asyncio.sleep(0.2)

            logger.info(
                f"Completed processing library '{library['section_name']}' with {len(library_items)} items"
            )
            return library_items

        except Exception as e:
            logger.error(f"Error processing library {library.get('section_name', 'unknown')}: {e}")
            return library_items

    async def _fetch_item_metadata(self, rating_key: str) -> Optional[Dict]:
        """Fetch and process metadata for a single media item."""
        async with self.semaphore:
            try:
                metadata_response = await self.tautulli.get_metadata(rating_key=rating_key)

                if not metadata_response:
                    logger.error(f"No response for metadata request on rating_key {rating_key}")
                    return None

                if metadata_response.get("response", {}).get("result") == "success":
                    metadata = metadata_response.get("response", {}).get("data", {})
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

        if not response or response.get("response", {}).get("result") != "success":
            logger.error("Failed to fetch libraries from Tautulli")
            return []

        libraries = response.get("response", {}).get("data", [])
        # Only include libraries with supported media types
        filtered_libraries = [
            lib for lib in libraries if lib["section_type"] in ("movie", "show", "episode")
        ]
        return filtered_libraries
