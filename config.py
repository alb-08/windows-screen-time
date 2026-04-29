import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / "config.json"


def load_config() -> dict:
    """Load and return the configuration. Exits with an error if config is missing or invalid."""
    if not CONFIG_FILE.exists():
        print(f"ERROR: config.json not found at {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: config.json is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    required_keys = ["games", "warning_minutes", "daily_summary_time",
                     "weekly_summary_time", "poll_interval_seconds"]
    for key in required_keys:
        if key not in cfg:
            print(f"ERROR: config.json is missing required key: '{key}'", file=sys.stderr)
            sys.exit(1)
    for exe, game_cfg in cfg["games"].items():
        for gk in ["display_name", "daily_limit_minutes", "min_match_minutes"]:
            if gk not in game_cfg:
                print(f"ERROR: Game '{exe}' is missing key '{gk}' in config.json", file=sys.stderr)
                sys.exit(1)
    return cfg
