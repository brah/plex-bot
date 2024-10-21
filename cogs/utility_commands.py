# cogs/utility_commands.py

import logging
import nextcord
from nextcord.ext import commands

logger = logging.getLogger('plexbot.utility_commands')
logger.setLevel(logging.INFO)

class UtilityCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.plex_embed_color = 0xE5A00D
        self.plex_image = "https://images-na.ssl-images-amazon.com/images/I/61-kdNZrX9L.png"

    @commands.command()
    async def help(self, ctx, *commands: str):
        """Shows all commands available or detailed information about a specific command."""
        prefix = "plex "
        if not commands:
            embed = nextcord.Embed(
                title="Command List",
                color=self.plex_embed_color,
                description="Here's a list of all my commands:",
            )
            embed.set_thumbnail(url=self.plex_image)

            # Collecting commands and categorizing them by cog
            for cog_name, cog in sorted(
                self.bot.cogs.items(),
                key=lambda x: len(x[1].get_commands()),
                reverse=True,
            ):
                cog_commands = [
                    (
                        f"{prefix}{cmd.name} [{' '.join(cmd.aliases)}]"
                        if cmd.aliases
                        else f"{prefix}{cmd.name}"
                    )
                    for cmd in cog.get_commands()
                    if not cmd.hidden
                ]
                if cog_commands:
                    embed.add_field(
                        name=f"__**{cog_name}**__",
                        value="\n".join(cog_commands),
                        inline=False,
                    )

            embed.set_footer(text="Use plex help <command> for more info on a command.")
            await ctx.send(embed=embed)
        else:
            command_name = commands[0]
            cmd = self.bot.get_command(command_name)
            if not cmd:
                await ctx.send(f"Command not found: {command_name}")
                return

            # Build detailed command information
            embed = nextcord.Embed(
                title=f"{prefix}{cmd.name}",
                description=cmd.help or "No description provided.",
                color=self.plex_embed_color,
            )
            if cmd.aliases:
                embed.add_field(
                    name="Aliases", value=", ".join(cmd.aliases), inline=False
                )

            # Formatting parameters for usage display
            params = [
                f"<{key}>" if param.default is param.empty else f"[{key}]"
                for key, param in cmd.params.items()
                if key not in ("self", "ctx")
            ]
            if params:
                embed.add_field(
                    name="Usage",
                    value=f"{prefix}{cmd.name} {' '.join(params)}",
                    inline=False,
                )

            await ctx.send(embed=embed)

def setup(bot):
    bot.add_cog(UtilityCommands(bot))
