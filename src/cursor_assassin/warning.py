"""Always-on-top countdown popup giving the user a chance to cancel the close.

Run as its own process (``--warn``) so Tk owns the main thread; mixing Tk into
the tray process crashes on macOS (see settings.py for the why)."""

from __future__ import annotations

import tkinter as tk


def run_standalone(seconds: int) -> bool:
    """Show the countdown on this process's main thread and block until it ends.
    Returns True if the user cancelled (keep Cursor open), False if it ran out."""
    root = tk.Tk()
    root.title("Cursor Assassin")
    root.attributes("-topmost", True)
    try:
        root.lift()
    except tk.TclError:
        pass

    w, h = 380, 170
    root.geometry(f"{w}x{h}")
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

    remaining = {"s": seconds}
    cancelled = {"v": False}

    tk.Label(
        root,
        text="Closing Cursor due to inactivity",
        font=("Helvetica", 13, "bold"),
    ).pack(pady=(16, 4))

    countdown = tk.Label(root, text="", font=("Helvetica", 28, "bold"), fg="#c0392b")
    countdown.pack()

    tk.Label(
        root,
        text="Press Cancel to keep Cursor open.",
        font=("Helvetica", 10),
    ).pack(pady=(4, 8))

    def _cancel() -> None:
        cancelled["v"] = True
        root.destroy()

    tk.Button(root, text="Cancel", width=12, command=_cancel).pack(pady=4)
    root.protocol("WM_DELETE_WINDOW", _cancel)
    root.bind("<Escape>", lambda _e: _cancel())

    def _tick() -> None:
        s = remaining["s"]
        if s <= 0:
            root.destroy()
            return
        mm, ss = divmod(s, 60)
        countdown.config(text=f"{mm:02d}:{ss:02d}")
        remaining["s"] = s - 1
        root.after(1000, _tick)

    _tick()
    root.mainloop()
    return cancelled["v"]
