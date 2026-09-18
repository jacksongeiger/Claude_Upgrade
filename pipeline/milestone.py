#!/usr/bin/env python3
"""milestone.py -- order spec.json milestones and emit build targets.

Usage:

  milestone.py --spec spec.json --state <state.json> next
      Print the id of the next milestone whose depends_on are all "done" in
      the state file and which is itself neither done nor blocked, or "none".

  milestone.py --spec spec.json --state <state.json> set <id> <done|blocked|open> [--note "..."]
      Update the state file's {"milestones": {id: {status, note, ts}}},
      creating the file if missing.

  milestone.py --spec spec.json target <id> --out target.json
      Write {"iter", "milestone", "task_ids", "mode":"task", "dimension":"build",
      "reason"} for check_plan.py. "iter" is 1, or the previous iter + 1 if
      --out already holds a target for the same milestone.

  milestone.py --spec spec.json list
      Print a table: id, title, depends_on, feature count, status.

--state defaults to <project dir>/.pipeline/build/state.json (project dir =
spec's own directory) when omitted, for `next` and `list`.

Exit codes: 0 ok (including "none" from `next`) · 2 dependency cycle ·
1 usage error / unreadable JSON.

Python 3.9+, stdlib only.
"""

import argparse
import json
import sys
import time
from pathlib import Path


def _load_json(path, label):
    p = Path(path)
    if not p.exists():
        return None, f"error: {label} not found: {p}"
    try:
        return json.loads(p.read_text()), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"error: invalid {label}: {exc}"


def _find_cycle(graph):
    """graph: {id: [dep ids]}. Returns a cycle path (list of ids) or None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    path = []

    def dfs(n):
        color[n] = GRAY
        path.append(n)
        for dep in graph.get(n, []):
            if dep not in graph:
                continue
            if color.get(dep) == GRAY:
                i = path.index(dep)
                return path[i:] + [dep]
            if color.get(dep) == WHITE:
                found = dfs(dep)
                if found:
                    return found
        path.pop()
        color[n] = BLACK
        return None

    for node in list(graph):
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


def default_state_path(spec_path):
    return Path(spec_path).resolve().parent / ".pipeline" / "build" / "state.json"


def load_state(state_path):
    p = Path(state_path)
    if not p.exists():
        return {"milestones": {}}
    try:
        state = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"milestones": {}}
    state.setdefault("milestones", {})
    return state


def save_state(state_path, state):
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n")


def milestone_status(state, mid):
    return (state.get("milestones", {}).get(mid) or {}).get("status", "open")


def milestone_graph(spec):
    return {m["id"]: (m.get("depends_on") or []) for m in spec.get("milestones", []) if m.get("id")}


def milestone_by_id(spec, mid):
    for m in spec.get("milestones", []):
        if m.get("id") == mid:
            return m
    return None


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------

def _tier3_gate(spec, state_path):
    """A large-band validation (over $100) promised a tier-3 row from an
    experiment before any milestone with dependencies; the verdict says
    whether one exists. Returns the slug when the gate holds, else None."""
    v = spec.get("validation") if isinstance(spec.get("validation"), dict) else None
    if not v or v.get("skipped") or not v.get("slug"):
        return None
    try:
        budget = float(v.get("validated_budget_usd") or 0)
    except (TypeError, ValueError):
        return None
    project = Path(state_path).resolve().parent.parent.parent if state_path else Path(".")
    vpath = project / ".pipeline" / "validate" / str(v["slug"]) / "verdict.json"
    toml_path = Path(__file__).resolve().parent / "validate.toml"
    try:
        import tomllib
        cfg = tomllib.loads(toml_path.read_text())
        if budget <= cfg["bands"]["mid_max_usd"] or not cfg["require"]["large"].get("tier3_before_dependent_milestone", True):
            return None
        verdict = json.loads(vpath.read_text())
    except (OSError, ValueError, KeyError):
        return None
    return None if verdict.get("has_tier3") else str(v["slug"])


def cmd_next(spec, state_path):
    graph = milestone_graph(spec)
    cycle = _find_cycle(graph)
    if cycle:
        print(f"error: milestone dependency cycle: {' -> '.join(cycle)}", file=sys.stderr)
        return 2

    state = load_state(state_path)
    tier3_gate = _tier3_gate(spec, state_path)
    for m in spec.get("milestones", []):
        mid = m.get("id")
        if not mid:
            continue
        status = milestone_status(state, mid)
        if status in ("done", "blocked"):
            continue
        deps = m.get("depends_on") or []
        if all(milestone_status(state, dep) == "done" for dep in deps):
            if deps and tier3_gate:
                print(f"needs-human: {mid} depends on {deps} and the validation ({tier3_gate}) has no tier-3 evidence row yet; "
                      f"run the experiment the verdict names, record it with fetch.py --source-kind experiment, re-run validate.py verdict", file=sys.stderr)
                return 3
            print(mid)
            return 0

    print("none")
    return 0


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------

def cmd_set(spec, state_path, mid, status, note):
    if milestone_by_id(spec, mid) is None:
        print(f"error: unknown milestone id: {mid}", file=sys.stderr)
        return 1

    state = load_state(state_path)
    state["milestones"][mid] = {
        "status": status,
        "note": note or "",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_state(state_path, state)
    print(f"set {mid} -> {status}")
    return 0


# ---------------------------------------------------------------------------
# target
# ---------------------------------------------------------------------------

def cmd_target(spec, mid, out_path):
    m = milestone_by_id(spec, mid)
    if m is None:
        print(f"error: unknown milestone id: {mid}", file=sys.stderr)
        return 1

    task_ids = [f["id"] for f in spec.get("features", []) if f.get("milestone") == mid and f.get("id")]

    out_p = Path(out_path)
    iter_n = 1
    if out_p.exists():
        try:
            prev = json.loads(out_p.read_text())
            if prev.get("milestone") == mid and isinstance(prev.get("iter"), int):
                iter_n = prev["iter"] + 1
        except (OSError, json.JSONDecodeError):
            pass

    data = {
        "iter": iter_n,
        "milestone": mid,
        "task_ids": task_ids,
        "mode": "task",
        "dimension": "build",
        "reason": f"milestone {mid}: {m.get('title', '')}",
    }
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {out_p}")
    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def print_table(rows, headers):
    widths = [len(str(h)) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))

    def fmt(cells):
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))

    print(fmt(headers))
    print(fmt(["-" * w for w in widths]))
    for r in rows:
        print(fmt(r))


def cmd_list(spec, state_path):
    state = load_state(state_path)
    milestones = spec.get("milestones", [])
    feature_counts = {}
    for f in spec.get("features", []):
        mid = f.get("milestone")
        feature_counts[mid] = feature_counts.get(mid, 0) + 1

    rows = []
    for m in milestones:
        mid = m.get("id")
        deps = ", ".join(m.get("depends_on") or []) or "-"
        rows.append([
            mid, m.get("title", ""), deps,
            feature_counts.get(mid, 0), milestone_status(state, mid),
        ])
    print_table(rows, ["id", "title", "depends_on", "features", "status"])
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(description="order milestones, emit build targets")
    ap.add_argument("--spec", required=True, help="path to spec.json")
    ap.add_argument("--state", help="state.json path (default: <project>/.pipeline/build/state.json)")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("next")

    p_set = sub.add_parser("set")
    p_set.add_argument("id")
    p_set.add_argument("status", choices=["done", "blocked", "open"])
    p_set.add_argument("--note", default="")

    p_target = sub.add_parser("target")
    p_target.add_argument("id")
    p_target.add_argument("--out", required=True)

    sub.add_parser("list")

    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    spec, err = _load_json(args.spec, "spec.json")
    if err:
        print(err, file=sys.stderr)
        return 1

    state_path = args.state or default_state_path(args.spec)

    if args.command == "next":
        return cmd_next(spec, state_path)
    if args.command == "set":
        return cmd_set(spec, state_path, args.id, args.status, args.note)
    if args.command == "target":
        return cmd_target(spec, args.id, args.out)
    if args.command == "list":
        return cmd_list(spec, state_path)

    print("error: unknown command", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
