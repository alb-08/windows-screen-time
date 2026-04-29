import logging
import sys
import time

import schedule

from config import load_config, BASE_DIR
from notifications import notify_daily_summary, notify_weekly_summary
from storage import get_today_key, get_week_data
from tracker import GameTracker

config = load_config()

log_path = BASE_DIR / config.get("log_file", "game_limiter.log")
logging.basicConfig(
    filename=str(log_path),
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))

tracker = GameTracker(config)


def _send_daily_summary() -> None:
    """Called by the scheduler at the configured daily_summary_time."""
    data = tracker.get_live_snapshot()
    day_key = get_today_key()
    notify_daily_summary(config["games"], data, day_key)
    logging.info("Daily summary notification sent.")


def _send_weekly_summary() -> None:
    """Called by the scheduler every Sunday at the configured weekly_summary_time."""
    data = tracker.get_live_snapshot()
    week_data = get_week_data(data)
    notify_weekly_summary(config["games"], week_data)
    logging.info("Weekly summary notification sent.")


daily_time = config.get("daily_summary_time", "22:00")
weekly_time = config.get("weekly_summary_time", "20:00")

schedule.every().day.at(daily_time).do(_send_daily_summary)
schedule.every().sunday.at(weekly_time).do(_send_weekly_summary)

logging.info(
    "Game Time Limiter started. Daily summary at %s. Weekly summary Sundays at %s.",
    daily_time, weekly_time,
)

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
