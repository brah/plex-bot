"""/plex watchers through nextcord's real component dispatch (ConnectionState.parse_interaction_create)."""

import asyncio

from fakes import (
    EPISODE_PAUSED, MOVIE_PLAYING, FakeMessage, FakeTautulli, Recorder, build_bot, component_payload,
    many_sessions, member_payload, patch_images, settle, MESSAGE_ID,
)
import nextcord

from cogs.server_commands import StreamDetailsSelect, StreamDetailView, WatchersView


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
    assert recorder.kinds() == ["defer", "followup"]
    sent = recorder.last("followup")
    assert sent["ephemeral"] is True and sent["embeds"][0].title == "The Matrix (1999)"
    assert sorted(f.filename for f in sent["files"]) == ["frame.jpg", "poster.jpg"]
    detail_view = sent["view"]
    assert isinstance(detail_view, StreamDetailView)
    assert not any(getattr(c, "label", None) == "Stop this stream" for c in detail_view.children)


def test_stream_managers_get_stop_button(monkeypatch):
    # Offline there's no cached guild, so Member.guild_permissions can't resolve admin rights;
    # make user 42 a configured stream manager instead (the same check, via stream_manager_ids).
    import cogs.server_commands as server_commands

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
        view.message = FakeMessage(recorder)
        _store(bot, view)
        select = next(c for c in view.children if isinstance(c, StreamDetailsSelect))
        _click(bot, select, component_type=3, values=["abc"])
        await settle()

    asyncio.run(run())
    assert "That stream has ended" in recorder.last("followup")["embeds"][0].description
    assert "message.edit" in recorder.kinds()  # overview re-rendered


def test_stop_from_detail_replaces_card_with_confirmation(monkeypatch):
    patch_images(monkeypatch)
    recorder = Recorder(monkeypatch)
    import cogs.server_commands as server_commands

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
    assert "Stopped **Alice**" in edited["embeds"][0].description and edited["view"] is None


def test_stop_option_reports_ended_stream(monkeypatch):
    async def run():
        _bot, cog = build_bot(FakeTautulli([]))
        return await cog.stop_stream("admin", "gone", None)

    ok, text = asyncio.run(run())
    assert ok is False and "isn't running anymore" in text
