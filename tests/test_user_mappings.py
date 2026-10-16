"""map.json storage: merging old duplicates, never wiping an unreadable file, safe writes, links."""

import json
import os

import pytest

import utilities
from utilities import MappingsUnavailable, UserMappings, normalise_mappings


@pytest.fixture
def map_file(tmp_path, monkeypatch):
    path = tmp_path / "map.json"
    monkeypatch.setattr(UserMappings, "_mapping_file", str(path))
    monkeypatch.setattr(UserMappings, "_mappings", None)
    monkeypatch.setattr(UserMappings, "_unreadable", False)
    monkeypatch.setattr(UserMappings, "_unreadable_version", None)
    return path


def _stored(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestNormalise:
    def test_merges_an_ignore_only_entry_with_a_later_link(self):
        # What old `plex ignore bob` then `plex mapdiscord bob @Bob` left behind.
        merged = normalise_mappings([
            {"discord_id": "", "plex_username": "bob", "ignore": True},
            {"discord_id": 22, "plex_username": "bob"},
        ])
        assert merged == [{"plex_username": "bob", "discord_id": "22", "ignore": True}]

    def test_drops_junk_and_empty_entries_and_keeps_extra_keys(self):
        merged = normalise_mappings([
            "nonsense", {"plex_username": ""}, {"plex_username": "zed"},
            {"plex_username": "alice", "discord_id": "11", "note": "kept"},
        ])
        assert merged == [{"plex_username": "alice", "discord_id": "11", "ignore": False, "note": "kept"}]

    def test_not_a_list(self):
        assert normalise_mappings({"plex_username": "alice"}) == []


class TestStorage:
    def test_missing_file_is_empty_and_saveable(self, map_file):
        assert UserMappings.load_user_mappings() == []
        UserMappings.link("alice", 11)
        assert _stored(map_file) == [{"plex_username": "alice", "discord_id": "11", "ignore": False}]
        assert not map_file.with_name("map.json.tmp").exists()

    def test_unreadable_file_is_never_overwritten(self, map_file):
        map_file.write_text('[{"plex_username": "alice", "discord_id": "11"', encoding="utf-8")  # cut short
        assert UserMappings.load_user_mappings() == [] and UserMappings.is_unreadable()
        with pytest.raises(MappingsUnavailable):
            UserMappings.link("bob", 22)
        assert map_file.read_text(encoding="utf-8").startswith('[{"plex_username": "alice"')

    def test_a_fixed_file_is_read_again_without_a_restart(self, map_file):
        map_file.write_text("{broken", encoding="utf-8")
        assert UserMappings.is_unreadable()
        map_file.write_text("[]", encoding="utf-8")
        stat = map_file.stat()
        os.utime(map_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))  # edited a moment later
        assert not UserMappings.is_unreadable()
        UserMappings.link("alice", 11)
        assert _stored(map_file) == [{"plex_username": "alice", "discord_id": "11", "ignore": False}]

    def test_writes_in_place_with_a_backup_when_the_file_cannot_be_swapped(self, map_file, monkeypatch):
        map_file.write_text(json.dumps([{"plex_username": "alice", "discord_id": "11"}]), encoding="utf-8")

        def busy(src, dst):  # what a Docker single-file bind mount does
            raise OSError(16, "Device or resource busy")

        monkeypatch.setattr(utilities.os, "replace", busy)
        UserMappings.toggle_ignore("alice")
        assert _stored(map_file) == [{"plex_username": "alice", "discord_id": "11", "ignore": True}]
        assert _stored(map_file.with_name("map.json.bak"))[0].get("ignore") is None
        assert not map_file.with_name("map.json.tmp").exists()

    def test_lookups_find_merged_entries(self, map_file):
        map_file.write_text(json.dumps([
            {"discord_id": "", "plex_username": "bob", "ignore": True},
            {"discord_id": 22, "plex_username": "bob"},
        ]), encoding="utf-8")
        assert UserMappings.get_mapping_by_plex_username("bob")["discord_id"] == "22"
        assert UserMappings.get_mapping_by_discord_id(22)["plex_username"] == "bob"


class TestOperations:
    def test_link_replaces_either_sides_old_link(self, map_file):
        UserMappings.link("alice", 11)
        UserMappings.link("bob", 22)
        moved = UserMappings.link("bob", 11)  # member 11 moves from alice to bob; bob drops 22
        assert (moved.moved_from_plex, moved.replaced_discord_id) == ("alice", "22")
        assert _stored(map_file) == [{"plex_username": "bob", "discord_id": "11", "ignore": False}]

    def test_relinking_the_same_pair_reports_nothing(self, map_file):
        UserMappings.link("alice", 11)
        again = UserMappings.link("alice", "11")
        assert again.moved_from_plex is None and again.replaced_discord_id is None

    def test_unlink_keeps_an_ignore(self, map_file):
        UserMappings.link("mallory", 33)
        assert UserMappings.toggle_ignore("mallory") is True
        assert UserMappings.unlink("mallory") == "33"
        assert UserMappings.unlink("mallory") is None
        assert _stored(map_file) == [{"plex_username": "mallory", "discord_id": "", "ignore": True}]

    def test_unignoring_an_unlinked_user_removes_the_entry(self, map_file):
        UserMappings.toggle_ignore("zed")
        assert UserMappings.toggle_ignore("zed") is False
        assert _stored(map_file) == []

    def test_failed_write_leaves_memory_unchanged(self, map_file, monkeypatch):
        UserMappings.link("alice", 11)

        def broken(self, *args, **kwargs):
            raise PermissionError("read-only")

        monkeypatch.setattr(type(map_file), "write_text", broken)
        with pytest.raises(OSError):
            UserMappings.link("bob", 22)
        assert [m["plex_username"] for m in UserMappings.load_user_mappings()] == ["alice"]


def test_os_replace_is_used_normally(map_file, monkeypatch):
    calls = []
    real = os.replace
    monkeypatch.setattr(utilities.os, "replace", lambda src, dst: (calls.append(dst), real(src, dst)))
    UserMappings.link("alice", 11)
    assert calls == [map_file]
