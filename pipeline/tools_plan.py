#!/usr/bin/env python3
"""Stage 2 tooling planner — spec needs + assess.py output -> tooling plan.

Usage:
    tools_plan.py --spec spec.json --assess assess.json \
        --out .pipeline/tooling-plan.json [--rdx <path|off>]
        Build/overwrite the plan file. Prints a readable table. Exit 0.

    tools_plan.py --verify --spec spec.json --assess assess.json \
        --plan .pipeline/tooling-plan.json
        Recompute ready/gap against fresh assess data, mark each existing gap
        "closed" or "open" in place, print the table. Exit 0 if every gap that
        a `needs` entry requires is closed, else 3.

    tools_plan.py --mark <need> <slug> --plan .pipeline/tooling-plan.json
        Set "chosen" = <slug> on the named gap in the plan file. Exit 0.

Exit codes: 0 ok · 1 usage error · 3 needs-human (unresolved gap / open gap
after --verify) · 4 infra (spec/assess/plan file missing or unreadable).

Stdlib only. Nothing here installs anything.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------
# KNOWN candidate table: keyed by (need, language) -> list of candidate dicts
# --------------------------------------------------------------------------

KNOWN = {
    ("coverage", "python"): [
        {"slug": "pytest-cov", "kind": "pip-dev", "version": "5.0.0",
         "source": "known", "tier": "green"},
    ],
    ("coverage", "javascript"): [
        {"slug": "@vitest/coverage-v8", "kind": "npm-dev", "version": "2.1.4",
         "source": "known", "tier": "green"},
        {"slug": "c8", "kind": "npm-dev", "version": "10.1.2",
         "source": "known", "tier": "green"},
    ],
    ("lighthouse", "javascript"): [
        {"slug": "lighthouse", "kind": "npm-dev", "version": "12.2.1",
         "source": "known", "tier": "green"},
        {"slug": "lighthouse", "kind": "npx", "version": "latest",
         "source": "known", "tier": "green"},
    ],
    ("lighthouse", "python"): [
        {"slug": "lighthouse", "kind": "npx", "version": "latest",
         "source": "known", "tier": "green"},
    ],
    ("persona", "javascript"): [
        {"slug": "playwright", "kind": "npm-dev", "version": "1.48.2",
         "source": "known", "tier": "green"},
        {"slug": "npx playwright install chromium", "kind": "note",
         "version": "latest", "source": "known", "tier": "green"},
    ],
    ("persona", "python"): [
        {"slug": "playwright", "kind": "npm-dev", "version": "1.48.2",
         "source": "known", "tier": "green"},
        {"slug": "npx playwright install chromium", "kind": "note",
         "version": "latest", "source": "known", "tier": "green"},
    ],
    ("perf", "javascript"): [
        {"slug": "write bench/<name>.js printing one JSON line", "kind": "note",
         "version": "n/a", "source": "known", "tier": "green"},
    ],
    ("perf", "python"): [
        {"slug": "write bench/<name>.py printing one JSON line", "kind": "note",
         "version": "n/a", "source": "known", "tier": "green"},
    ],
    ("evals", "javascript"): [
        {"slug": "add evals/ cases", "kind": "note", "version": "n/a",
         "source": "known", "tier": "green"},
    ],
    ("evals", "python"): [
        {"slug": "add evals/ cases", "kind": "note", "version": "n/a",
         "source": "known", "tier": "green"},
    ],
    ("ui", "javascript"): [
        {"slug": "vite", "kind": "npm-dev", "version": "5.4.11",
         "source": "known", "tier": "green"},
    ],
    ("db", "javascript"): [
        {"slug": "better-sqlite3", "kind": "npm-dev", "version": "11.5.0",
         "source": "known", "tier": "green"},
    ],
    ("db", "python"): [
        {"slug": "sqlite3 (stdlib, no install needed)", "kind": "note",
         "version": "n/a", "source": "known", "tier": "green"},
    ],
    ("auth", "javascript"): [
        {"slug": "decide an auth provider (spec.stack.auth)", "kind": "note",
         "version": "n/a", "source": "known", "tier": "green"},
    ],
    ("auth", "python"): [
        {"slug": "decide an auth provider (spec.stack.auth)", "kind": "note",
         "version": "n/a", "source": "known", "tier": "green"},
    ],
    ("deploy", "javascript"): [
        {"slug": "vercel deploy", "kind": "cli", "version": "latest",
         "source": "known", "tier": "green"},
        {"slug": "netlify deploy --prod", "kind": "cli", "version": "latest",
         "source": "known", "tier": "green"},
        {"slug": "fly deploy", "kind": "cli", "version": "latest",
         "source": "known", "tier": "green"},
    ],
    ("deploy", "python"): [
        {"slug": "fly deploy", "kind": "cli", "version": "latest",
         "source": "known", "tier": "green"},
        {"slug": "vercel deploy", "kind": "cli", "version": "latest",
         "source": "known", "tier": "green"},
    ],
}


def known_candidates(need, language):
    return [dict(c) for c in KNOWN.get((need, language), [])]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_cmd(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def lighthouse_available():
    if shutil.which("lighthouse"):
        return True
    p = run_cmd(["npx", "--no-install", "lighthouse", "--version"], timeout=20)
    return bool(p and p.returncode == 0)


def playwright_available():
    p = run_cmd(["node", "-e", "require('playwright')"], timeout=10)
    if p and p.returncode == 0:
        return True
    p = run_cmd(["npm", "root", "-g"], timeout=10)
    if p and p.returncode == 0:
        g = p.stdout.strip()
        if g and Path(g, "playwright").is_dir():
            return True
    return False


# --------------------------------------------------------------------------
# rdx
# --------------------------------------------------------------------------

def find_rdx(rdx_arg):
    """Return the rdx binary path to use, or None. rdx_arg: None (auto),
    'off' (disabled), or an explicit path."""
    if rdx_arg == "off":
        return None
    if rdx_arg:
        return rdx_arg
    found = shutil.which("rdx")
    if found:
        return found
    default = "/home/user/Claude_Upgrade/discovery/rdx.sh"
    if Path(default).exists():
        return default
    return None


def parse_rdx_output(text):
    """Parse leniently: a JSON list, a JSON object with a list under a
    common key, or newline-separated names. Returns a list of raw items
    (dicts or strings)."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "items", "candidates", "matches"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    # fall back: one candidate name per line
    return [line.strip() for line in text.splitlines() if line.strip()]


def rdx_candidates(rdx_bin, need, language):
    """Returns (candidates, rdx_status). rdx_status is None on success (or
    when rdx is simply off/missing) or a string describing why rdx was
    skipped."""
    if rdx_bin is None:
        return [], "unavailable: rdx not found"
    query = "%s %s" % (need, language or "")
    proc = run_cmd([rdx_bin, "search", query.strip(), "--json"], timeout=20)
    if proc is None:
        return [], "unavailable: rdx invocation failed"
    if proc.returncode != 0:
        return [], "unavailable: rdx exited %s" % proc.returncode
    try:
        items = parse_rdx_output(proc.stdout)
    except Exception as exc:
        return [], "unavailable: could not parse rdx output (%s)" % exc

    candidates = []
    for item in items[:3]:
        if isinstance(item, dict):
            slug = item.get("slug") or item.get("name") or item.get("package")
            tier = item.get("tier", "unknown")
            why = item.get("why") or item.get("reason") or item.get("description")
        else:
            slug = str(item)
            tier = "unknown"
            why = None
        if not slug:
            continue
        cand = {"slug": slug, "kind": "rdx", "tier": tier, "source": "rdx"}
        if why:
            cand["why"] = why
        candidates.append(cand)
    return candidates, None


# --------------------------------------------------------------------------
# readiness evaluation
# --------------------------------------------------------------------------

def evaluate_needs(needs, spec, assess):
    """Returns (ready_list, gaps_list_without_candidates_yet).
    Each gap dict: {need, why}."""
    ready = []
    gaps = []

    stack = spec.get("stack", {}) or {}
    language = stack.get("language")

    tests = assess.get("tests", {}) or {}
    bench = assess.get("bench", {}) or {}
    evals = assess.get("evals", {}) or {}
    ui = assess.get("ui", {}) or {}
    llm_calls = bool(assess.get("llm_calls"))

    for need in needs:
        if need == "unit-tests":
            if tests.get("runner", "none") != "none":
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs unit-tests; assess: no test runner detected"})

        elif need == "coverage":
            if tests.get("coverage_tool_installed"):
                ready.append(need)
            else:
                runner = tests.get("runner", "none")
                gaps.append({
                    "need": need,
                    "why": "spec needs coverage; assess: no coverage tool for %s" % runner,
                })

        elif need == "lighthouse":
            if lighthouse_available() and ui.get("present"):
                ready.append(need)
            else:
                reason = []
                if not ui.get("present"):
                    reason.append("no ui detected")
                if not lighthouse_available():
                    reason.append("lighthouse not resolvable")
                gaps.append({"need": need, "why": "spec needs lighthouse; assess: %s" % ", ".join(reason)})

        elif need == "persona":
            if playwright_available():
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs persona; assess: playwright not resolvable"})

        elif need == "perf":
            # a perf check whose command lives in the repo is a build
            # deliverable (the executors write the bench), not a tool gap
            perf_cmds = [c.get("cmd") for f in spec.get("features", []) for c in f.get("acceptance", [])
                         if isinstance(c, dict) and c.get("type") == "perf" and c.get("cmd")]
            if bench.get("present") or perf_cmds:
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs perf; assess: no benchmark script and no perf check names one"})

        elif need == "evals":
            if evals.get("present") and llm_calls:
                ready.append(need)
            else:
                reason = []
                if not llm_calls:
                    reason.append("no llm calls detected")
                if not evals.get("present"):
                    reason.append("no evals/ cases")
                gaps.append({"need": need, "why": "spec needs evals; assess: %s" % ", ".join(reason)})

        elif need == "ui":
            if ui.get("present"):
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs ui; assess: no ui detected"})

        elif need == "llm":
            if llm_calls:
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs llm; assess: no llm calls detected"})

        elif need == "db":
            if stack.get("data"):
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs db; spec.stack.data is not set"})

        elif need == "auth":
            if stack.get("auth"):
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs auth; spec.stack.auth is not set"})

        elif need == "deploy":
            deploy = stack.get("deploy") or {}
            if deploy.get("cmd"):
                ready.append(need)
            else:
                gaps.append({"need": need, "why": "spec needs deploy; spec.stack.deploy.cmd is not set"})

        else:
            # unknown need: treat conservatively as a gap with no candidates
            gaps.append({"need": need, "why": "spec needs %s; unrecognized need" % need})

    return ready, gaps, language


# --------------------------------------------------------------------------
# plan build
# --------------------------------------------------------------------------

def build_plan(spec, assess, rdx_arg):
    needs = spec.get("needs", []) or []
    ready, gaps, language = evaluate_needs(needs, spec, assess)

    rdx_bin = find_rdx(rdx_arg)
    rdx_status = None
    if rdx_arg == "off":
        rdx_status = "off"

    for gap in gaps:
        need = gap["need"]
        candidates = known_candidates(need, language)
        if rdx_arg != "off":
            rdx_cands, status = rdx_candidates(rdx_bin, need, language)
            candidates.extend(rdx_cands)
            if status is not None and rdx_status is None:
                rdx_status = status
        gap["candidates"] = candidates
        gap["chosen"] = None

    if rdx_status is None and rdx_arg != "off":
        rdx_status = "ok"

    unresolved = [g["need"] for g in gaps if not g["candidates"]]

    plan = {
        "gaps": gaps,
        "ready": ready,
        "unresolved": unresolved,
        "rdx": rdx_status,
        "generated": time.time(),
    }
    return plan


def print_table(ready, gaps):
    rows = []
    for need in ready:
        rows.append((need, "ready", ""))
    for gap in gaps:
        cands = gap.get("candidates") or []
        first = cands[0]["slug"] if cands else "(none)"
        rows.append((gap["need"], "gap", first))
    width_need = max([len(r[0]) for r in rows] + [4])
    width_status = max([len(r[1]) for r in rows] + [6])
    print("%-*s  %-*s  %s" % (width_need, "NEED", width_status, "STATUS", "FIRST CANDIDATE"))
    for need, status, first in rows:
        print("%-*s  %-*s  %s" % (width_need, need, width_status, status, first))


# --------------------------------------------------------------------------
# CLI actions
# --------------------------------------------------------------------------

def cmd_build(args):
    try:
        spec = load_json(args.spec)
    except Exception as exc:
        print("error: could not read spec %s: %s" % (args.spec, exc), file=sys.stderr)
        return 4
    try:
        assess = load_json(args.assess)
    except Exception as exc:
        print("error: could not read assess %s: %s" % (args.assess, exc), file=sys.stderr)
        return 4

    plan = build_plan(spec, assess, args.rdx)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
        fh.write("\n")

    print_table(plan["ready"], plan["gaps"])
    return 0


def cmd_verify(args):
    try:
        spec = load_json(args.spec)
    except Exception as exc:
        print("error: could not read spec %s: %s" % (args.spec, exc), file=sys.stderr)
        return 4
    try:
        assess = load_json(args.assess)
    except Exception as exc:
        print("error: could not read assess %s: %s" % (args.assess, exc), file=sys.stderr)
        return 4
    try:
        plan = load_json(args.plan)
    except Exception as exc:
        print("error: could not read plan %s: %s" % (args.plan, exc), file=sys.stderr)
        return 4

    needs = spec.get("needs", []) or []
    ready, new_gaps, _language = evaluate_needs(needs, spec, assess)
    new_gap_needs = {g["need"] for g in new_gaps}

    for gap in plan.get("gaps", []):
        if gap["need"] in new_gap_needs:
            gap["status"] = "open"
        else:
            gap["status"] = "closed"

    plan["ready"] = ready
    plan["generated"] = time.time()

    with open(args.plan, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
        fh.write("\n")

    print_table(ready, plan.get("gaps", []))

    still_open = [g for g in plan.get("gaps", []) if g.get("status") == "open" and g["need"] in needs]
    return 3 if still_open else 0


def cmd_mark(args):
    try:
        plan = load_json(args.plan)
    except Exception as exc:
        print("error: could not read plan %s: %s" % (args.plan, exc), file=sys.stderr)
        return 4

    need, slug = args.mark
    found = False
    for gap in plan.get("gaps", []):
        if gap["need"] == need:
            gap["chosen"] = slug
            found = True
            break

    if not found:
        print("error: no gap named %r in plan" % need, file=sys.stderr)
        return 1

    with open(args.plan, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)
        fh.write("\n")

    print("marked %s -> %s" % (need, slug))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage 2 tooling planner")
    parser.add_argument("--spec")
    parser.add_argument("--assess")
    parser.add_argument("--out")
    parser.add_argument("--plan")
    parser.add_argument("--rdx", default=None, help="path to rdx binary, or 'off'")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--mark", nargs=2, metavar=("NEED", "SLUG"))
    args = parser.parse_args(argv)

    if args.mark:
        if not args.plan:
            print("error: --mark requires --plan", file=sys.stderr)
            return 1
        return cmd_mark(args)

    if args.verify:
        if not (args.spec and args.assess and args.plan):
            print("error: --verify requires --spec, --assess and --plan", file=sys.stderr)
            return 1
        return cmd_verify(args)

    if not (args.spec and args.assess and args.out):
        print("error: --spec, --assess and --out are required", file=sys.stderr)
        return 1
    return cmd_build(args)


if __name__ == "__main__":
    sys.exit(main())
