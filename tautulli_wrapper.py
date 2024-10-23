# tautulli_wrapper.py

import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any

# Configure logging for this module
logger = logging.getLogger("plexbot.tautulli_wrapper")
logger.setLevel(logging.INFO)


class Tautulli:
    def __init__(self, api_key: str, tautulli_ip: str) -> None:
        logger.info("Initializing Tautulli wrapper.")
        self.api_key = api_key
        self.tautulli_ip = tautulli_ip
        self.tautulli_api_url = f"http://{self.tautulli_ip}/api/v2"
        self.session: Optional[aiohttp.ClientSession] = None
        logger.info(f"Tautulli API URL set to {self.tautulli_api_url}")

    async def initialize(self) -> None:
        """Asynchronous initializer to set up aiohttp ClientSession."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
            logger.info("aiohttp ClientSession initialized for Tautulli.")

    async def close(self) -> None:
        """Close the aiohttp ClientSession."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("aiohttp ClientSession closed for Tautulli.")

    async def api_call(self, cmd: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        if params is None:
            params = {}
        params["apikey"] = self.api_key
        params["cmd"] = cmd
        try:
            async with self.session.get(self.tautulli_api_url, params=params, timeout=30) as response:
                response_json = await response.json()
                return response_json
        except asyncio.TimeoutError:
            logger.error(f"API call '{cmd}' timed out.")
        except Exception as e:
            logger.error(f"API call '{cmd}' failed: {e}")
        return None

    async def get_activity(self, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Get the current activity on the PMS."""
        return await self.api_call("get_activity", params)

    async def get_history(self, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Get the Tautulli history."""
        return await self.api_call("get_history", params)

    async def get_home_stats(self, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Get the homepage watch statistics."""
        return await self.api_call("get_home_stats", params)

    async def get_recently_added(self, count: int) -> Optional[Dict[str, Any]]:
        """Get all items that were recently added to Plex."""
        if count is None:
            error_msg = "count is required; see Tautulli API Reference."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        params = {"count": count}
        return await self.api_call("get_recently_added", params)

    async def get_collections_table(self, section_id: str) -> Optional[Dict[str, Any]]:
        """Get the data on the Tautulli collections tables."""
        if section_id is None:
            error_msg = "section_id is required; see Tautulli API Reference."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        params = {"section_id": section_id}
        return await self.api_call("get_collections_table", params)

    async def get_item_user_stats(
        self, rating_key: str, params: Dict[str, Any] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the user stats for the media item."""
        if rating_key is None:
            error_msg = "rating_key is required; see Tautulli API Reference."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        if params is None:
            params = {}
        params["rating_key"] = rating_key
        return await self.api_call("get_item_user_stats", params)

    async def get_item_watch_time_stats(
        self, rating_key: str, params: Dict[str, Any] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the watch time stats for the media item."""
        if rating_key is None:
            error_msg = "rating_key is required; see Tautulli API Reference."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        if params is None:
            params = {}
        params["rating_key"] = rating_key
        return await self.api_call("get_item_watch_time_stats", params)

    async def get_metadata(self, rating_key: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a media item."""
        if rating_key is None:
            error_msg = "rating_key is required; see Tautulli API Reference."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        params = {"rating_key": rating_key}
        return await self.api_call("get_metadata", params)

    async def get_server_info(self) -> Optional[Dict[str, Any]]:
        """Get the PMS server information."""
        return await self.api_call("get_server_info")

    async def terminate_session(
        self, session_key: int = None, session_id: str = None, message: str = None
    ) -> int:
        """Stop a streaming session."""
        if session_id is None and session_key is None:
            error_msg = "Either session_key or session_id is required."
            logger.error(error_msg)
            return 400
        params = {"message": message}
        if session_key is not None:
            params["session_key"] = session_key
        else:
            params["session_id"] = session_id
        response = await self.api_call("terminate_session", params)
        if response and response.get("response", {}).get("result") == "success":
            return 200
        else:
            return 400

    async def get_library_user_stats(self, section_id: str = None) -> Optional[Dict[str, Any]]:
        """Get user stats for a library."""
        if section_id is None:
            error_msg = "Section ID is required."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        params = {"section_id": section_id}
        return await self.api_call("get_library_user_stats", params)

    async def get_libraries_table(self) -> Optional[Dict[str, Any]]:
        """Get the data on the Tautulli libraries table."""
        return await self.api_call("get_libraries_table")

    async def get_libraries(self) -> Optional[Dict[str, Any]]:
        """Get a list of all the libraries."""
        return await self.api_call("get_libraries")

    async def get_library(self, section_id) -> Optional[Dict[str, Any]]:
        """Get information about a specific library."""
        if section_id is None:
            error_msg = "section_id is required."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        params = {"section_id": section_id}
        return await self.api_call("get_library", params)

    async def get_library_media_info(
        self,
        section_id=None,
        rating_key=None,
        media_info=0,
        length=50,
        include_metadata=0,
    ) -> Optional[Dict[str, Any]]:
        """Get media information for a library or specific item."""
        if section_id is None and rating_key is None:
            error_msg = "Either section_id or rating_key are required."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        params = {
            "media_info": media_info,
            "include_metadata": include_metadata,
            "length": length,
        }
        if section_id is not None:
            params["section_id"] = section_id
        else:
            params["rating_key"] = rating_key
        return await self.api_call("get_library_media_info", params)

    async def get_most_watched_movies(self, time_range: int) -> Optional[Dict[str, Any]]:
        """Retrieve details about the most watched movies."""
        params = {
            "stat_id": "popular_movies",
            "time_range": time_range,
        }
        return await self.api_call("get_home_stats", params)

    async def get_most_watched_shows(self, time_range: int) -> Optional[Dict[str, Any]]:
        """Retrieve details about the most watched TV shows."""
        params = {
            "stat_id": "popular_tv",
            "time_range": time_range,
        }
        return await self.api_call("get_home_stats", params)

    def pms_image_proxy(self, img: str) -> str:
        """Construct the PMS image proxy URL."""
        return f"http://{self.tautulli_ip}/pms_image_proxy?img={img}&width=300&height=450&fallback=poster"


class TMDB:
    def __init__(self, api_key: str) -> None:
        logger.info("Initializing TMDB wrapper.")
        self.api_key = api_key
        self.tmdb_api_url = "https://api.themoviedb.org/3/"
        self.session: Optional[aiohttp.ClientSession] = None
        logger.info("TMDB API URL set.")

    async def initialize(self) -> None:
        """Asynchronous initializer to set up aiohttp ClientSession."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
            logger.info("aiohttp ClientSession initialized for TMDB.")

    async def close(self) -> None:
        """Close the aiohttp ClientSession."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("aiohttp ClientSession closed for TMDB.")

    async def search(self, query: str) -> Optional[list]:
        """Search for movies and TV shows."""
        if not query:
            error_msg = "Query string is required for search."
            logger.error(error_msg)
            raise ValueError(error_msg)

        params = {
            "api_key": self.api_key,
            "query": query,
            "include_adult": False,
        }
        movie_url = self.tmdb_api_url + "search/movie"
        tv_url = self.tmdb_api_url + "search/tv"
        combined_results = []

        # Use a shared method to avoid code duplication
        async def fetch_results(url: str, media_type: str):
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    results = (await response.json()).get("results", [])
                    for result in results:
                        result["media_type"] = media_type
                    return results
                else:
                    logger.error(f"Failed to get {media_type} search results: {response.status}")
                    return []

        movie_results = await fetch_results(movie_url, "movie")
        tv_results = await fetch_results(tv_url, "tv_show")
        combined_results.extend(movie_results + tv_results)

        # Sort results by popularity
        combined_results.sort(key=lambda x: x.get("popularity", 0), reverse=True)
        return combined_results

    async def get_movie_details(self, movie_id: int) -> Optional[dict]:
        if movie_id is None:
            error_msg = "movie_id is required; see TMDB API Reference."
            logger.error(error_msg)
            return None
        url = self.tmdb_api_url + f"movie/{movie_id}"
        params = {"api_key": self.api_key}
        async with self.session.get(url=url, params=params) as response:
            if response.status == 200:
                response_json = await response.json()
                return response_json
            else:
                logger.error(f"Failed to get movie details: {response.status}")
                return None

    async def get_genre_id(self, genre_name: str) -> Optional[int]:
        """Get the TMDB genre ID for a given genre name."""
        url = self.tmdb_api_url + "genre/movie/list"
        params = {"api_key": self.api_key, "language": "en-US"}
        async with self.session.get(url=url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                genres = data.get("genres", [])
                for genre in genres:
                    if genre["name"].lower() == genre_name.lower():
                        return genre["id"]
            else:
                logger.error(f"Failed to get genre list: {response.status}")
        return None

    async def get_popular_items(self, genre_id: int) -> Optional[list]:
        """Get popular movies or shows for a given genre ID."""
        recommendations = []
        for media_type in ["movie", "tv"]:
            url = self.tmdb_api_url + f"discover/{media_type}"
            params = {
                "api_key": self.api_key,
                "language": "en-US",
                "sort_by": "popularity.desc",
                "with_genres": genre_id,
                "include_adult": "false",  # Convert boolean to string
            }
            async with self.session.get(url=url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    for item in data.get("results", []):
                        item["media_type"] = media_type
                        recommendations.append(item)
                else:
                    logger.error(f"Failed to get popular items for {media_type}: {response.status}")
        return recommendations
