"""Generate app icons from the shared drawing in cursor_assassin.tray.

Outputs into assets/:
  - icon.png   (1024px master)
  - icon.ico   (Windows .exe / installer; multi-size)
  - icon.icns  (macOS .app; built via iconutil from an .iconset)

Run on macOS to get the .icns: ``uv run python scripts/generate_icons.py``.
The generated files are committed so CI just consumes them."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from cursor_assassin.tray import _draw_icon

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def main() -> None:
    ASSETS.mkdir(exist_ok=True)

    _draw_icon(1024, with_background=True).save(ASSETS / "icon.png")

    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    _draw_icon(256, with_background=True).save(
        ASSETS / "icon.ico", sizes=[(s, s) for s in ico_sizes]
    )

    # macOS .icns: render each required size natively, then let iconutil pack them.
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
        _draw_icon(px, with_background=True).save(iconset / f"icon_{name}.png")

    if sys.platform == "darwin" and shutil.which("iconutil"):
        subprocess.run(
            ["iconutil", "-c", "icns", "-o", str(ASSETS / "icon.icns"), str(iconset)],
            check=True,
        )
        shutil.rmtree(iconset)
        print("wrote assets/icon.png, icon.ico, icon.icns")
    else:
        print("wrote assets/icon.png, icon.ico; iconutil unavailable — kept assets/icon.iconset")


if __name__ == "__main__":
    main()
