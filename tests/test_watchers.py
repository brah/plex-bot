"""Unit tests for /plex watchers stream management, slash command paths and hints, and terminate_session."""

import asyncio
from types import SimpleNamespace

from cogs.server_commands import _can_manage_streams, _session_choices, _session_label
from errors import invoked_command_path, slash_equivalent
from slash_commands import command_paths
from tautulli_wrapper import Tautulli


def _member(member_id=1, role_ids=(), admin=False):
    return SimpleNamespace(
        id=member_id,
        roles=[SimpleNamespace(id=r) for r in role_ids],
        guild_permissions=SimpleNamespace(administrator=admin),
    )


class TestCanManageStreams:
    def test_admin(self):
        assert _can_manage_streams(_member(admin=True), []) is True

    def test_listed_user(self):
        assert _can_manage_streams(_member(member_id=42), [42]) is True

    def test_listed_role(self):
        assert _can_manage_streams(_member(role_ids=(7, 8)), [8]) is True

    def test_string_ids_from_config(self):
        assert _can_manage_streams(_member(member_id=42), ["42"]) is True

    def test_not_listed(self):
        assert _can_manage_streams(_member(member_id=1, role_ids=(7,)), [42, 8]) is False

    def test_invalid_ids_ignored(self):
        assert _can_manage_streams(_member(member_id=1), ["", "abc", None]) is False

    def test_user_outside_guild(self):
        # A plain User (no roles / guild permissions) can still be listed by ID.
        user = SimpleNamespace(id=42)
        assert _can_manage_streams(user, [42]) is True
        assert _can_manage_streams(user, []) is False


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
