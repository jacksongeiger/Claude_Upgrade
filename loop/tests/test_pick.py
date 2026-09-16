import argparse
import json
import sys
from pathlib import Path

LOOP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOOP_DIR))

import pick  # noqa: E402
import backlog_io  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def write_json(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def make_config(scorers):
    return {"version": 1, "scorers": scorers}


def make_args(tmp_path, iter_n, out_name="target.json"):
    return argparse.Namespace(
        config=str(tmp_path / "config.json"),
        backlog=str(tmp_path / "backlog.yaml"),
        scores=str(tmp_path / "scores.jsonl"),
        state=str(tmp_path / "state.json"),
        iter=iter_n,
        out=str(tmp_path / out_name),
    )


def base_row(**overrides):
    row = {
        "id": "bl-000",
        "title": "some task",
        "dimension": "tests",
        "est": "M",
        "source": "coverage-gap",
        "status": "open",
        "rung": 1,
        "attempts": 0,
        "iter_added": 0,
        "note": "",
    }
    row.update(overrides)
    return row


def setup_common(tmp_path, scorers, rows, scores_dims, state=None):
    write_json(tmp_path / "config.json", make_config(scorers))
    backlog_io.dump(rows, tmp_path / "backlog.yaml")
    write_jsonl(
        tmp_path / "scores.jsonl",
        [
            {
                "iter": 0,
                "composite": 50.0,
                "dims": scores_dims,
                "outcome": "baseline",
            }
        ],
    )
    write_json(tmp_path / "state.json", state or {})


TESTS_SCORER = {"name": "tests", "weight": 0.5, "eps": 0.5, "target": 100}
PERF_SCORER = {"name": "perf", "weight": 0.5, "eps": 1.2, "target": 100}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_picks_weakest_weighted_dimension(tmp_path):
    rows = [
        base_row(id="bl-tests", dimension="tests", est="M"),
        base_row(id="bl-perf", dimension="perf", est="M"),
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER, PERF_SCORER],
        rows,
        {"tests": {"value": 90, "ok": True}, "perf": {"value": 50, "ok": True}},
    )
    args = make_args(tmp_path, 1)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["dimension"] == "perf"
    assert target["task_ids"] == ["bl-perf"]
    assert target["mode"] == "task"


def test_small_beats_large(tmp_path):
    rows = [
        base_row(id="bl-large", dimension="tests", est="L"),
        base_row(id="bl-small", dimension="tests", est="S"),
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER],
        rows,
        {"tests": {"value": 50, "ok": True}},
    )
    args = make_args(tmp_path, 1)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["task_ids"] == ["bl-small"]


def test_frozen_rows_skipped(tmp_path):
    rows = [
        base_row(id="bl-frozen", dimension="tests", est="S", attempts=2, status="open"),
        base_row(id="bl-fresh", dimension="tests", est="L", attempts=0, status="open"),
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER],
        rows,
        {"tests": {"value": 50, "ok": True}},
    )
    args = make_args(tmp_path, 1)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    # bl-frozen has attempts>=2 -> frozen -> only bl-fresh eligible
    assert target["task_ids"] == ["bl-fresh"]

    # backlog on disk should have been normalized (rewritten) to frozen
    saved = backlog_io.load(tmp_path / "backlog.yaml")
    frozen = [r for r in saved if r["id"] == "bl-frozen"][0]
    assert frozen["status"] == "frozen"


def test_planner_rows_need_two_iters(tmp_path):
    rows = [
        base_row(
            id="bl-planner",
            dimension="tests",
            est="S",
            source="planner",
            rung=2,
            iter_added=5,
        ),
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER],
        rows,
        {"tests": {"value": 50, "ok": True}},
        state={"rung": 2},
    )

    # iter 6: 5 > 6-2=4 -> not yet eligible -> no candidates -> harvest/hypothesize
    args = make_args(tmp_path, 6)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["mode"] != "task"

    # iter 7: 5 > 7-2=5 is False -> eligible
    args = make_args(tmp_path, 7)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["mode"] == "task"
    assert target["task_ids"] == ["bl-planner"]


def test_refactor_titles_pushed_to_rung_2(tmp_path):
    rows = [
        base_row(id="bl-refactor", title="Refactor the ingest module", rung=1),
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER],
        rows,
        {"tests": {"value": 50, "ok": True}},
        state={"rung": 1},
    )
    args = make_args(tmp_path, 1)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    # bumped to rung 2, so not eligible at rung 1 -> no task chosen
    assert target["mode"] != "task"

    saved = backlog_io.load(tmp_path / "backlog.yaml")
    assert saved[0]["rung"] == 2


def test_lockout_excludes_dimension(tmp_path):
    rows = [
        base_row(id="bl-tests", dimension="tests", est="M"),
        base_row(id="bl-perf", dimension="perf", est="M"),
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER, PERF_SCORER],
        rows,
        # perf has the larger headroom, but it's locked out
        {"tests": {"value": 90, "ok": True}, "perf": {"value": 10, "ok": True}},
        state={"lockout": {"perf": 5}},
    )
    args = make_args(tmp_path, 3)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["dimension"] == "tests"
    assert target["task_ids"] == ["bl-tests"]


def test_lockout_triggered_by_low_deltas(tmp_path):
    rows = [
        base_row(id="bl-tests", dimension="tests", est="M"),
        base_row(id="bl-perf", dimension="perf", est="M"),
    ]
    picks_history = [
        {"iter": 1, "dimension": "perf", "delta_after": 0.1},
        {"iter": 2, "dimension": "perf", "delta_after": 0.2},
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER, PERF_SCORER],
        rows,
        {"tests": {"value": 90, "ok": True}, "perf": {"value": 10, "ok": True}},
        state={"picks_history": picks_history},
    )
    args = make_args(tmp_path, 3)
    lines_out = []

    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    # perf's last two picks moved it < its eps (1.2) -> locked out this iter
    assert target["dimension"] == "tests"


def test_harvest_hypothesize_stop_progression(tmp_path):
    rows = [base_row(id="bl-none", dimension="none")]

    setup_common(
        tmp_path,
        [TESTS_SCORER],
        rows,
        {"tests": {"value": 50, "ok": True}},
        state={"rung": 1},
    )
    args = make_args(tmp_path, 1)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["mode"] == "harvest"
    assert target["rung"] == 2

    write_json(tmp_path / "state.json", {"rung": 2})
    args = make_args(tmp_path, 2)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["mode"] == "hypothesize"
    assert target["rung"] == 3

    write_json(tmp_path / "state.json", {"rung": 3})
    args = make_args(tmp_path, 3)
    code = pick.run(args)
    assert code == 3
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["mode"] == "stop"


def test_dimension_none_never_picked(tmp_path):
    rows = [
        base_row(id="bl-none", dimension="none", rung=1),
        base_row(id="bl-none2", dimension="none", rung=3),
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER],
        rows,
        {"tests": {"value": 50, "ok": True}},
        state={"rung": 3},
    )
    args = make_args(tmp_path, 1)
    code = pick.run(args)
    assert code == 3
    target = json.loads((tmp_path / "target.json").read_text())
    assert target["mode"] == "stop"


def test_checkpoint_after_three_same_dimension_picks(tmp_path):
    rows = [
        base_row(id="bl-tests", dimension="tests", est="M"),
    ]
    picks_history = [
        {"iter": 1, "dimension": "tests", "delta_after": 5.0},
        {"iter": 2, "dimension": "tests", "delta_after": 5.0},
        {"iter": 3, "dimension": "tests", "delta_after": 5.0},
    ]
    setup_common(
        tmp_path,
        [TESTS_SCORER],
        rows,
        {"tests": {"value": 50, "ok": True}},
        state={"picks_history": picks_history},
    )
    args = make_args(tmp_path, 4)
    code = pick.run(args)
    assert code == 0
    target = json.loads((tmp_path / "target.json").read_text())
    assert target.get("checkpoint") is True
    assert "checkpoint_note" in target


# ---------------------------------------------------------------------------
# backlog_io round-trip
# ---------------------------------------------------------------------------

def test_backlog_io_round_trip_preserves_order_and_unknown_keys():
    rows = [
        {"id": "bl-001", "title": "has: a colon in it", "dimension": "tests",
         "est": "S", "status": "open", "rung": 1, "attempts": 0,
         "iter_added": 0, "note": "", "custom_field": "unknown-but-kept"},
        {"id": "bl-002", "title": "second row", "dimension": "perf",
         "est": "L", "status": "done", "rung": 2, "attempts": 3,
         "iter_added": 4, "note": "dimension at target"},
    ]
    text = backlog_io.dumps(rows)
    parsed = backlog_io.loads(text)
    assert parsed == rows
    assert [r["id"] for r in parsed] == ["bl-001", "bl-002"]
    assert parsed[0]["custom_field"] == "unknown-but-kept"


def test_backlog_io_tolerates_blank_lines_and_comments():
    text = """
# a top comment
rows:
  # a comment before a row
  - id: bl-001
    title: "quoted: title"

    dimension: tests
  - id: bl-002
    title: plain title
    dimension: perf
"""
    rows = backlog_io.loads(text)
    assert len(rows) == 2
    assert rows[0]["id"] == "bl-001"
    assert rows[0]["title"] == "quoted: title"
    assert rows[1]["title"] == "plain title"


def test_backlog_io_round_trip_via_file(tmp_path):
    rows = [base_row(id="bl-a"), base_row(id="bl-b", dimension="perf")]
    path = tmp_path / "backlog.yaml"
    backlog_io.dump(rows, path)
    loaded = backlog_io.load(path)
    assert loaded == rows
