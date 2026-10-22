"""config.json and PLEXBOT_* environment variables, read once at startup, with a default for every setting."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("plexbot.config")


class ConfigSection:
    """A section of configuration values: key -> (default, description, required)."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._values: Dict[str, Tuple[Any, str, bool]] = {}

    def add(self, key: str, default: Any, description: str, required: bool = False) -> None:
        self._values[key] = (default, description, required)

    def get_required_keys(self) -> List[str]:
        return [key for key, (_default, _description, required) in self._values.items() if required]


class Config:
    """Centralized configuration manager for PlexBot."""

    def __init__(self):
        self._sections: Dict[str, ConfigSection] = {}
        self._user_config: Dict[str, Dict[str, Any]] = {}
        self._initialized = False

    def add_section(self, name: str, description: str) -> ConfigSection:
        section = ConfigSection(name, description)
        self._sections[name] = section
        return section

    def initialize(self, config_file: str = "config.json") -> bool:
        """Load the user configuration and check the required values are there.

        Returns True if initialization was successful, False otherwise.
        """
        if self._initialized:
            return True

        config_path = Path(config_file)
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    self._user_config = json.load(f)
                logger.info(f"Configuration loaded from {config_file}")
            except Exception as e:
                logger.error(f"Failed to load configuration from {config_file}: {e}")
                self._user_config = {}
                return False
        else:
            logger.info(f"No {config_file}; using environment variables and defaults")

        self._apply_environment(os.environ)

        missing_required = [
            f"{section_name}.{key} ({env_name(section_name, key)})"
            for section_name, section in self._sections.items()
            for key in section.get_required_keys()
            if key not in self._user_config.get(section_name, {})
        ]
        if missing_required:
            logger.error(f"Missing required configuration: {', '.join(missing_required)}")
            return False

        self._initialized = True
        return True

    def _apply_environment(self, environ) -> None:
        """PLEXBOT_<SECTION>_<KEY> variables override config.json. Empty ones count as unset."""
        known = set()
        used = []
        for section_name, section in self._sections.items():
            for key, (default, _description, _required) in section._values.items():
                name = env_name(section_name, key)
                known.add(name)
                raw = environ.get(name, "").strip()
                if not raw:
                    continue
                try:
                    value = parse_env_value(raw, default)
                except ValueError:
                    logger.error(f"Ignoring {name}: {raw!r} isn't a valid {type(default).__name__}")
                    continue
                self._user_config.setdefault(section_name, {})[key] = value
                used.append(name)
        if used:
            logger.info(f"Settings from the environment: {', '.join(used)}")  # names only: values hold secrets
        for name in sorted(n for n in environ if n.upper().startswith("PLEXBOT_") and n.upper() not in known):
            logger.warning(f"Unknown setting {name} ignored; check the spelling")

    def get(self, section: str, key: str) -> Any:
        """The user's value, else the registered default. A setting that isn't registered raises KeyError."""
        default = self._sections[section]._values[key][0]
        return self._user_config.get(section, {}).get(key, default)


def env_name(section: str, key: str) -> str:
    return f"PLEXBOT_{section}_{key}".upper()


def parse_env_value(raw: str, default: Any) -> Any:
    """Read an environment string as the type of the setting's default. Raises ValueError."""
    if isinstance(default, bool):
        lowered = raw.lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise ValueError(raw)
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            if raw.startswith("#") or raw.lower().startswith("0x"):
                return int(raw.lstrip("#"), 16)  # hex colours such as "#E5A00D"
            raise
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return [item.strip() for item in raw.split(",") if item.strip()]  # IDs; read as digit strings
    return raw


# Create a singleton instance
config = Config()


def setup_default_config():
    """Register every setting the bot reads, with its default."""
    core = config.add_section("core", "Core bot settings")
    core.add("token", "", "Discord bot token", required=True)
    core.add("prefix", "plex ", "Prefix of the old text commands, so they can point at their slash command")
    core.add("legacy_text_commands", False,
             "Answer old prefix commands with their slash name; needs Discord's Message Content intent")
    core.add("log_level", "INFO", "Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")

    tautulli = config.add_section("tautulli", "Tautulli API settings")
    tautulli.add("ip", "localhost:8181", "Tautulli server IP and port", required=True)
    tautulli.add("apikey", "", "Tautulli API key", required=True)
    tautulli.add("use_https", False, "Use HTTPS for Tautulli API connections")

    discord = config.add_section("discord", "Discord server settings")
    discord.add("server_id", "", "Discord server ID")
    discord.add(
        "admin_ids",
        [],
        "Discord user or role IDs allowed to use /plexadmin and stop streams, from any server",
    )
    discord.add("top_role_id", 0, "Role ID for top Plex user")
    discord.add("second_role_id", 0, "Role ID for second Plex user")
    discord.add("third_role_id", 0, "Role ID for third Plex user")
    discord.add(
        "stream_manager_ids",
        [],
        "Discord user or role IDs allowed to stop streams via /plex watchers (bot admins always can)",
    )
    discord.add(
        "show_recent_viewers",
        False,
        "When nobody's watching, list the last viewers and what they watched on /plex watchers (public)",
    )

    qbit = config.add_section("qbittorrent", "qBittorrent settings")
    qbit.add("ip", "localhost", "qBittorrent WebUI IP")
    qbit.add("port", "8080", "qBittorrent WebUI port")
    qbit.add("username", "", "qBittorrent WebUI username; blank when qBittorrent asks for no login")
    qbit.add("password", "", "qBittorrent WebUI password")

    # Work in progress: the TMDB client is created when a key is set, but no command uses it yet.
    tmdb = config.add_section("tmdb", "TMDB API settings")
    tmdb.add("apikey", "", "TMDB API key")

    ui = config.add_section("ui", "User interface settings")
    ui.add("plex_embed_color", 0xE5A00D, 'Embed color: a number (15040013) or hex text ("#E5A00D")')

    cache = config.add_section("cache", "Cache settings")
    cache.add("update_interval", 3600, "Cache update interval in seconds (default 1 hour)")
    cache.add("media_cache_path", "cache/media_cache.json", "Path to media cache file")

    defaults = config.add_section("defaults", "Default values for commands")
    defaults.add("recent_count", 10, "Default number of recent items to show")
    defaults.add("time_range", 30, "Default time range in days")

    charts = config.add_section("charts", "Chart and visualization settings")
    charts.add("width", 7, "Chart width in inches")
    charts.add("height", 3.5, "Chart height in inches")
    charts.add("dpi", 200, "Chart DPI")

    commands = config.add_section("commands", "Command-specific settings")
    commands.add("recommendation_timeout", 180, "Timeout in seconds for recommendation buttons")


setup_default_config()
