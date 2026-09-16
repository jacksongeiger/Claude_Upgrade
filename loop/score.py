#!/usr/bin/env python3
"""Nightshift scoring driver. See loop/README.md ("Scorer contract" and
"scores.jsonl row") for the full spec.

Score an iteration:
    python3 score.py --config <config.json> --workdir <loop worktree> \\
        --iter N --commit SHA --cost USD --duration S --task ID \\
        --outcome kept|reset-flat|reset-regressed|baseline \\
        [--out <scores.jsonl>] [--manifest <manifest.sha256>] [--dry]

Write a manifest (init.py calls this once, after writing config.json):
    python3 score.py --manifest-write <manifest path> --config <config.json>

Exit codes: 0 scored (composite non-null) · 4 infra broken (composite null,
including a manifest mismatch) · 1 bad arguments.
"""
import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone

SCORER_TIMEOUT_S = 50 * 60  # generous outer bound; scorers bound their own commands


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def enabled_scorers(config):
    return [e for e in config.get("scorers", []) if e.get("enabled", True)]


def expected_manifest(config, config_path, script_dir):
    """label -> sha256 (or None if the file is missing)."""
    expected = {}
    expected["config.json"] = sha256_file(config_path) if os.path.exists(config_path) else None
    for entry in enabled_scorers(config):
        # `script` lets a dimension be named for what it measures ("rdx-eval")
        # while running a generic scorer ("cmd").
        script = entry.get("script", entry["name"])
        label = f"scorers/{script}.py"
        full = os.path.join(script_dir, "scorers", f"{script}.py")
        expected[label] = sha256_file(full) if os.path.exists(full) else None
    return expected


def read_manifest(path):
    actual = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                digest, label = parts
                actual[label.strip()] = digest.strip()
    return actual


def verify_manifest(manifest_path, config, config_path, script_dir):
    """Returns the label of the first mismatching/missing file, or None if ok."""
    expected = expected_manifest(config, config_path, script_dir)
    try:
        actual = read_manifest(manifest_path)
    except OSError as e:
        return f"manifest ({e})"
    for label, exp_hash in expected.items():
        if exp_hash is None:
            return label
        if actual.get(label) != exp_hash:
            return label
    return None


def cmd_manifest_write(args):
    with open(args.config) as f:
        config = json.load(f)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    expected = expected_manifest(config, args.config, script_dir)
    for label, digest in expected.items():
        if digest is None:
            print(json.dumps({"ok": False, "error": f"file not found: {label}"}))
            return 1
    lines = [f"{expected[label]}  {label}" for label in sorted(expected)]
    with open(args.manifest_write, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({"ok": True, "manifest": args.manifest_write, "files": len(lines)}))
    return 0


# ---------------------------------------------------------------------------
# running scorers
# ---------------------------------------------------------------------------

def run_scorer_once(scorer_path, cfg_json, workdir):
    if not os.path.exists(scorer_path):
        return {"ok": False, "value": 0, "error": f"scorer file not found: {scorer_path}", "raw": {}}
    try:
        proc = subprocess.run(
            [sys.executable, scorer_path, "--config", cfg_json, "--workdir", workdir],
            capture_output=True, text=True, timeout=SCORER_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "value": 0, "error": "scorer subprocess timed out", "raw": {}}
    except OSError as e:
        return {"ok": False, "value": 0, "error": f"could not launch scorer: {e}", "raw": {}}

    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": False, "value": 0,
                "error": f"scorer produced no output (exit {proc.returncode}): {(proc.stderr or '')[:500]}",
                "raw": {}}
    last_line = out.splitlines()[-1]
    try:
        parsed = json.loads(last_line)
    except ValueError as e:
        return {"ok": False, "value": 0, "error": f"scorer output not valid JSON: {e}", "raw": {}}
    if not isinstance(parsed, dict) or "value" not in parsed or "ok" not in parsed:
        return {"ok": False, "value": 0, "error": "scorer output missing required fields", "raw": {}}
    if not isinstance(parsed.get("value"), (int, float)):
        parsed["value"] = 0
    return parsed


def score_one(scorer_entry, script_dir, workdir):
    name = scorer_entry["name"]
    scorer_path = os.path.join(script_dir, "scorers", f"{scorer_entry.get('script', name)}.py")
    cfg_json = json.dumps(scorer_entry)
    n_runs = max(1, int(scorer_entry.get("runs", 1)))

    results = [run_scorer_once(scorer_path, cfg_json, workdir) for _ in range(n_runs)]
    values = [r["value"] for r in results]
    ok_all = all(r.get("ok") for r in results)
    median_value = statistics.median(values)
    # representative run: whichever run's value is closest to the median
    representative = min(results, key=lambda r: abs(r["value"] - median_value))

    raw = dict(representative.get("raw") or {})
    if not ok_all:
        errors = [r.get("error") for r in results if not r.get("ok") and r.get("error")]
        if errors:
            raw["error"] = errors[0]

    return {"value": median_value, "ok": ok_all, "raw": raw}


# ---------------------------------------------------------------------------
# output location
# ---------------------------------------------------------------------------

def project_root_for(workdir):
    abs_wd = os.path.abspath(workdir)
    parts = abs_wd.split(os.sep)
    for i in range(len(parts) - 1):
        if parts[i] == ".loop" and parts[i + 1] == "wt":
            root = os.sep.join(parts[:i])
            return root or os.sep
    return abs_wd


def default_out_path(workdir):
    return os.path.join(project_root_for(workdir), ".loop", "scores.jsonl")


def append_row(out_path, row):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "a") as f:
        f.write(json.dumps(row) + "\n")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_label(iter_n, ts):
    date_part = ts.split("T")[0]
    return f"loop-{date_part}.{iter_n}"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--config")
    p.add_argument("--workdir")
    p.add_argument("--iter", type=int)
    p.add_argument("--commit")
    p.add_argument("--cost", type=float)
    p.add_argument("--duration", type=float)
    p.add_argument("--task")
    p.add_argument("--outcome", default="pending",
                   help="kept | reset-flat | reset-regressed | baseline | pending | drift-check")
    p.add_argument("--out")
    p.add_argument("--manifest")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--manifest-write")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.manifest_write:
        if not args.config:
            eprint("--manifest-write requires --config")
            return 1
        return cmd_manifest_write(args)

    required = {
        "--config": args.config, "--workdir": args.workdir, "--iter": args.iter,
        "--commit": args.commit, "--cost": args.cost, "--duration": args.duration,
        "--task": args.task, "--outcome": args.outcome,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        eprint(f"missing required arguments: {', '.join(missing)}")
        return 1

    with open(args.config) as f:
        config = json.load(f)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = args.out or default_out_path(args.workdir)
    ts = now_iso()
    label = make_label(args.iter, ts)

    if args.manifest:
        mismatch = verify_manifest(args.manifest, config, args.config, script_dir)
        if mismatch:
            print(json.dumps({"ok": False, "error": f"manifest mismatch: {mismatch}"}))
            row = {
                "iter": args.iter, "ts": ts, "commit": args.commit, "composite": None,
                "dims": {}, "cost_usd": args.cost, "duration_s": args.duration,
                "task_id": args.task, "outcome": args.outcome, "label": label,
                "infra": "manifest",
            }
            if not args.dry:
                append_row(out_path, row)
            return 4

    weights = {e["name"]: e.get("weight", 0) for e in enabled_scorers(config)}
    dims = {}
    for entry in enabled_scorers(config):
        dims[entry["name"]] = score_one(entry, script_dir, args.workdir)

    all_ok = all(d["ok"] for d in dims.values()) if dims else False
    weight_sum = sum(weights.values())
    if all_ok and weight_sum > 0:
        composite = sum(weights[name] * d["value"] for name, d in dims.items()) / weight_sum
    else:
        composite = None

    row = {
        "iter": args.iter, "ts": ts, "commit": args.commit, "composite": composite,
        "dims": dims, "cost_usd": args.cost, "duration_s": args.duration,
        "task_id": args.task, "outcome": args.outcome, "label": label,
    }

    if not args.dry:
        append_row(out_path, row)
    print(json.dumps(row))
    return 0 if composite is not None else 4


if __name__ == "__main__":
    sys.exit(main())
