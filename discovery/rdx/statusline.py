"""Statusline renderer - the ambient half of the UI.

`statusLine` is Terminal-CLI-only (not VS Code, not the desktop app), and no
client lets a plugin render custom UI. So this one line is the entire
always-visible surface, and `rdx stats` / `rdx audit` are the on-demand detail
layer in the same terminal. There is no second place to look.

Reads the statusline payload JSON on stdin, writes one line to stdout. Like the
hook, it must never fail loudly: a broken statusline would show an error string
on every turn.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import sys

DIM = "\033[2m"
RESET = "\033[0m"
AMBER = "\033[33m"
GREEN = "\033[32m"


def _age(timestamp: str | None, now: _dt.datetime) -> str:
    if not timestamp:
        return "never"
    try:
        then = _dt.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return "?"
    delta = now - then
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)}m"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours // 24)}d"


def render(conn: sqlite3.Connection, *, now: _dt.datetime | None = None,
           color: bool = True) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)

    counts = conn.execute(
        "SELECT COUNT(*) FILTER (WHERE eligible = 1) AS eligible, "
        "       COUNT(*) FILTER (WHERE status = 'quarantined') AS quarantined "
        "FROM resource").fetchone()

    today = now.strftime("%Y-%m-%d")
    shown = conn.execute(
        "SELECT COUNT(*) FROM injection WHERE n_shown > 0 AND ts >= ?",
        (today,)).fetchone()[0]

    shadow = conn.execute(
        "SELECT COUNT(*) FROM injection WHERE shadow = 1 AND ts >= ?",
        (today,)).fetchone()[0]

    last_run = conn.execute(
        "SELECT MAX(last_run_at) FROM funnel_state").fetchone()[0]

    parts = [f"rdx {counts['eligible']:,} idx"]

    if shadow and not shown:
        parts.append(f"{shadow} shadow")
    else:
        parts.append(f"{shown} hint{'' if shown == 1 else 's'} today")

    age = _age(last_run, now)
    stale = age.endswith("d") and age != "1d"
    parts.append(f"crawl {age}")

    if counts["quarantined"]:
        flag = f"{counts['quarantined']} quarantined"
        parts.append(f"{AMBER}{flag}{RESET}" if color else flag)

    line = " · ".join(parts)
    if not color:
        return line
    return f"{AMBER if stale else DIM}{line}{RESET}" if stale else f"{DIM}{line}{RESET}"


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdin.read()  # payload is consumed but not currently needed
    except Exception:
        pass
    try:
        from . import config, db

        if not config.DB_PATH.exists():
            return 0
        conn = db.open_db(config.DB_PATH, readonly=True, busy_timeout_ms=50)
        try:
            print(render(conn))
        finally:
            conn.close()
    except Exception:
        # A statusline that prints a traceback every turn is worse than one
        # that prints nothing.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
