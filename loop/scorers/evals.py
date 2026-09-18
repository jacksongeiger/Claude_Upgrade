#!/usr/bin/env python3
"""Nightshift evals scorer.

python3 scorers/evals.py --config '<json of the scorer's config.json entry>' --workdir <dir>

Needs, in the scorer config:
  eval_cmd    (required) default command, run via `bash -c` in --workdir for
              each case (unless the case sets its own "cmd"). The case's
              "input" (if any) is piped to the command's stdin; its stdout is
              graded.
  cases_dir   (required) directory (searched recursively) of *.json case
              files. Each file is either one case object or a JSON array of
              case objects:
                {"id": "case-1", "type": "exact|regex|json_schema-lite|rubric",
                 "input": "...", "expected": ..., "cmd": "... (optional)"}
              - exact: stdout.strip() == str(expected).strip()
              - regex: re.search(expected, stdout) is not None
              - json_schema-lite: stdout parses as JSON and contains every key
                in `expected` (a list of key names, or {"required": [...]})
              - rubric: not graded here — no model calls in a deterministic
                scorer. Counted as skipped with a note, excluded from `value`.

Without eval_cmd + cases_dir this prints
    {"ok":false,"error":"not configured: eval_cmd and cases_dir required",...}

value = 100 * passed / total over deterministically-graded cases only. If no
deterministic cases are found (directory empty or only `rubric` cases),
ok:false.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

CASE_TIMEOUT_S = 120


def emit(name, value, ok, error, raw):
    print(json.dumps({"name": name, "value": value, "ok": ok, "error": error, "raw": raw}))


def load_cases(cases_dir):
    cases = []
    for path in sorted(Path(cases_dir).rglob("*.json")):
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            for i, c in enumerate(data):
                c = dict(c)
                c.setdefault("id", f"{path.stem}[{i}]")
                cases.append(c)
        elif isinstance(data, dict):
            data = dict(data)
            data.setdefault("id", path.stem)
            cases.append(data)
    return cases


def grade(case, output):
    ctype = case.get("type", "exact")
    expected = case.get("expected")
    if ctype == "exact":
        return str(output).strip() == str(expected).strip()
    if ctype == "regex":
        return re.search(expected, output, re.MULTILINE) is not None
    if ctype == "json_schema-lite":
        try:
            obj = json.loads(output)
        except ValueError:
            return False
        required = expected.get("required") if isinstance(expected, dict) else expected
        if not isinstance(required, list) or not isinstance(obj, dict):
            return False
        return all(k in obj for k in required)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    name = "evals"
    try:
        cfg = json.loads(args.config)
        name = cfg.get("name", "evals")
        workdir = args.workdir
        eval_cmd = cfg.get("eval_cmd")
        cases_dir = cfg.get("cases_dir")

        if not eval_cmd or not cases_dir:
            emit(name, 0, False, "not configured: eval_cmd and cases_dir required", {})
            return 0

        try:
            cases = load_cases(cases_dir)
        except Exception as e:
            emit(name, 0, False, f"could not read cases_dir: {e}", {})
            return 0

        results = []
        skipped = []
        for case in cases:
            cid = case.get("id", "?")
            if case.get("type") == "rubric":
                skipped.append({
                    "id": cid,
                    "note": "rubric cases require model grading; skipped by the deterministic scorer",
                })
                continue
            cmd = case.get("cmd", eval_cmd)
            try:
                proc = subprocess.run(
                    ["bash", "-c", cmd],
                    input=case.get("input", ""),
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=CASE_TIMEOUT_S,
                )
                passed = grade(case, proc.stdout)
            except (subprocess.TimeoutExpired, OSError) as e:
                passed = False
                results.append({"id": cid, "passed": False, "error": str(e)})
                continue
            results.append({"id": cid, "passed": bool(passed)})

        total = len(results)
        if total == 0:
            emit(name, 0, False, "no deterministic cases found in cases_dir", {
                "skipped": skipped,
            })
            return 0

        passed_n = sum(1 for r in results if r["passed"])
        value = 100.0 * passed_n / total
        raw = {
            "total": total,
            "passed": passed_n,
            "failed": total - passed_n,
            "failing": [r["id"] for r in results if not r["passed"]],
            "skipped": skipped,
        }
        emit(name, value, True, None, raw)
        return 0
    except Exception as e:
        emit(name, 0, False, f"scorer crashed: {e}", {})
        return 0


if __name__ == "__main__":
    sys.exit(main())
