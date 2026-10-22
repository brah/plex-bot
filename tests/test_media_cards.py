"""Renderers behind the library commands: media card, recent list, downloads, watch stats."""

import io
from types import SimpleNamespace

from PIL import Image

from plexbot.cogs.media_commands import (
    downloads_card, recent_line, split_torrents, title_choices, torrent_block,
)
from plexbot.media_details import watch_stats
from plexbot.ui.cards import item_choices, media_card, media_facts, runtime, session_title
from plexbot.ui.images import Attachment
from plexbot.ui.theme import Colour

MATRIX = {
    "rating_key": "101", "media_type": "movie", "title": "The Matrix", "year": "1999",
    "summary": "A hacker learns the *truth*.", "tagline": "Welcome to the Real World.",
    "content_rating": "R", "duration": "8160000", "genres": ["Action", "science fiction", "Thriller", "Cyberpunk"],
    "audience_rating": "8.7", "directors": ["Lana Wachowski", "Lilly Wachowski"],
    "actors": ["Keanu Reeves", "Laurence Fishburne", "Carrie-Anne Moss", "Hugo Weaving"],
}
EPISODE = {
    "rating_key": "202", "media_type": "episode", "title": "Hello, Ms. Cobel", "grandparent_title": "Severance",
    "parent_media_index": "1", "media_index": "2", "duration": "3000000", "originally_available_at": "2022-02-18",
}
SHOW = {"rating_key": "200", "media_type": "show", "title": "Severance", "year": "2022", "children_count": "19",
        "duration": "3000000", "rating": "0"}
SEASON = {"rating_key": "201", "media_type": "season", "title": "Season 2", "parent_title": "Severance"}


def _poster(colour=(30, 90, 200)) -> Attachment:
    out = io.BytesIO()
    Image.new("RGB", (20, 30), colour).save(out, format="PNG")
    return Attachment("poster.jpg", out.getvalue())


class TestMediaCard:
    def test_movie_facts_tagline_credits(self):
        embed = media_card(MATRIX)
        assert embed.title == "The Matrix (1999)"
        assert embed.author.name == "🎬 Movie"
        lines = embed.description.split("\n")
        assert lines[0] == "-# R · 2h 16m · Action, Science fiction, Thriller · ⭐ 8.7"
        assert lines[1] == "*Welcome to the Real World.*"
        assert lines[2] == "A hacker learns the \\*truth\\*."  # summaries are escaped
        assert lines[3] == ("-# Directed by Lana Wachowski, Lilly Wachowski · "
                            "Starring Keanu Reeves, Laurence Fishburne, Carrie-Anne Moss")

    def test_tinted_by_poster_else_media_colour(self):
        assert media_card(MATRIX, poster=_poster()).colour.value == 0x1E5AC8
        assert media_card(MATRIX).colour.value == Colour.MOVIE
        assert media_card(SHOW).colour.value == Colour.TV
        assert media_card(MATRIX, poster=_poster()).thumbnail.url == "attachment://poster.jpg"

    def test_episode_and_show(self):
        episode = media_card(EPISODE)
        assert episode.title == "Severance · S01E02 · Hello, Ms. Cobel"
        assert media_facts(EPISODE) == "50m · aired <t:1645142400:D>"
        assert media_facts(SHOW) == "19 episodes"  # no per-episode runtime, no zero rating
        assert session_title(SEASON) == "Severance · Season 2"

    def test_stats_follow_a_blank_line_and_minimal_items_render(self):
        embed = media_card(MATRIX, stats="▶ **3 plays**")
        assert embed.description.endswith("\n\n▶ **3 plays**")
        bare = media_card({"title": "Mystery"})
        assert bare.title == "Mystery"
        assert bare.description is None
        assert bare.author.name == "▶ Media"

    def test_runtime(self):
        assert runtime("8160000") == "2h 16m"
        assert runtime(3_600_000) == "1h"
        assert runtime(0) is None


class TestWatchStats:
    TIME = [{"query_days": 1, "total_time": 0, "total_plays": 0},
            {"query_days": 0, "total_time": 66_000, "total_plays": 16}]
    USERS = [{"username": "bob", "friendly_name": "_Bob_", "total_plays": 2},
             {"username": "alice", "friendly_name": "Alice", "total_plays": 5},
             {"username": "mallory", "friendly_name": "Mallory", "total_plays": 9},
             {"username": "zed", "friendly_name": "Zed", "total_plays": 0}]

    def test_plays_time_and_top_viewers_without_ignored(self):
        text = watch_stats(self.USERS, self.TIME, lambda row: row["friendly_name"], {"mallory"})
        assert text == "▶ **16 plays** · 18h 20m watched\n-# Watched most by **Alice** (5), **\\_Bob\\_** (2)"

    def test_unwatched(self):
        assert watch_stats([], [], str, set()) == "-# Nobody's watched this yet."

    def test_many_viewers_summarised(self):
        users = [{"username": f"u{i}", "friendly_name": f"U{i}", "total_plays": 10 - i} for i in range(7)]
        text = watch_stats(users, self.TIME, lambda row: row["friendly_name"], set())
        assert text.endswith("**U3** (7), 3 others")


class TestRecent:
    def test_lines(self):
        assert recent_line(dict(MATRIX, added_at="1760000000")) == "🎬 **The Matrix (1999)** · added <t:1760000000:R>"
        assert recent_line(SEASON) == "📺 **Severance · Season 2**"

    def test_choices_unique_and_capped(self):
        entries = [MATRIX, MATRIX, dict(MATRIX, rating_key="102")] + [
            dict(MATRIX, rating_key=str(300 + i), title=f"Film {i}") for i in range(30)
        ]
        choices = item_choices(entries)
        assert len(choices) == 25
        assert choices["The Matrix (1999)"] == "101"
        assert choices["The Matrix (1999) #2"] == "102"  # same title, different item

    def test_repeated_long_titles_stay_distinct_and_within_limits(self):
        long = dict(MATRIX, title="X" * 120, year=None)
        choices = item_choices([dict(long, rating_key=str(i)) for i in range(4)])
        assert len(choices) == 4
        assert all(len(label) <= 100 for label in choices)

    def test_title_choices_carry_rating_keys(self):
        assert title_choices([MATRIX, SHOW]) == {"The Matrix (1999)": "key:101", "Severance (2022)": "key:200"}


def _torrent(name, progress, dlspeed, state="downloading", seeds=5, paused=False, eta=600, size=2_000_000_000):
    return SimpleNamespace(name=name, progress=progress, dlspeed=dlspeed, state=state, num_seeds=seeds, eta=eta,
                           size=size, state_enum=SimpleNamespace(is_paused=paused))


class TestDownloads:
    def test_split_skips_paused_and_finds_stalled(self):
        fast = _torrent("Fast", 0.4, 5_000_000)
        nearly = _torrent("Nearly", 0.8, 1_000_000)
        dead = _torrent("Dead", 0.2, 0, state="stalledDL", seeds=0)
        finishing = _torrent("Finishing", 0.995, 0)
        paused = _torrent("Paused", 0.5, 0, paused=True)
        active, stalled = split_torrents([fast, dead, paused, nearly, finishing])
        assert [t.name for t in active] == ["Nearly", "Fast"]
        assert [t.name for t in stalled] == ["Dead", "Finishing"]

    def test_block_has_bar_speed_size_eta_seeds(self):
        block = torrent_block(_torrent("Big_Movie_2024", 0.61, 4_200_000), now=1_000)
        assert block == ("⬇️ **Big\\_Movie\\_2024**\n"
                         "`██████░░░░` 61% · 4.2 MB/s · 2.0 GB · done <t:1600:R> · 5 seeds")
        assert "done" not in torrent_block(_torrent("X", 0.1, 10, eta=8_640_000), now=0)

    def test_card(self):
        active = [_torrent(f"T{i}", 0.5, 1_000_000) for i in range(12)]
        reply = downloads_card(active, [_torrent("Dead", 0.2, 0)], now=1_000)
        embed = reply.embeds[0]
        assert embed.description.startswith("**12 downloads** · 12.0 MB/s · 1 stalled · updated <t:1000:R>")
        assert "…and 2 more downloads" in embed.description
        assert embed.description.endswith("**Stalled**\n• Dead")
        assert embed.colour.value == Colour.PLAYING
        assert [a.filename for a in reply.attachments] == ["mascot-downloading.png"]
