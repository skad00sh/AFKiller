"""Foreground/frontmost application detection."""

from __future__ import annotations

import sys
from collections.abc import Iterable

from afkiller.editors import Editor


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


def foreground_editor(editors: Iterable[Editor]) -> Editor | None:
    """The watched editor that is currently frontmost, or None.

    On macOS the foreground name is the app's ``localizedName`` (e.g. ``Code``); on Windows
    it's the executable (e.g. ``Code.exe``). Match against each editor's ``focus_aliases``
    and its Windows exe / stem so both platforms resolve."""
    name = foreground_app_name()
    if not name:
        return None
    low = name.lower()
    stem = low[:-4] if low.endswith(".exe") else low
    for ed in editors:
        if low in ed.focus_aliases or low == ed.win_exe or stem in ed.focus_aliases:
            return ed
    return None
