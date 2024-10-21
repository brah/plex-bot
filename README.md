# Plex-Bot

A for-fun Discord bot that talks to your Plex server through Tautulli's API, showing off cool commands like who's been watching the most Plex, suggests random movies/TV, shows off what's currently downloading from qBittorrent, and more soon™

## Features

- **Top Plex Users**: Get a list of the most active Plex viewers.
- **qBittorrent Downloads**: See current torrent downloads (optional).
- **Random Library Suggestions**: Choose genre, TV and/or Movies, and get a random movie picked.
- **More to Come**: I am consistently coding in what I think is a fun goof:)
- **NOTE** this is *currently* not designed to be a *utility* bot, don't expect admin features or extreme polish on anything provided.

## Setup

### What You'll Need

- **Python 3.8+**
- A working **Tautulli** setup ([Tautulli GitHub](https://github.com/Tautulli/Tautulli))
- Optional but fun: **qBittorrent** for download tracking

### Install and Configure

1. Clone the repo:

   ```bash
   git clone https://github.com/brah/plex-bot.git
   cd plex-bot
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a `config.json` in the same directory as `plexbot.py`. Here's a basic template:

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
       "qbit_password": "qbit_pass"
   }
   ```

    where `plex_top`, `plex_two`, `plex_three` are [role IDs](https://www.pythondiscord.com/pages/guides/pydis-guides/contributing/obtaining-discord-ids/#role-id) which you created to feature the top 3 media watchers of your server.
4. Fire it up:

   ```bash
   python3 plexbot.py
   ```

## Keeping It Running

Since this is a Discord bot, you'll probably want it to run 24/7. You’ve got a few options, up to preference (I use systemd, personally)

### Option 1: `systemd` (If you want it to run on boot)

Here’s a sample systemd service file. Save it as `/etc/systemd/system/plexbot.service`:

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

To get it rolling:

```bash
sudo systemctl daemon-reload
sudo systemctl start plexbot
sudo systemctl enable plexbot  # to start on boot
```

### Option 2: Use `screen` or `tmux`

These are super handy tools to keep the bot running in the background, even after you disconnect from SSH.

For **screen**:

```bash
screen -S plexbot
python3 plexbot.py
```

To detach, press `Ctrl+A`, then `D`. You can resume with:

```bash
screen -r plexbot
```

For **tmux**:

```bash
tmux new -s plexbot
python3 plexbot.py
```

Detach with `Ctrl+B`, then `D`, and resume with:

```bash
tmux attach -t plexbot
```

### Option 3: `pm2` (Great for node.js, works for Python too)

Install `pm2` globally if you don't have it:

```bash
npm install -g pm2
```

Then run:

```bash
pm2 start plexbot.py --name plexbot --interpreter=python3
```

You can also set it to start on reboot:

```bash
pm2 startup
pm2 save
```

## Commands

Once you’ve got the bot running, you can use the following commands in Discord:

- `plex top` - Shows the top Plex users.
- `plex downloading` - Shows current qBittorrent downloads.
- For a full list, type `plex help` in your Discord.

## Contributing

If you want to contribute, feel free to open a pull request or submit an issue. Keep in mind, though, I tend to break stuff from time to time, and I generally develop what *I* believe to be fun/useful as this is just a fun project - you are however welcome and invited to suggest/request with an open issue!
