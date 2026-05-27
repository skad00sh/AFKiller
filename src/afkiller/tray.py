"""Tray icon image + menu construction.

The menu is a thin launcher: trigger/threshold/close-mode configuration lives
in the settings window (settings.py), since the native menu dismisses on every
click. The menu keeps only quick actions that are one-shot by nature."""

from __future__ import annotations

from typing import Callable

import pystray
from PIL import Image, ImageDraw


def make_icon_image() -> Image.Image:
    """Draw a simple red 'CA' badge for the tray."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=(192, 57, 43, 255))
    d.text((14, 16), "CA", fill=(255, 255, 255, 255))
    return img


def build_menu(
    *,
    countdown_text: Callable[[], str],
    cost_text: Callable[[], str],
    cost_enabled: Callable[[], bool],
    open_settings: Callable[[], None],
    toggle_pause: Callable[[], None],
    is_paused: Callable[[], bool],
    quit_cursor_now: Callable[[], None],
    stop_cluster_now: Callable[[], None],
    databricks_enabled: Callable[[], bool],
    quit_app: Callable[[], None],
) -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(lambda _i: countdown_text(), None, enabled=False),
        pystray.MenuItem(
            lambda _i: cost_text(),
            None,
            enabled=False,
            visible=lambda _item: cost_enabled(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Settings…", lambda _icon, _item: open_settings()),
        pystray.MenuItem(
            "Pause 30 min",
            lambda _icon, _item: toggle_pause(),
            checked=lambda _item: is_paused(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Cursor now", lambda _icon, _item: quit_cursor_now()),
        pystray.MenuItem(
            "Stop cluster now",
            lambda _icon, _item: stop_cluster_now(),
            visible=lambda _item: databricks_enabled(),
        ),
        pystray.MenuItem("Quit AFKiller", lambda _icon, _item: quit_app()),
    )
