# utilities.py

import asyncio
import json
import logging
import subprocess
from functools import lru_cache
from io import BytesIO
from typing import List, Dict, Any

import aiohttp
import nextcord
from nextcord.ext import menus
from nextcord import File

logger = logging.getLogger('plexbot.utilities')
logger.setLevel(logging.INFO)

class Config:
    _config_data = None

    @classmethod
    def load_config(cls, filename: str = "config.json") -> Dict[str, Any]:
        """Load the configuration data from a JSON file."""
        if cls._config_data is None:
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    cls._config_data = json.load(f)
                logger.info("Configuration loaded successfully.")
            except Exception as e:
                logger.exception("Failed to load configuration.")
                cls._config_data = {}
        return cls._config_data

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        config = cls.load_config()
        return config.get(key, default)

    @classmethod
    def save_config(cls, data: Dict[str, Any], filename: str = "config.json") -> None:
        """Save the configuration data to a JSON file."""
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            cls._config_data = data
            logger.info("Configuration saved successfully.")
        except Exception as e:
            logger.exception("Failed to save configuration.")

    @classmethod
    def reload_config(cls, filename: str = "config.json") -> Dict[str, Any]:
        """Reload the configuration data from the JSON file."""
        cls._config_data = None
        return cls.load_config(filename)

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


def days_hours_minutes(seconds: int) -> str:
    """Converts seconds to days, hours, minutes."""
    if not isinstance(seconds, int):
        raise TypeError("Seconds must be an integer.")

    if seconds < 0:
        raise ValueError("Seconds must be non-negative.")

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} {'day' if days == 1 else 'days'}")
    if hours > 0:
        parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
    if minutes > 0:
        parts.append(f"{minutes} {'minute' if minutes == 1 else 'minutes'}")

    return ", ".join(parts) if parts else "0 minutes"


def get_git_revision_short_hash() -> str:
    """Get the current git commit short hash."""
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
            .decode("ascii")
            .strip()
        )
    except Exception as e:
        logger.error(f"Failed to get git revision: {e}")
        return "unknown"


def get_git_revision_short_hash_latest() -> str:
    """Get the latest git commit short hash from origin."""
    try:
        subprocess.check_call(["git", "fetch"])
        return (
            subprocess.check_output(["git", "rev-parse", "--short", "origin/HEAD"])
            .decode("ascii")
            .strip()
        )
    except Exception as e:
        logger.error(f"Failed to get latest git revision: {e}")
        return "unknown"


class NoStopButtonMenuPages(menus.ButtonMenuPages, inherit_buttons=False):
    def __init__(self, source, timeout=60) -> None:
        super().__init__(source, timeout=timeout)
        # Add the buttons we want
        self.add_item(menus.MenuPaginationButton(emoji=self.PREVIOUS_PAGE))
        self.add_item(menus.MenuPaginationButton(emoji=self.NEXT_PAGE))
        # Disable buttons that are unavailable to be pressed at the start
        self._disable_unavailable_buttons()


class MyEmbedDescriptionPageSource(menus.ListPageSource):
    def __init__(self, data, tautulli_ip):
        super().__init__(data, per_page=2)
        self.tautulli_ip = tautulli_ip

    async def fetch_image(self, url: str) -> BytesIO:
        """Fetch image from a URL."""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return BytesIO(await response.read())
                return None

    async def format_page(self, menu, entries):
        embed = nextcord.Embed(title="Recently Added", color=0xE5A00D)
        embed.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")

        for entry in entries:
            embed.add_field(name="\u200b", value=entry["description"], inline=False)
            thumb_key = entry.get("thumb_key", "")
            if thumb_key:
                thumb_url = f"http://{self.tautulli_ip}/pms_image_proxy?img={thumb_key}&width=200&height=400&fallback=poster"
                image_data = await self.fetch_image(thumb_url)
                if image_data:
                    file = File(fp=image_data, filename="image.jpg")
                    embed.set_image(url="attachment://image.jpg")
                    return {"embed": embed, "file": file}

        return embed
