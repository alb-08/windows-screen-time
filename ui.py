"""
Tray icon + Tk windows for the Game Time Limiter.

run_ui(tracker, config) is called from a background thread by main.py and
takes ownership of that thread for the Tk mainloop. The pystray icon runs
in its own thread (Icon.run_detached). Tray callbacks marshal back to the
Tk thread via root.after_idle so all Tk widget access stays on one thread.
"""
import logging
import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import pystray
from PIL import Image, ImageDraw, ImageFont

from config import save_config

REFRESH_MS = 1000
TRAY_REFRESH_MS = 5000


def _fmt(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _make_icon_image() -> Image.Image:
    """Render a small 64x64 icon: dark blue circle with 'GT' centred."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(28, 76, 138, 255))
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    text = "GT"
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((64 - w) / 2 - bbox[0], (64 - h) / 2 - bbox[1] - 2),
           text, fill=(255, 255, 255, 255), font=font)
    return img


# ---------------------------------------------------------------------------
# Status window
# ---------------------------------------------------------------------------

class StatusWindow:
    def __init__(self, root: tk.Tk, tracker) -> None:
        self.tracker = tracker
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Today")
        self.win.geometry("420x260")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self.rows: dict[str, dict] = {}
        self._build()
        self._refresh()

    def _build(self) -> None:
        outer = ttk.Frame(self.win, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Today's playtime", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Separator(outer).pack(fill="x", pady=(4, 8))

        status = self.tracker.get_status()
        for exe, info in status["games"].items():
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=4)
            name = ttk.Label(row, text=info["display_name"], width=22, anchor="w")
            name.pack(side="left")
            bar = ttk.Progressbar(row, length=180, maximum=info["limit_seconds"])
            bar.pack(side="left", padx=6)
            label = ttk.Label(row, text="", width=18, anchor="w")
            label.pack(side="left")
            self.rows[exe] = {"bar": bar, "label": label}

        self.pool_label = ttk.Label(outer, text="", anchor="w")
        self.pool_label.pack(fill="x", pady=(8, 0))

    def _refresh(self) -> None:
        if not self.win.winfo_exists():
            return
        status = self.tracker.get_status()
        for exe, info in status["games"].items():
            r = self.rows.get(exe)
            if not r:
                continue
            r["bar"]["maximum"] = max(1, info["limit_seconds"])
            r["bar"]["value"] = min(info["played_seconds"], info["limit_seconds"])
            tag = "  [running]" if info["running"] else ""
            r["label"].configure(
                text=f"{_fmt(info['played_seconds'])} / {_fmt(info['limit_seconds'])}{tag}"
            )

        pool = status.get("shared_pool_minutes")
        if pool:
            used = status["shared_pool_used_seconds"]
            self.pool_label.configure(
                text=f"Combined pool: {_fmt(used)} / {_fmt(pool * 60)}"
            )
        else:
            self.pool_label.configure(text="Combined pool: off")
        self.win.after(REFRESH_MS, self._refresh)

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------

class SettingsWindow:
    def __init__(self, root: tk.Tk, tracker, config: dict) -> None:
        self.tracker = tracker
        self.config = config
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Settings")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        self.entries: dict[str, dict[str, tk.Entry]] = {}
        self.shared_pool_var = tk.StringVar()
        self.warning_var = tk.StringVar()
        self.daily_var = tk.StringVar()
        self.weekly_var = tk.StringVar()

        self._build()

    def _build(self) -> None:
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Per-game limits", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        ttk.Label(f, text="Game").grid(row=1, column=0, sticky="w")
        ttk.Label(f, text="Daily limit (min)").grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(f, text="Min match (min)").grid(row=1, column=2, sticky="w", padx=8)

        row = 2
        for exe, gcfg in self.config["games"].items():
            ttk.Label(f, text=gcfg["display_name"]).grid(row=row, column=0, sticky="w", pady=2)
            limit_e = ttk.Entry(f, width=8)
            limit_e.insert(0, str(gcfg["daily_limit_minutes"]))
            limit_e.grid(row=row, column=1, padx=8)
            match_e = ttk.Entry(f, width=8)
            match_e.insert(0, str(gcfg["min_match_minutes"]))
            match_e.grid(row=row, column=2, padx=8)
            self.entries[exe] = {"limit": limit_e, "match": match_e}
            row += 1

        ttk.Separator(f).grid(row=row, column=0, columnspan=4, sticky="ew", pady=8)
        row += 1

        ttk.Label(f, text="Shared pool (min, blank = off)").grid(row=row, column=0, sticky="w")
        self.shared_pool_var.set("" if not self.config.get("shared_pool_minutes")
                                 else str(self.config["shared_pool_minutes"]))
        ttk.Entry(f, width=8, textvariable=self.shared_pool_var).grid(row=row, column=1, padx=8)
        row += 1

        ttk.Label(f, text="Warning minutes").grid(row=row, column=0, sticky="w")
        self.warning_var.set(str(self.config["warning_minutes"]))
        ttk.Entry(f, width=8, textvariable=self.warning_var).grid(row=row, column=1, padx=8)
        row += 1

        ttk.Label(f, text="Daily summary (HH:MM)").grid(row=row, column=0, sticky="w")
        self.daily_var.set(self.config["daily_summary_time"])
        ttk.Entry(f, width=8, textvariable=self.daily_var).grid(row=row, column=1, padx=8)
        row += 1

        ttk.Label(f, text="Weekly summary (HH:MM, Mon)").grid(row=row, column=0, sticky="w")
        self.weekly_var.set(self.config["weekly_summary_time"])
        ttk.Entry(f, width=8, textvariable=self.weekly_var).grid(row=row, column=1, padx=8)
        row += 1

        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=4)

    def _save(self) -> None:
        try:
            for exe, fields in self.entries.items():
                limit = int(fields["limit"].get())
                match = int(fields["match"].get())
                if limit <= 0 or match < 0 or match > limit:
                    raise ValueError(f"Invalid limits for {exe}.")
                self.config["games"][exe]["daily_limit_minutes"] = limit
                self.config["games"][exe]["min_match_minutes"] = match

            warning = int(self.warning_var.get())
            if warning < 0:
                raise ValueError("Warning minutes must be >= 0.")
            self.config["warning_minutes"] = warning

            for var, key in [(self.daily_var, "daily_summary_time"),
                             (self.weekly_var, "weekly_summary_time")]:
                v = var.get().strip()
                hh, mm = v.split(":")
                if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                    raise ValueError(f"Invalid time '{v}'.")
                self.config[key] = v

            sp = self.shared_pool_var.get().strip()
            self.config["shared_pool_minutes"] = int(sp) if sp else None

            save_config(self.config)
            self.tracker.apply_config_update(self.config)
        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.win)
            return

        messagebox.showinfo(
            "Settings saved",
            "Most changes apply immediately. Summary times take effect on the "
            "next launch (restart from the tray menu to apply now).",
            parent=self.win,
        )
        self.win.destroy()


# ---------------------------------------------------------------------------
# Usage editor window
# ---------------------------------------------------------------------------

class UsageEditor:
    def __init__(self, root: tk.Tk, tracker) -> None:
        self.tracker = tracker
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Edit Today's Usage")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        self.entries: dict[str, tk.Entry] = {}
        self._build()

    def _build(self) -> None:
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Override today's usage (minutes)",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Setting a smaller value will re-enable warnings/kills.",
                  foreground="#666").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

        status = self.tracker.get_status()
        row = 2
        for exe, info in status["games"].items():
            ttk.Label(f, text=info["display_name"]).grid(row=row, column=0, sticky="w", pady=2)
            played_min = info["played_seconds"] // 60
            e = ttk.Entry(f, width=8)
            e.insert(0, str(played_min))
            e.grid(row=row, column=1, padx=8)
            ttk.Button(f, text="Reset to 0",
                       command=lambda x=exe: self._set_one(x, 0)).grid(row=row, column=2)
            self.entries[exe] = e
            row += 1

        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Reset all to 0", command=self._reset_all).pack(side="right", padx=4)
        ttk.Button(btns, text="Apply", command=self._apply).pack(side="right", padx=4)

    def _set_one(self, exe: str, minutes: int) -> None:
        self.tracker.set_today_seconds(exe, minutes * 60)
        self.entries[exe].delete(0, tk.END)
        self.entries[exe].insert(0, str(minutes))

    def _reset_all(self) -> None:
        for exe in list(self.entries.keys()):
            self._set_one(exe, 0)

    def _apply(self) -> None:
        try:
            for exe, e in self.entries.items():
                m = int(e.get())
                if m < 0:
                    raise ValueError("Minutes must be >= 0.")
                self.tracker.set_today_seconds(exe, m * 60)
        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.win)
            return
        self.win.destroy()


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def run_ui(tracker, config: dict) -> None:
    root = tk.Tk()
    root.withdraw()  # Never show the root; only Toplevel windows are visible.

    open_windows: dict[str, object] = {}

    def _open(name: str, factory: Callable[[], object]) -> None:
        existing = open_windows.get(name)
        if existing is not None:
            try:
                existing.win.deiconify()
                existing.win.lift()
                existing.win.focus_force()
                return
            except (AttributeError, tk.TclError):
                pass
        win = factory()
        open_windows[name] = win
        # Drop reference when the window is destroyed.
        win.win.bind("<Destroy>", lambda _e, n=name: open_windows.pop(n, None))

    def show_status() -> None:
        _open("status", lambda: StatusWindow(root, tracker))

    def show_settings() -> None:
        _open("settings", lambda: SettingsWindow(root, tracker, config))

    def show_usage_editor() -> None:
        _open("usage", lambda: UsageEditor(root, tracker))

    def open_log() -> None:
        log_path = config.get("log_file", "game_limiter.log")
        try:
            os.startfile(log_path)  # type: ignore[attr-defined]
        except OSError as exc:
            logging.warning("Could not open log file: %s", exc)

    def restart_app() -> None:
        logging.info("Restart requested from tray menu.")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def quit_app() -> None:
        logging.info("Quit requested from tray menu.")
        try:
            icon.stop()
        except Exception:
            pass
        try:
            root.destroy()
        except tk.TclError:
            pass
        os._exit(0)

    # ---- Tray icon ----
    icon_image = _make_icon_image()
    menu = pystray.Menu(
        pystray.MenuItem("Show today's usage", lambda: root.after(0, show_status), default=True),
        pystray.MenuItem("Edit today's usage…", lambda: root.after(0, show_usage_editor)),
        pystray.MenuItem("Settings…", lambda: root.after(0, show_settings)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open log", lambda: root.after(0, open_log)),
        pystray.MenuItem("Restart", lambda: root.after(0, restart_app)),
        pystray.MenuItem("Quit", lambda: root.after(0, quit_app)),
    )
    icon = pystray.Icon("GameTimeLimiter", icon_image, "Game Time Limiter", menu)
    icon.run_detached()

    # ---- Tooltip refresher ----
    def refresh_tooltip() -> None:
        try:
            status = tracker.get_status()
            parts = []
            for info in status["games"].values():
                parts.append(f"{info['display_name']}: {_fmt(info['remaining_seconds'])} left")
            if status.get("shared_pool_minutes"):
                pool_left = status["shared_pool_minutes"] * 60 - status["shared_pool_used_seconds"]
                parts.append(f"Pool: {_fmt(max(0, pool_left))} left")
            icon.title = "Game Time Limiter\n" + "\n".join(parts)
        except Exception:
            logging.exception("Tray tooltip refresh failed.")
        root.after(TRAY_REFRESH_MS, refresh_tooltip)

    root.after(500, refresh_tooltip)
    root.mainloop()
