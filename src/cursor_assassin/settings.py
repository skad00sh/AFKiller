"""Settings window (Tk/ttk), run as its own process.

On macOS a process may own exactly one NSApplication on its main thread, and
both pystray and Tk demand it — so they cannot coexist in one process. The tray
app therefore launches this module in a child process (``--settings``) where Tk
owns the main thread. State is shared via config.toml: changes apply live by
saving to disk, and the tray process reloads on file change.

Identical layout on macOS and Windows; ttk draws each with native styling."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from cursor_assassin import config as cfg_mod
from cursor_assassin import process
from cursor_assassin.config import (
    PAUSE_DURATION_SEC,
    TRIGGER_KEYS,
    TRIGGER_LABELS,
    TRIGGER_PRESETS,
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
    root.title("Cursor Assassin — Settings")
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
            p = _is_paused()
            if pause_var.get() != p:
                pause_var.set(p)  # .set() does not fire the command callback
            root.after(1000, _refresh)
        except tk.TclError:
            pass  # window destroyed

    _refresh()
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
