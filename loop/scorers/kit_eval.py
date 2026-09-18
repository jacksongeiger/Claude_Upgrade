#!/usr/bin/env python3
"""Nightshift kit-eval scorer — how the kit behaves, not how covered it is.

python3 scorers/kit_eval.py --config '<json>' --workdir <dir>

Runs, inside --workdir (the kit checkout the loop is improving):
  suites   the fixture-driven shell suites that drive the real drivers with a
           fake model: loop/tests/test_run.sh, test_hooks.sh, test_merge.sh,
           pipeline/tests/test_pipeline.sh, test_validate.sh
  corpus   retro/corpus_run.py, the labelled regression corpus of past mistakes
  replay   every redacted stream under retro/corpus/streams/*.jsonl through
           loop/tail.py, asserting the meter's cost agrees with the recorded
           bill within the dry-run tolerance (-10%/+35%)

value = 100 * (0.5 * suites_passing_share + 0.3 * corpus_pass_rate + 0.2 * replay_ok_share)

Deterministic and free. This is the dimension Nightshift climbs on this
repo: coverage measures the tests, this measures the kit. Config keys:
  suites   (optional) list of suite paths relative to workdir (default above)
  timeout  (optional) seconds per suite, default 900
Prints one JSON line and exits 0 per the scorer contract.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_SUITES = ["loop/tests/test_hooks.sh", "loop/tests/test_merge.sh", "loop/tests/test_run.sh",
                  "pipeline/tests/test_pipeline.sh", "pipeline/tests/test_validate.sh"]


def run(cmd, cwd, timeout, stdin=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=stdin,
                           env=dict(os.environ, CLASSIFY_OFF="1"))
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 127, "", str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    a = ap.parse_args()
    cfg = json.loads(a.config)
    wd = Path(a.workdir).resolve()
    timeout = int(cfg.get("timeout", 900))
    t0 = time.time()
    raw = {"suites": {}, "corpus": None, "replay": {}}

    suites = cfg.get("suites") or DEFAULT_SUITES
    passed = 0
    ran = 0
    for s in suites:
        path = wd / s
        if not path.exists():
            raw["suites"][s] = "missing"
            continue
        ran += 1
        rc, out, err = run(["bash", str(path)], str(wd), timeout)
        ok = rc == 0
        passed += 1 if ok else 0
        raw["suites"][s] = "ok" if ok else f"rc={rc}: {(out or err).strip().splitlines()[-1][:120] if (out or err).strip() else ''}"
    suites_share = passed / ran if ran else 0.0

    corpus_rate = 0.0
    cr = wd / "retro" / "corpus_run.py"
    if cr.exists():
        rc, out, err = run([sys.executable, str(cr), "--json"], str(wd), timeout)
        try:
            data = json.loads(out.strip().splitlines()[-1])
            corpus_rate = float(data.get("rate", 0.0))
            raw["corpus"] = {k: data[k] for k in ("passed", "failed", "skipped", "rate")}
        except (ValueError, IndexError, KeyError):
            raw["corpus"] = f"unreadable: {(err or out)[-120:]}"
    else:
        raw["corpus"] = "missing"

    replay_ok = replay_n = 0
    tail = wd / "loop" / "tail.py"
    pricing = wd / "loop" / "pricing.json"
    for stream in sorted((wd / "retro" / "corpus" / "streams").glob("*.jsonl")) if (wd / "retro" / "corpus" / "streams").exists() else []:
        replay_n += 1
        meta = stream.with_suffix(".meta.json")
        try:
            expected = float(json.loads(meta.read_text())["result_cost_usd"]) if meta.exists() else None
        except (ValueError, KeyError):
            expected = None
        with tempfile_dir() as td:
            rc, out, err = run([sys.executable, str(tail), "--state", f"{td}/state.json", "--events", f"{td}/events.jsonl",
                                "--pricing", str(pricing), "--iter", "1"], str(wd), 120, stdin=stream.read_text())
        try:
            final = json.loads(out.strip().splitlines()[-1])
            live = float(final.get("live_spend_usd") or 0)
            bill = float(final.get("result_cost_usd") or expected or 0)
            agreement = (live - bill) / bill if bill else 0.0
            ok = -0.10 <= agreement <= 0.35
        except (ValueError, IndexError):
            ok, agreement = False, None
        replay_ok += 1 if ok else 0
        raw["replay"][stream.name] = {"ok": ok, "agreement": round(agreement, 4) if agreement is not None else None}
    replay_share = replay_ok / replay_n if replay_n else 1.0

    value = 100.0 * (0.5 * suites_share + 0.3 * corpus_rate + 0.2 * replay_share)
    raw.update({"suites_share": round(suites_share, 4), "corpus_rate": round(corpus_rate, 4), "replay_share": round(replay_share, 4),
                "duration_s": round(time.time() - t0, 1)})
    print(json.dumps({"name": cfg.get("name", "kit-eval"), "value": round(value, 3), "ok": ran > 0, "error": None if ran else "no suites found", "raw": raw}))
    return 0


class tempfile_dir:
    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="kit-eval-")
        return self.d

    def __exit__(self, *a):
        shutil.rmtree(self.d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
