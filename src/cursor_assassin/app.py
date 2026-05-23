"""Cursor Assassin orchestrator: watcher thread + tray icon."""

from __future__ import annotations

import subprocess
import sys
import time
import threading
from dataclasses import dataclass
from typing import Optional

import pystray

from cursor_assassin import config as cfg_mod
from cursor_assassin import focus, idle, process, settings, warning
from cursor_assassin.config import PAUSE_DURATION_SEC
from cursor_assassin.tray import build_menu, make_icon_image

TICK_SECONDS = 1.0


@dataclass
class TriggerState:
    elapsed_seconds: float = 0.0  # for cursor_unfocused & hard_cap
    # system_idle reads OS directly each tick — no stored state needed.


class App:
    def __init__(self) -> None:
        self.cfg = cfg_mod.load()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self._settings_proc: Optional[subprocess.Popen] = None
        self._cfg_mtime = self._config_mtime()

        # Per-trigger bookkeeping (timestamps).
        self.last_cursor_foreground_at: Optional[float] = None
        self.cursor_first_seen_at: Optional[float] = None
        self._cursor_running_prev = False

        # Last computed remaining-seconds across enabled triggers.
        self._countdown_text = "Starting..."

        self.icon = pystray.Icon(
            "cursor-assassin",
            icon=make_icon_image(),
            title="Cursor Assassin",
            menu=build_menu(
                countdown_text=lambda: self._countdown_text,
                open_settings=self._open_settings,
                toggle_pause=self._toggle_pause,
                is_paused=self._is_paused,
                quit_cursor_now=self._quit_cursor_now,
                quit_app=self._quit_app,
            ),
        )

    # ----- config persistence -----

    def _config_mtime(self) -> float:
        try:
            return cfg_mod.config_path().stat().st_mtime
        except OSError:
            return 0.0

    def _save(self) -> None:
        cfg_mod.save(self.cfg)
        self._cfg_mtime = self._config_mtime()  # our own write, don't self-reload

    def _maybe_reload_config(self) -> None:
        """Pick up edits made by the settings child process."""
        mtime = self._config_mtime()
        if mtime and mtime != self._cfg_mtime:
            self._cfg_mtime = mtime
            with self.lock:
                self.cfg = cfg_mod.load()
            self._refresh_menu()

    # ----- tray menu callbacks -----

    def _toggle_pause(self) -> None:
        with self.lock:
            self.cfg.paused_until_epoch = (
                time.time() + PAUSE_DURATION_SEC if not self._is_paused() else 0.0
            )
            self._save()
        self._refresh_menu()

    def _is_paused(self) -> bool:
        return time.time() < self.cfg.paused_until_epoch

    @staticmethod
    def _self_cmd(*extra: str) -> list[str]:
        """Command to relaunch this app in a sub-mode, in dev or frozen builds."""
        if getattr(sys, "frozen", False):
            return [sys.executable, *extra]
        return [sys.executable, "-m", "cursor_assassin", *extra]

    def _open_settings(self) -> None:
        if self._settings_proc is not None and self._settings_proc.poll() is None:
            return  # already open
        try:
            self._settings_proc = subprocess.Popen(self._self_cmd("--settings"))
        except OSError as e:
            print(f"[cursor-assassin] failed to open settings: {e}", file=sys.stderr)

    def _quit_cursor_now(self) -> None:
        if self.cfg.close_mode == "force_kill":
            process.kill_force()
        else:
            process.quit_graceful()

    def _quit_app(self) -> None:
        self.stop_event.set()
        self.icon.stop()

    def _refresh_menu(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            pass

    # ----- watcher loop -----

    def _remaining_for_enabled_triggers(self, now: float) -> dict[str, float]:
        """Returns {trigger_key: seconds_until_trip}. Only includes enabled triggers
        when Cursor is running (since closing a non-running app is a no-op)."""
        out: dict[str, float] = {}
        if not process.cursor_running():
            return out

        triggers = self.cfg.triggers

        if triggers["system_idle"].enabled:
            threshold = triggers["system_idle"].threshold_minutes * 60
            idle_s = idle.seconds_since_input()
            out["system_idle"] = max(0.0, threshold - idle_s)

        if triggers["cursor_unfocused"].enabled and self.last_cursor_foreground_at is not None:
            threshold = triggers["cursor_unfocused"].threshold_minutes * 60
            elapsed = now - self.last_cursor_foreground_at
            out["cursor_unfocused"] = max(0.0, threshold - elapsed)

        if triggers["hard_cap"].enabled and self.cursor_first_seen_at is not None:
            threshold = triggers["hard_cap"].threshold_minutes * 60
            elapsed = now - self.cursor_first_seen_at
            out["hard_cap"] = max(0.0, threshold - elapsed)

        return out

    def _reset_trigger_timers(self, now: float) -> None:
        self.last_cursor_foreground_at = now
        self.cursor_first_seen_at = now

    def _execute_close(self, tripped_trigger: str) -> None:
        if self.cfg.close_mode == "force_kill":
            process.kill_force()
            return

        # graceful_warn: run the countdown in a child process (Tk can't live in
        # the tray process on macOS). Exit 0 => ran out (close); nonzero/crash
        # => treat as cancelled, so we never kill Cursor on an ambiguous result.
        cancelled = True
        try:
            proc = subprocess.run(self._self_cmd("--warn", str(self.cfg.warning_seconds)))
            cancelled = proc.returncode != 0
        except OSError as e:
            print(f"[cursor-assassin] failed to show warning: {e}", file=sys.stderr)

        if cancelled:
            # Keep Cursor open — reset *all* trigger clocks so we don't re-fire.
            self._reset_trigger_timers(time.monotonic())
            return
        process.quit_graceful()

    def _watcher(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                # Never let an exception kill the watcher thread.
                print(f"[cursor-assassin] watcher tick error: {e}", file=sys.stderr)
            self.stop_event.wait(TICK_SECONDS)

    def _tick(self) -> None:
        self._maybe_reload_config()
        now = time.monotonic()
        running = process.cursor_running()

        # Track Cursor lifecycle.
        if running and not self._cursor_running_prev:
            # First time we've seen Cursor since startup or last close.
            self.cursor_first_seen_at = now
            self.last_cursor_foreground_at = now
        elif not running:
            self.cursor_first_seen_at = None
            self.last_cursor_foreground_at = None
        self._cursor_running_prev = running

        # Update "last cursor foreground" timestamp if it's the frontmost app now.
        if running and focus.is_cursor_foreground():
            self.last_cursor_foreground_at = now

        # Paused: show paused text and skip trigger evaluation.
        if self._is_paused():
            mins_left = int((self.cfg.paused_until_epoch - time.time()) // 60) + 1
            self._set_countdown(f"Paused ({mins_left} min left)")
            return

        if not running:
            self._set_countdown("Cursor not running")
            return

        remaining = self._remaining_for_enabled_triggers(now)
        if not remaining:
            self._set_countdown("No triggers enabled")
            return

        tripped = [k for k, v in remaining.items() if v <= 0]
        if tripped:
            self._set_countdown("Closing Cursor...")
            self._execute_close(tripped[0])
            return

        # Show smallest remaining countdown.
        key_min = min(remaining, key=lambda k: remaining[k])
        secs = int(remaining[key_min])
        mm, ss = divmod(secs, 60)
        self._set_countdown(f"Closes in {mm:02d}:{ss:02d} ({key_min.replace('_', ' ')})")

    def _set_countdown(self, text: str) -> None:
        if text == self._countdown_text:
            return
        self._countdown_text = text
        cfg_mod.write_status(text)  # publish for the settings window
        # Tooltip on hover (Windows) / menu bar title hint (macOS limited).
        try:
            self.icon.title = f"Cursor Assassin — {text}"
        except Exception:
            pass
        self._refresh_menu()

    # ----- lifecycle -----

    def run(self) -> None:
        watcher_thread = threading.Thread(target=self._watcher, daemon=True)
        watcher_thread.start()
        # pystray.run() blocks the main thread (required on macOS).
        self.icon.run()
        self.stop_event.set()
        watcher_thread.join(timeout=2)


def _ensure_tcl_paths() -> None:
    """python-build-standalone (what uv installs) ships Tcl/Tk under
    base_prefix/lib but doesn't set TCL_LIBRARY/TK_LIBRARY, so Tk can fail with
    'cannot find a usable init.tcl'. Point it at the bundled data. PyInstaller
    sets these itself in frozen builds, so skip there."""
    if getattr(sys, "frozen", False):
        return
    import glob
    import os

    lib = os.path.join(sys.base_prefix, "lib")
    for var, pattern, marker in (
        ("TCL_LIBRARY", "tcl*", "init.tcl"),
        ("TK_LIBRARY", "tk*", "tk.tcl"),
    ):
        if os.environ.get(var):
            continue
        for d in sorted(glob.glob(os.path.join(lib, pattern)), reverse=True):
            if os.path.isfile(os.path.join(d, marker)):
                os.environ[var] = d
                break


def main() -> None:
    _ensure_tcl_paths()
    argv = sys.argv[1:]
    if argv and argv[0] == "--settings":
        settings.run_standalone()
        return
    if argv and argv[0] == "--warn":
        seconds = int(argv[1]) if len(argv) > 1 else 30
        cancelled = warning.run_standalone(seconds)
        # Nonzero == cancelled/keep-open; 0 == proceed to close.
        raise SystemExit(10 if cancelled else 0)
    App().run()


if __name__ == "__main__":
    main()
