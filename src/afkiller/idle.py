"""OS-level idle detection (seconds since last keyboard/mouse input)."""

from __future__ import annotations

import sys


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def seconds_since_input() -> float:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return millis / 1000.0

elif sys.platform == "darwin":
    from Quartz import (  # type: ignore[import-not-found]
        CGEventSourceSecondsSinceLastEventType,
        kCGAnyInputEventType,
        kCGEventSourceStateCombinedSessionState,
    )

    def seconds_since_input() -> float:
        return float(
            CGEventSourceSecondsSinceLastEventType(
                kCGEventSourceStateCombinedSessionState,
                kCGAnyInputEventType,
            )
        )

else:
    def seconds_since_input() -> float:
        return 0.0
