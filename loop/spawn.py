#!/usr/bin/env python3
"""spawn.py — portable `setsid` + `timeout` for the drivers.

  spawn.py --pgid-file FILE [--timeout-min N] -- CMD [ARGS...]

Starts CMD in its own session (its pid is its pgid, so a driver can kill the
whole tree with `kill -- -PGID`), writes that pid to FILE, inherits stdio, and
exits with CMD's code (128+n when a signal killed it). On timeout the group
gets TERM, then KILL after 2s, and the exit code is 124 like GNU timeout(1).
macOS has neither setsid(1) nor timeout(1); Linux has both. This works on either.
"""
import argparse
import os
import signal
import subprocess
import sys

TERM_GRACE_S = 2


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):  # gone, or only zombies left (macOS says EPERM)
        pass


def kill_group(proc: subprocess.Popen) -> None:
    _killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=TERM_GRACE_S)
    except subprocess.TimeoutExpired:
        pass
    _killpg(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=TERM_GRACE_S)
    except subprocess.TimeoutExpired:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pgid-file", required=True)
    ap.add_argument("--timeout-min", type=float, default=0.0)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("spawn.py: no command given", file=sys.stderr)
        return 1

    proc = subprocess.Popen(cmd, start_new_session=True)
    with open(a.pgid_file, "w") as f:
        f.write(str(proc.pid))

    def relay(signum, _frame):  # the driver's trap reaches us: pass it down, then leave the same way
        kill_group(proc)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, relay)
    signal.signal(signal.SIGINT, relay)

    timeout = a.timeout_min * 60 if a.timeout_min > 0 else None
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        return 124
    return 128 - rc if rc < 0 else rc


if __name__ == "__main__":
    sys.exit(main())
