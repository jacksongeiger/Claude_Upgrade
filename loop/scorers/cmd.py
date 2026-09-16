#!/usr/bin/env python3
"""Generic command scorer — the escape hatch for project-specific measurement.

Config:
    {"name": "<anything>", "cmd": "<shell command>", "weight": 0.3, "eps": 1.0,
     "target": 100, "timeout_s": 1200}

The command runs in the workdir via `bash -c` and must print, somewhere in its
stdout, ONE JSON object on its own line containing at least `"value"` (a
number 0–100, higher is better). Anything else in that object is kept as
`raw`. Any other output is ignored. Non-zero exit, timeout, or no such line
→ ok:false.

This is how a project's own eval harness, benchmark, or quality gate becomes a
scoreboard dimension in one line of config, without writing a new scorer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    a = ap.parse_args(argv)
    cfg = json.loads(a.config)
    name = cfg.get("name", "cmd")
    cmd = cfg.get("cmd")
    out = {"name": name, "value": 0.0, "ok": False, "error": None, "raw": {}}
    if not cmd:
        out["error"] = "not configured: cmd required"
        print(json.dumps(out)); return 0
    t0 = time.time()
    try:
        proc = subprocess.run(["bash", "-c", cmd], cwd=a.workdir, capture_output=True,
                              text=True, timeout=float(cfg.get("timeout_s", 1200)))
    except subprocess.TimeoutExpired:
        out["error"] = f"timeout after {cfg.get('timeout_s', 1200)}s"
        print(json.dumps(out)); return 0
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"could not run: {exc}"
        print(json.dumps(out)); return 0

    found = None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and "value" in obj:
                found = obj
                break
    if found is None:
        out["error"] = (f"no JSON line with a value field (exit {proc.returncode}); "
                        f"stderr tail: {proc.stderr[-300:]!r}")
        print(json.dumps(out)); return 0
    try:
        value = float(found["value"])
    except (TypeError, ValueError):
        out["error"] = f"value is not a number: {found.get('value')!r}"
        print(json.dumps(out)); return 0
    value = max(0.0, min(100.0, value))
    raw = {k: v for k, v in found.items() if k != "value"}
    raw["duration_s"] = round(time.time() - t0, 3)
    raw["exit"] = proc.returncode
    out.update({"value": value, "ok": True, "raw": raw})
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
