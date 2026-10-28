"""/plex wrapped's numbers: rank, top show and movie, genres, habits and personality from a year of plays."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from plexbot.wrapped_year import default_year, wrap_year, year_window

MONDAY = date(2026, 3, 2)


def _play(when, user="sam", *, movie=None, show=None, episode=1, seconds=3600, watched=1):
    """A history play: a movie, or episode `episode` of `show`."""
    if isinstance(when, date) and not isinstance(when, datetime):
        when = datetime.combine(when, datetime.min.time(), timezone.utc).replace(hour=20)
    row = {"user": user, "play_duration": seconds, "watched_status": watched}
    if movie:
        row.update(media_type="movie", rating_key=f"m-{movie}", title=movie, thumb=f"/t/{movie}")
    elif show:
        row.update(media_type="episode", rating_key=f"{show}-{episode}", title=f"Episode {episode}",
                   grandparent_rating_key=f"s-{show}", grandparent_title=show, grandparent_thumb=f"/t/{show}")
    else:
        row.update(media_type="track", rating_key="t-1", title="Song")
    return when, row


def _weekdays(n):
    """The first n weekdays from Monday 2 March 2026."""
    days, day = [], MONDAY
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _wrap(plays, username="sam", cache=None):
    return wrap_year(plays, username, (cache or {}).get, 2026)


def test_default_year_is_last_year_in_january():
    assert default_year(date(2027, 1, 15)) == 2026
    assert default_year(date(2026, 12, 1)) == 2026


def test_year_window_uses_the_server_timezone():
    zone = ZoneInfo("America/New_York")
    late = datetime(2026, 12, 31, 23, 30, tzinfo=zone)  # already 2027 in UTC
    since, until = year_window(2026, zone)
    assert since == datetime(2026, 1, 1, tzinfo=zone)
    assert since <= late < until
    since, until = year_window(2027, zone)
    assert not since <= late < until


def test_rank_counts_everyone_but_music():
    days = _weekdays(20)
    plays = [_play(d, "sam", show="Harbour Lights", episode=i) for i, d in enumerate(days[:10])]
    plays += [_play(d, "alex", show="Cold Open", episode=i) for i, d in enumerate(days)]
    plays += [_play(d, "kim", show="Cold Open", episode=i) for i, d in enumerate(days[:5])]
    plays += [_play(d, "kim", seconds=40 * 3600) for d in days[:1]]  # a lot of music
    year = _wrap(plays)
    assert (year.rank, year.viewers, year.seconds, year.plays) == (2, 3, 36000, 10)


def test_top_show_and_biggest_night():
    plays = [_play(datetime(2026, 3, 14, 19 + i // 3, tzinfo=timezone.utc), show="The Long Static", episode=i,
                   seconds=2700) for i in range(7)]
    plays += [_play(date(2026, 3, 15), show="The Long Static", episode=7 + i, seconds=2700) for i in range(2)]
    show = _wrap(plays).top_show
    assert show.show.title == "The Long Static"
    assert (show.episodes, show.binge_episodes, show.binge_day) == (9, 7, date(2026, 3, 14))


def test_top_movie_counts_finished_plays_only():
    days = _weekdays(6)
    plays = [_play(days[0], movie="Night Ferry", seconds=7200), _play(days[1], movie="Night Ferry", seconds=7200),
             _play(days[2], movie="Night Ferry", seconds=780),  # resumed for the last minutes: not again
             _play(days[3], movie="Red Meridian", seconds=18000),
             _play(days[4], show="Cold Open"), _play(days[5], show="Cold Open", episode=2)]
    movie = _wrap(plays).top_movie
    assert (movie.movie.title, movie.finished, movie.movies) == ("Night Ferry", 2, 2)


def test_only_tv_has_no_top_movie():
    year = _wrap([_play(d, show="Cold Open", episode=i) for i, d in enumerate(_weekdays(5))])
    assert year.top_movie is None
    assert year.top_show.show.title == "Cold Open"


def test_only_movies_has_no_top_show():
    year = _wrap([_play(d, movie=f"Film {i}") for i, d in enumerate(_weekdays(5))])
    assert year.top_show is None
    assert year.top_movie.movies == 5


def test_habit_order():
    days = _weekdays(10)
    late = [_play(datetime.combine(d, datetime.min.time(), timezone.utc).replace(hour=23), movie=f"Late {i}")
            for i, d in enumerate(days[:4])]
    evening = [_play(d, show="Cold Open", episode=i) for i, d in enumerate(days[4:])]
    assert _wrap(late + evening).habit == ("Night owl", "40% of your plays started after 10 pm.")
    assert _wrap([_play(d, show="Cold Open", episode=i) for i, d in enumerate(days)]).habit == (
        "Prime time", "Most of your plays started around 8pm.")


def test_personality_order():
    # One show, 6 episodes a day on 3 days: Loyalist applies too, but Binger comes first.
    binge = [_play(datetime.combine(d, datetime.min.time(), timezone.utc).replace(hour=14 + i),
                   show="Cold Open", episode=n * 6 + i) for n, d in enumerate(_weekdays(3)) for i in range(6)]
    assert _wrap(binge).personality == ("The Binger", "3 days with 6 or more episodes of one show.")
    days = _weekdays(30)
    explorer = [_play(d, show=f"Show {i}") for i, d in enumerate(days[:15])]
    explorer += [_play(d, movie=f"Film {i}") for i, d in enumerate(days[15:])]
    assert _wrap(explorer).personality == ("The Explorer", "30 different titles. You try everything.")
    regular = [_play(d, show=f"Show {i}") for i, d in enumerate(days[:3])]
    regular += [_play(d, movie=f"Film {i}") for i, d in enumerate(days[3:5])]
    assert _wrap(regular).personality[0] == "The Regular"


def test_genres_and_genre_posters():
    days = _weekdays(5)
    plays = [_play(d, show="Static", episode=i) for i, d in enumerate(days[:3])]
    plays += [_play(days[3], movie="Meridian", seconds=7200), _play(days[4], movie="Plain")]
    cache = {"s-Static": {"genres": ["Sci-Fi"]}, "m-Meridian": {"genres": ["Sci-Fi", "Drama"]}}
    year = _wrap(plays, cache=cache)
    assert year.genres[0] == "sci-fi"
    assert year.genre_titles == 2
    assert year.genre_posters == ["/t/Static", "/t/Meridian"]


def test_firsts_lasts_and_busiest_month():
    plays = [_play(date(2026, 1, 2), movie="Harbour Lights"), _play(date(2026, 10, 27), movie="Night Ferry")]
    plays += [_play(date(2026, 3, 2 + i), show="Cold Open", episode=i) for i in range(4)]
    year = _wrap(plays)
    assert (year.first.title, year.first.day, year.first.poster) == ("Harbour Lights", date(2026, 1, 2),
                                                                     "/t/Harbour Lights")
    assert (year.last.title, year.last.day) == ("Night Ferry", date(2026, 10, 27))
    assert (year.busiest_month, year.busiest_month_seconds) == (3, 4 * 3600)
    assert [pick.title for pick in year.top5] == ["Cold Open", "Harbour Lights", "Night Ferry"]


def test_not_enough_to_wrap():
    days = _weekdays(5)
    assert _wrap([_play(d, movie=f"Film {i}") for i, d in enumerate(days[:4])]) is None
    assert _wrap([_play(d, movie=f"Film {i}", seconds=600) for i, d in enumerate(days)]) is None
