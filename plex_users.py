# plex_users.py

"""The server's Plex users, for commands with a `user` option (/plex history, /plex chart ...)."""

import time
from typing import Optional

import nextcord
from nextcord.utils import escape_markdown

from tautulli_wrapper import Tautulli
from ui.embeds import ErrorKind, error_state, service_down
from ui.format import truncate
from ui.theme import Limits
from utilities import ignored_usernames, viewer_name

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
        """The user whose Plex username, Plex name or Discord name is `wanted` (any case)."""
        wanted = wanted.strip().lower()
        for row in users:
            names = {row["username"], row.get("friendly_name") or "", self.name(row)}
            if wanted in {n.lower() for n in names if n}:
                return row
        return None

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
