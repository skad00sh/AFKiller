"""Tray icon image + menu construction."""

from __future__ import annotations

from typing import Callable

import pystray
from PIL import Image, ImageDraw

from cursor_assassin.config import Config, TRIGGER_KEYS

TRIGGER_LABELS: dict[str, str] = {
    "system_idle": "System idle",
    "cursor_unfocused": "Cursor unfocused",
    "hard_cap": "Hard cap",
}

# Per-trigger preset thresholds (minutes).
TRIGGER_PRESETS: dict[str, tuple[int, ...]] = {
    "system_idle": (5, 10, 15, 30, 60),
    "cursor_unfocused": (5, 10, 15, 20, 30, 60),
    "hard_cap": (60, 120, 240, 480),
}


def make_icon_image() -> Image.Image:
    """Draw a simple red 'CA' badge for the tray."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=(192, 57, 43, 255))
    d.text((14, 16), "CA", fill=(255, 255, 255, 255))
    return img


def _human_minutes(m: int) -> str:
    if m % 60 == 0 and m >= 60:
        h = m // 60
        return f"{h} h"
    return f"{m} min"


def build_menu(
    cfg: Config,
    *,
    countdown_text: Callable[[], str],
    toggle_trigger: Callable[[str], None],
    set_threshold: Callable[[str, int], None],
    set_close_mode: Callable[[str], None],
    toggle_pause: Callable[[], None],
    is_paused: Callable[[], bool],
    quit_cursor_now: Callable[[], None],
    quit_app: Callable[[], None],
) -> pystray.Menu:
    def _trigger_label(key: str) -> Callable[[pystray.MenuItem], str]:
        def _f(_item: pystray.MenuItem) -> str:
            t = cfg.triggers[key]
            return f"{TRIGGER_LABELS[key]} ({_human_minutes(t.threshold_minutes)})"
        return _f

    def _trigger_submenu(key: str) -> pystray.Menu:
        # pystray rejects actions whose co_argcount > 2, and default-valued
        # params count toward co_argcount. Capture loop vars via factory
        # closures so each action stays a strict 2-arg (icon, item) callable.
        def _on_toggle(k: str) -> Callable[[object, object], None]:
            return lambda _icon, _item: toggle_trigger(k)

        def _on_set_threshold(k: str, p: int) -> Callable[[object, object], None]:
            return lambda _icon, _item: set_threshold(k, p)

        items: list[pystray.MenuItem] = [
            pystray.MenuItem(
                "Enabled",
                _on_toggle(key),
                checked=lambda _item, k=key: cfg.triggers[k].enabled,
            ),
            pystray.Menu.SEPARATOR,
        ]
        for preset in TRIGGER_PRESETS[key]:
            items.append(
                pystray.MenuItem(
                    _human_minutes(preset),
                    _on_set_threshold(key, preset),
                    checked=lambda _item, k=key, p=preset: cfg.triggers[k].threshold_minutes == p,
                    radio=True,
                )
            )
        return pystray.Menu(*items)

    triggers_menu = pystray.Menu(
        *[
            pystray.MenuItem(_trigger_label(key), _trigger_submenu(key))
            for key in TRIGGER_KEYS
        ]
    )

    close_mode_menu = pystray.Menu(
        pystray.MenuItem(
            "Graceful w/ warning",
            lambda _icon, _item: set_close_mode("graceful_warn"),
            checked=lambda _item: cfg.close_mode == "graceful_warn",
            radio=True,
        ),
        pystray.MenuItem(
            "Force kill",
            lambda _icon, _item: set_close_mode("force_kill"),
            checked=lambda _item: cfg.close_mode == "force_kill",
            radio=True,
        ),
    )

    return pystray.Menu(
        pystray.MenuItem(lambda _i: countdown_text(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Triggers", triggers_menu),
        pystray.MenuItem("Close mode", close_mode_menu),
        pystray.MenuItem(
            "Pause 30 min",
            lambda _icon, _item: toggle_pause(),
            checked=lambda _item: is_paused(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Cursor now", lambda _icon, _item: quit_cursor_now()),
        pystray.MenuItem("Quit Cursor Assassin", lambda _icon, _item: quit_app()),
    )
