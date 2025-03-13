# bot_config.py

"""
Centralized configuration for the PlexBot.

This module contains constants and configuration values used throughout the bot.
"""


class BotConfig:
    """Configuration values and constants for PlexBot."""

    # UI constants
    PLEX_EMBED_COLOR = 0xE5A00D
    PLEX_IMAGE = "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
    QBIT_EMBED_COLOR = 0x6C81DF
    QBIT_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/New_qBittorrent_Logo.svg/1200px-New_qBittorrent_Logo.svg.png"

    # Cache settings
    CACHE_UPDATE_INTERVAL = 3600  # 1 hour in seconds
    MEDIA_CACHE_PATH = "cache/media_cache.json"

    # Default values
    DEFAULT_RECENT_COUNT = 10
    DEFAULT_STATS_DURATION = 7
    DEFAULT_HISTORY_LENGTH = 10000
    DEFAULT_TIME_RANGE = 30  # Default time range in days

    # Command-specific settings
    RECOMMENDATION_TIMEOUT = 180  # 3 minutes for reaction-based selection

    # API request settings
    MAX_CONCURRENT_REQUESTS = 10  # Maximum concurrent API requests
    API_REQUEST_TIMEOUT = 30  # API request timeout in seconds
    API_RETRY_LIMIT = 3  # Number of times to retry failed API requests

    # Media types mapping
    MEDIA_TYPE_MAPPING = {
        "movie": ["movie"],
        "tv": ["show", "episode"],
        "any": ["movie", "show", "episode"],
    }
