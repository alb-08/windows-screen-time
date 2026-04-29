import copy
import logging
import time
from datetime import date

import psutil

from storage import (
    add_game_seconds,
    get_game_seconds_today,
    load_data,
    save_data,
)
from notifications import (
    notify_killed_no_match_time,
    notify_killed_time_up,
    notify_warning,
)


class GameTracker:
    """
    Monitors configured game processes and enforces daily time limits.

    Call check() on every poll tick (i.e. every poll_interval_seconds).
    """

    def __init__(self, config: dict) -> None:
        self.games: dict = config["games"]
        self.warning_minutes: int = config["warning_minutes"]
        self.poll_interval: int = config["poll_interval_seconds"]

        self.data: dict = load_data()

        self.game_running: dict[str, bool] = {exe: False for exe in self.games}
        self.warning_sent: dict[str, bool] = {exe: False for exe in self.games}

        self._last_poll_time: float | None = None

        self._last_date: str = date.today().isoformat()

    def _get_process(self, exe_name: str) -> psutil.Process | None:
        """Return the first running process whose name matches exe_name (case-insensitive)."""
        target = exe_name.lower()
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"].lower() == target:
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return None

    def _kill_game(self, exe_name: str) -> None:
        """Kill ALL processes matching exe_name."""
        target = exe_name.lower()
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"].lower() == target:
                    proc.kill()
                    logging.info("Killed %s (pid=%s)", exe_name, proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                logging.warning("Could not kill %s: %s", exe_name, exc)

    def _check_midnight_reset(self) -> None:
        """At midnight, reload data from disk and reset per-game warning flags."""
        today = date.today().isoformat()
        if today != self._last_date:
            logging.info("Date changed to %s - resetting warning flags", today)
            self._last_date = today
            self.data = load_data()
            for exe in self.games:
                self.warning_sent[exe] = False

    def check(self) -> None:
        """Main poll method. Must be called every poll_interval_seconds seconds."""
        now = time.monotonic()
        self._check_midnight_reset()

        if self._last_poll_time is None:
            poll_delta = 0
        else:
            raw_delta = now - self._last_poll_time
            poll_delta = int(min(raw_delta, self.poll_interval * 2))

        any_running = False

        for exe, cfg in self.games.items():
            proc = self._get_process(exe)
            limit_s = cfg["daily_limit_minutes"] * 60
            min_match_s = cfg["min_match_minutes"] * 60
            warning_s = self.warning_minutes * 60

            if proc is not None:
                if not self.game_running[exe]:
                    played_base = get_game_seconds_today(self.data, exe)
                    remaining_base = limit_s - played_base

                    if remaining_base <= 0:
                        logging.info(
                            "%s launched but time is fully exhausted (%ds played).",
                            exe, played_base,
                        )
                        self._kill_game(exe)
                        notify_killed_time_up(cfg["display_name"])
                        continue

                    elif remaining_base < min_match_s:
                        mins_left = remaining_base // 60
                        logging.info(
                            "%s launched but only %dm left - below min match time %dm.",
                            exe, mins_left, cfg["min_match_minutes"],
                        )
                        self._kill_game(exe)
                        notify_killed_no_match_time(
                            cfg["display_name"], cfg["min_match_minutes"], mins_left
                        )
                        continue

                    else:
                        self.game_running[exe] = True
                        logging.info(
                            "%s session started. %dm remaining today.",
                            exe, remaining_base // 60,
                        )

                else:
                    if poll_delta > 0:
                        add_game_seconds(self.data, exe, poll_delta)
                        any_running = True

                    played_now = get_game_seconds_today(self.data, exe)
                    remaining_now = limit_s - played_now

                    if remaining_now <= 0:
                        save_data(self.data)
                        self.game_running[exe] = False
                        self._kill_game(exe)
                        notify_killed_time_up(cfg["display_name"])
                        logging.info(
                            "%s killed: daily limit reached (%ds played).",
                            exe, played_now,
                        )

                    elif remaining_now <= warning_s and not self.warning_sent[exe]:
                        mins_left = max(1, remaining_now // 60)
                        notify_warning(cfg["display_name"], mins_left)
                        self.warning_sent[exe] = True
                        logging.info(
                            "Warning sent for %s: %dm remaining.", exe, mins_left
                        )

            else:
                if self.game_running[exe]:
                    self.game_running[exe] = False
                    played_total = get_game_seconds_today(self.data, exe)
                    logging.info(
                        "%s session ended. Total today: %ds.", exe, played_total
                    )

        if any_running:
            save_data(self.data)

        self._last_poll_time = now

    def get_live_snapshot(self) -> dict:
        """Return a copy of self.data for display purposes (e.g. daily summary)."""
        return copy.deepcopy(self.data)
