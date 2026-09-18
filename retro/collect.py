#!/usr/bin/env python3
"""collect.py — the retro's facts, deterministically, from what the drivers
already wrote. No model, no transcript reading: every number here is a
count over ledgers, event logs, state files, verdicts, scores and reports.

    collect.py --project DIR [--project DIR ...] [--out facts.json] [--label v3.3]

Output (`retro/facts.schema.json` describes it):

    {"schema": 1, "generated_at", "label", "projects": [<project facts>], "totals": {...}}

Per project:
    validation: runs, verdicts {GO,NO-GO,PIVOT,INFRA}, cost_usd, overrules,
                feedback_contradictions
    build:      milestones, attempts_total, accepted_first_try, accepted_after_retry,
                blocked, cost_usd, cost_per_accepted_milestone
    nightshift: iterations, kept, reset_flat, reset_regressed, baseline_rows,
                stop_reasons {reason: n}, denies {kind: n}, cost_usd,
                agreement_max_abs (live meter vs bill, from DRYRUN lines)
    persona:    walkthroughs, completed, findings, findings_deduped, rows_resolved_judged,
                rows_resolved_rule
    feedback:   rows, by_dimension {dim: n}, duplicates_skipped
    ship:       reports, rows_failed {name: n}
    human_overrides: {kind: n}
    ledger:     stages {stage prefix: {n, usd}}

A missing file is a zero, never an error: a project that never ran a stage
has nothing to report for it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "loop"))


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jsonl(path):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def jload(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def lines(path):
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []


def r2(x):
    return round(float(x or 0.0), 4)


def collect_project(project):
    project = Path(project).resolve()
    pipe = project / ".pipeline"
    loop = project / ".loop"
    facts = {"name": project.name, "path": str(project)}

    ledger = jsonl(pipe / "ledger.jsonl")
    stages = defaultdict(lambda: {"n": 0, "usd": 0.0})
    for row in ledger:
        st = str(row.get("stage", "?"))
        key = st.split(":")[0]
        stages[key]["n"] += 1
        stages[key]["usd"] += float(row.get("cost_usd") or 0)
    facts["ledger"] = {"stages": {k: {"n": v["n"], "usd": r2(v["usd"])} for k, v in sorted(stages.items())}}

    # validation
    verdicts = Counter()
    overrules = 0
    contradictions = 0
    runs = 0
    for vpath in sorted((pipe / "validate").glob("*/verdict*.json")) if (pipe / "validate").exists() else []:
        v = jload(vpath, {}) or {}
        if not v.get("verdict"):
            continue
        runs += 1
        verdicts[v["verdict"]] += 1
        if v.get("overruled"):
            overrules += 1
    for cpath in sorted((pipe / "validate").glob("*/feedback-check.json")) if (pipe / "validate").exists() else []:
        contradictions += len((jload(cpath, {}) or {}).get("contradictions", []))
    facts["validation"] = {"runs": runs, "verdicts": dict(verdicts), "cost_usd": r2(stages["validate"]["usd"]),
                           "overrules": overrules, "feedback_contradictions": contradictions}

    # build
    state = jload(pipe / "build" / "state.json", {}) or {}
    ms = state.get("milestones") or {}
    attempts = state.get("attempts") or {}
    first_try = after_retry = blocked = 0
    for mid, m in ms.items():
        n = int(attempts.get(mid, 1 if m.get("status") == "done" else 0))
        if m.get("status") == "done":
            if n <= 1:
                first_try += 1
            else:
                after_retry += 1
        elif m.get("status") == "blocked":
            blocked += 1
    accepted = first_try + after_retry
    build_cost = stages["build"]["usd"]
    facts["build"] = {"milestones": len(ms), "attempts_total": sum(int(v) for v in attempts.values()),
                      "accepted_first_try": first_try, "accepted_after_retry": after_retry, "blocked": blocked,
                      "cost_usd": r2(build_cost),
                      "cost_per_accepted_milestone": r2(build_cost / accepted) if accepted else None}

    # nightshift
    scores = jsonl(loop / "scores.jsonl")
    outcomes = Counter(str(r.get("outcome")) for r in scores)
    lstate = jload(loop / "state.json", {}) or {}
    stop_reasons = Counter()
    denies = Counter()
    agreement = []
    for ln in lines(loop / "events.log"):
        m = re.search(r"\bSTOP reason=([\w-]+)", ln)
        if m:
            stop_reasons[m.group(1)] += 1
        m = re.search(r"\bDENY (\w+)", ln)
        if m:
            denies[m.group(1)] += 1
        m = re.search(r"agreement=(-?[0-9.]+)", ln)
        if m:
            try:
                agreement.append(abs(float(m.group(1))))
            except ValueError:
                pass
    facts["nightshift"] = {"iterations": max((int(r.get("iter") or 0) for r in scores), default=0),
                           "kept": outcomes.get("kept", 0), "reset_flat": outcomes.get("reset-flat", 0),
                           "reset_regressed": outcomes.get("reset-regressed", 0), "baseline_rows": outcomes.get("baseline", 0),
                           "stop_reasons": dict(stop_reasons), "denies": dict(denies),
                           "cost_usd": r2(sum(float(r.get("cost_usd") or 0) for r in scores)),
                           "last_stop_reason": lstate.get("stop_reason"),
                           "agreement_max_abs": r2(max(agreement)) if agreement else None}

    # persona
    walk = completed = findings = deduped = 0
    for sp in sorted((pipe / "ux").glob("*/score.json")) if (pipe / "ux").exists() else []:
        s = jload(sp, {}) or {}
        walk += 1
        completed += 1 if s.get("completed") else 0
        f = jload(sp.parent / "findings.json", {}) or {}
        findings += len(f.get("dead_ends") or []) + len(f.get("confusions") or [])
        d = jload(sp.parent / "findings.dedup.json", {}) or {}
        deduped += len(d.get("dropped") or [])
    resolved_judged = resolved_rule = 0
    fb_rows = 0
    fb_dims = Counter()
    try:
        import backlog_io  # noqa: E402
        rows = backlog_io.load(str(loop / "backlog.yaml")) if (loop / "backlog.yaml").exists() else []
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        rid = str(r.get("id", ""))
        note = str(r.get("note", ""))
        if rid.startswith("ux-") and r.get("status") == "done":
            if "(judged)" in note:
                resolved_judged += 1
            elif "superseded" in note:
                resolved_rule += 1
        if rid.startswith("fb-"):
            fb_rows += 1
            fb_dims[str(r.get("dimension") or "none")] += 1
    facts["persona"] = {"walkthroughs": walk, "completed": completed, "findings": findings, "findings_deduped": deduped,
                        "rows_resolved_judged": resolved_judged, "rows_resolved_rule": resolved_rule}
    facts["feedback"] = {"rows": fb_rows, "by_dimension": dict(fb_dims), "duplicates_skipped": stages["classify"]["n"] and 0}

    # ship
    reports = 0
    failed = Counter()
    for rp in sorted((pipe / "ship").glob("*/ship-report.json")) if (pipe / "ship").exists() else []:
        rep = jload(rp, {}) or {}
        reports += 1
        for c in rep.get("checks") or []:
            if c.get("ok") is False:
                failed[str(c.get("name"))] += 1
    facts["ship"] = {"reports": reports, "rows_failed": dict(failed)}

    # human overrides (from the pipeline events log)
    overrides = Counter()
    for ln in lines(pipe / "events.log"):
        m = re.search(r"HUMAN_OVERRIDE kind=([\w-]+)", ln)
        if m:
            overrides[m.group(1)] += 1
    facts["human_overrides"] = dict(overrides)
    return facts


def totals(projects):
    t = {"projects": len(projects), "validation_runs": 0, "verdicts": Counter(), "milestones": 0, "accepted_first_try": 0,
         "accepted_after_retry": 0, "blocked": 0, "build_cost_usd": 0.0, "nightshift_iterations": 0, "kept": 0,
         "reset_flat": 0, "reset_regressed": 0, "denies": Counter(), "walkthroughs": 0, "findings": 0,
         "human_overrides": Counter(), "cost_usd": 0.0}
    for p in projects:
        t["validation_runs"] += p["validation"]["runs"]
        t["verdicts"].update(p["validation"]["verdicts"])
        t["milestones"] += p["build"]["milestones"]
        t["accepted_first_try"] += p["build"]["accepted_first_try"]
        t["accepted_after_retry"] += p["build"]["accepted_after_retry"]
        t["blocked"] += p["build"]["blocked"]
        t["build_cost_usd"] += p["build"]["cost_usd"]
        t["nightshift_iterations"] += p["nightshift"]["iterations"]
        t["kept"] += p["nightshift"]["kept"]
        t["reset_flat"] += p["nightshift"]["reset_flat"]
        t["reset_regressed"] += p["nightshift"]["reset_regressed"]
        t["denies"].update(p["nightshift"]["denies"])
        t["walkthroughs"] += p["persona"]["walkthroughs"]
        t["findings"] += p["persona"]["findings"]
        t["human_overrides"].update(p["human_overrides"])
        t["cost_usd"] += sum(v["usd"] for v in p["ledger"]["stages"].values()) + p["nightshift"]["cost_usd"]
    accepted = t["accepted_first_try"] + t["accepted_after_retry"]
    t["first_try_rate"] = r2(t["accepted_first_try"] / accepted) if accepted else None
    t["cost_per_accepted_milestone"] = r2(t["build_cost_usd"] / accepted) if accepted else None
    t["verdicts"] = dict(t["verdicts"]); t["denies"] = dict(t["denies"]); t["human_overrides"] = dict(t["human_overrides"])
    t["build_cost_usd"] = r2(t["build_cost_usd"]); t["cost_usd"] = r2(t["cost_usd"])
    return t


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collect.py")
    ap.add_argument("--project", action="append", required=True)
    ap.add_argument("--out")
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    projects = [collect_project(p) for p in a.project]
    out = {"schema": 1, "generated_at": now(), "label": a.label, "projects": projects, "totals": totals(projects)}
    text = json.dumps(out, indent=2, sort_keys=True) + "\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "out": a.out, "projects": len(projects), "totals": out["totals"]}))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
