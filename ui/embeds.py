# ui/embeds.py

"""Standard response frames (card, empty, error, success) and Reply, which sends them."""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import nextcord

from ui.format import truncate
from ui.images import Attachment, mascot
from ui.theme import Colour, Limits

logger = logging.getLogger("plexbot.ui.embeds")


def card(*, title=None, description=None, colour: int = Colour.NEUTRAL, url=None) -> nextcord.Embed:
    """The base embed: every response frame starts here so limits are always respected."""
    return nextcord.Embed(
        title=truncate(title, Limits.TITLE) if title else None,
        description=truncate(description, Limits.DESCRIPTION) if description else None,
        colour=colour,
        url=url,
    )


def set_thumbnail(embed: nextcord.Embed, attachment: Optional[Attachment]) -> nextcord.Embed:
    if attachment:
        embed.set_thumbnail(url=attachment.url)
    return embed


def set_image(embed: nextcord.Embed, attachment: Optional[Attachment]) -> nextcord.Embed:
    if attachment:
        embed.set_image(url=attachment.url)
    return embed


@dataclass
class Reply:
    """A response's embeds, attachments and view, sent the same way from any interaction."""

    embeds: List[nextcord.Embed]
    attachments: List[Attachment] = field(default_factory=list)
    view: Optional[nextcord.ui.View] = None
    content: Optional[str] = None

    def _send_payload(self) -> dict:
        payload = {"embeds": self.embeds}
        if self.attachments:
            payload["files"] = [attachment.to_file() for attachment in self.attachments]
        if self.content is not None:
            payload["content"] = self.content
        if self.view is not None:
            payload["view"] = self.view
        return payload

    def edit_payload(self) -> dict:
        """Keyword arguments for editing a message into this reply (old attachments dropped)."""
        payload = {"content": self.content, "embeds": self.embeds, "attachments": [], "view": self.view}
        if self.attachments:
            payload["files"] = [attachment.to_file() for attachment in self.attachments]
        return payload

    async def send(self, interaction: nextcord.Interaction, *, ephemeral: bool = False,
                   replace_placeholder: bool = False):
        """Send as the first response, or as a follow-up after a defer.

        replace_placeholder: the command deferred publicly ("thinking…"), but this reply
        must be private. Discord makes the first follow-up inherit the placeholder's
        visibility, so delete the placeholder first. If that delete fails, fall back to
        a compact public line: the first line of the description, with no hint, mascot
        or view, so the full reply never goes out publicly.

        Hazard: replace_placeholder deletes the original response unconditionally, so it
        assumes this send is the command's last step that can fail. A command that sent
        real content and then raised would lose that content with the placeholder.
        """
        if not interaction.response.is_done():
            await interaction.response.send_message(ephemeral=ephemeral, **self._send_payload())
            return None
        if ephemeral and replace_placeholder:
            try:
                await interaction.delete_original_message()
            except nextcord.HTTPException as e:
                logger.warning(f"Could not remove the public placeholder, sending a compact line: {e}")
                return await interaction.followup.send(
                    content=self._compact_line(), ephemeral=False, wait=True
                )
        return await interaction.followup.send(ephemeral=ephemeral, wait=True, **self._send_payload())

    def _compact_line(self) -> str:
        description = self.embeds[0].description if self.embeds else None
        return description.split("\n", 1)[0] if description else "Something went wrong."

    async def edit(self, interaction: nextcord.Interaction) -> None:
        """Edit the interaction's own message (after a defer) into this reply."""
        await interaction.edit_original_message(**self.edit_payload())

    async def edit_message(self, message) -> None:
        await message.edit(**self.edit_payload())


class ErrorKind(Enum):
    INPUT = "input"
    SERVICE_DOWN = "service_down"
    PERMISSION = "permission"
    BUG = "bug"


_ERROR_STYLES = {
    ErrorKind.INPUT: (Colour.WARNING, None),
    ErrorKind.SERVICE_DOWN: (Colour.ERROR, "mascot-unplugged"),
    ErrorKind.PERMISSION: (Colour.WARNING, None),
    ErrorKind.BUG: (Colour.ERROR, "mascot-oops"),
}


def _with_hint(message: str, hint: Optional[str]) -> str:
    # "-# " is Discord's small grey subtext line.
    return f"{message}\n-# {hint}" if hint else message


def _framed(embed: nextcord.Embed, slot: Optional[str]) -> Reply:
    art = mascot(slot) if slot else None
    set_thumbnail(embed, art)
    return Reply([embed], [art] if art else [])


def error_state(kind: ErrorKind, message: str, *, hint: Optional[str] = None) -> Reply:
    colour, slot = _ERROR_STYLES[kind]
    return _framed(card(description=_with_hint(message, hint), colour=colour), slot)


def service_down(service: str) -> Reply:
    return error_state(ErrorKind.SERVICE_DOWN, f"Couldn't reach **{service}**.",
                       hint="Try again in a minute.")


def unexpected_error(path: Optional[str] = None) -> Reply:
    where = f" running `{path}`" if path else ""
    return error_state(ErrorKind.BUG, f"Something went wrong{where}.",
                       hint="That's on us, and it's been logged.")


def empty_state(message: str, *, hint: Optional[str] = None, title: Optional[str] = None) -> Reply:
    embed = card(title=title, description=_with_hint(message, hint), colour=Colour.NEUTRAL)
    return _framed(embed, "mascot-sleepy")


def success(message: str) -> Reply:
    return Reply([card(description=f"✅ {message}", colour=Colour.SUCCESS)])
