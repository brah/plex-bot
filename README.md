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

## Setup

### Requirements

- **Python 3.8+**
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

3. Create a `config.json` file in the root directory:

   ```json
   {
       "tautulli_ip": "localhost:8181",
       "tautulli_apikey": "<Your Tautulli API Key>",
       "token": "<Your Discord Bot Token>",
       "server_id": "<Your Discord Server ID>",
       "plex_top": role_id1,
       "plex_two": role_id2,
       "plex_three": role_id3,
       "qbit_ip": "localhost",
       "qbit_port": "8080",
       "qbit_username": "qbit_username",
       "qbit_password": "qbit_pass",
       "tmdb_apikey": "<Your TMDB API Key>"
   }
   ```

   *Note: `plex_top`, `plex_two`, and `plex_three` are [role IDs](https://www.pythondiscord.com/pages/guides/pydis-guides/contributing/obtaining-discord-ids/#role-id) which you've created to highlight the top 3 media watchers on your server.*

      *Note:  bot_config.py also has config options you can change, however they are mostly optional/personal preference.*

4. Run the bot:

   ```bash
   python plexbot.py
   ```

### Running as a Service

For 24/7 operation, consider one of these methods:

#### Option 1: systemd (Linux)

Create a file at `/etc/systemd/system/plexbot.service`:

```ini
[Unit]
Description=Plex Discord Bot Service
After=network.target

[Service]
User=<your-username>
WorkingDirectory=/path/to/plex-bot
ExecStart=/usr/bin/python3 /path/to/plex-bot/plexbot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl start plexbot
sudo systemctl enable plexbot  # to start on boot
```

#### Option 2: Screen or tmux

For temporary background operation:

**Screen:**
```bash
screen -S plexbot
python plexbot.py
# Press Ctrl+A, then D to detach
# Resume with: screen -r plexbot
```

**Tmux:**
```bash
tmux new -s plexbot
python plexbot.py
# Press Ctrl+B, then D to detach
# Resume with: tmux attach -t plexbot
```

#### Option 3: PM2

If you're familiar with Node.js ecosystem:

```bash
npm install -g pm2
pm2 start plexbot.py --name plexbot --interpreter=python3
pm2 startup
pm2 save
```

## Commands

| Command | Description |
|---------|-------------|
| `plex top` | Shows the top Plex users and assigns roles |
| `plex random [media_type] [genre]` | Shows a random item from your library with optional filtering |
| `plex recommend [@user]` | Recommends media based on watch history |
| `plex watchers` | Shows who's currently watching Plex |
| `plex downloading` | Shows current qBittorrent downloads |
| `plex recent [amount]` | Shows recently added media |
| `plex stats [days]` | Shows server statistics for the specified time period |
| `plex most_watched_hours` | Shows viewing activity by hour of day |
| `plex most_watched_days` | Shows viewing activity by day of week |
| `plex media_type_by_day` | Shows viewing trends by media type |
| `plex help` | Shows all available commands |
