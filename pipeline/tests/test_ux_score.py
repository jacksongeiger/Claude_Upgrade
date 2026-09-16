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
