# Plex-Bot

A Discord bot that interfaces with your Plex server through Tautulli's API, offering rich features like user statistics, media recommendations, live download tracking from qBittorrent, and more!

## Features

- **Top Plex Users**: Track and display the most active viewers with automatic Discord role assignments
- **Media Recommendations**: Get personalized recommendations based on watch history and genre preferences
- **Random Media Suggestions**: Discover content from your library with filtering by genre and media type
- **Live Activity**: See who's currently watching what on your Plex server
- **Download Tracking**: Monitor qBittorrent downloads in real time (optional)
- **Media Statistics**: Generate detailed viewing statistics with visual charts
- **Efficient Media Cache**: Optimized caching system for fast responses even with large libraries

## A note on AI

Being a hobby project, don't be surprised by the use of AI models in commits — I encourage and invite you to do the same! Use whatever tools help you build and contribute.

## Setup

### Requirements

- **Python 3.12+** (required by nextcord 3.2.0)
- A working **Tautulli** setup ([Tautulli GitHub](https://github.com/Tautulli/Tautulli))
- **Discord Bot Token** ([Discord Developer Portal](https://discord.com/developers/applications))
- Optional: **qBittorrent** for download tracking
- Optional: **TMDB API Key** for enhanced metadata

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/brah/plex-bot.git
   cd plex-bot
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Configure the bot:
   
   **Option 1: Migrate from an existing configuration**
   
   If you have a previous version of PlexBot with `config.json` and `bot_config.py` files, use the migration script:
   
   ```bash
   python migrate_config.py
   ```
   
   **Option 2: Create a new configuration**

   Copy the bundled template and fill in your values:

   ```bash
   cp config.example.json config.json
   ```

   The template mirrors this structure (everything not listed falls back to sensible defaults):

   ```json
   {
       "core": {
           "token": "<Your Discord Bot Token>",
           "prefix": "plex ",
           "log_level": "INFO"
       },
       "tautulli": {
           "ip": "localhost:8181",
           "apikey": "<Your Tautulli API Key>",
           "use_https": false
       },
       "discord": {
           "server_id": "<Your Discord Server ID>",
           "top_role_id": 123456789,
           "second_role_id": 123456789,
           "third_role_id": 123456789
       },
       "qbittorrent": {
           "ip": "localhost",
           "port": "8080",
           "username": "username",
           "password": "password"
       },
       "tmdb": {
           "apikey": "<Your TMDB API Key>"
       }
   }
   ```

   *Note: `top_role_id`, `second_role_id`, and `third_role_id` are role IDs which you've created to highlight the top 3 media watchers on your server.*

4. Run the bot:

   ```bash
   python plexbot.py
   ```

### Configuration System

PlexBot now uses a unified configuration system that organizes settings into logical sections:

- **core**: Basic bot settings (token, prefix)
- **tautulli**: Tautulli API connection settings
- **discord**: Discord server and role settings
- **qbittorrent**: qBittorrent connection settings
- **tmdb**: TMDB API settings
- **ui**: User interface customization
- **cache**: Media cache settings
- **defaults**: Default values for commands
- **charts**: Chart visualization settings
- **commands**: Command-specific settings
- **api**: API request settings
- **media_types**: Media type mappings
- **colors**: Visualization color schemes

#### Migrating to the New Configuration System

If you're upgrading from an older version of PlexBot, follow these steps:

1. **Backup your existing configuration**:
   The migration script will automatically create backups, but it's always good practice to make your own.

2. **Run the migration script**:
   ```bash
   python migrate_config.py
   ```

3. **Review the new configuration**:
   Check the generated `config.json` file to ensure all settings were correctly transferred.

4. **Additional options**:
   - `--force`: Override existing config.json
   - `--no-backup`: Skip creating backups
   - `--output FILE`: Specify output location
   - `--backup-dir DIR`: Specify backup directory

### Deployment

For 24/7 operation, pick one of the two supported options below. The bot is a
single long-running process with **no inbound ports**, and a Discord token allows
**one connection** — run exactly one instance. Because it talks to Tautulli (and
optionally qBittorrent) over your local network, it normally runs on the same
host/network as Tautulli.

#### Option A — Docker

Best if you already run a Dockerized media stack. Requires Docker with the
Compose plugin. A `Dockerfile` and `docker-compose.yml` are included.

1. Create your config and an empty mappings file in the repo directory:

   ```bash
   cp config.example.json config.json   # then edit it
   echo "[]" > map.json
   ```

2. Make Tautulli reachable from the container — set `tautulli.ip` in `config.json`
   accordingly (see the networking notes in `docker-compose.yml`). **Do not use
   `localhost`**; inside a container that points at the container itself. Use your
   host's LAN IP, a `host.docker.internal:8181` mapping, or the Tautulli service
   name if it shares this Docker network.

3. Build and start:

   ```bash
   docker compose up -d --build
   ```

4. Manage it:

   ```bash
   docker compose logs -f                 # follow logs
   git pull && docker compose up -d --build   # update
   ```

`config.json` and `map.json` stay on the host (mounted in); the media cache lives
in a named Docker volume. The container runs as UID:GID `1000:1000` — if your host
user differs, set `user:` in `docker-compose.yml` (or `chown 1000:1000 config.json
map.json`) so it can read/write them.

#### Option B — systemd

Best for a bare-metal or VM host. A ready-to-edit unit lives at
[`deploy/plexbot.service`](deploy/plexbot.service).

1. Install the bot (e.g. into `/opt/plex-bot`) with a virtualenv and deps, and
   create `config.json` (see [Installation](#installation)). Using a venv is
   important — the unit runs the bot via `venv/bin/python`, not the system
   `python3`, which won't have the pinned Python 3.12+ dependencies.

2. Install and enable the service (edit `User=` and the paths inside the unit
   first):

   ```bash
   sudo cp deploy/plexbot.service /etc/systemd/system/plexbot.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now plexbot
   ```

3. Logs: `journalctl -u plexbot -f`

#### Temporary (screen / tmux)

For quick testing only — these do **not** restart on reboot. Run `python
plexbot.py` inside a `screen -S plexbot` or `tmux new -s plexbot` session and
detach (`Ctrl+A D` / `Ctrl+B D`).

## Commands

| Command | Description |
|---------|-------------|
| `plex top` | Shows the top Plex users and assigns roles |
| `plex random [media_type] [genre]` | Shows a random item from your library with optional filtering |
| `plex tv <show name>` | Looks up a TV show in your library with watch stats |
| `plex movie <movie name>` | Looks up a movie in your library with watch stats |
| `plex recommend [@user]` | Recommends media based on watch history |
| `plex watchers` | Shows who's currently watching Plex |
| `plex downloading` | Shows current qBittorrent downloads |
| `plex recent [amount]` | Shows recently added media |
| `plex stats [days]` | Shows server statistics for the specified time period |
| `plex shows` | Shows top users by TV show watch time |
| `plex history [@user/username]` | Shows watch history for a user |
| `plex hot [days]` | Shows trending content and activity insights |
| `plex chart` | Shows options for visualized data/charts |
| `plex chart hours [@user] [days]` | Shows most active hours |
| `plex chart days [@user] [days]` | Shows most active days of week |
| `plex chart users [days]` | Shows most active users |
| `plex chart media [@user] [days]` | Shows media types watched per day |
| `plex chart months [@user] [days]` | Shows activity by month |
| `plex status` | Shows server status information |
| `plex killstream [session_key] [message]` | Terminates a user's stream (admin only) |
| `plex mapdiscord [plex_username] [@user]` | Maps Discord user to Plex username (admin only) |
| `plex ignore [plex_username]` | Toggles ignoring a user in stats (admin only) |
| `plex refresh_cache` | Manually refreshes the media cache (admin only) |
| `plex force_refresh_cache` | Fully invalidates and rebuilds the media cache (admin only) |
| `plex help [command]` | Shows help information |

## Troubleshooting

### Common Issues

- **Bot not responding to commands**: Ensure the bot token is correct and the bot has proper permissions in your Discord server.
- **No data from Tautulli**: Double-check your Tautulli API key and IP address settings.
- **qBittorrent integration issues**: Verify qBittorrent WebUI is enabled and credentials are correct.
- **Missing roles**: Make sure the bot has permission to manage roles and the role IDs are correctly configured.
- **Chart generation errors**: Ensure matplotlib and seaborn are correctly installed.

### Logs

The bot logs information to both the console and `plexbot.log`. If you're experiencing issues, check these logs for details.

## Technical Details

### Code Structure

- **plexbot.py**: Main entry point
- **config/**: Configuration system
- **errors/**: Error handling system
- **cogs/**: Command modules
  - **media_commands.py**: Media-related commands
  - **plex_data.py**: Data collection and processing
  - **plex_stats.py**: Statistics commands
  - **recommendations.py**: Recommendation system
  - **server_commands.py**: Server management
  - **user_management.py**: User mapping
  - **utility_commands.py**: Utility commands
  - **visualizations.py**: Chart commands
- **tautulli_wrapper.py**: Tautulli API wrapper
- **media_cache.py**: Media caching system
- **utilities.py**: Utility functions
- **tests/**: Unit tests for the helper functions

### Error Handling

The bot includes a robust error handling system that provides:
- Consistent error messages to users
- Detailed logging for troubleshooting
- Custom exceptions for specific error cases

If you experience issues, check the console output or log file for detailed error information.

## Customization

### UI Customization

You can customize the appearance of bot messages by modifying the UI settings in your configuration:

```json
"ui": {
    "plex_embed_color": 15040013,
    "plex_image": "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"
}
```

### Chart Customization

Control the appearance of generated charts:

```json
"charts": {
    "width": 14,
    "height": 6,
    "dpi": 100
}
```

### Default Values

Adjust default command behaviors:

```json
"defaults": {
    "recent_count": 10,
    "stats_duration": 7,
    "time_range": 30
}
```

## Upgrading

### From Earlier Versions

1. Pull the latest code:
   ```bash
   git pull origin main
   ```

2. Run the migration script to update your configuration:
   ```bash
   python migrate_config.py
   ```

3. Install any new dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Restart the bot:
   ```bash
   python plexbot.py
   ```