"""Schema, migrations and every SQL query in the system.

Two invariants this module is responsible for:
  1. A row with status != 'active' or eligible = 0 can never be returned by
     `candidates()`. The FTS triggers are unconditional (so the index stays
     consistent when those columns flip); the filter lives in exactly one
     query, and test_db.py proves it.
  2. Raw prompts are never stored. `injection.prompt_sha` is a digest, because
     otherwise this table becomes a permanent local log of everything the user
     has ever typed into Claude.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config
from .models import Resource, ResourceDraft

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource (
  id              TEXT PRIMARY KEY,
  type            TEXT NOT NULL CHECK (type IN ('mcp','plugin','skill','library')),
  name            TEXT NOT NULL,
  slug            TEXT NOT NULL,
  canon_key       TEXT,
  url             TEXT,
  summary         TEXT,
  tags            TEXT NOT NULL DEFAULT '[]',
  summary_src_sha TEXT,

  recipe_kind     TEXT CHECK (recipe_kind IS NULL OR recipe_kind IN
                    ('claude_plugin','claude_mcp_stdio','claude_mcp_http',
                     'pip','npm','enable_only','manual')),
  recipe_json     TEXT,
  pinned_version  TEXT,

  trust_tier      TEXT NOT NULL DEFAULT 'red'
                    CHECK (trust_tier IN ('green','yellow','red')),
  status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','deprecated','quarantined')),
  eligible        INTEGER NOT NULL DEFAULT 0,
  flags           TEXT NOT NULL DEFAULT '[]',
  blocking_flags  TEXT NOT NULL DEFAULT '[]',

  stars           INTEGER,
  pushed_at       TEXT,
  archived        INTEGER NOT NULL DEFAULT 0,
  install_count   INTEGER,
  quality_score   REAL NOT NULL DEFAULT 0.0,

  funnel          TEXT NOT NULL,
  source_ref      TEXT,
  first_seen      TEXT NOT NULL,
  last_seen       TEXT NOT NULL,
  last_verified   TEXT,
  UNIQUE (type, slug, funnel)
);

CREATE INDEX IF NOT EXISTS idx_resource_live
  ON resource(status, eligible, type, quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_resource_canon
  ON resource(canon_key) WHERE canon_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_resource_funnel
  ON resource(funnel, last_seen);

CREATE VIRTUAL TABLE IF NOT EXISTS resource_fts USING fts5(
  name, summary, tags, slug,
  content = 'resource', content_rowid = 'rowid',
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS resource_ai AFTER INSERT ON resource BEGIN
  INSERT INTO resource_fts(rowid, name, summary, tags, slug)
    VALUES (new.rowid, new.name, new.summary, new.tags, new.slug);
END;
CREATE TRIGGER IF NOT EXISTS resource_ad AFTER DELETE ON resource BEGIN
  INSERT INTO resource_fts(resource_fts, rowid, name, summary, tags, slug)
    VALUES ('delete', old.rowid, old.name, old.summary, old.tags, old.slug);
END;
CREATE TRIGGER IF NOT EXISTS resource_au AFTER UPDATE ON resource BEGIN
  INSERT INTO resource_fts(resource_fts, rowid, name, summary, tags, slug)
    VALUES ('delete', old.rowid, old.name, old.summary, old.tags, old.slug);
  INSERT INTO resource_fts(rowid, name, summary, tags, slug)
    VALUES (new.rowid, new.name, new.summary, new.tags, new.slug);
END;

CREATE TABLE IF NOT EXISTS injection (
  id                INTEGER PRIMARY KEY,
  ts                TEXT NOT NULL,
  session_id        TEXT,
  cwd               TEXT,
  prompt_sha        TEXT,
  prompt_len        INTEGER,
  query_terms       TEXT,
  n_candidates      INTEGER NOT NULL DEFAULT 0,
  n_shown           INTEGER NOT NULL DEFAULT 0,
  top_score         REAL,
  margin            REAL,
  suppressed_reason TEXT,
  shadow            INTEGER NOT NULL DEFAULT 0,
  latency_ms        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_injection_session ON injection(session_id, ts);

CREATE TABLE IF NOT EXISTS injection_item (
  injection_id INTEGER NOT NULL REFERENCES injection(id) ON DELETE CASCADE,
  resource_id  TEXT    NOT NULL,
  rank         INTEGER NOT NULL,
  score        REAL    NOT NULL,
  PRIMARY KEY (injection_id, rank)
);
CREATE INDEX IF NOT EXISTS idx_injection_item_res ON injection_item(resource_id);

CREATE TABLE IF NOT EXISTS tool_event (
  id                  INTEGER PRIMARY KEY,
  ts                  TEXT NOT NULL,
  session_id          TEXT,
  tool_name           TEXT NOT NULL,
  matched_resource_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_event_session ON tool_event(session_id, ts);

CREATE TABLE IF NOT EXISTS installed_resource (
  resource_id  TEXT PRIMARY KEY,
  scope        TEXT NOT NULL CHECK (scope IN ('local','project','user','unknown')),
  installed_at TEXT NOT NULL,
  installed_by TEXT NOT NULL CHECK (installed_by IN ('rdx','preexisting')),
  version      TEXT,
  tool_prefix  TEXT,
  tool_names   TEXT,
  last_seen_at TEXT,
  removed_at   TEXT
);

CREATE TABLE IF NOT EXISTS funnel_state (
  funnel        TEXT PRIMARY KEY,
  last_run_at   TEXT,
  last_cursor   TEXT,
  last_etag     TEXT,
  last_status   TEXT,
  last_error    TEXT,
  n_seen        INTEGER,
  n_upserted    INTEGER,
  n_quarantined INTEGER
);
"""


class Fts5Unavailable(RuntimeError):
    """Raised when the interpreter's SQLite lacks FTS5.

    macOS system Python is sometimes built without it; the fix is to build the
    venv on Homebrew python3. Failing loudly here beats failing mysteriously
    inside a hook that is supposed to be invisible.
    """


def check_fts5(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
    except sqlite3.OperationalError as exc:  # pragma: no cover - env dependent
        raise Fts5Unavailable(
            "This Python's SQLite was built without FTS5. Rebuild the venv on a "
            "python3 whose sqlite3 has FTS5 (on macOS: Homebrew python3)."
        ) from exc


def open_db(path: Path | None = None, *, readonly: bool = False,
            busy_timeout_ms: int = 5000) -> sqlite3.Connection:
    path = Path(path or config.DB_PATH)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | None = None) -> sqlite3.Connection:
    conn = open_db(path)
    check_fts5(conn)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(config.SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    return int(row["value"]) if row else 0


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest()


def upsert_resource(conn: sqlite3.Connection, draft: ResourceDraft, *,
                    now: str, flags: Sequence[str] = (),
                    blocking_flags: Sequence[str] = (),
                    summary_src_sha: str | None = None,
                    quality_score: float = 0.0) -> None:
    """Insert or update one sanitized resource.

    `first_seen` is preserved across updates; `last_seen` always advances.

    Slugs address resources on the CLI (`rdx install <slug>`), so they must be
    unique per (type, funnel). Upstream namespaces do collide even after
    disambiguation, so a genuine clash gets a short deterministic suffix
    derived from the id rather than failing the whole sync.
    """
    try:
        _upsert(conn, draft, now, flags, blocking_flags, summary_src_sha,
                quality_score)
        return
    except sqlite3.IntegrityError as exc:
        if "resource.slug" not in str(exc):
            raise
    suffix = hashlib.sha256(draft.id.encode("utf-8")).hexdigest()[:6]
    draft.slug = f"{draft.slug[:56]}-{suffix}"
    _upsert(conn, draft, now, flags, blocking_flags, summary_src_sha,
            quality_score)


def _upsert(conn: sqlite3.Connection, draft: ResourceDraft, now: str,
            flags: Sequence[str], blocking_flags: Sequence[str],
            summary_src_sha: str | None, quality_score: float) -> None:
    conn.execute(
        """
        INSERT INTO resource (
          id, type, name, slug, canon_key, url, summary, tags, summary_src_sha,
          recipe_kind, recipe_json, pinned_version, trust_tier, status, eligible,
          flags, blocking_flags, stars, pushed_at, archived, install_count,
          quality_score, funnel, source_ref, first_seen, last_seen, last_verified
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          slug = excluded.slug,
          canon_key = excluded.canon_key,
          url = excluded.url,
          summary = excluded.summary,
          tags = excluded.tags,
          summary_src_sha = excluded.summary_src_sha,
          recipe_kind = excluded.recipe_kind,
          recipe_json = excluded.recipe_json,
          pinned_version = excluded.pinned_version,
          trust_tier = excluded.trust_tier,
          status = excluded.status,
          flags = excluded.flags,
          blocking_flags = excluded.blocking_flags,
          stars = excluded.stars,
          pushed_at = excluded.pushed_at,
          archived = excluded.archived,
          install_count = excluded.install_count,
          quality_score = excluded.quality_score,
          source_ref = excluded.source_ref,
          last_seen = excluded.last_seen,
          last_verified = excluded.last_verified
        """,
        (
            draft.id, draft.type, draft.name, draft.slug, draft.canon_key,
            draft.url, draft.summary, json.dumps(draft.tags), summary_src_sha,
            draft.recipe_kind, draft.recipe_json, draft.pinned_version,
            draft.trust_tier, draft.status, json.dumps(list(flags)),
            json.dumps(list(blocking_flags)),
            draft.stars, draft.pushed_at, int(draft.archived),
            draft.install_count, quality_score, draft.funnel, draft.source_ref,
            now, now, now,
        ),
    )


def mark_stale_deprecated(conn: sqlite3.Connection, funnel: str, run_ts: str) -> int:
    """Rows this funnel stopped returning are deprecated, never deleted.

    Deletion would destroy the audit trail and make false-positive review of
    quarantined rows impossible.
    """
    cur = conn.execute(
        "UPDATE resource SET status = 'deprecated', eligible = 0 "
        "WHERE funnel = ? AND last_seen < ? AND status = 'active'",
        (funnel, run_ts),
    )
    return cur.rowcount


# Funnel priority for the eligibility cap.
#
# Phase 1 funnels supply no stars and no push dates, so quality_score is close
# to constant and cannot rank anything on its own. Ordering by quality alone
# made the cap an arbitrary tie-break that evicted the official `github`,
# `serena`, `playwright` and `linear` plugins in favour of ~1,300 anonymous
# registry entries. Curation is the signal we actually have, so it goes first.
FUNNEL_PRIORITY_SQL = """
  CASE funnel
    WHEN 'local_scan'     THEN 0
    WHEN 'mp_official'    THEN 1
    WHEN 'mp_claude_code' THEN 1
    WHEN 'mp_skills'      THEN 1
    WHEN 'mp_community'   THEN 2
    ELSE 3
  END
"""


def recompute_eligibility(conn: sqlite3.Connection, max_eligible: int) -> int:
    """Only active, non-archived, non-quarantined rows with a usable summary
    compete; the best `max_eligible` become injectable.

    The cap exists so `rdx audit` stays short enough for a human to read.
    """
    conn.execute("UPDATE resource SET eligible = 0")
    cur = conn.execute(
        """
        UPDATE resource SET eligible = 1
        WHERE id IN (
          SELECT id FROM resource
          WHERE status = 'active'
            AND archived = 0
            AND blocking_flags = '[]'
            AND summary IS NOT NULL
            AND length(summary) >= 10
          ORDER BY """ + FUNNEL_PRIORITY_SQL + """, quality_score DESC, stars DESC
          LIMIT ?
        )
        """,
        (max_eligible,),
    )
    return cur.rowcount


def _row_to_resource(row: sqlite3.Row) -> Resource:
    return Resource(
        id=row["id"], type=row["type"], name=row["name"], slug=row["slug"],
        summary=row["summary"] or "", url=row["url"],
        tags=json.loads(row["tags"] or "[]"), trust_tier=row["trust_tier"],
        status=row["status"], quality_score=row["quality_score"],
        stars=row["stars"], install_count=row["install_count"],
        funnel=row["funnel"], flags=json.loads(row["flags"] or "[]"),
    )


# FTS5 column weights, in declared order: name, summary, tags, slug.
#
# Without these, a query for "github issues" loses to any plugin with a long
# description mentioning both words - measured: the official `github` plugin
# ranked below `gitkraken`. An exact name or slug hit is a far stronger signal
# than a term buried in prose, and the weights say so.
BM25_WEIGHTS = (8.0, 1.0, 3.0, 10.0)


def candidates(conn: sqlite3.Connection, match_query: str, *,
               limit: int = 25) -> list[tuple[Resource, float]]:
    """THE retrieval query. This is the only place resources are read for
    injection, and therefore the only place the safety filter must hold.

    Excludes: non-active status (quarantined/deprecated), ineligible rows,
    flagged rows, and anything already installed.
    """
    try:
        rows = conn.execute(
            """
            SELECT r.*, bm25(resource_fts, ?, ?, ?, ?) AS bm
            FROM resource_fts
            JOIN resource r ON r.rowid = resource_fts.rowid
            WHERE resource_fts MATCH ?
              AND r.status = 'active'
              AND r.eligible = 1
              AND r.blocking_flags = '[]'
              AND r.id NOT IN (
                    SELECT resource_id FROM installed_resource
                    WHERE removed_at IS NULL
                  )
            ORDER BY bm
            LIMIT ?
            """,
            # Order matters: the bm25() weights are bound in the SELECT clause,
            # which precedes the WHERE ... MATCH placeholder.
            (*BM25_WEIGHTS, match_query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # Malformed FTS query (user text with stray syntax) is a miss, not a crash.
        return []
    return [(_row_to_resource(r), float(r["bm"])) for r in rows]


def log_injection(conn: sqlite3.Connection, *, ts: str, session_id: str | None,
                  cwd: str | None, prompt: str, query_terms: str,
                  n_candidates: int, n_shown: int, top_score: float | None,
                  margin: float | None, suppressed_reason: str | None,
                  shadow: bool, latency_ms: int,
                  items: Iterable[tuple[str, int, float]] = ()) -> int:
    cur = conn.execute(
        """
        INSERT INTO injection (ts, session_id, cwd, prompt_sha, prompt_len,
          query_terms, n_candidates, n_shown, top_score, margin,
          suppressed_reason, shadow, latency_ms)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (ts, session_id, cwd, prompt_digest(prompt), len(prompt), query_terms,
         n_candidates, n_shown, top_score, margin, suppressed_reason,
         int(shadow), latency_ms),
    )
    injection_id = int(cur.lastrowid)
    for resource_id, rank, score in items:
        conn.execute(
            "INSERT INTO injection_item (injection_id, resource_id, rank, score) "
            "VALUES (?,?,?,?)",
            (injection_id, resource_id, rank, score),
        )
    conn.commit()
    return injection_id


def recent_session_shows(conn: sqlite3.Connection, session_id: str | None,
                         since_ts: str) -> int:
    if not session_id:
        return 0
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM injection "
        "WHERE session_id = ? AND n_shown > 0 AND ts >= ?",
        (session_id, since_ts),
    ).fetchone()
    return int(row["n"])


def session_show_count(conn: sqlite3.Connection, session_id: str | None) -> int:
    if not session_id:
        return 0
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM injection WHERE session_id = ? AND n_shown > 0",
        (session_id,),
    ).fetchone()
    return int(row["n"])


def shows_without_accept(conn: sqlite3.Connection, resource_id: str,
                         since_ts: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM injection_item ii JOIN injection i ON i.id = ii.injection_id
        WHERE ii.resource_id = ? AND i.ts >= ? AND i.n_shown > 0
          AND NOT EXISTS (
            SELECT 1 FROM installed_resource ir
            WHERE ir.resource_id = ii.resource_id AND ir.installed_by = 'rdx'
          )
        """,
        (resource_id, since_ts),
    ).fetchone()
    return int(row["n"])


def get_funnel_state(conn: sqlite3.Connection, funnel: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM funnel_state WHERE funnel = ?", (funnel,)
    ).fetchone()
    return dict(row) if row else {}


def set_funnel_state(conn: sqlite3.Connection, funnel: str, **fields) -> None:
    allowed = {"last_run_at", "last_cursor", "last_etag", "last_status",
               "last_error", "n_seen", "n_upserted", "n_quarantined"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    conn.execute(
        f"INSERT INTO funnel_state (funnel, {cols}) VALUES (?, {placeholders}) "
        f"ON CONFLICT(funnel) DO UPDATE SET {updates}",
        (funnel, *fields.values()),
    )
    conn.commit()


def record_installed(conn: sqlite3.Connection, resource_id: str, *, scope: str,
                     installed_at: str, installed_by: str,
                     version: str | None = None, tool_prefix: str | None = None,
                     tool_names: Sequence[str] | None = None) -> None:
    conn.execute(
        """
        INSERT INTO installed_resource (resource_id, scope, installed_at,
          installed_by, version, tool_prefix, tool_names, last_seen_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(resource_id) DO UPDATE SET
          scope = excluded.scope,
          version = excluded.version,
          tool_prefix = excluded.tool_prefix,
          tool_names = COALESCE(excluded.tool_names, installed_resource.tool_names),
          last_seen_at = excluded.last_seen_at,
          removed_at = NULL
        """,
        (resource_id, scope, installed_at, installed_by, version, tool_prefix,
         json.dumps(list(tool_names)) if tool_names is not None else None,
         installed_at),
    )
    conn.commit()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return {
        "total": one("SELECT COUNT(*) FROM resource"),
        "active": one("SELECT COUNT(*) FROM resource WHERE status='active'"),
        "eligible": one("SELECT COUNT(*) FROM resource WHERE eligible=1"),
        "quarantined": one("SELECT COUNT(*) FROM resource WHERE status='quarantined'"),
        "deprecated": one("SELECT COUNT(*) FROM resource WHERE status='deprecated'"),
        "installed": one("SELECT COUNT(*) FROM installed_resource WHERE removed_at IS NULL"),
    }
