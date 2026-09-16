#!/usr/bin/env python3
"""check_plan.py -- Nightshift plan validator.

Modes (see loop/README.md for the full contract):

  check_plan.py <plan.json> [--target T] [--map M] [--config C] [--repo R]
      Validate plan.json against target.json / map.json / config.json.

  check_plan.py --verify <subtask-id> <plan.json> [--base <ref>]
      Post-execution check: diff of the subtask's branch must stay inside
      owned_paths, and report.json must carry no decisions_made.

  check_plan.py --backlog-diff <before.yaml> <after.yaml>
      Confirm the planner only touched status/attempts/note on existing rows
      and appended new rows at the end.

Exit codes: 0 ok · 2 reject (one problem per stdout line) · 1 error.
Python 3.9+, stdlib only.
"""

import argparse
import json
import posixpath
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import allowlist as _allow  # noqa: E402

RESERVED_DIRS = (".claude", ".loop", ".git")
VALID_MODELS = ("sonnet", "opus")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _git_toplevel(start_dir):
    try:
        out = subprocess.run(
            ["git", "-C", str(start_dir), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
    except Exception:
        return None
    top = out.stdout.strip()
    return Path(top) if top else None


def _load_json(path, label):
    """Returns (data, error_message_or_None)."""
    p = Path(path)
    if not p.exists():
        return None, f"error: {label} not found: {p}"
    try:
        return json.loads(p.read_text()), None
    except Exception as exc:
        return None, f"error: invalid {label}: {exc}"


def _check_and_normalize_path(raw):
    """Returns (problem_str_or_None, normalized_path_or_None)."""
    if not raw:
        return "is empty", None
    if raw.startswith("/"):
        return "is absolute", None
    norm = posixpath.normpath(raw)
    if norm == ".." or norm.startswith("../"):
        return "escapes the repo ('..')", None
    first = norm.split("/", 1)[0]
    if first in RESERVED_DIRS:
        return f"is under reserved dir '{first}/'", None
    return None, norm


def _paths_conflict(pa, pb):
    if pa == pb:
        return True
    if pb.startswith(pa + "/") or pa.startswith(pb + "/"):
        return True
    return False


# ---------------------------------------------------------------------------
# default mode: validate plan.json
# ---------------------------------------------------------------------------

def cmd_check(args):
    problems = []

    plan_path = Path(args.plan)
    plan, err = _load_json(plan_path, "plan.json")
    if err:
        print(err, file=sys.stderr)
        return 1
    plan_dir = plan_path.resolve().parent

    target_path = Path(args.target) if args.target else (plan_dir / "target.json")
    target, err = _load_json(target_path, "target.json")
    if err:
        print(err, file=sys.stderr)
        return 1

    repo = Path(args.repo) if args.repo else _git_toplevel(plan_dir)
    if repo is None:
        print(f"error: could not determine git repo root for {plan_dir}", file=sys.stderr)
        return 1

    map_path = Path(args.map) if args.map else (repo / ".loop" / "map.json")
    map_data = None
    if map_path.exists():
        map_data, err = _load_json(map_path, "map.json")
        if err:
            print(err, file=sys.stderr)
            return 1
    else:
        print(f"warning: map.json not found at {map_path}; skipping import-disjoint check",
              file=sys.stderr)

    max_fanout = 3
    config = None
    if args.config:
        config, err = _load_json(args.config, "config.json")
        if err:
            print(err, file=sys.stderr)
            return 1
        max_fanout = config.get("max_fanout", 3)

    subtasks = plan.get("subtasks", []) or []
    task_ids = set(target.get("task_ids", []) or [])

    # -- row must be in target.task_ids --------------------------------
    for st in subtasks:
        sid = st.get("id", "<missing-id>")
        row = st.get("row")
        if row not in task_ids:
            problems.append(f"subtask {sid}: row '{row}' not in target.task_ids")

    # -- unique ids -------------------------------------------------------
    seen_ids = set()
    for st in subtasks:
        sid = st.get("id")
        if sid in seen_ids:
            problems.append(f"duplicate subtask id: {sid}")
        seen_ids.add(sid)

    # -- fanout -------------------------------------------------------------
    if len(subtasks) > max_fanout:
        problems.append(f"fanout {len(subtasks)} exceeds max_fanout {max_fanout}")

    # -- required non-empty fields -----------------------------------------
    for st in subtasks:
        sid = st.get("id", "<missing-id>")
        if not st.get("goal"):
            problems.append(f"subtask {sid}: missing goal")
        if not st.get("acceptance_cmd"):
            problems.append(f"subtask {sid}: missing acceptance_cmd")
        elif config is not None:
            # An acceptance command the executor's allowlist would deny is a
            # subtask that cannot finish; catch it here, not after $2 of work.
            ok, seg = _allow.is_allowed(st["acceptance_cmd"], _allow.rules(config, Path(__file__).parent))
            if not ok:
                problems.append(f"subtask {sid}: acceptance_cmd segment not on the child allowlist: {seg!r}")
        if not st.get("owned_paths"):
            problems.append(f"subtask {sid}: owned_paths empty")

    # -- normalize + reserved/escape checks on owned_paths ------------------
    norm_owned = {}
    for st in subtasks:
        sid = st.get("id", "<missing-id>")
        paths = st.get("owned_paths") or []
        normed = []
        for p in paths:
            problem, npath = _check_and_normalize_path(p)
            if problem:
                problems.append(f"subtask {sid}: owned path '{p}' {problem}")
            else:
                normed.append(npath)
        norm_owned[sid] = normed

    # -- pairwise disjoint owned_paths ---------------------------------------
    sids = list(norm_owned.keys())
    for i in range(len(sids)):
        for j in range(i + 1, len(sids)):
            a_sid, b_sid = sids[i], sids[j]
            for pa in norm_owned[a_sid]:
                for pb in norm_owned[b_sid]:
                    if _paths_conflict(pa, pb):
                        problems.append(
                            f"owned_paths overlap: {a_sid}:'{pa}' and {b_sid}:'{pb}'"
                        )

    # -- import-disjoint (needs map.json) ------------------------------------
    if map_data is not None:
        imports = {}
        for mod in map_data.get("modules", []) or []:
            imports[mod.get("path")] = set(mod.get("imports", []) or [])
        all_module_paths = set(imports.keys())

        def expand(paths):
            files = set()
            for p in paths:
                if p in all_module_paths:
                    files.add(p)
                else:
                    prefix = p if p.endswith("/") else p + "/"
                    for m in all_module_paths:
                        if m.startswith(prefix):
                            files.add(m)
            return files

        expanded = {sid: expand(norm_owned[sid]) for sid in sids}
        reported_edges = set()
        for i in range(len(sids)):
            for j in range(i + 1, len(sids)):
                a_sid, b_sid = sids[i], sids[j]
                for fa in expanded[a_sid]:
                    for fb in expanded[b_sid]:
                        if fb in imports.get(fa, set()):
                            edge = (a_sid, b_sid, fa, fb)
                            if edge not in reported_edges:
                                problems.append(
                                    f"import edge between {a_sid} and {b_sid}: {fa} -> {fb}"
                                )
                                reported_edges.add(edge)
                        if fa in imports.get(fb, set()):
                            edge = (a_sid, b_sid, fb, fa)
                            if edge not in reported_edges:
                                problems.append(
                                    f"import edge between {a_sid} and {b_sid}: {fb} -> {fa}"
                                )
                                reported_edges.add(edge)

    # -- hard / model ---------------------------------------------------------
    for st in subtasks:
        sid = st.get("id", "<missing-id>")
        hard = st.get("hard")
        model = st.get("model")
        hard_ok = isinstance(hard, bool)
        if not hard_ok:
            problems.append(f"subtask {sid}: hard must be boolean, got {hard!r}")
        model_ok = model in VALID_MODELS
        if not model_ok:
            problems.append(f"subtask {sid}: model must be one of {VALID_MODELS}, got {model!r}")
        if hard_ok and model_ok:
            expected = "opus" if hard else "sonnet"
            if model != expected:
                problems.append(
                    f"subtask {sid}: model '{model}' inconsistent with hard={hard} "
                    f"(expected '{expected}')"
                )

    if problems:
        for p in problems:
            print(p)
        return 2
    print("ok")
    return 0


# ---------------------------------------------------------------------------
# --verify mode
# ---------------------------------------------------------------------------

def cmd_verify(subtask_id, plan_path_str, base_arg):
    plan_path = Path(plan_path_str)
    plan, err = _load_json(plan_path, "plan.json")
    if err:
        print(err, file=sys.stderr)
        return 1
    plan_dir = plan_path.resolve().parent

    subtask = None
    for st in plan.get("subtasks", []) or []:
        if st.get("id") == subtask_id:
            subtask = st
            break
    if subtask is None:
        print(f"error: subtask '{subtask_id}' not found in plan", file=sys.stderr)
        return 1

    owned_norm = set()
    for p in subtask.get("owned_paths") or []:
        _, norm = _check_and_normalize_path(p)
        owned_norm.add(norm if norm else p)

    report_path = plan_dir / "tasks" / subtask_id / "report.json"
    report, err = _load_json(report_path, "report.json")
    if err:
        print(err, file=sys.stderr)
        return 1

    branch = report.get("branch")
    if not branch:
        print("error: report.json missing 'branch'", file=sys.stderr)
        return 1

    repo = _git_toplevel(plan_dir)
    if repo is None:
        print(f"error: could not determine git repo root for {plan_dir}", file=sys.stderr)
        return 1

    base = base_arg
    if not base:
        base = "HEAD"
        state_path = plan_dir.parent.parent / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if state.get("run"):
                    base = state["run"]
            except Exception:
                pass

    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", f"{base}...{branch}"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"error: git diff failed: {exc.stderr.strip()}", file=sys.stderr)
        return 1

    changed = [line.strip() for line in out.stdout.splitlines() if line.strip()]

    problems = []
    for f in changed:
        if f not in owned_norm:
            problems.append(f"stray file not in owned_paths: {f}")

    decisions = report.get("decisions_made") or []
    if decisions:
        problems.append(f"report.json has non-empty decisions_made: {decisions}")

    if problems:
        for p in problems:
            print(p)
        return 2
    print("ok")
    return 0


# ---------------------------------------------------------------------------
# --backlog-diff mode
# ---------------------------------------------------------------------------

def _split_kv(content):
    if ":" not in content:
        return content.strip(), ""
    k, v = content.split(":", 1)
    k = k.strip()
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    else:
        try:
            v = int(v)
        except ValueError:
            pass
    return k, v


def _fallback_parse_backlog(path):
    rows = []
    current = None
    in_rows = False
    with open(path) as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            if line.strip() == "rows:" and not line[:1].isspace():
                in_rows = True
                continue
            if not in_rows:
                continue
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if stripped.startswith("- "):
                current = {}
                rows.append(current)
                k, v = _split_kv(stripped[2:])
                if k:
                    current[k] = v
            elif indent > 0 and current is not None:
                k, v = _split_kv(stripped)
                if k:
                    current[k] = v
            else:
                in_rows = False
    return {"rows": rows}


def _load_backlog(path):
    """Returns {"rows": [...]}, regardless of whether backlog_io.load()
    returns a bare list of rows or a dict with a "rows" key."""
    try:
        import backlog_io  # local sibling module, built in parallel
        data = backlog_io.load(str(path))
    except Exception:
        return _fallback_parse_backlog(path)
    if isinstance(data, dict):
        return data
    return {"rows": data}


def cmd_backlog_diff(before_path, after_path):
    try:
        before = _load_backlog(before_path)
        after = _load_backlog(after_path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    before_rows = before.get("rows", []) or []
    after_rows = after.get("rows", []) or []
    before_ids = [r.get("id") for r in before_rows]
    after_ids = [r.get("id") for r in after_rows]

    problems = []
    n = len(before_ids)
    allowed_mutable = {"status", "attempts", "note"}

    if after_ids[:n] != before_ids:
        problems.append(
            "existing rows were reordered or deleted (id order of the original rows changed)"
        )
    else:
        for i, bid in enumerate(before_ids):
            brow = before_rows[i]
            arow = after_rows[i]
            all_keys = set(brow.keys()) | set(arow.keys())
            for k in sorted(all_keys - allowed_mutable):
                if brow.get(k) != arow.get(k):
                    problems.append(f"row {bid}: field '{k}' changed ({brow.get(k)!r} -> {arow.get(k)!r})")

        appended = after_rows[n:]
        seen = set(before_ids)
        for r in appended:
            rid = r.get("id")
            if rid in seen:
                problems.append(f"appended row id '{rid}' duplicates an existing id")
            seen.add(rid)

    if problems:
        for p in problems:
            print(p)
        return 2
    print("ok")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="check_plan.py")
    p.add_argument("plan", nargs="?", help="plan.json")
    p.add_argument("--verify", metavar="SUBTASK_ID", help="verify mode: subtask id to check")
    p.add_argument("--backlog-diff", nargs=2, metavar=("BEFORE", "AFTER"),
                   help="backlog-diff mode: before.yaml after.yaml")
    p.add_argument("--target", help="target.json (default: <plan dir>/target.json)")
    p.add_argument("--map", help="map.json (default: <repo>/.loop/map.json)")
    p.add_argument("--config", help="config.json (for max_fanout, default 3)")
    p.add_argument("--repo", help="repo root (default: git toplevel of plan's dir)")
    p.add_argument("--base", help="--verify mode: base ref for git diff")
    return p


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv[1:])

    if args.verify:
        if not args.plan:
            print("error: --verify requires <plan.json>", file=sys.stderr)
            return 1
        return cmd_verify(args.verify, args.plan, args.base)

    if args.backlog_diff:
        before, after = args.backlog_diff
        return cmd_backlog_diff(before, after)

    if not args.plan:
        print("error: plan.json is required", file=sys.stderr)
        return 1

    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
