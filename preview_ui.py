"""
Visual preview of the proposed UI changes. Run on Windows:

    python preview_ui.py

This is a throwaway preview - no real config or tracker. Two buttons launch
the Manage Applications window and the redesigned Settings window with
fake data so you can see the layout, resizing, and tab order before any
real implementation.
"""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

# ── Fake data (stand-in for tracker.get_status() / config) ────────────────────

FAKE_APPS = [
    {"exe": "RocketLeague.exe",  "display_name": "Rocket League",     "limit": 120, "match": 15, "played_min": 47, "running": False},
    {"exe": "RainbowSix.exe",    "display_name": "Rainbow Six Siege", "limit": 120, "match": 15, "played_min": 0,  "running": False},
    {"exe": "discord.exe",       "display_name": "Discord",           "limit":  60, "match":  0, "played_min": 12, "running": True},
]

FAKE_RUNNING_PROCESSES = [
    {"name": "discord.exe",  "title": "Discord",  "path": r"C:\Users\you\AppData\Local\Discord\app-1.0\Discord.exe"},
    {"name": "Spotify.exe",  "title": "Spotify",  "path": r"C:\Users\you\AppData\Roaming\Spotify\Spotify.exe"},
    {"name": "steam.exe",    "title": "Steam",    "path": r"C:\Program Files (x86)\Steam\steam.exe"},
    {"name": "obs64.exe",    "title": "OBS Studio", "path": r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"},
    {"name": "chrome.exe",   "title": "Google Chrome", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
]


# ── Manage Applications window ────────────────────────────────────────────────

class ManageApplicationsPreview:
    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Manage Applications")
        self.win.geometry("680x440")
        self.win.minsize(560, 320)

        self.apps = [dict(a) for a in FAKE_APPS]
        self._build()

    def _build(self):
        outer = ttk.Frame(self.win, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Tracked applications",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Separator(outer).pack(fill="x", pady=(4, 8))

        # Treeview with scrollbar
        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)

        cols = ("display_name", "exe", "limit", "match", "today")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        self.tree.heading("display_name", text="Display name")
        self.tree.heading("exe", text="Exe")
        self.tree.heading("limit", text="Limit (min)")
        self.tree.heading("match", text="Min match (min)")
        self.tree.heading("today", text="Today")
        self.tree.column("display_name", width=180, anchor="w")
        self.tree.column("exe", width=180, anchor="w")
        self.tree.column("limit", width=90, anchor="center")
        self.tree.column("match", width=110, anchor="center")
        self.tree.column("today", width=80, anchor="center")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._edit())

        # Buttons row
        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Add from running…", command=self._add_running).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Add manually…",     command=self._add_manual).pack(side="left", padx=4)
        self.edit_btn   = ttk.Button(btns, text="Edit…",   command=self._edit,   state="disabled")
        self.remove_btn = ttk.Button(btns, text="Remove…", command=self._remove, state="disabled")
        self.edit_btn.pack(side="left", padx=4)
        self.remove_btn.pack(side="left", padx=4)

        ttk.Label(outer, text="* = currently running",
                  foreground="#666").pack(anchor="w", pady=(8, 0))

        # Close
        close_row = ttk.Frame(outer)
        close_row.pack(fill="x", pady=(8, 0))
        ttk.Button(close_row, text="Close", command=self.win.destroy).pack(side="right")

        self._refresh_tree()
        self._tick()  # 1s "today" refresh demo

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.apps:
            self.tree.insert("", "end", values=("(no applications tracked yet)", "", "", "", ""))
            self.edit_btn.configure(state="disabled")
            self.remove_btn.configure(state="disabled")
            return
        for app in self.apps:
            display = app["display_name"] + (" *" if app["running"] else "")
            self.tree.insert("", "end", iid=app["exe"], values=(
                display, app["exe"], f"{app['limit']} m",
                f"{app['match']} m", f"{app['played_min']} m",
            ))

    def _tick(self):
        # Demo: increment Discord's "today" each second to show live refresh.
        for a in self.apps:
            if a["running"]:
                a["played_min"] = min(a["limit"], a["played_min"] + 1)
        self._refresh_tree()
        self.win.after(1000, self._tick)

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        st = "normal" if sel else "disabled"
        self.edit_btn.configure(state=st)
        self.remove_btn.configure(state=st)

    def _add_running(self):
        AppFormDialog(self.win, "Add from running processes",
                      processes=FAKE_RUNNING_PROCESSES, on_ok=self._add_app)

    def _add_manual(self):
        AppFormDialog(self.win, "Add application manually",
                      processes=None, on_ok=self._add_app)

    def _edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        exe = sel[0]
        app = next((a for a in self.apps if a["exe"] == exe), None)
        if not app:
            return
        AppFormDialog(self.win, "Edit application", processes=None,
                      initial=app, on_ok=lambda updated: self._update_app(exe, updated))

    def _add_app(self, app):
        if any(a["exe"].lower() == app["exe"].lower() for a in self.apps):
            messagebox.showerror("Already tracked",
                                 f"{app['exe']} is already in the list. Use Edit.",
                                 parent=self.win)
            return
        self.apps.append({**app, "played_min": 0, "running": False})
        self._refresh_tree()

    def _update_app(self, exe, updated):
        for a in self.apps:
            if a["exe"] == exe:
                a.update(updated)
                break
        self._refresh_tree()

    def _remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        exe = sel[0]
        # Simulate passcode prompt
        pc = simpledialog.askstring("Passcode required",
                                    f"Enter passcode to stop tracking {exe}:",
                                    show="*", parent=self.win)
        if pc is None:
            return
        if not messagebox.askyesno("Confirm",
                                   f"Stop tracking {exe}? Today's recorded usage will be discarded.",
                                   parent=self.win):
            return
        self.apps = [a for a in self.apps if a["exe"] != exe]
        self._refresh_tree()


# ── Add / Edit form dialog ────────────────────────────────────────────────────

class AppFormDialog:
    def __init__(self, parent, title, processes=None, initial=None, on_ok=None):
        self.on_ok = on_ok
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.geometry("520x" + ("440" if processes else "240"))
        self.win.resizable(False, False)

        outer = ttk.Frame(self.win, padding=12)
        outer.pack(fill="both", expand=True)

        if processes is not None:
            ttk.Label(outer, text="Pick a running process:").pack(anchor="w")
            top = ttk.Frame(outer)
            top.pack(fill="x", pady=(2, 6))
            ttk.Label(top, text="Filter:").pack(side="left")
            self.filter_var = tk.StringVar()
            self.filter_var.trace_add("write", lambda *_: self._refilter())
            ttk.Entry(top, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(top, text="Refresh", command=self._refilter).pack(side="left")

            list_frame = ttk.Frame(outer)
            list_frame.pack(fill="both", expand=True)
            self.proc_tree = ttk.Treeview(list_frame, columns=("title", "exe", "path"),
                                          show="headings", height=6)
            self.proc_tree.heading("title", text="Title")
            self.proc_tree.heading("exe",   text="Exe")
            self.proc_tree.heading("path",  text="Path")
            self.proc_tree.column("title", width=120, anchor="w")
            self.proc_tree.column("exe",   width=120, anchor="w")
            self.proc_tree.column("path",  width=240, anchor="w")
            sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.proc_tree.yview)
            self.proc_tree.configure(yscrollcommand=sb.set)
            self.proc_tree.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            self.proc_tree.bind("<<TreeviewSelect>>", self._on_proc_select)
            self._all_procs = processes
            self._refilter()
            ttk.Separator(outer).pack(fill="x", pady=8)

        # Form fields
        form = ttk.Frame(outer)
        form.pack(fill="x")
        ttk.Label(form, text="Display name:").grid(row=0, column=0, sticky="w", pady=2)
        self.display_var = tk.StringVar(value=(initial or {}).get("display_name", ""))
        ttk.Entry(form, textvariable=self.display_var, width=30).grid(row=0, column=1, padx=6, pady=2, sticky="w")

        ttk.Label(form, text="Exe name:").grid(row=1, column=0, sticky="w", pady=2)
        self.exe_var = tk.StringVar(value=(initial or {}).get("exe", ""))
        self.exe_entry = ttk.Entry(form, textvariable=self.exe_var, width=30)
        self.exe_entry.grid(row=1, column=1, padx=6, pady=2, sticky="w")
        if initial:
            self.exe_entry.configure(state="readonly")  # don't allow renaming exe in edit

        ttk.Label(form, text="Daily limit (min):").grid(row=2, column=0, sticky="w", pady=2)
        self.limit_var = tk.StringVar(value=str((initial or {}).get("limit", 60)))
        ttk.Entry(form, textvariable=self.limit_var, width=10).grid(row=2, column=1, padx=6, pady=2, sticky="w")

        ttk.Label(form, text="Min match (min):").grid(row=3, column=0, sticky="w", pady=2)
        self.match_var = tk.StringVar(value=str((initial or {}).get("match", 0)))
        ttk.Entry(form, textvariable=self.match_var, width=10).grid(row=3, column=1, padx=6, pady=2, sticky="w")

        # Buttons
        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="OK",     command=self._submit).pack(side="right", padx=4)

    def _refilter(self):
        flt = self.filter_var.get().lower() if hasattr(self, "filter_var") else ""
        self.proc_tree.delete(*self.proc_tree.get_children())
        for p in self._all_procs:
            if flt and flt not in p["title"].lower() and flt not in p["name"].lower():
                continue
            self.proc_tree.insert("", "end", iid=p["name"], values=(p["title"], p["name"], p["path"]))

    def _on_proc_select(self, _evt):
        sel = self.proc_tree.selection()
        if not sel:
            return
        exe = sel[0]
        proc = next((p for p in self._all_procs if p["name"] == exe), None)
        if not proc:
            return
        self.display_var.set(proc["title"])
        self.exe_var.set(proc["name"])

    def _submit(self):
        try:
            limit = int(self.limit_var.get())
            match = int(self.match_var.get())
            if limit <= 0 or match < 0 or match > limit:
                raise ValueError
            display = self.display_var.get().strip()
            exe = self.exe_var.get().strip()
            if not display or not exe:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid input", "Check name, exe, limit, and min match.",
                                 parent=self.win)
            return
        if self.on_ok:
            self.on_ok({"display_name": display, "exe": exe, "limit": limit, "match": match})
        self.win.destroy()


# ── Settings preview with pool toggle ─────────────────────────────────────────

class SettingsPreview:
    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.title("Game Time - Settings")
        self.win.geometry("540x540")

        self.mode = tk.StringVar(value="per_app")
        self.pool_total = tk.StringVar(value="180")
        self.warning = tk.StringVar(value="15")
        self.grace = tk.StringVar(value="5")
        self.firewall = tk.BooleanVar(value=True)
        self.daily_time = tk.StringVar(value="22:00")
        self.weekly_time = tk.StringVar(value="09:00")
        self.entries = {}
        self._build()

    def _build(self):
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill="both", expand=True)

        # Mode picker
        mode_box = ttk.LabelFrame(f, text="Limit mode", padding=8)
        mode_box.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(mode_box, text="Per-application limits",
                        variable=self.mode, value="per_app",
                        command=self._on_mode_change).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(mode_box, text="Combined pool",
                        variable=self.mode, value="pool",
                        command=self._on_mode_change).pack(side="left", padx=(0, 12))
        ttk.Button(mode_box, text="?", width=3, command=self._show_help).pack(side="right")

        # Pool field (visible only in pool mode)
        self.pool_frame = ttk.LabelFrame(f, text="Total daily minutes (shared by all apps)", padding=8)
        ttk.Entry(self.pool_frame, textvariable=self.pool_total, width=10).pack(side="left")
        ttk.Label(self.pool_frame, text="min").pack(side="left", padx=(4, 0))

        # Per-app grid
        self.app_frame = ttk.LabelFrame(f, text="Per-application limits", padding=8)
        self.app_frame.pack(fill="x")
        ttk.Label(self.app_frame, text="Application").grid(row=0, column=0, sticky="w")
        self.limit_header = ttk.Label(self.app_frame, text="Daily limit (min)")
        self.limit_header.grid(row=0, column=1, padx=8)
        ttk.Label(self.app_frame, text="Min match (min)").grid(row=0, column=2, padx=8)
        for i, app in enumerate(FAKE_APPS):
            ttk.Label(self.app_frame, text=app["display_name"]).grid(row=i+1, column=0, sticky="w", pady=2)
            limit_var = tk.StringVar(value=str(app["limit"]))
            limit_e = ttk.Entry(self.app_frame, width=8, textvariable=limit_var)
            limit_e.grid(row=i+1, column=1, padx=8)
            match_var = tk.StringVar(value=str(app["match"]))
            ttk.Entry(self.app_frame, width=8, textvariable=match_var).grid(row=i+1, column=2, padx=8)
            self.entries[app["exe"]] = (limit_e, limit_var, match_var)

        ttk.Separator(f).pack(fill="x", pady=8)

        # Other fields
        misc = ttk.Frame(f)
        misc.pack(fill="x")
        ttk.Label(misc, text="Warning minutes:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(misc, textvariable=self.warning, width=8).grid(row=0, column=1, padx=8, sticky="w")
        ttk.Label(misc, text="Grace after limit (min):").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(misc, textvariable=self.grace, width=8).grid(row=1, column=1, padx=8, sticky="w")
        ttk.Label(misc, text="(lets the current match finish)",
                  foreground="#666").grid(row=1, column=2, sticky="w")
        ttk.Checkbutton(misc, text="Block game's internet during warning window",
                        variable=self.firewall).grid(row=2, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(misc, text="Daily summary (HH:MM):").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(misc, textvariable=self.daily_time, width=8).grid(row=3, column=1, padx=8, sticky="w")
        ttk.Label(misc, text="Weekly summary (Mon HH:MM):").grid(row=4, column=0, sticky="w", pady=2)
        ttk.Entry(misc, textvariable=self.weekly_time, width=8).grid(row=4, column=1, padx=8, sticky="w")

        ttk.Separator(f).pack(fill="x", pady=8)
        pc = ttk.Frame(f)
        pc.pack(fill="x")
        ttk.Label(pc, text="Passcode: not set").pack(side="left")
        ttk.Button(pc, text="Set / change…",
                   command=lambda: messagebox.showinfo("Demo",
                                                       "Passcode dialog appears here.",
                                                       parent=self.win)
                   ).pack(side="left", padx=8)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save",
                   command=lambda: messagebox.showinfo("Demo",
                                                       "Save here would write config.json + apply.",
                                                       parent=self.win)
                   ).pack(side="right", padx=4)

    def _on_mode_change(self):
        if self.mode.get() == "pool":
            self.pool_frame.pack(fill="x", pady=(0, 8), before=self.app_frame)
            self.limit_header.configure(text="Per-app cap (ignored in pool mode)")
            for limit_e, _, _ in self.entries.values():
                limit_e.configure(state="disabled")
        else:
            self.pool_frame.pack_forget()
            self.limit_header.configure(text="Daily limit (min)")
            for limit_e, _, _ in self.entries.values():
                limit_e.configure(state="normal")

    def _show_help(self):
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


# ── Launcher ─────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.title("UI Preview")
    root.geometry("320x140")

    ttk.Label(root, text="Game Time Limiter — UI preview",
              font=("Segoe UI", 11, "bold")).pack(pady=(12, 6))
    ttk.Label(root, text="Click to open each redesigned window.",
              foreground="#666").pack(pady=(0, 8))

    btns = ttk.Frame(root)
    btns.pack(pady=4)
    ttk.Button(btns, text="Manage Applications",
               command=lambda: ManageApplicationsPreview(root)).pack(side="left", padx=4)
    ttk.Button(btns, text="Settings (with pool toggle)",
               command=lambda: SettingsPreview(root)).pack(side="left", padx=4)

    root.mainloop()


if __name__ == "__main__":
    main()
