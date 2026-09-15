"""Measurement tests.

Two things matter here: the accept-rate window must be honest (an install an
hour later is not evidence the suggestion worked), and the spool must never
carry tool inputs, which contain file contents and arguments.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rdx import db, ingest, measure  # noqa: E402
from rdx.models import ResourceDraft  # noqa: E402

OBSERVE = ROOT.parent / "hooks" / "tool-observe.sh"
T0 = "2026-09-15T12:00:00"


@pytest.fixture
def conn():
    c = db.init_db(":memory:")
    ingest.sanitize_and_store(c, ResourceDraft(
        id="mcp:t:github", type="mcp", name="github", slug="github",
        funnel="mp_official", source_ref="https://example.test/x",
        summary="Query GitHub issues and pull requests from Claude.",
        url="https://example.test/x"), now=T0 + "Z")
    c.commit()
    db.recompute_eligibility(c, 100)
    c.commit()
    yield c
    c.close()


def _inject(conn, ts, resource_id="mcp:t:github"):
    return db.log_injection(
        conn, ts=ts, session_id="s1", cwd="/tmp", prompt="any mcp for github",
        query_terms="github", n_candidates=1, n_shown=1, top_score=0.8,
        margin=0.2, suppressed_reason=None, shadow=False, latency_ms=3,
        items=[(resource_id, 0, 0.8)])


# --------------------------------------------------------------------------
# Accept rate
# --------------------------------------------------------------------------

def test_no_injections_is_zero_not_a_crash(conn):
    assert measure.accept_rate(conn) == {"shown": 0, "accepted": 0, "rate": 0.0}


def test_install_inside_the_window_counts(conn):
    _inject(conn, T0)
    db.record_installed(conn, "mcp:t:github", scope="local",
                        installed_at="2026-09-15T12:10:00", installed_by="rdx")
    assert measure.accept_rate(conn)["rate"] == 1.0


def test_install_outside_the_window_does_not_count(conn):
    """An install an hour later is not evidence the suggestion caused it."""
    _inject(conn, T0)
    db.record_installed(conn, "mcp:t:github", scope="local",
                        installed_at="2026-09-15T14:00:00", installed_by="rdx")
    assert measure.accept_rate(conn)["rate"] == 0.0


def test_preexisting_install_does_not_count(conn):
    """Something already on the machine was not accepted from a suggestion."""
    _inject(conn, T0)
    db.record_installed(conn, "mcp:t:github", scope="local",
                        installed_at="2026-09-15T12:05:00",
                        installed_by="preexisting")
    assert measure.accept_rate(conn)["rate"] == 0.0


def test_install_before_the_injection_does_not_count(conn):
    _inject(conn, T0)
    db.record_installed(conn, "mcp:t:github", scope="local",
                        installed_at="2026-09-15T11:00:00", installed_by="rdx")
    assert measure.accept_rate(conn)["rate"] == 0.0


def test_top_unaccepted_feeds_the_snooze_list(conn):
    for _ in range(3):
        _inject(conn, T0)
    assert measure.top_unaccepted(conn) == [("github", 3)]


# --------------------------------------------------------------------------
# Spool
# --------------------------------------------------------------------------

def test_drain_attributes_via_tool_prefix(conn, tmp_path):
    db.record_installed(conn, "mcp:t:github", scope="local", installed_at=T0,
                        installed_by="rdx", tool_prefix="mcp__github__")
    spool = tmp_path / "toolevents.jsonl"
    spool.write_text("\n".join(json.dumps(e) for e in [
        {"ts": T0, "session_id": "s1", "tool_name": "mcp__github__search_code"},
        {"ts": T0, "session_id": "s1", "tool_name": "Bash"},
        {"ts": T0, "session_id": "s1", "tool_name": "mcp__other__thing"},
    ]))

    report = measure.drain_spool(conn, spool)
    assert report.events_stored == 3
    assert report.attributed == 1
    assert spool.read_text() == "", "spool not truncated after drain"


def test_drain_survives_malformed_lines(conn, tmp_path):
    spool = tmp_path / "toolevents.jsonl"
    spool.write_text('not json\n{"tool_name":"Bash"}\n{"no_tool":1}\n\n')
    report = measure.drain_spool(conn, spool)
    assert report.events_stored == 1
    assert report.malformed == 2


def test_missing_spool_is_a_noop(conn, tmp_path):
    assert measure.drain_spool(conn, tmp_path / "nope.jsonl").lines_read == 0


# --------------------------------------------------------------------------
# Tool drift
# --------------------------------------------------------------------------

def test_new_tool_after_install_is_flagged(conn, tmp_path):
    """The supply-chain case: a server quietly grows a tool post-install."""
    db.record_installed(conn, "mcp:t:github", scope="local", installed_at=T0,
                        installed_by="rdx", tool_prefix="mcp__github__",
                        tool_names=["mcp__github__search_code"])
    spool = tmp_path / "s.jsonl"
    spool.write_text(json.dumps(
        {"ts": T0, "session_id": "s1", "tool_name": "mcp__github__exfiltrate"}))
    measure.drain_spool(conn, spool)

    drift = measure.tool_drift(conn)
    assert len(drift) == 1
    assert "mcp__github__exfiltrate" in drift[0].added


def test_no_drift_when_tools_match(conn, tmp_path):
    db.record_installed(conn, "mcp:t:github", scope="local", installed_at=T0,
                        installed_by="rdx", tool_prefix="mcp__github__",
                        tool_names=["mcp__github__search_code"])
    spool = tmp_path / "s.jsonl"
    spool.write_text(json.dumps(
        {"ts": T0, "session_id": "s1", "tool_name": "mcp__github__search_code"}))
    measure.drain_spool(conn, spool)
    assert measure.tool_drift(conn) == []


# --------------------------------------------------------------------------
# The hook itself
# --------------------------------------------------------------------------

@pytest.mark.skipif(not OBSERVE.exists(), reason="hook not present")
def test_hook_never_records_tool_inputs(tmp_path):
    """Tool inputs contain file contents, prompts and arguments. None of it
    belongs in a log on disk."""
    payload = json.dumps({
        "session_id": "s1", "tool_name": "Read",
        "tool_input": {"file_path": "/home/me/.ssh/id_rsa",
                       "content": "SUPER SECRET KEY MATERIAL"},
    })
    subprocess.run(["bash", str(OBSERVE)], input=payload, text=True,
                   capture_output=True, timeout=30,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                        "RDX_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)})
    written = (tmp_path / "toolevents.jsonl").read_text()
    assert "SUPER SECRET" not in written
    assert "id_rsa" not in written
    assert json.loads(written.strip())["tool_name"] == "Read"


@pytest.mark.skipif(not OBSERVE.exists(), reason="hook not present")
def test_hook_always_exits_zero(tmp_path):
    """A measurement hook must never interfere with a tool call."""
    for payload in ("", "garbage", "{}", '{"tool_name":"Bash"}'):
        proc = subprocess.run(
            ["bash", str(OBSERVE)], input=payload, text=True,
            capture_output=True, timeout=30,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                 "RDX_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)})
        assert proc.returncode == 0


@pytest.mark.skipif(not OBSERVE.exists(), reason="hook not present")
def test_hook_honours_the_kill_switch(tmp_path):
    (tmp_path / "DISABLED").write_text("")
    subprocess.run(["bash", str(OBSERVE)],
                   input='{"session_id":"s","tool_name":"Bash"}', text=True,
                   capture_output=True, timeout=30,
                   env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                        "RDX_STATE_DIR": str(tmp_path), "HOME": str(tmp_path)})
    assert not (tmp_path / "toolevents.jsonl").exists()
