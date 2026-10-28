"""A person's year on Plex in numbers, for /plex wrapped.

Only movie and episode plays count: every song is a play, so music would swamp the rest.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from typing import Callable, List, Optional, Sequence, Tuple

from plexbot.ui.format import hour_label, plural
from plexbot.ui.images import poster_key
from plexbot.utilities import favourite_genres, play_seconds, plex_username

MIN_PLAYS, MIN_SECONDS = 5, 3600  # less than this is "not enough to wrap"
_FINISHED = 45 * 60  # as /plex hot's awards: resuming a movie's last minutes isn't watching it again
_NIGHT = (22, 23, 0, 1, 2, 3, 4)
_MORNING = (5, 6, 7, 8, 9)
_BINGE = 6  # episodes of one show in a day


def default_year(today: date) -> int:
    """This year, but last year in January, so a late Wrapped still works."""
    return today.year - 1 if today.month == 1 else today.year


def year_window(year: int, zone: tzinfo) -> Tuple[datetime, datetime]:
    """1 January 00:00 of `year` up to (not including) the next 1 January, in the server's timezone."""
    return datetime(year, 1, 1, tzinfo=zone), datetime(year + 1, 1, 1, tzinfo=zone)


@dataclass(frozen=True)
class Pick:
    """A movie or a show in someone's year."""

    key: str
    title: str
    poster: Optional[str]  # image key of its latest play
    seconds: int


@dataclass(frozen=True)
class TopShow:
    show: Pick
    episodes: int  # different episodes
    binge_episodes: int  # most episodes of it started on one day
    binge_day: date


@dataclass(frozen=True)
class TopMovie:
    movie: Pick
    finished: int  # times watched to the end
    movies: int  # different movies this year


@dataclass(frozen=True)
class Moment:
    title: str
    poster: Optional[str]
    day: date


@dataclass(frozen=True)
class WrappedYear:
    year: int
    seconds: int
    plays: int
    rank: int
    viewers: int
    top_show: Optional[TopShow]
    top_movie: Optional[TopMovie]
    genres: List[str]  # up to three, lower-case, most first
    genre_titles: int  # titles watched in genres[0]
    genre_posters: List[str]  # up to three image keys from genres[0], most watched first
    hours: List[int]  # plays started in each hour of the day
    peak_hour: int
    habit: Tuple[str, str]  # (label, line)
    first: Moment
    last: Moment
    busiest_month: int
    busiest_month_seconds: int
    personality: Tuple[str, str]  # (title, line)
    top5: List[Pick]


def _key(row: dict) -> str:
    """The movie, or the episode's show."""
    if row["media_type"] == "episode":
        return str(row.get("grandparent_rating_key") or row.get("grandparent_title") or "")
    return str(row.get("rating_key") or row.get("title") or "")


def _title(row: dict) -> str:
    if row["media_type"] == "episode":
        return row.get("grandparent_title") or row.get("title") or "Unknown"
    return row.get("title") or "Unknown"


def _pct(share: float) -> int:
    return round(100 * share)


def _most(counts: dict, earliest=lambda key: key):
    """The key with the highest count; ties go to the earliest key."""
    return min(counts, key=lambda key: (-counts[key], earliest(key)))


def _habit(night: float, morning: float, weekend: float, peak: int) -> Tuple[str, str]:
    if night >= 0.35:
        return "Night owl", f"{_pct(night)}% of your plays started after 10 pm."
    if morning >= 0.25:
        return "Early bird", f"{_pct(morning)}% of your plays started before 10 am."
    if weekend >= 0.5:
        return "Weekend warrior", f"{_pct(weekend)}% of your watch time was on weekends."
    return "Prime time", f"Most of your plays started around {hour_label(peak)}."


def wrap_year(plays: Sequence[Tuple[datetime, dict]], username: str,
              item_for: Callable[[str], Optional[dict]], year: int) -> Optional[WrappedYear]:
    """`username`'s year from everyone's plays in it; None when there's not enough to wrap.

    item_for(rating_key) returns the media cache item or None.
    """
    totals = Counter()
    mine = []
    for started, row in plays:
        if row.get("media_type") not in ("movie", "episode"):
            continue
        user = plex_username(row)
        totals[user] += play_seconds(row)
        if user == username:
            mine.append((started, row))
    seconds = totals[username]
    if len(mine) < MIN_PLAYS or seconds < MIN_SECONDS:
        return None
    mine.sort(key=lambda play: play[0])

    picks, kinds = {}, {}
    pick_seconds = Counter()
    episodes = defaultdict(set)
    show_days = Counter()  # (show, date) -> episodes started that day
    finished = Counter()  # movie -> plays watched to the end
    hours = [0] * 24
    months = Counter()
    weekend = 0
    for started, row in mine:
        key, length = _key(row), play_seconds(row)
        pick_seconds[key] += length
        picks[key] = (_title(row), poster_key(row))  # the latest play's poster wins
        kinds[key] = row["media_type"]
        if row["media_type"] == "episode":
            episodes[key].add(str(row.get("rating_key")))
            show_days[key, started.date()] += 1
        elif row.get("watched_status") == 1 and length >= _FINISHED:
            finished[key] += 1
        hours[started.hour] += 1
        months[started.month] += length
        weekend += length if started.weekday() >= 5 else 0

    every = {key: Pick(key, title, poster, pick_seconds[key]) for key, (title, poster) in picks.items()}
    ranked = sorted(every.values(), key=lambda pick: (-pick.seconds, pick.title))
    shows = [pick for pick in ranked if kinds[pick.key] == "episode"]
    movies = [pick for pick in ranked if kinds[pick.key] == "movie"]

    top_show = None
    if shows:
        show = shows[0]
        days = {day: count for (key, day), count in show_days.items() if key == show.key}
        day = _most(days)
        top_show = TopShow(show, len(episodes[show.key]), days[day], day)
    top_movie = None
    if movies:
        movie = min(movies, key=lambda pick: (-finished[pick.key], -pick.seconds, pick.title))
        top_movie = TopMovie(movie, finished[movie.key], len(movies))

    genres = favourite_genres([row for _started, row in mine], item_for)
    in_genre = [pick for pick in ranked
                if genres and genres[0] in {g.lower() for g in (item_for(pick.key) or {}).get("genres") or []}]

    peak = _most(dict(enumerate(hours)))
    night = sum(hours[h] for h in _NIGHT) / len(mine)
    morning = sum(hours[h] for h in _MORNING) / len(mine)
    busiest = _most(months)
    binge_days = {day for (_key_, day), count in show_days.items() if count >= _BINGE}
    rewatched = sum(1 for count in finished.values() if count >= 2)
    movie_seconds = sum(pick.seconds for pick in movies)
    if len(binge_days) >= 3:
        personality = ("The Binger", f"{len(binge_days)} days with {_BINGE} or more episodes of one show.")
    elif top_show and top_show.show.seconds >= 0.4 * seconds:
        share = _pct(top_show.show.seconds / seconds)
        personality = ("The Loyalist", f"{top_show.show.title} was {share}% of your year.")
    elif rewatched >= 3:
        personality = ("The Rewatcher", f"You went back to {rewatched} movies for another round.")
    elif movie_seconds >= 0.6 * seconds:
        personality = ("The Cinephile", f"{plural(len(movies), 'movie')}, and hardly a show in sight.")
    elif night >= 0.35:
        personality = ("The Night Owl", f"{_pct(night)}% of your plays started after 10 pm.")
    elif len(ranked) >= 30:
        personality = ("The Explorer", f"{len(ranked)} different titles. You try everything.")
    else:
        personality = ("The Regular", "Steady and dependable, like a good popcorn bucket.")

    def moment(play) -> Moment:
        started, row = play
        return Moment(_title(row), poster_key(row), started.date())

    return WrappedYear(
        year=year, seconds=seconds, plays=len(mine),
        rank=1 + sum(1 for total in totals.values() if total > seconds), viewers=len(totals),
        top_show=top_show, top_movie=top_movie,
        genres=genres, genre_titles=len(in_genre),
        genre_posters=[pick.poster for pick in in_genre if pick.poster][:3],
        hours=hours, peak_hour=peak, habit=_habit(night, morning, weekend / seconds, peak),
        first=moment(mine[0]), last=moment(mine[-1]),
        busiest_month=busiest, busiest_month_seconds=months[busiest],
        personality=personality, top5=ranked[:5],
    )
