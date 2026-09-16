"""Tests for loop/report.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest loop/tests/test_report.py -q
from the repo root.
"""
import json
import sys
from pathlib import Path

import pytest

LOOP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOOP_DIR))

import report  # noqa: E402


def write_json(path, obj):
    path.write_text(json.dumps(obj))


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


# ---------------------------------------------------------------------------
# nothing present
# ---------------------------------------------------------------------------

def test_report_with_nothing(tmp_path):
    md = report.write_report(tmp_path)

    assert "0 iterations" in md
    assert "score n/a" in md
    assert "(not started)" in md
    assert "## What changed" in md
    assert "## Needs you" in md
    assert "## Tools the loop wanted" in md
    assert "git merge --no-ff" in md
    # every optional section falls back to "none"
    assert md.count("none") >= 3

    md_path = tmp_path / ".loop" / "report.md"
    html_path = tmp_path / ".loop" / "report.html"
    assert md_path.exists() and md_path.read_text() == md
    assert html_path.exists()
    htm = html_path.read_text()
    assert "<html" in htm
    assert "not enough scored iterations" in htm


# ---------------------------------------------------------------------------
# one merged task, full trail of files
# ---------------------------------------------------------------------------

def _build_one_merged_task_project(tmp_path):
    loop_dir = tmp_path / ".loop"
    loop_dir.mkdir(parents=True)

    write_json(loop_dir / "state.json", {
        "run": "loop/2026-09-16", "cap_usd": 25, "spent_usd": 3.5, "stop_reason": "hours",
    })
    write_jsonl(loop_dir / "scores.jsonl", [
        {"iter": 0, "composite": 50.0, "outcome": "baseline"},
        {"iter": 1, "composite": 63.5, "outcome": "kept"},
    ])

    iter_dir = loop_dir / "iterations" / "1"
    iter_dir.mkdir(parents=True)
    (iter_dir / "summary.md").write_text("merged: t-014a\n")
    write_json(iter_dir / "plan.json", {
        "iter": 1, "task_ids": ["bl-014"],
        "subtasks": [{"id": "t-014a", "row": "bl-014", "goal": "add tests for ingest.parse_header"}],
    })
    task_dir = iter_dir / "tasks" / "t-014a"
    task_dir.mkdir(parents=True)
    write_json(task_dir / "review.json", {
        "id": "t-014a", "verdict": "approve", "goal_line": "1. Ship well-tested ingest code.",
        "scope_ok": True, "test_delta": {"added": 4, "removed": 0, "weakened": False},
        "score_gaming_suspected": False, "reasons": [],
    })
    shots_dir = task_dir / "shots"
    shots_dir.mkdir()
    (shots_dir / "home-1440-light.png").write_text("x")

    (loop_dir / "questions.md").write_text("Should we drop the legacy parser?\n")

    (loop_dir / "backlog.yaml").write_text(
        "rows:\n"
        "  - id: bl-020\n"
        "    title: needs a human call on licensing\n"
        "    dimension: none\n"
        "    est: S\n"
        "    source: human\n"
        "    status: needs-human\n"
        "    rung: 1\n"
        "    attempts: 0\n"
        "    iter_added: 0\n"
        '    note: ""\n'
        "  - id: bl-021\n"
        "    title: install rdx-a11y-checker\n"
        "    dimension: none\n"
        "    est: S\n"
        "    source: rdx\n"
        "    status: open\n"
        "    rung: 1\n"
        "    attempts: 0\n"
        "    iter_added: 0\n"
        '    note: ""\n'
    )
    return loop_dir


def test_report_with_one_merged_task(tmp_path):
    loop_dir = _build_one_merged_task_project(tmp_path)

    md = report.write_report(tmp_path)

    assert "loop/2026-09-16" in md
    assert "1 iterations" in md
    assert "score 50.0" in md and "63.5" in md
    assert "$3.50 of $25.00" in md
    assert "stopped: hours" in md

    assert "t-014a" in md
    assert '"1. Ship well-tested ingest code."' in md
    assert "add tests for ingest.parse_header" in md
    assert "+13.5" in md          # score delta of the iteration (63.5 - 50.0)
    assert "+4 / -0" in md        # test_delta
    assert "home-1440-light.png" in md
    assert "⚠" not in md     # no score gaming flagged

    assert "Should we drop the legacy parser?" in md
    assert "bl-020" in md
    assert "needs a human call on licensing" in md
    assert "install rdx-a11y-checker" in md

    assert "git merge --no-ff loop/2026-09-16" in md

    htm = (loop_dir / "report.html").read_text()
    assert "t-014a" in htm
    assert "home-1440-light.png" in htm
    assert "install rdx-a11y-checker" in htm
    assert "Should we drop the legacy parser?" in htm
    assert "<svg" in htm  # chart rendered — 2 scored iterations


def test_score_gaming_flag_rendered(tmp_path):
    loop_dir = _build_one_merged_task_project(tmp_path)
    review_path = loop_dir / "iterations" / "1" / "tasks" / "t-014a" / "review.json"
    review = json.loads(review_path.read_text())
    review["score_gaming_suspected"] = True
    write_json(review_path, review)

    md = report.write_report(tmp_path)
    assert "⚠" in md

    htm = (loop_dir / "report.html").read_text()
    assert "possible score gaming" in htm


# ---------------------------------------------------------------------------
# small unit checks
# ---------------------------------------------------------------------------

def test_load_backlog_rows_missing_file_returns_empty(tmp_path):
    assert report.load_backlog_rows(tmp_path / "nope.yaml") == []


def test_find_merged_tasks_multiple_ids_one_line(tmp_path):
    loop_dir = tmp_path / ".loop"
    iter_dir = loop_dir / "iterations" / "2"
    iter_dir.mkdir(parents=True)
    (iter_dir / "summary.md").write_text("merged: t-001a, t-001b\n")
    out = report.find_merged_tasks(loop_dir)
    assert [t[1] for t in out] == ["t-001a", "t-001b"]
    assert all(t[0] == 2 for t in out)


def test_render_svg_chart_needs_two_points():
    assert "not enough" in report.render_svg_chart([(0, None)])
    assert "not enough" in report.render_svg_chart([(0, 50.0)])
    svg = report.render_svg_chart([(0, 50.0), (1, 60.0)])
    assert svg.startswith("<svg")
    assert "<path" in svg


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
