# config/__init__.py

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union, List, TypeVar, Generic

# Configure logging
logger = logging.getLogger("plexbot.config")
logger.setLevel(logging.INFO)

T = TypeVar('T')

class ConfigValue(Generic[T]):
    """A configuration value with a default and description."""
    def __init__(self, default: T, description: str, required: bool = False):
        self.default = default
        self.description = description
        self.required = required
        
    def __str__(self) -> str:
        return str(self.default)

class ConfigSection:
    """A section of configuration values."""
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._values: Dict[str, ConfigValue] = {}
        
    def add(self, key: str, default: Any, description: str, required: bool = False) -> None:
        """Add a configuration value to this section."""
        self._values[key] = ConfigValue(default, description, required)
        
    def get_defaults(self) -> Dict[str, Any]:
        """Get a dictionary of default values for this section."""
        return {key: value.default for key, value in self._values.items()}
    
    def get_required_keys(self) -> List[str]:
        """Get a list of required configuration keys in this section."""
        return [key for key, value in self._values.items() if value.required]

class Config:
    """Centralized configuration manager for PlexBot."""
    def __init__(self):
        self._sections: Dict[str, ConfigSection] = {}
        self._user_config: Dict[str, Dict[str, Any]] = {}
        self._initialized = False
        
    def add_section(self, name: str, description: str) -> ConfigSection:
        """Add a new configuration section."""
        section = ConfigSection(name, description)
        self._sections[name] = section
        return section
        
    def initialize(self, config_file: str = "config.json") -> bool:
        """
        Initialize the configuration system.
        
        This loads the user configuration from file and validates required values.
        Returns True if initialization was successful, False otherwise.
        """
        if self._initialized:
            return True
            
        # Ensure config directory exists
        config_dir = Path("config")
        config_dir.mkdir(exist_ok=True)
            
        # Load user configuration
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
            logger.warning(f"Configuration file {config_file} not found, using defaults")
            
        # Validate required configuration
        missing_required = []
        for section_name, section in self._sections.items():
            for key in section.get_required_keys():
                if section_name not in self._user_config or key not in self._user_config.get(section_name, {}):
                    missing_required.append(f"{section_name}.{key}")
                    
        if missing_required:
            logger.error(f"Missing required configuration: {', '.join(missing_required)}")
            return False
            
        self._initialized = True
        return True
        
    def get(self, section: str, key: str, default: Optional[T] = None) -> T:
        """Get a configuration value."""
        if section in self._sections and key in self._sections[section]._values:
            section_default = self._sections[section]._values[key].default
            actual_default = default if default is not None else section_default
        else:
            actual_default = default
            
        section_values = self._user_config.get(section, {})
        return section_values.get(key, actual_default)
        
    def save(self, config_file: str = "config.json") -> bool:
        """Save the current configuration to file."""
        try:
            # Create a dict that merges defaults with user config
            config_to_save = {}
            for section_name, section in self._sections.items():
                # Start with defaults
                section_config = section.get_defaults()
                # Update with user values
                if section_name in self._user_config:
                    section_config.update(self._user_config[section_name])
                config_to_save[section_name] = section_config
                
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config_to_save, f, indent=4)
            logger.info(f"Configuration saved to {config_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save configuration to {config_file}: {e}")
            return False
            
    def set(self, section: str, key: str, value: Any) -> None:
        """Set a configuration value."""
        if section not in self._user_config:
            self._user_config[section] = {}
        self._user_config[section][key] = value
        
    def get_section(self, section: str) -> Dict[str, Any]:
        """Get all configuration values for a section."""
        # Start with defaults
        if section in self._sections:
            result = self._sections[section].get_defaults()
        else:
            result = {}
            
        # Update with user values
        if section in self._user_config:
            result.update(self._user_config[section])
            
        return result
        
    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Get all configuration values."""
        result = {}
        for section_name, section in self._sections.items():
            result[section_name] = self.get_section(section_name)
        return result
        
    def generate_default_config(self, file_path: str = "config.default.json") -> bool:
        """Generate a default configuration file with comments."""
        try:
            default_config = {}
            for section_name, section in self._sections.items():
                default_config[section_name] = {
                    "_description": section.description,
                    **{key: {"value": value.default, "description": value.description, "required": value.required}
                       for key, value in section._values.items()}
                }
                
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4)
            logger.info(f"Default configuration exported to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to export default configuration: {e}")
            return False


# Create a singleton instance
config = Config()

# Define configuration sections and values
def setup_default_config():
    """Set up the default configuration structure."""
    # Core bot settings
    core = config.add_section("core", "Core bot settings")
    core.add("token", "", "Discord bot token", required=True)
    core.add("prefix", "plex ", "Command prefix for the bot")
    core.add("log_level", "INFO", "Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    
    # Tautulli settings
    tautulli = config.add_section("tautulli", "Tautulli API settings")
    tautulli.add("ip", "localhost:8181", "Tautulli server IP and port", required=True)
    tautulli.add("apikey", "", "Tautulli API key", required=True)
    
    # Discord server settings
    discord = config.add_section("discord", "Discord server settings")
    discord.add("server_id", "", "Discord server ID")
    discord.add("top_role_id", 0, "Role ID for top Plex user")
    discord.add("second_role_id", 0, "Role ID for second Plex user")
    discord.add("third_role_id", 0, "Role ID for third Plex user")
    
    # qBittorrent settings
    qbit = config.add_section("qbittorrent", "qBittorrent settings")
    qbit.add("ip", "localhost", "qBittorrent WebUI IP")
    qbit.add("port", "8080", "qBittorrent WebUI port")
    qbit.add("username", "", "qBittorrent WebUI username")
    qbit.add("password", "", "qBittorrent WebUI password")
    
    # TMDB settings
    tmdb = config.add_section("tmdb", "TMDB API settings")
    tmdb.add("apikey", "", "TMDB API key")
    
    # UI settings
    ui = config.add_section("ui", "User interface settings")
    ui.add("plex_embed_color", 0xE5A00D, "Embed color for Plex embeds (hex)")
    ui.add("plex_image", "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png", "URL for Plex logo")
    ui.add("qbit_embed_color", 0x6C81DF, "Embed color for qBittorrent embeds (hex)")
    ui.add("qbit_image", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/New_qBittorrent_Logo.svg/1200px-New_qBittorrent_Logo.svg.png", "URL for qBittorrent logo")
    
    # Cache settings
    cache = config.add_section("cache", "Cache settings")
    cache.add("update_interval", 3600, "Cache update interval in seconds (default 1 hour)")
    cache.add("media_cache_path", "cache/media_cache.json", "Path to media cache file")
    
    # Default values for commands
    defaults = config.add_section("defaults", "Default values for commands")
    defaults.add("recent_count", 10, "Default number of recent items to show")
    defaults.add("stats_duration", 7, "Default duration in days for stats commands")
    defaults.add("history_length", 10000, "Default number of history items to fetch")
    defaults.add("time_range", 30, "Default time range in days")
    
    # Chart settings
    charts = config.add_section("charts", "Chart and visualization settings")
    charts.add("width", 14, "Chart width in inches")
    charts.add("height", 6, "Chart height in inches")
    charts.add("dpi", 100, "Chart DPI")
    charts.add("date_format", "%Y-%m-%d", "Date format for charts")
    charts.add("month_format", "%b %Y", "Month format for charts")
    
    # Command settings
    commands = config.add_section("commands", "Command-specific settings")
    commands.add("recommendation_timeout", 180, "Timeout in seconds for recommendation reactions")
    
    # API settings
    api = config.add_section("api", "API request settings")
    api.add("max_concurrent_requests", 10, "Maximum concurrent API requests")
    api.add("request_timeout", 30, "API request timeout in seconds")
    api.add("retry_limit", 3, "Number of times to retry failed API requests")
    
    # Media types mapping
    media_types = config.add_section("media_types", "Media types mapping")
    media_types.add("movie", ["movie"], "Media types considered as movies")
    media_types.add("tv", ["show", "episode"], "Media types considered as TV")
    media_types.add("any", ["movie", "show", "episode"], "All media types")
    
    # Visualization colors
    colors = config.add_section("colors", "Visualization colors")
    colors.add("plex_orange", "#E5A00D", "Plex orange color")
    colors.add("plex_grey_dark", "#1B1B1B", "Dark grey background")
    colors.add("media_types", {
        "Movie": "#E5A00D",
        "TV": "#F6E0B6",
        "Other": "#F3D38A",
        "Unknown": "#F0C75E"
    }, "Colors for different media types")

# Initialize the default configuration
setup_default_config()