"""Registry of supported VS Code-based editors.

This is the only IDE-specific data in the app: the per-editor identifiers used to detect
whether an editor is running / frontmost and to quit it. ``process.py`` and ``focus.py``
contain the generic OS logic and consume this registry, so adding another editor is a
one-line change here.

macOS detection matches the **app-bundle path** (``/<mac_app>.app/Contents/MacOS/``) rather
than the executable name — some editors ship a generic ``Electron`` executable (e.g. Kiro),
so the bundle name is the reliable signal. Windows matches the executable name (Electron
Windows builds are renamed to the product, e.g. ``Kiro.exe``). ``focus_aliases`` are the
lowercased names the OS reports for the frontmost app (macOS ``localizedName`` /
Windows process name)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Editor:
    key: str  # stable identifier used in config (never rename — would orphan configs)
    display_name: str  # shown in settings, e.g. "VS Code"
    mac_app: str  # macOS .app / AppleScript name; also the bundle matched on macOS
    win_exe: str  # Windows executable, lowercased
    focus_aliases: tuple[str, ...]  # lowercased names the foreground app may report


# macOS app names verified on this machine for VS Code / Cursor / Antigravity / Kiro;
# Windows exe is best-known for the unverified ones. Note Kiro's macOS executable is the
# generic "Electron" (CFBundleExecutable), which is exactly why we match the bundle path.
EDITORS: tuple[Editor, ...] = (
    Editor(
        key="vscode",
        display_name="VS Code",
        mac_app="Visual Studio Code",
        win_exe="code.exe",
        focus_aliases=("code", "visual studio code"),
    ),
    Editor(
        key="cursor",
        display_name="Cursor",
        mac_app="Cursor",
        win_exe="cursor.exe",
        focus_aliases=("cursor",),
    ),
    Editor(
        key="windsurf",
        display_name="Windsurf",
        mac_app="Windsurf",
        win_exe="windsurf.exe",
        focus_aliases=("windsurf",),
    ),
    Editor(
        key="antigravity",
        display_name="Antigravity",
        mac_app="Antigravity",
        win_exe="antigravity.exe",
        focus_aliases=("antigravity",),
    ),
    Editor(
        key="kiro",
        display_name="Kiro",
        mac_app="Kiro",
        win_exe="kiro.exe",
        focus_aliases=("kiro",),
    ),
)

KEYS: tuple[str, ...] = tuple(e.key for e in EDITORS)
BY_KEY: dict[str, Editor] = {e.key: e for e in EDITORS}


def enabled_editors(keys: Iterable[str]) -> tuple[Editor, ...]:
    """The editors whose keys appear in ``keys`` (unknown keys ignored), in registry order."""
    wanted = set(keys)
    return tuple(e for e in EDITORS if e.key in wanted)
