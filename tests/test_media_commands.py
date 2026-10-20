"""/plex movie, tv, random, recent, downloading and /plexadmin cache, end to end against fakes."""

import asyncio
from types import SimpleNamespace

import pytest

import plexbot.cogs.media_commands as media_commands
from plexbot import utilities
from plexbot.cogs.media_commands import (
    CacheView, DownloadsView, MediaCommands, RandomView, RecentDetailsSelect, RecentView,
)
from fakes import (
    FakeTautulli, Recorder, build_media_bot, component_payload, interaction, member_payload, patch_images,
    settle, MESSAGE_ID,
)

MATRIX = {"rating_key": "101", "media_type": "movie", "title": "The Matrix", "year": "1999",
          "summary": "A hacker learns the truth.", "thumb": "/library/metadata/101/thumb/1",
          "art": "/library/metadata/101/art/1", "genres": ["Action"]}
HEAT = {"rating_key": "102", "media_type": "movie", "title": "Heat", "year": "1995", "thumb": "/t/102"}
SEVERANCE = {"rating_key": "200", "media_type": "show", "title": "Severance", "year": "2022", "thumb": "/t/200"}
CACHE = [
    dict(MATRIX, genres=["action", "science fiction"]),
    dict(HEAT, genres=["crime", "action"]),
    dict(SEVERANCE, genres=["drama", "science fiction"]),
    {"rating_key": "103", "media_type": "movie", "title": "Mad Max", "year": "1979", "genres": ["action"]},
]


@pytest.fixture(autouse=True)
def no_mappings(monkeypatch):
    monkeypatch.setattr(utilities.UserMappings, "load_user_mappings",
                        staticmethod(lambda: [{"plex_username": "mallory", "ignore": True}]))
    patch_images(monkeypatch)


def _tautulli(**kwargs):
    tautulli = FakeTautulli([], **kwargs)
    tautulli.metadata = {"101": MATRIX, "102": HEAT, "200": SEVERANCE}
    tautulli.user_stats = {"101": [{"username": "alice", "friendly_name": "Alice", "total_plays": 3},
                                   {"username": "mallory", "friendly_name": "Mallory", "total_plays": 8}]}
    tautulli.watch_time = {"101": [{"query_days": 0, "total_time": 7200, "total_plays": 11}]}
    tautulli.libraries = [{"section_id": "1", "section_type": "movie"}, {"section_id": "2", "section_type": "show"}]
    tautulli.library_items = {"1": [MATRIX, HEAT], "2": [SEVERANCE]}
    return tautulli


def _run(coro_fn, tautulli=None, items=CACHE):
    async def run():
        bot, cog = build_media_bot(tautulli or _tautulli(), items)
        return await coro_fn(bot, cog)

    return asyncio.run(run())


class TestLookup:
    def test_picked_suggestion_renders_public_card_with_stats(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: MediaCommands.movie.callback(cog, interaction(bot), title="key:101"))
        assert recorder.kinds() == ["defer", "followup"]
        sent = recorder.last("followup")
        assert sent["ephemeral"] is False
        embed = sent["embeds"][0]
        assert embed.title == "The Matrix (1999)"
        assert embed.url.startswith("https://app.plex.tv/desktop/#!/server/srv1/details")
        assert "▶ **11 plays** · 2h watched" in embed.description
        assert "**Alice** (3)" in embed.description
        assert "Mallory" not in embed.description  # ignored user
        assert [f.filename for f in sent["files"]] == ["poster.jpg", "art.jpg"]

    def test_typed_key_outside_the_cache_is_refused(self, monkeypatch):
        # Rating keys count up, so a typed key could reach any Plex item (photos, home videos).
        recorder = Recorder(monkeypatch)
        tautulli = _tautulli()
        tautulli.metadata = {**tautulli.metadata, "999": {"title": "Holiday photos"}}
        _run(lambda bot, cog: MediaCommands.movie.callback(cog, interaction(bot), title="key:999"), tautulli)
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True
        assert "isn't in the library" in sent["embeds"][0].description

    def test_typed_title_found_in_its_library_only(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: MediaCommands.tv.callback(cog, interaction(bot), title="severance"))
        assert recorder.last("followup")["embeds"][0].title == "Severance (2022)"
        _run(lambda bot, cog: MediaCommands.movie.callback(cog, interaction(bot), title="Severance"))
        # Only movie libraries are searched; the cache has no movie called that either.
        assert "No movie matching **Severance**" in recorder.last("followup")["embeds"][0].description

    def test_unknown_title_is_a_private_error_replacing_the_placeholder(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: MediaCommands.movie.callback(cog, interaction(bot), title="Nope"))
        assert recorder.kinds() == ["defer", "delete_original_message", "followup"]
        assert recorder.last("followup")["ephemeral"] is True

    def test_tautulli_down(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: MediaCommands.movie.callback(cog, interaction(bot), title="Heat"),
             tautulli=_tautulli(reachable=False))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True
        assert "Couldn't reach **Tautulli**" in sent["embeds"][0].description

    def test_title_suggestions_prefix_first_and_by_kind(self):
        async def suggest(bot, cog):
            return cog._title_suggestions("ma", "movie"), cog._title_suggestions("", "tv")

        movies, shows = _run(suggest)
        assert list(movies) == ["Mad Max (1979)", "The Matrix (1999)"]  # prefix match, then contains
        assert shows == {"Severance (2022)": "key:200"}


class TestRandom:
    def test_pick_from_filtered_pool_with_roll_again(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: MediaCommands.random_pick.callback(cog, interaction(bot), kind="movie",
                                                                 genre="Science Fiction"))
        sent = recorder.last("followup")
        assert sent["embeds"][0].title == "The Matrix (1999)"
        assert sent["embeds"][0].footer.text == "🎲 Random pick from 1 match"
        assert isinstance(sent["view"], RandomView)
        assert sent["view"].author_id == 42

    def test_no_match_is_private(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: MediaCommands.random_pick.callback(cog, interaction(bot), kind="tv", genre="crime"))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True
        assert sent["embeds"][0].description.startswith("No TV shows tagged **crime** in the library.")

    def test_only_the_author_can_roll_again(self, monkeypatch):
        recorder = Recorder(monkeypatch)

        async def run(bot, cog):
            view = RandomView(cog, 42, [CACHE[0], CACHE[1]])
            bot._connection.store_view(view, MESSAGE_ID)
            stranger = member_payload(user_id=7)
            bot._connection.parse_interaction_create(component_payload(view.roll_again.custom_id, member=stranger))
            await settle()
            bot._connection.parse_interaction_create(component_payload(view.roll_again.custom_id))
            await settle()

        _run(run)
        assert recorder.calls[0][0] == "send_message"
        assert recorder.calls[0][1]["ephemeral"] is True
        assert "Only <@42> can re-roll" in recorder.calls[0][1]["embeds"][0].description
        assert [k for k, _ in recorder.calls[1:]] == ["defer", "edit_original_message"]
        assert recorder.last("edit_original_message")["embeds"][0].footer.text.startswith("🎲 Random pick")

    def test_pick_does_not_repeat_until_the_pool_is_used(self):
        view = SimpleNamespace(pool=[1, 2, 3], _unseen=[])
        picks = [RandomView.pick(view) for _ in range(3)]
        assert sorted(picks) == [1, 2, 3]

    def test_genre_suggestions_follow_the_type(self, monkeypatch):
        recorder = Recorder(monkeypatch)

        async def run(bot, cog):
            sent = []

            async def send_autocomplete(self, choices):
                sent.append(choices)

            monkeypatch.setattr(type(interaction(bot).response), "send_autocomplete", send_autocomplete)
            await MediaCommands._genre_autocomplete(cog, interaction(bot), "sc", kind="tv")
            await MediaCommands._genre_autocomplete(cog, interaction(bot), "", kind=None)
            return sent

        tv, every = _run(run)
        assert tv == {"Science fiction": "science fiction"}
        assert list(every)[0] == "Action"  # most common first
        assert recorder.calls == []


class TestRecent:
    ENTRIES = [dict(MATRIX, added_at="1760000000"), dict(SEVERANCE, added_at="1759990000")]

    def test_overview_with_strip_and_details(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        tautulli = _tautulli()
        tautulli.recently_added = self.ENTRIES
        _run(lambda bot, cog: MediaCommands.recent.callback(cog, interaction(bot), count=5, kind="movie"),
             tautulli=tautulli)
        assert tautulli.recent_calls == [(5, "movie")]
        sent = recorder.last("followup")
        embed = sent["embeds"][0]
        assert embed.title == "Recently added"
        assert embed.description.split("\n")[0] == "🎬 **The Matrix (1999)** · added <t:1760000000:R>"
        assert [f.filename for f in sent["files"]] == ["posters.png"]
        select = next(c for c in sent["view"].children if isinstance(c, RecentDetailsSelect))
        assert [o.value for o in select.options] == ["101", "200"]

    def test_nothing_added(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        _run(lambda bot, cog: MediaCommands.recent.callback(cog, interaction(bot), count=None, kind=None))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is False
        assert "Nothing's been added recently." in sent["embeds"][0].description

    def test_picking_an_item_sends_its_card_privately(self, monkeypatch):
        recorder = Recorder(monkeypatch)

        async def run(bot, cog):
            view = RecentView(cog, self.ENTRIES)
            bot._connection.store_view(view, MESSAGE_ID)
            select = view.children[0]
            bot._connection.parse_interaction_create(
                component_payload(select.custom_id, component_type=3, values=["200"]))
            await settle()

        _run(run)
        assert recorder.calls[0] == ("defer", {"ephemeral": True, "with_message": True})
        detail = recorder.last("followup")
        assert detail["ephemeral"] is True
        assert detail["embeds"][0].title == "Severance (2022)"
        assert recorder.kinds()[-1] == "clicked_message.edit"  # dropdown reset for the next pick


class TestDownloading:
    def test_not_configured(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        monkeypatch.setattr(MediaCommands, "_qbit_client", staticmethod(lambda: None))
        _run(lambda bot, cog: MediaCommands.downloading.callback(cog, interaction(bot)))
        sent = recorder.last("followup")
        assert sent["ephemeral"] is True
        assert "qBittorrent isn't set up." in sent["embeds"][0].description

    def _client(self, torrents=(), fail=None, username="bot"):
        def downloading():
            if fail:
                raise fail
            return list(torrents)

        def auth_log_in():
            self.logins.append(username)

        self.logins = []
        return SimpleNamespace(username=username, auth_log_in=auth_log_in,
                               torrents=SimpleNamespace(info=SimpleNamespace(downloading=downloading)))

    def test_no_login_needed(self, monkeypatch):
        """qBittorrent can let clients in without a login; the bot then doesn't try one."""
        recorder = Recorder(monkeypatch)
        monkeypatch.setattr(MediaCommands, "_qbit_client", staticmethod(lambda: self._client([], username="")))
        _run(lambda bot, cog: MediaCommands.downloading.callback(cog, interaction(bot)))
        assert "Nothing's downloading" in recorder.last("followup")["embeds"][0].description
        assert self.logins == []

    def test_without_a_login(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        for error, text in ((ConnectionError("refused"), "qBittorrent isn't set up."),
                            (media_commands.qbittorrentapi.LoginFailed(), "qBittorrent wants a login.")):
            monkeypatch.setattr(MediaCommands, "_qbit_client",
                                staticmethod(lambda e=error: self._client(fail=e, username="")))
            _run(lambda bot, cog: MediaCommands.downloading.callback(cog, interaction(bot)))
            assert text in recorder.last("followup")["embeds"][0].description

    def test_only_the_address_is_required(self, monkeypatch):
        settings = {"ip": "192.168.1.10", "port": "8080", "username": "", "password": ""}
        monkeypatch.setattr(media_commands.config, "get", lambda section, key, default=None: settings[key])
        client = MediaCommands._qbit_client()
        assert client is not None and client.username == ""
        settings["ip"] = ""
        assert MediaCommands._qbit_client() is None

    def test_unreachable_and_bad_login(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        for error, text in ((ConnectionError("down"), "Couldn't reach **qBittorrent**"),
                            (media_commands.qbittorrentapi.LoginFailed(), "didn't accept the bot's login")):
            monkeypatch.setattr(MediaCommands, "_qbit_client", staticmethod(lambda e=error: self._client(fail=e)))
            _run(lambda bot, cog: MediaCommands.downloading.callback(cog, interaction(bot)))
            assert text in recorder.last("followup")["embeds"][0].description

    def test_downloads_with_refresh(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        torrent = SimpleNamespace(name="Film", progress=0.5, dlspeed=2_000_000, state="downloading", num_seeds=3,
                                  eta=60, size=1_000_000_000, state_enum=SimpleNamespace(is_paused=False))
        monkeypatch.setattr(MediaCommands, "_qbit_client", staticmethod(lambda: self._client([torrent])))
        _run(lambda bot, cog: MediaCommands.downloading.callback(cog, interaction(bot)))
        sent = recorder.last("followup")
        assert sent["embeds"][0].title == "Downloading"
        assert isinstance(sent["view"], DownloadsView)

    def test_nothing_downloading(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        monkeypatch.setattr(MediaCommands, "_qbit_client", staticmethod(lambda: self._client([])))
        _run(lambda bot, cog: MediaCommands.downloading.callback(cog, interaction(bot)))
        assert "Nothing's downloading" in recorder.last("followup")["embeds"][0].description


class TestCacheAdmin:
    def test_status_and_refresh(self, monkeypatch):
        recorder = Recorder(monkeypatch)
        refreshed = []

        async def run(bot, cog):
            async def refresh():
                refreshed.append(True)

            cog.media_cache.refresh = refresh
            await MediaCommands.cache.callback(cog, interaction(bot))
            view = recorder.last("send_message")["view"]
            bot._connection.store_view(view, MESSAGE_ID)
            bot._connection.parse_interaction_create(component_payload(view.refresh_now.custom_id))
            await settle()
            return view

        view = _run(run)
        status = recorder.last("send_message")
        assert status["ephemeral"] is True
        assert isinstance(view, CacheView)
        assert status["embeds"][0].description.startswith("**4 items** · 3 movies · 1 show")
        assert "new titles every 1h" in status["embeds"][0].description
        assert refreshed == [True]
        assert "Refreshing" in recorder.last("edit_message")["embeds"][0].description
        assert "✅ Refreshed" in recorder.last("edit_original_message")["embeds"][0].description


def test_media_commands_registered_and_in_help():
    from plexbot.cogs.utility_commands import collect_help_entries

    async def run(bot, cog):
        plex = next(c for c in bot._connection._application_commands if c.name == "plex")
        admin = next(c for c in bot._connection._application_commands if c.name == "plexadmin")
        return set(plex.children), set(admin.children), collect_help_entries(bot)

    plex_children, admin_children, entries = _run(run)
    assert {"movie", "tv", "random", "recent", "downloading"} <= plex_children
    assert "cache" in admin_children
    usages = {e.usage: e for e in entries}
    assert usages["/plex movie <title>"].category == "library"
    assert usages["/plex random [type] [genre]"].example == "/plex random type:TV show genre:Comedy"
    assert usages["/plexadmin cache"].category == "admin"
    assert not any(u.startswith("plex ") for u in usages)  # no prefix commands left in this cog
