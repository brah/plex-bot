import requests
import json

CONFIG = json.load(open("config.json", "r"))


class Tautulli:
    def __init__(self) -> None:
        session = requests.Session()
        self.session = session
        self.api_key = CONFIG["tautulli_apikey"]
        self.tautulli_ip = CONFIG["tautulli_ip"]
        self.tautulli_api_url = (
            f"http://{self.tautulli_ip}/api/v2?apikey={self.api_key}&cmd="
        )

        """
        Title: Tautulli API Wrapper
        Names should correlate to their respective entries on https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference
        Parameters are also copied w. descriptions taken from above API page
        All methods return JSON, for specifics/example fields see API page
        
        As it stands, you need to pass optional arguments manually with the params, e.g.:
        params = {"session_key": 1}
        Required arguments are passed directly
        """

    def get_activity(self, params=None):
        """Get the current activity on the PMS.

        Optional parameters:
        session_key (int):  Session key for the session info to return, OR
        session_id (str):   Session ID for the session info to return
        """
        url = self.tautulli_api_url + "get_activity"
        response = self.session.get(url=url, params=params)
        return response.json()

    def get_history(self, params=None):
        """
        Get the Tautulli history.
        """
        url = self.tautulli_api_url + "get_history"
        response = self.session.get(url=url, params=params)
        return response.json()

    def get_home_stats(self, params=None):
        """Get the homepage watch statistics.

        Optional parameters:
        grouping (int):     0 or 1
        time_range (int):   The time range to calculate statistics, 30
        stats_type (str):   'plays' or 'duration'
        stats_start (int)   The row number of the stat item to start at, 0
        stats_count (int):  The number of stat items to return, 5
        stat_id (str):      A single stat to return, 'top_movies', 'popular_movies',
                            'top_tv', 'popular_tv', 'top_music', 'popular_music', 'top_libraries',
                            'top_users', 'top_platforms', 'last_watched', 'most_concurrent'
        """
        url = self.tautulli_api_url + "get_home_stats"
        response = self.session.get(url=url, params=params)
        return response.json()

    def get_recently_added(self, count: str, params=None):
        """Get all items that where recently added to plex.

        Required parameters:
        count (str):        Number of items to return

        Optional parameters:
        start (str):        The item number to start at
        media_type (str):   The media type: movie, show, artist
        section_id (str):   The id of the Plex library section
        """
        if count is None:
            return f"{count} is required; see `https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference#get_recently_added`"
        url = self.tautulli_api_url + f"get_recently_added&count={count}"
        response = self.session.get(url=url, params=params)
        return response.json()

    def get_collections_table(self, section_id: str):
        """Get the data on the Tautulli collections tables.

        Required parameters:
        section_id (str): The id of the Plex library section
        """
        if section_id is None:
            return f"{section_id} is required; see `https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference#get_collections_table`"
        url = self.tautulli_api_url + f"get_collections_table&section_id={section_id}"
        response = self.session.get(url=url)
        return response.json()

    def get_item_user_stats(self, rating_key: str, params=None):
        """Get the user stats for the media item.

        Required parameters:
        rating_key (str):   Rating key of the item

        Optional parameters:
        grouping (int):     0 or 1
        """
        if rating_key is None:
            return f"{rating_key} is required; see `https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference#get_item_user_stats`"
        url = self.tautulli_api_url + f"get_item_user_stats&rating_key={rating_key}"
        response = self.session.get(url=url, params=params)
        return response.json()

    def get_item_watch_time_stats(self, rating_key: str, params=None):
        """Get the watch time stats for the media item.

        Required parameters:
        rating_key (str):   Rating key of the item

        Optional parameters:
        grouping (int):     0 or 1
        query_days (str):   Comma separated days, e.g. "1,7,30,0"
        """
        if rating_key is None:
            return f"{rating_key} is required; see `https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference#get_item_user_stats`"
        url = (
            self.tautulli_api_url + f"get_item_watch_time_stats&rating_key={rating_key}"
        )
        response = self.session.get(url=url, params=params)
        return response.json()

    def pms_image_proxy(self, img: str = None, rating_key: str = None):
        if img is None and rating_key is None:
            return f"{img} or {rating_key} is required; see `https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference#pms_image_proxy`"
        else:
            if img is not None:
                url = self .tautulli_api_url + f"pms_image_proxy&img={img}"
            elif rating_key is not None:
                url = self.tautulli_api_url + f"pms_image_proxy&rating_key={rating_key}"
            self.session.get(url=url)
        return None

    def export_metadata(
        self, section_id: int = None, user_id: int = None, rating_key: int = None
    ):
        """Export library or media metadata to a file

        Required parameters:
        section_id (int):   The section id of the library items to export, OR
        user_id (int):      The user id of the playlist items to export, OR
        rating_key (int):   The rating key of the media item to export


        Optional parameters:
        file_format (str):          csv (default), json, xml, or m3u8
        metadata_level (int):       The level of metadata to export (default 1)
        media_info_level (int):     The level of media info to export (default 1)
        thumb_level (int):          The level of poster/cover images to export (default 0)
        art_level (int):            The level of background artwork images to export (default 0)
        custom_fields (str):        Comma separated list of custom fields to export
                                    in addition to the export level selected
        export_type (str):          'collection' or 'playlist' for library/user export,
                                    otherwise default to all library items
        individual_files (bool):    Export each item as an individual file for library/user export.
        """
        if section_id is None and user_id is None and rating_key is None:
            return f"{section_id}, {user_id}, or {rating_key} are required; see `https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference#export_metadata`"
        else:
            if section_id is not None:
                url = self.tautulli_api_url + f"export_metadata&section_id={section_id}"
            elif user_id is not None:
                url = self.tautulli_api_url + f"export_metadata&user_id={user_id}"
            elif rating_key is not None:
                url = self.tautulli_api_url + f"export_metadata&rating_key={rating_key}&file_format=json&thumb_level=1&art_level=1"
                print(url)
            response = self.session.get(url=url)
            print(response)
        return response.json()
