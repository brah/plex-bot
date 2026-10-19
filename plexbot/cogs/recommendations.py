"""/plex recommend: unwatched picks from the library in someone's favourite genres."""

import asyncio
import logging
from collections import Counter
from typing import List

import nextcord
from nextcord.ext import commands
from nextcord.utils import escape_markdown

from plexbot.config import config
from plexbot.errors import LoggedView
from plexbot.media_cache import MediaCache
from plexbot.media_details import MediaDetails, TautulliUnavailable, item_viewers
from plexbot.slash_commands import plex
from plexbot.tautulli_wrapper import Tautulli
from plexbot.ui.cards import genre_label, media_icon, session_title
from plexbot.ui.embeds import ErrorKind, Reply, bot_token_handle, card, empty_state, error_state, service_down
from plexbot.ui.format import join, plural, to_int, truncate
from plexbot.ui.images import fetch_poster_strip, poster_key
from plexbot.ui.theme import Limits, brand_colour
from plexbot.utilities import UserMappings, ignored_usernames

logger = logging.getLogger("plexbot.recommendations")

_PICKS = 3
_TOP_GENRES = 3
_SUMMARY = 150


def watched_keys(history: list) -> set:
    """Every item a history touches: episodes bring their season and show along."""
    return {str(entry[key]) for entry in history
            for key in ("rating_key", "parent_rating_key", "grandparent_rating_key") if entry.get(key)}


def favourite_genres(history: list, item_for) -> List[str]:
    """The top genres (lower-case, most first), counted once per watched item, show or season.

    item_for(rating_key) returns the media cache item or None.
    """
    counts = Counter()
    for key in sorted(watched_keys(history)):  # sorted, so ties break the same way every time
        for genre in (item_for(key) or {}).get("genres") or []:
            counts[genre.lower()] += 1
    return [genre for genre, _count in counts.most_common(_TOP_GENRES)]


def overview_card(picks: list, counts: list, genres: List[str], *, strip) -> nextcord.Embed:
    blocks = ["-# Based on their favourite genres: " + ", ".join(f"**{genre_label(g)}**" for g in genres)]
    for number, (item, count) in enumerate(zip(picks, counts), 1):
        summary = truncate(item.get("summary") or "No description available.", _SUMMARY)
        details = join([
            ", ".join(genre_label(g) for g in (item.get("genres") or [])[:3]),
            f"watched by {plural(count, 'person', 'people')}" if count else "nobody's watched it yet",
        ])
        blocks.append(
            f"**{number}. {escape_markdown(session_title(item))}** · {media_icon(item.get('media_type'))}\n"
            f"-# {escape_markdown(details)}\n{escape_markdown(summary)}"
        )
    embed = card(title="🍿 Recommendations", description="\n\n".join(blocks), colour=brand_colour())
    if strip:
        embed.set_image(url=strip.url)
    embed.set_footer(text="Pick a number for the details")
    return embed


class RecommendationView(LoggedView):
    """Numbered buttons open each pick's card in place; Back returns to the list.

    They change the shared message, so only the person who ran the command can use them.
    """

    def __init__(self, details: MediaDetails, author_id: int, picks: list, overview: Reply, timeout: float):
        super().__init__(timeout=timeout)
        self.details = details
        self.author_id = author_id
        self.picks = picks
        self.overview = overview
        for index in range(len(picks)):
            button = nextcord.ui.Button(label=str(index + 1), style=nextcord.ButtonStyle.primary)
            button.callback = self._detail_callback(index)
            self.add_item(button)
        self.back_button = nextcord.ui.Button(label="Back to list", style=nextcord.ButtonStyle.secondary,
                                              disabled=True)
        self.back_button.callback = self._back
        self.add_item(self.back_button)

    async def interaction_check(self, interaction: nextcord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await error_state(ErrorKind.PERMISSION, f"Only <@{self.author_id}> can browse these picks.",
                          hint="Run /plex recommend for your own.").send(interaction, ephemeral=True)
        return False

    def _detail_callback(self, index: int):
        async def callback(interaction: nextcord.Interaction):
            await self.show_pick(interaction, index)
        return callback

    async def show_pick(self, interaction: nextcord.Interaction, index: int):
        await interaction.response.defer()
        self.message = interaction.message  # bot-token handle; the command's webhook expires
        try:
            reply = await self.details.render(str(self.picks[index]["rating_key"]),
                                              footer=f"Recommendation {index + 1} of {len(self.picks)}")
        except TautulliUnavailable:
            await service_down("Tautulli").send(interaction, ephemeral=True)
            return
        if reply is None:
            await error_state(ErrorKind.INPUT, "That's not in the library anymore.").send(interaction, ephemeral=True)
            return
        self.back_button.disabled = False
        reply.view = self
        await reply.edit(interaction)

    async def _back(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        self.message = interaction.message
        self.back_button.disabled = True
        self.overview.view = self
        await self.overview.edit(interaction)


class Recommendations(commands.Cog):
    help_category = "library"

    def __init__(self, bot):
        self.bot = bot
        self.tautulli: Tautulli = bot.shared_resources.get("tautulli")
        self.media_cache: MediaCache = bot.shared_resources.get("media_cache")
        self.details = MediaDetails(bot, self.tautulli)

    async def pick(self, history: list) -> tuple:
        """(favourite genres, up to three unwatched picks in them) for a watch history."""
        keys = watched_keys(history)
        await self.media_cache.ensure_cache_valid()
        cached = self.media_cache.media_items
        genres = favourite_genres(history, lambda key: cached.get(key))
        if not genres:
            return genres, []
        candidates = await self.media_cache.get_items(
            genres=genres, exclude_rating_keys=keys, limit=_PICKS, random_sort=True,
        )
        return genres, candidates

    async def _viewer_counts(self, picks: list) -> list:
        responses = await asyncio.gather(*(self.tautulli.get_item_user_stats(p["rating_key"]) for p in picks))
        ignored = ignored_usernames()
        return [len(item_viewers(Tautulli.get_response_data(r, []) if Tautulli.check_response(r) else [], ignored))
                for r in responses]

    @plex.subcommand(description="Recommend unwatched movies and shows from someone's favourite genres")
    async def recommend(
        self,
        interaction: nextcord.Interaction,
        member: nextcord.Member = nextcord.SlashOption(
            description="Who to recommend for (default: you)", required=False,
        ),
    ):
        await interaction.response.defer()
        member = member or interaction.user
        name = member.mention
        mapping = UserMappings.get_mapping_by_discord_id(str(member.id))
        if not mapping or not mapping.get("plex_username"):
            await error_state(ErrorKind.INPUT, f"**{name}** isn't linked to a Plex account.",
                              hint="An admin can link Discord and Plex accounts.").send(
                interaction, ephemeral=True, replace_placeholder=True)
            return

        # Every play, not just the latest page: anything left out could be recommended as unwatched.
        history = await self.tautulli.get_history_rows({
            "user": mapping["plex_username"], "order_column": "date", "order_dir": "desc",
        })
        if history is None:
            logger.error("Failed to retrieve watch history from Tautulli.")
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return
        if not history:
            await empty_state(f"**{name}** hasn't watched anything yet, so there's nothing to go on.").send(
                interaction)
            return

        genres, picks = await self.pick(history)
        if not genres:
            await empty_state(f"Couldn't work out **{name}**'s favourite genres yet.",
                              hint="The library list may still be loading; try again in a few minutes.").send(
                interaction)
            return
        if not picks:
            await empty_state(f"**{name}** has seen everything in their favourite genres. Impressive.").send(
                interaction)
            return

        counts, strip = await asyncio.gather(
            self._viewer_counts(picks),
            fetch_poster_strip(self.tautulli, [poster_key(p) for p in picks]),
        )
        embed = overview_card(picks, counts, genres, strip=strip)
        embed.set_author(name=truncate(member.display_name, Limits.AUTHOR), icon_url=member.display_avatar.url)
        overview = Reply([embed], [strip] if strip else [])
        timeout = to_int(config.get("commands", "recommendation_timeout", 180), 180)
        view = RecommendationView(self.details, interaction.user.id, picks, overview, timeout=timeout)
        overview.view = view
        view.message = bot_token_handle(interaction, await overview.send(interaction))


def setup(bot):
    bot.add_cog(Recommendations(bot))
