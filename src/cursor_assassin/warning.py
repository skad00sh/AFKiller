"""Always-on-top countdown popup giving the user a chance to cancel the close."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Optional


def show_warning(seconds: int) -> bool:
    """Block on a Tk countdown dialog. Returns True if the user cancelled,
    False if the countdown ran out."""
    result: queue.Queue[bool] = queue.Queue(maxsize=1)

    def _run() -> None:
        root = tk.Tk()
        root.title("Cursor Assassin")
        root.attributes("-topmost", True)
        try:
            root.lift()
        except tk.TclError:
            pass

        # Centered, compact window.
        w, h = 380, 170
        root.geometry(f"{w}x{h}")
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")

        remaining = {"s": seconds}
        cancelled = {"v": False}

        header = tk.Label(
            root,
            text="Closing Cursor due to inactivity",
            font=("Helvetica", 13, "bold"),
        )
        header.pack(pady=(16, 4))

        countdown = tk.Label(root, text="", font=("Helvetica", 28, "bold"), fg="#c0392b")
        countdown.pack()

        sub = tk.Label(
            root,
            text="Press Cancel to keep Cursor open.",
            font=("Helvetica", 10),
        )
        sub.pack(pady=(4, 8))

        def _cancel() -> None:
            cancelled["v"] = True
            root.destroy()

        btn = tk.Button(root, text="Cancel", width=12, command=_cancel)
        btn.pack(pady=4)

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
        try:
            root.mainloop()
        finally:
            result.put(cancelled["v"])

    # Tk must run on its own thread when invoked from the watcher thread.
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join()
    try:
        return result.get_nowait()
    except queue.Empty:
        return False
