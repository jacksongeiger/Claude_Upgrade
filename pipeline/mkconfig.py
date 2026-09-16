#!/usr/bin/env python3
"""mkconfig.py — a Nightshift config for a project that has a spec but no
`/jg-loop init` yet (a brand-new project has no tests to baseline, so
init's proposal step would refuse).

    mkconfig.py --project <dir> --spec <spec.json> --out <config.json> [--cap USD]

Reads loop/assess.py for setup_cmd/test_cmd, `.pipeline/scorers.proposed.json`
(from spec_check --derive) for the scorer list, and the spec's stack for
serve_cmd/port. Writes the config and the manifest next to it. Exit 0; 1 on
usage; 4 when assess.py cannot run.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
LOOP = KIT.parent / "loop"
sys.path.insert(0, str(LOOP))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cap", type=float, default=None)
    a = p.parse_args(argv)
    project = Path(a.project).resolve()
    spec = json.loads(Path(a.spec).read_text())
    try:
        proc = subprocess.run([sys.executable, str(LOOP / "assess.py"), "--project", str(project)],
                              capture_output=True, text=True, timeout=300, check=True)
        assess = json.loads(proc.stdout)
    except Exception as e:  # noqa: BLE001
        print(f"mkconfig: assess.py failed: {e}", file=sys.stderr)
        return 4
    import init as loop_init  # noqa: E402
    setup_cmd, test_cmd = loop_init.guess_commands(assess)
    tests = assess.get("tests") or {}
    proposed = project / ".pipeline" / "scorers.proposed.json"
    entries = [{"name": "tests", "weight": 1.0}]
    if proposed.exists():
        data = json.loads(proposed.read_text())
        entries = data.get("scorers", []) if isinstance(data, dict) else data
        entries = [e if isinstance(e, dict) else {"name": e, "weight": 1.0} for e in entries] or entries
    scorer_names = [e["name"] for e in entries]
    weights = {e["name"]: e.get("weight", 1.0) for e in entries}
    serve = (spec.get("stack") or {}).get("serve") or {}
    scorers = []
    for name in scorer_names:
        entry = {"name": name, "weight": weights.get(name, 0.0), "runs": 1, "eps": 0.5, "target": 100}
        if name == "tests":
            entry.update({"cmd": tests.get("test_cmd") or test_cmd or "true",
                          "coverage_cmd": tests.get("coverage_cmd"), "coverage_file": tests.get("coverage_file")})
        elif name == "lighthouse":
            urls = sorted({c["url"] for f in spec.get("features", []) for c in f.get("acceptance", []) if c.get("type") == "lighthouse"}) or ["/"]
            entry.update({"serve_cmd": serve.get("cmd"), "port": serve.get("port"),
                          "urls": [f"http://127.0.0.1:{serve.get('port', 0)}{u}" for u in urls]})
        elif name == "persona":
            tasks = [{"task": c["task"], "max_steps": c.get("max_steps", 6), "persona": c.get("persona", "a first-time user")}
                     for f in spec.get("features", []) for c in f.get("acceptance", []) if c.get("type") == "persona"]
            entry.update({"serve_cmd": serve.get("cmd"), "port": serve.get("port"), "tasks": tasks})
        elif name == "perf":
            bench = (assess.get("bench") or {}).get("cmd")
            entry.update({"cmd": bench or "echo '{\"value\": 0}'", "script": "cmd"})
        elif name == "evals":
            entry.update({"dir": (assess.get("evals") or {}).get("dir") or "evals"})
        scorers.append(entry)
    budget = spec.get("budget") or {}
    cfg = {
        "version": 1, "project_dir": str(project),
        "slug": f"{project.name}-{__import__('hashlib').sha256(str(project).encode()).hexdigest()[:8]}",
        "main_branch": (assess.get("git") or {}).get("main_branch") or "main",
        "setup_cmd": setup_cmd, "test_cmd": tests.get("test_cmd") or test_cmd or "true",
        "cap_usd": a.cap or budget.get("nightshift_cap_usd", 25), "per_iter_max_usd": 8,
        "min_iter_usd": 1.5, "hours": 6, "max_flat": 3, "max_fanout": 3, "max_files_per_iteration": 40,
        "hypothesis_max_turns": 30, "child_max_turns": 120, "child_timeout_min": 60, "opus_allowed": True,
        "regress_eps": 3.0, "ui": {"enabled": bool((assess.get("ui") or {}).get("present"))}, "scorers": scorers,
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, indent=2) + "\n")
    subprocess.run([sys.executable, str(LOOP / "score.py"), "--manifest-write", str(out.parent / "manifest.sha256"), "--config", str(out)],
                   capture_output=True, text=True)
    goal = project / "GOAL.md"
    if goal.exists():
        (out.parent / "goal.md").write_text(goal.read_text())
    print(f"wrote {out} ({len(scorers)} scorers: {', '.join(scorer_names)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
