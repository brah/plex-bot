"""/plex wrapped's slides: every slide draws at full size, empty ones are skipped, and nothing overflows."""

import io
from dataclasses import replace
from datetime import date

from PIL import Image

from plexbot.ui.wrapped import SIZE, fit_lines, headline_font, story
from plexbot.wrapped_year import Moment, Pick, TopMovie, TopShow, WrappedYear
from fakes import _png

SHOW = Pick("s-1", "The Long Static", "/t/static", 32 * 3600)
MOVIE = Pick("m-1", "Night Ferry", "/t/ferry", 6 * 3600)


def _year(**changes) -> WrappedYear:
    year = WrappedYear(
        year=2026, seconds=312 * 3600, plays=1284, rank=2, viewers=14,
        top_show=TopShow(SHOW, 41, 9, date(2026, 3, 14)),
        top_movie=TopMovie(MOVIE, 3, 23),
        genres=["sci-fi", "drama", "comedy"], genre_titles=12, genre_posters=["/t/static", "/t/ferry"],
        hours=[3, 2, 1, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16, 22, 30, 38, 44, 50, 20], peak_hour=22,
        habit=("Night owl", "38% of your plays started after 10 pm."),
        first=Moment("Harbour Lights", "/t/harbour", date(2026, 1, 1)),
        last=Moment("Night Ferry", "/t/ferry", date(2026, 10, 27)),
        busiest_month=3, busiest_month_seconds=41 * 3600,
        personality=("The Binger", "3 days with 6 or more episodes of one show."),
        top5=[SHOW, MOVIE, Pick("s-2", "Harbour Lights", "/t/harbour", 3600), Pick("s-3", "Cold Open", None, 1800),
              Pick("m-2", "Red Meridian", "/t/red", 900)],
    )
    return replace(year, **changes)


POSTERS = {key: _png() for key in ("/t/static", "/t/ferry", "/t/harbour", "/t/red")}


def _sizes(slides):
    sizes = []
    for slide in slides:
        with Image.open(io.BytesIO(slide.data)) as image:
            sizes.append(image.size)
    return sizes


def test_story_draws_every_slide():
    slides = story(_year(), name="Sam", avatar=None, posters=POSTERS)
    assert [slide.filename for slide in slides] == [f"wrapped-{n}.png" for n in range(1, 10)]
    assert _sizes(slides) == [SIZE] * 9


def test_story_skips_slides_with_nothing_to_say():
    slides = story(_year(top_movie=None, genres=[], genre_posters=[]), name="Sam", avatar=None, posters=POSTERS)
    assert len(slides) == 7
    assert slides[-1].filename == "wrapped-7.png"
    no_show = story(_year(top_show=None), name="Sam", avatar=None, posters=POSTERS)
    assert len(no_show) == 8


def test_titles_that_dont_fit_still_draw():
    year = _year(top_show=TopShow(replace(SHOW, title="A" * 200), 41, 9, date(2026, 3, 14)),
                 top_movie=TopMovie(replace(MOVIE, title="進撃の巨人"), 3, 23))
    slides = story(year, name="Zoë 😀", avatar=None, posters=POSTERS)
    assert _sizes(slides) == [SIZE] * 9


def test_missing_posters_and_avatar():
    assert len(story(_year(), name="Sam", avatar=None, posters={})) == 9
    assert len(story(_year(), name="Sam", avatar=_png(), posters=POSTERS)) == 9
    assert len(story(_year(), name="Sam", avatar=b"not an image", posters={"/t/static": b"junk"})) == 9


def test_fit_lines():
    font, lines = fit_lines("A" * 200, 500, 120, 60)
    assert len(lines) <= 2
    assert all(font.getlength(line) <= 500 for line in lines)
    assert lines[-1].endswith("…")
    font, lines = fit_lines("DUNE", 500, 120, 60)
    assert (font.size, lines) == (120, ["DUNE"])


def test_headline_font_falls_back():
    assert headline_font("SEVERANCE", 80).path.endswith("BebasNeue-Regular.ttf")
    assert headline_font("進撃", 80).path.endswith("DejaVuSans-Bold.ttf")
