"""
Windows Firewall outbound block rules per game exe.

Adding rules requires admin. The Task Scheduler entry runs at HIGHEST
privilege so this works in production. When run unprivileged (e.g. manual
`python main.py` from a normal terminal), is_admin() returns False and the
tracker skips the firewall feature entirely so we don't trigger a UAC
prompt every time we'd otherwise call netsh.
"""
import ctypes
import logging
import subprocess

RULE_PREFIX = "GameTimeLimiter_"


def is_admin() -> bool:
    """True if the current process is running with admin rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _rule_name(exe_name: str) -> str:
    return f"{RULE_PREFIX}{exe_name}"


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    return True, proc.stdout.strip()


def block_outbound(exe_name: str, exe_path: str) -> bool:
    """Add an outbound block rule for the given exe path. Idempotent (adds a duplicate is OK)."""
    if not exe_path:
        return False
    if not is_admin():
        logging.warning("Skipping firewall block for %s: not running as admin.", exe_name)
        return False
    ok, msg = _run([
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={_rule_name(exe_name)}",
        "dir=out", "action=block",
        f"program={exe_path}",
        "enable=yes",
    ])
    if ok:
        logging.info("Firewall: blocked outbound for %s (%s)", exe_name, exe_path)
    else:
        logging.warning("Firewall block failed for %s: %s", exe_name, msg)
    return ok


def unblock_outbound(exe_name: str) -> bool:
    """Remove all rules with our naming convention for this exe. Idempotent."""
    if not is_admin():
        return False
    ok, msg = _run([
        "netsh", "advfirewall", "firewall", "delete", "rule",
        f"name={_rule_name(exe_name)}",
    ])
    if ok:
        logging.info("Firewall: unblocked %s", exe_name)
    else:
        # netsh returns non-zero when no matching rule exists; that's fine.
        logging.debug("Firewall unblock for %s: %s", exe_name, msg)
    return ok


def unblock_all(exe_names) -> None:
    if not is_admin():
        return
    for exe in exe_names:
        unblock_outbound(exe)
