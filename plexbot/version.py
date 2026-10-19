"""The bot's version, from its git tag: baked into the Docker image, or `git describe` in a checkout."""

import logging
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger("plexbot.version")

RELEASES_URL = "https://api.github.com/repos/brah/plex-bot/releases/latest"
_REPO_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=None)
def bot_version() -> str:
    """"1.2.0" in a published image, "1.2.0-3-gabc1234" three commits past it, "unknown" without git."""
    if os.environ.get("BOT_VERSION"):
        return os.environ["BOT_VERSION"]
    try:
        described = subprocess.check_output(
            ["git", "describe", "--tags", "--always"], cwd=_REPO_ROOT, stderr=subprocess.DEVNULL, timeout=10
        )
    except Exception as e:
        logger.debug(f"Couldn't read the version from git: {e}")
        return "unknown"
    return described.decode("ascii").strip().removeprefix("v")


def _release(text: Optional[str]) -> Optional[tuple]:
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(latest: Optional[str], current: str) -> bool:
    """Whether `latest` is a later release than `current`. Anything that isn't a version never is."""
    latest_release, current_release = _release(latest), _release(current)
    return bool(latest_release and current_release and latest_release > current_release)


async def latest_release() -> Optional[str]:
    """The newest release on GitHub, or None if GitHub can't say right now."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(RELEASES_URL, headers={"Accept": "application/vnd.github+json"}) as response:
                if response.status != 200:
                    logger.debug(f"GitHub answered {response.status} for the latest release")
                    return None
                return (await response.json())["tag_name"].removeprefix("v")
    except Exception as e:
        logger.debug(f"Couldn't check GitHub for a newer release: {e}")
        return None
