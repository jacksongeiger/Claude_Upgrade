#!/usr/bin/env python3
"""ux_score.py — score one persona run, and optionally feed its findings to
the backlog.

Usage:
    ux_score.py --run <run_dir> --check '<persona acceptance json>' [--backlog <path>]

<run_dir> must contain result.json, written by pipeline/js/persona_driver.cjs:
    {"status": "complete"|"stuck"|"abandoned"|"error", "steps": N}
<run_dir>/judge.json is read if present (written by agents/persona-judge.md
against pipeline/prompts/rubric.md):
    {"scores": {...}, "total": 0-10, "evidence": [...]}

<persona acceptance json> is one entry of a feature's `acceptance` list in
spec.json:
    {"type": "persona", "task": "...", "max_steps": N, "must": "complete"}

Writes <run_dir>/score.json and prints the same object as one JSON line:
    {"value", "completed", "steps", "max_steps", "step_ratio", "judged", "judge_total"}

    completed  = status == "complete" and steps <= max_steps * 1.5
    step_ratio = min(1, max_steps / steps)
    value      = 100 * (0.5*completed + 0.3*step_ratio + 0.2*judge_total/10)
                 when judge.json is present (judged: true), else
               = 100 * (0.625*completed + 0.375*step_ratio)  (judged: false)

With --backlog <path>, reads <run_dir>/findings.json if present
(written by the persona agent: {"dead_ends": [...], "confusions": [...]})
and appends one backlog row per finding title, deduplicated by id:
    id: "ux-" + first 8 hex of sha256(title)
    title: <the finding text>
    dimension: persona
    source: persona
    status: open
    rung: 1
    est: S
    attempts: 0
    iter_added: 0
    note: 'task "<the check's task>"'

Exit codes: 0 scored, the check's `must` is met · 2 scored, `must` is not met
(score.json is still written) · 4 result.json is missing · 1 usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import backlog_io  # noqa: E402


def compute_score(result: dict, check: dict, judge: dict | None) -> dict:
    status = result.get("status")
    steps = result.get("steps") or 0
    max_steps = check.get("max_steps") or 1

    completed = bool(status == "complete" and steps <= max_steps * 1.5)
    step_ratio = min(1.0, max_steps / steps) if steps > 0 else 0.0

    if judge is not None:
        judge_total = judge.get("total", 0)
        value = 100.0 * (0.5 * completed + 0.3 * step_ratio + 0.2 * (judge_total / 10.0))
        judged = True
    else:
        judge_total = None
        value = 100.0 * (0.625 * completed + 0.375 * step_ratio)
        judged = False

    return {
        "value": value,
        "completed": completed,
        "steps": steps,
        "max_steps": max_steps,
        "step_ratio": step_ratio,
        "judged": judged,
        "judge_total": judge_total,
    }


def feed_backlog(run_dir: str, backlog_path: str, task: str) -> int:
    """Append dead_ends/confusions from <run_dir>/findings.json to the
    backlog, deduplicated by id. Returns the number of rows added."""
    findings_path = os.path.join(run_dir, "findings.json")
    if not os.path.exists(findings_path):
        return 0
    with open(findings_path, "r", encoding="utf-8") as f:
        findings = json.load(f)

    titles = list(findings.get("dead_ends") or []) + list(findings.get("confusions") or [])
    if not titles:
        return 0

    rows = backlog_io.load(backlog_path) if os.path.exists(backlog_path) else []
    existing_ids = {row.get("id") for row in rows}

    added = 0
    for title in titles:
        title = str(title)
        row_id = "ux-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:8]
        if row_id in existing_ids:
            continue
        rows.append({
            "id": row_id,
            "title": title,
            "dimension": "persona",
            "source": "persona",
            "status": "open",
            "rung": 1,
            "est": "S",
            "attempts": 0,
            "iter_added": 0,
            "note": f'task "{task}"',
        })
        existing_ids.add(row_id)
        added += 1

    if added:
        backlog_io.dump(rows, backlog_path)
    return added


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--check", required=True)
    ap.add_argument("--backlog")
    args = ap.parse_args(argv)

    try:
        check = json.loads(args.check)
    except json.JSONDecodeError as e:
        print(f"--check must be JSON: {e}", file=sys.stderr)
        return 1

    result_path = os.path.join(args.run, "result.json")
    if not os.path.exists(result_path):
        print(f"missing result.json in {args.run}", file=sys.stderr)
        return 4
    with open(result_path, "r", encoding="utf-8") as f:
        result = json.load(f)

    judge = None
    judge_path = os.path.join(args.run, "judge.json")
    if os.path.exists(judge_path):
        with open(judge_path, "r", encoding="utf-8") as f:
            judge = json.load(f)

    score = compute_score(result, check, judge)

    score_path = os.path.join(args.run, "score.json")
    with open(score_path, "w", encoding="utf-8") as f:
        json.dump(score, f, indent=2)

    print(json.dumps(score))

    if args.backlog:
        feed_backlog(args.run, args.backlog, check.get("task", ""))

    must = check.get("must", "complete")
    if must == "complete" and not score["completed"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
