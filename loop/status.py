#!/usr/bin/env python3
"""Nightshift status reporter — the on-demand detail layer for the loop.

    python3 status.py [--project DIR] [--why] [--tail]

Plain text, stdlib only (3.9+). Reads `.loop/state.json`, `.loop/scores.jsonl`,
`.loop/iterations/<latest>/target.json`, `.loop/questions.md` and
`.loop/events.log` — never writes anything. A missing file is reported in
place, never fatal: this script always exits 0.

Contract: loop/README.md ("state.json", "scores.jsonl row", "target.json").
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SPARK_CHARS = "▁▂▃▄▅▆▇█"
STALE_SECONDS = 180


# ---------------------------------------------------------------------------
# loading helpers — never raise
# ---------------------------------------------------------------------------

def load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_jsonl(path: Path) -> list:
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return rows


def tail_lines(path: Path, n: int) -> list:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return []
    return [ln.rstrip("\n") for ln in lines[-n:]]


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def fmt1(v) -> str:
    return f"{v:.1f}" if isinstance(v, (int, float)) else "—"


def fmt2(v) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) else "0.00"


def sparkline(values) -> str:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return ""
    mn, mx = min(vals), max(vals)
    rng = mx - mn
    out = []
    for v in vals:
        idx = 3 if rng == 0 else max(0, min(7, int((v - mn) / rng * 7)))
        out.append(SPARK_CHARS[idx])
    return "".join(out)


def bar(ratio: float, width: int = 10) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))
    return "▮" * filled + "░" * (width - filled)


def heartbeat_age_s(loop_dir: Path):
    hb = loop_dir / "heartbeat"
    try:
        return datetime.now(timezone.utc).timestamp() - hb.stat().st_mtime
    except OSError:
        return None


def latest_iter_dir(loop_dir: Path):
    """The highest-numbered `.loop/iterations/<N>/` directory, or None."""
    base = loop_dir / "iterations"
    if not base.is_dir():
        return None
    best = None
    best_n = -1
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            n = int(child.name)
        except ValueError:
            continue
        if n > best_n:
            best_n = n
            best = child
    return best


# ---------------------------------------------------------------------------
# header (same content as statusline.sh's line 2, no colour / ANSI)
# ---------------------------------------------------------------------------

def build_header(state: dict, loop_dir: Path) -> str:
    phase = state.get("phase", "")
    stop_reason = state.get("stop_reason")
    run = state.get("run", "")
    it = state.get("iter", "")
    cap = state.get("cap_usd") or 0
    spent = state.get("spent_usd") or 0
    live = state.get("live_spend_usd") or 0
    score = state.get("score")
    best = state.get("best")
    delta = state.get("delta") or 0
    flat = state.get("flat", 0)
    rung = state.get("rung", "")

    if phase == "STOPPED":
        glyph = "◐" if stop_reason in ("needs-human", "flat") else "■"
    else:
        age = heartbeat_age_s(loop_dir)
        glyph = "⚠" if (age is not None and age > STALE_SECONDS) else "●"
        # Note: statusline.sh also flips to amber "◐" when questions.md grew
        # since the last statusline refresh. A one-shot CLI call has no such
        # "last refresh" to compare against, so that trigger is intentionally
        # not reproduced here.

    if glyph == "■":
        scores = load_jsonl(loop_dir / "scores.jsonl")
        composites = [r.get("composite") for r in scores if r.get("composite") is not None]
        first_s = fmt1(composites[0]) if composites else "—"
        last_s = fmt1(composites[-1]) if composites else "—"
        return (
            f"■ loop STOPPED {stop_reason} · {it} iters · "
            f"{first_s}→{last_s} · ${fmt2(spent)}/${fmt2(cap)}"
        )

    stale_prefix = ""
    if glyph == "⚠":
        age = heartbeat_age_s(loop_dir)
        stale_prefix = f"stale {int(age // 60)}m "

    scores = load_jsonl(loop_dir / "scores.jsonl")
    window = [r.get("composite") for r in scores[-12:]]
    spark = sparkline(window)

    eff = spent + live if live > 0 else spent
    tilde = "~" if live > 0 else ""
    ratio = (eff / cap) if cap > 0 else 0.0
    money = f"${fmt2(eff)}{tilde}/${fmt2(cap)}"
    arrow = "▼" if delta < 0 else "▲"

    return (
        f"{glyph} {stale_prefix}{run} · iter {it} · {phase} · "
        f"score {fmt1(score)} {arrow}{abs(delta):.1f} (best {fmt1(best)}) {spark} · "
        f"flat {flat}/3 · rung {rung} · {money} {bar(ratio)}"
    )


def build_sparkline_line(loop_dir: Path, state: dict) -> str:
    scores = load_jsonl(loop_dir / "scores.jsonl")
    composites = [r.get("composite") for r in scores[-12:]]
    non_null = [c for c in composites if isinstance(c, (int, float))]
    spark = sparkline(composites)
    first_s = fmt1(non_null[0]) if non_null else "—"
    last_s = fmt1(non_null[-1]) if non_null else "—"
    best = state.get("best")
    return f"score {spark} {first_s} → {last_s} (best {fmt1(best)})"


# ---------------------------------------------------------------------------
# scores table
# ---------------------------------------------------------------------------

def dimension_columns(rows: list) -> list:
    seen = []
    for r in rows:
        for dim in (r.get("dims") or {}).keys():
            if dim not in seen:
                seen.append(dim)
    return seen


def build_scores_table(all_rows: list) -> str:
    if not all_rows:
        return "(no scores.jsonl rows)"

    window = all_rows[-12:]
    start_index = len(all_rows) - len(window)

    # Delta against the true previous row in the full file, not just the
    # displayed window, so the first displayed row's delta is still correct.
    deltas = []
    for i, row in enumerate(window):
        full_i = start_index + i
        cur = row.get("composite")
        prev = all_rows[full_i - 1].get("composite") if full_i > 0 else None
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
            deltas.append(cur - prev)
        else:
            deltas.append(None)

    dims = dimension_columns(window)
    headers = ["iter", "Δ"] + dims + ["cost", "outcome", "task"]

    lines = []
    for row, d in zip(window, deltas):
        cells = [str(row.get("iter", "")), (f"{d:+.1f}" if d is not None else "—")]
        for dim in dims:
            entry = (row.get("dims") or {}).get(dim)
            if entry and entry.get("ok") and isinstance(entry.get("value"), (int, float)):
                cells.append(f"{entry['value']:.1f}")
            else:
                cells.append("—")
        cells.append(f"${fmt2(row.get('cost_usd'))}")
        cells.append(str(row.get("outcome", "")))
        cells.append(str(row.get("task_id", "")))
        lines.append(cells)

    widths = [len(h) for h in headers]
    for cells in lines:
        for i, c in enumerate(cells):
            widths[i] = max(widths[i], len(c))

    def fmt_row(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    out = [fmt_row(headers), fmt_row(["-" * w for w in widths])]
    out.extend(fmt_row(cells) for cells in lines)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# next pick / --why / --tail
# ---------------------------------------------------------------------------

def build_next_pick(loop_dir: Path) -> str:
    idir = latest_iter_dir(loop_dir)
    if idir is None:
        return "next pick: (no .loop/iterations/<N>/ found)"
    target = load_json(idir / "target.json")
    if target is None:
        return f"next pick: (missing or unreadable {idir / 'target.json'})"
    return (
        f"next pick: dimension={target.get('dimension')} "
        f"headroom={target.get('headroom')} rung={target.get('rung')} "
        f"mode={target.get('mode')}"
    )


def build_why(loop_dir: Path) -> str:
    idir = latest_iter_dir(loop_dir)
    if idir is None:
        return "--why: (no .loop/iterations/<N>/ found)"
    target = load_json(idir / "target.json")
    if target is None:
        return f"--why: (missing or unreadable {idir / 'target.json'})"

    out = [f"iter {target.get('iter')} — {target.get('mode')}", "", "reason:", f"  {target.get('reason', '')}"]

    candidates = target.get("candidates_considered") or []
    out.append("")
    if candidates:
        out.append("candidates considered:")
        headers = ["id", "dimension", "est", "attempts"]
        rows = [
            [str(c.get("id", "")), str(c.get("dimension", "")), str(c.get("est", "")), str(c.get("attempts", ""))]
            for c in candidates
        ]
        widths = [len(h) for h in headers]
        for r in rows:
            for i, c in enumerate(r):
                widths[i] = max(widths[i], len(c))

        def fmt_row(cells):
            return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

        out.append(fmt_row(headers))
        out.append(fmt_row(["-" * w for w in widths]))
        out.extend(fmt_row(r) for r in rows)
    else:
        out.append("candidates considered: (none)")

    lockout = target.get("lockout") or []
    out.append("")
    out.append(f"lockout: {', '.join(lockout) if lockout else '(none)'}")

    out.append("")
    if target.get("checkpoint"):
        out.append(f"checkpoint: {target.get('checkpoint_note', '')}")
    else:
        out.append("checkpoint: (none)")

    return "\n".join(out)


def build_tail(loop_dir: Path) -> str:
    idir = latest_iter_dir(loop_dir)
    if idir is None:
        return "--tail: (no .loop/iterations/<N>/ found)"
    iter_n = idir.name
    run_dir = loop_dir / "run"
    # run.sh currently writes stream-<iter>.jsonl; loop/README's stream file
    # naming has drifted from the original .txt convention, so try both.
    for candidate in (run_dir / f"stream-{iter_n}.txt", run_dir / f"stream-{iter_n}.jsonl"):
        if candidate.exists():
            lines = tail_lines(candidate, 20)
            return f"--tail ({candidate.name}, last {len(lines)} lines):\n" + "\n".join(lines)
    return f"--tail: (no stream-{iter_n}.txt or .jsonl found under {run_dir})"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_questions_line(loop_dir: Path) -> str:
    qfile = loop_dir / "questions.md"
    if not qfile.exists():
        return "open questions: (no questions.md)"
    text = qfile.read_text(encoding="utf-8", errors="replace")
    count = sum(1 for ln in text.splitlines() if ln.startswith("## "))
    return f"open questions: {count}"


def build_events_line(loop_dir: Path) -> str:
    efile = loop_dir / "events.log"
    if not efile.exists():
        return "events.log: (missing)"
    lines = tail_lines(efile, 5)
    if not lines:
        return "events.log: (empty)"
    return "last events:\n" + "\n".join(f"  {ln}" for ln in lines)


def run(argv=None) -> int:
    p = argparse.ArgumentParser(description="Nightshift status reporter")
    p.add_argument("--project", default=".", help="project directory (default: cwd)")
    p.add_argument("--why", action="store_true", help="explain the latest pick")
    p.add_argument("--tail", action="store_true", help="tail the latest iteration's stream")
    args = p.parse_args(argv)

    project = Path(args.project).resolve()
    loop_dir = project / ".loop"

    if not loop_dir.is_dir():
        print(f"no .loop/ directory under {project} — loop not initialized here")
        return 0

    if args.tail:
        print(build_tail(loop_dir))
        return 0

    if args.why:
        print(build_why(loop_dir))
        return 0

    state_path = loop_dir / "state.json"
    state = load_json(state_path)
    if state is None:
        print(f"missing or unreadable {state_path}")
        return 0

    print(build_header(state, loop_dir))
    print()
    print(build_sparkline_line(loop_dir, state))
    print()

    scores_path = loop_dir / "scores.jsonl"
    if not scores_path.exists():
        print(f"missing {scores_path}")
    else:
        print(build_scores_table(load_jsonl(scores_path)))
    print()

    print(build_next_pick(loop_dir))
    print()
    print(build_questions_line(loop_dir))
    print()
    print(build_events_line(loop_dir))

    return 0


def main(argv=None) -> int:
    try:
        return run(argv)
    except Exception as e:  # status.py must never crash the terminal session
        print(f"status.py: unexpected error: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
