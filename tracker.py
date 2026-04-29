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


class GameTracker:
    """
    Monitors configured game processes and enforces daily time limits.

    Call check() on every poll tick (every poll_interval_seconds).
    The tracker is the single writer for self.data and self.state; the UI
    reads via the snapshot/lock helpers below.
    """

    def __init__(self, config: dict) -> None:
        self.games: dict = config["games"]
        self.warning_minutes: int = config["warning_minutes"]
        self.poll_interval: int = config["poll_interval_seconds"]
        self.shared_pool_minutes: int | None = config.get("shared_pool_minutes") or None

        self.data: dict = load_data()
        self.state: dict = load_state()
        self._lock = threading.RLock()

        # In-memory only: which games are currently running and have a live session.
        self.game_running: dict[str, bool] = {exe: False for exe in self.games}

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

    # ------------------------------------------------------------------
    # Limit math
    # ------------------------------------------------------------------

    def _combined_today(self) -> int:
        day = self.data.get(get_today_key(), {})
        return sum(day.get(exe, 0) for exe in self.games)

    def _shared_pool_remaining(self) -> int | None:
        if not self.shared_pool_minutes:
            return None
        return self.shared_pool_minutes * 60 - self._combined_today()

    def _effective_remaining(self, exe: str) -> int:
        """Remaining seconds for this game today, taking shared pool (if any) into account."""
        cfg = self.games[exe]
        per_game = cfg["daily_limit_minutes"] * 60 - get_game_seconds_today(self.data, exe)
        pool = self._shared_pool_remaining()
        return per_game if pool is None else min(per_game, pool)

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

        for exe, cfg in self.games.items():
            running = self._process_running(exe)
            min_match_s = cfg["min_match_minutes"] * 60

            if running:
                if not self.game_running[exe]:
                    # ── New session starting ────────────────────────────
                    remaining = self._effective_remaining(exe)
                    pool_remaining = self._shared_pool_remaining()

                    if remaining <= 0:
                        logging.info("%s launched but time exhausted.", exe)
                        killed = self._kill_game(exe)
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

                    self.game_running[exe] = True
                    logging.info(
                        "%s session started. %dm remaining today.",
                        exe, remaining // 60,
                    )
                    # No delta to add this poll - first sighting of the process.
                    continue

                # ── Continuing session ─────────────────────────────────
                if self._last_poll_dt is not None:
                    added = self._accumulate_delta(self._last_poll_dt, now_dt, exe)
                    if added > 0:
                        any_data_change = True

                remaining = self._effective_remaining(exe)
                pool_remaining = self._shared_pool_remaining()

                if remaining <= 0:
                    save_data(self.data)
                    self.game_running[exe] = False
                    killed = self._kill_game(exe)
                    if killed and not kill_notified.get(exe):
                        if pool_remaining is not None and pool_remaining <= 0:
                            if notify_shared_pool_killed(cfg["display_name"]):
                                kill_notified[exe] = True
                                any_state_change = True
                        else:
                            if notify_killed_time_up(cfg["display_name"]):
                                kill_notified[exe] = True
                                any_state_change = True
                    logging.info("%s killed: limit reached.", exe)
                    any_data_change = False  # already saved above

                elif remaining <= warning_s and not warned.get(exe):
                    mins_left = max(1, remaining // 60)
                    if notify_warning(cfg["display_name"], mins_left):
                        warned[exe] = True
                        any_state_change = True
                        logging.info("Warning sent for %s: %dm remaining.", exe, mins_left)

            else:
                if self.game_running[exe]:
                    self.game_running[exe] = False
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
              "games": {
                 exe: {
                    "display_name": str,
                    "limit_seconds": int,
                    "played_seconds": int,
                    "remaining_seconds": int,  # effective (incl. shared pool)
                    "running": bool,
                    "warned": bool,
                    "killed": bool,
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
            combined = sum(day.get(exe, 0) for exe in self.games)
            status = {
                "today": today_key,
                "shared_pool_minutes": self.shared_pool_minutes,
                "shared_pool_used_seconds": combined,
                "games": {},
            }
            for exe, cfg in self.games.items():
                limit_s = cfg["daily_limit_minutes"] * 60
                played = day.get(exe, 0)
                per_game_remaining = limit_s - played
                pool_remaining = self._shared_pool_remaining()
                effective = (per_game_remaining if pool_remaining is None
                             else min(per_game_remaining, pool_remaining))
                status["games"][exe] = {
                    "display_name": cfg["display_name"],
                    "limit_seconds": limit_s,
                    "played_seconds": played,
                    "remaining_seconds": max(0, effective),
                    "running": self.game_running.get(exe, False),
                    "warned": bool(warned.get(exe)),
                    "killed": bool(kill_notified.get(exe)),
                }
            return status

    # ------------------------------------------------------------------
    # UI write helpers (config edit / usage edit)
    # ------------------------------------------------------------------

    def apply_config_update(self, new_config: dict) -> None:
        """Hot-apply a new config dict (edited via the settings window)."""
        with self._lock:
            self.games = new_config["games"]
            self.warning_minutes = new_config["warning_minutes"]
            self.poll_interval = new_config["poll_interval_seconds"]
            self.shared_pool_minutes = new_config.get("shared_pool_minutes") or None
            for exe in self.games:
                self.game_running.setdefault(exe, False)
            logging.info("Config hot-reloaded.")

    def set_today_seconds(self, exe: str, seconds: int) -> None:
        """Used by the usage editor to override today's count for a game."""
        with self._lock:
            from storage import set_game_seconds_today
            set_game_seconds_today(self.data, exe, seconds)
            # Reset the per-day flags for this exe so warnings/kills can fire again
            # if the user reset usage to zero.
            self.state["warned"].pop(exe, None)
            self.state["kill_notified"].pop(exe, None)
            save_data(self.data)
            save_state(self.state)
            logging.info("Usage override: %s set to %ds.", exe, seconds)
