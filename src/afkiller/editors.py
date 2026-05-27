"""Registry of supported VS Code-based editors.

This is the only IDE-specific data in the app: the per-editor identifiers used to detect
whether an editor is running / frontmost and to quit it. ``process.py`` and ``focus.py``
contain the generic OS logic and consume this registry, so adding another editor is a
one-line change here.

To add an editor, append an ``Editor`` below. macOS values come from the app bundle's
``Info.plist`` (``CFBundleExecutable`` = ``mac_process``, the ``.app`` name = ``mac_app``);
``focus_aliases`` are the lowercased names the OS reports for the frontmost app."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Editor:
    key: str  # stable identifier used in config (never rename — would orphan configs)
    display_name: str  # shown in settings, e.g. "VS Code"
    mac_app: str  # macOS .app / AppleScript application name
    mac_process: str  # macOS main-process name (CFBundleExecutable), lowercased for compare
    win_exe: str  # Windows executable, lowercased
    focus_aliases: tuple[str, ...]  # lowercased names the foreground app may report


# Identifiers verified on macOS where marked; Windsurf/Kiro use best-known values pending a
# real-install check (see the plan's risks). VS Code's process name "code" is generic but an
# exact (not substring) match is safe — Xcode reports "Xcode", helpers report "Code Helper".
EDITORS: tuple[Editor, ...] = (
    Editor(
        key="vscode",
        display_name="VS Code",
        mac_app="Visual Studio Code",
        mac_process="code",
        win_exe="code.exe",
        focus_aliases=("code", "visual studio code"),
    ),
    Editor(
        key="cursor",
        display_name="Cursor",
        mac_app="Cursor",
        mac_process="cursor",
        win_exe="cursor.exe",
        focus_aliases=("cursor",),
    ),
    Editor(
        key="windsurf",
        display_name="Windsurf",
        mac_app="Windsurf",
        mac_process="windsurf",
        win_exe="windsurf.exe",
        focus_aliases=("windsurf",),
    ),
    Editor(
        key="antigravity",
        display_name="Antigravity",
        mac_app="Antigravity",
        mac_process="antigravity",
        win_exe="antigravity.exe",
        focus_aliases=("antigravity",),
    ),
    Editor(
        key="kiro",
        display_name="Kiro",
        mac_app="Kiro",
        mac_process="kiro",
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
