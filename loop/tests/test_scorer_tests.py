import json
import os
import subprocess
import sys

SCORER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scorers", "tests.py")


def run_scorer(cfg, workdir):
    proc = subprocess.run(
        [sys.executable, SCORER, "--config", json.dumps(cfg), "--workdir", str(workdir)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"expected one JSON line, got: {proc.stdout!r}"
    return json.loads(lines[0])


def write_script(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


def test_pytest_style_pass_and_fail(tmp_path):
    script = tmp_path / "run_pytest.sh"
    write_script(script, """
cat <<'EOF'
============================= test session starts ==============================
collected 6 items

tests/test_a.py ....                                                    [ 66%]
tests/test_x.py F                                                        [ 83%]
tests/test_y.py E                                                        [100%]

=================================== FAILURES =====================================
_________________________________ test_y _________________________________________

=========================== short test summary info ==============================
FAILED tests/test_x.py::test_y
========================= 4 passed, 1 failed, 1 error in 0.42s ===================
EOF
exit 1
""")
    out = run_scorer({"name": "tests", "cmd": f"bash {script}"}, tmp_path)
    assert out["ok"] is True
    assert out["raw"]["n_tests"] == 6
    assert out["raw"]["passed"] == 4
    assert out["raw"]["failed"] == 2  # 1 failed + 1 error
    assert out["raw"]["failing"] == ["tests/test_x.py::test_y"]
    assert abs(out["value"] - (100.0 * 4 / 6)) < 1e-6
    assert out["raw"]["coverage_pct"] is None


def test_jest_style(tmp_path):
    script = tmp_path / "run_jest.sh"
    write_script(script, """
cat <<'EOF'
 FAIL  src/foo.test.js
  ✕ does the thing

Tests:       1 failed, 12 passed, 13 total
Time:        1.234 s
EOF
exit 1
""")
    out = run_scorer({"name": "tests", "cmd": f"bash {script}"}, tmp_path)
    assert out["ok"] is True
    assert out["raw"]["n_tests"] == 13
    assert out["raw"]["passed"] == 12
    assert out["raw"]["failed"] == 1
    assert abs(out["value"] - (100.0 * 12 / 13)) < 1e-6


def test_go_style(tmp_path):
    script = tmp_path / "run_go.sh"
    write_script(script, """
cat <<'EOF'
--- PASS: TestA (0.00s)
--- PASS: TestB (0.00s)
--- FAIL: TestC (0.00s)
FAIL
FAIL	example.com/pkg	0.010s
EOF
exit 1
""")
    out = run_scorer({"name": "tests", "cmd": f"bash {script}"}, tmp_path)
    assert out["ok"] is True
    assert out["raw"]["n_tests"] == 3
    assert out["raw"]["passed"] == 2
    assert out["raw"]["failed"] == 1
    assert out["raw"]["failing"] == ["TestC"]


def test_generic_fallback_success(tmp_path):
    out = run_scorer({"name": "tests", "cmd": "exit 0"}, tmp_path)
    assert out["ok"] is True
    assert out["value"] == 100.0
    assert out["raw"]["n_tests"] is None


def test_generic_fallback_failure(tmp_path):
    out = run_scorer({"name": "tests", "cmd": "exit 1"}, tmp_path)
    assert out["ok"] is True
    assert out["value"] == 0.0
    assert out["raw"]["n_tests"] is None


def test_no_cmd_configured(tmp_path):
    out = run_scorer({"name": "tests"}, tmp_path)
    assert out["ok"] is False
    assert out["value"] == 0
    assert "cmd" in out["error"]


def test_coverage_pytest_cov_json(tmp_path):
    cov_file = tmp_path / "coverage.json"
    cov_file.write_text(json.dumps({"totals": {"percent_covered": 80.0}}))
    script = tmp_path / "run_pytest.sh"
    write_script(script, """
cat <<'EOF'
10 passed in 0.10s
EOF
exit 0
""")
    out = run_scorer({
        "name": "tests",
        "cmd": f"bash {script}",
        "coverage_cmd": "true",
        "coverage_file": "coverage.json",
    }, tmp_path)
    assert out["ok"] is True
    assert out["raw"]["coverage_pct"] == 80.0
    expected = 100.0 * 1.0 * (0.5 + 0.5 * 80.0 / 100.0)
    assert abs(out["value"] - expected) < 1e-6


def test_coverage_jest_summary_json(tmp_path):
    cov_file = tmp_path / "coverage-summary.json"
    cov_file.write_text(json.dumps({"total": {"lines": {"pct": 55.5}}}))
    script = tmp_path / "run_jest.sh"
    write_script(script, """
cat <<'EOF'
Tests:       10 passed, 10 total
EOF
exit 0
""")
    out = run_scorer({
        "name": "tests",
        "cmd": f"bash {script}",
        "coverage_cmd": "true",
        "coverage_file": "coverage-summary.json",
    }, tmp_path)
    assert out["ok"] is True
    assert out["raw"]["coverage_pct"] == 55.5


def test_coverage_go_profile(tmp_path):
    cov_file = tmp_path / "cover.out"
    cov_file.write_text(
        "mode: set\n"
        "pkg/a.go:1.1,3.2 4 1\n"
        "pkg/a.go:5.1,7.2 6 0\n"
    )
    script = tmp_path / "run_go.sh"
    write_script(script, """
cat <<'EOF'
--- PASS: TestA (0.00s)
ok  	example.com/pkg	0.010s
EOF
exit 0
""")
    out = run_scorer({
        "name": "tests",
        "cmd": f"bash {script}",
        "coverage_cmd": "true",
        "coverage_file": "cover.out",
    }, tmp_path)
    assert out["ok"] is True
    # covered=4, total=10 -> 40%
    assert abs(out["raw"]["coverage_pct"] - 40.0) < 1e-6


def test_scorer_always_exits_zero_even_on_bad_config(tmp_path):
    proc = subprocess.run(
        [sys.executable, SCORER, "--config", "not json", "--workdir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False


def test_vitest_style_counts_broken_files_as_failures(tmp_path):
    script = tmp_path / "run_vitest.sh"
    write_script(script, "cat <<'EOF'\n"
                 " FAIL  tests/json-test-suite.ts [ tests/json-test-suite.ts ]\n"
                 "Error: ENOENT: no such file or directory\n"
                 " Test Files  2 failed | 21 passed (23)\n"
                 "      Tests  965 passed (965)\n"
                 "   Duration  3.47s\n"
                 "EOF\nexit 1\n")
    out = run_scorer({"name": "tests", "cmd": f"bash {script}"}, tmp_path)
    assert out["ok"] is True
    assert out["raw"]["passed"] == 965
    assert out["raw"]["failed"] == 2
    assert out["raw"]["n_tests"] == 967
    assert "tests/json-test-suite.ts" in out["raw"]["failing"]
    assert out["value"] < 100.0


def test_configured_coverage_that_is_unreadable_is_an_infra_failure(tmp_path):
    script = tmp_path / "run.sh"
    write_script(script, "echo 'Tests: 10 passed, 10 total'\nexit 0\n")
    out = run_scorer({"name": "tests", "cmd": f"bash {script}", "coverage_cmd": f"bash {script}",
                      "coverage_file": "coverage/coverage-summary.json"}, tmp_path)
    assert out["ok"] is False
    assert "missing or unreadable" in out["error"]
    assert out["raw"]["passed"] == 10
