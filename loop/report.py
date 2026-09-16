#!/usr/bin/env python3
"""Nightshift morning report.

    python3 report.py [--project DIR]

Writes <project>/.loop/report.md and .loop/report.html (self-contained,
inline CSS, a simple inline-SVG score line chart — no external assets) and
prints the .md. Plain English, for someone who does not read code.

Reads only files under <project>/.loop/ — no config.json / assess.py / a
running loop are required. Every input is optional; a missing file means its
section says "none". Exits 0 always unless the arguments are wrong (per
loop/README.md's "Exit codes").

Python 3.9+ stdlib only.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# tiny, dependency-free readers
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def load_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _parse_scalar(val):
    val = val.strip()
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        return val[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    if val == "true":
        return True
    if val == "false":
        return False
    if val == "":
        return ""
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    return val


def load_backlog_rows(path):
    """loop/backlog_io.py contract: load(path)->{"rows":[...]}. Imported
    lazily; falls back to a minimal reader for the same fixed schema
    (flat scalars only) when backlog_io isn't available or the file doesn't
    parse as JSON-in-YAML-clothing.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        kit = Path(__file__).resolve().parent
        sys.path.insert(0, str(kit))
        import backlog_io  # noqa: E402
        data = backlog_io.load(p)
        if isinstance(data, dict):
            return data.get("rows", [])
        if isinstance(data, list):
            return data
    except Exception:
        pass

    rows = []
    current = None
    for raw_line in p.read_text().splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped == "rows:":
            continue
        m = re.match(r"^\s*-\s*(\w+):\s*(.*)$", raw_line)
        if m and raw_line.lstrip().startswith("-"):
            if current is not None:
                rows.append(current)
            current = {m.group(1): _parse_scalar(m.group(2))}
            continue
        m2 = re.match(r"^\s+(\w+):\s*(.*)$", raw_line)
        if m2 and current is not None:
            current[m2.group(1)] = _parse_scalar(m2.group(2))
    if current is not None:
        rows.append(current)
    return rows


def _iter_num(name):
    try:
        return int(name)
    except ValueError:
        return -1


def child_dir(loop_dir):
    """Where the planner child's artifacts live.

    The child runs inside the loop worktree and native worktree isolation
    denies writes into the main checkout, so iterations/, backlog.yaml and
    questions.md are under <project>/.loop/wt/loop/.loop/. The driver's own
    state (state.json, scores.jsonl, events) stays in <project>/.loop/.
    """
    wt = loop_dir / "wt" / "loop" / ".loop"
    return wt if wt.is_dir() else loop_dir


def find_merged_tasks(loop_dir):
    """[(iter_num, task_id, iter_dir), ...] from every iterations/N/summary.md
    'merged: <id> [<id> ...]' line, in iteration order."""
    out = []
    iterations_dir = child_dir(loop_dir) / "iterations"
    if not iterations_dir.is_dir():
        return out
    for iter_dir in sorted(iterations_dir.iterdir(), key=lambda p: _iter_num(p.name)):
        if not iter_dir.is_dir():
            continue
        summary = iter_dir / "summary.md"
        if not summary.exists():
            continue
        n = _iter_num(iter_dir.name)
        for line in summary.read_text().splitlines():
            m = re.match(r"^\s*merged:\s*(.+)$", line, re.I)
            if not m:
                continue
            for tid in re.split(r"[,\s]+", m.group(1).strip()):
                if tid:
                    out.append((n, tid, iter_dir))
    return out


# ---------------------------------------------------------------------------
# data gathering
# ---------------------------------------------------------------------------

def gather(project_dir):
    project_dir = Path(project_dir)
    loop_dir = project_dir / ".loop"

    state = load_json(loop_dir / "state.json", {}) or {}
    scores = load_jsonl(loop_dir / "scores.jsonl")
    backlog_rows = load_backlog_rows(child_dir(loop_dir) / "backlog.yaml")

    scores_sorted = sorted(scores, key=lambda r: r.get("iter", 0))
    composites = [(r.get("iter", 0), r.get("composite")) for r in scores_sorted]
    composite_by_iter = {i: c for i, c in composites}

    first_score = composites[0][1] if composites else None
    last_score = composites[-1][1] if composites else None
    n_iterations = max((r.get("iter", 0) for r in scores_sorted), default=0)

    run = state.get("run") or "(not started)"
    spent = state.get("spent_usd") or 0
    cap = state.get("cap_usd") or 0
    stop_reason = state.get("stop_reason") or "not stopped yet"

    tasks = []
    for n, tid, iter_dir in find_merged_tasks(loop_dir):
        review = load_json(iter_dir / "tasks" / tid / "review.json", {}) or {}
        plan = load_json(iter_dir / "plan.json", {}) or {}
        goal = next(
            (s.get("goal") for s in plan.get("subtasks", []) if s.get("id") == tid), None,
        )
        prev_iters = [i for i in composite_by_iter if i < n]
        prev_c = composite_by_iter.get(max(prev_iters)) if prev_iters else None
        cur_c = composite_by_iter.get(n)
        delta = (cur_c - prev_c) if (cur_c is not None and prev_c is not None) else None

        shots_dir = iter_dir / "tasks" / tid / "shots"
        shots = sorted(p.name for p in shots_dir.glob("*")) if shots_dir.is_dir() else []

        tasks.append({
            "iter": n, "id": tid,
            "goal_line": review.get("goal_line"),
            "what_changed": goal,
            "delta": delta,
            "test_delta": review.get("test_delta") or {},
            "screenshots": shots,
            "score_gaming_suspected": bool(review.get("score_gaming_suspected")),
        })

    questions_path = child_dir(loop_dir) / "questions.md"
    questions_text = questions_path.read_text().strip() if questions_path.exists() else ""

    needs_human = [r for r in backlog_rows if r.get("status") == "needs-human"]
    rdx_rows = [r for r in backlog_rows if r.get("source") == "rdx"]

    return {
        "run": run, "spent": spent, "cap": cap, "stop_reason": stop_reason,
        "n_iterations": n_iterations, "first_score": first_score, "last_score": last_score,
        "composites": composites, "tasks": tasks,
        "questions_text": questions_text, "needs_human": needs_human, "rdx_rows": rdx_rows,
    }


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------

def fmt_score(v):
    return "n/a" if v is None else f"{v:.1f}"


def fmt_delta(v):
    return "n/a" if v is None else f"{v:+.1f}"


def render_markdown(data):
    lines = []
    lines.append(f"# Nightshift report — {data['run']}")
    lines.append("")
    lines.append(
        f"**{data['run']} · {data['n_iterations']} iterations · "
        f"score {fmt_score(data['first_score'])} → {fmt_score(data['last_score'])} · "
        f"${data['spent']:.2f} of ${data['cap']:.2f} · stopped: {data['stop_reason']}**"
    )
    lines.append("")

    lines.append("## What changed")
    if not data["tasks"]:
        lines.append("none")
    else:
        for t in data["tasks"]:
            warn = " ⚠ possible score gaming" if t["score_gaming_suspected"] else ""
            lines.append(f"### iter {t['iter']} — {t['id']}{warn}")
            gl = t["goal_line"]
            lines.append(f"- GOAL: \"{gl}\"" if gl else "- GOAL: none recorded")
            lines.append(f"- what changed: {t['what_changed'] or 'not recorded'}")
            lines.append(f"- score delta: {fmt_delta(t['delta'])}")
            td = t["test_delta"]
            lines.append(f"- tests: +{td.get('added', 0)} / -{td.get('removed', 0)}")
            if t["screenshots"]:
                lines.append(f"- screenshots: {', '.join(t['screenshots'])}")
            lines.append("")
    lines.append("")

    lines.append("## Needs you")
    if data["questions_text"]:
        lines.append(data["questions_text"])
    else:
        lines.append("none")
    if data["needs_human"]:
        lines.append("")
        lines.append("Backlog rows needing a human:")
        for r in data["needs_human"]:
            lines.append(f"- {r.get('id')}: {r.get('title')}")
    lines.append("")

    lines.append("## Tools the loop wanted")
    if data["rdx_rows"]:
        for r in data["rdx_rows"]:
            lines.append(f"- {r.get('title')}")
    else:
        lines.append("none")
    lines.append("")

    lines.append("## Bringing this into main")
    lines.append("Not run automatically. The exact command a human would run:")
    lines.append("")
    lines.append(f"    git merge --no-ff {data['run']}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# html — self-contained, inline CSS, inline SVG line chart, light+dark
# ---------------------------------------------------------------------------

def render_svg_chart(composites, width=560, height=170):
    pts = [(i, c) for i, c in composites if c is not None]
    if len(pts) < 2:
        return '<p class="muted">not enough scored iterations yet for a chart.</p>'

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if ymax == ymin:
        ymin, ymax = ymin - 1, ymax + 1
    pad = 28

    def sx(x):
        if xmax == xmin:
            return float(pad)
        return pad + (x - xmin) / (xmax - xmin) * (width - 2 * pad)

    def sy(y):
        return height - pad - (y - ymin) / (ymax - ymin) * (height - 2 * pad)

    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(pts))
    dots = "".join(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" class="dot"/>' for x, y in pts)
    first_label = (
        f'<text x="{sx(pts[0][0]):.1f}" y="{max(sy(pts[0][1]) - 10, 12):.1f}" class="label">'
        f'{pts[0][1]:.1f}</text>'
    )
    last_label = (
        f'<text x="{sx(pts[-1][0]):.1f}" y="{max(sy(pts[-1][1]) - 10, 12):.1f}" '
        f'class="label" text-anchor="end">{pts[-1][1]:.1f}</text>'
    )
    aria = (
        f"composite score across {len(pts)} scored iterations, "
        f"from {pts[0][1]:.1f} to {pts[-1][1]:.1f}"
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(aria)}" class="chart">'
        f'<line x1="{pad}" y1="{height - pad:.1f}" x2="{width - pad}" y2="{height - pad:.1f}" class="axis"/>'
        f'<path d="{path}" class="series"/>'
        f'{dots}{first_label}{last_label}'
        f'</svg>'
    )


CSS = """
:root {
  --bg: #ffffff; --surface: #f8fafc; --ink: #0f172a; --ink-muted: #64748b;
  --border: #e2e8f0; --accent: #2563eb; --warn: #b45309;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0b1220; --surface: #111827; --ink: #e5e7eb; --ink-muted: #94a3b8;
    --border: #1f2937; --accent: #60a5fa; --warn: #fbbf24;
  }
}
:root[data-theme="dark"] {
  --bg: #0b1220; --surface: #111827; --ink: #e5e7eb; --ink-muted: #94a3b8;
  --border: #1f2937; --accent: #60a5fa; --warn: #fbbf24;
}
* { box-sizing: border-box; }
body {
  background: var(--bg); color: var(--ink); margin: 0; padding: 24px 16px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.5;
}
.wrap { max-width: 760px; margin: 0 auto; }
.headline {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; font-size: 1.05rem;
}
h2 { border-bottom: 1px solid var(--border); padding-bottom: 6px; margin-top: 2rem; }
.task {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; margin: 12px 0;
}
.task h3 { margin: 0 0 8px 0; font-size: 1rem; }
.muted { color: var(--ink-muted); }
.warn { color: var(--warn); font-weight: 600; }
ul { margin: 4px 0; padding-left: 20px; }
code, pre {
  background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
  padding: 2px 6px; font-size: 0.9em;
}
pre { padding: 10px 12px; overflow-x: auto; }
.chart { width: 100%; height: auto; display: block; margin: 12px 0; }
.chart .axis { stroke: var(--border); stroke-width: 1; }
.chart .series { fill: none; stroke: var(--accent); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.chart .dot { fill: var(--accent); }
.chart .label { fill: var(--ink-muted); font-size: 11px; }
"""


def render_html(data):
    chart = render_svg_chart(data["composites"])

    if data["tasks"]:
        task_blocks = []
        for t in data["tasks"]:
            warn = ' <span class="warn">⚠ possible score gaming</span>' if t["score_gaming_suspected"] else ""
            gl = html.escape(t["goal_line"]) if t["goal_line"] else "none recorded"
            what = html.escape(t["what_changed"] or "not recorded")
            td = t["test_delta"]
            shots_html = ""
            if t["screenshots"]:
                shots_html = (
                    "<p>screenshots: "
                    + ", ".join(html.escape(s) for s in t["screenshots"])
                    + "</p>"
                )
            task_blocks.append(
                f'<div class="task"><h3>iter {t["iter"]} — {html.escape(t["id"])}{warn}</h3>'
                f'<p>GOAL: "{gl}"</p>'
                f'<p>what changed: {what}</p>'
                f'<p>score delta: {fmt_delta(t["delta"])} · tests: '
                f'+{td.get("added", 0)} / -{td.get("removed", 0)}</p>'
                f'{shots_html}</div>'
            )
        tasks_html = "\n".join(task_blocks)
    else:
        tasks_html = '<p class="muted">none</p>'

    if data["questions_text"]:
        questions_html = f"<pre>{html.escape(data['questions_text'])}</pre>"
    else:
        questions_html = '<p class="muted">none</p>'
    needs_human_html = ""
    if data["needs_human"]:
        items = "".join(
            f"<li>{html.escape(str(r.get('id')))}: {html.escape(str(r.get('title')))}</li>"
            for r in data["needs_human"]
        )
        needs_human_html = f"<p>Backlog rows needing a human:</p><ul>{items}</ul>"

    if data["rdx_rows"]:
        rdx_html = "<ul>" + "".join(
            f"<li>{html.escape(str(r.get('title')))}</li>" for r in data["rdx_rows"]
        ) + "</ul>"
    else:
        rdx_html = '<p class="muted">none</p>'

    run_esc = html.escape(str(data["run"]))
    headline = (
        f"{run_esc} · {data['n_iterations']} iterations · "
        f"score {fmt_score(data['first_score'])} &rarr; {fmt_score(data['last_score'])} · "
        f"${data['spent']:.2f} of ${data['cap']:.2f} · stopped: {html.escape(str(data['stop_reason']))}"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nightshift report</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>Nightshift report</h1>
  <p class="headline">{headline}</p>
  {chart}

  <h2>What changed</h2>
  {tasks_html}

  <h2>Needs you</h2>
  {questions_html}
  {needs_human_html}

  <h2>Tools the loop wanted</h2>
  {rdx_html}

  <h2>Bringing this into main</h2>
  <p>Not run automatically. The exact command a human would run:</p>
  <pre>git merge --no-ff {run_esc}</pre>
</div>
</body>
</html>
"""


def write_report(project_dir):
    project_dir = Path(project_dir)
    loop_dir = project_dir / ".loop"
    loop_dir.mkdir(parents=True, exist_ok=True)

    data = gather(project_dir)
    md = render_markdown(data)
    htm = render_html(data)

    (loop_dir / "report.md").write_text(md)
    (loop_dir / "report.html").write_text(htm)
    return md


def build_parser():
    ap = argparse.ArgumentParser(description="Nightshift morning report")
    ap.add_argument("--project", default=".")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    md = write_report(args.project)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
