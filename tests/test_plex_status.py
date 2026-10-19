"""plex.tv's status page: parsing the Statuspage-compatible summary and the incident.io widget feed."""

from datetime import datetime, timezone

from plexbot.plex_status import parse_status, services_card
from plexbot.ui.theme import Colour

SUMMARY = {
    "status": {"indicator": "none", "description": "All Systems Operational"},
    "components": [
        {"id": "c2", "name": "Plex website (www.plex.tv)", "status": "operational", "position": 1},
        {"id": "c1", "name": "Authentication and API server (plex.tv)", "status": "operational", "position": 0},
        {"id": "c3", "name": "Subtitles search", "status": "operational", "position": 2},
        {"id": "c4", "name": "Relay service", "status": "operational", "position": 3},
    ],
}
QUIET_WIDGET = {"ongoing_incidents": [], "in_progress_maintenances": [], "scheduled_maintenances": []}
# Shape taken from a real incident.io widget response.
INCIDENT_WIDGET = {
    "ongoing_incidents": [{
        "id": "i1", "name": "Slow subtitle results", "status": "investigating",
        "url": "https://status.plex.tv/incidents/i1", "current_worst_impact": "degraded_performance",
        "affected_components": [{"id": "c3", "name": "Subtitles search", "current_status": "degraded_performance"}],
        "last_update_at": "2026-10-14T18:51:36.404Z", "last_update_message": "Looking into it.",
    }],
    "in_progress_maintenances": [{
        "id": "m1", "name": "Relay upgrade", "status": "maintenance_in_progress",
        "url": "https://status.plex.tv/incidents/m1", "started_at": "2026-10-14T17:00:00Z",
        "scheduled_end_at": "2026-10-14T19:00:00Z", "last_update_at": "2026-10-14T17:00:00Z",
        "affected_components": [{"id": "c4", "name": "Relay service"}],
    }],
    "scheduled_maintenances": [{"id": "m2", "name": "Later", "status": "maintenance_scheduled",
                                "affected_components": []}],
}
NOW = datetime(2026, 10, 14, 20, tzinfo=timezone.utc)


def test_services_keep_page_order():
    status = parse_status(SUMMARY, QUIET_WIDGET)
    assert [s.name for s in status.services] == [
        "Authentication and API server (plex.tv)", "Plex website (www.plex.tv)", "Subtitles search",
        "Relay service",
    ]
    assert status.problems == []
    assert status.incidents == []
    assert not status.degraded


def test_widget_feed_marks_affected_services_and_lists_incidents():
    status = parse_status(SUMMARY, INCIDENT_WIDGET)
    assert [(s.name, s.status) for s in status.problems] == [
        ("Subtitles search", "degraded_performance"), ("Relay service", "under_maintenance"),
    ]
    assert [(i.name, i.maintenance) for i in status.incidents] == [
        ("Slow subtitle results", False), ("Relay upgrade", True),
    ]
    incident = status.incidents[0]
    assert incident.url == "https://status.plex.tv/incidents/i1"
    assert incident.since == datetime(2026, 10, 14, 18, 51, 36, 404000, tzinfo=timezone.utc)
    assert status.incidents[1].since == datetime(2026, 10, 14, 17, tzinfo=timezone.utc)
    assert status.degraded


def test_missing_or_broken_feeds():
    assert parse_status(None, QUIET_WIDGET) is None
    assert parse_status({"status": {}}, None) is None  # no services at all
    status = parse_status(SUMMARY, None)  # the widget feed is optional
    assert len(status.services) == 4
    assert status.incidents == []
    odd = dict(SUMMARY, status={"indicator": "minor", "description": "Minor Service Outage"})
    assert parse_status(odd, {"ongoing_incidents": [{"name": "x", "last_update_at": "garbage"}]}).degraded


def test_services_card_lists_every_service_in_order():
    reply = services_card(parse_status(SUMMARY, INCIDENT_WIDGET), now=NOW)
    embed = reply.embeds[0]
    lines = embed.description.split("\n")
    assert lines[0].startswith("🟢")
    assert lines[0].endswith("Authentication and API server (plex.tv)")
    assert lines[2] == "🟡 Subtitles search · degraded"
    assert lines[3] == "🔧 Relay service · maintenance"
    assert embed.colour.value == Colour.WARNING
    assert embed.url == "https://status.plex.tv/"
    incidents = next(f.value for f in embed.fields if f.name == "Incidents")
    assert "[Slow subtitle results](https://status.plex.tv/incidents/i1)" in incidents
    assert "<t:" in incidents


def test_services_card_when_all_is_well():
    embed = services_card(parse_status(SUMMARY, QUIET_WIDGET), now=NOW).embeds[0]
    assert embed.colour.value == Colour.PLAYING
    assert not embed.fields
