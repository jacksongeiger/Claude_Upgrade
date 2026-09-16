#!/usr/bin/env python3
"""spec_check.py -- validate spec.json (the pipeline's spine) and derive files.

Usage:

  spec_check.py <spec.json>
      Validate spec.json against the rule list in pipeline/README.md
      ("spec.json (the spine)"). Prints one problem per line on failure.

  spec_check.py <spec.json> --derive [--project <dir>]
      Validate, then (only if valid) write/refresh the derived files:
      GOAL.md, .loop/backlog.yaml (appended, deduped by id),
      .pipeline/acceptance-index.json, SPEC.md (only if absent),
      .pipeline/scorers.proposed.json. Idempotent: running twice changes
      nothing. Default project dir is the spec file's own directory.

Exit codes: 0 ok · 2 spec invalid (one problem per line) · 1 unreadable or
invalid JSON / usage error.

Python 3.9+, stdlib only.
"""

import argparse
import json
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent          # .../pipeline
ROOT = KIT.parent                              # .../Claude_Upgrade
sys.path.insert(0, str(ROOT / "loop"))
import backlog_io  # noqa: E402

ALLOWED_NEEDS = {
    "ui", "unit-tests", "coverage", "lighthouse", "persona", "perf",
    "evals", "llm", "db", "auth", "deploy",
}

# acceptance type -> required fields (checked generically, some types have
# extra shape rules handled in _check_acceptance)
ACCEPTANCE_TYPES = {"test", "perf", "lighthouse", "persona", "gate", "evals", "manual"}

ACCEPT_TO_SCORER = {
    "test": "tests", "perf": "perf", "lighthouse": "lighthouse",
    "persona": "persona", "evals": "evals",
}

CANONICAL_SCORER_ORDER = ["tests", "perf", "lighthouse", "persona", "evals"]
NEED_TO_SCORER = {
    "unit-tests": "tests", "coverage": "tests", "lighthouse": "lighthouse",
    "persona": "persona", "perf": "perf", "evals": "evals",
}

# validation
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
            if color.get(dep, WHITE) == GRAY:
                i = path.index(dep)
                return path[i:] + [dep]
            if color.get(dep, WHITE) == WHITE:
                found = dfs(dep)
                if found:
                    return found
        path.pop()
        color[n] = BLACK
        return None

    for node in graph:
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None

def _check_acceptance(entry, feat_id, idx):
    problems = []
    if not isinstance(entry, dict):
        return [f"feature {feat_id} acceptance[{idx}]: not an object"]
    t = entry.get("type")
    if not t:
        return [f"feature {feat_id} acceptance[{idx}]: missing type"]
    if t not in ACCEPTANCE_TYPES:
        return [f"feature {feat_id} acceptance[{idx}]: unknown type '{t}'"]

    def need(field):
        if not entry.get(field):
            problems.append(f"feature {feat_id} acceptance[{idx}] ({t}): missing '{field}'")

    if t == "test":
        need("cmd")
        if entry.get("must") != "pass":
            problems.append(f"feature {feat_id} acceptance[{idx}] (test): must must be 'pass'")
    elif t == "perf":
        need("cmd")
        need("metric")
        if entry.get("max") is None and entry.get("min") is None:
            problems.append(f"feature {feat_id} acceptance[{idx}] (perf): needs 'max' or 'min'")
    elif t == "lighthouse":
        need("url")
        minv = entry.get("min")
        if not isinstance(minv, dict) or not minv:
            problems.append(f"feature {feat_id} acceptance[{idx}] (lighthouse): 'min' must be a non-empty object")
    elif t == "persona":
        need("task")
        if not isinstance(entry.get("max_steps"), int) or entry.get("max_steps") is False or entry.get("max_steps", 0) <= 0:
            problems.append(f"feature {feat_id} acceptance[{idx}] (persona): 'max_steps' must be a positive int")
        if entry.get("must") != "complete":
            problems.append(f"feature {feat_id} acceptance[{idx}] (persona): must must be 'complete'")
    elif t == "gate":
        need("name")
        if entry.get("kind") not in ("screenshot", "perf"):
            problems.append(f"feature {feat_id} acceptance[{idx}] (gate): 'kind' must be 'screenshot' or 'perf'")
    elif t == "evals":
        need("cmd")
        if entry.get("min") is None:
            problems.append(f"feature {feat_id} acceptance[{idx}] (evals): missing 'min'")
    elif t == "manual":
        need("what")

    return problems

def validate(spec):
    """Returns (problems: list[str], counts: dict)."""
    problems = []

    milestones = spec.get("milestones")
    if not isinstance(milestones, list):
        problems.append("milestones: must be a list")
        milestones = []
    features = spec.get("features")
    if not isinstance(features, list):
        problems.append("features: must be a list")
        features = []
    needs = spec.get("needs")
    if not isinstance(needs, list):
        problems.append("needs: must be a list")
        needs = []
    success = spec.get("success")
    if not isinstance(success, list):
        problems.append("success: must be a list")
        success = []

    # -- milestones ---------------------------------------------------------
    seen_mids = set()
    mgraph = {}
    for m in milestones:
        mid = m.get("id") if isinstance(m, dict) else None
        if not mid:
            problems.append("milestone: missing id")
            continue
        if mid in seen_mids:
            problems.append(f"duplicate milestone id: '{mid}'")
        seen_mids.add(mid)
        deps = m.get("depends_on") or []
        mgraph[mid] = deps
        for dep in deps:
            if dep not in {mm.get("id") for mm in milestones if isinstance(mm, dict)}:
                problems.append(f"milestone '{mid}' depends_on unknown milestone '{dep}'")

    cycle = _find_cycle(mgraph)
    if cycle:
        problems.append(f"milestone dependency cycle: {' -> '.join(cycle)}")

    # -- needs ----------------------------------------------------------------
    for n in needs:
        if n not in ALLOWED_NEEDS:
            problems.append(f"unknown need: '{n}'")

    # -- features -------------------------------------------------------------
    n_unmeasurable = 0
    seen_fids = set()
    for f in features:
        if not isinstance(f, dict):
            problems.append("feature: not an object")
            continue
        fid = f.get("id") or "<missing-id>"
        if f.get("id"):
            if fid in seen_fids:
                problems.append(f"duplicate feature id: '{fid}'")
            seen_fids.add(fid)
        else:
            problems.append("feature: missing id")

        mid = f.get("milestone")
        if not mid or mid not in seen_mids:
            problems.append(f"feature {fid}: milestone '{mid}' does not exist")

        acceptance = f.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance:
            problems.append(f"feature {fid}: no acceptance entries")
            acceptance = []

        manual_count = 0
        for idx, entry in enumerate(acceptance):
            problems.extend(_check_acceptance(entry, fid, idx))
            if isinstance(entry, dict) and entry.get("type") == "manual":
                manual_count += 1
        if manual_count > 1:
            problems.append(f"feature {fid}: more than one manual acceptance entry")
        if len(acceptance) == 1 and manual_count == 1:
            n_unmeasurable += 1

    n_features = len(features)
    if n_features and (n_unmeasurable / n_features) > 0.2:
        problems.append(
            f"unmeasurable features {n_unmeasurable}/{n_features} exceeds 20%"
        )

    # -- success ----------------------------------------------------------------
    if isinstance(spec.get("success"), list) and not success:
        problems.append("success: must have at least 1 line")
    for idx, line in enumerate(success):
        if not isinstance(line, str) or not line:
            problems.append(f"success[{idx}]: must be a non-empty string")
        elif len(line) > 120:
            problems.append(f"success[{idx}]: exceeds 120 chars")

    counts = {
        "features": n_features,
        "milestones": len(milestones),
        "unmeasurable": n_unmeasurable,
    }
    return problems, counts

# derive
def _goal_md(spec):
    lines = []
    for i, s in enumerate(spec.get("success", []), start=1):
        lines.append(f"{i}. {s}")
    lines.append("")
    for i, f in enumerate(spec.get("features", []), start=1):
        lines.append(f"F{i}. {f.get('id')} — {f.get('title')}")
    return "\n".join(lines) + "\n"

def _spec_md(spec):
    lines = [f"# {spec.get('name', '')}", ""]
    if spec.get("one_liner"):
        lines += [spec["one_liner"], ""]
    stack = spec.get("stack") or {}
    if stack:
        lines += ["## Stack", "", "```json", json.dumps(stack, indent=2), "```", ""]
    needs = spec.get("needs") or []
    if needs:
        lines += ["## Needs", "", ", ".join(needs), ""]
    lines += ["## Milestones", ""]
    for m in spec.get("milestones", []):
        deps = ", ".join(m.get("depends_on") or []) or "-"
        lines.append(f"- **{m.get('id')}** {m.get('title')} (depends on: {deps})")
    lines.append("")
    lines += ["## Features", ""]
    for f in spec.get("features", []):
        lines.append(f"### {f.get('id')} — {f.get('title')} (milestone {f.get('milestone')})")
        if f.get("description"):
            lines.append(f.get("description"))
        for a in f.get("acceptance", []) or []:
            lines.append(f"- `{a.get('type')}`: {json.dumps(a)}")
        lines.append("")
    lines += ["## Success", ""]
    for s in spec.get("success", []):
        lines.append(f"- {s}")
    return "\n".join(lines) + "\n"

def _derive_backlog(spec, backlog_path):
    if backlog_path.exists():
        loaded = backlog_io.load(str(backlog_path))
    else:
        loaded = []
    rows = loaded.get("rows", []) if isinstance(loaded, dict) else list(loaded)
    existing_ids = {r.get("id") for r in rows}

    for f in spec.get("features", []):
        bid = f"spec-{f.get('id')}"
        if bid in existing_ids:
            continue
        dimension = "none"
        for a in f.get("acceptance", []) or []:
            scorer = ACCEPT_TO_SCORER.get(a.get("type"))
            if scorer:
                dimension = scorer
                break
        rows.append({
            "id": bid,
            "title": f.get("title", ""),
            "dimension": dimension,
            "est": "S",
            "source": "spec",
            "status": "open",
            "rung": 1,
            "attempts": 0,
            "iter_added": 0,
            "note": f"milestone {f.get('milestone')}",
        })
        existing_ids.add(bid)

    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    backlog_io.dump(rows, str(backlog_path))

def _derive_scorers(spec):
    needs = spec.get("needs", []) or []
    present = {NEED_TO_SCORER[n] for n in needs if n in NEED_TO_SCORER}
    scorer_names = [s for s in CANONICAL_SCORER_ORDER if s in present]

    raw = {}
    if "tests" in scorer_names:
        others = [s for s in scorer_names if s != "tests"]
        if others:
            raw["tests"] = 0.4
            for s in others:
                raw[s] = 0.6 / len(others)
        else:
            raw["tests"] = 1.0
    elif scorer_names:
        for s in scorer_names:
            raw[s] = 1.0 / len(scorer_names)

    total = sum(raw.values())
    scorers = []
    if total > 0:
        for s in scorer_names:
            scorers.append({"name": s, "weight": raw[s] / total})
    return {"scorers": scorers}

def derive(spec, project_dir):
    project_dir = Path(project_dir)
    written = []

    goal_path = project_dir / "GOAL.md"
    goal_path.write_text(_goal_md(spec))
    written.append(str(goal_path))

    backlog_path = project_dir / ".loop" / "backlog.yaml"
    _derive_backlog(spec, backlog_path)
    written.append(str(backlog_path))

    idx_path = project_dir / ".pipeline" / "acceptance-index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    # {feature id: {milestone, checks}} — gates.py run-all filters by milestone
    idx = {f.get("id"): {"milestone": f.get("milestone"), "checks": f.get("acceptance", [])}
           for f in spec.get("features", [])}
    idx_path.write_text(json.dumps(idx, indent=2) + "\n")
    written.append(str(idx_path))

    # ignore the pipeline's scratch, never its records
    gi = project_dir / ".gitignore"
    wanted = [".pipeline/run/", ".pipeline/wt/", ".pipeline/build/", ".pipeline/ux/*/shots/",
              ".pipeline/gates/last/", ".pipeline/ledger.jsonl", ".pipeline/events.*", ".pipeline/assess.json"]
    existing = gi.read_text().splitlines() if gi.exists() else []
    missing = [w for w in wanted if w not in existing]
    if missing:
        gi.write_text("\n".join(existing + missing) + "\n")
        written.append(str(gi))

    spec_md_path = project_dir / "SPEC.md"
    if not spec_md_path.exists():
        spec_md_path.write_text(_spec_md(spec))
        written.append(str(spec_md_path))

    scorers_path = project_dir / ".pipeline" / "scorers.proposed.json"
    scorers_path.parent.mkdir(parents=True, exist_ok=True)
    scorers_path.write_text(json.dumps(_derive_scorers(spec), indent=2) + "\n")
    written.append(str(scorers_path))

    return written

# CLI
def build_parser():
    ap = argparse.ArgumentParser(description="validate/derive spec.json")
    ap.add_argument("spec", help="path to spec.json")
    ap.add_argument("--derive", action="store_true")
    ap.add_argument("--project", help="project dir (default: spec's own directory)")
    return ap

def main(argv=None):
    args = build_parser().parse_args(argv)

    spec_path = Path(args.spec)
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read/parse {spec_path}: {exc}", file=sys.stderr)
        return 1

    problems, counts = validate(spec)
    if problems:
        for p in problems:
            print(p)
        return 2

    summary = (
        f"ok: {counts['features']} features, {counts['milestones']} milestones, "
        f"{counts['unmeasurable']} unmeasurable"
    )
    print(summary)

    if args.derive:
        project_dir = Path(args.project) if args.project else spec_path.resolve().parent
        written = derive(spec, project_dir)
        for w in written:
            print(f"wrote {w}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
