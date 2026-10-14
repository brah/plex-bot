# cogs/utility_commands.py

import logging
from dataclasses import dataclass
from typing import Optional

import nextcord
from nextcord.ext import commands

from config import config
from errors import LoggedView
from slash_commands import command_paths, plex
from ui.embeds import Reply, card, set_thumbnail
from ui.format import plural, truncate
from ui.images import mascot
from ui.theme import Limits, brand_colour

logger = logging.getLogger("plexbot.utility_commands")
logger.setLevel(logging.INFO)

# (key, emoji, name, summary). Cogs opt in with a `help_category` class attribute.
CATEGORIES = [
    ("watching", "\U0001F3AC", "Watching", "Who's streaming right now and how the server is doing"),
    ("library", "\U0001F4DA", "Library", "Look things up, get a random pick, see what's new"),
    ("stats", "\U0001F3C6", "Stats", "Top watchers, watch history and what's trending"),
    ("charts", "\U0001F4CA", "Charts", "When and what people watch, as charts"),
    ("admin", "\U0001F6E0️", "Admin", "Server admin tools"),
]

# Worked examples, keyed by the command's usage path.
EXAMPLES = {
    "/plex watchers": "/plex watchers",
    "/plex status": "/plex status",
    "/plexadmin logs": "/plexadmin logs level:Error search:tautulli",
}


@dataclass(frozen=True)
class HelpEntry:
    usage: str
    description: str
    category: str
    example: Optional[str] = None


def _slash_usage(path: str, command) -> str:
    options = getattr(command, "options", {}) or {}
    parts = [path] + [f"<{name}>" if option.required else f"[{name}]" for name, option in options.items()]
    return " ".join(parts)


def collect_help_entries(bot, prefix: str) -> list:
    """Help text generated from the commands themselves, so it can't drift out of date."""
    entries = []
    for path, command in command_paths(bot):
        cog_category = getattr(getattr(command, "parent_cog", None), "help_category", None)
        category = "admin" if path.startswith("/plexadmin") else cog_category
        if category is None:
            continue
        entries.append(
            HelpEntry(_slash_usage(path, command), command.description or "", category, EXAMPLES.get(path))
        )
    for cog in bot.cogs.values():
        category = getattr(cog, "help_category", None)
        if category is None:
            continue
        for command in cog.get_commands():
            if command.hidden:
                continue
            usage = f"{prefix}{command.name} {command.signature}".strip()
            description = (command.help or "").strip().split("\n")[0]
            entries.append(HelpEntry(usage, description, category, EXAMPLES.get(f"{prefix}{command.name}")))
    return entries


def visible_entries(entries, is_admin: bool) -> list:
    return [entry for entry in entries if is_admin or entry.category != "admin"]


def _categories_with(entries):
    return [c for c in CATEGORIES if any(entry.category == c[0] for entry in entries)]


def help_overview(entries) -> Reply:
    lines = [
        f"{emoji} **{name}** — {summary} · "
        f"{plural(sum(e.category == key for e in entries), 'command')}"
        for key, emoji, name, summary in _categories_with(entries)
    ]
    embed = card(
        title="PlexBot",
        description="What's on, what's popular and who's watching.\n\n" + "\n".join(lines),
        colour=brand_colour(),
    )
    embed.set_footer(text="Pick a category below · type /plex to browse everything")
    art = mascot("mascot-wave")
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])


def help_category_page(entries, key: str) -> Reply:
    _key, emoji, name, summary = next(c for c in CATEGORIES if c[0] == key)
    blocks = []
    for entry in entries:
        if entry.category != key:
            continue
        block = f"`{entry.usage}`\n{entry.description}"
        if entry.example:
            block += f"\n-# e.g. `{entry.example}`"
        blocks.append(block)
    description = truncate(summary + "\n\n" + "\n\n".join(blocks), Limits.DESCRIPTION)
    return Reply([card(title=f"{emoji} {name}", description=description, colour=brand_colour())])


class HelpCategorySelect(nextcord.ui.StringSelect):
    def __init__(self, entries):
        options = [nextcord.SelectOption(label="Overview", value="overview", emoji="\U0001F3E0")]
        options += [
            nextcord.SelectOption(label=name, value=key, emoji=emoji, description=truncate(summary, 100))
            for key, emoji, name, summary in _categories_with(entries)
        ]
        super().__init__(placeholder="Choose a category…", options=options)

    async def callback(self, interaction: nextcord.Interaction):
        view: HelpView = self.view
        key = self.values[0]
        reply = help_overview(view.entries) if key == "overview" else help_category_page(view.entries, key)
        reply.view = view
        await interaction.response.edit_message(**reply.edit_payload())


class HelpView(LoggedView):
    def __init__(self, entries):
        super().__init__(timeout=600)
        self.entries = entries
        self.add_item(HelpCategorySelect(entries))


class UtilityCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @plex.subcommand(name="help", description="Show what PlexBot can do")
    async def help(self, interaction: nextcord.Interaction):
        permissions = getattr(interaction.user, "guild_permissions", None)
        is_admin = bool(permissions and permissions.administrator)
        prefix = config.get("core", "prefix", "plex ")
        entries = visible_entries(collect_help_entries(self.bot, prefix), is_admin)
        reply = help_overview(entries)
        reply.view = HelpView(entries)
        await reply.send(interaction, ephemeral=True)


def setup(bot):
    bot.add_cog(UtilityCommands(bot))
