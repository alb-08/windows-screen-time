import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "playtime.json"
STATE_FILE = DATA_DIR / "state.json"
MAX_HISTORY_DAYS = 7


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON to a temp file in the same dir, fsync, then os.replace onto target."""
    _ensure_data_dir()
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Playtime data
# ---------------------------------------------------------------------------

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
    """Persist playtime data to disk atomically. Prunes entries older than MAX_HISTORY_DAYS."""
    today = date.today()
    cutoff = (today - timedelta(days=MAX_HISTORY_DAYS - 1)).isoformat()
    pruned = {k: v for k, v in data.items() if k >= cutoff}
    _atomic_write_json(DATA_FILE, pruned)


def get_today_key() -> str:
    return date.today().isoformat()


def get_game_seconds_today(data: dict, exe_name: str) -> int:
    """Return seconds played today for the given exe (0 if no data)."""
    return data.get(get_today_key(), {}).get(exe_name, 0)


def get_game_seconds_for_date(data: dict, day_key: str, exe_name: str) -> int:
    return data.get(day_key, {}).get(exe_name, 0)


def add_game_seconds(data: dict, exe_name: str, seconds: int, day_key: str | None = None) -> None:
    """Add seconds to the given day's total for the given exe. Modifies data in-place."""
    if seconds <= 0:
        return
    if day_key is None:
        day_key = get_today_key()
    if day_key not in data:
        data[day_key] = {}
    data[day_key][exe_name] = data[day_key].get(exe_name, 0) + seconds


def set_game_seconds_today(data: dict, exe_name: str, seconds: int) -> None:
    """Replace today's seconds for the given exe (used by the usage editor)."""
    seconds = max(0, int(seconds))
    day_key = get_today_key()
    if day_key not in data:
        data[day_key] = {}
    data[day_key][exe_name] = seconds


def get_week_data(data: dict, monday: date | None = None) -> dict:
    """
    Return a dict of {ISO_date_str: {exe: seconds}} for a Mon-Sun week.
    If monday is None, the current week's Monday is used.
    Missing days are returned as empty dicts so callers always get exactly 7 entries.
    """
    if monday is None:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
    week = {}
    for i in range(7):
        day = monday + timedelta(days=i)
        key = day.isoformat()
        week[key] = data.get(key, {})
    return week


def get_last_week_monday() -> date:
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


# ---------------------------------------------------------------------------
# State (per-day flags + last-summary timestamps)
# ---------------------------------------------------------------------------
#
# Structure:
# {
#   "today": "YYYY-MM-DD",
#   "warned":         {"ExeName.exe": true, ...},
#   "kill_notified":  {"ExeName.exe": true, ...},
#   "last_daily_summary": "YYYY-MM-DD",
#   "last_weekly_summary_monday": "YYYY-MM-DD"
# }

def _empty_state() -> dict:
    return {
        "today": get_today_key(),
        "warned": {},
        "kill_notified": {},
        "firewall_blocked": {},
        "exe_paths": {},
        "last_daily_summary": "",
        "last_weekly_summary_monday": "",
    }


def load_state() -> dict:
    _ensure_data_dir()
    if not STATE_FILE.exists():
        return _empty_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    state = _empty_state()
    state.update(loaded)
    return state


def save_state(state: dict) -> None:
    _atomic_write_json(STATE_FILE, state)


def reset_day_flags(state: dict) -> None:
    """Clear per-day flags and update the 'today' marker. Modifies state in-place.
    Note: caller is responsible for unblocking firewall rules before calling this,
    since the cleared 'firewall_blocked' map is the source of truth for what's blocked.
    """
    state["today"] = get_today_key()
    state["warned"] = {}
    state["kill_notified"] = {}
    state["firewall_blocked"] = {}
