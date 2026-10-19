"""ui.embeds: standard frames and Reply's send/edit behaviour."""

import asyncio

import nextcord

import plexbot.ui.embeds as embeds
from plexbot.ui.embeds import (
    ErrorKind,
    Reply,
    card,
    empty_state,
    error_state,
    service_down,
    success,
    unexpected_error,
)
from plexbot.ui.images import Attachment
from plexbot.ui.theme import Colour, Limits

ART = Attachment("mascot-oops.png", b"png")


class Response:
    def __init__(self, done=False):
        self.done = done
        self.calls = []

    def is_done(self):
        return self.done

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))


class Followup:
    def __init__(self):
        self.calls = []

    async def send(self, **kwargs):
        self.calls.append(("followup", kwargs))
        return "message"


class Interaction:
    def __init__(self, done=False, delete_fails=False):
        self.response = Response(done)
        self.followup = Followup()
        self.deleted = False
        self.delete_fails = delete_fails
        self.edits = []

    async def delete_original_message(self):
        if self.delete_fails:
            raise nextcord.HTTPException(type("R", (), {"status": 400, "reason": "Bad"})(), "nope")
        self.deleted = True

    async def edit_original_message(self, **kwargs):
        self.edits.append(kwargs)


class TestCard:
    def test_truncates_to_discord_limits(self):
        embed = card(title="t" * 400, description="d" * 5000, colour=Colour.ERROR)
        assert len(embed.title) == Limits.TITLE and len(embed.description) == Limits.DESCRIPTION
        assert embed.colour.value == Colour.ERROR

    def test_optional_parts(self):
        embed = card()
        assert embed.title is None and embed.description is None


class TestStates:
    def test_service_down_has_unplugged_mascot(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
        reply = service_down("Tautulli")
        embed = reply.embeds[0]
        assert "Tautulli" in embed.description and embed.colour.value == Colour.ERROR
        assert embed.thumbnail.url == "attachment://mascot-unplugged.png"
        assert [a.filename for a in reply.attachments] == ["mascot-unplugged.png"]

    def test_input_error_has_no_mascot(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: ART)
        reply = error_state(ErrorKind.INPUT, "No movie matching 'xyz'.", hint="Try fewer words.")
        assert reply.attachments == [] and reply.embeds[0].colour.value == Colour.WARNING
        assert "-# Try fewer words." in reply.embeds[0].description

    def test_missing_mascot_file_just_means_no_image(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: None)
        reply = unexpected_error("/plex status")
        assert reply.attachments == [] and not reply.embeds[0].thumbnail.url
        assert "/plex status" in reply.embeds[0].description

    def test_empty_and_success(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
        empty = empty_state("Nobody's watching right now.", hint="Hit Refresh to check again.")
        assert empty.embeds[0].colour.value == Colour.NEUTRAL
        assert empty.attachments[0].filename == "mascot-sleepy.png"
        done = success("Stopped Bob's stream.")
        assert done.embeds[0].colour.value == Colour.SUCCESS and done.attachments == []


class TestReplySend:
    def test_first_response(self):
        interaction = Interaction(done=False)
        asyncio.run(Reply([card(description="hi")], [ART]).send(interaction, ephemeral=True))
        kind, kwargs = interaction.response.calls[0]
        assert kind == "send_message" and kwargs["ephemeral"] is True
        assert [f.filename for f in kwargs["files"]] == ["mascot-oops.png"]
        assert "view" not in kwargs

    def test_followup_after_defer(self):
        interaction = Interaction(done=True)
        result = asyncio.run(Reply([card(description="hi")]).send(interaction))
        kind, kwargs = interaction.followup.calls[0]
        assert result == "message" and kwargs["ephemeral"] is False and kwargs["wait"] is True
        assert "files" not in kwargs

    def test_private_error_replaces_public_placeholder(self):
        interaction = Interaction(done=True)
        asyncio.run(Reply([card()]).send(interaction, ephemeral=True, replace_placeholder=True))
        assert interaction.deleted is True
        assert interaction.followup.calls[0][1]["ephemeral"] is True

    def test_placeholder_delete_failure_falls_back_to_compact_public_line(self, monkeypatch):
        monkeypatch.setattr(embeds, "mascot", lambda slot: ART)
        reply = error_state(
            ErrorKind.SERVICE_DOWN, "Couldn't reach **Tautulli**.", hint="Try again in a minute."
        )
        interaction = Interaction(done=True, delete_fails=True)
        asyncio.run(reply.send(interaction, ephemeral=True, replace_placeholder=True))
        kwargs = interaction.followup.calls[0][1]
        assert kwargs["ephemeral"] is False
        assert kwargs["content"] == "Couldn't reach **Tautulli**."
        assert "embeds" not in kwargs and "files" not in kwargs and "view" not in kwargs

    def test_compact_line_falls_back_when_there_is_no_description(self):
        interaction = Interaction(done=True, delete_fails=True)
        asyncio.run(Reply([card()]).send(interaction, ephemeral=True, replace_placeholder=True))
        assert interaction.followup.calls[0][1]["content"] == "Something went wrong."


class TestReplyEdit:
    def test_edit_replaces_attachments_and_view(self):
        interaction = Interaction(done=True)
        asyncio.run(Reply([card()], [ART]).edit(interaction))
        kwargs = interaction.edits[0]
        assert kwargs["attachments"] == [] and kwargs["view"] is None
        assert [f.filename for f in kwargs["files"]] == ["mascot-oops.png"]
