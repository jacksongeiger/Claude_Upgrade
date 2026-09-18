"""pytest for pipeline/milestone.py. stdlib + pytest only."""

import json
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent
SCRIPT = PIPELINE / "milestone.py"
FIXTURE = PIPELINE / "tests" / "fixtures" / "spec.valid.json"


def load_valid():
    return json.loads(FIXTURE.read_text())


def write_spec(tmp_path, spec, name="spec.json"):
    p = tmp_path / name
    p.write_text(json.dumps(spec))
    return p


def run(args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        cwd=str(cwd), capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------

def test_next_no_state_gives_first_milestone(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run(["--spec", str(p), "--state", str(tmp_path / "state.json"), "next"], tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "m1"


def test_next_after_m1_done_gives_m2(tmp_path):
    p = write_spec(tmp_path, load_valid())
    state = tmp_path / "state.json"
    run(["--spec", str(p), "--state", str(state), "set", "m1", "done"], tmp_path)
    proc = run(["--spec", str(p), "--state", str(state), "next"], tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "m2"


def test_next_all_done_gives_none(tmp_path):
    p = write_spec(tmp_path, load_valid())
    state = tmp_path / "state.json"
    run(["--spec", str(p), "--state", str(state), "set", "m1", "done"], tmp_path)
    run(["--spec", str(p), "--state", str(state), "set", "m2", "done"], tmp_path)
    proc = run(["--spec", str(p), "--state", str(state), "next"], tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "none"


def test_next_blocked_milestone_is_skipped_not_picked(tmp_path):
    p = write_spec(tmp_path, load_valid())
    state = tmp_path / "state.json"
    run(["--spec", str(p), "--state", str(state), "set", "m1", "blocked"], tmp_path)
    proc = run(["--spec", str(p), "--state", str(state), "next"], tmp_path)
    assert proc.returncode == 0
    # m2 depends on m1, which is blocked (not done) -> nothing eligible
    assert proc.stdout.strip() == "none"


def test_next_dependency_cycle_exit2(tmp_path):
    spec = load_valid()
    spec["milestones"] = [
        {"id": "m1", "title": "A", "depends_on": ["m2"]},
        {"id": "m2", "title": "B", "depends_on": ["m1"]},
    ]
    p = write_spec(tmp_path, spec)
    proc = run(["--spec", str(p), "--state", str(tmp_path / "state.json"), "next"], tmp_path)
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------

def test_set_creates_state_file(tmp_path):
    p = write_spec(tmp_path, load_valid())
    state = tmp_path / "state.json"
    assert not state.exists()
    proc = run(["--spec", str(p), "--state", str(state), "set", "m1", "done", "--note", "shipped"], tmp_path)
    assert proc.returncode == 0
    data = json.loads(state.read_text())
    assert data["milestones"]["m1"]["status"] == "done"
    assert data["milestones"]["m1"]["note"] == "shipped"
    assert "ts" in data["milestones"]["m1"]


def test_set_preserves_other_milestones(tmp_path):
    p = write_spec(tmp_path, load_valid())
    state = tmp_path / "state.json"
    run(["--spec", str(p), "--state", str(state), "set", "m1", "done"], tmp_path)
    run(["--spec", str(p), "--state", str(state), "set", "m2", "blocked", "--note", "flaky"], tmp_path)
    data = json.loads(state.read_text())
    assert data["milestones"]["m1"]["status"] == "done"
    assert data["milestones"]["m2"]["status"] == "blocked"
    assert data["milestones"]["m2"]["note"] == "flaky"


def test_set_unknown_milestone_exit1(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run(["--spec", str(p), "--state", str(tmp_path / "state.json"), "set", "m-nope", "done"], tmp_path)
    assert proc.returncode == 1


def test_set_bad_status_exit_nonzero(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run(["--spec", str(p), "--state", str(tmp_path / "state.json"), "set", "m1", "maybe"], tmp_path)
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# target
# ---------------------------------------------------------------------------

def test_target_writes_task_ids_for_milestone(tmp_path):
    p = write_spec(tmp_path, load_valid())
    out = tmp_path / "target.json"
    proc = run(["--spec", str(p), "target", "m1", "--out", str(out)], tmp_path)
    assert proc.returncode == 0
    data = json.loads(out.read_text())
    assert data["milestone"] == "m1"
    assert data["task_ids"] == ["f-001"]
    assert data["mode"] == "task"
    assert data["dimension"] == "build"
    assert data["iter"] == 1
    assert "m1" in data["reason"]


def test_target_increments_iter_on_rerun_same_milestone(tmp_path):
    p = write_spec(tmp_path, load_valid())
    out = tmp_path / "target.json"
    run(["--spec", str(p), "target", "m1", "--out", str(out)], tmp_path)
    run(["--spec", str(p), "target", "m1", "--out", str(out)], tmp_path)
    data = json.loads(out.read_text())
    assert data["iter"] == 2


def test_target_resets_iter_for_different_milestone(tmp_path):
    p = write_spec(tmp_path, load_valid())
    out = tmp_path / "target.json"
    run(["--spec", str(p), "target", "m1", "--out", str(out)], tmp_path)
    run(["--spec", str(p), "target", "m2", "--out", str(out)], tmp_path)
    data = json.loads(out.read_text())
    assert data["milestone"] == "m2"
    assert data["iter"] == 1
    assert data["task_ids"] == ["f-002"]


def test_target_unknown_milestone_exit1(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run(["--spec", str(p), "target", "m-nope", "--out", str(tmp_path / "target.json")], tmp_path)
    assert proc.returncode == 1


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_prints_table_with_headers_and_rows(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run(["--spec", str(p), "list"], tmp_path)
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert lines[0].split() == ["id", "title", "depends_on", "features", "status"]
    assert any(l.startswith("m1") for l in lines)
    assert any(l.startswith("m2") for l in lines)


def test_list_reflects_state_when_given(tmp_path):
    p = write_spec(tmp_path, load_valid())
    state = tmp_path / "state.json"
    run(["--spec", str(p), "--state", str(state), "set", "m1", "done"], tmp_path)
    proc = run(["--spec", str(p), "--state", str(state), "list"], tmp_path)
    lines = proc.stdout.splitlines()
    m1_line = next(l for l in lines if l.startswith("m1"))
    assert "done" in m1_line
    m2_line = next(l for l in lines if l.startswith("m2"))
    assert "open" in m2_line


def test_list_without_state_defaults_to_open(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run(["--spec", str(p), "list"], tmp_path)
    lines = proc.stdout.splitlines()
    assert all("done" not in l and "blocked" not in l for l in lines[2:])


# ---------------------------------------------------------------------------
# the tier-3 gate for large validations
# ---------------------------------------------------------------------------

def _large_validation(tmp_path, has_tier3):
    slug = "feedbeef"
    d = tmp_path / ".pipeline" / "validate" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "verdict.json").write_text(json.dumps({"verdict": "GO", "validated_budget_usd": 500, "has_tier3": has_tier3}))
    spec = load_valid()
    spec["validation"] = {"slug": slug, "verdict_sha256": "x" * 64, "validated_budget_usd": 500}
    return write_spec(tmp_path, spec), tmp_path / ".pipeline" / "build" / "state.json"


def test_large_validation_without_tier3_refuses_a_dependent_milestone(tmp_path):
    p, state = _large_validation(tmp_path, has_tier3=False)
    assert run(["--spec", str(p), "--state", str(state), "next"], tmp_path).stdout.strip() == "m1"
    run(["--spec", str(p), "--state", str(state), "set", "m1", "done"], tmp_path)
    proc = run(["--spec", str(p), "--state", str(state), "next"], tmp_path)
    assert proc.returncode == 3 and "tier-3" in proc.stderr


def test_large_validation_with_tier3_proceeds(tmp_path):
    p, state = _large_validation(tmp_path, has_tier3=True)
    run(["--spec", str(p), "--state", str(state), "set", "m1", "done"], tmp_path)
    proc = run(["--spec", str(p), "--state", str(state), "next"], tmp_path)
    assert proc.returncode == 0 and proc.stdout.strip() == "m2"
