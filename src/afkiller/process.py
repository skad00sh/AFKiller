"""Find and terminate editor processes (VS Code-based IDEs).

Generic over the editor set: callers pass the ``Editor`` definitions to act on (see
editors.py), so nothing here is hardcoded to a particular IDE."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Iterator

import psutil

from afkiller.editors import Editor

_IS_WIN = sys.platform == "win32"


def _match(proc: psutil.Process, editors: Iterable[Editor]) -> Editor | None:
    """The editor this process is the *main* process of, or None.

    macOS: match the app-bundle path ``/<mac_app>.app/Contents/MacOS/`` — robust even when
    the executable is the generic ``Electron`` (e.g. Kiro), and it excludes helper children
    (which live under ``/Contents/Frameworks/``). Windows: exact executable-name match."""
    try:
        if _IS_WIN:
            name = (proc.info.get("name") or "").lower()
            if not name:
                return None
            for ed in editors:
                if name == ed.win_exe:
                    return ed
        else:
            exe = proc.info.get("exe") or ""
            for ed in editors:
                if f"/{ed.mac_app}.app/Contents/MacOS/" in exe:
                    return ed
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    return None


def _iter_running(editors: Iterable[Editor]) -> Iterator[tuple[psutil.Process, Editor]]:
    """Yield (process, editor) for each live main process of a watched editor. Helper
    children don't match — only the editor's main process does."""
    editors = tuple(editors)
    for proc in psutil.process_iter(attrs=["pid", "name", "exe"]):
        ed = _match(proc, editors)
        if ed is not None:
            yield proc, ed


def running_editors(editors: Iterable[Editor]) -> list[Editor]:
    """Distinct watched editors currently running, in first-seen order."""
    seen: list[Editor] = []
    for _proc, ed in _iter_running(editors):
        if ed not in seen:
            seen.append(ed)
    return seen


def any_running(editors: Iterable[Editor]) -> bool:
    return next(_iter_running(editors), None) is not None


def quit_graceful(editors: Iterable[Editor]) -> bool:
    """Ask each running watched editor to quit cleanly (its own UI handles unsaved-file
    prompts). Returns True if a quit signal was issued, False if none were running."""
    procs = list(_iter_running(editors))
    if not procs:
        return False

    running: list[Editor] = []
    for _proc, ed in procs:
        if ed not in running:
            running.append(ed)

    if sys.platform == "darwin":
        issued = False
        for ed in running:
            try:
                subprocess.run(
                    ["osascript", "-e", f'tell application "{ed.mac_app}" to quit'],
                    check=False,
                    timeout=5,
                )
                issued = True
            except (subprocess.SubprocessError, OSError):
                continue
        return issued

    if _IS_WIN:
        import ctypes
        from ctypes import wintypes

        WM_CLOSE = 0x0010
        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )

        pids = {proc.pid for proc, _ed in procs}
        posted = False

        def _cb(hwnd: int, _lparam: int) -> bool:
            nonlocal posted
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                posted = True
            return True

        EnumWindows(EnumWindowsProc(_cb), 0)
        return posted

    # Fallback: SIGTERM the matched parents.
    for proc, _ed in procs:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return True


def kill_force(editors: Iterable[Editor]) -> bool:
    """Hard-kill every watched editor's process tree (parent + children). Returns True if
    anything was killed."""
    parents = [proc for proc, _ed in _iter_running(editors)]
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
