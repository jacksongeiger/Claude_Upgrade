"""Measurement: did a suggestion actually change what happened?

Two signals, deliberately unequal in weight.

**Primary — install within the window.** `rdx install <slug>` run within 30
minutes of an injection naming that slug, in the same session. Unambiguous,
works for every resource type, and needs no correlation heuristics. This is the
number that decides whether the project earned its place.

**Secondary — tool usage via PostToolUse.** An MCP server namespaces its tools
(`mcp__github__*`), so observed tool calls can be attributed back to a resource
through `installed_resource.tool_prefix`. Honest caveat, stated because it
shapes how much the number is worth: this only works for MCPs, and only after a
restart picks the server up. It is the weakest of the requirements, which is
why it ships last and stays optional.

The PostToolUse hook appends JSONL to a spool rather than writing to SQLite.
That path fires hundreds of times per session; spawning sqlite3 each time would
add latency to every tool call and contend with the ingest write lock for no
benefit. `rdx stats` drains the spool.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import config

ACCEPT_WINDOW_MINUTES = 30


@dataclass
class DrainReport:
    lines_read: int = 0
    events_stored: int = 0
    attributed: int = 0
    malformed: int = 0


def drain_spool(conn: sqlite3.Connection, spool: Path | None = None) -> DrainReport:
    """Move PostToolUse events from the JSONL spool into the database.

    Truncates only after a successful commit, so a crash mid-drain loses
    nothing: the worst case is replaying events, and the insert is idempotent
    enough that duplicates only inflate a usage count.
    """
    spool = Path(spool or config.SPOOL_PATH)
    report = DrainReport()
    if not spool.exists():
        return report

    try:
        raw = spool.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return report

    prefixes = {
        row["tool_prefix"]: row["resource_id"]
        for row in conn.execute(
            "SELECT resource_id, tool_prefix FROM installed_resource "
            "WHERE tool_prefix IS NOT NULL AND removed_at IS NULL")
    }

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        report.lines_read += 1
        try:
            event = json.loads(line)
        except ValueError:
            report.malformed += 1
            continue

        tool_name = str(event.get("tool_name") or "")
        if not tool_name:
            report.malformed += 1
            continue

        resource_id = None
        for prefix, rid in prefixes.items():
            if tool_name.startswith(prefix):
                resource_id = rid
                break

        conn.execute(
            "INSERT INTO tool_event (ts, session_id, tool_name, matched_resource_id) "
            "VALUES (?,?,?,?)",
            (str(event.get("ts") or ""), event.get("session_id"), tool_name,
             resource_id),
        )
        report.events_stored += 1
        report.attributed += int(resource_id is not None)

    conn.commit()
    try:
        spool.write_text("", encoding="utf-8")
    except OSError:
        pass
    return report


def accept_rate(conn: sqlite3.Connection) -> dict[str, float]:
    """Injections that led to an rdx install of a suggested slug, in window."""
    shown = conn.execute(
        "SELECT COUNT(*) FROM injection WHERE n_shown > 0").fetchone()[0]
    if not shown:
        return {"shown": 0, "accepted": 0, "rate": 0.0}

    accepted = conn.execute(
        """
        SELECT COUNT(DISTINCT i.id) FROM injection i
        WHERE i.n_shown > 0 AND EXISTS (
          SELECT 1 FROM injection_item ii
          JOIN installed_resource ir ON ir.resource_id = ii.resource_id
          WHERE ii.injection_id = i.id
            AND ir.installed_by = 'rdx'
            -- Both sides go through datetime(): SQLite normalizes to a
            -- space separator, while rdx stores ISO 'T'. Comparing the raw
            -- strings silently never matches, which would have pinned the
            -- accept rate at 0% forever and made the project look dead.
            AND datetime(ir.installed_at) >= datetime(i.ts)
            AND datetime(ir.installed_at) <= datetime(i.ts, ?)
        )
        """,
        (f"+{ACCEPT_WINDOW_MINUTES} minutes",),
    ).fetchone()[0]

    return {"shown": shown, "accepted": accepted,
            "rate": round(accepted / shown, 4)}


@dataclass
class DriftReport:
    resource_id: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def tool_drift(conn: sqlite3.Connection) -> list[DriftReport]:
    """Compare observed MCP tool names against the install-time snapshot.

    A server that quietly grows a new tool after you installed it is the
    supply-chain case worth noticing — Anthropic documents no pinning or change
    detection here, so this is genuinely additive rather than duplicated.
    """
    out: list[DriftReport] = []
    for row in conn.execute(
            "SELECT resource_id, tool_prefix, tool_names FROM installed_resource "
            "WHERE tool_prefix IS NOT NULL AND tool_names IS NOT NULL "
            "AND removed_at IS NULL"):
        try:
            snapshot = set(json.loads(row["tool_names"] or "[]"))
        except ValueError:
            continue

        observed = {
            r["tool_name"] for r in conn.execute(
                "SELECT DISTINCT tool_name FROM tool_event WHERE tool_name LIKE ?",
                (row["tool_prefix"] + "%",))
        }
        if not observed:
            continue

        report = DriftReport(
            resource_id=row["resource_id"],
            added=sorted(observed - snapshot),
            removed=sorted(snapshot - observed),
        )
        if report.added:  # only additions are a security signal
            out.append(report)
    return out


def top_unaccepted(conn: sqlite3.Connection, limit: int = 10) -> list[tuple[str, int]]:
    """Resources shown repeatedly and never installed — the snooze candidates."""
    return [
        (r["slug"], r["n"]) for r in conn.execute(
            """
            SELECT res.slug AS slug, COUNT(*) AS n
            FROM injection_item ii
            JOIN injection i ON i.id = ii.injection_id
            JOIN resource res ON res.id = ii.resource_id
            WHERE i.n_shown > 0 AND NOT EXISTS (
              SELECT 1 FROM installed_resource ir
              WHERE ir.resource_id = ii.resource_id AND ir.installed_by = 'rdx')
            GROUP BY res.slug ORDER BY n DESC LIMIT ?
            """, (limit,))
    ]
