"""Keeping the index fresh, on a schedule the user does not have to remember.

The README's diagram has always said "NIGHTLY (offline)" and nothing ever
scheduled it. An index that silently ages is worse than an obviously empty one:
the gate keeps firing, the suggestions keep looking plausible, and they slowly
stop reflecting what exists. `rdx status` reports staleness for this reason,
but reporting is not fixing.

macOS gets launchd, which is correct for a user-level periodic job: it runs on
the next wake if the machine was asleep at the scheduled time, where a cron
entry would simply be missed. Linux gets crontab. Anything else is told what to
run so it can be wired up by hand rather than failing silently.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

LABEL = "com.claude-upgrade.rdx.sync"
CRON_MARKER = "# rdx nightly sync"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _rdx_command() -> list[str]:
    """The exact command a scheduler should run.

    Resolved to the venv interpreter, never the bare name: a launchd job runs
    with a minimal PATH that will not contain ~/.local/bin, so `rdx` would not
    be found and the job would fail once a night, forever, in a log nobody
    reads.
    """
    venv_python = config.PROJECT_DIR / "venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    return [python, "-m", "rdx.cli", "sync"]


def _plist_body(hour: int, minute: int) -> str:
    cmd = _rdx_command()
    args = "".join(f"        <string>{c}</string>\n" for c in cmd)
    log = config.STATE_DIR / "sync.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}    </array>
    <key>WorkingDirectory</key><string>{config.PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict><key>PYTHONPATH</key><string>{config.PROJECT_DIR}</string></dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{hour}</integer>
        <key>Minute</key><integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key><string>{log}</string>
    <key>StandardErrorPath</key><string>{log}</string>
    <key>RunAtLoad</key><false/>
</dict>
</plist>
"""


def install(hour: int = 3, minute: int = 30) -> int:
    system = platform.system()
    if system == "Darwin":
        return _install_launchd(hour, minute)
    if system == "Linux" and shutil.which("crontab"):
        return _install_cron(hour, minute)
    print(f"No scheduler wired up for {system}. Run this nightly yourself:")
    print("   ", " ".join(_rdx_command()))
    return 1


def _install_launchd(hour: int, minute: int) -> int:
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_plist_body(hour, minute), encoding="utf-8")

    # bootout first so re-running is idempotent rather than an error.
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"],
                   capture_output=True)
    proc = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"✗ launchctl bootstrap failed: {proc.stderr.strip()}")
        print(f"  The plist is written at {path}; load it manually with:")
        print(f"    launchctl bootstrap gui/{uid} {path}")
        return 1

    print(f"✓ nightly sync scheduled at {hour:02d}:{minute:02d} (launchd)")
    print(f"  plist : {path}")
    print(f"  log   : {config.STATE_DIR / 'sync.log'}")
    print("  Runs on next wake if the machine was asleep. Remove with "
          "`rdx unschedule`.")
    return 0


def _crontab_lines() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return proc.stdout.splitlines() if proc.returncode == 0 else []


def _write_crontab(lines: list[str]) -> bool:
    proc = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                          text=True, capture_output=True)
    return proc.returncode == 0


def _install_cron(hour: int, minute: int) -> int:
    kept = [ln for ln in _crontab_lines() if CRON_MARKER not in ln]
    cmd = " ".join(_rdx_command())
    log = config.STATE_DIR / "sync.log"
    kept.append(f"{minute} {hour} * * * cd {config.PROJECT_DIR} && "
                f"PYTHONPATH={config.PROJECT_DIR} {cmd} >> {log} 2>&1 "
                f"{CRON_MARKER}")
    if not _write_crontab(kept):
        print("✗ could not write crontab")
        return 1
    print(f"✓ nightly sync scheduled at {hour:02d}:{minute:02d} (cron)")
    print(f"  log: {log}")
    return 0


def uninstall() -> int:
    system = platform.system()
    removed = False

    if system == "Darwin":
        path = _plist_path()
        if path.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                           capture_output=True)
            path.unlink()
            removed = True
    elif shutil.which("crontab"):
        lines = _crontab_lines()
        kept = [ln for ln in lines if CRON_MARKER not in ln]
        if len(kept) != len(lines):
            _write_crontab(kept)
            removed = True

    print("✓ nightly sync removed" if removed else "nothing was scheduled")
    return 0
