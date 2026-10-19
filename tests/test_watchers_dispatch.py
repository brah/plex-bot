"""/plex watchers through nextcord's real component dispatch (ConnectionState.parse_interaction_create)."""

import asyncio
from types import SimpleNamespace

from fakes import (
    EPISODE_PAUSED, MOVIE_PLAYING, FakeMessage, FakeTautulli, Recorder, build_bot, component_payload,
    many_sessions, member_payload, patch_images, settle, MESSAGE_ID,
)
import nextcord

from plexbot.cogs.server_commands import StreamDetailsSelect, StreamDetailView, WatchersView


def _store(bot, view):
    bot._connection.store_view(view, MESSAGE_ID)


def _click(bot, item, **kwargs):
    bot._connection.parse_interaction_create(component_payload(item.custom_id, **kwargs))


def test_overview_has_refresh_and_details_select(monkeypatch):
    patch_images(monkeypatch)

    async def run():
        _bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING, EPISODE_PAUSED]))
        view = WatchersView(cog, [])
        reply = await cog.render_watchers(await cog.fetch_activity(), view)
        return reply, view

    reply, view = asyncio.run(run())
    assert reply.embeds[0].title == "Now playing"
    assert [a.filename for a in reply.attachments] == ["posters.png"]
    select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
    assert [o.value for o in select.options] == ["abc", "def"]
    assert not any(getattr(c, "label", None) == "Manage streams" for c in view.children)


def test_nobody_watching_is_empty_state_without_select(monkeypatch):
    patch_images(monkeypatch)

    async def run():
        _bot, cog = build_bot(FakeTautulli([]))
        view = WatchersView(cog, [])
        return await cog.render_watchers(await cog.fetch_activity(), view), view

    reply, view = asyncio.run(run())
    assert "Nobody's watching" in reply.embeds[0].description
    assert not any(isinstance(c, StreamDetailsSelect) for c in view.children)


def test_details_select_capped_at_25(monkeypatch):
    patch_images(monkeypatch)

    async def run():
        _bot, cog = build_bot(FakeTautulli(many_sessions(30)))
        view = WatchersView(cog, [])
        await cog.render_watchers(await cog.fetch_activity(), view)
        return view

    view = asyncio.run(run())
    select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
    assert len(select.options) == 25


def test_refresh_click_edits_the_message(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        refresh = next(c for c in view.children if getattr(c, "label", None) == "Refresh")
        _click(bot, refresh)
        await settle()

    asyncio.run(run())
    assert recorder.kinds()[:2] == ["defer", "edit_original_message"]
    assert recorder.last("edit_original_message")["attachments"] == []


def test_refresh_when_tautulli_down_tells_clicker_privately(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING], reachable=False))
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        _click(bot, next(c for c in view.children if getattr(c, "label", None) == "Refresh"))
        await settle()

    asyncio.run(run())
    assert recorder.kinds() == ["defer", "followup"]
    assert recorder.last("followup")["ephemeral"] is True


def test_picking_a_stream_opens_private_detail(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING, EPISODE_PAUSED]))
        view = WatchersView(cog, [MOVIE_PLAYING, EPISODE_PAUSED])
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    assert recorder.kinds() == ["defer", "followup", "clicked_message.edit"]
    sent = recorder.last("followup")
    assert sent["ephemeral"] is True
    assert sent["embeds"][0].title == "The Matrix (1999)"
    assert sorted(f.filename for f in sent["files"]) == ["frame.jpg", "poster.jpg"]
    detail_view = sent["view"]
    assert isinstance(detail_view, StreamDetailView)
    assert not any(getattr(c, "label", None) == "Stop this stream" for c in detail_view.children)


def test_picking_a_stream_resets_the_public_dropdown(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)
    holder = {}

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = holder["view"] = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    reset = recorder.last("clicked_message.edit")
    assert reset["view"] is holder["view"]
    assert reset["message"].id == MESSAGE_ID
    assert set(reset) == {"view", "message"}  # only the components change


def test_failed_dropdown_reset_still_shows_the_detail_card(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def failing_edit(self, **kwargs):
        raise nextcord.HTTPException(SimpleNamespace(status=500, reason="boom"), "boom")

    monkeypatch.setattr(nextcord.Message, "edit", failing_edit)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    assert recorder.kinds() == ["defer", "followup"]


def test_overview_message_handle_is_the_clicked_message_after_refresh(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)
    original = FakeMessage(recorder, message_id=6666)  # the command's expiring webhook handle
    holder = {}

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = holder["view"] = WatchersView(cog, [MOVIE_PLAYING])
        view.message = original
        _store(bot, view)
        _click(bot, next(c for c in view.children if getattr(c, "label", None) == "Refresh"))
        await settle()

    asyncio.run(run())
    message = holder["view"].message
    assert message is not original
    assert isinstance(message, nextcord.Message)
    assert message.id == MESSAGE_ID


def test_overview_message_handle_is_the_clicked_message_after_a_pick(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)
    original = FakeMessage(recorder, message_id=6666)
    holder = {}

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = holder["view"] = WatchersView(cog, [MOVIE_PLAYING])
        view.message = original
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    message = holder["view"].message
    assert message is not original
    assert isinstance(message, nextcord.Message)
    assert message.id == MESSAGE_ID


def test_initial_overview_handle_is_a_bot_token_partial_message():
    from plexbot.ui.embeds import bot_token_handle

    class Channel:
        def get_partial_message(self, message_id):
            return ("partial", message_id)

    webhook_message = SimpleNamespace(id=6666)
    assert bot_token_handle(SimpleNamespace(channel=Channel()), webhook_message) == ("partial", 6666)
    # No channel that can make one: keep the original handle rather than lose it.
    assert bot_token_handle(SimpleNamespace(channel=None), webhook_message) is webhook_message
    assert bot_token_handle(SimpleNamespace(channel=Channel()), None) is None


def test_stream_managers_get_stop_button(monkeypatch):
    # Offline there's no cached guild, so Member.guild_permissions can't resolve admin rights;
    # make user 42 a configured stream manager instead (the same check, via stream_manager_ids).
    import plexbot.cogs.server_commands as server_commands

    real_get = server_commands.config.get
    monkeypatch.setattr(
        server_commands.config, "get",
        lambda section, key, default=None: ["42"] if (section, key) == ("discord", "stream_manager_ids")
        else real_get(section, key, default),
    )
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"], member=member_payload(user_id=42))
        await settle()

    asyncio.run(run())
    detail_view = recorder.last("followup")["view"]
    assert any(getattr(c, "label", None) == "Stop this stream" for c in detail_view.children)


def test_stream_that_ended_shows_private_notice_and_rerenders(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, cog = build_bot(FakeTautulli([]))  # the stream ended before the click
        view = WatchersView(cog, [MOVIE_PLAYING])
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    assert "That stream has ended" in recorder.last("followup")["embeds"][0].description
    # The overview was re-rendered (through the clicked message's bot-token handle).
    assert any(kind == "clicked_message.edit" and "embeds" in kwargs for kind, kwargs in recorder.calls)


def test_stop_from_detail_replaces_card_with_confirmation(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)
    import plexbot.cogs.server_commands as server_commands

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(server_commands.asyncio, "sleep", no_wait)

    async def run():
        tautulli = FakeTautulli([MOVIE_PLAYING])
        bot, cog = build_bot(tautulli)
        watchers = WatchersView(cog, [MOVIE_PLAYING])
        modal = server_commands.StopStreamModal(cog, "abc", "Alice — The Matrix", watchers)
        modal.message_input._inputed_value = "Server maintenance"
        payload = dict(component_payload("modal"), type=5,
                       data={"custom_id": modal.custom_id, "components": []})
        interaction = nextcord.Interaction(data=payload, state=bot._connection)
        await modal.callback(interaction)
        return tautulli

    tautulli = asyncio.run(run())
    assert tautulli.terminated == [("abc", "Server maintenance")]
    assert recorder.kinds()[:2] == ["defer", "edit_original_message"]
    edited = recorder.last("edit_original_message")
    assert "Stopped **Alice**" in edited["embeds"][0].description
    assert edited["view"] is None


def test_stop_confirmation_mentions_a_linked_viewer(monkeypatch):
    from plexbot import utilities

    monkeypatch.setattr(utilities, "mapped_discord_user",
                        lambda bot, username: SimpleNamespace(mention="<@42>") if username == "alice" else None)

    async def run():
        _bot, cog = build_bot(FakeTautulli([MOVIE_PLAYING]))
        return await cog.stop_stream("admin", "abc", None)

    ok, text = asyncio.run(run())
    assert ok is True
    assert text.startswith("Stopped **<@42>**'s stream")


def test_stop_option_reports_ended_stream(monkeypatch):
    async def run():
        _bot, cog = build_bot(FakeTautulli([]))
        return await cog.stop_stream("admin", "gone", None)

    ok, text = asyncio.run(run())
    assert ok is False
    assert "isn't running anymore" in text
