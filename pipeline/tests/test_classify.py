"""classify.py and the judgment it puts behind feedback.py and persona_run.py.
A fake claude answers from CLASSIFY_WHAT; the rules must take over when it
is off or answers badly."""
import json
import os
import stat

import pytest
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(KIT.parent / "loop"))
import classify  # noqa: E402
import backlog_io  # noqa: E402

FAKE = """#!/usr/bin/env bash
# answers by CLASSIFY_WHAT; the prompt is $2
case "${CLASSIFY_WHAT:-}" in
  dimension)      r='{"dimension":"perf","why":"slow"}' ;;
  duplicates)     case "$2" in *"could not find save"*"ux-1:"*) r='{"same":["ux-1"]}' ;; *"searching takes ages"*"fb-1:"*) r='{"same":["fb-1"]}' ;; *) r='{"same":[]}' ;; esac ;;
  resolved)       r='{"resolved":["ux-1"]}' ;;
  contradictions) r='{"contradictions":[{"bullet":"nobody here writes changelogs at all","anchor":"developers want a changelog generated from git history","why":"pain denied"}]}' ;;
  *)              r='{}' ;;
esac
printf '{"total_cost_usd":0.004,"result":"%s"}\\n' "$(printf '%s' "$r" | sed 's/"/\\\\"/g')"
"""


@pytest.fixture(autouse=True)
def _classify_on(monkeypatch):
    monkeypatch.delenv("CLASSIFY_OFF", raising=False)


def fake(tmp_path, text=FAKE):
    p = tmp_path / "fake_claude.sh"
    p.write_text(text)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_ask_validates_shape_and_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_CLAUDE", fake(tmp_path))
    assert classify.dimension("the page takes ten seconds", str(tmp_path)) == "perf"
    rows = [json.loads(l) for l in (tmp_path / ".pipeline" / "ledger.jsonl").read_text().splitlines()]
    assert rows[-1]["stage"] == "classify:dimension" and rows[-1]["cost_usd"] == 0.004
    # a bad shape is None, not an exception
    monkeypatch.setenv("NIGHTSHIFT_CLAUDE", fake(tmp_path, "#!/usr/bin/env bash\necho '{\"result\":\"{\\\"dimension\\\":\\\"vibes\\\"}\"}'\n"))
    assert classify.dimension("x", str(tmp_path)) is None


def test_off_switch_and_missing_binary_give_none(tmp_path, monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_CLAUDE", "/nonexistent/claude")
    assert classify.dimension("it crashed", str(tmp_path)) is None
    monkeypatch.setenv("NIGHTSHIFT_CLAUDE", fake(tmp_path))
    monkeypatch.setenv("CLASSIFY_OFF", "1")
    assert classify.dimension("it crashed", str(tmp_path)) is None


def test_duplicates_only_returns_known_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_CLAUDE", fake(tmp_path))
    assert classify.duplicates("could not find save button", {"ux-1": "save button hidden", "ux-2": "x"}, str(tmp_path)) == ["ux-1"]
    assert classify.duplicates("anything", {"ux-9": "y"}, str(tmp_path)) == []
    assert classify.duplicates("anything", {}, str(tmp_path)) == []


# ---------------------------------------------------------------------------
# feedback.py with and without the model
# ---------------------------------------------------------------------------

def run_feedback(tmp_path, env):
    inbox = tmp_path / "FEEDBACK.md"
    backlog = tmp_path / ".loop" / "backlog.yaml"
    return subprocess.run([sys.executable, str(KIT / "feedback.py"), "--inbox", str(inbox), "--backlog", str(backlog)],
                          capture_output=True, text=True, env=env, cwd=str(tmp_path))


def test_feedback_uses_the_model_for_dimension_and_skips_duplicates(tmp_path, monkeypatch):
    (tmp_path / ".loop").mkdir()
    backlog_io.dump([{"id": "fb-1", "title": "search is painfully slow", "dimension": "perf", "source": "production",
                      "status": "open", "rung": 1, "est": "S", "attempts": 0, "iter_added": 0, "note": ""}],
                    str(tmp_path / ".loop" / "backlog.yaml"))
    (tmp_path / "FEEDBACK.md").write_text("## 2026-09-18\n- the export took forever to finish\n- searching takes ages, same as before\n")
    env = dict(os.environ, NIGHTSHIFT_CLAUDE=fake(tmp_path))
    proc = run_feedback(tmp_path, env)
    assert proc.returncode == 0, proc.stderr
    rows = {r["id"]: r for r in backlog_io.load(str(tmp_path / ".loop" / "backlog.yaml"))}
    added = [r for r in rows.values() if r["id"] != "fb-1"]
    assert len(added) == 1 and added[0]["dimension"] == "perf"   # the keyword rule would have said "none" for "took forever"
    assert "duplicate of fb-1" in proc.stdout


def test_feedback_falls_back_to_keywords_when_the_model_is_off(tmp_path):
    (tmp_path / ".loop").mkdir()
    (tmp_path / "FEEDBACK.md").write_text("## 2026-09-18\n- the export took forever to finish\n- it crashed on save\n")
    env = dict(os.environ, CLASSIFY_OFF="1")
    proc = run_feedback(tmp_path, env)
    assert proc.returncode == 0, proc.stderr
    dims = sorted(r["dimension"] for r in backlog_io.load(str(tmp_path / ".loop" / "backlog.yaml")))
    assert dims == ["none", "tests"]


def test_feedback_reports_contradictions_with_the_validation(tmp_path):
    (tmp_path / ".loop").mkdir()
    vd = tmp_path / ".pipeline" / "validate" / "abcd1234"; vd.mkdir(parents=True)
    (vd / "verdict.json").write_text(json.dumps({"verdict": "GO"}))
    (vd / "claims.json").write_text(json.dumps({"claims": [{"id": "c1", "statement": "developers want a changelog generated from git history"}]}))
    (tmp_path / "FEEDBACK.md").write_text("## 2026-09-18\n- nobody here writes changelogs at all\n")
    env = dict(os.environ, NIGHTSHIFT_CLAUDE=fake(tmp_path))
    proc = run_feedback(tmp_path, env)
    assert proc.returncode == 0, proc.stderr
    assert "contradicts validation" in proc.stdout
    found = json.loads((vd / "feedback-check.json").read_text())["contradictions"]
    assert found and found[0]["bullet"] == "nobody here writes changelogs at all"


# ---------------------------------------------------------------------------
# persona_run's judgment
# ---------------------------------------------------------------------------

def test_persona_judgment_drops_duplicates_and_resolves_only_what_the_model_names(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("persona_run", KIT / "persona_run.py")
    pr = importlib.util.module_from_spec(spec); spec.loader.exec_module(pr)
    monkeypatch.setenv("NIGHTSHIFT_CLAUDE", fake(tmp_path))
    (tmp_path / ".loop").mkdir()
    backlog = tmp_path / ".loop" / "backlog.yaml"
    backlog_io.dump([
        {"id": "ux-1", "title": "save button hidden in overflow", "dimension": "persona", "source": "persona", "status": "open", "rung": 1, "est": "S", "attempts": 0, "iter_added": 0, "note": 'task "save a note"'},
        {"id": "ux-2", "title": "no confirmation after saving", "dimension": "persona", "source": "persona", "status": "open", "rung": 1, "est": "S", "attempts": 0, "iter_added": 0, "note": 'task "save a note"'},
    ], str(backlog))
    run_dir = tmp_path / "save-a-note-3"; run_dir.mkdir()
    (run_dir / "result.json").write_text(json.dumps({"status": "complete", "steps": 2}))
    (run_dir / "findings.json").write_text(json.dumps({"dead_ends": ["could not find save, it was in a menu"], "confusions": []}))
    (run_dir / "trail.json").write_text(json.dumps([{"action": "click Save", "saw": "note saved"}]))
    decided = pr.apply_judgment(str(run_dir), str(backlog), "save a note", str(tmp_path))
    assert decided is True
    findings = json.loads((run_dir / "findings.json").read_text())
    assert findings["dead_ends"] == []   # the model said it is ux-1 again
    assert json.loads((run_dir / "findings.dedup.json").read_text())["dropped"][0]["same_as"] == ["ux-1"]
    rows = {r["id"]: r for r in backlog_io.load(str(backlog))}
    assert rows["ux-1"]["status"] == "done" and "judged" in rows["ux-1"]["note"]
    assert rows["ux-2"]["status"] == "open"   # the walkthrough did not touch it, and the name rule would have closed it


def test_persona_judgment_yields_to_the_rule_when_the_model_is_off(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("persona_run", KIT / "persona_run.py")
    pr = importlib.util.module_from_spec(spec); spec.loader.exec_module(pr)
    monkeypatch.setenv("CLASSIFY_OFF", "1")
    (tmp_path / ".loop").mkdir()
    backlog = tmp_path / ".loop" / "backlog.yaml"
    backlog_io.dump([{"id": "ux-1", "title": "x", "dimension": "persona", "source": "persona", "status": "open", "rung": 1, "est": "S", "attempts": 0, "iter_added": 0, "note": 'task "save a note"'}], str(backlog))
    run_dir = tmp_path / "r"; run_dir.mkdir()
    (run_dir / "result.json").write_text(json.dumps({"status": "complete", "steps": 2}))
    (run_dir / "findings.json").write_text(json.dumps({"dead_ends": ["something new"], "confusions": []}))
    assert pr.apply_judgment(str(run_dir), str(backlog), "save a note", str(tmp_path)) is False
    assert json.loads((run_dir / "findings.json").read_text())["dead_ends"] == ["something new"]
