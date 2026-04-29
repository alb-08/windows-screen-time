import logging
import sys
import threading
import time
from datetime import date, datetime, timedelta

import schedule

from config import load_config, BASE_DIR
from notifications import notify_daily_summary, notify_weekly_summary
from storage import (
    get_today_key,
    get_week_data,
    get_last_week_monday,
    save_state,
)
from tracker import GameTracker

# ── Load config ────────────────────────────────────────────────────────────────
config = load_config()

# ── Logging setup ─────────────────────────────────────────────────────────────
log_path = BASE_DIR / config.get("log_file", "game_limiter.log")
logging.basicConfig(
    filename=str(log_path),
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))

# ── Tracker ────────────────────────────────────────────────────────────────────
tracker = GameTracker(config)


# ── Summary callbacks ──────────────────────────────────────────────────────────

def _send_daily_summary_for(day_key: str) -> None:
    data = tracker.get_live_snapshot()
    pool = config.get("shared_pool_minutes") or None
    if notify_daily_summary(config["games"], data, day_key, shared_pool_minutes=pool):
        with tracker._lock:
            tracker.state["last_daily_summary"] = day_key
            save_state(tracker.state)
        logging.info("Daily summary sent for %s.", day_key)


def _send_weekly_summary_for(monday: date) -> None:
    data = tracker.get_live_snapshot()
    week_data = get_week_data(data, monday=monday)
    if notify_weekly_summary(config["games"], week_data):
        with tracker._lock:
            tracker.state["last_weekly_summary_monday"] = monday.isoformat()
            save_state(tracker.state)
        logging.info("Weekly summary sent for week of %s.", monday.isoformat())


def _scheduled_daily() -> None:
    _send_daily_summary_for(get_today_key())


def _scheduled_weekly() -> None:
    # Fires Monday morning, summarising the *previous* Mon-Sun week.
    _send_weekly_summary_for(get_last_week_monday())


# ── Missed-job catch-up ────────────────────────────────────────────────────────

def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def catchup_missed_summaries() -> None:
    """
    On startup, fire any daily/weekly summary that should have already run today/this week
    but didn't because the process was offline at the scheduled time.
    """
    now = datetime.now()
    today = now.date()
    daily_h, daily_m = _parse_hhmm(config.get("daily_summary_time", "22:00"))
    weekly_h, weekly_m = _parse_hhmm(config.get("weekly_summary_time", "09:00"))

    state = tracker.get_state_snapshot()

    # Daily: if we're past today's daily_summary_time and haven't sent today's summary, send it.
    daily_due = now >= datetime.combine(today, datetime.min.time()).replace(hour=daily_h, minute=daily_m)
    if daily_due and state.get("last_daily_summary") != today.isoformat():
        _send_daily_summary_for(today.isoformat())

    # Weekly: if we're past Monday's weekly_summary_time and haven't sent this week's summary, send it.
    this_monday = today - timedelta(days=today.weekday())
    weekly_anchor = datetime.combine(this_monday, datetime.min.time()).replace(hour=weekly_h, minute=weekly_m)
    weekly_due = now >= weekly_anchor
    last_week_monday = this_monday - timedelta(days=7)
    if weekly_due and state.get("last_weekly_summary_monday") != last_week_monday.isoformat():
        _send_weekly_summary_for(last_week_monday)


# ── Register schedules ─────────────────────────────────────────────────────────

daily_time = config.get("daily_summary_time", "22:00")
weekly_time = config.get("weekly_summary_time", "09:00")

schedule.every().day.at(daily_time).do(_scheduled_daily)
schedule.every().monday.at(weekly_time).do(_scheduled_weekly)

logging.info(
    "Game Time Limiter started. Daily %s, weekly Mondays %s. Shared pool: %s.",
    daily_time, weekly_time,
    config.get("shared_pool_minutes") or "off",
)

catchup_missed_summaries()


# ── Startup firewall reconciliation ────────────────────────────────────────────
# If the previous run was killed without unblocking, state.firewall_blocked may
# already record rules in netsh. The midnight reset path in tracker unblocks
# everything at date rollover; for same-day restarts we leave the rules alone
# since they correctly reflect "limit hit today".


# ── UI ─────────────────────────────────────────────────────────────────────────

def _start_ui() -> None:
    try:
        from ui import run_ui
    except Exception as exc:
        logging.warning("UI dependencies missing or failed to import: %s", exc)
        return

    def _runner() -> None:
        try:
            run_ui(tracker, config)
        except Exception:
            logging.exception("UI thread crashed.")

    t = threading.Thread(target=_runner, name="GameLimiterUI", daemon=True)
    t.start()


_start_ui()


# ── Main loop ──────────────────────────────────────────────────────────────────
poll_interval = config.get("poll_interval_seconds", 5)

while True:
    try:
        schedule.run_pending()
        tracker.check()
    except KeyboardInterrupt:
        logging.info("Game Time Limiter stopped by user.")
        sys.exit(0)
    except Exception as exc:
        logging.error("Unhandled error in main loop: %s", exc, exc_info=True)
    time.sleep(poll_interval)
