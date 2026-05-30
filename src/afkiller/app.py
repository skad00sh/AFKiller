"""AFKiller orchestrator: watcher thread + tray icon."""

from __future__ import annotations

import subprocess
import sys
import time
import threading
from dataclasses import dataclass
from typing import Optional

import pystray

from afkiller import config as cfg_mod
from afkiller import databricks, editors, focus, idle, process, settings, warning
from afkiller.config import PAUSE_DURATION_SEC
from afkiller.tray import build_menu, make_icon_image

TICK_SECONDS = 1.0
# Re-scan for the live Databricks SSH proxy process every N ticks (not every tick).
DB_DETECT_INTERVAL_TICKS = 5
# Re-poll the cluster's runtime (a ~1-2s `clusters get`) for the DBU meter every N ticks.
COST_POLL_INTERVAL_TICKS = 30


def _fmt_uptime(seconds: float) -> str:
    """Compact duration like '2h13m' or '7m'."""
    minutes = int(seconds // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


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

        # Databricks cluster-stop bookkeeping.
        self.cursor_closed_at: Optional[float] = None
        self.last_connected_cluster_id: Optional[str] = None
        self._db_detect_counter = 0
        # Whether a remote SSH session is currently attached (None = unknown / not running).
        # Refreshed on the same throttled cadence as cluster detection.
        self._ssh_active: Optional[bool] = None
        # Last SSH-connected state we notified about (None = no baseline yet, so the first
        # reading is adopted silently and only later changes pop a notification).
        self._ssh_notified: Optional[bool] = None

        # DBU cost meter: a cached cluster-runtime snapshot, refreshed off-thread on a slow
        # cadence (clusters-get is a network call). Uptime is extrapolated between polls from
        # the monotonic timestamp so the meter climbs smoothly.
        self._cluster_runtime: Optional[databricks.ClusterRuntime] = None
        self._cluster_runtime_at = 0.0  # monotonic time the snapshot was taken
        # Seed at the interval so the first eligible tick polls immediately; thereafter it
        # polls every COST_POLL_INTERVAL_TICKS regardless of success, so a failing CLI call
        # retries on the slow cadence instead of every tick.
        self._cost_poll_counter = COST_POLL_INTERVAL_TICKS
        self._cost_poll_inflight = False

        # Idle-but-running alert: clock since the cluster went RUNNING-with-no-SSH, and a
        # once-per-idle-spell fired flag.
        self._cluster_idle_since: Optional[float] = None
        self._idle_alert_fired = False

        # Last computed remaining-seconds across enabled triggers.
        self._countdown_text = "Starting..."

        self.icon = pystray.Icon(
            "afkiller",
            icon=make_icon_image(),
            title="AFKiller",
            menu=build_menu(
                countdown_text=lambda: self._countdown_text,
                cost_text=self.cost_text,
                cost_enabled=self.cost_enabled,
                open_settings=self._open_settings,
                toggle_pause=self._toggle_pause,
                is_paused=self._is_paused,
                quit_editor_now=self._quit_editor_now,
                stop_cluster_now=self._stop_cluster_now,
                databricks_enabled=lambda: self.cfg.databricks.enabled,
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
        return [sys.executable, "-m", "afkiller", *extra]

    def _open_settings(self) -> None:
        if self._settings_proc is not None and self._settings_proc.poll() is None:
            return  # already open
        try:
            self._settings_proc = subprocess.Popen(self._self_cmd("--settings"))
        except OSError as e:
            print(f"[afkiller] failed to open settings: {e}", file=sys.stderr)

    def _editors(self) -> tuple[editors.Editor, ...]:
        """The editor definitions currently being watched (from config)."""
        return editors.enabled_editors(self.cfg.watched_editors)

    def _quit_editor_now(self) -> None:
        if self.cfg.close_mode == "force_kill":
            process.kill_force(self._editors())
        else:
            process.quit_graceful(self._editors())

    def _stop_cluster_now(self) -> None:
        """Manual 'Stop cluster now'. Runs in a thread so the blocking CLI call doesn't
        freeze the tray menu."""
        threading.Thread(target=self._do_stop_cluster_now, daemon=True).start()

    def _do_stop_cluster_now(self) -> None:
        db = self.cfg.databricks
        target = (
            db.cluster_id
            or self.last_connected_cluster_id
            or databricks.detect_active_cluster_id()
        )
        if not target:
            self._notify("AFKiller", "No Databricks cluster detected to stop.")
            return
        rt = databricks.cluster_runtime(target, db.profile)
        ok = databricks.terminate_cluster(target, db.profile)
        saved = 0.0
        if ok:
            minutes_idle = (
                (time.monotonic() - self.cursor_closed_at) / 60.0
                if self.cursor_closed_at is not None
                else 0.0
            )
            saved = self._credit_saved(rt.autotermination_minutes if rt else None, minutes_idle)
        if db.notify:
            self._notify(
                "AFKiller",
                self._stop_message(target, saved) if ok else f"Failed to stop cluster {target}",
            )

    def _notify(self, title: str, message: str) -> None:
        """Best-effort native notification; falls back to osascript on macOS."""
        try:
            self.icon.notify(message, title)
            return
        except Exception:
            pass
        if sys.platform == "darwin":
            msg = message.replace('"', '\\"')
            ttl = title.replace('"', '\\"')
            try:
                subprocess.run(
                    ["osascript", "-e", f'display notification "{msg}" with title "{ttl}"'],
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass

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
        when an editor is running (since closing a non-running app is a no-op)."""
        out: dict[str, float] = {}
        if not process.any_running(self._editors()):
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
            process.kill_force(self._editors())
            return

        # graceful_warn: run the countdown in a child process (Tk can't live in
        # the tray process on macOS). Exit 0 => ran out (close); nonzero/crash
        # => treat as cancelled, so we never kill the editor on an ambiguous result.
        cancelled = True
        try:
            proc = subprocess.run(self._self_cmd("--warn", str(self.cfg.warning_seconds)))
            cancelled = proc.returncode != 0
        except OSError as e:
            print(f"[afkiller] failed to show warning: {e}", file=sys.stderr)

        if cancelled:
            # Keep the editor open — reset *all* trigger clocks so we don't re-fire.
            self._reset_trigger_timers(time.monotonic())
            return
        process.quit_graceful(self._editors())

    def _credit_saved(self, autotermination_minutes: Optional[int], minutes_idle: float) -> float:
        """Estimate DBUs saved by stopping the cluster early and add to the persistent total.
        Returns the credited amount (0 if cost is off or it can't be estimated). Every stop
        (while cost tracking is on) bumps the stop count, even when the DBU credit is 0."""
        if not self.cfg.cost.enabled:
            return 0.0
        rate = self.cfg.cost.dbu_per_hour
        saved = 0.0
        if rate > 0 and autotermination_minutes and autotermination_minutes > 0:
            saved_minutes = max(0.0, autotermination_minutes - minutes_idle)
            saved = saved_minutes / 60.0 * rate
        stats = cfg_mod.load_stats()
        stats.total_dbus_saved += saved
        stats.stops_count += 1
        cfg_mod.save_stats(stats)
        return saved

    @staticmethod
    def _stop_message(target: str, saved: float) -> str:
        msg = f"Stopping Databricks cluster {target}"
        if saved > 0:
            msg += f" · ≈{saved:.2f} DBU saved"
        return msg

    def _maybe_stop_cluster(self, now: float) -> None:
        """Called each tick while Cursor is not running. Stops the Databricks cluster once
        it has been closed for the configured grace window, unless an SSH session is still
        active. Updates the countdown text either way."""
        db = self.cfg.databricks
        target = db.cluster_id or self.last_connected_cluster_id
        if not db.enabled or not target or self.cursor_closed_at is None:
            # Feature off, no cluster known, or never observed a close this session.
            self._set_countdown("Editor not running")
            return

        window = db.delay_minutes * 60
        remaining = window - (now - self.cursor_closed_at)
        if db.require_system_idle:
            # Fire only once both Cursor-closed *and* OS-idle exceed the window.
            remaining = max(remaining, window - idle.seconds_since_input())

        if remaining > 0:
            mm, ss = divmod(int(remaining), 60)
            self._set_countdown(f"Cluster stops in {mm:02d}:{ss:02d}")
            return

        # Window elapsed — re-check the guard, then act.
        if databricks.ssh_session_active(target):
            # A session is live again (e.g. terminal ssh) — hold off and re-arm.
            self.cursor_closed_at = now
            self._set_countdown("Cluster in use (SSH active)")
            return

        rt = databricks.cluster_runtime(target, db.profile)
        state = rt.state if rt else None
        if state and state != databricks.RUNNING:
            # Already stopped/stopping/pending — nothing to do.
            self.cursor_closed_at = None
            self._set_countdown(f"Cluster {state.lower()}")
            return

        self._set_countdown("Stopping cluster…")
        if databricks.terminate_cluster(target, db.profile):
            minutes_idle = (now - self.cursor_closed_at) / 60.0 if self.cursor_closed_at else 0.0
            saved = self._credit_saved(rt.autotermination_minutes if rt else None, minutes_idle)
            if db.notify:
                self._notify("AFKiller", self._stop_message(target, saved))
            self.cursor_closed_at = None  # done; don't re-fire
        else:
            # CLI missing/unauth/network error — retry after another full window, no spam.
            self.cursor_closed_at = now

    # ----- DBU cost meter -----

    def _maybe_poll_runtime(self) -> None:
        """Refresh the cached cluster runtime off-thread, on a slow cadence, when cost
        tracking is on and we know which cluster to look at. One poll in flight at a time."""
        cost = self.cfg.cost
        target = self.cfg.databricks.cluster_id or self.last_connected_cluster_id
        if not cost.enabled or not target or self._cost_poll_inflight:
            return
        self._cost_poll_counter += 1
        if self._cost_poll_counter >= COST_POLL_INTERVAL_TICKS:
            self._cost_poll_counter = 0
            self._cost_poll_inflight = True
            threading.Thread(target=self._do_poll_runtime, args=(target,), daemon=True).start()

    def _do_poll_runtime(self, target: str) -> None:
        try:
            db = self.cfg.databricks
            rt = databricks.cluster_runtime(
                target, db.profile, cli=databricks.resolve_cli(db.cli_path)
            )
            with self.lock:
                self._cluster_runtime = rt
                self._cluster_runtime_at = time.monotonic()
        finally:
            self._cost_poll_inflight = False

    def _maybe_ssh_change_notify(self) -> None:
        """Pop a notification when the SSH session connects or disconnects. Tracks the last
        notified state so it fires only on a real edge; the first reading sets the baseline
        silently (so an already-open session at startup doesn't trigger a stray popup)."""
        connected = self._ssh_active is True
        if not self.cfg.notify_on_ssh_change:
            self._ssh_notified = connected  # keep baseline current so re-enabling is quiet
            return
        if self._ssh_notified is None:
            self._ssh_notified = connected  # establish baseline, no notification
            return
        if connected == self._ssh_notified:
            return
        self._ssh_notified = connected
        if connected:
            target = self.cfg.databricks.cluster_id or self.last_connected_cluster_id or "cluster"
            self._notify("AFKiller", f"SSH connected to {target} — AFKiller is watching.")
        else:
            self._notify("AFKiller", "SSH session closed.")

    def _maybe_idle_alert(self, now: float) -> None:
        """Fire one notification when the cluster has been RUNNING with no SSH session for the
        configured window. Independent of auto-stop — useful when auto-stop is off, or to
        catch a cluster started outside Cursor. Resets when SSH returns or the cluster stops."""
        cost = self.cfg.cost
        if not cost.enabled or not cost.idle_alert_enabled:
            self._cluster_idle_since = None
            self._idle_alert_fired = False
            return
        rt = self._cluster_runtime
        idle = rt is not None and rt.state == databricks.RUNNING and not self._ssh_active
        if not idle:
            self._cluster_idle_since = None
            self._idle_alert_fired = False
            return
        if self._cluster_idle_since is None:
            self._cluster_idle_since = now
            return
        if self._idle_alert_fired:
            return
        if now - self._cluster_idle_since >= cost.idle_alert_minutes * 60:
            self._idle_alert_fired = True
            target = self.cfg.databricks.cluster_id or self.last_connected_cluster_id or "cluster"
            rate_txt = f" (~{cost.dbu_per_hour:g} DBU/hr)" if cost.dbu_per_hour > 0 else ""
            self._notify(
                "AFKiller",
                f"Cluster {target} has been idle {cost.idle_alert_minutes} min "
                f"and is still running{rate_txt}.",
            )

    def cost_enabled(self) -> bool:
        """Whether to show the cost line in the tray menu."""
        return self.cfg.cost.enabled and self.cfg.cost.show_in_tray

    def cost_text(self) -> str:
        """Tray line for the DBU meter, read from the cached runtime snapshot."""
        c = self.cfg.cost
        rt = self._cluster_runtime
        if rt is None:
            return "Cluster cost: unknown"
        if rt.state != databricks.RUNNING or rt.uptime_seconds is None:
            return "Cluster not running"
        uptime = rt.uptime_seconds + max(0.0, time.monotonic() - self._cluster_runtime_at)
        dur = _fmt_uptime(uptime)
        if c.dbu_per_hour <= 0:
            return f"Running {dur} · set DBU/hr in Settings"
        dbus = uptime / 3600.0 * c.dbu_per_hour
        return f"≈ {dbus:.2f} DBU · {dur}"

    def _watcher(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                # Never let an exception kill the watcher thread.
                print(f"[afkiller] watcher tick error: {e}", file=sys.stderr)
            self.stop_event.wait(TICK_SECONDS)

    def _tick(self) -> None:
        self._maybe_reload_config()
        now = time.monotonic()
        running = process.any_running(self._editors())

        # Track Cursor lifecycle.
        if running and not self._cursor_running_prev:
            # First time we've seen Cursor since startup or last close.
            self.cursor_first_seen_at = now
            self.last_cursor_foreground_at = now
            self.cursor_closed_at = None  # reopened → cancel any pending cluster-stop
        elif not running:
            self.cursor_first_seen_at = None
            self.last_cursor_foreground_at = None
            if self._cursor_running_prev:
                # running → closed transition: arm the cluster-stop grace timer. Only after
                # an observed transition, so we never touch a cluster that was already up
                # with Cursor closed at startup.
                self.cursor_closed_at = now
        self._cursor_running_prev = running

        # Update "last editor foreground" timestamp if a watched editor is frontmost now.
        if running and focus.foreground_editor(self._editors()) is not None:
            self.last_cursor_foreground_at = now

        # While Cursor runs, periodically scan for the live remote SSH session. It serves two
        # purposes: remembering which cluster the tunnel uses (to stop it after Cursor closes,
        # when the proxy process is usually gone) and gating whether we may close Cursor at all
        # — closing it with no session attached frees no cluster, so it's pointless.
        db = self.cfg.databricks
        cost = self.cfg.cost
        want_detect = (
            self.cfg.close_only_when_ssh_connected
            or self.cfg.notify_on_ssh_change
            or db.enabled
            or cost.enabled
        )
        # While Cursor runs we need the SSH signal for the close gate; the idle alert also
        # needs it after Cursor closes (a cluster can sit idle with no tunnel), so keep
        # scanning when that's on.
        if want_detect and (running or cost.idle_alert_enabled):
            self._db_detect_counter += 1
            if self._ssh_active is None or self._db_detect_counter >= DB_DETECT_INTERVAL_TICKS:
                self._db_detect_counter = 0
                ids, self._ssh_active = databricks.scan_sessions()
                if (db.enabled or cost.enabled) and not db.cluster_id and ids:
                    self.last_connected_cluster_id = ids[0]
        elif not running:
            self._ssh_active = None

        # Notify on SSH connect/disconnect (rising/falling edge of the signal above; closing
        # Cursor flips it to None == disconnected, which counts as a falling edge).
        self._maybe_ssh_change_notify()

        # DBU meter: keep the cached cluster runtime fresh (off-thread, slow cadence) so the
        # tray can show consumption. Runs whether Cursor is open or closed (the cluster bills
        # either way) as long as we know which cluster to look at.
        self._maybe_poll_runtime()
        self._maybe_idle_alert(now)

        # Paused: show paused text and skip trigger evaluation.
        if self._is_paused():
            mins_left = int((self.cfg.paused_until_epoch - time.time()) // 60) + 1
            self._set_countdown(f"Paused ({mins_left} min left)")
            return

        if not running:
            self._maybe_stop_cluster(now)
            return

        # Only close Cursor while it holds a remote SSH session. Without one, closing it frees
        # no cluster — so hold off and keep the trigger clocks reset until a session connects.
        if self.cfg.close_only_when_ssh_connected and not self._ssh_active:
            self._reset_trigger_timers(now)
            self._set_countdown("Editor open (no SSH session)")
            return

        remaining = self._remaining_for_enabled_triggers(now)
        if not remaining:
            self._set_countdown("No triggers enabled")
            return

        tripped = [k for k, v in remaining.items() if v <= 0]
        if tripped:
            self._set_countdown("Closing editor...")
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
            self.icon.title = f"AFKiller — {text}"
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
