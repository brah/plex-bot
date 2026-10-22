"""Async clients for Tautulli's API (every call goes through Tautulli.api_call) and TMDB's."""

import asyncio
import ipaddress
import json
import logging
from datetime import tzinfo
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import tzlocal

# Configure logging for this module
logger = logging.getLogger("plexbot.tautulli_wrapper")


def on_the_internet(address: str) -> bool:
    """Whether a "host:port" is reached over the internet, where plain HTTP would expose the API key:
    not localhost, a private or LAN address, or a one-word name such as a Docker service ("tautulli")."""
    host = urlsplit(f"//{address}").hostname or ""
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host and not host.endswith((".local", ".lan", ".internal", ".home.arpa"))


class Tautulli:
    def __init__(self, api_key: str, tautulli_ip: str, use_https: bool = False) -> None:
        logger.info("Initializing Tautulli wrapper.")
        self.api_key = api_key
        self.tautulli_ip = tautulli_ip
        self.use_https = use_https
        self.protocol = "https" if use_https else "http"
        self.tautulli_api_url = f"{self.protocol}://{self.tautulli_ip}/api/v2"
        self.session: Optional[aiohttp.ClientSession] = None
        self.request_timeout = 30
        self._pms_identifier: Optional[str] = None  # cached by server_id()
        self._zone: Optional[tzinfo] = None  # cached by server_timezone()
        self._unreachable = False  # while True, connection failures are logged at DEBUG
        if not use_https and on_the_internet(tautulli_ip):
            logger.warning(f"Tautulli at {tautulli_ip} is reached over the internet using HTTP, so the API key "
                           "is sent in plaintext. Set tautulli.use_https (PLEXBOT_TAUTULLI_USE_HTTPS) to true.")
        logger.info(f"Tautulli API URL set to {self.tautulli_api_url}")

    def _ensure_session(self) -> None:
        """Create an HTTP session if one is not already available."""
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

        self._ensure_session()

        params["apikey"] = self.api_key
        params["cmd"] = cmd
        try:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            async with self.session.get(self.tautulli_api_url, params=params, timeout=timeout) as response:
                if self._unreachable:
                    self._unreachable = False
                    logger.info("Tautulli is answering again.")
                if response.status >= 400 and response.content_type == "application/json":
                    # Tautulli's refusal (a removed title, a bad key): returned so callers can tell
                    # "no" from no answer; check_response reads it as a failure.
                    body = await response.json()
                    message = (body.get("response") or {}).get("message")
                    logger.error(f"API call '{cmd}' refused (HTTP {response.status}): {message}")
                    return body
                response.raise_for_status()
                return await response.json()
        except asyncio.TimeoutError:
            self._connection_failed(cmd, f"timed out after {self.request_timeout}s")
        except aiohttp.ClientConnectionError as exc:
            self._connection_failed(cmd, exc)
        except aiohttp.ClientError as exc:
            logger.error(f"API call '{cmd}' failed: {exc}")
        except json.JSONDecodeError:
            logger.error(f"API call '{cmd}' returned invalid JSON.")

        return None

    def _connection_failed(self, cmd: str, reason) -> None:
        """Log the first failure of an outage as an error, and the rest at DEBUG until Tautulli answers.

        The presence loop asks every 15 seconds, so a long outage would otherwise bury everything else.
        """
        if self._unreachable:
            logger.debug(f"API call '{cmd}' failed: {reason}")
            return
        self._unreachable = True
        logger.error(f"API call '{cmd}' failed: {reason}. Until Tautulli answers again, "
                     "further connection failures are only logged at DEBUG.")

    @staticmethod
    def check_response(response: Optional[Dict[str, Any]]) -> bool:
        """Check if a Tautulli API response indicates success."""
        return bool(response and response.get("response", {}).get("result") == "success")

    @staticmethod
    def get_response_data(response: Optional[Dict[str, Any]], default=None):
        """Safely extract the data from a Tautulli API response."""
        if not response:
            return default
        return response.get("response", {}).get("data", default)

    @staticmethod
    def data(response: Optional[Dict[str, Any]], default=None):
        """The response's data when Tautulli answered "success", else default."""
        return Tautulli.get_response_data(response, default) if Tautulli.check_response(response) else default

    async def get_activity(self, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Get the current activity on the PMS."""
        return await self.api_call("get_activity", params)

    async def get_history(self, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Get the Tautulli history."""
        return await self.api_call("get_history", params)

    async def get_history_rows(self, params: Dict[str, Any], page: int = 1000,
                               fields: Optional[tuple] = None) -> Optional[list]:
        """Every history row matching params, a page at a time; None when Tautulli didn't answer.

        get_history returns at most `length` rows, newest first, so one call over a long window on a
        busy server would silently lose the oldest plays. Bound the rows with params (e.g. "after").
        fields: keep only these keys, trimmed per page to save memory.
        """
        return await self._all_rows(
            lambda start: self.get_history({**params, "start": start, "length": page}), page, key="row_id",
            fields=fields,
        )

    async def get_library_media_rows(self, section_id, page: int = 1000) -> Optional[list]:
        """Every title in a library, a page at a time; None when Tautulli didn't answer."""
        return await self._all_rows(
            lambda start: self.get_library_media_info(section_id=section_id, start=start, length=page), page,
            key="rating_key",
        )

    async def _all_rows(self, fetch, page: int, key: str, fields: Optional[tuple] = None) -> Optional[list]:
        """Call fetch(start) for successive pages of a paged Tautulli table until one comes back short.

        A row added while paging pushes the rest down a place, so the next page starts with a row
        already read; rows whose `key` was seen before are dropped.
        """
        rows: list = []
        seen = set()
        start = 0
        while True:
            response = await fetch(start)
            if not self.check_response(response):
                return None
            batch = (self.get_response_data(response, {}) or {}).get("data") or []
            start += len(batch)
            for row in batch:
                row_key = row.get(key)
                if row_key is None or row_key not in seen:
                    seen.add(row_key)
                    rows.append({name: row[name] for name in fields if name in row} if fields else row)
            if len(batch) < page:
                return rows

    async def get_image(
        self, key: Optional[str], width: int = 300, height: int = 450, fallback: Optional[str] = None
    ) -> Optional[bytes]:
        """A Plex image (poster, art, preview frame) through Tautulli's image proxy, or None.

        fallback names the stock image Tautulli substitutes when the real one is missing
        (e.g. "poster"); without it a missing image is None, never a placeholder.
        """
        if not key or not key.strip():
            return None
        self._ensure_session()
        params = {"cmd": "pms_image_proxy", "apikey": self.api_key, "img": key.strip(),
                  "width": width, "height": height}
        if fallback:
            params["fallback"] = fallback
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.get(self.tautulli_api_url, params=params, timeout=timeout) as response:
                if response.status == 200 and "image" in (response.content_type or ""):
                    return await response.read()
                logger.warning(f"Failed to fetch image: status={response.status}, "
                               f"content_type={response.content_type}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Error fetching image {key}: {e}")
        return None

    async def get_settings(self) -> Optional[Dict[str, Any]]:
        """Get Tautulli's settings (default_timezone, ...)."""
        return await self.api_call("get_settings")

    async def get_users(self) -> Optional[Dict[str, Any]]:
        """Get every Plex user Tautulli knows (user_id, username, friendly_name, thumb, ...)."""
        return await self.api_call("get_users")

    async def get_home_stats(self, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Get the homepage watch statistics."""
        return await self.api_call("get_home_stats", params)

    async def get_recently_added(self, count: int, media_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get all items that were recently added to Plex (media_type: movie, show, artist)."""
        params = {"count": count}
        if media_type:
            params["media_type"] = media_type
        return await self.api_call("get_recently_added", params)

    async def get_item_user_stats(self, rating_key: str) -> Optional[Dict[str, Any]]:
        """Get the user stats for the media item."""
        return await self.api_call("get_item_user_stats", {"rating_key": rating_key})

    async def get_item_watch_time_stats(self, rating_key: str) -> Optional[Dict[str, Any]]:
        """Get the watch time stats for the media item."""
        return await self.api_call("get_item_watch_time_stats", {"rating_key": rating_key})

    async def get_metadata(self, rating_key: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a media item."""
        return await self.api_call("get_metadata", {"rating_key": rating_key})

    async def get_server_info(self) -> Optional[Dict[str, Any]]:
        """Get the PMS server information."""
        return await self.api_call("get_server_info")

    async def server_id(self) -> Optional[str]:
        """The Plex server's machine identifier, for app.plex.tv links (cached once known)."""
        if self._pms_identifier is None:
            response = await self.get_server_info()
            if self.check_response(response):
                self._pms_identifier = self.get_response_data(response, {}).get("pms_identifier") or ""
        return self._pms_identifier or None

    async def server_timezone(self) -> tzinfo:
        """The timezone Tautulli is set to (so 9pm means 9pm on the server), else the host's."""
        if self._zone is None:
            response = await self.get_settings()
            if not self.check_response(response):
                return tzlocal.get_localzone()  # not remembered: ask Tautulli again next time
            name = (self.get_response_data(response, {}) or {}).get("default_timezone")
            try:
                self._zone = ZoneInfo(name) if name else tzlocal.get_localzone()
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning(f"Unknown Tautulli timezone {name!r}; using the host's.")
                self._zone = tzlocal.get_localzone()
        return self._zone

    async def get_pms_update(self) -> Optional[Dict[str, Any]]:
        """Check whether a Plex Media Server update is available."""
        return await self.api_call("get_pms_update")

    async def terminate_session(self, session_id: str, message: Optional[str] = None) -> int:
        """Stop a streaming session: 200 when Tautulli stopped it, else 400."""
        params = {"session_id": session_id}
        if message:  # omitted when absent: aiohttp/yarl reject None query values outright
            params["message"] = message
        response = await self.api_call("terminate_session", params)
        return 200 if self.check_response(response) else 400

    async def get_library_user_stats(self, section_id: str) -> Optional[Dict[str, Any]]:
        """Get user stats for a library."""
        return await self.api_call("get_library_user_stats", {"section_id": section_id})

    async def get_libraries_table(self) -> Optional[Dict[str, Any]]:
        """Get the data on the Tautulli libraries table."""
        return await self.api_call("get_libraries_table")

    async def get_libraries(self) -> Optional[Dict[str, Any]]:
        """Get a list of all the libraries."""
        return await self.api_call("get_libraries")

    async def get_library_media_info(
        self, section_id, length: int = 50, search: Optional[str] = None, start: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Get one page of a library's titles (total_file_size comes with it)."""
        params = {"section_id": section_id, "start": start, "length": length}
        if search is not None:
            params["search"] = search
        return await self.api_call("get_library_media_info", params)

# Plex media type -> TMDB's name for it in API paths.
TMDB_KINDS = {"movie": "movie", "show": "tv"}


class TMDB:
    """TMDB's API, for trailers, More like this and next episodes. Created when tmdb.apikey is set.

    Every call returns None when TMDB doesn't answer, so a card never waits on it for long.
    """

    TIMEOUT = 5  # seconds

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.api_url = "https://api.themoviedb.org/3/"
        self.session: Optional[aiohttp.ClientSession] = None
        self._unreachable = False  # while True, failures are logged at DEBUG

    def _ensure_session(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    def _auth(self) -> tuple:
        """(headers, params): TMDB's settings offer an API key and a longer Read Access Token (a JWT)."""
        if self.api_key.startswith("eyJ"):
            return {"Authorization": f"Bearer {self.api_key}"}, {}
        return {}, {"api_key": self.api_key}

    async def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        self._ensure_session()
        headers, auth = self._auth()
        try:
            async with self.session.get(
                self.api_url + path, params={**auth, "language": "en-US", **(params or {})},
                headers=headers, timeout=aiohttp.ClientTimeout(total=self.TIMEOUT),
            ) as response:
                response.raise_for_status()
                body = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            # Log the first failure of an outage as an error, the rest at DEBUG until TMDB answers.
            log = logger.debug if self._unreachable else logger.error
            log(f"TMDB request '{path}' failed: {exc!r}")
            self._unreachable = True
            return None
        if self._unreachable:
            self._unreachable = False
            logger.info("TMDB is answering again.")
        return body

    async def title(self, kind: str, tmdb_id: int) -> Optional[dict]:
        """A movie's or show's details, with its recommendations, similar titles and videos."""
        return await self._get(f"{kind}/{tmdb_id}",
                               {"append_to_response": "recommendations,similar,videos"})

    async def recommendations(self, kind: str, tmdb_id: int, page: int = 2) -> Optional[list]:
        """One more page of recommendations (title() includes the first)."""
        body = await self._get(f"{kind}/{tmdb_id}/recommendations", {"page": page})
        return None if body is None else body.get("results") or []

    async def show(self, tmdb_id: int) -> Optional[dict]:
        """A show's details: its status and next_episode_to_air."""
        return await self._get(f"tv/{tmdb_id}")

    @staticmethod
    def id_from_guids(guids) -> Optional[int]:
        """The TMDB id from Plex guids like ["imdb://tt0312004", "tmdb://533"]."""
        for guid in guids or []:
            if str(guid).startswith("tmdb://") and str(guid)[7:].isdigit():
                return int(str(guid)[7:])
        return None

    @staticmethod
    def trailer_url(videos: list) -> Optional[str]:
        """The newest official YouTube trailer, else the newest YouTube trailer."""
        trailers = [v for v in videos or []
                    if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("key")]
        if not trailers:
            return None
        best = max(trailers, key=lambda v: (bool(v.get("official")), v.get("published_at") or ""))
        return f"https://www.youtube.com/watch?v={best['key']}"

    @staticmethod
    def suggestion_ids(details: dict, more: Optional[list]) -> list:
        """TMDB ids to suggest, best first: recommendations, the extra page of them, then similar titles."""
        results = [
            *((details.get("recommendations") or {}).get("results") or []),
            *(more or []),
            *((details.get("similar") or {}).get("results") or []),
        ]
        return list(dict.fromkeys(r["id"] for r in results if isinstance(r.get("id"), int)))
