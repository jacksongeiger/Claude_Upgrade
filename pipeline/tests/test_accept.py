"""Tests for pipeline/accept.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest \
        pipeline/tests/test_accept.py -q -p no:cacheprovider
"""
import json
import subprocess
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1]
ACCEPT = PIPELINE_DIR / "accept.py"


def write_spec(path, features):
    spec = {
        "version": 1,
        "name": "Fixture",
        "one_liner": "test fixture",
        "stack": {"language": "python", "runtime": "python", "ui": False, "llm": False,
                   "data": "none", "deploy": {"kind": "static", "cmd": "true"}},
        "needs": [],
        "milestones": [{"id": "m1", "title": "M1", "depends_on": []}],
        "features": features,
        "success": ["It works"],
        "budget": {"build_usd": 1, "nightshift_cap_usd": 1},
        "human_gates": [],
    }
    path.write_text(json.dumps(spec))


def run_accept(spec_path, workdir, out_path, extra=None):
    args = [sys.executable, str(ACCEPT), "--spec", str(spec_path), "--milestone", "m1",
            "--workdir", str(workdir), "--out", str(out_path)]
    if extra:
        args += extra
    return subprocess.run(args, capture_output=True, text=True)


def test_all_pass(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "ok test",
         "acceptance": [{"type": "test", "cmd": "true", "must": "pass"}], "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    data = json.loads(out_path.read_text())
    assert data["ok"] is True
    assert data["milestone"] == "m1"
    assert data["features"]["f-001"]["ok"] is True
    assert data["features"]["f-001"]["checks"] == [{"type": "test", "ok": True, "detail": "exit 0"}]


def test_failed_check_exit_2(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "failing test",
         "acceptance": [{"type": "test", "cmd": "false", "must": "pass"}], "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr

    data = json.loads(out_path.read_text())
    assert data["ok"] is False
    assert data["features"]["f-001"]["ok"] is False
    assert data["features"]["f-001"]["checks"][0]["ok"] is False


def test_infra_check_exit_4_wins_over_failed(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "mixed",
         "acceptance": [
             {"type": "test", "cmd": "false", "must": "pass"},
             {"type": "gate", "name": "no-such-gate", "kind": "bogus"},
         ], "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 4, proc.stdout + proc.stderr

    data = json.loads(out_path.read_text())
    assert data["ok"] is False
    types = {c["type"] for c in data["features"]["f-001"]["checks"]}
    assert types == {"test", "gate"}


def test_perf_check(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "perf",
         "acceptance": [{"type": "perf", "cmd": "echo '{\"ms_p95\": 10}'", "metric": "ms_p95", "max": 50}],
         "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(out_path.read_text())
    assert data["features"]["f-001"]["checks"][0]["ok"] is True


def test_manual_check_never_fails(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "manual only",
         "acceptance": [{"type": "manual", "what": "feels fast enough"}], "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(out_path.read_text())
    assert data["ok"] is True
    check = data["features"]["f-001"]["checks"][0]
    assert check == {"type": "manual", "ok": None, "detail": "feels fast enough"}


def test_gate_check_infra_propagates(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    # A perf-kind gate check exercises the 'gate' acceptance type end to end
    # without needing a browser: a cmd that fails to produce JSON -> infra
    # via gates.py, which accept.py must surface as infra too.
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "gated",
         "acceptance": [{"type": "gate", "name": "g", "kind": "perf", "cmd": "echo nope",
                          "metric": "x", "max": 1}],
         "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    data = json.loads(out_path.read_text())
    assert data["features"]["f-001"]["checks"][0]["type"] == "gate"
    assert data["features"]["f-001"]["checks"][0]["ok"] is None


def test_gate_check_pass(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "gated pass",
         "acceptance": [{"type": "gate", "name": "g", "kind": "perf",
                          "cmd": "echo '{\"ms\": 5}'", "metric": "ms", "max": 10}],
         "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_persona_check_no_run_dir_is_infra(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "persona",
         "acceptance": [{"type": "persona", "task": "do the thing", "max_steps": 4, "must": "complete"}],
         "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    data = json.loads(out_path.read_text())
    check = data["features"]["f-001"]["checks"][0]
    assert check["type"] == "persona"
    assert check["ok"] is None
    assert check["detail"] == "no persona run"


def test_evals_check(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "evals",
         "acceptance": [{"type": "evals", "cmd": "echo '{\"value\": 0.9}'", "min": 0.8}],
         "priority": 1},
        {"id": "f-002", "milestone": "m1", "title": "evals fail",
         "acceptance": [{"type": "evals", "cmd": "echo '{\"value\": 0.5}'", "min": 0.8}],
         "priority": 2},
    ])
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    data = json.loads(out_path.read_text())
    assert data["features"]["f-001"]["ok"] is True
    assert data["features"]["f-002"]["ok"] is False


def test_only_milestone_features_are_run(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    spec = {
        "version": 1, "name": "Fixture", "one_liner": "x",
        "stack": {"language": "python", "runtime": "python", "ui": False, "llm": False,
                  "data": "none", "deploy": {"kind": "static", "cmd": "true"}},
        "needs": [],
        "milestones": [{"id": "m1", "title": "M1", "depends_on": []},
                        {"id": "m2", "title": "M2", "depends_on": ["m1"]}],
        "features": [
            {"id": "f-001", "milestone": "m1", "title": "in scope",
             "acceptance": [{"type": "test", "cmd": "true", "must": "pass"}], "priority": 1},
            {"id": "f-002", "milestone": "m2", "title": "out of scope",
             "acceptance": [{"type": "test", "cmd": "false", "must": "pass"}], "priority": 1},
        ],
        "success": ["ok"], "budget": {"build_usd": 1, "nightshift_cap_usd": 1}, "human_gates": [],
    }
    spec_path.write_text(json.dumps(spec))
    proc = run_accept(spec_path, tmp_path, out_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(out_path.read_text())
    assert list(data["features"].keys()) == ["f-001"]


def test_timeout_is_infra(tmp_path):
    spec_path = tmp_path / "spec.json"
    out_path = tmp_path / "acceptance.json"
    write_spec(spec_path, [
        {"id": "f-001", "milestone": "m1", "title": "slow",
         "acceptance": [{"type": "test", "cmd": "sleep 2", "must": "pass"}], "priority": 1},
    ])
    proc = run_accept(spec_path, tmp_path, out_path, extra=["--timeout", "0.2"])
    assert proc.returncode == 4, proc.stdout + proc.stderr
    data = json.loads(out_path.read_text())
    check = data["features"]["f-001"]["checks"][0]
    assert check["ok"] is None
    assert "timed out" in check["detail"]
