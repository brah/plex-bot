"""Shared test fakes: Tautulli session payloads and nextcord dispatch helpers."""

import asyncio
import io
from types import SimpleNamespace

import nextcord
from PIL import Image

from tautulli_wrapper import Tautulli

MOVIE_PLAYING = {
    "session_id": "abc", "session_key": "1", "media_type": "movie", "state": "playing",
    "friendly_name": "Alice", "username": "alice", "user_thumb": "https://plex.tv/users/1/avatar",
    "title": "The Matrix", "full_title": "The Matrix", "year": "1999", "rating_key": "101",
    "thumb": "/library/metadata/101/thumb/1", "art": "/library/metadata/101/art/1",
    "bif_thumb": "/library/parts/55/indexes/sd/600000", "indexes": 1,
    "view_offset": "600000", "duration": "8160000",
    "transcode_decision": "direct play", "video_decision": "direct play", "audio_decision": "direct play",
    "video_full_resolution": "1080p", "stream_video_full_resolution": "1080p", "video_codec": "h264",
    "audio_codec": "eac3", "audio_channel_layout": "5.1(side)",
    "player": "Living Room", "product": "Plex for Roku", "platform": "Roku",
    "location": "lan", "bandwidth": "12000",
}

EPISODE_PAUSED = {
    "session_id": "def", "session_key": "2", "media_type": "episode", "state": "paused",
    "friendly_name": "Bob", "username": "bob", "user_thumb": "",
    "title": "Hello, Ms. Cobel", "grandparent_title": "Severance", "parent_media_index": "1",
    "media_index": "2", "year": "2022", "rating_key": "202",
    "thumb": "/library/metadata/202/thumb/1", "grandparent_thumb": "/library/metadata/200/thumb/1",
    "art": "/library/metadata/200/art/1", "bif_thumb": "", "indexes": 0,
    "view_offset": "100000", "duration": "3000000",
    "transcode_decision": "transcode", "video_decision": "transcode", "audio_decision": "copy",
    "video_full_resolution": "4k", "stream_video_full_resolution": "1080p", "video_codec": "hevc",
    "audio_codec": "truehd", "audio_channel_layout": "7.1",
    "subtitle_codec": "srt", "subtitle_language": "English", "subtitle_decision": "burn",
    "player": "Bob's iPhone", "product": "Plex for iOS", "platform": "iOS",
    "location": "wan", "bandwidth": "8000",
}

TRACK_PLAYING = {
    "session_id": "ghi", "media_type": "track", "state": "playing", "friendly_name": "Cara",
    "username": "cara", "title": "Bye Bye Bye", "grandparent_title": "*NSYNC",
    "parent_thumb": "/library/metadata/300/thumb/1", "thumb": "/library/metadata/301/thumb/1",
    "view_offset": "60000", "duration": "200000", "transcode_decision": "direct play",
    "audio_codec": "flac", "audio_channel_layout": "stereo", "player": "Desktop", "location": "lan",
}

LIVE_TV = {
    "session_id": "jkl", "media_type": "clip", "state": "playing", "username": "dan",
    "title": "News at Ten", "view_offset": "0", "duration": "0", "thumb": "",
    "transcode_decision": "", "location": "wan",
}

MARKDOWN_TITLE = dict(MOVIE_PLAYING, session_id="mno", friendly_name="_eve_", title="**Bold** _Live_")


def many_sessions(n: int) -> list:
    return [
        dict(MOVIE_PLAYING, session_id=f"s{i}", friendly_name=f"User {i}", username=f"user{i}",
             title=f"Film {i}")
        for i in range(n)
    ]


GUILD_ID = 123
MESSAGE_ID = 5555


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (20, 30), (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


class FakeTautulli:
    """Stands in for tautulli_wrapper.Tautulli; responses use Tautulli's envelope."""

    tautulli_ip, use_https, api_key = "host:8181", False, "key"

    def __init__(self, sessions=None, reachable=True):
        self.sessions = list(sessions or [])
        self.reachable = reachable
        self.terminated = []
        self.history, self.history_reachable, self.history_calls = [], True, []

    def _ok(self, data):
        return {"response": {"result": "success", "data": data}} if self.reachable else None

    async def get_activity(self):
        bandwidth = sum(int(s.get("bandwidth") or 0) for s in self.sessions)
        return self._ok({"sessions": self.sessions, "stream_count": len(self.sessions),
                         "total_bandwidth": bandwidth})

    async def get_server_info(self):
        return self._ok({"pms_identifier": "srv1", "pms_name": "Home", "pms_version": "1.41.0",
                         "pms_platform": "Linux", "pms_plexpass": 1})

    async def get_history(self, params=None):
        self.history_calls.append(params)
        if not self.history_reachable:
            return None
        rows = self.history
        if params and "start" in params:  # a paged request (Tautulli.get_history_rows)
            rows = rows[params["start"]:params["start"] + params["length"]]
        return self._ok({"data": rows})

    get_history_rows = Tautulli.get_history_rows
    get_library_media_rows = Tautulli.get_library_media_rows
    _all_rows = Tautulli._all_rows
    server_id = Tautulli.server_id
    _pms_identifier = None
    check_response = staticmethod(Tautulli.check_response)
    get_response_data = staticmethod(Tautulli.get_response_data)

    async def terminate_session(self, session_key=None, session_id=None, message=None):
        self.terminated.append((session_id, message))
        self.sessions = [s for s in self.sessions if s.get("session_id") != session_id]
        return 200

    # Library data for the media commands: metadata by rating key, plus per-item stats.
    metadata: dict = {}
    user_stats: dict = {}
    watch_time: dict = {}
    libraries: list = []
    library_items: dict = {}  # section_id -> rows
    recently_added: list = []
    recent_calls: list = []

    async def get_metadata(self, rating_key):
        return self._ok(self.metadata.get(str(rating_key), {}))

    async def get_item_user_stats(self, rating_key, params=None):
        return self._ok(self.user_stats.get(str(rating_key), []))

    async def get_item_watch_time_stats(self, rating_key, params=None):
        return self._ok(self.watch_time.get(str(rating_key), []))

    async def get_libraries(self):
        return self._ok(self.libraries)

    async def get_library_media_info(self, section_id=None, search=None, length=50, start=0, **kwargs):
        rows = self.library_items.get(str(section_id), [])
        if search:
            rows = [r for r in rows if search.lower() in r.get("title", "").lower()]
        return self._ok({"data": rows[start:start + length]})

    # Stats data: home stats by stat_id, library tables and users.
    home_stats: dict = {}
    home_stats_calls: list = []
    libraries_table: list = []
    library_user_stats: dict = {}  # section_id -> rows
    users: list = []

    async def get_home_stats(self, params=None):
        self.home_stats_calls = self.home_stats_calls + [params]
        return self._ok({"rows": self.home_stats.get(params["stat_id"], [])})

    async def get_libraries_table(self):
        return self._ok({"data": self.libraries_table})

    async def get_library_user_stats(self, section_id=None):
        return self._ok(self.library_user_stats.get(str(section_id), []))

    async def get_users(self):
        return self._ok(self.users)

    settings: dict = {"default_timezone": "UTC"}

    async def get_settings(self):
        return self._ok(self.settings)

    async def get_recently_added(self, count, media_type=None):
        self.recent_calls = self.recent_calls + [(count, media_type)]
        return self._ok({"recently_added": self.recently_added[:count]})


def patch_images(monkeypatch):
    """Make every Plex image fetch return a tiny valid PNG instead of hitting the network."""

    async def fake_image(self, key, width=300, height=450, fallback=None):
        return _png() if key else None

    monkeypatch.setattr(FakeTautulli, "get_image", fake_image, raising=False)


class FakeMessage:
    def __init__(self, recorder, message_id=MESSAGE_ID):
        self.id = message_id
        self.recorder = recorder

    async def edit(self, **kwargs):
        self.recorder.calls.append(("message.edit", kwargs))


class Recorder:
    """Replaces Discord-facing interaction methods and records what the bot sent."""

    def __init__(self, monkeypatch):
        self.calls = []
        recorder = self

        async def defer(self, *, ephemeral=False, with_message=False):
            recorder.calls.append(("defer", {"ephemeral": ephemeral, "with_message": with_message}))

        def is_done(self):
            return any(kind in ("defer", "send_message", "edit_message", "send_modal")
                       for kind, _ in recorder.calls)

        async def send_message(self, content=None, **kwargs):
            recorder.calls.append(("send_message", dict(kwargs, content=content)))
            return nextcord.PartialInteractionMessage(self._parent)  # as nextcord returns

        async def edit_message(self, **kwargs):
            recorder.calls.append(("edit_message", kwargs))

        async def send_modal(self, modal):
            recorder.calls.append(("send_modal", {"modal": modal}))

        async def edit_original_message(self, **kwargs):
            recorder.calls.append(("edit_original_message", kwargs))

        async def delete_original_message(self, **kwargs):
            recorder.calls.append(("delete_original_message", {}))

        async def clicked_message_edit(self, **kwargs):
            recorder.calls.append(("clicked_message.edit", dict(kwargs, message=self)))

        class Followup:
            async def send(self, content=None, **kwargs):
                recorder.calls.append(("followup", dict(kwargs, content=content)))
                return FakeMessage(recorder, message_id=6666)

        monkeypatch.setattr(nextcord.InteractionResponse, "defer", defer)
        monkeypatch.setattr(nextcord.InteractionResponse, "is_done", is_done)
        monkeypatch.setattr(nextcord.InteractionResponse, "send_message", send_message)
        monkeypatch.setattr(nextcord.InteractionResponse, "edit_message", edit_message)
        monkeypatch.setattr(nextcord.InteractionResponse, "send_modal", send_modal)
        monkeypatch.setattr(nextcord.Interaction, "edit_original_message", edit_original_message)
        monkeypatch.setattr(nextcord.Interaction, "delete_original_message", delete_original_message)
        monkeypatch.setattr(nextcord.Message, "edit", clicked_message_edit)
        monkeypatch.setattr(nextcord.Interaction, "followup", property(lambda self: Followup()))

    def kinds(self):
        return [kind for kind, _ in self.calls]

    def last(self, kind):
        return next(kwargs for k, kwargs in reversed(self.calls) if k == kind)


def build_bot(tautulli):
    """A PlexBot with ServerCommands loaded the way setup_hook does, without connecting."""
    from cogs.server_commands import ServerCommands
    from plexbot import PlexBot, install_slash_groups

    bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None,
                  default_guild_ids=[GUILD_ID])
    bot.shared_resources = {"tautulli": tautulli}
    bot._connection.application_id = 999
    install_slash_groups(bot)
    cog = ServerCommands(bot)
    cog.status_task.cancel()
    bot.add_cog(cog)
    return bot, cog


def build_media_bot(tautulli, items=()):
    """A PlexBot with MediaCommands loaded and a fresh media cache holding `items`."""
    from datetime import datetime

    from cogs.media_commands import MediaCommands
    from media_cache import MediaCache
    from plexbot import PlexBot, install_slash_groups

    cache = MediaCache(tautulli)
    cache.media_items = {str(item["rating_key"]): item for item in items}
    cache.last_updated = datetime.now()
    bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None,
                  default_guild_ids=[GUILD_ID])
    bot.shared_resources = {"tautulli": tautulli, "media_cache": cache}
    bot._connection.application_id = 999
    install_slash_groups(bot)
    cog = MediaCommands(bot)
    bot.add_cog(cog)
    return bot, cog


def build_stats_bot(tautulli):
    """A PlexBot with PlexStats loaded."""
    from cogs.plex_stats import PlexStats
    from plexbot import PlexBot, install_slash_groups

    bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None,
                  default_guild_ids=[GUILD_ID])
    bot.shared_resources = {"tautulli": tautulli}
    bot._connection.application_id = 999
    install_slash_groups(bot)
    cog = PlexStats(bot)
    bot.add_cog(cog)
    return bot, cog


def interaction(bot, **kwargs):
    """A component-style interaction from user 42; good enough for slash command bodies too."""
    return nextcord.Interaction(data=component_payload("cmd", **kwargs), state=bot._connection)


def member_payload(user_id=42, permissions="0", roles=()):
    return {
        "user": {"id": str(user_id), "username": f"user{user_id}", "discriminator": "0", "avatar": None},
        "roles": [str(r) for r in roles], "joined_at": "2020-01-01T00:00:00+00:00",
        "deaf": False, "mute": False, "permissions": permissions, "flags": 0,
    }


def component_payload(custom_id, *, component_type=2, values=None, member=None, message_id=MESSAGE_ID):
    """A button (type 2) or select (type 3) click as Discord sends it over the gateway."""
    data = {"custom_id": custom_id, "component_type": component_type}
    if values is not None:
        data["values"] = values
    return {
        "id": "1", "application_id": "999", "type": 3, "token": "tok", "version": 1,
        "guild_id": str(GUILD_ID), "channel_id": "777", "locale": "en-US", "app_permissions": "0",
        "entitlements": [], "authorizing_integration_owners": {},
        "member": member or member_payload(),
        "message": {
            "id": str(message_id), "channel_id": "777", "type": 0, "content": "",
            "author": {"id": "999", "username": "bot", "discriminator": "0", "avatar": None},
            "attachments": [], "embeds": [], "mentions": [], "mention_roles": [], "pinned": False,
            "mention_everyone": False, "tts": False, "timestamp": "2026-10-14T00:00:00+00:00",
            "edited_timestamp": None, "flags": 0, "components": [],
        },
        "data": data,
    }


async def settle():
    """Let nextcord's dispatched view/modal callback tasks run."""
    for _ in range(5):
        await asyncio.sleep(0.01)


# Re-exported for tests that need a plain object with guild permissions.
def permissions_user(admin=False, user_id=42, role_ids=()):
    return SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=r) for r in role_ids],
                           guild_permissions=SimpleNamespace(administrator=admin))
