"""Tests for loop/status.py — the on-demand detail layer for the loop.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest loop/tests/test_status.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

STATUS_PY = Path(__file__).resolve().parent.parent / "status.py"


def run_status(args):
    proc = subprocess.run(
        [sys.executable, str(STATUS_PY)] + args,
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_json(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def base_state(**overrides):
    state = {
        "run": "loop/2026-09-16", "iter": 7, "phase": "EXEC",
        "cap_usd": 40, "spent_usd": 18.4, "live_spend_usd": 0.0,
        "score": 71.4, "best": 71.4, "delta": 2.1, "flat": 0, "rung": 1,
        "task": "bl-014", "agents": [], "stop_reason": None,
    }
    state.update(overrides)
    return state


def score_row(iter_n, composite, **overrides):
    row = {
        "iter": iter_n, "ts": "2026-09-16T03:00:00Z", "commit": "abc",
        "composite": composite,
        "dims": {"tests": {"value": composite, "ok": True, "raw": {}}},
        "cost_usd": 1.0, "duration_s": 100, "task_id": f"bl-{iter_n:03d}",
        "outcome": "kept", "label": f"loop-2026-09-16.{iter_n}",
    }
    row.update(overrides)
    return row


def setup_loop(tmp_path, state=None, scores=None, heartbeat=True):
    loop = tmp_path / ".loop"
    loop.mkdir()
    write_json(loop / "state.json", state if state is not None else base_state())
    if scores is not None:
        write_jsonl(loop / "scores.jsonl", scores)
    if heartbeat:
        (loop / "heartbeat").touch()
    return loop


# ---------------------------------------------------------------------------
# missing files
# ---------------------------------------------------------------------------

def test_no_loop_dir_reports_and_exits_zero(tmp_path):
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert rc == 0
    assert "not initialized" in out or ".loop" in out


def test_missing_state_json_reports_and_exits_zero(tmp_path):
    (tmp_path / ".loop").mkdir()
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert rc == 0
    assert "state.json" in out


def test_missing_scores_jsonl_reports(tmp_path):
    setup_loop(tmp_path, scores=None)
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert rc == 0
    assert "scores.jsonl" in out


# ---------------------------------------------------------------------------
# header content
# ---------------------------------------------------------------------------

def test_default_header_has_expected_fields(tmp_path):
    setup_loop(tmp_path, scores=[score_row(0, 60.0, outcome="baseline"),
                                  score_row(7, 71.4)])
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert rc == 0
    first_line = out.splitlines()[0]
    assert "●" in first_line
    assert "loop/2026-09-16" in first_line
    assert "iter 7" in first_line
    assert "EXEC" in first_line
    assert "score 71.4" in first_line
    assert "▲2.1" in first_line
    assert "best 71.4" in first_line
    assert "flat 0/3" in first_line
    assert "rung 1" in first_line
    assert "$18.40/$40.00" in first_line


def test_live_spend_shown_with_tilde(tmp_path):
    setup_loop(tmp_path, state=base_state(live_spend_usd=0.9),
               scores=[score_row(7, 71.4)])
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "$19.30~/$40.00" in out


def test_stopped_needs_human_is_amber_diamond_not_red(tmp_path):
    setup_loop(tmp_path, state=base_state(phase="STOPPED", stop_reason="needs-human"),
               scores=[score_row(7, 71.4)])
    rc, out, err = run_status(["--project", str(tmp_path)])
    first_line = out.splitlines()[0]
    assert first_line.startswith("◐")


def test_stopped_other_reason_uses_special_red_line(tmp_path):
    setup_loop(
        tmp_path,
        state=base_state(phase="STOPPED", stop_reason="regression", iter=9),
        scores=[score_row(0, 60.0, outcome="baseline"), score_row(9, 75.0)],
    )
    rc, out, err = run_status(["--project", str(tmp_path)])
    first_line = out.splitlines()[0]
    assert first_line.startswith("■")
    assert "loop STOPPED regression" in first_line
    assert "9 iters" in first_line
    assert "60.0→75.0" in first_line
    assert "$18.40/$40.00" in first_line


def test_stale_heartbeat_prefixes_warning(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)], heartbeat=False)
    hb = loop / "heartbeat"
    hb.touch()
    import os
    import time
    old = time.time() - 400
    os.utime(hb, (old, old))
    rc, out, err = run_status(["--project", str(tmp_path)])
    first_line = out.splitlines()[0]
    assert first_line.startswith("⚠")
    assert "stale" in first_line


# ---------------------------------------------------------------------------
# sparkline line
# ---------------------------------------------------------------------------

def test_sparkline_line_shows_endpoints_and_best(tmp_path):
    rows = [score_row(i, 60.0 + i) for i in range(8)]
    setup_loop(tmp_path, state=base_state(best=67.0), scores=rows)
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "score " in out
    assert "60.0 → 67.0" in out
    assert "(best 67.0)" in out


# ---------------------------------------------------------------------------
# scores table
# ---------------------------------------------------------------------------

def test_scores_table_has_dimension_and_delta_columns(tmp_path):
    rows = [score_row(0, 60.0, outcome="baseline"), score_row(1, 64.0)]
    setup_loop(tmp_path, scores=rows)
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "tests" in out
    assert "+4.0" in out
    assert "bl-001" in out


def test_scores_table_limited_to_last_12_but_delta_uses_full_history(tmp_path):
    # A distinctive jump (iter 2 -> iter 3) that appears nowhere else, so the
    # only way "+9.0" can show up is if the first *displayed* row (iter 3,
    # since the window is the last 12 of 15 rows, i.e. iters 3..14) computed
    # its delta against iter 2 from the full file rather than treating the
    # window's first row as having no predecessor.
    composites = [50.0 + i for i in range(15)]
    composites[3] = composites[2] + 9.0  # iter 2 -> iter 3 jumps by +9.0
    rows = [score_row(i, c) for i, c in enumerate(composites)]
    setup_loop(tmp_path, scores=rows)
    rc, out, err = run_status(["--project", str(tmp_path)])
    # iter 2 itself must not appear in the table (only last 12: iters 3..14)
    assert "bl-002" not in out
    assert "bl-003" in out
    assert "+9.0" in out


# ---------------------------------------------------------------------------
# next pick / --why / --tail
# ---------------------------------------------------------------------------

def test_next_pick_reads_latest_target_json(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    idir = loop / "iterations" / "7"
    idir.mkdir(parents=True)
    write_json(idir / "target.json", {
        "iter": 7, "rung": 1, "dimension": "tests", "headroom": 13.8,
        "task_ids": ["bl-014"], "lockout": [], "mode": "task",
        "reason": "tests has the largest weighted headroom",
        "candidates_considered": [],
    })
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "next pick:" in out
    assert "dimension=tests" in out
    assert "headroom=13.8" in out
    assert "rung=1" in out
    assert "mode=task" in out


def test_next_pick_uses_highest_numbered_iteration_dir(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(9, 71.4)])
    for n, dim in ((3, "perf"), (9, "tests"), (12, "evals")):
        idir = loop / "iterations" / str(n)
        idir.mkdir(parents=True)
        write_json(idir / "target.json", {
            "iter": n, "rung": 1, "dimension": dim, "headroom": 1.0,
            "task_ids": [], "lockout": [], "mode": "task",
            "reason": "r", "candidates_considered": [],
        })
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "dimension=evals" in out


def test_why_prints_reason_candidates_lockout_checkpoint(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    idir = loop / "iterations" / "7"
    idir.mkdir(parents=True)
    write_json(idir / "target.json", {
        "iter": 7, "rung": 2, "dimension": "tests", "headroom": 13.8,
        "task_ids": ["bl-014"], "lockout": ["perf"], "mode": "task",
        "reason": "tests has the largest weighted headroom (0.5 x 27.5)",
        "candidates_considered": [{"id": "bl-014", "dimension": "tests", "est": "S", "attempts": 0}],
        "checkpoint": True, "checkpoint_note": "tests picked 3x in a row",
    })
    rc, out, err = run_status(["--project", str(tmp_path), "--why"])
    assert rc == 0
    assert "largest weighted headroom" in out
    assert "bl-014" in out
    assert "perf" in out
    assert "tests picked 3x in a row" in out


def test_why_missing_target_reports(tmp_path):
    setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    rc, out, err = run_status(["--project", str(tmp_path), "--why"])
    assert rc == 0
    assert "no .loop/iterations" in out


def test_tail_reads_last_20_lines_of_stream_file(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    idir = loop / "iterations" / "7"
    idir.mkdir(parents=True)
    write_json(idir / "target.json", {"iter": 7, "mode": "task"})
    run_dir = loop / "run"
    run_dir.mkdir()
    lines = [f"line-{i}" for i in range(30)]
    (run_dir / "stream-7.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc, out, err = run_status(["--project", str(tmp_path), "--tail"])
    assert rc == 0
    assert "line-29" in out
    assert "line-9" not in out  # only the last 20 (lines 10..29)


def test_tail_falls_back_to_jsonl_stream_file(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    idir = loop / "iterations" / "7"
    idir.mkdir(parents=True)
    write_json(idir / "target.json", {"iter": 7, "mode": "task"})
    run_dir = loop / "run"
    run_dir.mkdir()
    (run_dir / "stream-7.jsonl").write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    rc, out, err = run_status(["--project", str(tmp_path), "--tail"])
    assert rc == 0
    assert '{"a":2}' in out


def test_tail_missing_stream_file_reports(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    idir = loop / "iterations" / "7"
    idir.mkdir(parents=True)
    write_json(idir / "target.json", {"iter": 7, "mode": "task"})
    rc, out, err = run_status(["--project", str(tmp_path), "--tail"])
    assert rc == 0
    assert "no stream-7" in out


# ---------------------------------------------------------------------------
# questions / events
# ---------------------------------------------------------------------------

def test_open_questions_counts_iteration_headers(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    (loop / "questions.md").write_text(
        "\n## Iteration 3 — permission stall\ndetail\n"
        "\n## Iteration 5 — checkpoint\ndetail\n",
        encoding="utf-8",
    )
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "open questions: 2" in out


def test_no_questions_file_reports(tmp_path):
    setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "no questions.md" in out


def test_events_log_tail_shown(tmp_path):
    loop = setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    events = [f"2026-09-16T0{i}:00:00Z EVENT{i}" for i in range(8)]
    (loop / "events.log").write_text("\n".join(events) + "\n", encoding="utf-8")
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "EVENT7" in out
    assert "EVENT2" not in out  # only last 5 (events 3..7)


def test_missing_events_log_reports(tmp_path):
    setup_loop(tmp_path, scores=[score_row(7, 71.4)])
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert "events.log: (missing)" in out


# ---------------------------------------------------------------------------
# exit codes always 0
# ---------------------------------------------------------------------------

def test_always_exits_zero_even_with_garbage_state(tmp_path):
    loop = tmp_path / ".loop"
    loop.mkdir()
    (loop / "state.json").write_text("{not valid json", encoding="utf-8")
    rc, out, err = run_status(["--project", str(tmp_path)])
    assert rc == 0
