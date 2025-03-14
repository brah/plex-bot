#!/usr/bin/env python3
# migrate_config.py
#
# One-time migration script to convert from the old configuration format to the new one.
# Usage: python migrate_config.py [--force] [--no-backup]

import argparse
import importlib.util
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("migrate_config")

def create_backup(file_path, backup_dir=None):
    """Create a backup of a file before modifying it."""
    file_path = Path(file_path)
    if not file_path.exists():
        logger.warning(f"File does not exist: {file_path}")
        return False

    if backup_dir:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(exist_ok=True, parents=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{file_path.name}.{timestamp}"
    else:
        backup_path = file_path.with_suffix(f"{file_path.suffix}.bak")
    
    try:
        shutil.copy2(file_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return False

def load_legacy_json_config(config_file="config.json"):
    """Load the legacy JSON configuration file."""
    config_path = Path(config_file)
    if not config_path.exists():
        logger.warning(f"Legacy config file not found: {config_file}")
        return {}
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse {config_file}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error reading {config_file}: {e}")
        return {}

def load_bot_config_module(bot_config_file="bot_config.py"):
    """Load the BotConfig class from the legacy bot_config.py file."""
    bot_config_path = Path(bot_config_file)
    if not bot_config_path.exists():
        logger.warning(f"Legacy bot config file not found: {bot_config_file}")
        return None
    
    try:
        spec = importlib.util.spec_from_file_location("bot_config", bot_config_path)
        bot_config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot_config_module)
        
        if hasattr(bot_config_module, "BotConfig"):
            return bot_config_module.BotConfig
        else:
            logger.warning("BotConfig class not found in bot_config.py")
            return None
    except Exception as e:
        logger.error(f"Error loading bot_config.py: {e}")
        return None

def migrate_config(legacy_json_config, bot_config_class, output_file="config.json"):
    """Migrate from the old configuration format to the new one."""
    # Initialize the new configuration structure
    new_config = {
        "core": {
            "token": legacy_json_config.get("token", ""),
            "prefix": "plex ",
            "log_level": "INFO",
        },
        "tautulli": {
            "ip": legacy_json_config.get("tautulli_ip", "localhost:8181"),
            "apikey": legacy_json_config.get("tautulli_apikey", ""),
        },
        "discord": {
            "server_id": legacy_json_config.get("server_id", ""),
            "top_role_id": legacy_json_config.get("plex_top", 0),
            "second_role_id": legacy_json_config.get("plex_two", 0),
            "third_role_id": legacy_json_config.get("plex_three", 0),
        },
        "qbittorrent": {
            "ip": legacy_json_config.get("qbit_ip", "localhost"),
            "port": legacy_json_config.get("qbit_port", "8080"),
            "username": legacy_json_config.get("qbit_username", ""),
            "password": legacy_json_config.get("qbit_password", ""),
        },
        "tmdb": {
            "apikey": legacy_json_config.get("tmdb_apikey", ""),
        },
        "defaults": {
            "stats_duration": legacy_json_config.get("default_duration", 7),
        }
    }
    
    # Migrate data from bot_config if available
    if bot_config_class:
        # UI settings
        new_config["ui"] = {
            "plex_embed_color": getattr(bot_config_class, "PLEX_EMBED_COLOR", 0xE5A00D),
            "plex_image": getattr(bot_config_class, "PLEX_IMAGE", "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"),
            "qbit_embed_color": getattr(bot_config_class, "QBIT_EMBED_COLOR", 0x6C81DF),
            "qbit_image": getattr(bot_config_class, "QBIT_IMAGE", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/New_qBittorrent_Logo.svg/1200px-New_qBittorrent_Logo.svg.png"),
        }
        
        # Cache settings
        new_config["cache"] = {
            "update_interval": getattr(bot_config_class, "CACHE_UPDATE_INTERVAL", 3600),
            "media_cache_path": getattr(bot_config_class, "MEDIA_CACHE_PATH", "cache/media_cache.json"),
        }
        
        # Default values
        new_config["defaults"].update({
            "recent_count": getattr(bot_config_class, "DEFAULT_RECENT_COUNT", 10),
            "history_length": getattr(bot_config_class, "DEFAULT_HISTORY_LENGTH", 10000),
            "time_range": getattr(bot_config_class, "DEFAULT_TIME_RANGE", 30),
        })
        
        # Chart settings
        new_config["charts"] = {
            "width": getattr(bot_config_class, "CHART_WIDTH", 14),
            "height": getattr(bot_config_class, "CHART_HEIGHT", 6),
            "dpi": getattr(bot_config_class, "CHART_DPI", 100),
            "date_format": getattr(bot_config_class, "CHART_DATE_FORMAT", "%Y-%m-%d"),
            "month_format": getattr(bot_config_class, "CHART_MONTH_FORMAT", "%b %Y"),
        }
        
        # Command settings
        new_config["commands"] = {
            "recommendation_timeout": getattr(bot_config_class, "RECOMMENDATION_TIMEOUT", 180),
        }
        
        # API settings
        new_config["api"] = {
            "max_concurrent_requests": getattr(bot_config_class, "MAX_CONCURRENT_REQUESTS", 10),
            "request_timeout": getattr(bot_config_class, "API_REQUEST_TIMEOUT", 30),
            "retry_limit": getattr(bot_config_class, "API_RETRY_LIMIT", 3),
        }
        
        # Media types mapping
        if hasattr(bot_config_class, "MEDIA_TYPE_MAPPING"):
            new_config["media_types"] = bot_config_class.MEDIA_TYPE_MAPPING
            
        # Visualization colors
        new_config["colors"] = {
            "plex_orange": getattr(bot_config_class, "PLEX_ORANGE", "#E5A00D"),
            "plex_grey_dark": getattr(bot_config_class, "PLEX_GREY_DARK", "#1B1B1B"),
        }
        
        if hasattr(bot_config_class, "PLEX_COLORS"):
            new_config["colors"]["media_types"] = bot_config_class.PLEX_COLORS
    
    # Save the new configuration
    output_path = Path(output_file)
    output_path.parent.mkdir(exist_ok=True, parents=True)
    
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=4)
        logger.info(f"Migrated configuration saved to {output_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to save migrated configuration: {e}")
        return False

def check_path_exists(path, create=False):
    """Check if a path exists and optionally create it."""
    path = Path(path)
    exists = path.exists()
    
    if not exists and create:
        try:
            if path.suffix:  # It's a file
                path.parent.mkdir(exist_ok=True, parents=True)
            else:  # It's a directory
                path.mkdir(exist_ok=True, parents=True)
            logger.info(f"Created {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to create {path}: {e}")
            return False
    
    return exists

def main():
    parser = argparse.ArgumentParser(description="Migrate from old configuration format to new format")
    parser.add_argument("--force", action="store_true", help="Overwrite existing config file")
    parser.add_argument("--no-backup", action="store_true", help="Skip creating backups")
    parser.add_argument("--output", default="config.json", help="Output file path")
    parser.add_argument("--backup-dir", default="config_backups", help="Directory for backups")
    args = parser.parse_args()
    
    # Check if output file already exists
    output_file = Path(args.output)
    if output_file.exists() and not args.force:
        logger.error(f"Output file {args.output} already exists. Use --force to overwrite.")
        return 1
    
    # Create backup directory
    if not args.no_backup:
        backup_dir = Path(args.backup_dir)
        check_path_exists(backup_dir, create=True)
    
    # Create backups of original files
    legacy_files = ["config.json", "bot_config.py"]
    for file_path in legacy_files:
        if Path(file_path).exists() and not args.no_backup:
            create_backup(file_path, args.backup_dir)
    
    # Load legacy configuration
    legacy_json_config = load_legacy_json_config()
    bot_config_class = load_bot_config_module()
    
    if not legacy_json_config and not bot_config_class:
        logger.error("No legacy configuration found. Make sure at least one of config.json or bot_config.py exists.")
        return 1
    
    # Migrate configuration
    success = migrate_config(legacy_json_config, bot_config_class, args.output)
    
    if success:
        logger.info("Configuration migration completed successfully.")
        return 0
    else:
        logger.error("Configuration migration failed.")
        return 1

if __name__ == "__main__":
    sys.exit(main())