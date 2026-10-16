"""/plex status: library totals, sizes, version line and the status card."""

import asyncio
from datetime import datetime, timezone

import pytest

import ui.embeds
from fakes import FakeTautulli, Recorder, build_bot, component_payload, settle, MESSAGE_ID
from cogs.server_commands import build_status_reply, library_totals, size_comparison, size_comparisons, version_line
from plex_status import PlexStatus, Service, Incident
from ui.images import Attachment
from ui.theme import Colour

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
SERVER = {"pms_name": "Home", "pms_version": "1.41.0.1234-abcdef12", "pms_platform": "Linux",
          "pms_plexpass": 1, "pms_identifier": "srv1", "pms_ip": "10.0.0.2"}
LIBRARIES = [
    {"section_id": "1", "section_type": "movie", "count": "1234", "parent_count": "", "child_count": ""},
    {"section_id": "2", "section_type": "movie", "count": "10", "parent_count": None, "child_count": None},
    {"section_id": "3", "section_type": "show", "count": "62", "parent_count": "240", "child_count": "3745"},
    {"section_id": "4", "section_type": "artist", "count": "300", "parent_count": "900", "child_count": "4500"},
    {"section_id": "5", "section_type": "photo", "count": "50"},
]
RELEASED = 1_759_000_000
UP_TO_DATE = {"update_available": False, "version": "1.41.0.1234-abcdef12", "release_date": RELEASED}
HEALTHY = PlexStatus("none", "All Systems Operational",
                     [Service(f"Service {i}", "operational") for i in range(4)])
TB = 1_000_000_000_000


def first(options):
    return options[0]


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
    reply = build_status_reply(SERVER, LIBRARIES, activity, UP_TO_DATE, HEALTHY, now=NOW, pick=first)
    embed = reply.embeds[0]
    assert embed.title == "Home" and embed.colour.value == Colour.PLAYING
    assert embed.description == (
        "Plex Media Server 1.41.0.1234 on Linux · 🎟️ Plex Pass\n"
        f"✅ Up to date · released <t:{RELEASED}:D>"
    )
    fields = _fields(reply)
    assert "1,244 movies" in fields["Library"] and "3,745 episodes" in fields["Library"]
    assert "4,500 tracks" in fields["Library"]
    assert fields["Activity"] == "2 streams · 20.0 Mbps"
    assert fields["Plex services"] == "🟢 All 4 services operational"
    assert "Update available" not in fields
    assert embed.thumbnail.url == "attachment://mascot-server.png"
    assert embed.footer.text is None  # no sizes known, so no size line
    assert "10.0.0.2" not in str(embed.to_dict())


def test_incident_update_and_missing_parts(monkeypatch):
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: None)
    incident = PlexStatus("minor", "Partial outage",
                          [Service("Plex Web", "partial_outage"), Service("Relay", "operational")],
                          [Incident("Plex Web is slow", "https://status.plex.tv/incidents/1", NOW)])
    update = {"update_available": 1, "version": "1.42.0.2000-0123abcd", "release_date": RELEASED}
    reply = build_status_reply(dict(SERVER, pms_plexpass=0), [], None, update, incident, now=NOW, pick=first)
    embed = reply.embeds[0]
    fields = _fields(reply)
    assert embed.colour.value == Colour.WARNING and "Plex Pass" not in embed.description
    assert embed.description.endswith(f"⬆️ Update available: 1.42.0.2000 · released <t:{RELEASED}:R>")
    assert "Library" not in fields and fields["Activity"] == "Unavailable"
    services = fields["Plex services"].split("\n")
    assert services[0] == "⚠️ 1 of 2 services operational"
    assert services[1] == "🟠 Plex Web · partial outage"
    assert services[2].startswith("[Plex Web is slow](https://status.plex.tv/incidents/1) · since <t:")
    assert "Update available" not in fields
    assert reply.attachments == []


def test_nobody_watching_and_unknown_service(monkeypatch):
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: None)
    reply = build_status_reply(SERVER, LIBRARIES, {"stream_count": 0}, None, None, now=NOW, pick=first)
    fields = _fields(reply)
    assert fields["Activity"] == "Nobody's watching"
    assert fields["Plex services"] == "Status unavailable"
    assert reply.embeds[0].colour.value == Colour.PLAYING
    assert reply.embeds[0].description == "Plex Media Server 1.41.0.1234 on Linux · 🎟️ Plex Pass"


@pytest.mark.parametrize("update, expected", [
    (UP_TO_DATE, f"✅ Up to date · released <t:{RELEASED}:D>"),
    # A beta server can be newer than the channel Tautulli checks: up to date, but that's not its date.
    ({"update_available": False, "version": "1.40.9.1000-aaaa", "release_date": RELEASED}, "✅ Up to date"),
    ({"update_available": "true", "version": "1.42.0.1-bb"}, "⬆️ Update available: 1.42.0.1"),
    ({}, None),
    (None, None),
])
def test_version_line(update, expected):
    assert version_line("1.41.0.1234-abcdef12", update) == expected


def test_library_sizes_and_size_line(monkeypatch):
    import cogs.server_commands as server_commands
    monkeypatch.setattr(server_commands, "mascot", lambda slot: None)
    sizes = {"movie": 8 * TB, "artist": TB // 10}
    reply = build_status_reply(SERVER, LIBRARIES, None, None, None, now=NOW, sizes=sizes, pick=first)
    library = _fields(reply)["Library"].split("\n")
    assert library[0] == "\U0001F3AC 1,244 movies · 8.0 TB"
    assert "TB" not in library[1] and "GB" not in library[1]  # TV size unknown: not shown
    assert library[2].endswith("4,500 tracks · 100 GB")
    assert reply.embeds[0].footer.text.startswith("8.1 TB of media: about 5.5 million floppy disks")


def test_size_comparisons_are_realistic():
    assert size_comparisons(int(8.1 * TB)) == [
        "about 5.5 million floppy disks, stacked 18 km high, 2× the height of Mount Everest",
        "about 5.5 million floppy disks, laid end to end for 494 km",
        "110 tonnes of floppy disks, as heavy as 18 elephants",
        "about 11,035 CDs, stacked 13 m high, 2× the height of a giraffe",
        "about 1,723 DVDs, stacked 2.1 m high, taller than a person",
        "about 324 Blu-rays",  # a 39 cm stack isn't worth comparing
        "enough to fill 63 phones with 128 GB each",
        "36.7 years of non-stop downloading on a 56k dial-up modem",
        "93.8 days of non-stop downloading on 8 Mbps ADSL",
        "7.5 days of non-stop downloading at 100 Mbps",
        "18 hours of non-stop downloading on gigabit fibre",
    ]


def test_size_comparison_is_picked_at_random_and_scales_down():
    seen = []
    line = size_comparison(int(8.1 * TB), pick=lambda options: seen.append(options) or options[-1])
    assert line == "8.1 TB of media: 18 hours of non-stop downloading on gigabit fibre"
    assert len(seen[0]) == 11
    assert size_comparison(0) is None


def test_size_comparisons_scale_with_the_library():
    small = size_comparisons(5_000_000_000)
    assert "about 7 CDs" in small and "7 minutes of non-stop downloading at 100 Mbps" in small
    # Fewer than two discs or phones reads badly ("about 1 Blu-rays"), so those lines are dropped.
    assert not any("DVD" in line or "Blu-ray" in line or "phones" in line for line in small)
    huge = size_comparisons(45 * TB)
    assert huge[0] == "about 30.5 million floppy disks, stacked 101 km high, past the edge of space"
    assert "203.8 years of non-stop downloading on a 56k dial-up modem" in huge



class SizeTautulli:
    def __init__(self, sizes, slow=()):
        self.sizes, self.slow, self.calls = sizes, set(slow), []

    async def get_library_media_info(self, section_id=None, length=50, **kwargs):
        assert length == 1  # only the totals are needed
        self.calls.append(section_id)
        if section_id in self.slow:
            await asyncio.sleep(10)
        return {"response": {"result": "success", "data": {"total_file_size": self.sizes.get(section_id, 0)}}}


def test_cog_library_sizes_are_cached_and_skip_slow_libraries(monkeypatch):
    import cogs.server_commands as server_commands
    from cogs.server_commands import ServerCommands

    monkeypatch.setattr(server_commands, "_SIZE_WAIT", 0.05)
    cog = object.__new__(ServerCommands)
    cog._section_sizes = {}
    cog.tautulli = SizeTautulli({"1": 5 * TB, "2": TB, "3": 0, "4": 2 * TB}, slow={"4"})

    async def run():
        first_sizes = await cog._library_sizes(LIBRARIES)
        calls = list(cog.tautulli.calls)
        second_sizes = await cog._library_sizes(LIBRARIES)
        return first_sizes, calls, second_sizes

    first_sizes, calls, second_sizes = asyncio.run(run())
    assert sorted(calls) == ["1", "2", "3", "4"]  # photo libraries aren't sized
    assert first_sizes == {"movie": 6 * TB}  # TV reports 0 (not calculated); music timed out
    assert second_sizes == first_sizes
    assert sorted(cog.tautulli.calls[4:]) == ["4"]  # only the library that timed out is retried


def _click_services(monkeypatch, latest):
    import cogs.server_commands as server_commands
    from cogs.server_commands import StatusView

    async def fake_fetch():
        return latest

    monkeypatch.setattr(server_commands, "fetch_status", fake_fetch)
    recorder = Recorder(monkeypatch)

    async def run():
        bot, _cog = build_bot(FakeTautulli([]))
        view = StatusView(HEALTHY)
        bot._connection.store_view(view, MESSAGE_ID)
        button = next(c for c in view.children if getattr(c, "label", None) == "Plex services")
        bot._connection.parse_interaction_create(component_payload(button.custom_id))
        await settle()

    asyncio.run(run())
    return recorder


def test_services_button_opens_the_full_list_privately(monkeypatch):
    latest = PlexStatus("none", "All Systems Operational", [Service("Relay service", "degraded_performance")])
    recorder = _click_services(monkeypatch, latest)
    assert recorder.kinds() == ["defer", "followup"]
    assert recorder.calls[0][1] == {"ephemeral": True, "with_message": True}
    sent = recorder.last("followup")
    assert sent["ephemeral"] is True
    assert sent["embeds"][0].description == "🟡 Relay service · degraded"  # fetched fresh on click


def test_services_button_falls_back_to_the_card_snapshot(monkeypatch):
    recorder = _click_services(monkeypatch, None)
    assert recorder.last("followup")["embeds"][0].description.count("🟢") == 4


def test_presence_counts_every_stream_and_their_total_bandwidth(monkeypatch):
    from fakes import EPISODE_PAUSED, MOVIE_PLAYING

    shown = []

    async def run():
        tautulli = FakeTautulli([MOVIE_PLAYING, EPISODE_PAUSED])  # one stream at home, one remote
        bot, cog = build_bot(tautulli)

        async def change_presence(activity=None, **kwargs):
            shown.append(activity.name)

        monkeypatch.setattr(bot, "change_presence", change_presence)
        await cog.status_task.coro(cog)
        tautulli.sessions = [MOVIE_PLAYING]
        cog._display_streams = True  # the next tick would show the help hint
        await cog.status_task.coro(cog)

    asyncio.run(run())
    assert shown == ["2 streams at 20.0 Mbps", "1 stream at 12.0 Mbps"]
