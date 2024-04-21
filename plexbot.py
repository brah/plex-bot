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

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"*{error}*\nTry `.help {ctx.command}`")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "You do not have the appropriate permissions to run this command."
        )
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("I don't have sufficient permissions!")


bot.load_extension("cogs.plex_commands")

bot.run(config["token"])
