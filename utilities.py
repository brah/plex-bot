# utilities.py

import json
import logging
import subprocess
from functools import lru_cache
from io import BytesIO
from typing import List, Dict, Any, Optional, Tuple

import aiohttp
from nextcord.ext import menus
from nextcord import File

logger = logging.getLogger("plexbot.utilities")
logger.setLevel(logging.INFO)


# Bot configuration lives in the `config` package (config/__init__.py).
# The old utilities.Config JSON reader was removed as dead code.


class UserMappings:
    _mappings = None
    _mapping_file = "map.json"

    @classmethod
    @lru_cache(maxsize=1)
    def load_user_mappings(cls) -> List[Dict[str, Any]]:
        """Load user mappings from the JSON file."""
        if cls._mappings is None:
            try:
                with open(cls._mapping_file, "r", encoding="utf-8") as json_file:
                    cls._mappings = json.load(json_file)
                logger.info("User mappings loaded successfully.")
            except (json.JSONDecodeError, FileNotFoundError) as err:
                logger.error(f"Failed to load or decode JSON: {err}")
                cls._mappings = []
        return cls._mappings

    @classmethod
    def save_user_mappings(cls, data: List[Dict[str, Any]]) -> None:
        """Save user mappings to the JSON file."""
        try:
            with open(cls._mapping_file, "w", encoding="utf-8") as json_file:
                json.dump(data, json_file, indent=4)
            cls._mappings = data
            cls.load_user_mappings.cache_clear()  # Invalidate the cache
            logger.info("User mappings saved and cache cleared.")
        except Exception as e:
            logger.exception(f"Failed to save user mappings: {e}")

    @classmethod
    def get_mapping_by_discord_id(cls, discord_id: str) -> Dict[str, Any]:
        """Get the mapping for a given Discord ID."""
        mappings = cls.load_user_mappings()
        return next((m for m in mappings if str(m.get("discord_id")) == discord_id), None)

    @classmethod
    def get_mapping_by_plex_username(cls, plex_username: str) -> Dict[str, Any]:
        """Get the mapping for a given Plex username."""
        mappings = cls.load_user_mappings()
        return next((m for m in mappings if m.get("plex_username") == plex_username), None)


def format_duration(seconds: int) -> str:
    """Format a number of seconds into a compact duration like '2d 5h 30m'.

    Days/hours are omitted when zero; minutes are always shown when nothing else
    is (so a sub-minute value renders as '0m'). This is the canonical duration
    formatter used across the bot.
    """
    days, remainder = divmod(int(seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def get_git_revision_short_hash() -> str:
    """Get the current git commit short hash."""
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode("ascii").strip()
    except Exception as e:
        logger.error(f"Failed to get git revision: {e}")
        return "unknown"


def get_git_revision_short_hash_latest() -> str:
    """Get the latest git commit short hash from origin."""
    try:
        subprocess.check_call(["git", "fetch"])
        return (
            subprocess.check_output(["git", "rev-parse", "--short", "origin/HEAD"]).decode("ascii").strip()
        )
    except Exception as e:
        logger.error(f"Failed to get latest git revision: {e}")
        return "unknown"


async def fetch_plex_image(
    tautulli_ip: str, thumb_key: str, width: int = 300, height: int = 450,
    use_https: bool = False, api_key: str = ""
) -> Optional[BytesIO]:
    """
    Fetch an image from the Plex/Tautulli server.

    Args:
        tautulli_ip: The IP address of the Tautulli server
        thumb_key: The thumbnail key from Plex
        width: The desired width of the image
        height: The desired height of the image
        use_https: Whether to use HTTPS
        api_key: Tautulli API key (required for authenticated image proxy)

    Returns:
        BytesIO object containing the image data, or None if the fetch failed
    """
    if not thumb_key or not thumb_key.strip():
        return None

    try:
        protocol = "https" if use_https else "http"
        url = f"{protocol}://{tautulli_ip}/api/v2"
        params = {
            "cmd": "pms_image_proxy",
            "img": thumb_key.strip(),
            "width": width,
            "height": height,
            "fallback": "poster",
        }
        if api_key:
            params["apikey"] = api_key

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status == 200 and "image" in (response.content_type or ""):
                    return BytesIO(await response.read())
                else:
                    logger.warning(f"Failed to fetch image: status={response.status}, content_type={response.content_type}")
                    return None
    except Exception as e:
        logger.error(f"Error fetching image: {e}", exc_info=True)
        return None


async def prepare_thumbnail_for_embed(
    tautulli_ip: str, thumb_key: str, width: int = 300, height: int = 450,
    use_https: bool = False, api_key: str = ""
) -> Tuple[Optional[File], Optional[str]]:
    """
    Prepares a thumbnail for inclusion in a Discord embed.

    Args:
        tautulli_ip: The IP address of the Tautulli server
        thumb_key: The thumbnail key from Plex
        width: The desired width of the image
        height: The desired height of the image
        use_https: Whether to use HTTPS for the image request
        api_key: Tautulli API key for authenticated access

    Returns:
        A tuple containing (File, attachment_url) or (None, None) if preparation failed
    """
    if not thumb_key:
        return None, None

    image_data = await fetch_plex_image(tautulli_ip, thumb_key, width, height, use_https=use_https, api_key=api_key)
    if image_data:
        file = File(fp=image_data, filename="image.jpg")
        attachment_url = "attachment://image.jpg"
        return file, attachment_url
    return None, None


class NoStopButtonMenuPages(menus.ButtonMenuPages, inherit_buttons=False):
    def __init__(self, source, timeout=60) -> None:
        super().__init__(source, timeout=timeout)
        # Add the buttons we want
        self.add_item(menus.MenuPaginationButton(emoji=self.PREVIOUS_PAGE))
        self.add_item(menus.MenuPaginationButton(emoji=self.NEXT_PAGE))
        # Disable buttons that are unavailable to be pressed at the start
        self._disable_unavailable_buttons()
