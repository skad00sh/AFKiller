"""Foreground/frontmost application detection."""

from __future__ import annotations

import sys


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    import psutil

    def foreground_app_name() -> str | None:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        try:
            return psutil.Process(pid.value).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

elif sys.platform == "darwin":
    from AppKit import NSWorkspace  # type: ignore[import-not-found]

    def foreground_app_name() -> str | None:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        name = app.localizedName()
        return str(name) if name else None

else:
    def foreground_app_name() -> str | None:
        return None


def is_cursor_foreground() -> bool:
    name = foreground_app_name()
    if not name:
        return False
    lowered = name.lower()
    return "cursor" in lowered
