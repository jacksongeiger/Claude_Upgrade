#!/usr/bin/env python3
"""Stand-in for loop/scorers/tests.py, used only by test_merge.sh while the
real scorer is being built in parallel. Same CLI contract:

    python3 fake_tests_scorer.py --config '<json>' --workdir <dir>

Prints exactly one JSON line matching the scorer contract in loop/README.md
and exits 0. The `--config` value is accepted but ignored; the fake test
result is read from a state file inside --workdir so a git merge can change
"which tests fail" just by changing tracked file content, the same way a
real test suite would change behavior across commits.

State file: <workdir>/tests_state.json => {"n_tests": N, "failing": [ids]}
Falls back to the FAILING (comma-separated) / N_TESTS env vars when the state
file is absent. OK=false / ERROR=<msg> env vars simulate a broken scorer.
"""
import argparse
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="{}")
    ap.add_argument("--workdir", default=".")
    args = ap.parse_args()

    if os.environ.get("OK", "true").lower() == "false":
        out = {"name": "tests", "value": None, "ok": False,
               "error": os.environ.get("ERROR", "simulated scorer failure"),
               "raw": None}
        print(json.dumps(out))
        return 0

    state_path = os.path.join(args.workdir, "tests_state.json")
    if os.path.isfile(state_path):
        with open(state_path) as f:
            state = json.load(f)
        n_tests = int(state.get("n_tests", 0))
        failing = list(state.get("failing", []))
    else:
        n_tests = int(os.environ.get("N_TESTS", "0"))
        failing_raw = os.environ.get("FAILING", "")
        failing = [t for t in failing_raw.split(",") if t]

    passed = max(n_tests - len(failing), 0)
    value = (100.0 * passed / n_tests) if n_tests else 0.0

    out = {
        "name": "tests",
        "value": value,
        "ok": True,
        "error": None,
        "raw": {
            "n_tests": n_tests,
            "passed": passed,
            "failed": len(failing),
            "coverage_pct": None,
            "failing": failing,
        },
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
