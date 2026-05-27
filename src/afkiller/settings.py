"""Settings window (Tk/ttk), run as its own process.

On macOS a process may own exactly one NSApplication on its main thread, and
both pystray and Tk demand it — so they cannot coexist in one process. The tray
app therefore launches this module in a child process (``--settings``) where Tk
owns the main thread. State is shared via config.toml: changes apply live by
saving to disk, and the tray process reloads on file change.

Identical layout on macOS and Windows; ttk draws each with native styling."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk

from afkiller import config as cfg_mod
from afkiller import databricks, process
from afkiller.config import (
    DATABRICKS_DELAY_PRESETS,
    PAUSE_DURATION_SEC,
    TRIGGER_KEYS,
    TRIGGER_LABELS,
    TRIGGER_PRESETS,
    human_delay,
    human_minutes,
)


def run_standalone() -> None:
    """Show the settings window on this process's main thread and block until
    closed. Loads config from disk and writes every change straight back."""
    cfg = cfg_mod.load()

    def _set_trigger_enabled(key: str, value: bool) -> None:
        cfg.triggers[key].enabled = value
        cfg_mod.save(cfg)

    def _set_threshold(key: str, minutes: int) -> None:
        cfg.triggers[key].threshold_minutes = minutes
        cfg_mod.save(cfg)

    def _set_close_mode(mode: str) -> None:
        cfg.close_mode = mode
        cfg_mod.save(cfg)

    def _set_pause(value: bool) -> None:
        cfg.paused_until_epoch = time.time() + PAUSE_DURATION_SEC if value else 0.0
        cfg_mod.save(cfg)

    def _is_paused() -> bool:
        return time.time() < cfg.paused_until_epoch

    def _quit_cursor_now() -> None:
        if cfg.close_mode == "force_kill":
            process.kill_force()
        else:
            process.quit_graceful()

    root = tk.Tk()
    root.title("AFKiller — Settings")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
        root.lift()
    except tk.TclError:
        pass

    frm = ttk.Frame(root, padding=16)
    frm.grid(row=0, column=0, sticky="nsew")
    frm.columnconfigure(1, weight=1)

    # ----- live status line (published by the tray process) -----
    status_var = tk.StringVar(value=cfg_mod.read_status())
    ttk.Label(frm, textvariable=status_var, font=("Helvetica", 12, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    ttk.Separator(frm, orient="horizontal").grid(
        row=1, column=0, columnspan=2, sticky="ew", pady=(8, 10)
    )

    # ----- triggers -----
    ttk.Label(frm, text="Triggers", font=("Helvetica", 11, "bold")).grid(
        row=2, column=0, columnspan=2, sticky="w", pady=(0, 4)
    )

    row = 3
    for key in TRIGGER_KEYS:
        enabled_var = tk.BooleanVar(value=cfg.triggers[key].enabled)

        def _on_toggle(k: str = key, var: tk.BooleanVar = enabled_var) -> None:
            _set_trigger_enabled(k, var.get())

        ttk.Checkbutton(
            frm, text=TRIGGER_LABELS[key], variable=enabled_var, command=_on_toggle
        ).grid(row=row, column=0, sticky="w", pady=2)

        presets = TRIGGER_PRESETS[key]
        current = cfg.triggers[key].threshold_minutes
        label_to_min = {human_minutes(p): p for p in presets}
        if current not in presets:  # surface a non-preset saved value
            label_to_min[human_minutes(current)] = current

        thr_var = tk.StringVar(value=human_minutes(current))
        combo = ttk.Combobox(
            frm, textvariable=thr_var, values=list(label_to_min.keys()),
            state="readonly", width=9,
        )

        def _on_threshold(
            _event: object = None,
            k: str = key,
            var: tk.StringVar = thr_var,
            mapping: dict[str, int] = label_to_min,
        ) -> None:
            minutes = mapping.get(var.get())
            if minutes is not None:
                _set_threshold(k, minutes)

        combo.bind("<<ComboboxSelected>>", _on_threshold)
        combo.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=2)
        row += 1

    ssh_gate_var = tk.BooleanVar(value=cfg.close_only_when_ssh_connected)

    def _on_ssh_gate() -> None:
        cfg.close_only_when_ssh_connected = ssh_gate_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Only close Cursor when connected over SSH",
        variable=ssh_gate_var, command=_on_ssh_gate,
    ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
    row += 1

    ssh_notify_var = tk.BooleanVar(value=cfg.notify_on_ssh_change)

    def _on_ssh_notify() -> None:
        cfg.notify_on_ssh_change = ssh_notify_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Notify on SSH connect / disconnect",
        variable=ssh_notify_var, command=_on_ssh_notify,
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    ttk.Separator(frm, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=(10, 10)
    )
    row += 1

    # ----- close mode -----
    ttk.Label(frm, text="Close mode", font=("Helvetica", 11, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
    )
    row += 1

    mode_var = tk.StringVar(value=cfg.close_mode)

    def _on_mode() -> None:
        _set_close_mode(mode_var.get())

    ttk.Radiobutton(
        frm, text="Graceful (warn first)", variable=mode_var,
        value="graceful_warn", command=_on_mode,
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1
    ttk.Radiobutton(
        frm, text="Force kill (immediate)", variable=mode_var,
        value="force_kill", command=_on_mode,
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    ttk.Separator(frm, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=(10, 10)
    )
    row += 1

    # ----- pause -----
    pause_var = tk.BooleanVar(value=_is_paused())

    def _on_pause() -> None:
        _set_pause(pause_var.get())

    ttk.Checkbutton(
        frm, text="Pause for 30 min", variable=pause_var, command=_on_pause
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    # ----- Databricks cluster stop -----
    ttk.Separator(frm, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=(10, 10)
    )
    row += 1
    ttk.Label(frm, text="Databricks cluster stop", font=("Helvetica", 11, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
    )
    row += 1

    db = cfg.databricks
    db_status_var = tk.StringVar(value="")
    AUTO_CLUSTER = "(auto-detect)"
    cluster_label_to_id: dict[str, str] = {}

    def _run_async(work, on_result) -> None:
        """Run a blocking CLI call off the Tk thread; deliver the result back on the main
        thread via after() polling (Tk is not thread-safe)."""
        holder: dict[str, object] = {}

        def worker() -> None:
            try:
                holder["result"] = work()
            except Exception as e:  # noqa: BLE001 - surfaced as a status message
                holder["result"] = e

        threading.Thread(target=worker, daemon=True).start()

        def poll() -> None:
            try:
                if "result" in holder:
                    on_result(holder["result"])
                else:
                    root.after(150, poll)
            except tk.TclError:
                pass  # window closed mid-call

        root.after(150, poll)

    db_enabled_var = tk.BooleanVar(value=db.enabled)

    def _on_db_enabled() -> None:
        db.enabled = db_enabled_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Stop the cluster after Cursor closes",
        variable=db_enabled_var, command=_on_db_enabled,
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    ttk.Label(frm, text="CLI profile").grid(row=row, column=0, sticky="w", pady=2)
    profile_var = tk.StringVar(value=db.profile)

    def _on_profile(_e: object = None) -> None:
        db.profile = profile_var.get().strip() or "DEFAULT"
        cfg_mod.save(cfg)

    profile_combo = ttk.Combobox(frm, textvariable=profile_var, width=16, values=[db.profile])
    profile_combo.bind("<<ComboboxSelected>>", _on_profile)
    profile_combo.bind("<FocusOut>", _on_profile)
    profile_combo.bind("<Return>", _on_profile)
    profile_combo.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=2)
    row += 1

    # Populate the profile dropdown from ~/.databrickscfg (async, non-blocking).
    def _profiles_done(result: object) -> None:
        if isinstance(result, Exception) or not result:
            return
        profile_combo["values"] = list(dict.fromkeys([*result, db.profile]))  # type: ignore[misc]

    _run_async(lambda: databricks.list_profiles(databricks.resolve_cli(db.cli_path)), _profiles_done)

    ttk.Label(frm, text="Cluster").grid(row=row, column=0, sticky="w", pady=2)
    cluster_box = ttk.Frame(frm)
    cluster_box.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=2)
    cluster_var = tk.StringVar(value=(db.cluster_id or AUTO_CLUSTER))
    cluster_combo = ttk.Combobox(
        cluster_box, textvariable=cluster_var, values=[AUTO_CLUSTER], width=22,
    )
    cluster_combo.grid(row=0, column=0)

    def _on_cluster(_e: object = None) -> None:
        v = cluster_var.get().strip()
        db.cluster_id = "" if v in ("", AUTO_CLUSTER) else cluster_label_to_id.get(v, v)
        cfg_mod.save(cfg)

    cluster_combo.bind("<<ComboboxSelected>>", _on_cluster)
    cluster_combo.bind("<FocusOut>", _on_cluster)
    cluster_combo.bind("<Return>", _on_cluster)

    def _refresh_clusters() -> None:
        # Show the cluster Cursor is currently connected to (read from the live SSH
        # tunnel), rather than listing every cluster — which is slow in large workspaces.
        prof = profile_var.get().strip()
        db_status_var.set("Detecting connected cluster…")

        def work() -> object:
            cli = databricks.resolve_cli(db.cli_path)
            if not cli:
                return ("err", "databricks CLI not found")
            infos = [
                (cid, *databricks.cluster_info(cid, prof, cli=cli))
                for cid in databricks.detect_active_cluster_ids()
            ]
            return ("ok", infos)

        def done(result: object) -> None:
            if isinstance(result, Exception):
                db_status_var.set("Error detecting cluster")
                return
            kind, payload = result  # type: ignore[misc]
            if kind == "err":
                db_status_var.set(str(payload))
                return
            infos = payload  # list[(id, name, state)]
            cluster_label_to_id.clear()
            labels = [AUTO_CLUSTER]
            for cid, name, state in infos:  # type: ignore[misc]
                label = f"{name or cid} ({state})" if state else (name or cid)
                cluster_label_to_id[label] = cid
                labels.append(label)
            cluster_combo["values"] = labels
            if not infos:
                db_status_var.set(
                    "No connected cluster detected — connect Cursor to a cluster, "
                    "or paste a cluster ID."
                )
            elif len(infos) == 1:
                cid, name, state = infos[0]
                db_status_var.set(f"Connected: {name or cid} ({state or 'unknown'})")
            else:
                db_status_var.set(f"Connected to {len(infos)} clusters — pick one above")

        _run_async(work, done)

    ttk.Button(cluster_box, text="Refresh", width=8, command=_refresh_clusters).grid(
        row=0, column=1, padx=(6, 0)
    )
    row += 1

    ttk.Label(frm, text="Stop after").grid(row=row, column=0, sticky="w", pady=2)
    delay_label_to_min = {human_delay(p): p for p in DATABRICKS_DELAY_PRESETS}
    if db.delay_minutes not in DATABRICKS_DELAY_PRESETS:
        delay_label_to_min[human_delay(db.delay_minutes)] = db.delay_minutes
    delay_var = tk.StringVar(value=human_delay(db.delay_minutes))
    delay_combo = ttk.Combobox(
        frm, textvariable=delay_var, values=list(delay_label_to_min.keys()),
        state="readonly", width=12,
    )

    def _on_delay(_e: object = None) -> None:
        m = delay_label_to_min.get(delay_var.get())
        if m is not None:
            db.delay_minutes = m
            cfg_mod.save(cfg)

    delay_combo.bind("<<ComboboxSelected>>", _on_delay)
    delay_combo.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=2)
    row += 1

    db_idle_var = tk.BooleanVar(value=db.require_system_idle)

    def _on_db_idle() -> None:
        db.require_system_idle = db_idle_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Also require the system to be idle",
        variable=db_idle_var, command=_on_db_idle,
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    db_notify_var = tk.BooleanVar(value=db.notify)

    def _on_db_notify() -> None:
        db.notify = db_notify_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Notify when the cluster is stopped",
        variable=db_notify_var, command=_on_db_notify,
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    def _test_connection() -> None:
        prof = profile_var.get().strip()
        cid = db.cluster_id
        db_status_var.set("Testing…")

        def work() -> object:
            # A fast auth check — no full cluster listing, which can time out in a
            # large workspace.
            cli = databricks.resolve_cli(db.cli_path)
            if not cli:
                return ("err", "databricks CLI not found")
            user = databricks.current_user(prof, cli=cli)
            if user is None:
                return ("err", "CLI found, but auth/profile check failed")
            if cid:
                state = databricks.cluster_state(cid, prof, cli=cli)
                return ("ok", f"Connected as {user} · cluster {state or 'unknown'}")
            return ("ok", f"Connected as {user}")

        def done(result: object) -> None:
            if isinstance(result, Exception):
                db_status_var.set("Error testing connection")
                return
            kind, payload = result  # type: ignore[misc]
            db_status_var.set(str(payload))

        _run_async(work, done)

    def _stop_cluster_now() -> None:
        prof = profile_var.get().strip()
        db_status_var.set("Stopping…")

        def work() -> object:
            target = db.cluster_id or databricks.detect_active_cluster_id()
            if not target:
                return ("none", None)
            return ("done", (target, databricks.terminate_cluster(target, prof)))

        def done(result: object) -> None:
            if isinstance(result, Exception):
                db_status_var.set("Error stopping cluster")
                return
            kind, payload = result  # type: ignore[misc]
            if kind == "none":
                db_status_var.set("No cluster detected to stop")
            else:
                target, ok = payload  # type: ignore[misc]
                db_status_var.set(f"Stopping {target}" if ok else f"Failed to stop {target}")

        _run_async(work, done)

    db_btns = ttk.Frame(frm)
    db_btns.grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
    row += 1
    ttk.Button(db_btns, text="Test connection", command=_test_connection).grid(row=0, column=0)
    ttk.Button(db_btns, text="Stop cluster now", command=_stop_cluster_now).grid(
        row=0, column=1, padx=(8, 0)
    )
    ttk.Label(frm, textvariable=db_status_var, foreground="#555555").grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(2, 0)
    )
    row += 1

    # ----- Cost tracking (DBUs) -----
    ttk.Separator(frm, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=(10, 10)
    )
    row += 1
    ttk.Label(frm, text="Cost tracking (DBUs)", font=("Helvetica", 11, "bold")).grid(
        row=row, column=0, columnspan=2, sticky="w", pady=(0, 4)
    )
    row += 1

    cost = cfg.cost

    cost_enabled_var = tk.BooleanVar(value=cost.enabled)

    def _on_cost_enabled() -> None:
        cost.enabled = cost_enabled_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Track DBU consumption", variable=cost_enabled_var, command=_on_cost_enabled
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    ttk.Label(frm, text="DBU per hour").grid(row=row, column=0, sticky="w", pady=2)
    rate_var = tk.StringVar(value=f"{cost.dbu_per_hour:g}")

    def _on_rate(_e: object = None) -> None:
        raw = rate_var.get().strip()
        try:
            val = max(0.0, float(raw)) if raw else 0.0
        except ValueError:
            rate_var.set(f"{cost.dbu_per_hour:g}")  # revert invalid input
            return
        cost.dbu_per_hour = val
        rate_var.set(f"{val:g}")
        cfg_mod.save(cfg)

    rate_entry = ttk.Entry(frm, textvariable=rate_var, width=12, justify="right")
    rate_entry.bind("<FocusOut>", _on_rate)
    rate_entry.bind("<Return>", _on_rate)
    rate_entry.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=2)
    row += 1

    def _saved_label(s: cfg_mod.Stats) -> str:
        return (
            f"DBUs saved: ≈ {s.total_dbus_saved:.2f}  ·  {s.stops_count} stops"
            f"  ·  since {s.since}"
        )

    saved_var = tk.StringVar(value=_saved_label(cfg_mod.load_stats()))

    def _on_reset_stats() -> None:
        saved_var.set(_saved_label(cfg_mod.reset_stats()))

    ttk.Label(frm, textvariable=saved_var, foreground="#555555").grid(
        row=row, column=0, sticky="w", pady=2
    )
    ttk.Button(frm, text="Reset", width=8, command=_on_reset_stats).grid(
        row=row, column=1, sticky="e", padx=(12, 0), pady=2
    )
    row += 1

    cost_tray_var = tk.BooleanVar(value=cost.show_in_tray)

    def _on_cost_tray() -> None:
        cost.show_in_tray = cost_tray_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Show in menu bar", variable=cost_tray_var, command=_on_cost_tray
    ).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1

    idle_alert_var = tk.BooleanVar(value=cost.idle_alert_enabled)

    def _on_idle_alert() -> None:
        cost.idle_alert_enabled = idle_alert_var.get()
        cfg_mod.save(cfg)

    ttk.Checkbutton(
        frm, text="Alert when the cluster is idle but still running",
        variable=idle_alert_var, command=_on_idle_alert,
    ).grid(row=row, column=0, sticky="w")

    idle_presets = (5, 10, 15, 30, 60)
    idle_label_to_min = {human_minutes(p): p for p in idle_presets}
    if cost.idle_alert_minutes not in idle_presets:
        idle_label_to_min[human_minutes(cost.idle_alert_minutes)] = cost.idle_alert_minutes
    idle_min_var = tk.StringVar(value=human_minutes(cost.idle_alert_minutes))
    idle_combo = ttk.Combobox(
        frm, textvariable=idle_min_var, values=list(idle_label_to_min.keys()),
        state="readonly", width=9,
    )

    def _on_idle_min(_e: object = None) -> None:
        m = idle_label_to_min.get(idle_min_var.get())
        if m is not None:
            cost.idle_alert_minutes = m
            cfg_mod.save(cfg)

    idle_combo.bind("<<ComboboxSelected>>", _on_idle_min)
    idle_combo.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=2)
    row += 1

    # ----- action buttons -----
    btns = ttk.Frame(frm)
    btns.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(14, 0))
    btns.columnconfigure(0, weight=1)
    ttk.Button(btns, text="Quit Cursor now", command=_quit_cursor_now).grid(
        row=0, column=0, sticky="w"
    )
    ttk.Button(btns, text="Close", command=root.destroy).grid(row=0, column=1, sticky="e")

    # Center once natural size is known.
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")

    def _refresh() -> None:
        try:
            status_var.set(cfg_mod.read_status())
            saved_var.set(_saved_label(cfg_mod.load_stats()))  # reflect live stats writes
            p = _is_paused()
            if pause_var.get() != p:
                pause_var.set(p)  # .set() does not fire the command callback
            root.after(1000, _refresh)
        except tk.TclError:
            pass  # window destroyed

    _refresh()
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
