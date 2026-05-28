"""Generate platform icons from assets/icon.png.

Produces, into assets/:
  - icon.ico   (Windows .exe; multi-size)
  - icon.icns  (macOS .app; built via iconutil from an .iconset)

The master ``assets/icon.png`` (app icon, created using ChatGPT) is the single source.
Run on macOS to also get the ``.icns``:

    uv run python scripts/generate_icons.py

The generated ``icon.ico``/``icon.icns`` are committed so CI just consumes them."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SRC = ASSETS / "icon.png"


def main() -> None:
    master = Image.open(SRC).convert("RGBA")

    # Windows .ico: a few sizes packed into one file (max 256 for ICO).
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    master.save(ASSETS / "icon.ico", sizes=[(s, s) for s in ico_sizes])
    print("wrote assets/icon.ico")

    # macOS .icns: render each required size, then let iconutil pack the .iconset.
    iconset = ASSETS / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    specs = [
        (16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
        (128, "128x128"), (256, "128x128@2x"), (256, "256x256"), (512, "256x256@2x"),
        (512, "512x512"), (1024, "512x512@2x"),
    ]
    for px, name in specs:
        master.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{name}.png")

    if sys.platform == "darwin" and shutil.which("iconutil"):
        subprocess.run(
            ["iconutil", "-c", "icns", "-o", str(ASSETS / "icon.icns"), str(iconset)],
            check=True,
        )
        shutil.rmtree(iconset)
        print("wrote assets/icon.icns")
    else:
        print("iconutil unavailable — kept assets/icon.iconset (run on macOS for .icns)")


if __name__ == "__main__":
    main()
