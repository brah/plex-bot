"""/plex wrapped: someone's year on Plex as a private story of slides, with a summary card to share."""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Union

import nextcord
from nextcord.ext import commands

from plexbot.errors import LoggedView
from plexbot.media_cache import MediaCache
from plexbot.plex_users import PlexUsers, unknown_user
from plexbot.slash_commands import plex
from plexbot.tautulli_wrapper import Tautulli
from plexbot.ui.embeds import ErrorKind, Reply, card, empty_state, error_state, service_down, set_thumbnail
from plexbot.ui.images import Attachment, mascot, plex_image
from plexbot.ui.theme import brand_colour
from plexbot.ui.wrapped import story
from plexbot.utilities import UserMappings, mapped_discord_user, viewer_mention, viewer_name
from plexbot.wrapped_year import WrappedYear, default_year, wrap_year, year_window

logger = logging.getLogger("plexbot.wrapped")

_TIMEOUT = 900  # a private message can only be edited through an interaction token, which lasts 15 minutes


class WrappedView(LoggedView):
    """◀ and ▶ page through the slides; Share posts the last one, the summary, in the channel once."""

    def __init__(self, slides: List[Attachment], share_text: str, timeout: float = _TIMEOUT):
        super().__init__(timeout=timeout)
        self.slides, self.share_text = slides, share_text
        self.index, self.shared = 0, False
        self._sync()

    def _sync(self) -> None:
        self.back.disabled = self.index == 0
        self.next.disabled = self.index == len(self.slides) - 1
        self.page.label = f"{self.index + 1} / {len(self.slides)}"

    async def _turn(self, interaction: nextcord.Interaction, step: int) -> None:
        self.index = max(0, min(len(self.slides) - 1, self.index + step))
        self._sync()
        self.refresh_private(interaction)
        await interaction.response.edit_message(file=self.slides[self.index].to_file(), attachments=[], view=self)

    @nextcord.ui.button(label="◀", style=nextcord.ButtonStyle.secondary)
    async def back(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._turn(interaction, -1)

    @nextcord.ui.button(label="1 / 1", style=nextcord.ButtonStyle.secondary, disabled=True)
    async def page(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        pass  # a counter, never clickable

    @nextcord.ui.button(label="▶", style=nextcord.ButtonStyle.secondary)
    async def next(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await self._turn(interaction, 1)

    @nextcord.ui.button(label="Share", style=nextcord.ButtonStyle.success)
    async def share(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        if self.shared:  # a second click that raced the first
            await interaction.response.defer()
            return
        self.shared = True
        button.disabled, button.label = True, "Shared"
        self.refresh_private(interaction)
        await interaction.response.edit_message(view=self)
        # Sharing someone else's year names them without pinging them.
        await interaction.followup.send(content=self.share_text, file=self.slides[-1].to_file(), ephemeral=False,
                                        allowed_mentions=nextcord.AllowedMentions.none())


class Wrapped(commands.Cog):
    help_category = "stats"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.users = PlexUsers(bot, self.tautulli)

    @plex.subcommand(description="Someone's year on Plex as a story, with a card to share")
    async def wrapped(
        self,
        interaction: nextcord.Interaction,
        user: str = nextcord.SlashOption(
            description="Whose year (default: yours)", required=False, autocomplete=True, max_length=100,
        ),
        year: int = nextcord.SlashOption(
            description="Which year (default: this one, or last year in January)", required=False,
            min_value=2000, max_value=2100,
        ),
    ):
        loading = card(title="Wrapping your year…", description="Counting your plays.", colour=brand_colour())
        gift = mascot("mascot-gift")
        set_thumbnail(loading, gift)
        sent = await Reply([loading], [gift] if gift else []).send(interaction, ephemeral=True)
        reply = await self._story(interaction, user, year)
        if reply.view:
            reply.view.message = sent
        await reply.edit(interaction)

    @wrapped.on_autocomplete("user")
    async def _wrapped_user_autocomplete(self, interaction: nextcord.Interaction, user: str):
        await interaction.response.send_autocomplete(await self.users.choices(user))

    async def _story(self, interaction: nextcord.Interaction, wanted: Optional[str], year: Optional[int]) -> Reply:
        """The first slide under its paging view, or the frame that says why there's no story."""
        zone = await self.tautulli.server_timezone()
        today = datetime.now(zone).date()
        year = year or default_year(today)
        if year > today.year:
            return error_state(ErrorKind.INPUT, f"{year} hasn't happened yet.")
        person = await self._person(interaction, wanted)
        if isinstance(person, Reply):
            return person
        # shortcut: refetches the whole year on every run; cache per year if busy servers find it slow.
        plays = await self.bot.get_cog("Visualizations").plays_between(*year_window(year, zone))
        if plays is None:
            return service_down("Tautulli")
        await self.media_cache.ensure_cache_valid()
        result = wrap_year(plays, person["username"], self.media_cache.media_items.get, year)
        mention = viewer_mention(self.bot, person)
        if result is None:
            return empty_state(f"Not enough to wrap yet for **{mention}** in {year}.")
        posters, avatar = await asyncio.gather(self._posters(result), self._avatar(person))
        slides = await asyncio.to_thread(story, result, name=viewer_name(self.bot, person), avatar=avatar,
                                         posters=posters)
        view = WrappedView(slides, f"📼 {mention}'s {year} on Plex · try `/plex wrapped`")
        return Reply([], [slides[0]], view)

    async def _person(self, interaction: nextcord.Interaction, wanted: Optional[str]) -> Union[dict, Reply]:
        """The Plex user named by `user`, else the caller's linked one; a Reply saying why there's none."""
        users = await self.users.all()
        if users is None:
            return service_down("Tautulli")
        if wanted:
            return self.users.match(users, wanted) or unknown_user(wanted)
        username = (UserMappings.get_mapping_by_discord_id(str(interaction.user.id)) or {}).get("plex_username")
        if not username:
            return error_state(ErrorKind.INPUT, "You aren't linked to a Plex account.",
                               hint="Pick a user, or ask an admin to link you with /plexadmin link.")
        # An ignored user isn't in `users`; their plays are left out too, so they get "not enough".
        return next((row for row in users if row["username"] == username), {"username": username})

    async def _posters(self, year: WrappedYear) -> Dict[str, bytes]:
        picks = [year.top_show.show if year.top_show else None, year.top_movie.movie if year.top_movie else None,
                 *year.top5]
        keys = list(dict.fromkeys(filter(None, [
            *(pick.poster for pick in picks if pick), *year.genre_posters, year.first.poster, year.last.poster,
        ])))
        images = await asyncio.gather(*(
            plex_image(self.tautulli, key, filename="poster.jpg", width=400, height=600) for key in keys
        ))
        return {key: image.data for key, image in zip(keys, images) if image}

    async def _avatar(self, person: dict) -> Optional[bytes]:
        """Their Discord avatar when linked; Plex avatars would need a fetch from plex.tv."""
        member = mapped_discord_user(self.bot, person.get("username"))
        if not member:
            return None
        try:
            return await member.display_avatar.read()
        except nextcord.DiscordException as e:
            logger.warning(f"Couldn't fetch the avatar for /plex wrapped: {e}")
            return None


def setup(bot):
    bot.add_cog(Wrapped(bot))
