#!/usr/bin/env python3
"""Nightshift task picker.

Deterministic, no randomness, no model: picks the next backlog row (or asks
the planner to harvest/hypothesize, or tells the driver to stop) based on
the objective function described in loop/README.md ("target.json").

Exit codes: 0 chosen or harvest/hypothesize · 3 nothing selectable · 1 error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backlog_io  # noqa: E402

REFACTOR_RE = re.compile(
    r"refactor|rename|extract|clean ?up|abstract|move|reorgani[sz]", re.IGNORECASE
)
EST_RANK = {"S": 0, "M": 1, "L": 2}


class PickError(Exception):
    """Raised on bad input; caught in main() and turned into exit(1)."""


def parse_args(argv):
    p = argparse.ArgumentParser(description="Nightshift task picker")
    p.add_argument("--config", required=True)
    p.add_argument("--backlog", required=True)
    p.add_argument("--scores", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--iter", required=True, type=int)
    p.add_argument("--out", required=True)
    return p.parse_args(argv)


def load_json(path, required=True):
    p = Path(path)
    if not p.exists():
        if required:
            raise PickError(f"missing file: {path}")
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise PickError(f"bad JSON in {path}: {e}") from e


def load_state(path):
    state = load_json(path, required=False)
    if state is None:
        state = {}
    state.setdefault("lockout", {})
    state.setdefault("picks_history", [])
    state.setdefault("rung", 1)
    return state


def load_last_composite_row(path):
    """Return the last scores.jsonl row whose composite is not null."""
    p = Path(path)
    if not p.exists():
        raise PickError(f"missing scores file: {path}")
    last = None
    with open(p, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise PickError(f"bad JSON in {path} line {lineno}: {e}") from e
            if row.get("composite") is not None:
                last = row
    if last is None:
        raise PickError(f"no scores row with non-null composite in {path}")
    return last


def enabled_scorers(config):
    return {
        s["name"]: s
        for s in config.get("scorers", [])
        if s.get("enabled", True) and "name" in s
    }


def compute_headrooms(scorers, dims):
    """{name: (headroom, value, target, weight)} for each enabled scorer."""
    out = {}
    for name, sc in scorers.items():
        weight = sc.get("weight", 0)
        target = sc.get("target", 100)
        entry = dims.get(name) or {}
        value = entry.get("value")
        if value is None:
            value = 0
        raw = max(0, target - value)
        headroom = weight * raw
        out[name] = {
            "headroom": headroom,
            "value": value,
            "target": target,
            "weight": weight,
            "raw": raw,
        }
    return out


def compute_new_lockouts(picks_history, scorers, iter_now):
    """Dimensions whose last two picks both moved it less than its eps."""
    by_dim = {}
    for entry in picks_history:
        dim = entry.get("dimension")
        if dim is None:
            continue
        by_dim.setdefault(dim, []).append(entry)

    new_lockouts = {}
    for dim, entries in by_dim.items():
        last_two = entries[-2:]
        if len(last_two) < 2:
            continue
        eps = scorers.get(dim, {}).get("eps", 0)
        deltas = [abs(e.get("delta_after") or 0) for e in last_two]
        if all(d < eps for d in deltas):
            new_lockouts[dim] = iter_now + 2
    return new_lockouts


def active_lockout_dims(lockout, iter_now):
    return {dim for dim, until in lockout.items() if iter_now <= until}


def close_rows_at_target(rows, headrooms):
    """Close open rows whose dimension has reached its target. Returns changed?"""
    changed = False
    for row in rows:
        dim = row.get("dimension")
        info = headrooms.get(dim)
        if info is None:
            continue
        if row.get("status") == "open" and info["value"] >= info["target"]:
            row["status"] = "done"
            row["note"] = "dimension at target"
            changed = True
    return changed


def normalize_rows(rows):
    """Freeze rows with attempts>=2, bump refactor-titled rows to rung 2."""
    changed = False
    for row in rows:
        if row.get("attempts", 0) >= 2 and row.get("status") != "frozen":
            row["status"] = "frozen"
            changed = True
        title = row.get("title", "")
        if (
            isinstance(title, str)
            and REFACTOR_RE.search(title)
            and row.get("rung", 1) == 1
        ):
            row["rung"] = 2
            changed = True
    return changed


def eligible_candidates(rows, rung, iter_now, excluded_dims):
    out = []
    for row in rows:
        if row.get("status") != "open":
            continue
        if row.get("dimension") == "none":
            continue
        if row.get("rung", 1) > rung:
            continue
        if row.get("source") == "planner" and row.get("iter_added", 0) > iter_now - 2:
            continue
        if row.get("dimension") in excluded_dims:
            continue
        out.append(row)
    return out


def sort_key(row, index):
    est = EST_RANK.get(row.get("est"), len(EST_RANK))
    attempts = row.get("attempts", 0)
    return (est, attempts, index)


def candidate_summary(rows, limit=10):
    return [
        {
            "id": r.get("id"),
            "dimension": r.get("dimension"),
            "est": r.get("est"),
            "attempts": r.get("attempts", 0),
        }
        for r in rows[:limit]
    ]


def build_target(
    iter_now, rung, dimension, headroom, task_ids, mode, reason, candidates,
    lockout_list=None,
):
    return {
        "iter": iter_now,
        "rung": rung,
        "dimension": dimension,
        "headroom": headroom,
        "task_ids": task_ids,
        "lockout": sorted(lockout_list) if lockout_list else [],
        "mode": mode,
        "reason": reason,
        "candidates_considered": candidate_summary(candidates),
    }


def run(args):
    config = load_json(args.config)
    state = load_state(args.state)
    last_row = load_last_composite_row(args.scores)
    dims = last_row.get("dims", {})

    scorers = enabled_scorers(config)
    if not scorers:
        raise PickError("no enabled scorers in config")

    headrooms = compute_headrooms(scorers, dims)

    lockout = dict(state.get("lockout", {}))
    new_lockouts = compute_new_lockouts(
        state.get("picks_history", []), scorers, args.iter
    )
    effective_lockout = dict(lockout)
    effective_lockout.update(new_lockouts)
    excluded_dims = active_lockout_dims(effective_lockout, args.iter)

    rows = backlog_io.load(args.backlog)

    backlog_changed = False
    backlog_changed |= close_rows_at_target(rows, headrooms)
    backlog_changed |= normalize_rows(rows)
    if backlog_changed:
        backlog_io.dump(rows, args.backlog)

    rung = state.get("rung", 1)

    state_patch = {}
    if new_lockouts:
        state_patch["lockout"] = new_lockouts

    picks_history = state.get("picks_history", [])
    checkpoint = False
    checkpoint_note = None
    last3 = picks_history[-3:]
    if len(last3) == 3 and len({e.get("dimension") for e in last3}) == 1:
        checkpoint = True
        checkpoint_note = (
            f"{last3[-1].get('dimension')} picked 3× in a row "
            "— verify GOAL.md wants this"
        )

    all_candidates = eligible_candidates(rows, rung, args.iter, excluded_dims)

    # Which dimensions have at least one eligible candidate.
    dims_with_candidates = {r["dimension"] for r in all_candidates}
    ranked_dims = sorted(
        (d for d in headrooms if d in dims_with_candidates),
        key=lambda d: (-headrooms[d]["headroom"], d),
    )

    target = None
    exit_code = 0

    if ranked_dims:
        chosen_dim = ranked_dims[0]
        info = headrooms[chosen_dim]
        dim_candidates = [r for r in all_candidates if r["dimension"] == chosen_dim]
        indexed = list(enumerate(rows))
        index_of = {id(r): i for i, r in indexed}
        dim_candidates.sort(key=lambda r: sort_key(r, index_of[id(r)]))
        chosen_row = dim_candidates[0]

        reason = (
            f"{chosen_dim} has the largest weighted headroom "
            f"({info['weight']} × {round(info['raw'], 1)})"
        )
        target = build_target(
            args.iter,
            rung,
            chosen_dim,
            round(info["headroom"], 1),
            [chosen_row["id"]],
            "task",
            reason,
            dim_candidates,
            lockout_list=excluded_dims,
        )
    else:
        if rung == 1:
            mode = "harvest"
            new_rung = 2
            reason = (
                "no open, eligible candidates remain at rung 1; "
                "requesting harvest to add rows at rung 2"
            )
        elif rung == 2:
            mode = "hypothesize"
            new_rung = 3
            reason = (
                "no open, eligible candidates remain at rung 2 after harvest; "
                "requesting hypothesis generation at rung 3"
            )
        else:
            mode = "stop"
            new_rung = rung
            reason = (
                "no open, eligible candidates remain at rung 3; "
                "nothing left to pick, stopping"
            )
            exit_code = 3

        if new_rung != rung:
            state_patch["rung"] = new_rung

        target = build_target(
            args.iter, new_rung, None, 0, [], mode, reason, [],
            lockout_list=excluded_dims,
        )

    if checkpoint:
        target["checkpoint"] = True
        target["checkpoint_note"] = checkpoint_note

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(target, f, indent=2)
        f.write("\n")

    print(json.dumps(target))
    print(json.dumps({"state_patch": state_patch}))

    return exit_code


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run(args)
    except PickError as e:
        print(f"pick.py: error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # unexpected -> still a bad-input-shaped failure
        print(f"pick.py: unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
