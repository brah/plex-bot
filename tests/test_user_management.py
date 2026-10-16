"""/plexadmin users, link, unlink and ignore against a temporary map.json and fake Tautulli users."""

import asyncio
import json
from types import SimpleNamespace

import nextcord
import pytest

from cogs.user_management import UserManagement, users_card
from fakes import GUILD_ID, FakeTautulli, Recorder, interaction
from utilities import UserMappings

USERS = [
    {"user_id": 1, "username": "alice", "friendly_name": "Alice"},
    {"user_id": 2, "username": "bob", "friendly_name": "bob"},
    {"user_id": 3, "username": "mallory", "friendly_name": "Mallory"},
]


@pytest.fixture(autouse=True)
def map_file(tmp_path, monkeypatch):
    path = tmp_path / "map.json"
    path.write_text(json.dumps([
        {"plex_username": "alice", "discord_id": "11"},
        {"plex_username": "mallory", "discord_id": "", "ignore": True},
        {"plex_username": "gone", "discord_id": "99"},
    ]), encoding="utf-8")
    monkeypatch.setattr(UserMappings, "_mapping_file", str(path))
    monkeypatch.setattr(UserMappings, "_mappings", None)
    monkeypatch.setattr(UserMappings, "_unreadable", False)
    monkeypatch.setattr(UserMappings, "_unreadable_version", None)
    return path


def _run(fn, tautulli=None):
    from plexbot import PlexBot, install_slash_groups

    if tautulli is None:
        tautulli = FakeTautulli([])
        tautulli.users = USERS

    async def run():
        bot = PlexBot(command_prefix="plex ", intents=nextcord.Intents.default(), help_command=None,
                      default_guild_ids=[GUILD_ID])
        bot.shared_resources = {"tautulli": tautulli}
        bot._connection.application_id = 999
        install_slash_groups(bot)
        cog = UserManagement(bot)
        bot.add_cog(cog)
        return await fn(bot, cog)

    return asyncio.run(run())


def _sent(recorder):
    sent = recorder.last("followup")
    assert sent["ephemeral"] is True  # everything here is private
    return sent["embeds"][0].description


def test_users_card_lists_links_ignores_and_stale_entries():
    embed = users_card(USERS, UserMappings.load_user_mappings(), unreadable=False)
    blocks = embed.description.split("\n\n")
    assert blocks[0] == "**3 Plex users** · 1 linked · 1 ignored"
    assert blocks[1].split("\n") == [
        "**Alice (alice)** · → <@11>", "**bob** · not linked", "**Mallory (mallory)** · not linked · 🙈 ignored",
    ]
    assert blocks[2] == "-# In map.json but not on the Plex server: gone"


def test_users_card_warns_when_map_json_is_unreadable():
    embed = users_card(USERS, [], unreadable=True)
    assert embed.description.startswith("⚠️ map.json couldn't be read")


def test_users_command(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(lambda bot, cog: UserManagement.users_list.callback(cog, interaction(bot)))
    assert "**3 Plex users**" in _sent(recorder)


def test_link_validates_against_tautulli_and_moves_links(monkeypatch, map_file):
    recorder = Recorder(monkeypatch)
    member = SimpleNamespace(id=11, display_name="Ali")
    _run(lambda bot, cog: UserManagement.link.callback(cog, interaction(bot), plex_user="BOB", member=member))
    description = _sent(recorder)
    assert description.startswith("✅ Linked **bob** to <@11>.")
    assert "<@11> was linked to **alice** before; that link was removed." in description
    stored = {m["plex_username"]: m["discord_id"] for m in json.loads(map_file.read_text(encoding="utf-8"))}
    assert stored == {"bob": "11", "mallory": "", "gone": "99"}


def test_link_unknown_plex_user(monkeypatch, map_file):
    recorder = Recorder(monkeypatch)
    before = map_file.read_text(encoding="utf-8")
    _run(lambda bot, cog: UserManagement.link.callback(
        cog, interaction(bot), plex_user="nobody", member=SimpleNamespace(id=5, display_name="X")))
    assert "No Plex user called **nobody**" in _sent(recorder)
    assert map_file.read_text(encoding="utf-8") == before


def test_link_refused_when_map_json_is_unreadable(monkeypatch, map_file):
    recorder = Recorder(monkeypatch)
    map_file.write_text("{oops", encoding="utf-8")
    _run(lambda bot, cog: UserManagement.link.callback(
        cog, interaction(bot), plex_user="bob", member=SimpleNamespace(id=5, display_name="X")))
    assert "map.json couldn't be read" in _sent(recorder)
    assert map_file.read_text(encoding="utf-8") == "{oops"


def test_unlink(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(lambda bot, cog: UserManagement.unlink.callback(cog, interaction(bot), plex_user="alice"))
    assert _sent(recorder) == "✅ Unlinked **alice** from <@11>."
    _run(lambda bot, cog: UserManagement.unlink.callback(cog, interaction(bot), plex_user="alice"))
    assert "isn't linked to anyone" in _sent(recorder)


def test_ignore_toggles_and_works_for_users_who_left(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(lambda bot, cog: UserManagement.ignore.callback(cog, interaction(bot), plex_user="mallory"))
    assert _sent(recorder) == "✅ **Mallory (mallory)** is no longer ignored."
    _run(lambda bot, cog: UserManagement.ignore.callback(cog, interaction(bot), plex_user="gone"))
    assert "**gone** is now ignored" in _sent(recorder)


def test_tautulli_down(monkeypatch):
    recorder = Recorder(monkeypatch)
    _run(lambda bot, cog: UserManagement.ignore.callback(cog, interaction(bot), plex_user="bob"),
         FakeTautulli([], reachable=False))
    assert "Couldn't reach **Tautulli**" in _sent(recorder)


def test_autocomplete_labels_and_unlink_offers_only_linked_users():
    async def run(bot, cog):
        return await cog._choices(""), await cog._choices("", linked_only=True), await cog._choices("mal")

    every, linked, typed = _run(run)
    assert every == {"Alice (alice) · linked": "alice", "bob": "bob", "Mallory (mallory) · ignored": "mallory"}
    assert linked == {"Alice (alice) · linked": "alice"}
    assert typed == {"Mallory (mallory) · ignored": "mallory"}


def test_admin_commands_registered_and_old_names_redirect():
    from errors import slash_equivalent
    from slash_commands import command_paths

    async def run(bot, cog):
        return [path for path, _command in command_paths(bot)]

    paths = _run(run)
    assert {"/plexadmin users", "/plexadmin link", "/plexadmin unlink", "/plexadmin ignore"} <= set(paths)
    assert slash_equivalent("mapdiscord", paths) == "/plexadmin link"
    assert slash_equivalent("ignore", paths) == "/plexadmin ignore"
