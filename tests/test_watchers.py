"""Unit tests for /plex watchers stream management, slash command paths and hints, and terminate_session."""

import asyncio
import logging
from types import SimpleNamespace

import aiohttp
import nextcord
import pytest

from plexbot.cogs.server_commands import _can_manage_streams, _session_choices, _session_label
from plexbot.errors import invoked_command_path, slash_equivalent
from plexbot.slash_commands import admin_check, command_paths
from plexbot.tautulli_wrapper import Tautulli


def _member(member_id=1, role_ids=(), admin=False):
    return SimpleNamespace(
        id=member_id,
        roles=[SimpleNamespace(id=r) for r in role_ids],
        guild_permissions=SimpleNamespace(administrator=admin),
    )


GUILD = 123


@pytest.fixture
def configured(monkeypatch):
    """Set discord config values for one test: configured(server_id="123", admin_ids=[5])."""
    from plexbot.config import config

    def set_discord(**values):
        monkeypatch.setattr(config, "_user_config", {"discord": values})

    set_discord()
    return set_discord


class TestCanManageStreams:
    def test_admin_of_the_configured_server(self, configured):
        configured(server_id=str(GUILD))
        assert _can_manage_streams(_member(admin=True), GUILD, []) is True

    def test_admin_of_another_server_is_not_enough(self, configured):
        configured(server_id=str(GUILD))
        assert _can_manage_streams(_member(admin=True), 999, []) is False

    def test_without_server_id_discord_admin_is_not_enough(self, configured):
        assert _can_manage_streams(_member(admin=True), GUILD, []) is False

    def test_admin_ids_work_anywhere(self, configured):
        configured(admin_ids=["42"])
        assert _can_manage_streams(_member(member_id=42), 999, []) is True

    def test_listed_user(self, configured):
        assert _can_manage_streams(_member(member_id=42), GUILD, [42]) is True

    def test_listed_role(self, configured):
        assert _can_manage_streams(_member(role_ids=(7, 8)), GUILD, [8]) is True

    def test_string_ids_from_config(self, configured):
        assert _can_manage_streams(_member(member_id=42), GUILD, ["42"]) is True

    def test_not_listed(self, configured):
        assert _can_manage_streams(_member(member_id=1, role_ids=(7,)), GUILD, [42, 8]) is False

    def test_invalid_ids_ignored(self, configured):
        assert _can_manage_streams(_member(member_id=1), GUILD, ["", "abc", None]) is False

    def test_user_outside_guild(self, configured):
        # A plain User (no roles / guild permissions) can still be listed by ID.
        user = SimpleNamespace(id=42)
        assert _can_manage_streams(user, None, [42]) is True
        assert _can_manage_streams(user, None, []) is False


class TestAdminCheck:
    def _interaction(self, name, admin=False, guild_id=GUILD, user_id=1):
        return SimpleNamespace(data={"name": name}, guild_id=guild_id,
                               user=_member(member_id=user_id, admin=admin))

    def test_plex_commands_are_open(self, configured):
        assert admin_check(self._interaction("plex")) is True

    def test_plexadmin_needs_a_bot_admin(self, configured):
        configured(server_id=str(GUILD), admin_ids=[5])
        assert admin_check(self._interaction("plexadmin", admin=True)) is True
        assert admin_check(self._interaction("plexadmin", admin=True, guild_id=999)) is False
        assert admin_check(self._interaction("plexadmin", user_id=5, guild_id=999)) is True
        assert admin_check(self._interaction("plexadmin")) is False

    def test_refusal_is_answered_privately(self, monkeypatch, configured):
        from plexbot.ui import embeds
        from plexbot.errors import ErrorHandler

        monkeypatch.setattr(embeds, "mascot", lambda slot: None)
        sent = []

        class Response:
            def is_done(self):
                return False

            async def send_message(self, content=None, *, ephemeral=False, **kwargs):
                sent.append((kwargs["embeds"][0].description, ephemeral))

        interaction = SimpleNamespace(
            data={"name": "plexadmin", "options": [{"type": 1, "name": "logs"}]},
            user=SimpleNamespace(id=1), response=Response(),
        )
        error = nextcord.ApplicationCheckFailure("nope")
        asyncio.run(ErrorHandler(None).application_error_handler(interaction, error))
        assert sent == [(sent[0][0], True)]
        assert "`/plexadmin logs`" in sent[0][0]


SESSION_A = {"session_id": "abc", "friendly_name": "Alice", "full_title": "The Matrix", "username": "alice"}
SESSION_B = {"session_id": "def", "friendly_name": "Bob", "full_title": "Severance - Hello, Ms. Cobel"}


class TestSessionLabel:
    def test_basic(self):
        assert _session_label(SESSION_A) == "Alice — The Matrix"

    def test_fallbacks(self):
        assert _session_label({"username": "carol", "title": "Dune"}) == "carol — Dune"

    def test_truncated_to_discord_limit(self):
        label = _session_label({"friendly_name": "A", "full_title": "x" * 300})
        assert len(label) <= 100
        assert label.endswith("…")


class TestSessionChoices:
    def test_all_when_empty_query(self):
        assert _session_choices([SESSION_A, SESSION_B], "") == {
            "Alice — The Matrix": "abc",
            "Bob — Severance - Hello, Ms. Cobel": "def",
        }

    def test_filters_case_insensitive(self):
        assert _session_choices([SESSION_A, SESSION_B], "matrix") == {"Alice — The Matrix": "abc"}

    def test_duplicate_labels_disambiguated(self):
        twin = dict(SESSION_A, session_id="xyz")
        choices = _session_choices([SESSION_A, twin], "")
        assert sorted(choices.values()) == ["abc", "xyz"]
        assert len(choices) == 2

    def test_capped_at_25(self):
        sessions = [{"session_id": str(i), "friendly_name": f"U{i}", "full_title": "T"} for i in range(40)]
        assert len(_session_choices(sessions, "")) == 25

    def test_skips_sessions_without_id(self):
        assert _session_choices([{"friendly_name": "X", "full_title": "Y"}], "") == {}


class TestSlashEquivalent:
    paths = ["/plex status", "/plex watchers", "/plex chart hours", "/plex chart days", "/plexadmin logs"]

    def test_same_name(self):
        assert slash_equivalent("status", self.paths) == "/plex status"

    def test_case_insensitive(self):
        assert slash_equivalent("Status", self.paths) == "/plex status"

    def test_admin_group(self):
        assert slash_equivalent("logs", self.paths) == "/plexadmin logs"

    def test_renamed(self):
        assert slash_equivalent("killstream", self.paths) == "/plex watchers"

    def test_cache_commands_point_at_plexadmin_cache(self):
        paths = self.paths + ["/plexadmin cache"]
        for old in ("refresh_cache", "force_refresh_cache", "check_media_cache"):
            assert slash_equivalent(old, paths) == "/plexadmin cache"

    def test_nested_group_points_at_group(self):
        assert slash_equivalent("chart", self.paths) == "/plex chart"

    def test_unknown(self):
        assert slash_equivalent("nonsense", self.paths) is None

    def test_none(self):
        assert slash_equivalent(None, self.paths) is None


class TestInvokedCommandPath:
    def _interaction(self, data):
        return SimpleNamespace(data=data)

    def test_subcommand(self):
        data = {"name": "plex", "options": [{"type": 1, "name": "status", "options": []}]}
        assert invoked_command_path(self._interaction(data)) == "/plex status"

    def test_nested_group_with_value_options(self):
        data = {
            "name": "plex",
            "options": [
                {"type": 2, "name": "chart", "options": [
                    {"type": 1, "name": "hours", "options": [{"type": 4, "name": "days", "value": 30}]}
                ]}
            ],
        }
        assert invoked_command_path(self._interaction(data)) == "/plex chart hours"

    def test_flat_command(self):
        assert invoked_command_path(self._interaction({"name": "ping"})) == "/ping"

    def test_missing_data(self):
        assert invoked_command_path(self._interaction(None)) == "/command"


def _cmd(name, children=()):
    return SimpleNamespace(name=name, children={c.name: c for c in children})


class TestCommandPaths:
    def test_walks_groups_to_leaves(self):
        chart = _cmd("chart", [_cmd("hours"), _cmd("days")])
        plex = _cmd("plex", [_cmd("status"), chart])
        admin = _cmd("plexadmin", [_cmd("logs")])
        bot = SimpleNamespace(get_all_application_commands=lambda: [plex, admin])
        assert [path for path, _ in command_paths(bot)] == [
            "/plex status",
            "/plex chart hours",
            "/plex chart days",
            "/plexadmin logs",
        ]

    def test_flat_command_and_childless_types(self):
        flat = _cmd("ping")
        context_menu = SimpleNamespace(name="Report")  # message/user commands have no children
        bot = SimpleNamespace(get_all_application_commands=lambda: [flat, context_menu])
        assert [path for path, _ in command_paths(bot)] == ["/ping", "/Report"]


class TestTerminateSession:
    def _call(self, **kwargs):
        client = Tautulli(api_key="k", tautulli_ip="h:8181")
        captured = {}

        async def fake_api_call(cmd, params=None):
            captured["cmd"], captured["params"] = cmd, params
            return {"response": {"result": "success"}}

        client.api_call = fake_api_call
        status = asyncio.run(client.terminate_session(**kwargs))
        return status, captured

    def test_omits_missing_message(self):
        status, captured = self._call(session_id="abc")
        assert status == 200
        assert captured["params"] == {"session_id": "abc"}

    def test_includes_message(self):
        _, captured = self._call(session_id="abc", message="Bye")
        assert captured["params"] == {"message": "Bye", "session_id": "abc"}


def test_history_rows_pages_until_a_short_page():
    """A long window can hold more plays than one get_history call returns; every page is fetched."""
    client = Tautulli(api_key="k", tautulli_ip="host:8181")
    plays = [{"row": n} for n in range(5)]
    calls = []

    async def get_history(params=None):
        calls.append((params["start"], params["length"]))
        page = plays[params["start"]:params["start"] + params["length"]]
        return {"response": {"result": "success", "data": {"data": page}}}

    client.get_history = get_history
    assert asyncio.run(client.get_history_rows({"after": "2026-01-01"}, page=2)) == plays
    assert calls == [(0, 2), (2, 2), (4, 2)]


def test_history_rows_drop_a_play_pushed_onto_the_next_page():
    """History is newest first, so a play that ends between two pages shifts every row down one."""
    client = Tautulli(api_key="k", tautulli_ip="host:8181")
    plays = [{"row_id": n} for n in (5, 4, 3, 2, 1)]

    async def get_history(params=None):
        page = plays[params["start"]:params["start"] + params["length"]]
        if params["start"] == 0:
            plays.insert(0, {"row_id": 6})  # ends just after the first page is read
        return {"response": {"result": "success", "data": {"data": page}}}

    client.get_history = get_history
    rows = asyncio.run(client.get_history_rows({}, page=2))
    assert [row["row_id"] for row in rows] == [5, 4, 3, 2, 1]  # 4 came back on page two


def test_history_rows_is_none_when_tautulli_fails_midway():
    client = Tautulli(api_key="k", tautulli_ip="host:8181")
    answers = [{"response": {"result": "success", "data": {"data": [{}, {}]}}}, None]

    async def get_history(params=None):
        return answers.pop(0)

    client.get_history = get_history
    assert asyncio.run(client.get_history_rows({}, page=2)) is None


class _Session:
    """aiohttp.ClientSession stand-in: each get() raises or answers with the next result."""

    closed = False

    def __init__(self, results):
        self.results = results

    def get(self, *args, **kwargs):
        return _Request(self.results.pop(0))


class _Request:
    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def __aexit__(self, *exc):
        return False


class _Answer:
    def raise_for_status(self):
        pass

    async def json(self):
        return {"response": {"result": "success", "data": {}}}


def test_a_tautulli_outage_logs_one_error_then_its_end(caplog):
    down = aiohttp.ClientConnectionError("Cannot connect to host")
    client = Tautulli(api_key="k", tautulli_ip="host:8181", use_https=True)
    client.session = _Session([down, TimeoutError(), down, _Answer(), down])
    caplog.set_level(logging.DEBUG, logger="plexbot.tautulli_wrapper")
    caplog.clear()  # the client's own setup lines

    async def run():
        return [await client.get_activity() for _ in range(5)]

    answers = asyncio.run(run())
    assert [answer is not None for answer in answers] == [False, False, False, True, False]
    # The presence loop asks every 15 seconds: one error per outage, the repeats only at DEBUG.
    assert [r.levelname for r in caplog.records] == ["ERROR", "DEBUG", "DEBUG", "INFO", "ERROR"]
