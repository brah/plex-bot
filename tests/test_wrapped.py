"""/plex wrapped: a private story of slides, paged with buttons, whose summary Share posts once in public."""

import asyncio
from datetime import datetime, timezone

import pytest

from plexbot import utilities
from plexbot.cogs.wrapped import Wrapped, WrappedView
from fakes import (
    MESSAGE_ID, FakeTautulli, Recorder, build_wrapped_bot, component_payload, interaction, patch_images, settle,
)

USERS = [{"username": "sam", "friendly_name": "Sam", "user_id": 1},
         {"username": "alex", "friendly_name": "Alex", "user_id": 2}]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings",
                        staticmethod(lambda: [{"discord_id": "42", "plex_username": "sam"}]))
    patch_images(monkeypatch)


def _row(user, month, day, *, movie=None, show=None, episode=1, seconds=3600):
    started = int(datetime(2026, month, day, 20, tzinfo=timezone.utc).timestamp())
    row = {"user": user, "friendly_name": user.title(), "started": started, "play_duration": seconds,
           "watched_status": 1}
    if movie:
        row.update(media_type="movie", rating_key=f"m-{movie}", title=movie, thumb=f"/t/{movie}")
    else:
        row.update(media_type="episode", rating_key=f"{show}-{episode}", title=f"Episode {episode}",
                   grandparent_rating_key=f"s-{show}", grandparent_title=show, grandparent_thumb=f"/t/{show}")
    return row


def _tautulli(rows=None):
    tautulli = FakeTautulli()
    tautulli.users = USERS
    tautulli.history = rows if rows is not None else (
        [_row("sam", 3, 2 + i, show="Cold Open", episode=i) for i in range(6)]
        + [_row("sam", 5, 1, movie="Night Ferry", seconds=7200), _row("sam", 9, 9, movie="Red Meridian")]
        + [_row("alex", 4, 1 + i, show="Cold Open", episode=i) for i in range(3)]
    )
    return tautulli


def _run(fn, tautulli):
    async def run():
        bot, cog = build_wrapped_bot(tautulli)
        return await fn(bot, cog)

    return asyncio.run(run())


def _open(user=None, year=2026):
    async def run(bot, cog):
        await Wrapped.wrapped.callback(cog, interaction(bot), user=user, year=year)
        return bot

    return run


def _edited_text(recorder):
    return recorder.last("edit_original_message")["embeds"][0].description


def test_wrapped_shows_a_private_story(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(_open(), _tautulli())
    kind, first = recorder.calls[0]
    assert kind == "send_message" and first["ephemeral"] is True
    assert first["embeds"][0].title == "Wrapping your year…"
    edit = recorder.last("edit_original_message")
    assert edit["embeds"] == []
    assert [file.filename for file in edit["files"]] == ["wrapped-1.png"]
    view = edit["view"]
    assert isinstance(view, WrappedView)
    assert view.page.label == f"1 / {len(view.slides)}"
    assert view.back.disabled and not view.next.disabled


def _click(recorder, *names):
    async def run(bot, cog):
        await _open()(bot, cog)
        view = recorder.last("edit_original_message")["view"]
        bot._connection.store_view(view, MESSAGE_ID)
        for name in names:
            bot._connection.parse_interaction_create(component_payload(getattr(view, name).custom_id))
            await settle()
        return view

    return run


def test_paging_moves_between_slides(monkeypatch):
    recorder = Recorder(monkeypatch)
    view = _run(_click(recorder, "next"), _tautulli())
    sent = recorder.last("edit_message")
    assert sent["file"].filename == "wrapped-2.png"
    assert view.page.label == f"2 / {len(view.slides)}"
    assert not view.back.disabled
    recorder.calls.clear()
    view = _run(_click(recorder, "next", "back"), _tautulli())
    assert recorder.last("edit_message")["file"].filename == "wrapped-1.png"
    assert view.back.disabled


def test_share_posts_the_summary_once(monkeypatch):
    recorder = Recorder(monkeypatch)
    view = _run(_click(recorder, "share", "share"), _tautulli())
    posts = [kwargs for kind, kwargs in recorder.calls if kind == "followup"]
    assert len(posts) == 1
    post = posts[0]
    assert post["ephemeral"] is False
    assert post["file"].filename == f"wrapped-{len(view.slides)}.png"
    assert "2026 on Plex" in post["content"]
    assert post["allowed_mentions"].users is False
    assert view.share.disabled and view.share.label == "Shared"


def test_unlinked_person_is_told_to_pick_a_user(monkeypatch):
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings", staticmethod(lambda: []))
    recorder = Recorder(monkeypatch)
    _run(_open(), _tautulli())
    assert "aren't linked" in _edited_text(recorder)


def test_unknown_user(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(_open(user="nobody"), _tautulli())
    assert "No Plex user called" in _edited_text(recorder)


def test_someone_else_by_name(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(_open(user="alex"), _tautulli([_row("alex", 4, 1 + i, show="Cold Open", episode=i) for i in range(5)]))
    view = recorder.last("edit_original_message")["view"]
    assert "Alex" in view.share_text


def test_not_enough_to_wrap(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(_open(), _tautulli([_row("sam", 3, 2, movie="Night Ferry"), _row("sam", 3, 3, movie="Red Meridian")]))
    assert "Not enough to wrap yet" in _edited_text(recorder)


def test_future_year_is_refused(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(_open(year=datetime.now(timezone.utc).year + 1), _tautulli())
    assert "hasn't happened yet" in _edited_text(recorder)


def test_tautulli_down(monkeypatch):
    recorder = Recorder(monkeypatch)
    tautulli = _tautulli()
    tautulli.history_reachable = False
    _run(_open(), tautulli)
    assert "Couldn't reach **Tautulli**" in _edited_text(recorder)
