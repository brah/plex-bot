"""/plex status: library totals and the status card."""

from datetime import datetime, timezone

import ui.embeds
from cogs.server_commands import build_status_reply, library_totals
from ui.images import Attachment
from ui.theme import Colour

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
SERVER = {"pms_name": "Home", "pms_version": "1.41.0", "pms_platform": "Linux", "pms_plexpass": 1,
          "pms_identifier": "srv1", "pms_ip": "10.0.0.2"}
LIBRARIES = [
    {"section_type": "movie", "count": "1234", "parent_count": "", "child_count": ""},
    {"section_type": "movie", "count": "10", "parent_count": None, "child_count": None},
    {"section_type": "show", "count": "62", "parent_count": "240", "child_count": "3745"},
    {"section_type": "artist", "count": "300", "parent_count": "900", "child_count": "4500"},
    {"section_type": "photo", "count": "50"},
]
HEALTHY = {"indicator": "none", "description": "All Systems Operational"}


def _fields(reply):
    return {field.name: field.value for field in reply.embeds[0].fields}


def test_library_totals():
    assert library_totals(LIBRARIES) == {"movies": 1244, "shows": 62, "episodes": 3745,
                                         "artists": 300, "tracks": 4500}
    assert library_totals([]) == {"movies": 0, "shows": 0, "episodes": 0, "artists": 0, "tracks": 0}


def test_healthy_status(monkeypatch):
    monkeypatch.setattr(ui.embeds, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: Attachment(f"{slot}.png", b"x"))
    activity = {"stream_count": "2", "total_bandwidth": "20000"}
    reply = build_status_reply(SERVER, LIBRARIES, activity, {"update_available": False}, HEALTHY, now=NOW)
    embed = reply.embeds[0]
    assert embed.title == "Home" and embed.colour.value == Colour.PLAYING
    assert "Plex Media Server 1.41.0 on Linux · Plex Pass" == embed.description
    fields = _fields(reply)
    assert "1,244 movies" in fields["Library"] and "3,745 episodes" in fields["Library"]
    assert "4,500 tracks" in fields["Library"]
    assert fields["Activity"] == "2 streams · 20.0 Mbps"
    assert fields["Plex services"] == "All Systems Operational"
    assert "Update available" not in fields
    assert embed.thumbnail.url == "attachment://mascot-server.png"
    assert "10.0.0.2" not in str(embed.to_dict())


def test_incident_update_and_missing_parts(monkeypatch):
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: None)
    incident = {"indicator": "minor", "description": "Partial outage: Plex Web"}
    reply = build_status_reply(dict(SERVER, pms_plexpass=0), [], None,
                               {"update_available": 1, "version": "1.42.0"}, incident, now=NOW)
    embed = reply.embeds[0]
    fields = _fields(reply)
    assert embed.colour.value == Colour.WARNING and "Plex Pass" not in embed.description
    assert "Library" not in fields and fields["Activity"] == "Unavailable"
    assert fields["Plex services"].endswith("Partial outage: Plex Web")
    assert fields["Update available"] == "Plex Media Server 1.42.0"
    assert reply.attachments == []


def test_nobody_watching_and_unknown_service(monkeypatch):
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: None)
    reply = build_status_reply(SERVER, LIBRARIES, {"stream_count": 0}, None, None, now=NOW)
    fields = _fields(reply)
    assert fields["Activity"] == "Nobody's watching"
    assert fields["Plex services"] == "Status unavailable"
    assert reply.embeds[0].colour.value == Colour.PLAYING
