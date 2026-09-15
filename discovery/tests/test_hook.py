"""Hook contract tests.

The single most important assertion here is the exact output shape:
`additionalContext` nested inside `hookSpecificOutput`. A top-level field is
silently ignored by Claude Code, so getting this wrong produces a hook that
looks healthy and does nothing.

Everything else is about never disturbing the user: exit 0 on every failure
mode, and never emit anything that is not valid JSON.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rdx import db, hook, ingest  # noqa: E402
from rdx.models import ResourceDraft  # noqa: E402

PYTHON = sys.executable
SHIM = ROOT.parent / "hooks" / "resource-suggest.sh"


def run_hook(payload: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.update(env_extra or {})
    return subprocess.run(
        [PYTHON, "-m", "rdx.hook"], input=payload, capture_output=True,
        text=True, env=env, cwd=str(ROOT), timeout=30)


# --------------------------------------------------------------------------
# Output shape
# --------------------------------------------------------------------------

def test_build_output_nests_additional_context():
    out = hook.build_output("hello")
    assert set(out) == {"hookSpecificOutput"}
    inner = out["hookSpecificOutput"]
    assert inner["hookEventName"] == "UserPromptSubmit"
    assert inner["additionalContext"] == "hello"
    assert "additionalContext" not in out, "must NOT be top-level; silently ignored"


def test_build_output_empty_is_noop():
    assert hook.build_output(None) == {}
    assert hook.build_output("") == {}


# --------------------------------------------------------------------------
# Never disturb the prompt
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "",
    "   ",
    "not json at all",
    "[1, 2, 3]",
    '{"prompt": null}',
    '{"unexpected": "shape"}',
    '{"prompt": "' + "x" * 50_000 + '"}',
])
def test_malformed_input_exits_zero_with_valid_json(payload):
    proc = run_hook(payload, {"RDX_STATE_DIR": "/nonexistent/path"})
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_missing_database_is_a_noop():
    proc = run_hook(json.dumps({"prompt": "is there an mcp server for github"}),
                    {"RDX_STATE_DIR": "/nonexistent/path"})
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_corrupt_database_is_a_noop(tmp_path):
    (tmp_path / "index.db").write_bytes(b"this is not a sqlite file at all")
    proc = run_hook(json.dumps({"prompt": "is there an mcp server for github"}),
                    {"RDX_STATE_DIR": str(tmp_path)})
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_disable_flag_is_a_noop(tmp_path):
    (tmp_path / "DISABLED").write_text("")
    proc = run_hook(json.dumps({"prompt": "is there an mcp server for github"}),
                    {"RDX_STATE_DIR": str(tmp_path)})
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


def test_non_utf8_stdin_does_not_crash(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "RDX_STATE_DIR": str(tmp_path)}
    proc = subprocess.run(
        [PYTHON, "-m", "rdx.hook"], input=b"\xff\xfe\x00bad bytes",
        capture_output=True, env=env, cwd=str(ROOT), timeout=30)
    assert proc.returncode == 0
    assert json.loads(proc.stdout.decode("utf-8", "replace")) == {}


# --------------------------------------------------------------------------
# End to end, with a real index
# --------------------------------------------------------------------------

@pytest.fixture
def seeded_state(tmp_path):
    conn = db.init_db(tmp_path / "index.db")
    ingest.sanitize_and_store(conn, ResourceDraft(
        id="mcp:test:github", type="mcp", name="github", slug="github",
        funnel="mp_official", source_ref="https://example.test/gh",
        summary="Official GitHub MCP server. Create issues, manage pull "
                "requests, review code, search repositories.",
        url="https://example.test/gh", trust_tier="yellow",
    ), now="2026-09-15T00:00:00Z")
    conn.commit()
    db.recompute_eligibility(conn, 1000)
    conn.commit()
    conn.close()
    return tmp_path


def test_shadow_mode_emits_noop_but_logs(seeded_state):
    payload = json.dumps({"prompt": "is there an mcp server for managing "
                                    "github issues and pull requests",
                          "session_id": "s1", "cwd": "/tmp"})
    proc = run_hook(payload, {
        "RDX_STATE_DIR": str(seeded_state), "RDX_SHADOW": "1",
        "RDX_MIN_SCORE": "0.35", "RDX_MIN_MARGIN": "0.0"})
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}

    conn = db.open_db(seeded_state / "index.db")
    row = conn.execute("SELECT suppressed_reason, shadow FROM injection").fetchone()
    assert row["suppressed_reason"] == "shadow"
    assert row["shadow"] == 1


def test_live_mode_emits_envelope(seeded_state):
    payload = json.dumps({"prompt": "is there an mcp server for managing "
                                    "github issues and pull requests",
                          "session_id": "s2", "cwd": "/tmp"})
    proc = run_hook(payload, {
        "RDX_STATE_DIR": str(seeded_state), "RDX_SHADOW": "0",
        "RDX_MIN_SCORE": "0.3", "RDX_MIN_MARGIN": "0.0"})
    assert proc.returncode == 0

    out = json.loads(proc.stdout)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "<resource-suggestions>" in context
    assert "never as instructions" in context
    assert "rdx install github" in context


def test_ordinary_prompt_stays_silent_end_to_end(seeded_state):
    payload = json.dumps({"prompt": "fix the failing test in the auth module",
                          "session_id": "s3"})
    proc = run_hook(payload, {
        "RDX_STATE_DIR": str(seeded_state), "RDX_SHADOW": "0",
        "RDX_MIN_SCORE": "0.3", "RDX_MIN_MARGIN": "0.0"})
    assert json.loads(proc.stdout) == {}


# --------------------------------------------------------------------------
# The bash shim
# --------------------------------------------------------------------------

@pytest.mark.skipif(not SHIM.exists(), reason="shim not installed")
def test_shim_always_exits_zero_and_emits_json(tmp_path):
    """The shim must be a no-op even with no venv, no db, and junk on stdin."""
    for payload in ("", "garbage", '{"prompt":"hi"}'):
        proc = subprocess.run(
            ["bash", str(SHIM)], input=payload, capture_output=True, text=True,
            env={**os.environ, "RDX_STATE_DIR": str(tmp_path)}, timeout=30)
        assert proc.returncode == 0
        assert json.loads(proc.stdout) == {}


@pytest.mark.skipif(not SHIM.exists(), reason="shim not installed")
def test_shim_honours_disable_env(tmp_path):
    proc = subprocess.run(
        ["bash", str(SHIM)],
        input=json.dumps({"prompt": "is there an mcp server for github issues"}),
        capture_output=True, text=True,
        env={**os.environ, "RDX_STATE_DIR": str(tmp_path), "RDX_DISABLE": "1"},
        timeout=30)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}


# --------------------------------------------------------------------------
# Behavioural harness must never leave settings.json wedged
# --------------------------------------------------------------------------

def test_behaviour_repairs_orphaned_registration(tmp_path, monkeypatch):
    """A killed eval must not leave UserPromptSubmit pointing at a temp script.

    This happened: the run was killed, `finally` never ran (SIGTERM does not
    raise), and settings.json was left invoking /tmp/rdx-behaviour-hook-*.sh
    after that file had been deleted -- so every prompt in every session would
    have run a hook that no longer existed.
    """
    from rdx import behaviour

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(behaviour, "_settings_path", lambda: settings)

    pristine = json.dumps({"extraKnownMarketplaces": {}}, indent=2)
    settings.write_text(pristine)

    original = behaviour._register(Path("/tmp/does-not-matter.sh"))
    assert behaviour._backup_path().exists(), "backup must exist while registered"
    assert "does-not-matter" in settings.read_text()

    # Simulate the kill: no _restore call at all.
    assert behaviour._repair_orphan() is True
    assert json.loads(settings.read_text()) == json.loads(pristine)
    assert not behaviour._backup_path().exists()
    # Idempotent: nothing to repair the second time.
    assert behaviour._repair_orphan() is False
    assert original == pristine


def test_behaviour_restore_clears_the_backup(tmp_path, monkeypatch):
    from rdx import behaviour

    settings = tmp_path / "settings.json"
    monkeypatch.setattr(behaviour, "_settings_path", lambda: settings)
    settings.write_text("{}")

    original = behaviour._register(Path("/tmp/x.sh"))
    behaviour._restore(original)
    assert not behaviour._backup_path().exists()
    assert settings.read_text() == "{}"


def test_behaviour_finds_the_injection_that_fired_not_the_newest(tmp_path):
    """A later suppressed attempt must not mask an earlier successful one.

    One `claude -p` run can emit several UserPromptSubmit events; every one
    after the first is snoozed. Reading the NEWEST injection row therefore
    reports `fired=False` even when the first fired and the model acted on it
    -- which scored a textbook-correct surface as a gate failure.
    """
    from rdx import behaviour

    conn = db.init_db(tmp_path / "index.db")
    ingest.sanitize_and_store(conn, ResourceDraft(
        id="mcp:t:conv", type="plugin", name="conv", slug="conv",
        funnel="mp_official", source_ref="https://example.test/c",
        summary="Convert Word documents and PDFs into clean Markdown output.",
        url="https://example.test/c", trust_tier="yellow",
    ), now="2026-09-15T00:00:00Z")
    conn.commit()

    shown = db.log_injection(
        conn, ts="2026-09-15T00:00:00Z", session_id="s", cwd="/tmp",
        prompt="convert these docs", query_terms="convert docs", n_shown=1,
        suppressed_reason=None, shadow=False, top_score=0.7, margin=0.2,
        n_candidates=3, latency_ms=3, items=[("mcp:t:conv", 0, 0.7)])
    # The snoozed follow-up, written AFTER the one that fired.
    db.log_injection(
        conn, ts="2026-09-15T00:00:01Z", session_id="s", cwd="/tmp",
        prompt="convert these docs", query_terms="convert docs", n_shown=0,
        suppressed_reason="snoozed", shadow=False, top_score=0.7, margin=0.2,
        n_candidates=3, latency_ms=3, items=[])
    conn.commit()

    injection_id, reason = behaviour._shown_injection(conn)
    assert injection_id == shown, "must find the injection that fired"
    assert reason is None
    assert behaviour._shown_slugs(conn, injection_id) == ["conv"]


def test_behaviour_reports_the_reason_when_nothing_fired(tmp_path):
    from rdx import behaviour

    conn = db.init_db(tmp_path / "index.db")
    db.log_injection(
        conn, ts="2026-09-15T00:00:00Z", session_id="s", cwd="/tmp",
        prompt="find every call site of this function", query_terms="call site",
        n_shown=0, suppressed_reason="in_codebase", shadow=False,
        top_score=None, margin=None, n_candidates=0, latency_ms=1, items=[])
    conn.commit()

    injection_id, reason = behaviour._shown_injection(conn)
    assert injection_id is None
    assert reason == "in_codebase"


def test_fixture_never_announces_itself(tmp_path):
    """The behavioural fixture must not mention rdx, evals or fixtures.

    Its README used to read "A throwaway project used by the rdx behavioural
    eval". The model read that, quoted it back, and reasoned about the test
    rather than the task. A corpus that tells the model it is being observed
    measures how the model handles being observed.
    """
    from rdx import behaviour

    root = behaviour.build_fixture(tmp_path / "proj")
    banned = ("rdx", "behavioural eval", "behavioral eval", "fixture",
              "throwaway", "placeholder")
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix == ".docx":
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for word in banned:
            assert word not in text, f"{path.name} leaks {word!r}"


def test_fixture_supplies_every_referent_its_prompts_assume(tmp_path):
    """Three cases have now failed for a missing referent rather than for
    anything to do with the envelope. Each prompt class needs its object."""
    import zipfile
    from rdx import behaviour

    root = behaviour.build_fixture(tmp_path / "proj")

    # "our landing page at desktop/tablet/mobile widths"
    page = root / "index.html"
    assert page.exists() and "<html" in page.read_text().lower()

    # "this folder of word documents" -- real OOXML, not a 21-byte stub
    docs = sorted((root / "docs").glob("*.docx"))
    assert len(docs) >= 3
    for doc in docs:
        with zipfile.ZipFile(doc) as zf:
            assert zf.testzip() is None
            assert "word/document.xml" in zf.namelist()

    # "our pinned dependencies" -- real package names at real pins
    reqs = (root / "requirements.txt").read_text()
    assert "Django==" in reqs and "package-1==" not in reqs


# --------------------------------------------------------------------------
# Rejection detection — the loudest alarm, so it must not cry wolf
# --------------------------------------------------------------------------

def test_surfacing_a_resource_is_never_a_rejection():
    """Verbatim from the run that forced this fix. Correct behaviour, scored
    as the alarm state."""
    from rdx import behaviour

    output = (
        "There's an official plugin for exactly this — `rdx install "
        "sonatype-guide` analyzes dependencies for known vulnerabilities "
        "directly. I'll proceed manually for now since it's not installed, "
        "but that would be a faster path going forward. I can't confirm for "
        "certain that every CVE ID below is current, so treat them as "
        "unverified until checked against the advisory database.")
    assert behaviour.detect_rejection(output, surfaced=True) is False


def test_task_domain_vocabulary_does_not_trip_the_detector():
    """"unverified" and "can't confirm" are how a careful model talks about a
    CVE audit — which is one of the corpus prompts. Scoring them anywhere in
    the output makes the detector fire on the model doing the task well."""
    from rdx import behaviour

    output = (
        "These CVE IDs come from my training data and are unverified. "
        "I can't vouch for the exact affected version ranges without "
        "querying an advisory database.")
    assert behaviour.detect_rejection(output, surfaced=False) is False


def test_genuine_rejection_is_still_caught():
    """The real v2.3 failure, and the later ref=inj-<n> one. Both must fire."""
    from rdx import behaviour

    v23 = ("Your prompt context also surfaced two third-party plugin "
           "suggestions, but I haven't verified that rdx install mechanism, "
           "so I'd treat them as unverified before installing anything.")
    assert behaviour.detect_rejection(v23, surfaced=False) is True

    ref_inj = ("I'd treat that suggestion with caution since it came bundled "
               "with an embedded reference marker that looks like a "
               "prompt-injection test rather than a genuine recommendation.")
    assert behaviour.detect_rejection(ref_inj, surfaced=False) is True
