"""/plex watchers when nobody's watching: optionally, who watched last."""

import asyncio
from types import SimpleNamespace

from plexbot import utilities

from fakes import FakeTautulli, build_bot, patch_images
from plexbot.cogs.server_commands import WatchersView, last_viewers
from plexbot.ui.cards import recent_viewer_entry, recent_viewer_line

MOVIE = {"user": "alice", "friendly_name": "Alice", "media_type": "movie", "title": "The Matrix",
         "full_title": "The Matrix", "year": 1999, "percent_complete": 98, "platform": "Roku",
         "date": 1_760_000_000, "stopped": 1_760_008_000}
EPISODE = {"user": "bob", "friendly_name": "_Bob_", "media_type": "episode", "title": "Hello, Ms. Cobel",
           "grandparent_title": "Severance", "parent_media_index": 1, "media_index": 2,
           "percent_complete": 45, "platform": "iOS", "date": 1_759_990_000, "stopped": 1_759_995_000}
OLDER_ALICE = dict(MOVIE, title="Heat", full_title="Heat", year=1995, stopped=1_759_000_000)
IGNORED = dict(MOVIE, user="mallory", friendly_name="Mallory")


def test_line_for_a_finished_movie():
    assert recent_viewer_line(MOVIE) == "🎬 **Alice** · The Matrix (1999) · on Roku · <t:1760008000:R>"


def test_line_for_an_unfinished_episode_escapes_markdown():
    assert recent_viewer_line(EPISODE) == (
        "📺 **\\_Bob\\_** · Severance · S01E02 · Hello, Ms. Cobel · stopped at 45% · on iOS · <t:1759995000:R>"
    )


def test_line_with_missing_details():
    assert recent_viewer_line({"user": "dan", "media_type": "clip"}) == "▶ **dan** · Unknown"


def test_entry_puts_who_and_when_first_and_the_rest_in_subtext():
    assert recent_viewer_entry(EPISODE) == (
        "📺 **\\_Bob\\_** · <t:1759995000:R>\n"
        "-# Severance · S01E02 · Hello, Ms. Cobel · stopped at 45% · on iOS"
    )
    assert recent_viewer_entry({"user": "dan", "media_type": "clip"}) == "▶ **dan**\n-# Unknown"


def test_last_viewers_keeps_each_viewers_latest_play_and_skips_ignored():
    rows = [MOVIE, IGNORED, OLDER_ALICE, EPISODE]
    assert last_viewers(rows, {"mallory"}) == [MOVIE, EPISODE]
    many = [dict(MOVIE, user=f"u{i}") for i in range(8)]
    assert len(last_viewers(many, set())) == 3


def _render(monkeypatch, *, enabled, history=None, reachable=True):
    import plexbot.cogs.server_commands as server_commands

    patch_images(monkeypatch)
    monkeypatch.setattr(server_commands.config, "get", lambda section, key, default=None:
                        enabled if (section, key) == ("discord", "show_recent_viewers") else default)
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings",
                        staticmethod(lambda: [{"plex_username": "mallory", "ignore": True}]))
    tautulli = FakeTautulli([])
    tautulli.history = history if history is not None else [MOVIE, IGNORED, EPISODE]
    tautulli.history_reachable = reachable

    async def run():
        _bot, cog = build_bot(tautulli)
        return await cog.render_watchers(await cog.fetch_activity(), WatchersView(cog, []))

    return asyncio.run(run()), tautulli


def test_nobody_watching_lists_last_viewers_when_enabled(monkeypatch):
    reply, tautulli = _render(monkeypatch, enabled=True)
    description = reply.embeds[0].description
    assert description.startswith((
        "Nobody's watching right now.\n\n**Last seen**\n🎬 **Alice** · <t:1760008000:R>\n-# The Matrix (1999) · on Roku"
    ))
    assert "Bob" in description and "Mallory" not in description
    assert description.endswith("-# Hit Refresh to check again.")
    assert tautulli.history_calls == [{"length": 25, "order_column": "date", "order_dir": "desc"}]


def test_last_seen_mentions_linked_viewers(monkeypatch):
    monkeypatch.setattr(utilities, "mapped_discord_user",
                        lambda bot, username: SimpleNamespace(mention="<@42>") if username == "alice" else None)
    reply, _ = _render(monkeypatch, enabled=True)
    description = reply.embeds[0].description
    assert "🎬 **<@42>** · <t:1760008000:R>" in description  # linked: their Discord @mention
    assert "📺 **\\_Bob\\_** · <t:1759995000:R>" in description  # not linked: their Plex name, escaped


def test_history_stays_private_by_default(monkeypatch):
    reply, tautulli = _render(monkeypatch, enabled=False)
    assert reply.embeds[0].description == "Nobody's watching right now.\n-# Hit Refresh to check again."
    assert tautulli.history_calls == []


def test_unavailable_or_empty_history_falls_back_to_the_plain_message(monkeypatch):
    for kwargs in ({"reachable": False}, {"history": []}, {"history": [IGNORED]}):
        reply, _ = _render(monkeypatch, enabled=True, **kwargs)
        assert reply.embeds[0].description == "Nobody's watching right now.\n-# Hit Refresh to check again."
