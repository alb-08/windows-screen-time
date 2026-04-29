# Game Time Limiter

Silently tracks daily playtime for **Rocket League** and **Rainbow Six Siege** on Windows 10/11, enforces per-game (or combined) daily limits, sends toast notifications, and exposes a tray icon with live usage and a settings UI.

## What it does

- Polls the running process list every 5 seconds and accumulates time when a tracked game is running.
- **Warns** 5 minutes before the daily limit.
- **Kills the game** when the limit is reached.
- **Refuses to start** a game that has less than `min_match_minutes` of time left for the day.
- **Daily summary** toast at 22:00 showing time played per game (and combined pool, if enabled).
- **Weekly summary** toast every **Monday at 09:00** covering the previous Mon–Sun (so Sunday-evening play is included).
- **Missed-summary catch-up:** if the PC was off / asleep when a summary was due, it fires on next start.
- **Atomic writes** to `data/playtime.json` and `data/state.json` (no truncation if killed mid-write).
- **Persistent flags** so warnings/kill notifications fire only once per day even across restarts.
- All times reset at local midnight; 7 days of history are retained for the weekly summary.
- **Tray icon** with a tooltip showing live remaining time and a menu for status / settings / usage editor / restart / quit.

## Requirements

- Windows 10 (build 18362+) or Windows 11
- Python 3.11 or newer
- Administrator access (only required once, for the Task Scheduler setup)

## Install

```powershell
git clone https://github.com/alb-08/windows-screen-time.git
cd windows-screen-time

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Configure

Edit `config.json` (or use the **Settings…** window from the tray menu). Defaults:

```json
{
  "games": {
    "RocketLeague.exe":  { "display_name": "Rocket League",     "daily_limit_minutes": 120, "min_match_minutes": 7  },
    "RainbowSix.exe":    { "display_name": "Rainbow Six Siege", "daily_limit_minutes": 120, "min_match_minutes": 10 }
  },
  "shared_pool_minutes": null,
  "warning_minutes": 5,
  "daily_summary_time": "22:00",
  "weekly_summary_time": "09:00",
  "poll_interval_seconds": 5,
  "log_file": "game_limiter.log"
}
```

- **`shared_pool_minutes`**: set to a number (e.g. `120`) to enforce a single combined daily allowance across all tracked games. Per-game `daily_limit_minutes` still applies as a per-game cap.
- **Exe names** must match the process name shown in Task Manager exactly (case-insensitive).
- **`weekly_summary_time`** is the time on **Monday** at which the previous week's summary is sent.

## Run manually (foreground, for testing)

```powershell
python main.py
```

A log file (`game_limiter.log`) is created in the project directory. `Ctrl+C` to stop. The tray icon will appear in the notification area.

## Install as a startup task (recommended)

Run **once** from an Administrator PowerShell or CMD:

```powershell
python setup_startup.py
```

This registers a Task Scheduler entry `GameTimeLimiter` that:
- Triggers `ONLOGON` (when you sign in)
- Runs as your user (uses `whoami` output, so domain / Microsoft accounts work) with `HIGHEST` privilege
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

## Tray icon

The tray icon ("GT") sits in the Windows notification area and never appears in Alt-Tab. Hover for a live remaining-time tooltip. Right-click for:

- **Show today's usage** (default left-click) — progress bars per game and combined pool.
- **Edit today's usage…** — override or reset today's totals (also clears the warned/killed flags so warnings/kills can fire again if you reset to zero).
- **Settings…** — change limits, warning time, summary times, and toggle the shared pool. Saved to `config.json`. Most fields apply immediately; summary times take effect on the next launch (use **Restart**).
- **Open log** — opens `game_limiter.log` in your default text editor.
- **Restart** / **Quit**.

## File layout

```
main.py             Entry point: poll loop + scheduler + UI launcher
tracker.py          Process monitoring, time accumulation, kill logic, shared-pool math
notifications.py    Windows toast notifications (warnings + summaries)
storage.py          Atomic JSON persistence (playtime.json + state.json), 7-day rolling history
config.py           Loads / validates / saves config.json
config.json         User-editable settings (also editable via UI)
ui.py               Tray icon + Tk windows (status / settings / usage editor)
setup_startup.py    One-time Task Scheduler registration (run as Admin)
data/playtime.json  Auto-created; per-day totals (gitignored)
data/state.json     Auto-created; per-day flags + summary timestamps (gitignored)
game_limiter.log    Auto-created (gitignored)
```

## Tail the log

```powershell
Get-Content .\game_limiter.log -Wait -Tail 50
```

## Known limitations

- **Sleep inflation:** if the PC sleeps mid-session, up to ~10 seconds of phantom playtime can be recorded per sleep/wake cycle (capped by design).
- **Sub-poll sessions:** a game opened and closed entirely between two 5-second polls is not counted (inherent to polling).
- **Single-instance only:** running `main.py` twice will double-count playtime. The Task Scheduler entry handles this; don't also run it manually.
- **Per-user data:** `data/playtime.json` is local to the project folder. For multi-user households, give each user their own copy.
- **Launcher idle time:** if a game's exe stays running while idle in a launcher, that idle time still counts.

## Uninstall

1. `schtasks /Delete /TN "GameTimeLimiter" /F`
2. Delete the project folder.
