"""Render mascot SVGs to transparent PNGs with a headless Chromium browser. Dev-only.

Discord can't display SVG, so the bot ships PNGs; this regenerates them from the
SVG sources. Needs Edge or Chrome (set CHROME_PATH to override).

Usage: python scripts/render_mascots.py [slot ...] [--src assets/mascot] [--out assets/mascot] [--size 512]
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "assets" / "mascot"


def find_browser() -> str:
    candidates = [
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        shutil.which("microsoft-edge"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    sys.exit("No Edge/Chrome found; set CHROME_PATH.")


def render(browser: str, svg: Path, png: Path, size: int) -> None:
    png.unlink(missing_ok=True)
    subprocess.run(
        [
            browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--default-background-color=00000000", f"--window-size={size},{size}",
            f"--screenshot={png.resolve()}", svg.resolve().as_uri(),
        ],
        check=True, capture_output=True, timeout=60,
    )
    # Edge's launcher can exit before its child writes the file: wait until its size stops changing.
    deadline, last = time.monotonic() + 30, -1
    while (size := png.stat().st_size if png.exists() else 0) == 0 or size != last:
        if time.monotonic() > deadline:
            sys.exit(f"The browser never wrote {png}.")
        last = size
        time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slots", nargs="*", help="slot names (default: every SVG in --src)")
    parser.add_argument("--src", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()

    browser = find_browser()
    if args.slots:
        svgs = [args.src / f"{slot}.svg" for slot in args.slots]
    else:
        svgs = sorted(args.src.glob("*.svg"))
    args.out.mkdir(parents=True, exist_ok=True)
    for svg in svgs:
        png = args.out / f"{svg.stem}.png"
        render(browser, svg, png, args.size)
        print(f"{svg.name} -> {png}")


if __name__ == "__main__":
    main()
