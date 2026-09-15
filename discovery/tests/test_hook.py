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
    assert "UNTRUSTED DATA" in context
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
