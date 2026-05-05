"""
One-time setup: registers Game Time Limiter with Windows Task Scheduler.
Run this script as Administrator:
    Right-click PowerShell or CMD -> "Run as administrator"
    python setup_startup.py
"""
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    script_dir = Path(__file__).parent.resolve()
    python_exe = Path(sys.executable)

    pythonw = python_exe.parent / "pythonw.exe"
    if not pythonw.exists():
        pythonw = python_exe
        print("Warning: pythonw.exe not found; using python.exe (console window will appear).")

    main_script = script_dir / "main.py"
    task_name = "GameTimeLimiter"

    # Prefer `whoami` output (DOMAIN\user or AzureAD\user) over bare USERNAME,
    # which doesn't work for domain-joined or Microsoft-account machines.
    try:
        username = subprocess.check_output(["whoami"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        username = os.environ.get("USERNAME", "")
    if not username:
        print("ERROR: could not determine current user (whoami / %USERNAME% both empty).")
        sys.exit(1)

    cmd = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", f'"{pythonw}" "{main_script}"',
        "/SC", "ONLOGON",
        "/RU", username,
        "/RL", "HIGHEST",
        "/IT",
        "/F",
    ]

    print(f"Registering Task Scheduler task '{task_name}'...")
    print(f"  Script : {main_script}")
    print(f"  Runtime: {pythonw}")
    print(f"  User   : {username}")
    print()

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"SUCCESS: Task '{task_name}' registered.")
        print("The Game Time Limiter will start automatically the next time you log in.")
        print()
        print("To start it now without rebooting, run:")
        print(f'  schtasks /Run /TN "{task_name}"')
    else:
        print(f"FAILED to register task.")
        print(f"  stdout: {result.stdout.strip()}")
        print(f"  stderr: {result.stderr.strip()}")
        print()
        print("Troubleshooting:")
        print("  1. Ensure you are running this script as Administrator.")
        print("  2. Check that schtasks.exe is on your PATH.")
        print("  3. Try removing the existing task first:")
        print(f'       schtasks /Delete /TN "{task_name}" /F')
        sys.exit(1)


if __name__ == "__main__":
    main()
