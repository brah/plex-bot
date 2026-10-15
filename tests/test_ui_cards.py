"""ui.cards: /plex watchers overview and detail cards from realistic Tautulli sessions."""

from datetime import datetime, timezone

from fakes import EPISODE_PAUSED, LIVE_TV, MARKDOWN_TITLE, MOVIE_PLAYING, TRACK_PLAYING, many_sessions
from ui.cards import (
    plex_web_link, session_lines, session_title, session_user, stream_detail, stream_overview,
)
from ui.images import Attachment
from ui.theme import Colour, Limits

NOW = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)
STRIP = Attachment("posters.png", b"png")


class TestTitles:
    def test_movie_episode_track_live(self):
        assert session_title(MOVIE_PLAYING) == "The Matrix (1999)"
        assert session_title(EPISODE_PAUSED) == "Severance · S01E02 · Hello, Ms. Cobel"
        assert session_title(TRACK_PLAYING) == "*NSYNC — Bye Bye Bye"
        assert session_title(LIVE_TV) == "News at Ten"

    def test_user_fallbacks(self):
        assert session_user(MOVIE_PLAYING) == "Alice"
        assert session_user(LIVE_TV) == "dan"
        assert session_user({}) == "Unknown"


class TestSessionLines:
    def test_two_lines_with_progress_quality_device(self):
        first, second = session_lines(MOVIE_PLAYING).split("\n")
        assert first.startswith("▶") and "**Alice**" in first and "The Matrix (1999)" in first
        assert "10:00 / 2:16:00" in second
        assert "1080p Direct Play" in second and "Living Room" in second

    def test_markdown_is_escaped(self):
        line = session_lines(MARKDOWN_TITLE)
        assert "\\_eve\\_" in line and "\\*\\*Bold\\*\\*" in line

    def test_live_tv_without_duration_does_not_crash(self):
        assert "News at Ten" in session_lines(LIVE_TV)


class TestOverview:
    def test_header_colour_and_strip(self):
        embed = stream_overview([MOVIE_PLAYING, EPISODE_PAUSED], bandwidth_kbps=20000, strip=STRIP,
                                updated=NOW)
        assert embed.description.startswith("**2 streams** · 20.0 Mbps · updated <t:")
        assert embed.colour.value == Colour.PLAYING
        assert embed.image.url == "attachment://posters.png"

    def test_all_paused_is_amber(self):
        embed = stream_overview([EPISODE_PAUSED], bandwidth_kbps=0, strip=None, updated=NOW)
        assert embed.colour.value == Colour.WARNING and not embed.image.url

    def test_names_can_be_discord_mentions(self):
        embed = stream_overview([MOVIE_PLAYING], bandwidth_kbps=0, strip=None, updated=NOW,
                                name=lambda session: "<@42>")
        assert "**<@42>** · The Matrix (1999)" in embed.description

    def test_many_streams_stay_within_limits(self):
        embed = stream_overview(many_sessions(200), bandwidth_kbps=1, strip=None, updated=NOW)
        assert len(embed.description) <= Limits.DESCRIPTION
        assert "more streams" in embed.description.splitlines()[-1]


class TestDetail:
    def test_movie_detail(self):
        frame, poster = Attachment("frame.jpg", b"f"), Attachment("poster.jpg", b"p")
        embed = stream_detail(MOVIE_PLAYING, avatar_url="https://a/1.png", link="https://app.plex.tv/x",
                              frame=frame, poster=poster, now=NOW)
        assert embed.title == "The Matrix (1999)" and embed.url == "https://app.plex.tv/x"
        assert embed.author.name == "Alice" and embed.author.icon_url == "https://a/1.png"
        assert embed.image.url == "attachment://frame.jpg"
        assert embed.thumbnail.url == "attachment://poster.jpg"
        fields = {field.name: field.value for field in embed.fields}
        assert "Ends <t:" in fields["Progress"]
        assert "**Video** 1080p H264 · Direct Play" in fields["Stream"]
        assert "**Audio** EAC3 5.1(side) · Direct Play" in fields["Stream"]
        assert "Local" in fields["Player"] and "12.0 Mbps" in fields["Player"]

    def test_transcoded_paused_episode(self):
        embed = stream_detail(EPISODE_PAUSED, avatar_url=None, link=None, frame=None, poster=None, now=NOW)
        fields = {field.name: field.value for field in embed.fields}
        assert "Paused" in fields["Progress"] and "Ends" not in fields["Progress"]
        assert "4k → 1080p HEVC · Transcode" in fields["Stream"]
        assert "**Subtitles** English (SRT) · Burned in" in fields["Stream"]
        assert "Remote" in fields["Player"]
        assert embed.colour.value == Colour.WARNING and embed.author.icon_url is None

    def test_never_shows_ip_or_location(self):
        session = dict(MOVIE_PLAYING, ip_address="203.0.113.7", ip_address_public="203.0.113.7",
                       location="wan", city="Springfield")
        embed = stream_detail(session, avatar_url=None, link=None, frame=None, poster=None, now=NOW)
        text = str(embed.to_dict())
        assert "203.0.113.7" not in text and "Springfield" not in text

    def test_sparse_session_does_not_crash(self):
        stream_detail(LIVE_TV, avatar_url=None, link=None, frame=None, poster=None, now=NOW)


class TestLink:
    def test_plex_web_link(self):
        assert plex_web_link("abc123", "101") == (
            "https://app.plex.tv/desktop/#!/server/abc123/details?key=%2Flibrary%2Fmetadata%2F101"
        )
        assert plex_web_link(None, "101") is None and plex_web_link("abc", None) is None
