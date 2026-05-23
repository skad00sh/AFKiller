"""Find and terminate Cursor processes."""

from __future__ import annotations

import subprocess
import sys

import psutil


def _matches_cursor(proc: psutil.Process) -> bool:
    try:
        name = (proc.name() or "").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    if sys.platform == "win32":
        return name == "cursor.exe"
    # macOS: main app process is "Cursor"; "Cursor Helper" are renderer/gpu/utility children.
    return name == "cursor"


def find_cursor_processes() -> list[psutil.Process]:
    procs: list[psutil.Process] = []
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        if _matches_cursor(proc):
            procs.append(proc)
    return procs


def cursor_running() -> bool:
    return bool(find_cursor_processes())


def quit_graceful() -> bool:
    """Ask Cursor to quit cleanly. Cursor's own UI handles unsaved-file prompts.
    Returns True if a quit signal was issued, False if Cursor wasn't running."""
    if not cursor_running():
        return False

    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Cursor" to quit'],
                check=False,
                timeout=5,
            )
            return True
        except (subprocess.SubprocessError, OSError):
            return False

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        WM_CLOSE = 0x0010
        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )

        cursor_pids = {p.pid for p in find_cursor_processes()}
        posted = False

        def _cb(hwnd: int, _lparam: int) -> bool:
            nonlocal posted
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in cursor_pids and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                posted = True
            return True

        EnumWindows(EnumWindowsProc(_cb), 0)
        return posted

    # Fallback: SIGTERM the parent processes.
    for proc in find_cursor_processes():
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return True


def kill_force() -> bool:
    """Hard-kill every Cursor process (parent + children). Returns True if any process was killed."""
    parents = find_cursor_processes()
    if not parents:
        return False

    targets: list[psutil.Process] = []
    seen: set[int] = set()
    for parent in parents:
        for proc in [parent, *parent.children(recursive=True)]:
            if proc.pid in seen:
                continue
            seen.add(proc.pid)
            targets.append(proc)

    for proc in targets:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs(targets, timeout=5)
    return True
