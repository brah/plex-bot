# plex-bot
Hobby project to interact with Plex through the Tautulli API for fun commands on your Discord server

## Setup

Preferably on the same machine where Plex and Tautulli reside (not strictly, external requests *should* work OK, but you know)

`git clone https://github.com/brah/plex-bot.git`

Tweak [config.json](https://github.com/brah/plex-bot/blob/main/config.json) - Should be mostly self explanatory **(make sure to remove all comments, they're just to assist)**:

  

```json
{
"tautulli_ip": "192.168.0.50:8181",
"tautulli_apikey": "",
"token": "discord bot token",
"server_id": 0,
"plex_top": 1,
"plex_two": 2,
"plex_three": 3
}
```
- **tautulli_ip** - Local DNS records should work fine (i.e. media.server:8181)
- **tautulli_apikey** - TAUTULLI_IP:PORT/settings#tabs_tabs-web_interface <- Fill your address+port and you can find it at the bottom of that URL
- **token** - [Guide](https://www.writebots.com/discord-bot-token/)
- **server_id** - The bot (currently) is designed to operate on **one** server, right click the server, copy ID and paste it here.
- **plex_top**
- **plex_two**
- **plex_three** - these should all be role IDs (i.e. plex 1st, plex 2nd, plex 3rd) for top 3 plex users to get special roles. Create the roles (permissions of the roles/name/etc. doesn't matter - as long as the roles don't rank above **THIS** bot's role). Note these ARE optional, however most of the bot's functionality comes down to the `plex top` command.
