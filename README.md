# plex-bot

Hobby project to interact with Plex through the Tautulli API for fun commands on your Discord server

If you intend to use it, please do note I can and will break stuff at any and every moment (+ development will likely come in bursts, just one of those things :-))

## Setup

Preferably on the same machine where Plex and Tautulli reside (not strictly, external requests *should* work OK, but you know)

Note this will install in the directory you are currently sitting in, doesn't matter where it goes. Personally, I created a `/home/app` folder where I run it from.

1. `git clone https://github.com/brah/plex-bot.git`
2. `pip install -r requirements.txt`
3. Create a `config.json` file in the same directory as `plexbot.py`, add the sample values below and fill as necessary - Should be mostly self explanatory, however there is explanations below:

```json
{
"tautulli_ip": "192.168.0.50:8181",
"tautulli_apikey": "",
"token": "Discord bot token",
"server_id": 0,
"plex_top": 1,
"plex_two": 2,
"plex_three": 3,
"qbit_ip": "192.qbit.ip",
"qbit_port": "8080",
"qbit_username": "qbit_username",
"qbit_password": "qbit_pass"
}
```

- **tautulli_ip** - Local DNS records should work fine (i.e. media.server:8181), else `localhost:8181` or `IP:PORT`
- **tautulli_apikey** - Go to your respective page: `TAUTULLI_IP:PORT/settings#tabs_tabs-web_interface` and you can find your API key at the bottom
- **token** - [Guide](https://www.writebots.com/discord-bot-token/)
- **tmdb_apikey** - [Create one here](https://www.themoviedb.org/settings/api) note: this is optional, it is (going to be...) used for images in `plex recent` for recent additions to library - WIP integration
- **server_id** - The bot (currently) is designed to operate on **one** server, right click the server, copy ID and paste it here.
- **plex_top**
- **plex_two**
- **plex_three** - these should all be role IDs (i.e. plex 1st, plex 2nd, plex 3rd) for top 3 plex users to get special roles. Create the roles (permissions of the roles/name/etc. doesn't matter - as long as the roles don't rank above **THIS** bot's role). Note these ARE optional, however most of the bot's functionality comes down to the `plex top` command.
- **qbit_ip** - qBittorrent config is OPTIONAL! Adds `plex downloading` functionality so users can see current downloads. There is no filtering! All non-100% torrents are posted‼️
- **qbit_port** - typically `8080`, you can find it in qBittorrent settings->webUI->port
- **qbit_username** - default is admin, blank should work if you do not have any auth (or have it disabled for localhost)
- **qbit_password** - default on qBittorrent is adminadmin

## Running the script

Once your `config.json` is prepared, you can run `python3 plexbot.py` - do note if you close the terminal the script will stop also. How you run it infinitely is up to you, some valid options are [screen](https://linuxize.com/post/how-to-use-linux-screen/), cron, [forever](https://stackoverflow.com/a/19571283) or even as a [systemd-service](https://medium.com/codex/setup-a-python-script-as-a-service-through-systemctl-systemd-f0cc55a42267)

## Commands

![Plex help command with current commands](https://i.imgur.com/aQ4BBf4.png)
