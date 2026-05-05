import copy
import logging
import threading
import time
from datetime import date, datetime, timedelta

import psutil

from storage import (
    add_game_seconds,
    get_game_seconds_today,
    get_today_key,
    load_data,
    load_state,
    reset_day_flags,
    save_data,
    save_state,
)
from notifications import (
    notify_killed_no_match_time,
    notify_killed_time_up,
    notify_shared_pool_killed,
    notify_warning,
)
from firewall import block_outbound, unblock_outbound, unblock_all


class GameTracker:
    """
    Monitors configured game processes and enforces daily time limits.

    Call check() on every poll tick (every poll_interval_seconds).
    The tracker is the single writer for self.data and self.state; the UI
    reads via the snapshot/lock helpers below.
    """

    def __init__(self, config: dict) -> None:
        self.applications: dict = config["applications"]
        self.warning_minutes: int = config["warning_minutes"]
        self.poll_interval: int = config["poll_interval_seconds"]
        self.shared_pool_minutes: int | None = config.get("shared_pool_minutes") or None
        self.grace_minutes: int = int(config.get("grace_minutes", 0) or 0)
        self.firewall_block_at_warning: bool = bool(config.get("firewall_block_at_warning", True))

        self.data: dict = load_data()
        self.state: dict = load_state()
        self._lock = threading.RLock()

        # In-memory only: which applications are currently running and have a live session.
        self.app_running: dict[str, bool] = {exe: False for exe in self.applications}

        # Wall-clock time of previous poll (for midnight-split delta).
        self._last_poll_dt: datetime | None = None

        # Detect mid-session date change for the in-memory flags.
        self._reset_flags_if_new_day()

    # ------------------------------------------------------------------
    # Day / state helpers
    # ------------------------------------------------------------------

    def _reset_flags_if_new_day(self) -> None:
        today = get_today_key()
        if self.state.get("today") != today:
            logging.info("Date changed to %s - resetting per-day flags", today)
            # Unblock any firewall rules from yesterday before clearing the map.
            previously_blocked = list(self.state.get("firewall_blocked", {}).keys())
            unblock_all(previously_blocked)
            reset_day_flags(self.state)
            save_state(self.state)
            # Refresh data from disk in case another process touched it.
            self.data = load_data()

    # ------------------------------------------------------------------
    # Process helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_processes(exe_name: str):
        target = exe_name.lower()
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info["name"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name and name.lower() == target:
                yield proc

    def _process_running(self, exe_name: str) -> bool:
        for _ in self._iter_processes(exe_name):
            return True
        return False

    def _kill_game(self, exe_name: str) -> bool:
        """Kill ALL processes matching exe_name. Returns True if at least one was killed."""
        killed_any = False
        for proc in self._iter_processes(exe_name):
            try:
                proc.kill()
                killed_any = True
                logging.info("Killed %s (pid=%s)", exe_name, proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                logging.warning("Could not kill %s: %s", exe_name, exc)
        return killed_any

    def _capture_exe_path(self, exe_name: str) -> str | None:
        """Record the full path to the exe in state.exe_paths (needed for firewall rules)."""
        cached = self.state.get("exe_paths", {}).get(exe_name)
        if cached:
            return cached
        for proc in self._iter_processes(exe_name):
            try:
                path = proc.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if path:
                self.state.setdefault("exe_paths", {})[exe_name] = path
                save_state(self.state)
                return path
        return None

    def _apply_firewall_block(self, exe_name: str) -> bool:
        """Apply firewall block if not already blocked. Returns True if a new block was applied."""
        blocked_map = self.state.setdefault("firewall_blocked", {})
        if blocked_map.get(exe_name):
            return False
        path = self._capture_exe_path(exe_name)
        if not path:
            logging.warning("Cannot block %s: exe path unknown.", exe_name)
            return False
        if block_outbound(exe_name, path):
            blocked_map[exe_name] = True
            save_state(self.state)
            return True
        return False

    # ------------------------------------------------------------------
    # Limit math
    # ------------------------------------------------------------------

    def _combined_today(self) -> int:
        day = self.data.get(get_today_key(), {})
        return sum(day.get(exe, 0) for exe in self.applications)

    def _shared_pool_remaining(self) -> int | None:
        if not self.shared_pool_minutes:
            return None
        return self.shared_pool_minutes * 60 - self._combined_today()

    def _effective_remaining(self, exe: str) -> int:
        """Remaining seconds for this app today.
        In pool mode the per-app daily_limit_minutes is ignored entirely; only the
        shared pool gates playtime. In per-app mode the per-app limit applies.
        """
        pool = self._shared_pool_remaining()
        if pool is not None:
            return pool
        cfg = self.applications[exe]
        return cfg["daily_limit_minutes"] * 60 - get_game_seconds_today(self.data, exe)

    # ------------------------------------------------------------------
    # Delta accumulation across midnight
    # ------------------------------------------------------------------

    def _accumulate_delta(self, prev_dt: datetime, now_dt: datetime, exe: str) -> int:
        """
        Add the elapsed seconds since prev_dt to the per-day buckets, splitting at
        midnight if needed. Caps the raw delta at 2x poll_interval to absorb sleep.
        Returns the seconds added to *today's* bucket (for warning math).
        """
        raw = (now_dt - prev_dt).total_seconds()
        capped = min(raw, self.poll_interval * 2)
        if capped <= 0:
            return 0

        # Walk back from now_dt by `capped` seconds.
        start_dt = now_dt - timedelta(seconds=capped)

        added_today = 0
        cursor = start_dt
        while cursor.date() < now_dt.date():
            next_midnight = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time())
            chunk = int((next_midnight - cursor).total_seconds())
            if chunk > 0:
                add_game_seconds(self.data, exe, chunk, day_key=cursor.date().isoformat())
            cursor = next_midnight

        # Final chunk on today's date.
        chunk = int((now_dt - cursor).total_seconds())
        if chunk > 0:
            add_game_seconds(self.data, exe, chunk, day_key=now_dt.date().isoformat())
            added_today = chunk
        return added_today

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Main poll method. Must be called every poll_interval_seconds seconds."""
        with self._lock:
            self._check_inner()

    def _check_inner(self) -> None:
        now_dt = datetime.now()
        self._reset_flags_if_new_day()

        any_data_change = False
        any_state_change = False
        warning_s = self.warning_minutes * 60

        warned = self.state["warned"]
        kill_notified = self.state["kill_notified"]

        for exe, cfg in self.applications.items():
            running = self._process_running(exe)
            min_match_s = cfg["min_match_minutes"] * 60

            if running:
                if not self.app_running[exe]:
                    # ── New session starting ────────────────────────────
                    remaining = self._effective_remaining(exe)
                    pool_remaining = self._shared_pool_remaining()

                    if remaining <= 0:
                        logging.info("%s launched but time exhausted.", exe)
                        # Cache path before killing so we can still block the firewall.
                        self._capture_exe_path(exe)
                        killed = self._kill_game(exe)
                        if self._apply_firewall_block(exe):
                            any_state_change = True
                        if killed and not kill_notified.get(exe):
                            if pool_remaining is not None and pool_remaining <= 0:
                                if notify_shared_pool_killed(cfg["display_name"]):
                                    kill_notified[exe] = True
                                    any_state_change = True
                            else:
                                if notify_killed_time_up(cfg["display_name"]):
                                    kill_notified[exe] = True
                                    any_state_change = True
                        continue

                    if remaining < min_match_s:
                        mins_left = remaining // 60
                        logging.info("%s launched but only %dm left.", exe, mins_left)
                        killed = self._kill_game(exe)
                        if killed and not kill_notified.get(exe):
                            if notify_killed_no_match_time(
                                cfg["display_name"], cfg["min_match_minutes"], mins_left
                            ):
                                kill_notified[exe] = True
                                any_state_change = True
                        continue

                    self.app_running[exe] = True
                    logging.info(
                        "%s session started. %dm remaining today.",
                        exe, remaining // 60,
                    )
                    # No delta to add this poll - first sighting of the process.
                    continue

                # ── Continuing session ─────────────────────────────────
                self._capture_exe_path(exe)  # opportunistic: cache path while running

                if self._last_poll_dt is not None:
                    added = self._accumulate_delta(self._last_poll_dt, now_dt, exe)
                    if added > 0:
                        any_data_change = True

                remaining = self._effective_remaining(exe)
                pool_remaining = self._shared_pool_remaining()
                grace_s = self.grace_minutes * 60

                if remaining <= 0:
                    # Limit hit. First time? Notify and (if grace=0) kill now.
                    if not kill_notified.get(exe):
                        if self.grace_minutes > 0:
                            if notify_warning(
                                cfg["display_name"] + " (limit reached)",
                                self.grace_minutes,
                            ):
                                kill_notified[exe] = True
                                any_state_change = True
                                logging.info("%s grace started (%dm).", exe, self.grace_minutes)
                        elif pool_remaining is not None and pool_remaining <= 0:
                            if notify_shared_pool_killed(cfg["display_name"]):
                                kill_notified[exe] = True
                                any_state_change = True
                        else:
                            if notify_killed_time_up(cfg["display_name"]):
                                kill_notified[exe] = True
                                any_state_change = True

                    # Always block the firewall once the limit is hit.
                    if self._apply_firewall_block(exe):
                        any_state_change = True

                    over_by = -remaining
                    if over_by >= grace_s:
                        # Past grace - kill now.
                        save_data(self.data)
                        self.app_running[exe] = False
                        self._kill_game(exe)
                        logging.info("%s killed: limit + grace exceeded.", exe)
                        any_data_change = False  # already saved above

                elif remaining <= warning_s and not warned.get(exe):
                    mins_left = max(1, remaining // 60)
                    if notify_warning(cfg["display_name"], mins_left):
                        warned[exe] = True
                        any_state_change = True
                        logging.info("Warning sent for %s: %dm remaining.", exe, mins_left)
                    if self.firewall_block_at_warning:
                        if self._apply_firewall_block(exe):
                            any_state_change = True

            else:
                if self.app_running[exe]:
                    self.app_running[exe] = False
                    logging.info(
                        "%s session ended. Total today: %ds.",
                        exe, get_game_seconds_today(self.data, exe),
                    )
                # If the game previously triggered a kill_notified flag but is
                # now gone, clear the flag so a future relaunch can warn again
                # within the same day if remaining is still 0 (but warned only
                # once - kill_notified is reset by midnight).
                # We leave kill_notified set for the day; relaunches in the same
                # day with no remaining time will be killed silently. That's the
                # correct behaviour: one notification per "you hit the wall" event.

        if any_data_change:
            save_data(self.data)
        if any_state_change:
            save_state(self.state)

        self._last_poll_dt = now_dt

    # ------------------------------------------------------------------
    # UI / read-only helpers
    # ------------------------------------------------------------------

    def get_live_snapshot(self) -> dict:
        """Deep copy of current playtime data (safe for cross-thread reads)."""
        with self._lock:
            return copy.deepcopy(self.data)

    def get_state_snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.state)

    def get_status(self) -> dict:
        """
        Return a structured status dict for UI consumption:
            {
              "today": "YYYY-MM-DD",
              "shared_pool_minutes": int | None,
              "shared_pool_used_seconds": int,
              "applications": {
                 exe: {
                    "display_name": str,
                    "limit_seconds": int,        # display limit (pool total in pool mode)
                    "per_app_limit_seconds": int,
                    "played_seconds": int,
                    "remaining_seconds": int,    # effective (incl. shared pool)
                    "running": bool,
                    "warned": bool,
                    "killed": bool,
                    "firewall_blocked": bool,
                 }, ...
              }
            }
        """
        with self._lock:
            self._reset_flags_if_new_day()
            today_key = get_today_key()
            day = self.data.get(today_key, {})
            warned = self.state.get("warned", {})
            kill_notified = self.state.get("kill_notified", {})
            combined = sum(day.get(exe, 0) for exe in self.applications)
            status = {
                "today": today_key,
                "shared_pool_minutes": self.shared_pool_minutes,
                "shared_pool_used_seconds": combined,
                "applications": {},
            }
            firewall_blocked = self.state.get("firewall_blocked", {})
            pool_remaining = self._shared_pool_remaining()
            for exe, cfg in self.applications.items():
                limit_s = cfg["daily_limit_minutes"] * 60
                played = day.get(exe, 0)
                # In pool mode, the displayed limit becomes the pool total so progress
                # bars / remaining text reflect the actual gate.
                if pool_remaining is not None:
                    display_limit_s = self.shared_pool_minutes * 60
                    effective = pool_remaining
                else:
                    display_limit_s = limit_s
                    effective = limit_s - played
                status["applications"][exe] = {
                    "display_name": cfg["display_name"],
                    "limit_seconds": display_limit_s,
                    "per_app_limit_seconds": limit_s,
                    "played_seconds": played,
                    "remaining_seconds": max(0, effective),
                    "running": self.app_running.get(exe, False),
                    "warned": bool(warned.get(exe)),
                    "killed": bool(kill_notified.get(exe)),
                    "firewall_blocked": bool(firewall_blocked.get(exe)),
                }
            status["grace_minutes"] = self.grace_minutes
            return status

    # ------------------------------------------------------------------
    # UI write helpers (config edit / usage edit)
    # ------------------------------------------------------------------

    def apply_config_update(self, new_config: dict) -> None:
        """Hot-apply a new config dict (edited via the settings window)."""
        with self._lock:
            self.applications = new_config["applications"]
            self.warning_minutes = new_config["warning_minutes"]
            self.poll_interval = new_config["poll_interval_seconds"]
            self.shared_pool_minutes = new_config.get("shared_pool_minutes") or None
            self.grace_minutes = int(new_config.get("grace_minutes", 0) or 0)
            self.firewall_block_at_warning = bool(new_config.get("firewall_block_at_warning", True))
            for exe in list(self.app_running.keys()):
                if exe not in self.applications:
                    self.app_running.pop(exe, None)
            for exe in self.applications:
                self.app_running.setdefault(exe, False)
            logging.info("Config hot-reloaded.")

    def set_today_seconds(self, exe: str, seconds: int) -> None:
        """Used by the usage editor to override today's count for a game."""
        with self._lock:
            from storage import set_game_seconds_today
            set_game_seconds_today(self.data, exe, seconds)
            # Clear per-day flags for this exe so warnings/kills can fire again.
            self.state["warned"].pop(exe, None)
            self.state["kill_notified"].pop(exe, None)
            # Unblock the firewall if reducing usage gives them time again.
            limit_s = self.applications[exe]["daily_limit_minutes"] * 60
            if seconds < limit_s and self.state.get("firewall_blocked", {}).get(exe):
                unblock_outbound(exe)
                self.state["firewall_blocked"].pop(exe, None)
            save_data(self.data)
            save_state(self.state)
            logging.info("Usage override: %s set to %ds.", exe, seconds)

    def shutdown_unblock_all(self) -> None:
        """Remove every firewall rule we've installed. Called on quit."""
        with self._lock:
            blocked = list(self.state.get("firewall_blocked", {}).keys())
            unblock_all(blocked)
            self.state["firewall_blocked"] = {}
            save_state(self.state)
