import json
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "playtime.json"
MAX_HISTORY_DAYS = 7


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_data() -> dict:
    """
    Load persisted playtime data from disk.
    Returns an empty dict if the file does not exist or is corrupted.
    Structure: {"YYYY-MM-DD": {"ExeName.exe": <seconds_int>, ...}, ...}
    """
    _ensure_data_dir()
    if not DATA_FILE.exists():
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_data(data: dict) -> None:
    """Persist playtime data to disk. Prunes entries older than MAX_HISTORY_DAYS."""
    _ensure_data_dir()
    today = date.today()
    cutoff = (today - timedelta(days=MAX_HISTORY_DAYS - 1)).isoformat()
    pruned = {k: v for k, v in data.items() if k >= cutoff}
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f, indent=2)


def get_today_key() -> str:
    return date.today().isoformat()


def get_game_seconds_today(data: dict, exe_name: str) -> int:
    """Return seconds played today for the given exe (0 if no data)."""
    return data.get(get_today_key(), {}).get(exe_name, 0)


def add_game_seconds(data: dict, exe_name: str, seconds: int) -> None:
    """Add seconds to today's total for the given exe. Modifies data in-place."""
    if seconds <= 0:
        return
    day_key = get_today_key()
    if day_key not in data:
        data[day_key] = {}
    data[day_key][exe_name] = data[day_key].get(exe_name, 0) + seconds


def get_week_data(data: dict) -> dict:
    """
    Return a dict of {ISO_date_str: {exe: seconds}} for the current Mon-Sun week.
    Missing days are returned as empty dicts (not omitted), so callers always
    get exactly 7 entries.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week = {}
    for i in range(7):
        day = monday + timedelta(days=i)
        key = day.isoformat()
        week[key] = data.get(key, {})
    return week
