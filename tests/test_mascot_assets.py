"""Every mascot slot ships a PNG (what the bot sends), its SVG source and an AI prompt."""

import io

import pytest
from PIL import Image

from plexbot.ui.images import MASCOT_DIR, MASCOT_SLOTS

PROMPTS = (
    (MASCOT_DIR / "PROMPTS.md").read_text(encoding="utf-8") if (MASCOT_DIR / "PROMPTS.md").exists() else ""
)


@pytest.mark.parametrize("slot", MASCOT_SLOTS)
def test_slot_is_complete(slot):
    png = MASCOT_DIR / f"{slot}.png"
    assert png.exists() and (MASCOT_DIR / f"{slot}.svg").exists()
    assert f"## {slot}" in PROMPTS
    data = png.read_bytes()
    assert len(data) < 250_000  # sent with every response that uses it
    with Image.open(io.BytesIO(data)) as image:
        assert image.mode == "RGBA" and image.size[0] == image.size[1] <= 1024
