"""Cursor Assassin orchestrator: watcher thread + tray icon."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import pystray

from cursor_assassin import config as cfg_mod
from cursor_assassin import focus, idle, process, warning
from cursor_assassin.tray import build_menu, make_icon_image

PAUSE_DURATION_SEC = 30 * 60
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
        self.paused_until: float = 0.0

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
                self.cfg,
                countdown_text=lambda: self._countdown_text,
                toggle_trigger=self._toggle_trigger,
                set_threshold=self._set_threshold,
                set_close_mode=self._set_close_mode,
                toggle_pause=self._toggle_pause,
                is_paused=self._is_paused,
                quit_cursor_now=self._quit_cursor_now,
                quit_app=self._quit_app,
            ),
        )

    # ----- tray menu callbacks -----

    def _save(self) -> None:
        cfg_mod.save(self.cfg)

    def _toggle_trigger(self, key: str) -> None:
        with self.lock:
            t = self.cfg.triggers[key]
            t.enabled = not t.enabled
            self._save()
        self._refresh_menu()

    def _set_threshold(self, key: str, minutes: int) -> None:
        with self.lock:
            self.cfg.triggers[key].threshold_minutes = minutes
            self._save()
        self._refresh_menu()

    def _set_close_mode(self, mode: str) -> None:
        with self.lock:
            self.cfg.close_mode = mode
            self._save()
        self._refresh_menu()

    def _toggle_pause(self) -> None:
        with self.lock:
            if self._is_paused():
                self.paused_until = 0.0
            else:
                self.paused_until = time.monotonic() + PAUSE_DURATION_SEC
        self._refresh_menu()

    def _is_paused(self) -> bool:
        return self.paused_until > time.monotonic()

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

        # graceful_warn
        cancelled = warning.show_warning(self.cfg.warning_seconds)
        if cancelled:
            # User wants to keep Cursor open — reset *all* trigger clocks so we
            # don't immediately re-fire.
            now = time.monotonic()
            self._reset_trigger_timers(now)
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
            mins_left = int((self.paused_until - now) // 60) + 1
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


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
