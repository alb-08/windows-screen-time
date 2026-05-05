import logging
from datetime import date

from windows_toasts import WindowsToaster, Toast

APP_ID = "GameTimeLimiter"
_toaster: WindowsToaster | None = None


def _get_toaster() -> WindowsToaster:
    global _toaster
    if _toaster is None:
        _toaster = WindowsToaster(APP_ID)
    return _toaster


def _send_toast(lines: list[str]) -> bool:
    """
    Fire a Windows toast notification.
    lines[0] = title (bold)
    lines[1:] = body text lines
    Returns True on success, False on failure (failures are also logged).
    """
    if not lines:
        return False
    try:
        toast = Toast()
        toast.text_fields = lines
        _get_toaster().show_toast(toast)
        return True
    except Exception as exc:
        logging.error("Toast notification failed: %s", exc)
        return False


def _fmt(total_seconds: int) -> str:
    """Format seconds as a human-readable duration string."""
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def notify_warning(display_name: str, minutes_left: int) -> bool:
    return _send_toast([
        "Game Time Warning",
        f"{display_name}: {minutes_left} minute{'s' if minutes_left != 1 else ''} remaining today.",
        "Finish up - your session will end automatically.",
    ])


def notify_killed_time_up(display_name: str) -> bool:
    return _send_toast([
        "Daily Limit Reached",
        f"{display_name} has been closed.",
        "You've used your full daily allowance.",
    ])


def notify_killed_no_match_time(display_name: str, min_match_minutes: int, minutes_left: int) -> bool:
    return _send_toast([
        "Not Enough Time to Play",
        f"{display_name} has been closed.",
        f"Only {minutes_left}m left today (minimum session is {min_match_minutes}m).",
    ])


def notify_shared_pool_killed(display_name: str) -> bool:
    return _send_toast([
        "Daily Limit Reached",
        f"{display_name} closed - combined daily pool used up.",
        "All tracked games are blocked until tomorrow.",
    ])


def notify_daily_summary(apps_config: dict, data: dict, day_key: str,
                         shared_pool_minutes: int | None = None) -> bool:
    """
    Single multi-line toast summarising a day's playtime.
    Stays within 3 body lines (or 4 with shared pool) to fit the toast template.
    """
    day_data = data.get(day_key, {})
    lines = ["Daily Summary"]

    combined_s = 0
    for exe, cfg in apps_config.items():
        played_s = day_data.get(exe, 0)
        combined_s += played_s
        limit_s = cfg["daily_limit_minutes"] * 60
        line = f"{cfg['display_name']}: {_fmt(played_s)} / {_fmt(limit_s)}"
        if played_s >= limit_s:
            line += "  (limit hit)"
        lines.append(line)

    if shared_pool_minutes:
        pool_s = shared_pool_minutes * 60
        line = f"Combined: {_fmt(combined_s)} / {_fmt(pool_s)}"
        if combined_s >= pool_s:
            line += "  (pool used)"
        lines.append(line)

    return _send_toast(lines)


def notify_weekly_summary(apps_config: dict, week_data: dict) -> bool:
    """
    Single multi-line toast for a Mon-Sun week.
    One body line per app (3 lines total for two apps) to fit the toast template.
    """
    dates = sorted(week_data.keys())
    if dates:
        start = date.fromisoformat(dates[0])
        end = date.fromisoformat(dates[-1])
        range_label = f"  ({start.strftime('%a %#d %b')} - {end.strftime('%a %#d %b')})"
    else:
        range_label = ""

    lines = [f"Weekly Summary{range_label}"]

    for exe, cfg in apps_config.items():
        limit_s = cfg["daily_limit_minutes"] * 60
        total_s = sum(day.get(exe, 0) for day in week_data.values())
        avg_s = total_s // 7
        days_hit = sum(1 for day in week_data.values() if day.get(exe, 0) >= limit_s)
        lines.append(
            f"{cfg['display_name']}: {_fmt(total_s)}, {_fmt(avg_s)} avg/day, {days_hit}/7 days hit"
        )

    return _send_toast(lines)
