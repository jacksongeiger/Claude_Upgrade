"""Tests for loop/check_plan.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest loop/tests/test_check_plan.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECK_PLAN = Path(__file__).resolve().parent.parent / "check_plan.py"


def run_check(args):
    proc = subprocess.run(
        [sys.executable, str(CHECK_PLAN)] + args,
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def write_json(path, data):
    path.write_text(json.dumps(data))


def base_subtask(**overrides):
    st = {
        "id": "t-1a",
        "row": "bl-1",
        "goal": "do the thing",
        "acceptance_cmd": "pytest -q",
        "owned_paths": ["a.py"],
        "hard": False,
        "model": "sonnet",
        "worktree": "/abs/.loop/wt/t-1a",
        "branch": "loop/2026-09-16/t-1a",
    }
    st.update(overrides)
    return st


def write_plan_target(plan_dir, subtasks, task_ids):
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = {
        "iter": 1,
        "task_ids": sorted({st["row"] for st in subtasks}),
        "subtasks": subtasks,
        "decisions": [],
    }
    target = {"iter": 1, "task_ids": task_ids}
    write_json(plan_dir / "plan.json", plan)
    write_json(plan_dir / "target.json", target)
    return plan_dir / "plan.json"


def init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=path, check=True)


# ---------------------------------------------------------------------------
# row-membership: subtask.row must be in target.task_ids
# ---------------------------------------------------------------------------

def test_row_in_target_pass(tmp_path):
    st = base_subtask()
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 0
    assert out.strip() == "ok"


def test_row_not_in_target_fail(tmp_path):
    st = base_subtask(row="bl-999")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "not in target.task_ids" in out


# ---------------------------------------------------------------------------
# unique ids
# ---------------------------------------------------------------------------

def test_unique_ids_pass(tmp_path):
    sts = [
        base_subtask(id="t-1a", row="bl-1", owned_paths=["a.py"]),
        base_subtask(id="t-1b", row="bl-1", owned_paths=["b.py"]),
    ]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 0


def test_duplicate_ids_fail(tmp_path):
    sts = [
        base_subtask(id="t-1a", row="bl-1", owned_paths=["a.py"]),
        base_subtask(id="t-1a", row="bl-1", owned_paths=["b.py"]),
    ]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "duplicate subtask id" in out


# ---------------------------------------------------------------------------
# fanout
# ---------------------------------------------------------------------------

def test_fanout_within_default_limit_pass(tmp_path):
    sts = [base_subtask(id=f"t-{i}", row="bl-1", owned_paths=[f"f{i}.py"]) for i in range(3)]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 0


def test_fanout_exceeded_fail(tmp_path):
    sts = [base_subtask(id=f"t-{i}", row="bl-1", owned_paths=[f"f{i}.py"]) for i in range(4)]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "exceeds max_fanout" in out


def test_fanout_custom_config(tmp_path):
    sts = [base_subtask(id=f"t-{i}", row="bl-1", owned_paths=[f"f{i}.py"]) for i in range(4)]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    config_path = tmp_path / "config.json"
    write_json(config_path, {"max_fanout": 5})
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path), "--config", str(config_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# required non-empty fields
# ---------------------------------------------------------------------------

def test_missing_goal_fail(tmp_path):
    st = base_subtask(goal="")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "missing goal" in out


def test_missing_acceptance_cmd_fail(tmp_path):
    st = base_subtask(acceptance_cmd="")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "missing acceptance_cmd" in out


def test_empty_owned_paths_fail(tmp_path):
    st = base_subtask(owned_paths=[])
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "owned_paths empty" in out


# ---------------------------------------------------------------------------
# owned_paths pairwise disjoint
# ---------------------------------------------------------------------------

def test_owned_paths_disjoint_pass(tmp_path):
    sts = [
        base_subtask(id="t-1a", row="bl-1", owned_paths=["a.py"]),
        base_subtask(id="t-1b", row="bl-1", owned_paths=["b.py"]),
    ]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 0


def test_owned_paths_exact_overlap_fail(tmp_path):
    sts = [
        base_subtask(id="t-1a", row="bl-1", owned_paths=["a.py"]),
        base_subtask(id="t-1b", row="bl-1", owned_paths=["a.py"]),
    ]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "owned_paths overlap" in out


def test_owned_paths_prefix_overlap_fail(tmp_path):
    sts = [
        base_subtask(id="t-1a", row="bl-1", owned_paths=["src"]),
        base_subtask(id="t-1b", row="bl-1", owned_paths=["src/foo.py"]),
    ]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "owned_paths overlap" in out


# ---------------------------------------------------------------------------
# reserved / absolute / escaping paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_path,expected_fragment", [
    (".loop/state.json", "reserved dir"),
    (".claude/agents/x.md", "reserved dir"),
    (".git/config", "reserved dir"),
    ("/etc/passwd", "absolute"),
    ("../outside.py", "escapes"),
])
def test_bad_owned_path_fail(tmp_path, bad_path, expected_fragment):
    st = base_subtask(owned_paths=[bad_path])
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert expected_fragment in out


def test_good_owned_path_pass(tmp_path):
    st = base_subtask(owned_paths=["discovery/rdx/ingest.py"])
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 0


# ---------------------------------------------------------------------------
# import-disjoint (map.json)
# ---------------------------------------------------------------------------

def _map_json(imports_a_to_b):
    return {
        "generated": "2026-09-16T00:00:00Z",
        "files_sha": "deadbeef",
        "modules": [
            {"path": "a.py", "loc": 10, "imports": ["b.py"] if imports_a_to_b else [], "hash": "h1", "desc": ""},
            {"path": "b.py", "loc": 10, "imports": [], "hash": "h2", "desc": ""},
        ],
    }


def test_import_disjoint_pass(tmp_path):
    sts = [
        base_subtask(id="t-1a", row="bl-1", owned_paths=["a.py"]),
        base_subtask(id="t-1b", row="bl-1", owned_paths=["b.py"]),
    ]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    map_path = tmp_path / "map.json"
    write_json(map_path, _map_json(imports_a_to_b=False))
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path), "--map", str(map_path)])
    assert rc == 0


def test_import_disjoint_fail(tmp_path):
    sts = [
        base_subtask(id="t-1a", row="bl-1", owned_paths=["a.py"]),
        base_subtask(id="t-1b", row="bl-1", owned_paths=["b.py"]),
    ]
    plan_path = write_plan_target(tmp_path, sts, task_ids=["bl-1"])
    map_path = tmp_path / "map.json"
    write_json(map_path, _map_json(imports_a_to_b=True))
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path), "--map", str(map_path)])
    assert rc == 2
    assert "import edge" in out
    assert "a.py -> b.py" in out


def test_import_disjoint_skipped_when_map_missing(tmp_path):
    st = base_subtask()
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    missing_map = tmp_path / "nope.json"
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path), "--map", str(missing_map)])
    assert rc == 0
    assert "warning" in err.lower()
    assert "map.json" in err.lower()


# ---------------------------------------------------------------------------
# hard / model
# ---------------------------------------------------------------------------

def test_hard_false_sonnet_pass(tmp_path):
    st = base_subtask(hard=False, model="sonnet")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 0


def test_hard_true_opus_pass(tmp_path):
    st = base_subtask(hard=True, model="opus")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 0


def test_hard_not_bool_fail(tmp_path):
    st = base_subtask(hard="yes", model="sonnet")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "hard must be boolean" in out


def test_model_invalid_fail(tmp_path):
    st = base_subtask(hard=False, model="haiku")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "model must be one of" in out


def test_hard_model_inconsistent_fail(tmp_path):
    st = base_subtask(hard=True, model="sonnet")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path)])
    assert rc == 2
    assert "inconsistent" in out


# ---------------------------------------------------------------------------
# defaults: repo=git toplevel, target=<plan dir>/target.json, map=<repo>/.loop/map.json
# ---------------------------------------------------------------------------

def test_defaults_resolve_pass(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    it_dir = repo / ".loop" / "iterations" / "1"
    st = base_subtask(owned_paths=["a.py"])
    plan_path = write_plan_target(it_dir, [st], task_ids=["bl-1"])
    rc, out, err = run_check([str(plan_path)])
    assert rc == 0
    assert out.strip() == "ok"


# ---------------------------------------------------------------------------
# --verify
# ---------------------------------------------------------------------------

def _setup_verify_repo(tmp_path, stray_file=False, decisions_made=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)

    subprocess.run(["git", "checkout", "-b", "feature/t-1a"], cwd=repo, check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    if stray_file:
        (repo / "stray.py").write_text("y = 2\n")
        subprocess.run(["git", "add", "stray.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "task work"], cwd=repo, check=True)

    it_dir = repo / ".loop" / "iterations" / "1"
    st = base_subtask(id="t-1a", row="bl-1", owned_paths=["a.py"])
    write_plan_target(it_dir, [st], task_ids=["bl-1"])
    task_dir = it_dir / "tasks" / "t-1a"
    task_dir.mkdir(parents=True)
    write_json(task_dir / "report.json", {
        "id": "t-1a", "status": "done", "commit": "HEAD", "branch": "feature/t-1a",
        "files": ["a.py"], "test_output_tail": "", "question": None,
        "decisions_made": decisions_made or [],
    })
    return it_dir / "plan.json"


def test_verify_pass(tmp_path):
    plan_path = _setup_verify_repo(tmp_path)
    rc, out, err = run_check(["--verify", "t-1a", str(plan_path), "--base", "main"])
    assert rc == 0
    assert out.strip() == "ok"


def test_verify_stray_file_fail(tmp_path):
    plan_path = _setup_verify_repo(tmp_path, stray_file=True)
    rc, out, err = run_check(["--verify", "t-1a", str(plan_path), "--base", "main"])
    assert rc == 2
    assert "stray file" in out
    assert "stray.py" in out


def test_verify_decisions_made_fail(tmp_path):
    plan_path = _setup_verify_repo(tmp_path, decisions_made=["chose approach X"])
    rc, out, err = run_check(["--verify", "t-1a", str(plan_path), "--base", "main"])
    assert rc == 2
    assert "decisions_made" in out


# ---------------------------------------------------------------------------
# --backlog-diff
# ---------------------------------------------------------------------------

BACKLOG_BEFORE = """rows:
  - id: bl-1
    title: "first row"
    dimension: tests
    est: S
    source: coverage-gap
    status: open
    rung: 1
    attempts: 0
    iter_added: 0
    note: ""
  - id: bl-2
    title: "second row"
    dimension: perf
    est: M
    source: bench
    status: open
    rung: 1
    attempts: 1
    iter_added: 0
    note: ""
"""

BACKLOG_AFTER_OK = """rows:
  - id: bl-1
    title: "first row"
    dimension: tests
    est: S
    source: coverage-gap
    status: done
    rung: 1
    attempts: 1
    iter_added: 0
    note: "fixed"
  - id: bl-2
    title: "second row"
    dimension: perf
    est: M
    source: bench
    status: failed
    rung: 1
    attempts: 2
    iter_added: 0
    note: "still slow"
  - id: bl-3
    title: "new row"
    dimension: none
    est: S
    source: planner
    status: open
    rung: 2
    attempts: 0
    iter_added: 1
    note: ""
"""

BACKLOG_AFTER_REORDER = """rows:
  - id: bl-2
    title: "second row"
    dimension: perf
    est: M
    source: bench
    status: open
    rung: 1
    attempts: 1
    iter_added: 0
    note: ""
  - id: bl-1
    title: "first row"
    dimension: tests
    est: S
    source: coverage-gap
    status: open
    rung: 1
    attempts: 0
    iter_added: 0
    note: ""
"""

BACKLOG_AFTER_TITLE_EDIT = """rows:
  - id: bl-1
    title: "first row, retitled"
    dimension: tests
    est: S
    source: coverage-gap
    status: open
    rung: 1
    attempts: 0
    iter_added: 0
    note: ""
  - id: bl-2
    title: "second row"
    dimension: perf
    est: M
    source: bench
    status: open
    rung: 1
    attempts: 1
    iter_added: 0
    note: ""
"""


def test_backlog_diff_status_and_append_pass(tmp_path):
    before = tmp_path / "before.yaml"
    after = tmp_path / "after.yaml"
    before.write_text(BACKLOG_BEFORE)
    after.write_text(BACKLOG_AFTER_OK)
    rc, out, err = run_check(["--backlog-diff", str(before), str(after)])
    assert rc == 0
    assert out.strip() == "ok"


def test_backlog_diff_reorder_fail(tmp_path):
    before = tmp_path / "before.yaml"
    after = tmp_path / "after.yaml"
    before.write_text(BACKLOG_BEFORE)
    after.write_text(BACKLOG_AFTER_REORDER)
    rc, out, err = run_check(["--backlog-diff", str(before), str(after)])
    assert rc == 2


def test_backlog_diff_title_edit_fail(tmp_path):
    before = tmp_path / "before.yaml"
    after = tmp_path / "after.yaml"
    before.write_text(BACKLOG_BEFORE)
    after.write_text(BACKLOG_AFTER_TITLE_EDIT)
    rc, out, err = run_check(["--backlog-diff", str(before), str(after)])
    assert rc == 2
    assert "title" in out


def test_acceptance_cmd_not_on_allowlist_rejected(tmp_path):
    """An acceptance command the executor could never run is a plan defect."""
    st = base_subtask(acceptance_cmd="git push origin main && pytest -q")
    plan_path = write_plan_target(tmp_path, [st], task_ids=["bl-1"])
    config_path = tmp_path / "config.json"
    write_json(config_path, {"max_fanout": 3, "test_cmd": "pytest -q"})
    rc, out, err = run_check([str(plan_path), "--repo", str(tmp_path), "--config", str(config_path)])
    assert rc == 2
    assert "not on the child allowlist" in out


# ---------------------------------------------------------------------------
# pinned scorer files are nobody's to own
# ---------------------------------------------------------------------------

def _pinned_config(tmp_path):
    repo = tmp_path / "repo"
    (repo / "eval").mkdir(parents=True)
    (repo / "corpora").mkdir()
    (repo / "eval" / "harness.py").write_text("x\n")
    (repo / "corpora" / "cases.yaml").write_text("x\n")
    cfg = tmp_path / "config.json"
    write_json(cfg, {"project_dir": str(repo), "max_fanout": 3,
                     "scorers": [{"name": "eval", "script": "cmd", "cmd": "python3 eval/harness.py",
                                  "pins": ["eval/harness.py", "corpora/*.yaml"]}]})
    return repo, cfg


def test_owning_a_pinned_file_fails(tmp_path):
    repo, cfg = _pinned_config(tmp_path)
    plan = write_plan_target(tmp_path / "it", [base_subtask(owned_paths=["corpora/cases.yaml"])], ["bl-1"])
    rc, out, _ = run_check([str(plan), "--config", str(cfg), "--repo", str(repo)])
    assert rc == 2 and "pinned scorer file" in out


def test_owning_a_directory_that_contains_a_pinned_file_fails(tmp_path):
    repo, cfg = _pinned_config(tmp_path)
    plan = write_plan_target(tmp_path / "it", [base_subtask(owned_paths=["eval/"])], ["bl-1"])
    rc, out, _ = run_check([str(plan), "--config", str(cfg), "--repo", str(repo)])
    assert rc == 2 and "pinned scorer file" in out


def test_owning_other_files_still_passes(tmp_path):
    repo, cfg = _pinned_config(tmp_path)
    plan = write_plan_target(tmp_path / "it", [base_subtask(owned_paths=["src/app.py"])], ["bl-1"])
    rc, out, _ = run_check([str(plan), "--config", str(cfg), "--repo", str(repo)])
    assert rc == 0, out
