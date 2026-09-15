"""Schema invariants.

The load-bearing test here is `test_quarantined_rows_are_unreachable`: the FTS
triggers are unconditional, so an unsafe row IS in the FTS index. The only
thing keeping it away from a model is the filter inside db.candidates(). If
that filter ever regresses, this test is what catches it.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rdx import config, db  # noqa: E402
from rdx.models import ResourceDraft  # noqa: E402

NOW = "2026-09-15T00:00:00Z"


def draft(rid: str, *, name="thing", summary="does a useful thing with pdfs",
          status="active", slug=None, funnel="test", rtype="mcp",
          tags=None, archived=False) -> ResourceDraft:
    return ResourceDraft(
        id=rid, type=rtype, name=name, slug=slug or rid.replace(":", "-"),
        funnel=funnel, source_ref="https://example.test/x", summary=summary,
        url="https://example.test/x", tags=tags or [], status=status,
        archived=archived,
    )


@pytest.fixture
def conn():
    c = db.init_db(":memory:")
    yield c
    c.close()


def test_schema_version_recorded(conn):
    assert db.schema_version(conn) == config.SCHEMA_VERSION


def test_fts5_is_available(conn):
    db.check_fts5(conn)  # must not raise


def test_insert_populates_fts(conn):
    db.upsert_resource(conn, draft("mcp:test:a"), now=NOW, quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    rows = db.candidates(conn, "pdfs")
    assert [r.id for r, _ in rows] == ["mcp:test:a"]


def test_update_refreshes_fts(conn):
    d = draft("mcp:test:a", summary="handles spreadsheets")
    db.upsert_resource(conn, d, now=NOW, quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    assert db.candidates(conn, "spreadsheets")

    d.summary = "handles kubernetes clusters"
    db.upsert_resource(conn, d, now=NOW, quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    assert not db.candidates(conn, "spreadsheets"), "stale FTS row survived update"
    assert db.candidates(conn, "kubernetes")


def test_delete_removes_from_fts(conn):
    db.upsert_resource(conn, draft("mcp:test:a"), now=NOW, quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    conn.execute("DELETE FROM resource WHERE id = 'mcp:test:a'")
    conn.commit()
    assert not db.candidates(conn, "pdfs")


@pytest.mark.parametrize("status", ["quarantined", "deprecated"])
def test_quarantined_rows_are_unreachable(conn, status):
    """A malicious row indexed in FTS must never reach retrieval."""
    db.upsert_resource(conn, draft("mcp:test:evil", status=status), now=NOW,
                       quality_score=1.0)
    conn.execute("UPDATE resource SET eligible = 1")  # force worst case
    conn.commit()

    # It IS in the FTS index...
    in_fts = conn.execute(
        "SELECT COUNT(*) FROM resource_fts WHERE resource_fts MATCH 'pdfs'"
    ).fetchone()[0]
    assert in_fts == 1

    # ...but retrieval must not return it.
    assert db.candidates(conn, "pdfs") == []


def test_blocking_flagged_rows_are_unreachable(conn):
    """Defense in depth: even if status were somehow left 'active', a blocking
    flag must still keep the row away from retrieval."""
    db.upsert_resource(conn, draft("mcp:test:flagged"), now=NOW,
                       flags=["instr_override"],
                       blocking_flags=["instr_override"], quality_score=1.0)
    conn.execute("UPDATE resource SET eligible = 1")
    conn.commit()
    assert db.candidates(conn, "pdfs") == []


def test_advisory_flags_do_not_block_retrieval(conn):
    """The mirror image, and the bug this split was introduced to fix:
    advisory flags like 'truncated' are set on most long descriptions. If they
    blocked retrieval, nearly the whole index would be silently unreachable."""
    db.upsert_resource(conn, draft("mcp:test:ok"), now=NOW,
                       flags=["truncated", "needs_secrets",
                              "invisible_chars_removed"],
                       blocking_flags=[], quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    assert [r.id for r, _ in db.candidates(conn, "pdfs")] == ["mcp:test:ok"]


def test_installed_rows_are_excluded(conn):
    db.upsert_resource(conn, draft("mcp:test:a"), now=NOW, quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    assert db.candidates(conn, "pdfs")

    db.record_installed(conn, "mcp:test:a", scope="user", installed_at=NOW,
                        installed_by="preexisting")
    assert db.candidates(conn, "pdfs") == [], "already-installed resource suggested"


def test_ineligible_rows_are_unreachable(conn):
    db.upsert_resource(conn, draft("mcp:test:a"), now=NOW, quality_score=0.9)
    # eligible defaults to 0 and recompute_eligibility was not called
    assert db.candidates(conn, "pdfs") == []


def test_eligibility_cap_is_enforced(conn):
    for i in range(10):
        db.upsert_resource(conn, draft(f"mcp:test:{i}"), now=NOW,
                           quality_score=i / 10)
    n = db.recompute_eligibility(conn, 3)
    assert n == 3
    assert db.counts(conn)["eligible"] == 3


def test_archived_rows_never_eligible(conn):
    db.upsert_resource(conn, draft("mcp:test:dead", archived=True), now=NOW,
                       quality_score=1.0)
    db.recompute_eligibility(conn, 100)
    assert db.counts(conn)["eligible"] == 0
    assert db.candidates(conn, "pdfs") == []


def test_first_seen_preserved_across_updates(conn):
    d = draft("mcp:test:a")
    db.upsert_resource(conn, d, now="2026-01-01T00:00:00Z")
    db.upsert_resource(conn, d, now="2026-09-15T00:00:00Z")
    row = conn.execute("SELECT first_seen, last_seen FROM resource").fetchone()
    assert row["first_seen"] == "2026-01-01T00:00:00Z"
    assert row["last_seen"] == "2026-09-15T00:00:00Z"


def test_mark_stale_deprecated(conn):
    db.upsert_resource(conn, draft("mcp:test:old"), now="2026-01-01T00:00:00Z")
    db.upsert_resource(conn, draft("mcp:test:new"), now="2026-09-15T00:00:00Z")
    n = db.mark_stale_deprecated(conn, "test", "2026-09-15T00:00:00Z")
    conn.commit()
    assert n == 1
    assert db.counts(conn)["deprecated"] == 1


def test_prompt_is_never_stored(conn):
    secret = "my secret prompt about ACME internal project"
    db.log_injection(conn, ts=NOW, session_id="s1", cwd="/tmp", prompt=secret,
                     query_terms="acme", n_candidates=0, n_shown=0,
                     top_score=None, margin=None, suppressed_reason="no_match",
                     shadow=True, latency_ms=3)
    dump = "\n".join(conn.iterdump())
    assert secret not in dump
    assert db.prompt_digest(secret) in dump


def test_malformed_fts_query_is_a_miss_not_a_crash(conn):
    db.upsert_resource(conn, draft("mcp:test:a"), now=NOW, quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    assert db.candidates(conn, 'pdf OR "unterminated') == []


def test_tags_roundtrip(conn):
    db.upsert_resource(conn, draft("mcp:test:a", tags=["has:mcp", "pdf"]),
                       now=NOW, quality_score=0.9)
    db.recompute_eligibility(conn, 100)
    res, _ = db.candidates(conn, "pdfs")[0]
    assert res.tags == ["has:mcp", "pdf"]
