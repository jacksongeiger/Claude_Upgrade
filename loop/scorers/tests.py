#!/usr/bin/env python3
"""Nightshift tests scorer.

python3 scorers/tests.py --config '<json of the scorer's config.json entry>' --workdir <dir>

Config keys used here:
  cmd            (required) test command, run via `bash -c` in --workdir
  coverage_cmd   (optional) coverage command, run the same way after cmd
  coverage_file  (optional) path (relative to --workdir, or absolute) to read
                 coverage from after coverage_cmd succeeds

Prints exactly one JSON line and always exits 0, per the Scorer contract in
loop/README.md:
    {"name":"tests","value":72.5,"ok":true,"error":null,"raw":{...}}

value = 100 * pass_rate * (0.5 + 0.5 * coverage_pct/100) when coverage is
available, else 100 * pass_rate. raw always includes n_tests, passed, failed,
coverage_pct (or null), failing (list of test ids), duration_s.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

TIMEOUT_S = 20 * 60


def _run(cmd, workdir):
    """Run `cmd` via bash -c in workdir. Returns (returncode, stdout, stderr, wall_s)."""
    start = time.time()
    proc = subprocess.run(
        ["bash", "-c", cmd],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )
    wall = time.time() - start
    return proc.returncode, proc.stdout or "", proc.stderr or "", wall


# ---------------------------------------------------------------------------
# Output parsers. Each returns None if its format wasn't recognized, else a
# dict with n_tests, passed, failed, failing.
# ---------------------------------------------------------------------------

def _parse_pytest(text):
    passed = failed = errors = None
    for line in text.splitlines():
        m_p = re.search(r"(\d+)\s+passed", line)
        m_f = re.search(r"(\d+)\s+failed", line)
        m_e = re.search(r"(\d+)\s+error", line)
        if m_p or m_f or m_e:
            if m_p:
                passed = int(m_p.group(1))
            if m_f:
                failed = int(m_f.group(1))
            if m_e:
                errors = int(m_e.group(1))
    if passed is None and failed is None and errors is None:
        return None
    passed = passed or 0
    failed = (failed or 0) + (errors or 0)
    failing = re.findall(r"^FAILED\s+(\S+)", text, re.MULTILINE)
    return {"n_tests": passed + failed, "passed": passed, "failed": failed, "failing": failing}


def _parse_jest(text):
    m = re.search(r"^\s*Tests:?\s+(.+)$", text, re.MULTILINE)
    if not m:
        return None
    segment = m.group(1).replace("|", ",")
    counts = {}
    for part in segment.split(","):
        part = part.strip()
        mm = re.match(r"(\d+)\s+(\w+)", part)
        if mm:
            counts[mm.group(2).lower()] = int(mm.group(1))
    if not counts:
        return None
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    # vitest/jest report a test FILE that could not run ("Test Files 2 failed")
    # separately from test counts; a broken file is a failure, not a pass.
    mf = re.search(r"^\s*Test (?:Files|Suites):?\s+(.+)$", text, re.MULTILINE)
    if mf:
        mm = re.search(r"(\d+)\s+failed", mf.group(1))
        if mm:
            failed += int(mm.group(1))
    n_tests = counts.get("total", passed + failed + counts.get("skipped", 0))
    failing = re.findall(r"^\s*(?:✕|✗)\s+(.+)$", text, re.MULTILINE)
    if not failing:
        failing = re.findall(r"^\s*FAIL\s+(\S+)", text, re.MULTILINE)
    return {"n_tests": n_tests, "passed": passed, "failed": failed, "failing": failing}


def _parse_go(text):
    fails = re.findall(r"^--- FAIL:\s+(\S+)", text, re.MULTILINE)
    passes = re.findall(r"^--- PASS:\s+(\S+)", text, re.MULTILINE)
    if fails or passes:
        return {
            "n_tests": len(fails) + len(passes),
            "passed": len(passes),
            "failed": len(fails),
            "failing": fails,
        }
    ok_pkgs = re.findall(r"^ok\s+(\S+)", text, re.MULTILINE)
    fail_pkgs = re.findall(r"^FAIL\s+(\S+)", text, re.MULTILINE)
    if ok_pkgs or fail_pkgs:
        return {
            "n_tests": len(ok_pkgs) + len(fail_pkgs),
            "passed": len(ok_pkgs),
            "failed": len(fail_pkgs),
            "failing": fail_pkgs,
        }
    return None


def parse_output(text):
    # jest/vitest summaries ("Tests: 1 failed, 12 passed" / "Test Files 2 failed")
    # also satisfy the looser pytest regexes, so the specific parser goes first
    # when its signature is present.
    order = (_parse_pytest, _parse_jest, _parse_go)
    if re.search(r"^\s*(?:Tests:|Test (?:Files|Suites))\s", text, re.MULTILINE):
        order = (_parse_jest, _parse_pytest, _parse_go)
    for parser in order:
        result = parser(text)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def read_coverage_pct(workdir, coverage_file):
    path = coverage_file
    if not os.path.isabs(path):
        path = os.path.join(workdir, path)
    if not os.path.exists(path):
        return None
    try:
        if path.endswith(".json"):
            with open(path) as f:
                data = json.load(f)
            totals = data.get("totals") if isinstance(data, dict) else None
            if isinstance(totals, dict) and "percent_covered" in totals:
                return float(totals["percent_covered"])
            total = data.get("total") if isinstance(data, dict) else None
            if isinstance(total, dict):
                lines = total.get("lines")
                if isinstance(lines, dict) and "pct" in lines:
                    return float(lines["pct"])
            return None
        # go coverprofile text format:
        #   mode: set
        #   pkg/file.go:10.2,12.3 2 1
        covered = 0
        total_stmts = 0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("mode:"):
                    continue
                parts = line.rsplit(" ", 2)
                if len(parts) != 3:
                    continue
                _name, numstmt_s, count_s = parts
                try:
                    numstmt = int(numstmt_s)
                    count = int(count_s)
                except ValueError:
                    continue
                total_stmts += numstmt
                if count > 0:
                    covered += numstmt
        if total_stmts == 0:
            return None
        return 100.0 * covered / total_stmts
    except Exception:
        return None


def emit(name, value, ok, error, raw):
    print(json.dumps({"name": name, "value": value, "ok": ok, "error": error, "raw": raw}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    name = "tests"
    try:
        cfg = json.loads(args.config)
        name = cfg.get("name", "tests")
        workdir = args.workdir
        cmd = cfg.get("cmd")
        if not cmd:
            emit(name, 0, False, "no cmd configured", {
                "n_tests": None, "passed": None, "failed": None,
                "coverage_pct": None, "failing": [], "duration_s": None,
            })
            return 0

        try:
            returncode, stdout, stderr, wall = _run(cmd, workdir)
        except subprocess.TimeoutExpired:
            emit(name, 0, False, f"cmd timed out after {TIMEOUT_S}s", {
                "n_tests": None, "passed": None, "failed": None,
                "coverage_pct": None, "failing": [], "duration_s": TIMEOUT_S,
            })
            return 0
        except OSError as e:
            emit(name, 0, False, f"could not run cmd: {e}", {
                "n_tests": None, "passed": None, "failed": None,
                "coverage_pct": None, "failing": [], "duration_s": None,
            })
            return 0

        combined = stdout + "\n" + stderr
        parsed = parse_output(combined)
        if parsed is not None and parsed["n_tests"]:
            passed = parsed["passed"]
            failed = parsed["failed"]
            n_tests = parsed["n_tests"]
            failing = parsed["failing"]
            pass_rate = passed / n_tests if n_tests else (1.0 if returncode == 0 else 0.0)
        else:
            # generic fallback: exit code 0 means all passed, counts unknown
            n_tests = None
            passed = None
            failed = None
            failing = []
            pass_rate = 1.0 if returncode == 0 else 0.0

        coverage_pct = None
        coverage_cmd = cfg.get("coverage_cmd")
        coverage_file = cfg.get("coverage_file")
        if coverage_cmd and coverage_file:
            try:
                cov_rc, _cov_out, _cov_err, cov_wall = _run(coverage_cmd, workdir)
                wall += cov_wall
                if cov_rc == 0:
                    coverage_pct = read_coverage_pct(workdir, coverage_file)
            except (subprocess.TimeoutExpired, OSError):
                coverage_pct = None

        if coverage_pct is not None:
            value = 100.0 * pass_rate * (0.5 + 0.5 * coverage_pct / 100.0)
        else:
            value = 100.0 * pass_rate

        raw = {
            "n_tests": n_tests,
            "passed": passed,
            "failed": failed,
            "coverage_pct": coverage_pct,
            "failing": failing,
            "duration_s": wall,
        }
        emit(name, value, True, None, raw)
        return 0
    except Exception as e:
        emit(name, 0, False, f"scorer crashed: {e}", {
            "n_tests": None, "passed": None, "failed": None,
            "coverage_pct": None, "failing": [], "duration_s": None,
        })
        return 0


if __name__ == "__main__":
    sys.exit(main())
