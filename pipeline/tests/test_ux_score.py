import json
import sys
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SCRIPT = KIT / "ux_score.py"


def run(run_dir, check, backlog=None):
    cmd = [sys.executable, str(SCRIPT), "--run", str(run_dir), "--check", json.dumps(check)]
    if backlog is not None:
        cmd += ["--backlog", str(backlog)]
    return subprocess.run(cmd, capture_output=True, text=True)


def write_json(path, obj):
    path.write_text(json.dumps(obj))


def last_line(proc):
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_score_without_judge(tmp_path):
    write_json(tmp_path / "result.json", {"status": "complete", "steps": 4})
    check = {"type": "persona", "task": "x", "max_steps": 4, "must": "complete"}
    proc = run(tmp_path, check)
    assert proc.returncode == 0, proc.stderr
    out = last_line(proc)
    assert out["judged"] is False
    assert out["judge_total"] is None
    assert out["completed"] is True
    assert out["step_ratio"] == 1.0
    expected = 100.0 * (0.625 * 1 + 0.375 * 1.0)
    assert abs(out["value"] - expected) < 1e-9
    on_disk = json.loads((tmp_path / "score.json").read_text())
    assert on_disk == out


def test_score_with_judge(tmp_path):
    write_json(tmp_path / "result.json", {"status": "complete", "steps": 6})
    write_json(tmp_path / "judge.json", {
        "scores": {"findability": 2, "feedback": 2, "recovery": 1,
                   "consistency": 2, "wording": 1},
        "total": 8, "evidence": ["step 2: had to guess"],
    })
    check = {"type": "persona", "task": "x", "max_steps": 4, "must": "complete"}
    proc = run(tmp_path, check)
    assert proc.returncode == 0, proc.stderr
    out = last_line(proc)
    assert out["judged"] is True
    assert out["judge_total"] == 8
    step_ratio = min(1.0, 4 / 6)
    completed = 1  # steps 6 <= max_steps*1.5 == 6, status complete
    expected = 100.0 * (0.5 * completed + 0.3 * step_ratio + 0.2 * 8 / 10.0)
    assert abs(out["value"] - expected) < 1e-9


def test_completed_false_when_too_many_steps(tmp_path):
    write_json(tmp_path / "result.json", {"status": "complete", "steps": 10})
    check = {"type": "persona", "task": "x", "max_steps": 4, "must": "complete"}
    out = last_line(run(tmp_path, check))
    assert out["completed"] is False


def test_exit_2_when_must_complete_not_met(tmp_path):
    write_json(tmp_path / "result.json", {"status": "stuck", "steps": 3})
    check = {"type": "persona", "task": "x", "max_steps": 4, "must": "complete"}
    proc = run(tmp_path, check)
    assert proc.returncode == 2
    assert (tmp_path / "score.json").exists()
    out = json.loads((tmp_path / "score.json").read_text())
    assert out["completed"] is False


def test_exit_4_when_result_missing(tmp_path):
    check = {"type": "persona", "task": "x", "max_steps": 4, "must": "complete"}
    proc = run(tmp_path, check)
    assert proc.returncode == 4
    assert not (tmp_path / "score.json").exists()


def test_backlog_rows_appended_and_deduplicated(tmp_path):
    write_json(tmp_path / "result.json", {"status": "complete", "steps": 4})
    write_json(tmp_path / "findings.json", {
        "dead_ends": ["Could not find the save button"],
        "confusions": ["Search box looked disabled"],
    })
    backlog = tmp_path / "backlog.yaml"
    check = {"type": "persona", "task": 'create a note', "max_steps": 4, "must": "complete"}

    proc = run(tmp_path, check, backlog=backlog)
    assert proc.returncode == 0, proc.stderr

    sys.path.insert(0, str(KIT.parent))
    from loop import backlog_io

    rows = backlog_io.load(backlog)
    assert len(rows) == 2
    titles = {r["title"] for r in rows}
    assert titles == {"Could not find the save button", "Search box looked disabled"}
    for r in rows:
        assert r["dimension"] == "persona"
        assert r["source"] == "persona"
        assert r["status"] == "open"
        assert r["rung"] == 1
        assert r["note"] == 'task "create a note"'
        assert r["id"].startswith("ux-")

    # Running again with the same findings must not duplicate rows.
    proc2 = run(tmp_path, check, backlog=backlog)
    assert proc2.returncode == 0, proc2.stderr
    rows2 = backlog_io.load(backlog)
    assert len(rows2) == 2
    assert {r["id"] for r in rows2} == {r["id"] for r in rows}


def test_backlog_untouched_without_findings(tmp_path):
    write_json(tmp_path / "result.json", {"status": "complete", "steps": 4})
    backlog = tmp_path / "backlog.yaml"
    check = {"type": "persona", "task": "x", "max_steps": 4, "must": "complete"}
    proc = run(tmp_path, check, backlog=backlog)
    assert proc.returncode == 0, proc.stderr
    assert not backlog.exists()


def test_clean_walkthrough_closes_stale_rows_for_same_task(tmp_path):
    """A finding raised on task T is closed when a later walkthrough of T
    completes with no findings; rows for other tasks stay open."""
    sys.path.insert(0, str(KIT.parent))
    from loop import backlog_io

    first = tmp_path / "create-note-1"; first.mkdir()
    write_json(first / "result.json", {"status": "complete", "steps": 4})
    write_json(first / "findings.json", {"dead_ends": ["Could not type into Title"], "confusions": []})
    backlog = tmp_path / "backlog.yaml"
    check = {"type": "persona", "task": "create a note", "max_steps": 4, "must": "complete"}
    assert run(first, check, backlog=backlog).returncode == 0

    other = tmp_path / "search-1"; other.mkdir()
    write_json(other / "result.json", {"status": "complete", "steps": 2})
    write_json(other / "findings.json", {"dead_ends": ["Search box hidden"], "confusions": []})
    assert run(other, {"type": "persona", "task": "find a note", "max_steps": 3, "must": "complete"},
               backlog=backlog).returncode == 0
    assert {r["status"] for r in backlog_io.load(backlog)} == {"open"}

    # a stuck re-run of the same task closes nothing
    stuck = tmp_path / "create-note-2"; stuck.mkdir()
    write_json(stuck / "result.json", {"status": "stuck", "steps": 6})
    write_json(stuck / "findings.json", {"dead_ends": [], "confusions": []})
    run(stuck, check, backlog=backlog)
    assert {r["status"] for r in backlog_io.load(backlog)} == {"open"}

    # a clean re-run of the same task closes its row only
    clean = tmp_path / "create-note-3"; clean.mkdir()
    write_json(clean / "result.json", {"status": "complete", "steps": 3})
    write_json(clean / "findings.json", {"dead_ends": [], "confusions": []})
    assert run(clean, check, backlog=backlog).returncode == 0
    rows = {r["title"]: r for r in backlog_io.load(backlog)}
    assert rows["Could not type into Title"]["status"] == "done"
    assert "superseded: clean walkthrough create-note-3" in rows["Could not type into Title"]["note"]
    assert rows["Search box hidden"]["status"] == "open"
