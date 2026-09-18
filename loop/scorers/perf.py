#!/usr/bin/env python3
"""Nightshift perf scorer.

python3 scorers/perf.py --config '<json of the scorer's config.json entry>' --workdir <dir>

Needs, in the scorer config:
  bench_cmd   (required) command, run via `bash -c` in --workdir, that prints
              one JSON object per line to stdout, each shaped like
              {"metric": "p50_latency_ms", "value": 123.4, "unit": "ms",
               "lower_is_better": true}
  baseline    (required) {"<metric name>": <baseline value>, ...} — one entry
              per metric bench_cmd is expected to report

Optional config: `commit`, `version_label` — carried into the perf/results.jsonl
log rows below (score.py may inject these; if absent they are logged as null).

Without bench_cmd + baseline this prints
    {"ok":false,"error":"not configured: bench_cmd and baseline required",...}

Scoring, per metric present in both bench_cmd's output and `baseline`:
  ratio = baseline/now   (lower_is_better)   or   now/baseline (otherwise)
  metric_score = 100 * clip(ratio, 0.5, 1.5) / 1.5
`value` is the geometric mean of metric_score across matched metrics.

Every run that actually executes bench_cmd appends one row per reported
metric to `<workdir>/perf/results.jsonl`:
    {"ts":"...","version_label":...,"metric":"...","value":<raw now-value>,
     "unit":"...","commit":...}
so performance can be compared across versions (CLAUDE.md's versioned
performance log rule).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

TIMEOUT_S = 20 * 60


def emit(name, value, ok, error, raw):
    print(json.dumps({"name": name, "value": value, "ok": ok, "error": error, "raw": raw}))


def clip(x, lo, hi):
    return max(lo, min(hi, x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    name = "perf"
    try:
        cfg = json.loads(args.config)
        name = cfg.get("name", "perf")
        workdir = args.workdir
        bench_cmd = cfg.get("bench_cmd")
        baseline = cfg.get("baseline")

        if not bench_cmd or not baseline:
            emit(name, 0, False, "not configured: bench_cmd and baseline required", {})
            return 0

        try:
            proc = subprocess.run(
                ["bash", "-c", bench_cmd],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            emit(name, 0, False, f"bench_cmd timed out after {TIMEOUT_S}s", {})
            return 0
        except OSError as e:
            emit(name, 0, False, f"could not run bench_cmd: {e}", {})
            return 0

        reported = {}
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and "metric" in obj and "value" in obj:
                reported[obj["metric"]] = obj
            elif isinstance(obj, dict):
                # the pipeline's bench shape: {"ms_p95": 12.3, ...} — one
                # numeric key per metric, lower is better unless the config
                # says otherwise
                for k, v in obj.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        reported[k] = {"metric": k, "value": v, "unit": "",
                                       "lower_is_better": cfg.get("lower_is_better", True)}

        metric_scores = {}
        for metric, base_val in baseline.items():
            entry = reported.get(metric)
            if entry is None:
                continue
            now = entry.get("value")
            lower_is_better = entry.get("lower_is_better", True)
            try:
                if lower_is_better:
                    ratio = float(base_val) / float(now)
                else:
                    ratio = float(now) / float(base_val)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            metric_scores[metric] = 100.0 * clip(ratio, 0.5, 1.5) / 1.5

        if not metric_scores:
            emit(name, 0, False, "no metrics from bench_cmd matched baseline config", {
                "reported": list(reported.keys()),
            })
            return 0

        geomean = math.exp(sum(math.log(v) for v in metric_scores.values()) / len(metric_scores))

        # Append raw per-metric results to the versioned perf log.
        perf_dir = os.path.join(workdir, "perf")
        os.makedirs(perf_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        commit = cfg.get("commit")
        version_label = cfg.get("version_label")
        with open(os.path.join(perf_dir, "results.jsonl"), "a") as f:
            for metric, entry in reported.items():
                row = {
                    "ts": ts,
                    "version_label": version_label,
                    "metric": metric,
                    "value": entry.get("value"),
                    "unit": entry.get("unit"),
                    "commit": commit,
                }
                f.write(json.dumps(row) + "\n")

        raw = {"metrics": metric_scores, "baseline": baseline, "reported": reported}
        emit(name, geomean, True, None, raw)
        return 0
    except Exception as e:
        emit(name, 0, False, f"scorer crashed: {e}", {})
        return 0


if __name__ == "__main__":
    sys.exit(main())
