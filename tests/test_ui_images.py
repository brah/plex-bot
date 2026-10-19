"""ui.images: attachments, mascots, Plex images, poster strips and poster tint."""

import asyncio
import io

import pytest
from PIL import Image

import plexbot.ui.images as images
from plexbot.ui.images import (
    Attachment,
    current_frame,
    dominant_colour,
    mascot,
    plex_image,
    poster_key,
    poster_strip,
    sectioned_strip,
)


def png(colour=(255, 0, 0), size=(200, 300)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


class FakeTautulli:
    """Records image requests; answers with `image` (bytes, None, or a coroutine function)."""

    def __init__(self, image=b"img"):
        self.image = image
        self.calls = []

    async def get_image(self, key, width=300, height=450, fallback=None):
        self.calls.append((key, width, height, fallback))
        return await self.image() if callable(self.image) else self.image


class TestAttachment:
    def test_url_and_fresh_files(self):
        attachment = Attachment("poster-1.jpg", b"data")
        assert attachment.url == "attachment://poster-1.jpg"
        first, second = attachment.to_file(), attachment.to_file()
        assert first.filename == "poster-1.jpg"
        assert first.fp.read() == b"data"
        assert second.fp.read() == b"data"  # each send needs its own unread stream


class TestMascot:
    def test_missing_slot_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "MASCOT_DIR", tmp_path)
        images._mascot_bytes.cache_clear()
        assert mascot("mascot-nope") is None

    def test_existing_slot(self, tmp_path, monkeypatch):
        (tmp_path / "mascot-wave.png").write_bytes(png())
        monkeypatch.setattr(images, "MASCOT_DIR", tmp_path)
        images._mascot_bytes.cache_clear()
        attachment = mascot("mascot-wave")
        assert attachment.filename == "mascot-wave.png"
        assert attachment.data == png()
        images._mascot_bytes.cache_clear()


class TestPlexImage:
    def test_wraps_get_image(self):
        tautulli = FakeTautulli()
        result = asyncio.run(plex_image(tautulli, "/library/1/thumb", filename="p.jpg"))
        assert result == Attachment("p.jpg", b"img")
        assert tautulli.calls == [("/library/1/thumb", 300, 450, None)]

    def test_explicit_fallback_is_passed_through(self):
        tautulli = FakeTautulli()
        asyncio.run(plex_image(tautulli, "/x", filename="p.jpg", fallback="poster"))
        assert tautulli.calls[0][3] == "poster"

    def test_missing_key_or_failed_fetch_is_none(self):
        tautulli = FakeTautulli(image=None)
        assert asyncio.run(plex_image(tautulli, "", filename="p.jpg")) is None
        assert asyncio.run(plex_image(tautulli, "/x", filename="p.jpg")) is None

    def test_slow_fetch_gives_up(self, monkeypatch):
        async def hangs():
            await asyncio.sleep(10)

        monkeypatch.setattr(images, "IMAGE_TIMEOUT", 0.05)
        assert asyncio.run(plex_image(FakeTautulli(image=hangs), "/x", filename="p.jpg")) is None


class TestSessionImages:
    def test_poster_key_prefers_show_and_album_art(self):
        episode = {"media_type": "episode", "grandparent_thumb": "/show", "thumb": "/ep"}
        assert poster_key(episode) == "/show"
        assert poster_key({"media_type": "track", "parent_thumb": "/album", "thumb": "/t"}) == "/album"
        assert poster_key({"media_type": "movie", "thumb": "/movie"}) == "/movie"
        assert poster_key({"media_type": "movie"}) is None

    def test_current_frame_needs_preview_thumbnails(self):
        tautulli = FakeTautulli(image=b"frame")
        with_index = {"bif_thumb": "/library/parts/9/indexes/sd/60000", "indexes": 1}
        assert asyncio.run(current_frame(tautulli, with_index)).data == b"frame"
        assert tautulli.calls == [("/library/parts/9/indexes/sd/60000", 640, 360, None)]
        assert asyncio.run(current_frame(tautulli, {"bif_thumb": "/x", "indexes": 0})) is None
        assert asyncio.run(current_frame(tautulli, {"indexes": 1})) is None


class TestPosterStrip:
    def test_single_poster(self):
        strip = poster_strip([png(size=(200, 300))])
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (200, 300)

    def test_several_posters_with_gaps(self):
        strip = poster_strip([png(), png((0, 255, 0)), png((0, 0, 255))], gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (3 * 200 + 2 * 8, 300)

    def test_extra_count_adds_a_tile(self):
        strip = poster_strip([png()] * 5, extra=4, gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (6 * 200 + 5 * 8, 300)
            assert im.getpixel((5 * 208 + 100, 150))[:3] == (32, 34, 37)  # the "+4" tile, last

    def test_wraps_into_two_even_rows(self):
        for count, per_row in ((12, 6), (10, 5), (7, 4)):
            strip = poster_strip([png()] * count, gap=8)
            with Image.open(io.BytesIO(strip.data)) as im:
                assert im.size == (per_row * 200 + (per_row - 1) * 8, 2 * 300 + 8), count
        # The "+N" tile counts toward the rows: 6 posters and it make 4 + 3.
        strip = poster_strip([png()] * 6, extra=4, gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (4 * 200 + 3 * 8, 608)
            assert im.getpixel((2 * 208 + 100, 308 + 150))[:3] == (32, 34, 37)  # end of the second row

    def test_invalid_images_skipped_and_none_when_empty(self):
        assert poster_strip([b"not an image"]) is None
        assert poster_strip([]) is None
        assert poster_strip([b"junk", png()]).filename == "posters.png"


class TestSectionedStrip:
    def test_a_row_per_section_behind_its_divider(self):
        strip = sectioned_strip([("MOVIES", 0xE5A00D, [png()] * 3), ("TV", 0x4A90D9, [png((0, 0, 255))])], gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (4 * 200 + 3 * 8, 2 * 300 + 8)  # the widest row: divider + 3 posters
            assert im.getpixel((100, 150))[:3] == (32, 34, 37)  # the MOVIES divider leads the first row
            assert im.getpixel((2, 150))[:3] == (0xE5, 0xA0, 0x0D)  # edged in the movie colour
            assert im.getpixel((208 + 100, 308 + 150))[:3] == (0, 0, 255)  # TV's poster, after its divider
            assert im.getpixel((2 * 208 + 100, 308 + 150))[3] == 0  # the shorter row leaves the rest clear

    def test_sections_without_posters_are_left_out(self):
        strip = sectioned_strip([("MOVIES", 0xE5A00D, []), ("TV", 0x4A90D9, [png()])], gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (2 * 200 + 8, 300)
        assert sectioned_strip([("MOVIES", 0xE5A00D, [b"junk"])]) is None


class TestDominantColour:
    def test_solid_colour(self):
        assert dominant_colour(png((255, 0, 0))) == 0xFF0000

    def test_prefers_a_vivid_colour_over_black(self):
        im = Image.new("RGB", (100, 100), (0, 0, 0))
        im.paste((200, 30, 30), (0, 0, 30, 100))
        out = io.BytesIO()
        im.save(out, format="PNG")
        assert (dominant_colour(out.getvalue()) >> 16) & 0xFF > 150

    def test_invalid_bytes(self):
        assert dominant_colour(b"nope") is None


@pytest.mark.parametrize("slot", images.MASCOT_SLOTS)
def test_mascot_slots_are_named_consistently(slot):
    assert slot.startswith("mascot-")


class FakeImageSession:
    """Stands in for Tautulli's aiohttp session; records the params of each image request."""

    closed = False

    def __init__(self):
        self.requests = []

    def get(self, url, params=None, timeout=None):
        self.requests.append(params)

        class Response:
            status = 200
            content_type = "image/jpeg"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def read(self):
                return b"jpeg"

        return Response()


@pytest.mark.parametrize("kwargs, expected", [({"fallback": "poster"}, "poster"), ({}, None)])
def test_get_image_fallback_param(kwargs, expected):
    from plexbot.tautulli_wrapper import Tautulli

    tautulli = Tautulli(api_key="key", tautulli_ip="host:8181")
    tautulli.session = FakeImageSession()  # the client's own session, reused for every image
    assert asyncio.run(tautulli.get_image("/library/x", **kwargs)) == b"jpeg"
    (params,) = tautulli.session.requests
    assert params["img"] == "/library/x"
    assert params["cmd"] == "pms_image_proxy"
    assert params.get("fallback") == expected
    assert ("fallback" in params) is (expected is not None)
