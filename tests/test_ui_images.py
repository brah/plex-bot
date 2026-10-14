"""ui.images: attachments, mascots, Plex images, poster strips and poster tint."""

import asyncio
import io

import pytest
from PIL import Image

import ui.images as images
from ui.images import (
    Attachment,
    current_frame,
    dominant_colour,
    mascot,
    plex_image,
    poster_key,
    poster_strip,
)


def png(colour=(255, 0, 0), size=(200, 300)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


class FakeTautulli:
    tautulli_ip = "host:8181"
    use_https = False
    api_key = "key"


class TestAttachment:
    def test_url_and_fresh_files(self):
        attachment = Attachment("poster-1.jpg", b"data")
        assert attachment.url == "attachment://poster-1.jpg"
        first, second = attachment.to_file(), attachment.to_file()
        assert first.filename == "poster-1.jpg" and first.fp.read() == b"data"
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
        assert attachment.filename == "mascot-wave.png" and attachment.data == png()
        images._mascot_bytes.cache_clear()


class TestPlexImage:
    def test_wraps_fetch(self, monkeypatch):
        calls = {}

        async def fake_fetch(ip, key, width, height, *, use_https, api_key, fallback):
            calls.update(ip=ip, key=key, width=width, height=height, fallback=fallback)
            return io.BytesIO(b"img")

        monkeypatch.setattr(images, "fetch_plex_image", fake_fetch)
        result = asyncio.run(plex_image(FakeTautulli(), "/library/1/thumb", filename="p.jpg"))
        assert result == Attachment("p.jpg", b"img")
        assert calls == dict(
            ip="host:8181", key="/library/1/thumb", width=300, height=450, fallback=None
        )

    def test_explicit_fallback_is_passed_through(self, monkeypatch):
        seen = []

        async def fake_fetch(ip, key, width, height, *, use_https, api_key, fallback):
            seen.append(fallback)
            return io.BytesIO(b"img")

        monkeypatch.setattr(images, "fetch_plex_image", fake_fetch)
        asyncio.run(plex_image(FakeTautulli(), "/x", filename="p.jpg", fallback="poster"))
        assert seen == ["poster"]

    def test_missing_key_or_failed_fetch_is_none(self, monkeypatch):
        async def failed(*args, **kwargs):
            return None

        monkeypatch.setattr(images, "fetch_plex_image", failed)
        assert asyncio.run(plex_image(FakeTautulli(), "", filename="p.jpg")) is None
        assert asyncio.run(plex_image(FakeTautulli(), "/x", filename="p.jpg")) is None

    def test_slow_fetch_gives_up(self, monkeypatch):
        async def hangs(*args, **kwargs):
            await asyncio.sleep(10)

        monkeypatch.setattr(images, "fetch_plex_image", hangs)
        monkeypatch.setattr(images, "IMAGE_TIMEOUT", 0.05)
        assert asyncio.run(plex_image(FakeTautulli(), "/x", filename="p.jpg")) is None


class TestSessionImages:
    def test_poster_key_prefers_show_and_album_art(self):
        episode = {"media_type": "episode", "grandparent_thumb": "/show", "thumb": "/ep"}
        assert poster_key(episode) == "/show"
        assert poster_key({"media_type": "track", "parent_thumb": "/album", "thumb": "/t"}) == "/album"
        assert poster_key({"media_type": "movie", "thumb": "/movie"}) == "/movie"
        assert poster_key({"media_type": "movie"}) is None

    def test_current_frame_needs_preview_thumbnails(self, monkeypatch):
        seen = []

        async def fake_fetch(ip, key, width, height, *, use_https, api_key, fallback):
            seen.append((key, width, height, fallback))
            return io.BytesIO(b"frame")

        monkeypatch.setattr(images, "fetch_plex_image", fake_fetch)
        with_index = {"bif_thumb": "/library/parts/9/indexes/sd/60000", "indexes": 1}
        assert asyncio.run(current_frame(FakeTautulli(), with_index)).data == b"frame"
        assert seen == [("/library/parts/9/indexes/sd/60000", 640, 360, None)]
        assert asyncio.run(current_frame(FakeTautulli(), {"bif_thumb": "/x", "indexes": 0})) is None
        assert asyncio.run(current_frame(FakeTautulli(), {"indexes": 1})) is None


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
        strip = poster_strip([png()] * 6, extra=4, gap=8)
        with Image.open(io.BytesIO(strip.data)) as im:
            assert im.size == (6 * 200 + 6 * 8 + 200, 300)

    def test_invalid_images_skipped_and_none_when_empty(self):
        assert poster_strip([b"not an image"]) is None
        assert poster_strip([]) is None
        assert poster_strip([b"junk", png()]).filename == "posters.png"


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
