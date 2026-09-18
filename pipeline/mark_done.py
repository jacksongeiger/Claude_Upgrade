#!/usr/bin/env python3
"""mark_done.py — close the backlog rows of an accepted milestone.

    mark_done.py --spec spec.json --milestone m1 --backlog .loop/backlog.yaml

Rows derived from the spec (`spec-<feature id>`) for the milestone's features
get `status: done`. Other rows are untouched. Exit 0; 1 on usage.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))
import backlog_io  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--milestone", required=True)
    ap.add_argument("--backlog", required=True)
    a = ap.parse_args(argv)
    spec = json.loads(Path(a.spec).read_text())
    ids = {f"spec-{f['id']}" for f in spec.get("features", []) if f.get("milestone") == a.milestone}
    if not Path(a.backlog).exists():
        print("no backlog")
        return 0
    data = backlog_io.load(a.backlog)
    rows = data["rows"] if isinstance(data, dict) else data
    n = 0
    for r in rows:
        if r.get("id") in ids and r.get("status") != "done":
            r["status"] = "done"
            r["note"] = (r.get("note") or "") + f" accepted in build ({a.milestone})"
            n += 1
    backlog_io.dump(rows, a.backlog)
    print(f"marked {n} rows done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
