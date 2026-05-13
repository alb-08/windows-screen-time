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

import psutil
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
# Process listing (for "Add from running")
# ---------------------------------------------------------------------------

def _list_running_processes() -> list[dict]:
    """Return [{name, exe, pid}] for running processes, deduped by name."""
    seen: dict[str, dict] = {}
    for proc in psutil.process_iter(["name", "exe", "pid"]):
        try:
            info = proc.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        name = info.get("name") or ""
        if not name or name.lower() in seen:
            continue
        seen[name.lower()] = {
            "name": name,
            "exe": info.get("exe") or "",
            "pid": info.get("pid"),
        }
    return sorted(seen.values(), key=lambda p: p["name"].lower())


# ---------------------------------------------------------------------------
# Status window
# ---------------------------------------------------------------------------

class StatusWindow:
    def __init__(self, root: tk.Tk, tracker) -> None:
        self.tracker = tracker
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Today")
        self.win.geometry("440x300")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        self._mode: str | None = None  # "pool" or "per_app"; rebuilt on change
        self._build_shell()
        self._refresh()

    def _build_shell(self) -> None:
        outer = ttk.Frame(self.win, padding=12)
        outer.pack(fill="both", expand=True)
        self.title_label = ttk.Label(outer, text="Today's playtime",
                                     font=("Segoe UI", 11, "bold"))
        self.title_label.pack(anchor="w")
        ttk.Separator(outer).pack(fill="x", pady=(4, 8))
        self.body = ttk.Frame(outer)
        self.body.pack(fill="both", expand=True)
        self.subnote = ttk.Label(outer, text="", anchor="w", foreground="#666")
        self.subnote.pack(fill="x", pady=(8, 0))

    def _rebuild_for_mode(self, mode: str) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self._mode = mode
        if mode == "pool":
            self.pool_bar = ttk.Progressbar(self.body, length=320, maximum=1)
            self.pool_bar.pack(fill="x", pady=4)
            self.pool_label = ttk.Label(self.body, text="", anchor="w")
            self.pool_label.pack(fill="x", pady=(2, 6))
            self.per_app_label = ttk.Label(self.body, text="", anchor="w",
                                           foreground="#666", justify="left")
            self.per_app_label.pack(fill="x", pady=(4, 0))
        else:
            self.rows: dict[str, dict] = {}

    def _ensure_row(self, exe: str, display_name: str) -> None:
        if exe in self.rows:
            return
        row = ttk.Frame(self.body)
        row.pack(fill="x", pady=4)
        name = ttk.Label(row, text=display_name, width=22, anchor="w")
        name.pack(side="left")
        bar = ttk.Progressbar(row, length=180, maximum=1)
        bar.pack(side="left", padx=6)
        label = ttk.Label(row, text="", width=18, anchor="w")
        label.pack(side="left")
        self.rows[exe] = {"row": row, "name": name, "bar": bar, "label": label}

    def _refresh(self) -> None:
        if not self.win.winfo_exists():
            return
        status = self.tracker.get_status()
        pool = status.get("shared_pool_minutes")
        mode = "pool" if pool else "per_app"

        if mode != self._mode:
            self._rebuild_for_mode(mode)

        if mode == "pool":
            pool_s = pool * 60
            used = status["shared_pool_used_seconds"]
            self.pool_bar["maximum"] = max(1, pool_s)
            self.pool_bar["value"] = min(used, pool_s)
            self.pool_label.configure(
                text=f"Combined pool: {_fmt(used)} / {_fmt(pool_s)}"
            )
            running_lines = []
            for info in status["applications"].values():
                tag = " [running]" if info["running"] else ""
                running_lines.append(
                    f"  • {info['display_name']}: {_fmt(info['played_seconds'])}{tag}"
                )
            self.per_app_label.configure(text="\n".join(running_lines))
            self.subnote.configure(text="Per-app caps are ignored while combined pool is on.")
        else:
            live_exes = set(status["applications"].keys())
            for exe in list(self.rows.keys()):
                if exe not in live_exes:
                    self.rows[exe]["row"].destroy()
                    self.rows.pop(exe, None)
            for exe, info in status["applications"].items():
                self._ensure_row(exe, info["display_name"])
                r = self.rows[exe]
                r["name"].configure(text=info["display_name"])
                r["bar"]["maximum"] = max(1, info["limit_seconds"])
                r["bar"]["value"] = min(info["played_seconds"], info["limit_seconds"])
                tag = "  [running]" if info["running"] else ""
                r["label"].configure(
                    text=f"{_fmt(info['played_seconds'])} / {_fmt(info['limit_seconds'])}{tag}"
                )
            self.subnote.configure(text="Combined pool: off")

        self.win.after(REFRESH_MS, self._refresh)

    def close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Add / Edit application form
# ---------------------------------------------------------------------------

class AppFormDialog:
    """Modal dialog used by Manage Applications: add (manual or from running)
    and edit. Calls on_ok({display_name, exe, daily_limit_minutes,
    min_match_minutes}) when the user confirms."""

    def __init__(self, parent, title: str, *,
                 from_running: bool = False,
                 initial: dict | None = None,
                 on_ok: Callable[[dict], None] | None = None) -> None:
        self.on_ok = on_ok
        self.from_running = from_running
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.attributes("-topmost", True)
        self.win.geometry("560x" + ("460" if from_running else "260"))

        outer = ttk.Frame(self.win, padding=12)
        outer.pack(fill="both", expand=True)

        if from_running:
            ttk.Label(outer, text="Pick a running process:").pack(anchor="w")
            top = ttk.Frame(outer)
            top.pack(fill="x", pady=(2, 6))
            ttk.Label(top, text="Filter:").pack(side="left")
            self.filter_var = tk.StringVar()
            self.filter_var.trace_add("write", lambda *_: self._refilter())
            ttk.Entry(top, textvariable=self.filter_var).pack(
                side="left", fill="x", expand=True, padx=6)
            ttk.Button(top, text="Refresh", command=self._reload_processes).pack(side="left")

            list_frame = ttk.Frame(outer)
            list_frame.pack(fill="both", expand=True)
            self.proc_tree = ttk.Treeview(
                list_frame, columns=("name", "path"), show="headings", height=8
            )
            self.proc_tree.heading("name", text="Process")
            self.proc_tree.heading("path", text="Path")
            self.proc_tree.column("name", width=160, anchor="w")
            self.proc_tree.column("path", width=340, anchor="w")
            sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.proc_tree.yview)
            self.proc_tree.configure(yscrollcommand=sb.set)
            self.proc_tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self.proc_tree.bind("<<TreeviewSelect>>", self._on_proc_select)
            self._all_procs: list[dict] = []
            self._reload_processes()
            ttk.Separator(outer).pack(fill="x", pady=8)

        form = ttk.Frame(outer)
        form.pack(fill="x")
        initial = initial or {}

        ttk.Label(form, text="Display name:").grid(row=0, column=0, sticky="w", pady=2)
        self.display_var = tk.StringVar(value=initial.get("display_name", ""))
        ttk.Entry(form, textvariable=self.display_var, width=32).grid(
            row=0, column=1, padx=6, pady=2, sticky="w")

        ttk.Label(form, text="Exe name:").grid(row=1, column=0, sticky="w", pady=2)
        self.exe_var = tk.StringVar(value=initial.get("exe", ""))
        self.exe_entry = ttk.Entry(form, textvariable=self.exe_var, width=32)
        self.exe_entry.grid(row=1, column=1, padx=6, pady=2, sticky="w")
        if initial:
            self.exe_entry.configure(state="readonly")

        ttk.Label(form, text="Daily limit (min):").grid(row=2, column=0, sticky="w", pady=2)
        self.limit_var = tk.StringVar(value=str(initial.get("daily_limit_minutes", 60)))
        ttk.Entry(form, textvariable=self.limit_var, width=10).grid(
            row=2, column=1, padx=6, pady=2, sticky="w")

        ttk.Label(form, text="Min match (min):").grid(row=3, column=0, sticky="w", pady=2)
        self.match_var = tk.StringVar(value=str(initial.get("min_match_minutes", 0)))
        ttk.Entry(form, textvariable=self.match_var, width=10).grid(
            row=3, column=1, padx=6, pady=2, sticky="w")

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="OK", command=self._submit).pack(side="right", padx=4)

    def _reload_processes(self) -> None:
        try:
            self._all_procs = _list_running_processes()
        except Exception:
            logging.exception("Failed to list running processes.")
            self._all_procs = []
        self._refilter()

    def _refilter(self) -> None:
        flt = self.filter_var.get().lower() if hasattr(self, "filter_var") else ""
        self.proc_tree.delete(*self.proc_tree.get_children())
        for p in self._all_procs:
            if flt and flt not in p["name"].lower():
                continue
            self.proc_tree.insert("", "end", iid=p["name"], values=(p["name"], p["exe"]))

    def _on_proc_select(self, _evt) -> None:
        sel = self.proc_tree.selection()
        if not sel:
            return
        name = sel[0]
        proc = next((p for p in self._all_procs if p["name"] == name), None)
        if not proc:
            return
        # Default display name: strip .exe and title-case.
        suggested = proc["name"].rsplit(".", 1)[0]
        if not self.display_var.get().strip():
            self.display_var.set(suggested)
        self.exe_var.set(proc["name"])

    def _submit(self) -> None:
        try:
            limit = int(self.limit_var.get())
            match = int(self.match_var.get())
            if limit <= 0:
                raise ValueError("Daily limit must be > 0.")
            if match < 0:
                raise ValueError("Min match must be >= 0.")
            if match > limit:
                raise ValueError("Min match cannot exceed daily limit.")
            display = self.display_var.get().strip()
            exe = self.exe_var.get().strip()
            if not display:
                raise ValueError("Display name is required.")
            if not exe:
                raise ValueError("Exe name is required.")
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self.win)
            return
        if self.on_ok:
            self.on_ok({
                "display_name": display,
                "exe": exe,
                "daily_limit_minutes": limit,
                "min_match_minutes": match,
            })
        self.win.destroy()


# ---------------------------------------------------------------------------
# Manage Applications window
# ---------------------------------------------------------------------------

class ManageApplicationsWindow:
    def __init__(self, root: tk.Tk, tracker, config: dict) -> None:
        self.tracker = tracker
        self.config = config
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Manage Applications")
        self.win.geometry("720x460")
        self.win.minsize(580, 320)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)
        self._build()
        self._refresh_tree()
        self._tick()

    def _build(self) -> None:
        outer = ttk.Frame(self.win, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Tracked applications",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Separator(outer).pack(fill="x", pady=(4, 8))

        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)

        cols = ("display_name", "exe", "limit", "match", "today")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=10)
        self.tree.heading("display_name", text="Display name")
        self.tree.heading("exe", text="Exe")
        self.tree.heading("limit", text="Limit (min)")
        self.tree.heading("match", text="Min match (min)")
        self.tree.heading("today", text="Today")
        self.tree.column("display_name", width=200, anchor="w")
        self.tree.column("exe", width=200, anchor="w")
        self.tree.column("limit", width=90, anchor="center")
        self.tree.column("match", width=120, anchor="center")
        self.tree.column("today", width=80, anchor="center")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._edit())

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Add from running…",
                   command=self._add_running).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Add manually…",
                   command=self._add_manual).pack(side="left", padx=4)
        self.edit_btn = ttk.Button(btns, text="Edit…", command=self._edit, state="disabled")
        self.remove_btn = ttk.Button(btns, text="Remove…", command=self._remove, state="disabled")
        self.edit_btn.pack(side="left", padx=4)
        self.remove_btn.pack(side="left", padx=4)

        ttk.Label(outer, text="* = currently running",
                  foreground="#666").pack(anchor="w", pady=(8, 0))

        close_row = ttk.Frame(outer)
        close_row.pack(fill="x", pady=(8, 0))
        ttk.Button(close_row, text="Close", command=self.win.destroy).pack(side="right")

    def _refresh_tree(self) -> None:
        if not self.win.winfo_exists():
            return
        # Preserve selection across rebuilds.
        prev_sel = self.tree.selection()[0] if self.tree.selection() else None

        self.tree.delete(*self.tree.get_children())
        status = self.tracker.get_status()
        apps = status["applications"]
        if not apps:
            self.tree.insert("", "end", values=("(no applications tracked yet)", "", "", "", ""))
            self.edit_btn.configure(state="disabled")
            self.remove_btn.configure(state="disabled")
            return
        for exe, info in apps.items():
            cfg = self.config["applications"][exe]
            display = info["display_name"] + (" *" if info["running"] else "")
            today_min = info["played_seconds"] // 60
            self.tree.insert("", "end", iid=exe, values=(
                display, exe,
                f"{cfg['daily_limit_minutes']} m",
                f"{cfg['min_match_minutes']} m",
                f"{today_min} m",
            ))
        if prev_sel and prev_sel in self.tree.get_children():
            self.tree.selection_set(prev_sel)
        self._on_select()

    def _tick(self) -> None:
        self._refresh_tree()
        if self.win.winfo_exists():
            self.win.after(1000, self._tick)

    def _on_select(self, _evt=None) -> None:
        sel = self.tree.selection()
        # Don't enable buttons for the placeholder row (which has no iid match).
        valid = bool(sel) and sel[0] in self.config["applications"]
        st = "normal" if valid else "disabled"
        self.edit_btn.configure(state=st)
        self.remove_btn.configure(state=st)

    def _add_running(self) -> None:
        AppFormDialog(self.win, "Add from running processes",
                      from_running=True, on_ok=self._add_app)

    def _add_manual(self) -> None:
        AppFormDialog(self.win, "Add application manually", on_ok=self._add_app)

    def _edit(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        exe = sel[0]
        cfg = self.config["applications"].get(exe)
        if not cfg:
            return
        initial = {
            "display_name": cfg["display_name"],
            "exe": exe,
            "daily_limit_minutes": cfg["daily_limit_minutes"],
            "min_match_minutes": cfg["min_match_minutes"],
        }
        AppFormDialog(self.win, "Edit application", initial=initial,
                      on_ok=lambda upd: self._update_app(exe, upd))

    def _add_app(self, app: dict) -> None:
        exe = app["exe"]
        if any(e.lower() == exe.lower() for e in self.config["applications"]):
            messagebox.showerror(
                "Already tracked",
                f"{exe} is already in the list. Use Edit instead.",
                parent=self.win,
            )
            return
        self.config["applications"][exe] = {
            "display_name": app["display_name"],
            "daily_limit_minutes": app["daily_limit_minutes"],
            "min_match_minutes": app["min_match_minutes"],
        }
        save_config(self.config)
        self.tracker.apply_config_update(self.config)
        self._refresh_tree()

    def _update_app(self, exe: str, upd: dict) -> None:
        cfg = self.config["applications"].get(exe)
        if not cfg:
            return
        old_limit = cfg["daily_limit_minutes"]
        old_match = cfg["min_match_minutes"]
        loosening = (upd["daily_limit_minutes"] > old_limit
                     or upd["min_match_minutes"] < old_match)
        if loosening and not _prompt_passcode(self.win, self.config,
                                              f"loosen limits for {upd['display_name']}"):
            return
        # Display-name-only edits don't need a passcode.
        cfg["display_name"] = upd["display_name"]
        cfg["daily_limit_minutes"] = upd["daily_limit_minutes"]
        cfg["min_match_minutes"] = upd["min_match_minutes"]
        save_config(self.config)
        self.tracker.apply_config_update(self.config)
        self._refresh_tree()

    def _remove(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        exe = sel[0]
        cfg = self.config["applications"].get(exe)
        if not cfg:
            return
        if not _prompt_passcode(self.win, self.config,
                                f"stop tracking {cfg['display_name']}"):
            return
        if not messagebox.askyesno(
            "Confirm",
            f"Stop tracking {cfg['display_name']} ({exe})?\n"
            f"Today's recorded usage will be discarded.",
            parent=self.win,
        ):
            return
        del self.config["applications"][exe]
        save_config(self.config)
        self.tracker.apply_config_update(self.config)
        self._refresh_tree()


# ---------------------------------------------------------------------------
# Settings window (with pool-mode radio buttons)
# ---------------------------------------------------------------------------

class SettingsWindow:
    def __init__(self, root: tk.Tk, tracker, config: dict) -> None:
        self.tracker = tracker
        self.config = config
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Settings")
        self.win.geometry("560x600")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self.win.destroy)

        self.entries: dict[str, dict[str, tk.Entry]] = {}
        self.mode_var = tk.StringVar(
            value="pool" if config.get("shared_pool_minutes") else "per_app"
        )
        self.pool_var = tk.StringVar(
            value=str(config.get("shared_pool_minutes") or 180)
        )
        self.warning_var = tk.StringVar()
        self.grace_var = tk.StringVar()
        self.daily_var = tk.StringVar()
        self.weekly_var = tk.StringVar()
        self.firewall_var = tk.BooleanVar()

        self._build()
        self._on_mode_change()

    def _build(self) -> None:
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill="both", expand=True)

        # Mode picker
        mode_box = ttk.LabelFrame(f, text="Limit mode", padding=8)
        mode_box.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(mode_box, text="Per-application limits",
                        variable=self.mode_var, value="per_app",
                        command=self._on_mode_change).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(mode_box, text="Combined pool",
                        variable=self.mode_var, value="pool",
                        command=self._on_mode_change).pack(side="left", padx=(0, 12))
        ttk.Button(mode_box, text="?", width=3, command=self._show_help).pack(side="right")

        # Pool field (visible only in pool mode)
        self.pool_frame = ttk.LabelFrame(
            f, text="Total daily minutes (shared by all apps)", padding=8
        )
        ttk.Entry(self.pool_frame, textvariable=self.pool_var, width=10).pack(side="left")
        ttk.Label(self.pool_frame, text="min").pack(side="left", padx=(4, 0))

        # Per-app grid
        self.app_frame = ttk.LabelFrame(f, text="Per-application limits", padding=8)
        ttk.Label(self.app_frame, text="Application").grid(row=0, column=0, sticky="w")
        self.limit_header = ttk.Label(self.app_frame, text="Daily limit (min)")
        self.limit_header.grid(row=0, column=1, padx=8)
        ttk.Label(self.app_frame, text="Min match (min)").grid(row=0, column=2, padx=8)

        row = 1
        for exe, gcfg in self.config["applications"].items():
            ttk.Label(self.app_frame, text=gcfg["display_name"]).grid(
                row=row, column=0, sticky="w", pady=2)
            limit_e = ttk.Entry(self.app_frame, width=8)
            limit_e.insert(0, str(gcfg["daily_limit_minutes"]))
            limit_e.grid(row=row, column=1, padx=8)
            match_e = ttk.Entry(self.app_frame, width=8)
            match_e.insert(0, str(gcfg["min_match_minutes"]))
            match_e.grid(row=row, column=2, padx=8)
            self.entries[exe] = {"limit": limit_e, "match": match_e}
            row += 1
        if not self.config["applications"]:
            ttk.Label(
                self.app_frame,
                text="(no applications yet — add some from Manage Applications…)",
                foreground="#666",
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
        self.app_frame.pack(fill="x")

        ttk.Separator(f).pack(fill="x", pady=8)

        misc = ttk.Frame(f)
        misc.pack(fill="x")

        ttk.Label(misc, text="Warning minutes:").grid(row=0, column=0, sticky="w", pady=2)
        self.warning_var.set(str(self.config["warning_minutes"]))
        ttk.Entry(misc, textvariable=self.warning_var, width=8).grid(
            row=0, column=1, padx=8, sticky="w")

        ttk.Label(misc, text="Grace after limit (min):").grid(row=1, column=0, sticky="w", pady=2)
        self.grace_var.set(str(self.config.get("grace_minutes", 0)))
        ttk.Entry(misc, textvariable=self.grace_var, width=8).grid(
            row=1, column=1, padx=8, sticky="w")
        ttk.Label(misc, text="(lets the current match finish)",
                  foreground="#666").grid(row=1, column=2, sticky="w")

        self.firewall_var.set(bool(self.config.get("firewall_block_at_warning", True)))
        ttk.Checkbutton(
            misc, text="Block app's internet during warning window (admin only)",
            variable=self.firewall_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Label(misc, text="Daily summary (HH:MM):").grid(row=3, column=0, sticky="w", pady=2)
        self.daily_var.set(self.config["daily_summary_time"])
        ttk.Entry(misc, textvariable=self.daily_var, width=8).grid(
            row=3, column=1, padx=8, sticky="w")

        ttk.Label(misc, text="Weekly summary (Mon HH:MM):").grid(row=4, column=0, sticky="w", pady=2)
        self.weekly_var.set(self.config["weekly_summary_time"])
        ttk.Entry(misc, textvariable=self.weekly_var, width=8).grid(
            row=4, column=1, padx=8, sticky="w")

        ttk.Separator(f).pack(fill="x", pady=8)
        pc = ttk.Frame(f)
        pc.pack(fill="x")
        passcode_state = "set" if passcode_is_set(self.config) else "not set"
        ttk.Label(pc, text=f"Passcode: {passcode_state}").pack(side="left")
        ttk.Button(
            pc, text="Set / change…",
            command=lambda: _set_passcode_dialog(self.win, self.config),
        ).pack(side="left", padx=8)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=4)

    def _on_mode_change(self) -> None:
        if self.mode_var.get() == "pool":
            self.pool_frame.pack(fill="x", pady=(0, 8), before=self.app_frame)
            self.limit_header.configure(text="Per-app cap (ignored in pool mode)")
            for fields in self.entries.values():
                fields["limit"].configure(state="disabled")
        else:
            self.pool_frame.pack_forget()
            self.limit_header.configure(text="Daily limit (min)")
            for fields in self.entries.values():
                fields["limit"].configure(state="normal")

    def _show_help(self) -> None:
        messagebox.showinfo(
            "Limit modes",
            "Per-application limits:\n"
            "    Each app has its own daily allowance.\n\n"
            "Combined pool:\n"
            "    All apps share one daily allowance. Per-app limits\n"
            "    are ignored while pool mode is on; switching back\n"
            "    restores them.",
            parent=self.win,
        )

    def _save(self) -> None:
        try:
            new_apps: dict[str, dict] = {}
            loosening = False
            mode = self.mode_var.get()

            for exe, fields in self.entries.items():
                old_cfg = self.config["applications"][exe]
                # In pool mode, the limit field is disabled - keep the old value.
                if mode == "pool":
                    limit = old_cfg["daily_limit_minutes"]
                else:
                    limit = int(fields["limit"].get())
                match = int(fields["match"].get())
                if limit <= 0 or match < 0 or match > limit:
                    raise ValueError(f"Invalid limits for {exe}.")
                if limit > old_cfg["daily_limit_minutes"] or match < old_cfg["min_match_minutes"]:
                    loosening = True
                new_apps[exe] = dict(old_cfg)
                new_apps[exe]["daily_limit_minutes"] = limit
                new_apps[exe]["min_match_minutes"] = match

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

            old_pool = self.config.get("shared_pool_minutes")
            if mode == "pool":
                new_pool = int(self.pool_var.get())
                if new_pool <= 0:
                    raise ValueError("Combined pool minutes must be > 0.")
                if old_pool is not None and new_pool > old_pool:
                    loosening = True
            else:
                new_pool = None
                if old_pool is not None:
                    # Switching pool off is loosening (per-app caps may now be larger).
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

        self.config["applications"] = new_apps
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
        self._original_seconds: dict[str, int] = {
            exe: info["played_seconds"]
            for exe, info in self.tracker.get_status()["applications"].items()
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
        for exe, info in status["applications"].items():
            ttk.Label(f, text=info["display_name"]).grid(row=row, column=0, sticky="w", pady=2)
            played_min = info["played_seconds"] // 60
            e = ttk.Entry(f, width=8)
            e.insert(0, str(played_min))
            e.grid(row=row, column=1, padx=8)
            ttk.Button(f, text="Reset to 0",
                       command=lambda x=exe: self._set_one(x, 0)).grid(row=row, column=2)
            self.entries[exe] = e
            row += 1
        if not status["applications"]:
            ttk.Label(f, text="(no applications tracked)",
                      foreground="#666").grid(row=row, column=0, columnspan=3, sticky="w")
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
    root.withdraw()

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
        win.win.bind("<Destroy>", lambda _e, n=name: open_windows.pop(n, None))

    def show_status() -> None:
        _open("status", lambda: StatusWindow(root, tracker))

    def show_settings() -> None:
        _open("settings", lambda: SettingsWindow(root, tracker, config))

    def show_manage_apps() -> None:
        _open("manage", lambda: ManageApplicationsWindow(root, tracker, config))

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

    icon_image = _make_icon_image()
    menu = pystray.Menu(
        pystray.MenuItem("Show today's usage",
                         lambda: root.after(0, show_status), default=True),
        pystray.MenuItem("Edit today's usage…",
                         lambda: root.after(0, show_usage_editor)),
        pystray.MenuItem("Manage applications…",
                         lambda: root.after(0, show_manage_apps)),
        pystray.MenuItem("Settings…", lambda: root.after(0, show_settings)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open log", lambda: root.after(0, open_log)),
        pystray.MenuItem("Restart", lambda: root.after(0, restart_app)),
        pystray.MenuItem("Quit", lambda: root.after(0, quit_app)),
    )
    icon = pystray.Icon("GameTimeLimiter", icon_image, "Game Time Limiter", menu)
    icon.run_detached()

    def refresh_tooltip() -> None:
        try:
            status = tracker.get_status()
            parts = []
            for info in status["applications"].values():
                parts.append(f"{info['display_name']}: {_fmt(info['remaining_seconds'])} left")
            if status.get("shared_pool_minutes"):
                pool_left = status["shared_pool_minutes"] * 60 - status["shared_pool_used_seconds"]
                parts.append(f"Pool: {_fmt(max(0, pool_left))} left")
            icon.title = "Game Time Limiter\n" + "\n".join(parts) if parts else "Game Time Limiter"
        except Exception:
            logging.exception("Tray tooltip refresh failed.")
        root.after(TRAY_REFRESH_MS, refresh_tooltip)

    root.after(500, refresh_tooltip)
    root.mainloop()
