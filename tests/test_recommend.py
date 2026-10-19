"""/plex recommend: genre analysis, picks, the overview and its author-only buttons."""

import asyncio
from datetime import datetime

import nextcord
import pytest

from plexbot import utilities
from plexbot.cogs.recommendations import (
    RecommendationView, Recommendations, favourite_genres, overview_card, watched_keys,
)
from plexbot.media_details import item_viewers
from fakes import (
    GUILD_ID, MESSAGE_ID, FakeTautulli, Recorder, component_payload, interaction, member_payload, patch_images,
    settle,
)
from plexbot.media_cache import MediaCache

HISTORY = [
    {"rating_key": "11", "parent_rating_key": "10", "grandparent_rating_key": "1"},  # an episode of show 1
    {"rating_key": "12", "parent_rating_key": "10", "grandparent_rating_key": "1"},
    {"rating_key": "2"},  # a movie
]
CACHE = {
    "1": {"rating_key": "1", "title": "Severance", "media_type": "show", "genres": ["drama", "science fiction"]},
    "2": {"rating_key": "2", "title": "Heat", "media_type": "movie", "genres": ["crime", "drama"]},
    "3": {"rating_key": "3", "title": "Arrival", "year": "2016", "media_type": "movie",
          "genres": ["drama", "science fiction"], "summary": "Linguist meets *aliens*.", "thumb": "/t/3"},
    "4": {"rating_key": "4", "title": "Collateral", "year": "2004", "media_type": "movie", "genres": ["crime"],
          "thumb": "/t/4"},
    "5": {"rating_key": "5", "title": "Bluey", "media_type": "show", "genres": ["animation"]},
}


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings", staticmethod(lambda: [
        {"discord_id": "42", "plex_username": "alice"},
        {"plex_username": "mallory", "ignore": True},
    ]))
    patch_images(monkeypatch)


class TestPicking:
    def test_watched_keys_include_season_and_show(self):
        assert watched_keys(HISTORY) == {"1", "2", "10", "11", "12"}

    def test_favourite_genres_count_each_item_once(self):
        assert favourite_genres(HISTORY, CACHE.get) == ["drama", "science fiction", "crime"]

    def test_viewers_skip_ignored_and_unplayed_most_plays_first(self):
        rows = [{"username": "bob", "total_plays": 2}, {"username": "mallory", "total_plays": 9},
                {"username": "zed", "total_plays": 0}, {"username": "cara", "total_plays": "5"}]
        assert [row["username"] for row in item_viewers(rows, {"mallory"})] == ["cara", "bob"]

    def test_overview(self):
        embed = overview_card([CACHE["3"], CACHE["4"]], [2, 0], ["drama", "science fiction"], strip=None)
        blocks = embed.description.split("\n\n")
        assert blocks[0] == "-# Based on their favourite genres: **Drama**, **Science fiction**"
        assert blocks[1] == ("**1. Arrival (2016)** · 🎬\n-# Drama, Science fiction · watched by 2 people\n"
                             "Linguist meets \\*aliens\\*.")
        assert blocks[2].split("\n")[1] == "-# Crime · nobody's watched it yet"


def _build(tautulli):
    from plexbot.bot import PlexBot, install_slash_groups

    cache = MediaCache(tautulli)
    cache.media_items = dict(CACHE)
    cache.last_updated = datetime.now()
    bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None,
                  default_guild_ids=[GUILD_ID])
    bot.shared_resources = {"tautulli": tautulli, "media_cache": cache}
    bot._connection.application_id = 999
    install_slash_groups(bot)
    cog = Recommendations(bot)
    bot.add_cog(cog)
    return bot, cog


def _tautulli():
    tautulli = FakeTautulli([])
    tautulli.history = HISTORY
    tautulli.metadata = {key: dict(item) for key, item in CACHE.items()}
    tautulli.user_stats = {"3": [{"username": "bob", "total_plays": 1}]}
    return tautulli


def _run(fn, tautulli):
    async def run():
        bot, cog = _build(tautulli)
        return await fn(bot, cog)

    return asyncio.run(run())


class TestCommand:
    def test_recommends_unwatched_titles_in_favourite_genres(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = _tautulli()
        _run(lambda bot, cog: Recommendations.recommend.callback(cog, interaction(bot), member=None), tautulli)
        assert tautulli.history_calls[-1]["user"] == "alice"
        sent = recorder.last("followup")
        embed = sent["embeds"][0]
        assert embed.title == "🍿 Recommendations" and sent["ephemeral"] is False
        assert "Arrival" in embed.description and "Collateral" in embed.description
        assert "Bluey" not in embed.description and "Heat" not in embed.description  # off-genre / watched
        assert [f.filename for f in sent["files"]] == ["posters.png"]
        view = sent["view"]
        assert isinstance(view, RecommendationView) and view.author_id == 42
        assert [c.label for c in view.children] == ["1", "2", "Back to list"]

    def test_unlinked_member_is_private(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        monkeypatch.setattr(utilities.UserMappings, "load_user_mappings", staticmethod(lambda: []))
        _run(lambda bot, cog: Recommendations.recommend.callback(cog, interaction(bot), member=None), _tautulli())
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True and "isn't linked to a Plex account" in sent["embeds"][0].description

    def test_no_history(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = _tautulli()
        tautulli.history = []
        _run(lambda bot, cog: Recommendations.recommend.callback(cog, interaction(bot), member=None), tautulli)
        assert "hasn't watched anything yet" in recorder.last("followup")["embeds"][0].description

    def test_tautulli_down(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: Recommendations.recommend.callback(cog, interaction(bot), member=None),
             FakeTautulli([], reachable=False))
        assert "Couldn't reach **Tautulli**" in recorder.last("followup")["embeds"][0].description


class TestButtons:
    def _view(self, bot, cog):
        overview = overview_card([CACHE["3"], CACHE["4"]], [1, 0], ["drama"], strip=None)
        from plexbot.ui.embeds import Reply

        view = RecommendationView(cog.details, 42, [CACHE["3"], CACHE["4"]], Reply([overview]), timeout=60)
        bot._connection.store_view(view, MESSAGE_ID)
        return view

    def test_author_opens_a_pick_and_goes_back(self, monkeypatch):
        recorder = Recorder(monkeypatch)

        async def run(bot, cog):
            view = self._view(bot, cog)
            bot._connection.parse_interaction_create(component_payload(view.children[0].custom_id))
            await settle()
            assert view.back_button.disabled is False
            bot._connection.parse_interaction_create(component_payload(view.back_button.custom_id))
            await settle()
            return view

        view = _run(run, _tautulli())
        edits = [kwargs for kind, kwargs in recorder.calls if kind == "edit_original_message"]
        assert edits[0]["embeds"][0].title == "Arrival (2016)"
        assert edits[0]["embeds"][0].footer.text == "Recommendation 1 of 2"
        assert edits[1]["embeds"][0].title == "🍿 Recommendations" and view.back_button.disabled is True

    def test_others_get_a_private_hint(self, monkeypatch):
        recorder = Recorder(monkeypatch)

        async def run(bot, cog):
            view = self._view(bot, cog)
            bot._connection.parse_interaction_create(
                component_payload(view.children[1].custom_id, member=member_payload(user_id=7)))
            await settle()

        _run(run, _tautulli())
        kind, sent = recorder.calls[0]
        assert kind == "send_message" and sent["ephemeral"] is True
        assert "Only <@42> can browse these picks." in sent["embeds"][0].description
        assert "edit_original_message" not in recorder.kinds()


def test_registered_in_help():
    from plexbot.cogs.utility_commands import collect_help_entries

    async def run(bot, cog):
        return collect_help_entries(bot)

    entries = {e.usage: e.category for e in _run(run, FakeTautulli([]))}
    assert entries["/plex recommend [member]"] == "library"
    assert not any(usage.startswith("plex recommend") for usage in entries)
