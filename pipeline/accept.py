#!/usr/bin/env python3
"""accept.py — run a milestone's acceptance checks, stdlib only.

Usage:
    accept.py --spec spec.json --milestone m1 --workdir <dir>
              [--out acceptance.json] [--serve-cmd '<cmd>' --port <n>]

For every feature of --milestone (spec.json's `features` list, filtered by
`feature.milestone == --milestone`), every `acceptance` entry is run
according to its `type`:

    test       run `cmd` via `bash -c` in --workdir; ok = exit 0.
    perf       same rule as gates.py's perf check: run `cmd`, parse the
               LAST JSON-object line of stdout, compare parsed[metric]
               against max/min.
    gate       shell out to `gates.py run --check '<check>' --workdir <dir>`.
    lighthouse shell out to loop/scorers/lighthouse.py with
               {name, serve_cmd, port, urls:[base+url]} (serve_cmd/port from
               --serve-cmd/--port or spec.stack.serve.{cmd,port}); compare
               each `min` category against the scorer's raw per-category
               score. ok:false from the scorer -> infra.
    persona    shell out to ux_score.py --check '<check>' --run
               .pipeline/ux/<feature-id>-<n>/ (n = 1-based count of persona
               checks seen so far for this feature). If ux_score.py or the
               run dir's trail.json is missing: infra, detail "no persona run".
    manual     recorded as {"type":"manual","ok":null,"detail":<what>},
               never counted as failed.
    evals      run `cmd`, parse the last JSON line {"value":...}, compare to
               `min`.

Writes --out (default "acceptance.json") as
    {"milestone","features": {id: {"checks": [...], "ok": bool}}, "ok": bool}
and prints one summary line per feature.

A check whose command times out (default 600s) is infra.

Exit codes: 0 all ok · 2 any check failed · 4 any check infra (infra wins
over failed) · 1 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GATES_PY = os.path.join(HERE, "gates.py")
UX_SCORE_PY = os.path.join(HERE, "ux_score.py")
LIGHTHOUSE_PY = os.path.join(os.path.dirname(HERE), "loop", "scorers", "lighthouse.py")

DEFAULT_TIMEOUT_S = 600

LIGHTHOUSE_CATEGORY_MAP = {
    "accessibility": "a11y",
    "performance": "perf",
    "best-practices": "best",
    "best_practices": "best",
    "seo": "seo",
}


def check_result(type_, ok, detail, status):
    """status: one of 'ok', 'failed', 'infra', 'manual' — used for exit codes,
    not written to acceptance.json beyond ok/detail (kept for readability)."""
    return {"type": type_, "ok": ok, "detail": detail, "status": status}


def _last_json_line(text):
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except ValueError:
            return None
    return None


def run_test_check(check, workdir, timeout):
    cmd = check.get("cmd")
    if not cmd:
        return check_result("test", None, "missing cmd", "infra")
    try:
        proc = subprocess.run(["bash", "-c", cmd], cwd=workdir, capture_output=True,
                               text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return check_result("test", None, f"cmd timed out after {timeout}s", "infra")
    except OSError as e:
        return check_result("test", None, f"could not run cmd: {e}", "infra")
    ok = proc.returncode == 0
    tail = (proc.stderr or proc.stdout or "").strip()[-500:]
    detail = f"exit {proc.returncode}" + (f": {tail}" if tail and not ok else "")
    return check_result("test", ok, detail, "ok" if ok else "failed")


def run_perf_check(check, workdir, timeout):
    cmd = check.get("cmd")
    metric = check.get("metric")
    has_max = "max" in check
    has_min = "min" in check
    if not cmd or not metric or not (has_max or has_min):
        return check_result("perf", None, "missing cmd, metric, or max/min", "infra")
    try:
        proc = subprocess.run(["bash", "-c", cmd], cwd=workdir, capture_output=True,
                               text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return check_result("perf", None, f"cmd timed out after {timeout}s", "infra")
    except OSError as e:
        return check_result("perf", None, f"could not run cmd: {e}", "infra")

    obj = _last_json_line(proc.stdout)
    if not isinstance(obj, dict):
        return check_result("perf", None, f"last line of stdout is not a JSON object (exit {proc.returncode})", "infra")
    if metric not in obj:
        return check_result("perf", None, f"metric {metric!r} not in output {obj!r}", "infra")
    try:
        value = float(obj[metric])
    except (TypeError, ValueError):
        return check_result("perf", None, f"metric {metric!r} is not a number: {obj[metric]!r}", "infra")

    if has_max:
        bound = float(check["max"])
        ok = value <= bound
        detail = f"{metric}={value} {'<=' if ok else '>'} max {bound}"
    else:
        bound = float(check["min"])
        ok = value >= bound
        detail = f"{metric}={value} {'>=' if ok else '<'} min {bound}"
    return check_result("perf", ok, detail, "ok" if ok else "failed")


def run_gate_check(check, workdir, timeout):
    try:
        proc = subprocess.run(
            [sys.executable, GATES_PY, "run", "--check", json.dumps(check), "--workdir", workdir],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return check_result("gate", None, f"gates.py timed out after {timeout}s", "infra")
    except OSError as e:
        return check_result("gate", None, f"could not run gates.py: {e}", "infra")

    line = None
    for out_line in reversed((proc.stdout or "").splitlines()):
        out_line = out_line.strip()
        if out_line:
            line = out_line
            break
    detail = line or (proc.stderr or "").strip() or f"gates.py exited {proc.returncode}"
    if proc.returncode == 0:
        return check_result("gate", True, detail, "ok")
    if proc.returncode == 2:
        return check_result("gate", False, detail, "failed")
    # 3 (needs-baseline) and 4 (infra) both block acceptance and count as infra here.
    return check_result("gate", None, detail, "infra")


def _serve_config(check, args, spec):
    serve_cmd = args.serve_cmd
    port = args.port
    if not serve_cmd or not port:
        serve = ((spec.get("stack") or {}).get("serve")) or {}
        serve_cmd = serve_cmd or serve.get("cmd")
        port = port or serve.get("port")
    return serve_cmd, port


def run_lighthouse_check(check, workdir, timeout, args, spec):
    serve_cmd, port = _serve_config(check, args, spec)
    if not serve_cmd or not port:
        return check_result("lighthouse", None, "no serve_cmd/port (pass --serve-cmd/--port or set spec.stack.serve)", "infra")
    url_path = check.get("url", "/")
    base = f"http://localhost:{port}"
    full_url = base + url_path if url_path.startswith("/") else f"{base}/{url_path}"
    config = {"name": "lighthouse", "serve_cmd": serve_cmd, "port": port, "urls": [full_url]}
    try:
        proc = subprocess.run(
            [sys.executable, LIGHTHOUSE_PY, "--config", json.dumps(config), "--workdir", workdir],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return check_result("lighthouse", None, f"lighthouse scorer timed out after {timeout}s", "infra")
    except OSError as e:
        return check_result("lighthouse", None, f"could not run lighthouse scorer: {e}", "infra")

    obj = _last_json_line(proc.stdout)
    if not isinstance(obj, dict):
        return check_result("lighthouse", None, f"lighthouse scorer produced no JSON (exit {proc.returncode})", "infra")
    if not obj.get("ok", False):
        return check_result("lighthouse", None, f"lighthouse scorer failed: {obj.get('error')}", "infra")

    raw = obj.get("raw") or {}
    per_url = raw.get("per_url") or {}
    scores = per_url.get(full_url)
    if scores is None and len(per_url) == 1:
        scores = next(iter(per_url.values()))
    if scores is None:
        return check_result("lighthouse", None, f"no scores for {full_url} in scorer output", "infra")

    mins = check.get("min") or {}
    failures = []
    for category, min_score in mins.items():
        key = LIGHTHOUSE_CATEGORY_MAP.get(category, category)
        got = scores.get(key)
        if got is None:
            failures.append(f"{category}: no score reported")
        elif got < min_score:
            failures.append(f"{category}: {got} < {min_score}")
    ok = not failures
    detail = "all categories met their minimum" if ok else "; ".join(failures)
    return check_result("lighthouse", ok, detail, "ok" if ok else "failed")


PERSONA_RUN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona_run.py")


def _git_head(workdir):
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=workdir, capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _load_json_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class _Server:
    """Start spec.stack.serve once for all persona checks of an accept run."""

    def __init__(self):
        self.proc = None
        self.base = None

    def ensure(self, serve_cmd, port, workdir):
        if self.base or not serve_cmd or not port:
            return self.base
        import socket, time
        self.proc = subprocess.Popen(["bash", "-c", serve_cmd], cwd=workdir, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                    self.base = f"http://127.0.0.1:{port}"
                    return self.base
            except OSError:
                time.sleep(0.5)
        self.stop()
        return None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            import signal
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass
        self.proc = None


SERVER = _Server()


def run_persona_check(check, workdir, feature_id, n, timeout, args=None, spec=None):
    run_dir = os.path.join(workdir, ".pipeline", "ux", f"{feature_id}-{n}")
    trail_path = os.path.join(run_dir, "trail.json")
    if not os.path.exists(UX_SCORE_PY):
        return check_result("persona", None, "no persona run", "infra")
    # A run is reusable only if it completed and was made against this very
    # tree; a stuck run, or one from before the code changed, is stale and is
    # kept aside for the record.
    head = _git_head(workdir)
    if os.path.exists(trail_path):
        meta = _load_json_file(os.path.join(run_dir, "run.json")) or {}
        res = _load_json_file(os.path.join(run_dir, "result.json")) or {}
        if res.get("status") != "complete" or (head and meta.get("head") and meta.get("head") != head):
            import time
            os.rename(run_dir, run_dir + ".stale-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    if not os.path.exists(trail_path):
        # No walkthrough yet: do it now. The server comes from the spec; the
        # persona and judge are metered claude -p children of persona_run.py.
        if not os.path.exists(PERSONA_RUN_PY) or args is None or spec is None:
            return check_result("persona", None, "no persona run", "infra")
        serve_cmd, port = _serve_config(check, args, spec)
        base = SERVER.ensure(serve_cmd, port, workdir)
        if not base:
            return check_result("persona", None, "no persona run: server did not start (spec.stack.serve)", "infra")
        cmd = [sys.executable, PERSONA_RUN_PY, "--url", base + (check.get("url") or "/"), "--task", check["task"],
               "--max-steps", str(check.get("max_steps", 6)), "--run-dir", run_dir, "--workdir", workdir,
               "--setup", json.dumps(check.get("setup") or [])]
        if check.get("persona"):
            cmd += ["--persona", check["persona"]]
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "run.json"), "w") as f:
            json.dump({"head": head, "feature": feature_id, "task": check["task"]}, f)
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=max(timeout, 1800))
        except (subprocess.TimeoutExpired, OSError) as e:
            return check_result("persona", None, f"persona run failed: {e}", "infra")
        if not os.path.exists(trail_path) and not os.path.exists(os.path.join(run_dir, "result.json")):
            return check_result("persona", None, "no persona run: the persona produced nothing", "infra")
    try:
        proc = subprocess.run(
            [sys.executable, UX_SCORE_PY, "--check", json.dumps(check), "--run", run_dir],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return check_result("persona", None, f"ux_score.py timed out after {timeout}s", "infra")
    except OSError as e:
        return check_result("persona", None, f"could not run ux_score.py: {e}", "infra")

    obj = _last_json_line(proc.stdout)
    if not isinstance(obj, dict):
        detail = (proc.stderr or proc.stdout or f"ux_score.py exited {proc.returncode}").strip()
        return check_result("persona", None, detail, "infra")
    ok = obj.get("ok")
    if ok is None:
        ok = proc.returncode == 0
    detail = obj.get("detail") or json.dumps(obj)
    return check_result("persona", bool(ok), detail, "ok" if ok else "failed")


def run_manual_check(check):
    return check_result("manual", None, check.get("what", ""), "manual")


def run_evals_check(check, workdir, timeout):
    cmd = check.get("cmd")
    min_value = check.get("min")
    if not cmd or min_value is None:
        return check_result("evals", None, "missing cmd or min", "infra")
    try:
        proc = subprocess.run(["bash", "-c", cmd], cwd=workdir, capture_output=True,
                               text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return check_result("evals", None, f"cmd timed out after {timeout}s", "infra")
    except OSError as e:
        return check_result("evals", None, f"could not run cmd: {e}", "infra")

    obj = _last_json_line(proc.stdout)
    if not isinstance(obj, dict) or "value" not in obj:
        return check_result("evals", None, f"last line of stdout has no value (exit {proc.returncode})", "infra")
    try:
        value = float(obj["value"])
    except (TypeError, ValueError):
        return check_result("evals", None, f"value is not a number: {obj['value']!r}", "infra")
    ok = value >= float(min_value)
    detail = f"value={value} {'>=' if ok else '<'} min {min_value}"
    return check_result("evals", ok, detail, "ok" if ok else "failed")


def run_feature(feature, workdir, timeout, args, spec):
    results = []
    persona_n = 0
    skip = set((getattr(args, "skip", "") or "").split(",")) - {""}
    for check in feature.get("acceptance", []):
        ctype = check.get("type")
        if ctype in skip:
            results.append(check_result(ctype, None, "skipped (--skip)", "manual"))
        elif ctype == "test":
            results.append(run_test_check(check, workdir, timeout))
        elif ctype == "perf":
            results.append(run_perf_check(check, workdir, timeout))
        elif ctype == "gate":
            results.append(run_gate_check(check, workdir, timeout))
        elif ctype == "lighthouse":
            results.append(run_lighthouse_check(check, workdir, timeout, args, spec))
        elif ctype == "persona":
            persona_n += 1
            results.append(run_persona_check(check, workdir, feature["id"], persona_n, timeout, args, spec))
        elif ctype == "manual":
            results.append(run_manual_check(check))
        elif ctype == "evals":
            results.append(run_evals_check(check, workdir, timeout))
        else:
            results.append(check_result(ctype or "?", None, f"unknown acceptance type {ctype!r}", "infra"))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a milestone's acceptance checks.")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--milestone", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", default="acceptance.json")
    ap.add_argument("--serve-cmd", default=None)
    ap.add_argument("--skip", default="", help="comma-separated check types to record as skipped (e.g. persona,lighthouse)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = ap.parse_args(argv)

    try:
        with open(args.spec) as f:
            spec = json.load(f)
    except (OSError, ValueError) as e:
        print(f"could not read spec {args.spec}: {e}", file=sys.stderr)
        return 1

    features = [f for f in spec.get("features", []) if f.get("milestone") == args.milestone]

    out = {"milestone": args.milestone, "features": {}, "ok": True}
    any_failed = False
    any_infra = False

    for feature in features:
        fid = feature["id"]
        results = run_feature(feature, args.workdir, args.timeout, args, spec)
        n_ok = sum(1 for r in results if r["status"] == "ok")
        n_failed = sum(1 for r in results if r["status"] == "failed")
        n_infra = sum(1 for r in results if r["status"] == "infra")
        n_manual = sum(1 for r in results if r["status"] == "manual")
        feature_ok = n_failed == 0 and n_infra == 0
        if n_failed:
            any_failed = True
        if n_infra:
            any_infra = True

        checks_out = [{"type": r["type"], "ok": r["ok"], "detail": r["detail"]} for r in results]
        out["features"][fid] = {"checks": checks_out, "ok": feature_ok}

        if n_infra:
            status = "INFRA"
        elif n_failed:
            status = "FAILED"
        else:
            status = "ok"
        print(f"{fid}: {status} ({n_ok} ok, {n_failed} failed, {n_infra} infra, {n_manual} manual)")

    out["ok"] = not any_failed and not any_infra
    SERVER.stop()

    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")

    if any_infra:
        return 4
    if any_failed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
