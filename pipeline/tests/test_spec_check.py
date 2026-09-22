"""pytest for pipeline/spec_check.py. stdlib + pytest only."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parent.parent
SCRIPT = PIPELINE / "spec_check.py"
FIXTURE = PIPELINE / "tests" / "fixtures" / "spec.valid.json"


def load_valid():
    return json.loads(FIXTURE.read_text())


def run(args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        cwd=str(cwd), capture_output=True, text=True,
    )


def write_spec(tmp_path, spec, name="spec.json"):
    p = tmp_path / name
    p.write_text(json.dumps(spec))
    return p


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_valid_spec_ok(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip().splitlines()[0] == "ok: 2 features, 2 milestones, 0 unmeasurable"


def test_unreadable_json_exit1(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text("{not json")
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 1


def test_missing_file_exit1(tmp_path):
    proc = run([str(tmp_path / "nope.json")], tmp_path)
    assert proc.returncode == 1


def test_missing_acceptance_exit2(tmp_path):
    spec = load_valid()
    spec["features"][0]["acceptance"] = []
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("no acceptance entries" in l for l in proc.stdout.splitlines())


def test_dependency_cycle_exit2(tmp_path):
    spec = load_valid()
    spec["milestones"] = [
        {"id": "m1", "title": "A", "depends_on": ["m2"]},
        {"id": "m2", "title": "B", "depends_on": ["m1"]},
    ]
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("cycle" in l for l in proc.stdout.splitlines())


def test_unknown_need_exit2(tmp_path):
    spec = load_valid()
    spec["needs"] = ["ui", "space-lasers"]
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("unknown need" in l and "space-lasers" in l for l in proc.stdout.splitlines())


def test_manual_only_feature_is_valid_and_counted(tmp_path):
    """An 'adjective' manual-only claim (unmeasurable) is still valid while
    the overall unmeasurable ratio stays <= 20%."""
    spec = load_valid()
    # Add 8 more plainly-measurable features so 1 manual-only feature out of
    # 10 total is 10%, comfortably under the 20% ceiling.
    for i in range(3, 11):
        spec["features"].append({
            "id": f"f-{i:03d}", "milestone": "m2", "title": f"Feature {i}",
            "acceptance": [{"type": "test", "cmd": f"npm test -- f{i}", "must": "pass"}],
        })
    spec["features"][1]["acceptance"] = [
        {"type": "manual", "what": "Search should feel fast"}
    ]
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip().splitlines()[0] == "ok: 10 features, 2 milestones, 1 unmeasurable"


def test_over_20_percent_unmeasurable_exit2(tmp_path):
    spec = load_valid()
    spec["features"][0]["acceptance"] = [{"type": "manual", "what": "feels snappy"}]
    spec["features"][1]["acceptance"] = [{"type": "manual", "what": "feels fast"}]
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("exceeds 20%" in l for l in proc.stdout.splitlines())


def test_two_manual_entries_in_one_feature_exit2(tmp_path):
    spec = load_valid()
    spec["features"][0]["acceptance"].append({"type": "manual", "what": "feels nice"})
    spec["features"][0]["acceptance"].append({"type": "manual", "what": "feels nice too"})
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("more than one manual" in l for l in proc.stdout.splitlines())


def test_unknown_acceptance_type_exit2(tmp_path):
    spec = load_valid()
    spec["features"][0]["acceptance"][0]["type"] = "vibes"
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("unknown type" in l for l in proc.stdout.splitlines())


def test_feature_unknown_milestone_exit2(tmp_path):
    spec = load_valid()
    spec["features"][0]["milestone"] = "m-nope"
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("does not exist" in l for l in proc.stdout.splitlines())


def test_duplicate_milestone_id_exit2(tmp_path):
    spec = load_valid()
    spec["milestones"].append({"id": "m1", "title": "dup", "depends_on": []})
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("duplicate milestone id" in l for l in proc.stdout.splitlines())


def test_success_line_too_long_exit2(tmp_path):
    spec = load_valid()
    spec["success"][0] = "x" * 121
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("exceeds 120 chars" in l for l in proc.stdout.splitlines())


def test_perf_missing_max_and_min_exit2(tmp_path):
    spec = load_valid()
    del spec["features"][1]["acceptance"][1]["max"]
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert any("needs 'max' or 'min'" in l for l in proc.stdout.splitlines())


# ---------------------------------------------------------------------------
# --derive
# ---------------------------------------------------------------------------

def test_derive_writes_goal_md(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run([str(p), "--derive"], tmp_path)
    assert proc.returncode == 0
    goal = (tmp_path / "GOAL.md").read_text()
    lines = goal.splitlines()
    assert lines[0] == "1. Every feature's acceptance passes"
    assert "" in lines
    assert "F1. f-001 — Create a note" in lines
    assert "F2. f-002 — Search notes" in lines


def test_derive_writes_backlog_rows(tmp_path):
    p = write_spec(tmp_path, load_valid())
    run([str(p), "--derive"], tmp_path)
    sys.path.insert(0, str(PIPELINE.parent / "loop"))
    import backlog_io
    rows = backlog_io.load(str(tmp_path / ".loop" / "backlog.yaml"))
    rows = rows["rows"] if isinstance(rows, dict) else rows
    ids = {r["id"] for r in rows}
    assert ids == {"spec-f-001", "spec-f-002"}
    row1 = next(r for r in rows if r["id"] == "spec-f-001")
    assert row1["dimension"] == "tests"
    assert row1["source"] == "spec"
    assert row1["status"] == "open"
    assert row1["rung"] == 1
    assert row1["attempts"] == 0
    assert row1["iter_added"] == 0
    assert row1["est"] == "S"
    assert row1["note"] == "milestone m1"


def test_derive_acceptance_index(tmp_path):
    p = write_spec(tmp_path, load_valid())
    run([str(p), "--derive"], tmp_path)
    idx = json.loads((tmp_path / ".pipeline" / "acceptance-index.json").read_text())
    assert set(idx.keys()) == {"f-001", "f-002"}
    assert idx["f-001"]["milestone"] == "m1"
    assert idx["f-001"]["checks"][0]["type"] == "test"


def test_derive_scorers_proposed(tmp_path):
    p = write_spec(tmp_path, load_valid())
    run([str(p), "--derive"], tmp_path)
    scorers = json.loads((tmp_path / ".pipeline" / "scorers.proposed.json").read_text())["scorers"]
    by_name = {s["name"]: s["weight"] for s in scorers}
    # the fixture needs "ui": /jg-ui's measured check is a scorer of its own,
    # so its `ui` backlog rows have a dimension Nightshift can climb
    assert [s["name"] for s in scorers] == ["tests", "perf", "lighthouse", "persona", "ui"]
    assert by_name["tests"] == pytest.approx(0.4)
    assert by_name["lighthouse"] == pytest.approx(0.15)
    assert by_name["persona"] == pytest.approx(0.15)
    assert by_name["perf"] == pytest.approx(0.15)
    assert by_name["ui"] == pytest.approx(0.15)
    assert sum(by_name.values()) == pytest.approx(1.0)


def test_derive_scorers_tests_only(tmp_path):
    spec = load_valid()
    spec["needs"] = ["unit-tests"]
    # needs must cover the checks, so drop the checks that imply other scorers
    for f in spec["features"]:
        f["acceptance"] = [c for c in f["acceptance"] if c["type"] in ("test", "manual")]
    p = write_spec(tmp_path, spec)
    run([str(p), "--derive"], tmp_path)
    scorers = json.loads((tmp_path / ".pipeline" / "scorers.proposed.json").read_text())["scorers"]
    assert scorers == [{"name": "tests", "weight": 1.0}]


def test_derive_spec_md_written_once(tmp_path):
    p = write_spec(tmp_path, load_valid())
    run([str(p), "--derive"], tmp_path)
    spec_md = tmp_path / "SPEC.md"
    assert spec_md.exists()
    spec_md.write_text("hand-edited prose\n")
    run([str(p), "--derive"], tmp_path)
    assert spec_md.read_text() == "hand-edited prose\n"


def test_derive_project_flag(tmp_path):
    spec_dir = tmp_path / "specdir"
    spec_dir.mkdir()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    p = write_spec(spec_dir, load_valid())
    proc = run([str(p), "--derive", "--project", str(project_dir)], tmp_path)
    assert proc.returncode == 0
    assert (project_dir / "GOAL.md").exists()
    assert not (spec_dir / "GOAL.md").exists()


def test_derive_idempotent(tmp_path):
    p = write_spec(tmp_path, load_valid())
    run([str(p), "--derive"], tmp_path)

    def snapshot():
        out = {}
        for f in sorted((tmp_path).rglob("*")):
            if f.is_file():
                out[str(f.relative_to(tmp_path))] = f.read_bytes()
        return out

    before = snapshot()
    proc = run([str(p), "--derive"], tmp_path)
    assert proc.returncode == 0
    after = snapshot()
    assert before == after


def test_derive_invalid_spec_does_not_write(tmp_path):
    spec = load_valid()
    spec["features"][0]["acceptance"] = []
    p = write_spec(tmp_path, spec)
    proc = run([str(p), "--derive"], tmp_path)
    assert proc.returncode == 2
    assert not (tmp_path / "GOAL.md").exists()


def test_derive_no_duplicate_backlog_rows_when_row_preexists(tmp_path):
    p = write_spec(tmp_path, load_valid())
    loop_dir = tmp_path / ".loop"
    loop_dir.mkdir()
    (loop_dir / "backlog.yaml").write_text(
        'rows:\n  - id: spec-f-001\n    title: "Create a note"\n    dimension: tests\n'
        '    est: S\n    source: spec\n    status: open\n    rung: 1\n'
        '    attempts: 0\n    iter_added: 0\n    note: "milestone m1"\n'
    )
    run([str(p), "--derive"], tmp_path)
    sys.path.insert(0, str(PIPELINE.parent / "loop"))
    import backlog_io
    rows = backlog_io.load(str(loop_dir / "backlog.yaml"))
    rows = rows["rows"] if isinstance(rows, dict) else rows
    ids = [r["id"] for r in rows]
    assert ids.count("spec-f-001") == 1
    assert "spec-f-002" in ids


def test_serve_required_for_persona_and_needs_must_cover_checks(tmp_path):
    spec = load_valid()
    del spec["stack"]["serve"]
    spec["needs"] = ["ui", "unit-tests"]
    p = write_spec(tmp_path, spec)
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 2
    assert "stack.serve" in proc.stdout
    assert "missing 'persona'" in proc.stdout and "missing 'lighthouse'" in proc.stdout


# ---------------------------------------------------------------------------
# the validation gate
# ---------------------------------------------------------------------------

def _verdict(tmp_path, spec, verdict="GO", budget=50, overruled=None):
    import hashlib
    slug = "abcd1234"
    d = tmp_path / ".pipeline" / "validate" / slug
    d.mkdir(parents=True, exist_ok=True)
    v = {"verdict": verdict, "validated_budget_usd": budget, "slug": slug, "overruled": overruled}
    (d / "verdict.json").write_text(json.dumps(v))
    spec["validation"] = {"slug": slug, "verdict_sha256": hashlib.sha256((d / "verdict.json").read_bytes()).hexdigest(),
                          "validated_budget_usd": budget}
    return spec


def test_spec_without_validation_block_is_reported_not_failed(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run([str(p)], tmp_path)
    assert proc.returncode == 0 and "no validation block" in proc.stdout


def test_gate_flag_refuses_without_a_verdict(tmp_path):
    p = write_spec(tmp_path, load_valid())
    proc = run([str(p), "--gate-validate"], tmp_path)
    assert proc.returncode == 3 and "run /jg-validate" in proc.stdout


def test_go_verdict_passes_the_gate(tmp_path):
    spec = _verdict(tmp_path, load_valid())
    p = write_spec(tmp_path, spec)
    proc = run([str(p), "--gate-validate"], tmp_path)
    assert proc.returncode == 0 and "validated GO" in proc.stdout


def test_no_go_verdict_without_overrule_refuses(tmp_path):
    spec = _verdict(tmp_path, load_valid(), verdict="NO-GO")
    p = write_spec(tmp_path, spec)
    assert run([str(p)], tmp_path).returncode == 3  # the block is present, so it is enforced even without the flag


def test_overruled_no_go_passes(tmp_path):
    spec = _verdict(tmp_path, load_valid(), verdict="NO-GO", overruled={"by": "human", "reason": "x"})
    p = write_spec(tmp_path, spec)
    assert run([str(p), "--gate-validate"], tmp_path).returncode == 0


def test_budget_outgrowing_the_verdict_is_exit_2(tmp_path):
    spec = _verdict(tmp_path, load_valid(), budget=15)
    spec["budget"] = {"build_usd": 40, "nightshift_cap_usd": 10}
    p = write_spec(tmp_path, spec)
    proc = run([str(p), "--gate-validate"], tmp_path)
    assert proc.returncode == 2 and "exceeds the validated budget" in proc.stdout


def test_changed_verdict_is_caught_by_the_sha(tmp_path):
    spec = _verdict(tmp_path, load_valid())
    vpath = tmp_path / ".pipeline" / "validate" / "abcd1234" / "verdict.json"
    v = json.loads(vpath.read_text()); v["verdict"] = "GO"; v["note"] = "edited"; vpath.write_text(json.dumps(v))
    p = write_spec(tmp_path, spec)
    proc = run([str(p), "--gate-validate"], tmp_path)
    assert proc.returncode == 3 and "changed since the spec recorded it" in proc.stdout


def test_skipped_validation_is_allowed_and_named(tmp_path):
    spec = load_valid(); spec["validation"] = {"skipped": True, "by": "human"}
    p = write_spec(tmp_path, spec)
    proc = run([str(p), "--gate-validate"], tmp_path)
    assert proc.returncode == 0 and "skipped by human" in proc.stdout
