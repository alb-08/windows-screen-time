"""
Tray icon + Tk windows for the Game Time Limiter.

run_ui(tracker, config) is called from a background thread by main.py and
takes ownership of that thread for the Tk mainloop. The pystray icon runs
in its own thread (Icon.run_detached). Tray callbacks marshal back to the
Tk thread via root.after_idle so all Tk widget access stays on one thread.
"""
import logging
import os
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

import pystray
from PIL import Image, ImageDraw, ImageFont

from config import save_config
from passcode import hash_passcode, passcode_is_set, verify_passcode

REFRESH_MS = 1000
TRAY_REFRESH_MS = 5000


def _fmt(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


# ---------------------------------------------------------------------------
# Passcode gating
# ---------------------------------------------------------------------------

def _prompt_passcode(parent, config: dict, reason: str) -> bool:
    """Ask for the passcode; return True if it matches (or none is set)."""
    if not passcode_is_set(config):
        return True
    pc = simpledialog.askstring(
        "Passcode required",
        f"Enter passcode to {reason}:",
        show="*",
        parent=parent,
    )
    if pc is None:
        return False
    if verify_passcode(pc, config.get("passcode_salt"), config.get("passcode_hash")):
        return True
    messagebox.showerror("Wrong passcode", "Passcode does not match.", parent=parent)
    return False


def _set_passcode_dialog(parent, config: dict) -> None:
    """Set or change the passcode. Asks for old passcode if one is already set."""
    if passcode_is_set(config):
        if not _prompt_passcode(parent, config, "change the passcode"):
            return
    new_pc = simpledialog.askstring(
        "New passcode", "Enter a new passcode (blank to remove):",
        show="*", parent=parent,
    )
    if new_pc is None:
        return
    if not new_pc:
        config.pop("passcode_salt", None)
        config.pop("passcode_hash", None)
        save_config(config)
        messagebox.showinfo("Passcode removed", "Passcode protection is now off.", parent=parent)
        return
    confirm = simpledialog.askstring(
        "Confirm passcode", "Re-enter the new passcode:",
        show="*", parent=parent,
    )
    if confirm is None:
        return
    if confirm != new_pc:
        messagebox.showerror("Mismatch", "Passcodes did not match. Not changed.", parent=parent)
        return
    salt, h = hash_passcode(new_pc)
    config["passcode_salt"] = salt
    config["passcode_hash"] = h
    save_config(config)
    messagebox.showinfo("Passcode set", "Passcode protection is on.", parent=parent)


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
        self.grace_var = tk.StringVar()
        self.daily_var = tk.StringVar()
        self.weekly_var = tk.StringVar()
        self.firewall_var = tk.BooleanVar()

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

        ttk.Label(f, text="Grace after limit (min)").grid(row=row, column=0, sticky="w")
        self.grace_var.set(str(self.config.get("grace_minutes", 0)))
        ttk.Entry(f, width=8, textvariable=self.grace_var).grid(row=row, column=1, padx=8)
        ttk.Label(f, text="lets the current match finish",
                  foreground="#666").grid(row=row, column=2, columnspan=2, sticky="w", padx=8)
        row += 1

        self.firewall_var.set(bool(self.config.get("firewall_block_at_warning", True)))
        ttk.Checkbutton(
            f, text="Block game's internet during warning window (admin only)",
            variable=self.firewall_var,
        ).grid(row=row, column=0, columnspan=4, sticky="w", pady=(2, 4))
        row += 1

        ttk.Label(f, text="Daily summary (HH:MM)").grid(row=row, column=0, sticky="w")
        self.daily_var.set(self.config["daily_summary_time"])
        ttk.Entry(f, width=8, textvariable=self.daily_var).grid(row=row, column=1, padx=8)
        row += 1

        ttk.Label(f, text="Weekly summary (HH:MM, Mon)").grid(row=row, column=0, sticky="w")
        self.weekly_var.set(self.config["weekly_summary_time"])
        ttk.Entry(f, width=8, textvariable=self.weekly_var).grid(row=row, column=1, padx=8)
        row += 1

        ttk.Separator(f).grid(row=row, column=0, columnspan=4, sticky="ew", pady=8)
        row += 1
        passcode_state = "set" if passcode_is_set(self.config) else "not set"
        ttk.Label(f, text=f"Passcode: {passcode_state}").grid(row=row, column=0, sticky="w")
        ttk.Button(
            f, text="Set / change…",
            command=lambda: _set_passcode_dialog(self.win, self.config),
        ).grid(row=row, column=1, padx=8, sticky="w")
        row += 1

        btns = ttk.Frame(f)
        btns.grid(row=row, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=4)

    def _save(self) -> None:
        try:
            new_games: dict[str, dict] = {}
            loosening = False

            for exe, fields in self.entries.items():
                limit = int(fields["limit"].get())
                match = int(fields["match"].get())
                if limit <= 0 or match < 0 or match > limit:
                    raise ValueError(f"Invalid limits for {exe}.")
                old_limit = self.config["games"][exe]["daily_limit_minutes"]
                old_match = self.config["games"][exe]["min_match_minutes"]
                if limit > old_limit or match < old_match:
                    loosening = True
                new_games[exe] = dict(self.config["games"][exe])
                new_games[exe]["daily_limit_minutes"] = limit
                new_games[exe]["min_match_minutes"] = match

            warning = int(self.warning_var.get())
            if warning < 0:
                raise ValueError("Warning minutes must be >= 0.")
            if warning < self.config["warning_minutes"]:
                loosening = True

            grace = int(self.grace_var.get())
            if grace < 0:
                raise ValueError("Grace minutes must be >= 0.")
            if grace > self.config.get("grace_minutes", 0):
                loosening = True

            for var, key in [(self.daily_var, "daily_summary_time"),
                             (self.weekly_var, "weekly_summary_time")]:
                v = var.get().strip()
                hh, mm = v.split(":")
                if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                    raise ValueError(f"Invalid time '{v}'.")

            sp_raw = self.shared_pool_var.get().strip()
            new_pool = int(sp_raw) if sp_raw else None
            old_pool = self.config.get("shared_pool_minutes")
            if (old_pool is not None and new_pool is None) or \
               (old_pool is not None and new_pool is not None and new_pool > old_pool):
                loosening = True

            new_firewall = bool(self.firewall_var.get())
            if self.config.get("firewall_block_at_warning", True) and not new_firewall:
                loosening = True

        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.win)
            return

        if loosening and not _prompt_passcode(self.win, self.config,
                                              "save these settings (loosens limits)"):
            return

        # All clear - apply.
        self.config["games"] = new_games
        self.config["warning_minutes"] = warning
        self.config["grace_minutes"] = grace
        self.config["daily_summary_time"] = self.daily_var.get().strip()
        self.config["weekly_summary_time"] = self.weekly_var.get().strip()
        self.config["shared_pool_minutes"] = new_pool
        self.config["firewall_block_at_warning"] = new_firewall

        save_config(self.config)
        self.tracker.apply_config_update(self.config)

        messagebox.showinfo(
            "Settings saved",
            "Most changes apply immediately. Summary times take effect on the "
            "next launch (use Restart from the tray menu).",
            parent=self.win,
        )
        self.win.destroy()


# ---------------------------------------------------------------------------
# Usage editor window
# ---------------------------------------------------------------------------

class UsageEditor:
    def __init__(self, root: tk.Tk, tracker, config: dict) -> None:
        self.tracker = tracker
        self.config = config
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Edit Today's Usage")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        self.entries: dict[str, tk.Entry] = {}
        # Snapshot current usage for "loosening" comparison
        self._original_seconds: dict[str, int] = {
            exe: info["played_seconds"]
            for exe, info in self.tracker.get_status()["games"].items()
        }
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
        new_seconds = minutes * 60
        if new_seconds < self._original_seconds.get(exe, 0):
            if not _prompt_passcode(self.win, self.config,
                                    f"reduce {exe} usage (gives back time)"):
                return
        self.tracker.set_today_seconds(exe, new_seconds)
        self._original_seconds[exe] = new_seconds
        self.entries[exe].delete(0, tk.END)
        self.entries[exe].insert(0, str(minutes))

    def _reset_all(self) -> None:
        # Single passcode prompt for the whole batch.
        if any(0 < self._original_seconds.get(exe, 0) for exe in self.entries):
            if not _prompt_passcode(self.win, self.config,
                                    "reset usage (gives back time)"):
                return
        for exe in list(self.entries.keys()):
            self.tracker.set_today_seconds(exe, 0)
            self._original_seconds[exe] = 0
            self.entries[exe].delete(0, tk.END)
            self.entries[exe].insert(0, "0")

    def _apply(self) -> None:
        try:
            updates: list[tuple[str, int]] = []
            loosening = False
            for exe, e in self.entries.items():
                m = int(e.get())
                if m < 0:
                    raise ValueError("Minutes must be >= 0.")
                new_seconds = m * 60
                if new_seconds < self._original_seconds.get(exe, 0):
                    loosening = True
                updates.append((exe, new_seconds))
        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.win)
            return
        if loosening and not _prompt_passcode(
            self.win, self.config, "reduce today's usage (gives back time)"
        ):
            return
        for exe, secs in updates:
            self.tracker.set_today_seconds(exe, secs)
            self._original_seconds[exe] = secs
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
        _open("usage", lambda: UsageEditor(root, tracker, config))

    def open_log() -> None:
        log_path = config.get("log_file", "game_limiter.log")
        try:
            os.startfile(log_path)  # type: ignore[attr-defined]
        except OSError as exc:
            logging.warning("Could not open log file: %s", exc)

    def restart_app() -> None:
        if not _prompt_passcode(root, config, "restart Game Time Limiter"):
            return
        logging.info("Restart requested from tray menu.")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def quit_app() -> None:
        if not _prompt_passcode(root, config, "quit Game Time Limiter"):
            return
        logging.info("Quit requested from tray menu.")
        try:
            tracker.shutdown_unblock_all()
        except Exception:
            logging.exception("Unblock-all on shutdown failed.")
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
