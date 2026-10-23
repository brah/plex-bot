"""The server's Plex users, for commands with a `user` option (/plex history, /plex chart ...)."""

import time
from typing import Optional

import nextcord
from nextcord.utils import escape_markdown

from plexbot.tautulli_wrapper import Tautulli
from plexbot.ui.embeds import ErrorKind, error_state, service_down
from plexbot.ui.format import truncate
from plexbot.ui.theme import Limits
from plexbot.utilities import ignored_usernames, viewer_name

_TTL = 300  # autocomplete asks on every keystroke; the user list rarely changes


class PlexUsers:
    """Tautulli's users minus ignored ones, named as the Discord server knows them."""

    def __init__(self, bot, tautulli):
        self.bot = bot
        self.tautulli = tautulli
        self._cache: tuple = (0.0, [])  # (fetched at, users)

    async def everyone(self) -> Optional[list]:
        """Every Plex user Tautulli knows, ignored ones included; None when Tautulli didn't answer."""
        fetched, users = self._cache
        if users and time.monotonic() - fetched < _TTL:
            return users
        response = await self.tautulli.get_users()
        if not Tautulli.check_response(response):
            return None
        users = [row for row in Tautulli.get_response_data(response, []) or [] if row.get("username")]
        self._cache = (time.monotonic(), users)
        return users

    async def all(self) -> Optional[list]:
        """Every user who isn't ignored (applied on each call, so an ignore takes effect at once)."""
        users = await self.everyone()
        if users is None:
            return None
        ignored = ignored_usernames()
        return [row for row in users if row["username"] not in ignored]

    def name(self, row: dict) -> str:
        return viewer_name(self.bot, row)

    def label(self, row: dict) -> str:
        """"Alice" or, when their Discord name differs from their Plex one, "Ali (alice)"."""
        shown, plex_name = self.name(row), row.get("friendly_name") or row["username"]
        return shown if shown == plex_name else f"{shown} ({plex_name})"

    def match(self, users: list, wanted: str) -> Optional[dict]:
        """The user whose Plex username is `wanted` (what the suggestions send), else the one whose
        Plex username or Plex name is `wanted` in any case, else the one whose Discord name is."""
        wanted = wanted.strip()
        exact = next((row for row in users if row["username"] == wanted), None)
        if exact:
            return exact
        wanted = wanted.lower()
        # Plex names first: one user's Discord name can be another user's Plex name.
        for row in users:
            if wanted in {name.lower() for name in (row["username"], row.get("friendly_name")) if name}:
                return row
        return next((row for row in users if self.name(row).lower() == wanted), None)

    async def choices(self, typed: str) -> dict:
        """Autocomplete choices: {label: username}."""
        typed = (typed or "").strip().lower()
        choices = {}
        for row in await self.all() or []:
            label = truncate(self.label(row), Limits.SELECT_LABEL)
            if typed in label.lower() or typed in row["username"].lower():
                choices.setdefault(label, row["username"])
        return dict(list(choices.items())[:Limits.SELECT_OPTIONS])

    async def resolve(self, interaction: nextcord.Interaction, wanted: str) -> tuple:
        """(user, True) for a `user` option, after a public defer.

        On failure the private error has been sent already and this returns (None, False).
        """
        users = await self.all()
        if users is None:
            await service_down("Tautulli").send(interaction, ephemeral=True, replace_placeholder=True)
            return None, False
        person = self.match(users, wanted)
        if person is None:
            await error_state(
                ErrorKind.INPUT, f"No Plex user called **{escape_markdown(truncate(wanted, 80))}**.",
                hint="Pick one from the suggestions as you type.",
            ).send(interaction, ephemeral=True, replace_placeholder=True)
            return None, False
        return person, True
