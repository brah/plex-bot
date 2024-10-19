import requests
import json
import logging

# Configure logging for this module
logger = logging.getLogger('plexbot.tautulli_wrapper')
logger.setLevel(logging.INFO)  # Set to INFO level for production

CONFIG = json.load(open("config.json", "r"))


class Tautulli:
    def __init__(self) -> None:
        logger.info("Initializing Tautulli wrapper.")
        self.session = requests.Session()
        self.api_key = CONFIG["tautulli_apikey"]
        self.tautulli_ip = CONFIG["tautulli_ip"]
        self.tautulli_api_url = (
            f"http://{self.tautulli_ip}/api/v2?apikey={self.api_key}&cmd="
        )
        logger.info("Tautulli API URL set.")

    def get_activity(self, params=None):
        """Get the current activity on the PMS."""
        url = self.tautulli_api_url + "get_activity"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def get_apikey(self, params=None):
        """Get the apikey. Username and password are required if auth is enabled."""
        url = self.tautulli_api_url + "get_apikey"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def get_history(self, params=None):
        """Get the Tautulli history."""
        url = self.tautulli_api_url + "get_history"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def get_home_stats(self, params=None):
        """Get the homepage watch statistics."""
        url = self.tautulli_api_url + "get_home_stats"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def get_recently_added(self, count: str, params=None):
        """Get all items that were recently added to Plex."""
        if count is None:
            error_msg = "count is required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        url = self.tautulli_api_url + f"get_recently_added&count={count}"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def get_collections_table(self, section_id: str):
        """Get the data on the Tautulli collections tables."""
        if section_id is None:
            error_msg = "section_id is required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        url = self.tautulli_api_url + f"get_collections_table&section_id={section_id}"
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def get_item_user_stats(self, rating_key: str, params=None):
        """Get the user stats for the media item."""
        if rating_key is None:
            error_msg = "rating_key is required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        url = self.tautulli_api_url + f"get_item_user_stats&rating_key={rating_key}"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def get_item_watch_time_stats(self, rating_key: str, params=None):
        """Get the watch time stats for the media item."""
        if rating_key is None:
            error_msg = "rating_key is required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        url = self.tautulli_api_url + f"get_item_watch_time_stats&rating_key={rating_key}"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def pms_image_proxy(self, img: str = None, rating_key: str = None):
        if img is None and rating_key is None:
            error_msg = "img or rating_key is required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        else:
            if img is not None:
                url = self.tautulli_api_url + f"pms_image_proxy&img={img}"
            elif rating_key is not None:
                url = self.tautulli_api_url + f"pms_image_proxy&rating_key={rating_key}"
        return None  # Note: Function seems incomplete; consider implementing or removing.

    def export_metadata(
        self, section_id: int = None, user_id: int = None, rating_key: int = None
    ):
        """Export library or media metadata to a file."""
        if section_id is None and user_id is None and rating_key is None:
            error_msg = "section_id, user_id, or rating_key are required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        if section_id is not None:
            url = self.tautulli_api_url + f"export_metadata&section_id={section_id}"
        elif user_id is not None:
            url = self.tautulli_api_url + f"export_metadata&user_id={user_id}"
        elif rating_key is not None:
            url = (
                self.tautulli_api_url
                + f"export_metadata&rating_key={rating_key}&file_format=json&thumb_level=1&art_level=1"
            )
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def get_metadata(self, rating_key: str = None, sync_id: str = None, params=None):
        """Get the metadata for a media item."""
        if rating_key is None and sync_id is None:
            error_msg = "Either rating_key or sync_id are required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        if rating_key is not None:
            url = self.tautulli_api_url + f"get_metadata&rating_key={rating_key}"
        elif sync_id is not None:
            url = self.tautulli_api_url + f"get_metadata&sync_id={sync_id}"
        response = self.session.get(url=url, params=params)
        response_json = response.json()
        return response_json

    def get_server_info(self):
        """Get the PMS server information."""
        url = self.tautulli_api_url + "get_server_info"
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def terminate_session(
        self, session_key: int = None, session_id: str = None, message: str = None
    ):
        """Stop a streaming session."""
        if session_id is None and session_key is None:
            error_msg = "Either session_key or session_id are required; see Tautulli API Reference."
            logger.error(error_msg)
            return error_msg
        if session_key is not None:
            url = self.tautulli_api_url + f"terminate_session&session_key={session_key}"
        elif session_id is not None:
            url = self.tautulli_api_url + f"terminate_session&session_id={session_id}"
        if message is not None:
            url += f"&message={message}"
        response = self.session.get(url=url)
        return response.status_code

    def get_library_user_stats(self, section_id: str = None):
        """Get user stats for a library."""
        if section_id is None:
            error_msg = "Section ID is required."
            logger.error(error_msg)
            return error_msg
        url = self.tautulli_api_url + f"get_library_user_stats&section_id={section_id}"
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def get_mapped_username(self, member):
        try:
            with open("map.json", "r") as f:
                mapping = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load mapping: {e}")
            return member.display_name

        for user_map in mapping:
            if str(user_map["discord_id"]) == str(member.id):
                return user_map["plex_username"]

        return member.display_name

    def get_libraries_table(self):
        """Get the data on the Tautulli libraries table."""
        url = self.tautulli_api_url + "get_libraries_table"
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def get_libraries(self):
        """Get a list of all the libraries."""
        url = self.tautulli_api_url + "get_libraries"
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def get_library(self, section_id):
        """Get information about a specific library."""
        if section_id is None:
            error_msg = "section_id is required."
            logger.error(error_msg)
            return error_msg
        url = self.tautulli_api_url + f"get_library&section_id={section_id}"
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def get_library_media_info(self, section_id=None, rating_key=None):
        """Get media information for a library or specific item."""
        length = 1000
        if section_id is None and rating_key is None:
            error_msg = "Either section_id or rating_key are required."
            logger.error(error_msg)
            return {"response": {"result": "error", "message": error_msg}}
        if section_id is not None:
            url = (
                self.tautulli_api_url
                + f"get_library_media_info&section_id={section_id}&length={length}"
            )
        else:
            url = (
                self.tautulli_api_url
                + f"get_library_media_info&rating_key={rating_key}&length={length}"
            )
        response = self.session.get(url=url)
        response_json = response.json()
        return response_json

    def get_most_watched_movies(self, time_range: int):
        """Retrieve details about the most watched movies."""
        params = {
            "stat_id": "popular_movies",
            "time_range": time_range,
        }
        try:
            response = self.session.get(
                self.tautulli_api_url + "get_home_stats", params=params
            )
            response.raise_for_status()
            response_json = response.json()
            return response_json
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err}")
        except Exception as err:
            logger.error(f"Other error occurred: {err}")
        return None

    def get_most_watched_shows(self, time_range: int):
        """Retrieve details about the most watched TV shows."""
        params = {
            "stat_id": "popular_tv",
            "time_range": time_range,
        }
        try:
            response = self.session.get(
                self.tautulli_api_url + "get_home_stats", params=params
            )
            response.raise_for_status()
            response_json = response.json()
            return response_json
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred: {http_err}")
        except Exception as err:
            logger.error(f"Other error occurred: {err}")
        return None


class TMDB:
    def __init__(self) -> None:
        logger.info("Initializing TMDB wrapper.")
        self.session = requests.Session()
        self.api_key = CONFIG["tmdb_apikey"]
        self.tmdb_api_url = "https://api.themoviedb.org/3/"
        logger.info("TMDB API URL set.")

    def search(self, query: str):
        """Search for movies and TV shows."""
        if not query:
            error_msg = "Query string is required for search."
            logger.error(error_msg)
            raise ValueError(error_msg)

        params = {
            'api_key': self.api_key,
            'query': query,
            'include_adult': False,
        }
        movie_url = self.tmdb_api_url + "search/movie"
        tv_url = self.tmdb_api_url + "search/tv"
        combined_results = []

        # Search movies
        movie_response = self.session.get(movie_url, params=params)
        if movie_response.status_code == 200:
            movie_results = movie_response.json().get('results', [])
            for result in movie_results:
                result['media_type'] = 'Movie'
            combined_results.extend(movie_results)
        else:
            logger.error(f"Failed to get movie search results: {movie_response.status_code}")

        # Search TV shows
        tv_response = self.session.get(tv_url, params=params)
        if tv_response.status_code == 200:
            tv_results = tv_response.json().get('results', [])
            for result in tv_results:
                result['media_type'] = 'TV Show'
            combined_results.extend(tv_results)
        else:
            logger.error(f"Failed to get TV search results: {tv_response.status_code}")

        # Sort results by popularity
        combined_results.sort(key=lambda x: x.get('popularity', 0), reverse=True)
        return combined_results

    def get_movie_details(self, movie_id: int = None):
        if movie_id is None:
            error_msg = "movie_id is required; see TMDB API Reference."
            logger.error(error_msg)
            return error_msg
        url = self.tmdb_api_url + f"movie/{movie_id}"
        params = {'api_key': self.api_key}
        response = self.session.get(url=url, params=params)
        if response.status_code == 200:
            response_json = response.json()
            return response_json
        else:
            logger.error(f"Failed to get movie details: {response.status_code}")
            return None
