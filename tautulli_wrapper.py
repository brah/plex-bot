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

    def get_home_stats(self, params=None):
        url = self.tautulli_api_url + "get_home_stats"
        response = self.session.get(url=url, params=params)
        return response.json()

    def get_history(self, params=None):
        url = self.tautulli_api_url + "get_history"
        response = self.session.get(url=url, params=params)
        return response.json()

    def get_activity(self, params=None):
        url = self.tautulli_api_url + "get_activity"
        response = self.session.get(url=url, params=params)
        return response.json()
    
    def recently_added(self, params=None):
        url = self.tautulli_api_url + "get_recently_added"
        response = self.session.get(url=url, params=params)
        return response.json()