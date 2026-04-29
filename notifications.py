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


def _send_toast(lines: list[str]) -> None:
    """
    Fire a Windows toast notification.
    lines[0] = title (bold)
    lines[1:] = body text lines
    Failures are logged but never raise exceptions into the caller.
    """
    if not lines:
        return
    try:
        toast = Toast()
        toast.text_fields = lines
        _get_toaster().show_toast(toast)
    except Exception as exc:
        logging.error("Toast notification failed: %s", exc)


def notify_warning(display_name: str, minutes_left: int) -> None:
    """Fires when warning_minutes remain for a running game."""
    _send_toast([
        "⚠️ Game Time Warning",
        f"{display_name}: {minutes_left} minute{'s' if minutes_left != 1 else ''} remaining today.",
        "Finish up - your session will end automatically.",
    ])


def notify_killed_time_up(display_name: str) -> None:
    """Fires when a game is killed because the daily limit was reached."""
    _send_toast([
        "\U0001f6d1 Daily Limit Reached",
        f"{display_name} has been closed.",
        "You've used your full 2-hour daily allowance.",
    ])


def notify_killed_no_match_time(
    display_name: str, min_match_minutes: int, minutes_left: int
) -> None:
    """Fires when a game is killed on launch because there isn't time for a full match."""
    _send_toast([
        "\U0001f6d1 Not Enough Time to Play",
        f"{display_name} has been closed.",
        f"Only {minutes_left}m remaining today - minimum session is {min_match_minutes}m.",
    ])


def _fmt(total_seconds: int) -> str:
    """Format seconds as a human-readable duration string."""
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def notify_daily_summary(games_config: dict, data: dict, day_key: str) -> None:
    """Send a single multi-line toast summarising today's playtime for all tracked games."""
    day_data = data.get(day_key, {})
    lines = ["\U0001f4ca Daily Game Summary"]

    for exe, cfg in games_config.items():
        played_s = day_data.get(exe, 0)
        limit_s = cfg["daily_limit_minutes"] * 60
        played_fmt = _fmt(played_s)
        limit_fmt = _fmt(limit_s)
        line = f"{cfg['display_name']}: {played_fmt} / {limit_fmt}"
        if played_s >= limit_s:
            line += "  ✅ Limit hit"
        lines.append(line)

    _send_toast(lines)


def notify_weekly_summary(games_config: dict, week_data: dict) -> None:
    """Send a single multi-line toast summarising the Mon-Sun week for all tracked games."""
    dates = sorted(week_data.keys())
    if dates:
        start = date.fromisoformat(dates[0])
        end = date.fromisoformat(dates[-1])
        range_label = f"  ({start.strftime('%a %#d %b')} – {end.strftime('%a %#d %b')})"
    else:
        range_label = ""

    lines = [f"\U0001f4c5 Weekly Game Summary{range_label}"]

    for exe, cfg in games_config.items():
        limit_s = cfg["daily_limit_minutes"] * 60
        total_s = sum(day.get(exe, 0) for day in week_data.values())
        avg_s = total_s // 7
        days_hit = sum(1 for day in week_data.values() if day.get(exe, 0) >= limit_s)

        lines.append(
            f"{cfg['display_name']}: {_fmt(total_s)} total, {_fmt(avg_s)} avg/day"
        )
        lines.append(f"  Limit hit: {days_hit}/7 days")

    _send_toast(lines)
