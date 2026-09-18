#!/usr/bin/env python3
"""report.py — RETRO.md from one or more facts files, trends by label.

    report.py --facts retro/history/*.json [--out RETRO.md]

Renders the latest facts as tables and, when more than one facts file is
given, a trend table across labels for the numbers the kit is judged by:
first-try acceptance rate, cost per accepted milestone, Nightshift kept
share, persona findings per walkthrough, human overrides. Cost per accepted
milestone lives here as a trend and nowhere else: it is a report metric,
never a keep/undo input (CLAUDE.md, Performance Benchmarking).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(paths):
    out = []
    for p in paths:
        try:
            out.append(json.loads(Path(p).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda f: f.get("generated_at", ""))


def fmt(v, money=False):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"${v:.2f}" if money else f"{v:.2f}"
    return str(v)


def render(facts_list):
    latest = facts_list[-1]
    t = latest["totals"]
    L = [f"# Retro — {latest.get('label') or latest['generated_at'][:10]}", "",
         f"Generated {latest['generated_at']} from {t['projects']} project(s). Every number is a count over files the drivers wrote; no transcript was read.", "",
         "## Totals", "",
         "| what | value |", "|---|---|",
         f"| validation runs | {t['validation_runs']} ({', '.join(f'{k} {v}' for k, v in sorted(t['verdicts'].items())) or 'none'}) |",
         f"| milestones accepted first try / after retry / blocked | {t['accepted_first_try']} / {t['accepted_after_retry']} / {t['blocked']} |",
         f"| first-try acceptance rate | {fmt(t['first_try_rate'])} |",
         f"| cost per accepted milestone | {fmt(t['cost_per_accepted_milestone'], money=True)} |",
         f"| Nightshift iterations kept / flat / regressed | {t['kept']} / {t['reset_flat']} / {t['reset_regressed']} |",
         f"| executor denies | {', '.join(f'{k} {v}' for k, v in sorted(t['denies'].items())) or 'none'} |",
         f"| persona walkthroughs / findings | {t['walkthroughs']} / {t['findings']} |",
         f"| human overrides | {', '.join(f'{k} {v}' for k, v in sorted(t['human_overrides'].items())) or 'none'} |",
         f"| total spend recorded | {fmt(t['cost_usd'], money=True)} |", ""]
    L += ["## Per project", "", "| project | validation | build (first/retry/blocked, $/accepted) | nightshift (kept/flat/regressed, stop) | persona | ship failures | overrides |", "|---|---|---|---|---|---|---|"]
    for p in latest["projects"]:
        v, b, n, pe, s = p["validation"], p["build"], p["nightshift"], p["persona"], p["ship"]
        L.append(f"| {p['name']} | {v['runs']} run(s) {', '.join(f'{k} {c}' for k, c in sorted(v['verdicts'].items())) or ''} {fmt(v['cost_usd'], True)} | "
                 f"{b['accepted_first_try']}/{b['accepted_after_retry']}/{b['blocked']}, {fmt(b['cost_per_accepted_milestone'], True)} | "
                 f"{n['kept']}/{n['reset_flat']}/{n['reset_regressed']}, {n.get('last_stop_reason') or '—'} | "
                 f"{pe['completed']}/{pe['walkthroughs']} done, {pe['findings']} findings ({pe['findings_deduped']} deduped) | "
                 f"{', '.join(f'{k} {c}' for k, c in sorted(s['rows_failed'].items())) or 'none'} | "
                 f"{', '.join(f'{k} {c}' for k, c in sorted(p['human_overrides'].items())) or 'none'} |")
    if len(facts_list) > 1:
        L += ["", "## Trend", "", "| label | first-try rate | $/accepted milestone | kept share | findings per walkthrough | overrides | spend |", "|---|---|---|---|---|---|---|"]
        for f in facts_list:
            t = f["totals"]
            it = t["kept"] + t["reset_flat"] + t["reset_regressed"]
            kept_share = round(t["kept"] / it, 2) if it else None
            fpw = round(t["findings"] / t["walkthroughs"], 2) if t["walkthroughs"] else None
            L.append(f"| {f.get('label') or f['generated_at'][:10]} | {fmt(t['first_try_rate'])} | {fmt(t['cost_per_accepted_milestone'], True)} | "
                     f"{fmt(kept_share)} | {fmt(fpw)} | {sum(t['human_overrides'].values())} | {fmt(t['cost_usd'], True)} |")
    L += ["", "## What to look at", ""]
    t = latest["totals"]
    hints = []
    if t["first_try_rate"] is not None and t["first_try_rate"] < 0.6:
        hints.append("- first-try acceptance under 60%: the planner's goals or the acceptance checks disagree; read the blocked milestones' notes")
    if t["reset_flat"] > t["kept"]:
        hints.append("- more flat nights than kept ones: the picker has no headroom or the scorers saturate; check `eps` and the backlog")
    if t["denies"].get("safety"):
        hints.append("- safety denies happened: an executor tried to leave its worktree; read `.loop/events.log`")
    if t["verdicts"].get("PIVOT", 0) + t["verdicts"].get("NO-GO", 0) > t["verdicts"].get("GO", 0):
        hints.append("- more ideas failed validation than passed: good, if the ledgers read true; spot-check one VERDICT.md")
    if not hints:
        hints.append("- nothing stands out in these numbers; the next real project will add more than another retro")
    L += hints + [""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="report.py")
    ap.add_argument("--facts", nargs="+", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    facts = load(a.facts)
    if not facts:
        print(json.dumps({"ok": False, "error": "no readable facts"})); return 1
    md = render(facts)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
        print(json.dumps({"ok": True, "out": a.out, "facts": len(facts)}))
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
