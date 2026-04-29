# Game Time Limiter

Silently tracks daily playtime for **Rocket League** and **Rainbow Six Siege** on Windows 10/11, enforces a 2-hour daily limit per game, and sends Windows toast notifications for warnings and daily/weekly summaries.

## What it does

- Polls the running process list every 5 seconds and accumulates time when a tracked game is running.
- **Warns** 5 minutes before the daily limit.
- **Kills the game** when the limit is reached.
- **Refuses to start** a game that has less than `min_match_minutes` of time left for the day.
- **Daily summary** toast at 22:00 showing time played per game.
- **Weekly summary** toast every Sunday at 20:00 covering Mon–Sun totals, averages, and limit-hit days.
- All times reset at local midnight; 7 days of history are retained for the weekly summary.

## Requirements

- Windows 10 (build 18362+) or Windows 11
- Python 3.11 or newer
- Administrator access (only required once, for the Task Scheduler setup)

## Install

```powershell
git clone <repo-url>
cd windows-screen-time

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Configure

Edit `config.json`. Defaults:

```json
{
  "games": {
    "RocketLeague.exe":  { "display_name": "Rocket League",     "daily_limit_minutes": 120, "min_match_minutes": 7  },
    "RainbowSix.exe":    { "display_name": "Rainbow Six Siege", "daily_limit_minutes": 120, "min_match_minutes": 10 }
  },
  "warning_minutes": 5,
  "daily_summary_time": "22:00",
  "weekly_summary_time": "20:00",
  "poll_interval_seconds": 5,
  "log_file": "game_limiter.log"
}
```

The exe name must match the process name shown in Task Manager exactly (case-insensitive).

## Run manually (foreground, for testing)

```powershell
python main.py
```

A log file (`game_limiter.log`) is created in the project directory. `Ctrl+C` to stop.

## Install as a startup task (recommended)

Run **once** from an Administrator PowerShell or CMD:

```powershell
python setup_startup.py
```

This registers a Task Scheduler entry `GameTimeLimiter` that:
- Triggers `ONLOGON` (when you sign in)
- Runs as your user with `HIGHEST` privilege (needed to kill game processes)
- Uses `pythonw.exe` (no console window)

Verify / start without rebooting:

```powershell
schtasks /Query /TN "GameTimeLimiter" /FO LIST
schtasks /Run   /TN "GameTimeLimiter"
```

Remove:

```powershell
schtasks /Delete /TN "GameTimeLimiter" /F
```

## File layout

```
main.py             Entry point: poll loop + scheduler
tracker.py          Process monitoring, time accumulation, kill logic
notifications.py    Windows toast notifications (warnings + summaries)
storage.py          JSON persistence with 7-day rolling history
config.py           Loads and validates config.json
config.json         User-editable settings
setup_startup.py    One-time Task Scheduler registration (run as Admin)
data/playtime.json  Auto-created; per-day totals (gitignored)
game_limiter.log    Auto-created; rotating log (gitignored)
```

## Tail the log

```powershell
Get-Content .\game_limiter.log -Wait -Tail 50
```

## Known limitations

- **Sleep inflation:** if the PC sleeps mid-session, up to ~10 seconds of phantom playtime can be recorded per sleep/wake cycle (capped by design).
- **Single-instance only:** running `main.py` twice will double-count playtime. The Task Scheduler entry handles this; don't also run it manually.
- **Per-user data:** `data/playtime.json` is local to the project folder. For multi-user households, give each user their own copy.
- **Launcher idle time:** if a game's exe stays running while idle in a launcher, that idle time still counts.

## Uninstall

1. `schtasks /Delete /TN "GameTimeLimiter" /F`
2. Delete the project folder.
