#!/usr/bin/env python3
"""FEEDBACK.md (+ Sentry export) -> backlog rows (pipeline README, "Stage 8 —
/jg-feedback").

    python3 feedback.py --inbox FEEDBACK.md --backlog .loop/backlog.yaml
                         [--sentry export.json] [--dry-run]

Inbox format: `## <anything>` headings with `- ` bullets underneath. Only
headings that do not already start with "## processed" are read. Each bullet
becomes a backlog row (id = fb-<8 hex sha1 of the normalised title>,
dimension by keyword, status open/needs-human). Rows already present in the
backlog (by id) are skipped. `--sentry` adds one row per {"title","count",
"culprit"} entry, dimension "tests".

After appending, processed headings are rewritten to "## processed
<original heading>" (bullets kept as-is). `--dry-run` prints what would be
added and leaves both files untouched.

Prints "added N rows" then one "<id> <title>" line per row added.

Exit codes: 0 always (per pipeline/README.md "Exit codes") · 1 usage error.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HEADING_RE = re.compile(r"^##\s+(.*)$")
BULLET_RE = re.compile(r"^-\s+(.*)$")

DIM_RULES = [
    (re.compile(r"crash|error|exception|traceback|500|broken", re.I), "tests"),
    (re.compile(r"slow|lag|seconds|timeout|performance", re.I), "perf"),
    (re.compile(r"confus|can't find|cannot find|unclear|where is|hard to|didn't know", re.I), "persona"),
]


def map_dimension(title):
    for pat, dim in DIM_RULES:
        if pat.search(title):
            return dim
    return "none"


def normalize_title(title):
    return re.sub(r"\s+", " ", title.strip().lower())


def make_id(title):
    digest = hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()
    return f"fb-{digest[:8]}"


def build_row(bullet_text, heading):
    trimmed = bullet_text.strip()
    title = trimmed[:140]
    dim = map_dimension(trimmed)
    status = "open" if dim != "none" else "needs-human"
    return {
        "id": make_id(trimmed),
        "title": title,
        "dimension": dim,
        "source": "production",
        "status": status,
        "rung": 1,
        "est": "S",
        "attempts": 0,
        "iter_added": 0,
        "note": f"from FEEDBACK.md {heading}",
    }


def build_sentry_row(entry):
    raw_title = str(entry.get("title", "")).strip()
    title = raw_title[:140]
    count = entry.get("count")
    culprit = entry.get("culprit", "")
    return {
        "id": make_id(raw_title),
        "title": title,
        "dimension": "tests",
        "source": "production",
        "status": "open",
        "rung": 1,
        "est": "S",
        "attempts": 0,
        "iter_added": 0,
        "note": f"sentry ×{count} {culprit}",
    }


def parse_inbox(lines):
    """Return a list of {line_idx, heading, processed, bullets}."""
    sections = []
    current = None
    for idx, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            heading = m.group(1).rstrip()
            processed = heading.strip().lower().startswith("processed")
            current = {"line_idx": idx, "heading": heading, "processed": processed, "bullets": []}
            sections.append(current)
            continue
        if current is not None:
            bm = BULLET_RE.match(line.strip())
            if bm:
                current["bullets"].append(bm.group(1).strip())
    return sections


def load_backlog(path, backlog_io):
    p = Path(path)
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return []
    data = backlog_io.loads(text)
    if isinstance(data, dict):
        return list(data.get("rows", []))
    return list(data)


def build_parser():
    ap = argparse.ArgumentParser(description="FEEDBACK.md (+ Sentry export) -> backlog rows")
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--backlog", required=True)
    ap.add_argument("--sentry")
    ap.add_argument("--dry-run", action="store_true")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    sys.path.insert(0, str(ROOT / "loop"))
    import backlog_io  # noqa: E402

    rows = load_backlog(args.backlog, backlog_io)
    ids = {r.get("id") for r in rows}
    added = []

    inbox_path = Path(args.inbox)
    inbox_text = inbox_path.read_text(encoding="utf-8") if inbox_path.exists() else ""
    lines = inbox_text.splitlines()
    sections = parse_inbox(lines)

    touched_idxs = []
    for sec in sections:
        if sec["processed"] or not sec["bullets"]:
            continue
        touched_idxs.append(sec["line_idx"])
        for bullet in sec["bullets"]:
            row = build_row(bullet, sec["heading"])
            if row["id"] in ids:
                continue
            ids.add(row["id"])
            rows.append(row)
            added.append(row)

    if args.sentry:
        entries = json.loads(Path(args.sentry).read_text(encoding="utf-8"))
        for entry in entries:
            row = build_sentry_row(entry)
            if row["id"] in ids:
                continue
            ids.add(row["id"])
            rows.append(row)
            added.append(row)

    print(f"added {len(added)} rows")
    for row in added:
        print(f"{row['id']} {row['title']}")

    if args.dry_run:
        return 0

    Path(args.backlog).parent.mkdir(parents=True, exist_ok=True)
    backlog_io.dump(rows, args.backlog)

    if touched_idxs:
        idx_set = set(touched_idxs)
        new_lines = list(lines)
        for sec in sections:
            if sec["line_idx"] in idx_set:
                new_lines[sec["line_idx"]] = f"## processed {sec['heading']}"
        inbox_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
