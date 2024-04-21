import json
import nextcord
from nextcord.ext import commands

intents = nextcord.Intents.default()
# Need message_content for prefix commands
intents.message_content = True
# Need members for role changes in plex_top
intents.members = True

# Initialize bot with the prefix `plex ` and intents
bot = commands.Bot(
    command_prefix=["plex ", "Plex "], intents=intents, help_command=None
)
config = json.load(open("config.json", "r"))


bot.load_extension("cogs.plex_commands")

bot.run(config["token"])
