# utilities.py

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import aiohttp
from nextcord import File

logger = logging.getLogger("plexbot.utilities")
logger.setLevel(logging.INFO)


# Bot configuration lives in the `config` package (config/__init__.py).
# The old utilities.Config JSON reader was removed as dead code.


class MappingsUnavailable(Exception):
    """map.json exists but couldn't be read, so saving over it would lose every mapping."""


def normalise_mappings(entries) -> List[Dict[str, Any]]:
    """One entry per Plex username, Discord ids as strings, ignore as a bool.

    Old versions could write two entries for one Plex user (an ignore-only entry, then a
    link), and lookups found only the first; merging heals that. Entries that neither link
    nor ignore anyone are dropped, and unknown keys are kept.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        plex = str(entry.get("plex_username") or "").strip()
        if not plex:
            continue
        target = merged.setdefault(plex, {"plex_username": plex, "discord_id": "", "ignore": False})
        discord_id = str(entry.get("discord_id") or "").strip()
        if discord_id and not target["discord_id"]:
            target["discord_id"] = discord_id
        target["ignore"] = target["ignore"] or bool(entry.get("ignore", False))
        for key, value in entry.items():
            target.setdefault(key, value)
    return [m for m in merged.values() if m["discord_id"] or m["ignore"]]


@dataclass
class LinkResult:
    plex_username: str
    discord_id: str
    replaced_discord_id: Optional[str] = None  # who that Plex user was linked to before
    moved_from_plex: Optional[str] = None  # the Plex user that Discord account was linked to before


class UserMappings:
    """Plex user <-> Discord member links and ignored Plex users, stored in map.json.

    Each Plex user links to at most one Discord member and each member to at most one Plex user.
    """

    _mappings: Optional[List[Dict[str, Any]]] = None
    _mapping_file = "map.json"
    _unreadable = False  # the file exists but couldn't be read; saving is refused

    @classmethod
    def _path(cls) -> Path:
        return Path(cls._mapping_file)

    @classmethod
    def load_user_mappings(cls) -> List[Dict[str, Any]]:
        """The mappings (read once, then kept in memory)."""
        if cls._mappings is None:
            cls._unreadable = False
            try:
                raw = json.loads(cls._path().read_text(encoding="utf-8"))
                if not isinstance(raw, list):
                    raise ValueError("expected a JSON list")
                cls._mappings = normalise_mappings(raw)
                logger.info("User mappings loaded successfully.")
            except FileNotFoundError:
                cls._mappings = []
            except (OSError, ValueError) as err:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
                logger.error(f"Couldn't read {cls._mapping_file}; links and ignores are off until it's fixed: {err}")
                cls._unreadable = True
                cls._mappings = []
        return cls._mappings

    @classmethod
    def is_unreadable(cls) -> bool:
        cls.load_user_mappings()
        return cls._unreadable

    @classmethod
    def save_user_mappings(cls, data: List[Dict[str, Any]]) -> None:
        """Write the mappings. Raises MappingsUnavailable or OSError; never wipes an unreadable file.

        Writes a temporary file and swaps it in, so a crash can't leave half a file. A Docker
        single-file bind mount can't be swapped, so then it writes in place after a .bak copy.
        """
        if cls.is_unreadable():
            raise MappingsUnavailable(f"{cls._mapping_file} couldn't be read")
        data = normalise_mappings(data)
        text = json.dumps(data, indent=4)
        path = cls._path()
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        try:
            os.replace(temporary, path)
        except OSError:
            if path.exists():
                shutil.copyfile(path, path.with_name(path.name + ".bak"))
            path.write_text(text, encoding="utf-8")
            temporary.unlink(missing_ok=True)
        cls._mappings = data
        logger.info("User mappings saved.")

    @classmethod
    def reload(cls) -> None:
        """Forget what's in memory; the next read loads map.json again."""
        cls._mappings = None

    @classmethod
    def get_mapping_by_discord_id(cls, discord_id: str) -> Dict[str, Any]:
        """Get the mapping for a given Discord ID."""
        mappings = cls.load_user_mappings()
        return next((m for m in mappings if str(m.get("discord_id")) == str(discord_id)), None)

    @classmethod
    def get_mapping_by_plex_username(cls, plex_username: str) -> Dict[str, Any]:
        """Get the mapping for a given Plex username."""
        mappings = cls.load_user_mappings()
        return next((m for m in mappings if m.get("plex_username") == plex_username), None)

    @classmethod
    def _copy(cls) -> List[Dict[str, Any]]:
        return [dict(m) for m in cls.load_user_mappings()]

    @staticmethod
    def _entry(data: list, plex_username: str) -> Dict[str, Any]:
        entry = next((m for m in data if m["plex_username"] == plex_username), None)
        if entry is None:
            entry = {"plex_username": plex_username, "discord_id": "", "ignore": False}
            data.append(entry)
        return entry

    @classmethod
    def link(cls, plex_username: str, discord_id) -> LinkResult:
        """Link a Plex user to a Discord member, replacing either side's previous link."""
        discord_id = str(discord_id)
        data = cls._copy()
        result = LinkResult(plex_username, discord_id)
        for entry in data:
            if entry["discord_id"] == discord_id and entry["plex_username"] != plex_username:
                result.moved_from_plex = entry["plex_username"]
                entry["discord_id"] = ""
        entry = cls._entry(data, plex_username)
        if entry["discord_id"] and entry["discord_id"] != discord_id:
            result.replaced_discord_id = entry["discord_id"]
        entry["discord_id"] = discord_id
        cls.save_user_mappings(data)
        return result

    @classmethod
    def unlink(cls, plex_username: str) -> Optional[str]:
        """Remove a Plex user's link; returns the Discord id it had, or None if it had none."""
        data = cls._copy()
        entry = next((m for m in data if m["plex_username"] == plex_username and m["discord_id"]), None)
        if entry is None:
            return None
        previous, entry["discord_id"] = entry["discord_id"], ""
        cls.save_user_mappings(data)
        return previous

    @classmethod
    def toggle_ignore(cls, plex_username: str) -> bool:
        """Flip whether a Plex user is ignored; returns the new state. Their link is kept."""
        data = cls._copy()
        entry = cls._entry(data, plex_username)
        entry["ignore"] = not entry["ignore"]
        cls.save_user_mappings(data)
        return entry["ignore"]


# --- Plex viewers as the Discord server knows them ---------------------------
# Tautulli names the Plex username "username" in sessions and user stats, but "user" in
# history and home-stats rows; these helpers accept either.


def plex_username(row: Dict[str, Any]) -> Optional[str]:
    return row.get("username") or row.get("user")


def ignored_usernames() -> set:
    """Plex users an admin marked as ignored (plex ignore): left out of stats and lists."""
    return {m["plex_username"] for m in UserMappings.load_user_mappings() if m.get("ignore", False)}


def mapped_discord_user(bot, username: Optional[str]):
    """The Discord user mapped to a Plex username, if the bot can see them."""
    mapping = UserMappings.get_mapping_by_plex_username(username) if username else None
    if not mapping or not mapping.get("discord_id"):
        return None
    try:
        return bot.get_user(int(mapping["discord_id"]))
    except (TypeError, ValueError):
        return None


def viewer_name(bot, row: Dict[str, Any]) -> str:
    """Their Discord name when mapped (who people recognise here), else their Plex name."""
    member = mapped_discord_user(bot, plex_username(row))
    if member:
        return member.display_name
    return row.get("friendly_name") or plex_username(row) or "Unknown"


def viewer_avatar(bot, row: Dict[str, Any]) -> Optional[str]:
    """Their Discord avatar when mapped, else their Plex one."""
    member = mapped_discord_user(bot, plex_username(row))
    if member:
        return member.display_avatar.url
    thumb = row.get("user_thumb") or row.get("thumb") or ""
    return thumb if thumb.startswith("http") else None


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
    use_https: bool = False, api_key: str = "", fallback: Optional[str] = "poster"
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
        fallback: Image Tautulli substitutes when the real one is missing (None to fail instead)

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
        }
        # Tautulli substitutes a generic image when the real one is missing; callers that
        # want "no image" instead (e.g. a stream's current frame) pass fallback=None.
        if fallback:
            params["fallback"] = fallback
        if api_key:
            params["apikey"] = api_key

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
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
