#!/usr/bin/env python3
"""Nightshift bootstrap.

    python3 init.py --propose [--project DIR] [--assess <json file>]
                     [--goal <goal.md>] [--cap 25] [--hours 6]
    python3 init.py --write   [--project DIR] [--proposal <json>]

See loop/README.md for every schema referenced here ("config.json",
"backlog.yaml", "scores.jsonl row", "Directory layout", "Scorer contract").

Python 3.9+ stdlib only.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent           # .../loop
ROOT = KIT.parent                                # .../Claude_Upgrade
AGENTS_DIR = ROOT / "agents"

FLOORS = {"tests": 0.5, "perf": 1.0, "evals": 1.0, "lighthouse": 1.5}
BASE_WEIGHTS = {"tests": 0.5, "perf": 0.3, "evals": 0.3, "lighthouse": 0.2}
TARGETS = {"tests": 95, "perf": 90, "evals": 90, "lighthouse": 90}

PERF_GOAL_RE = re.compile(r"speed|latency|throughput|perf", re.I)
REFUSAL_KEYWORD_RE = re.compile(
    r"test|coverage|perf|speed|latency|eval|accuracy|lighthouse|a11y|accessib", re.I
)
REFACTOR_RE = re.compile(r"refactor|rename|extract|clean ?up|abstract|move|reorgani[sz]", re.I)

# order matters: first match wins
DIM_KEYWORDS = [
    (re.compile(r"\btest", re.I), "tests"),
    (re.compile(r"\bslow\b|\bperf", re.I), "perf"),
    (re.compile(r"a11y|accessib", re.I), "lighthouse"),
]

GITIGNORE_LINES = [
    ".loop/run/", ".loop/wt/", ".loop/heartbeat", ".loop/state.json",
    ".loop/events.*", ".claude/worktrees/",
]

CONFIG_DEFAULTS = {
    "per_iter_max_usd": 6.0,
    "min_iter_usd": 1.5,
    "max_flat": 3,
    "max_fanout": 3,
    "max_files_per_iteration": 25,
    "hypothesis_max_turns": 30,
    "child_max_turns": 80,
    "child_timeout_min": 45,
    "opus_allowed": True,
    "regress_eps": 1.0,
}


def compute_slug(project_dir):
    p = Path(project_dir).resolve()
    h = hashlib.sha256(str(p).encode()).hexdigest()[:8]
    return f"{p.name}-{h}"


# ---------------------------------------------------------------------------
# assess.py — invoked lazily as a subprocess so this file never hard-imports
# a module another builder may not have finished yet.
# ---------------------------------------------------------------------------

def run_assess(project_dir):
    script = KIT / "assess.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--project", str(project_dir)],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"assess.py failed: {proc.stderr.strip()}")
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError("assess.py produced no output")
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Scorer contract: `python3 loop/scorers/<name>.py --config <json> --workdir <dir>`
# ---------------------------------------------------------------------------

def run_scorer(name, entry, workdir, kit=KIT):
    script = Path(kit) / "scorers" / f"{name}.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--config", json.dumps(entry), "--workdir", str(workdir)],
            capture_output=True, text=True, timeout=20 * 60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"name": name, "value": 0, "ok": False, "error": str(e), "raw": {}}
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        return {
            "name": name, "value": 0, "ok": False,
            "error": f"no output (exit {proc.returncode}): {proc.stderr.strip()[:300]}",
            "raw": {},
        }
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as e:
        return {"name": name, "value": 0, "ok": False, "error": f"bad scorer output: {e}", "raw": {}}


def baseline_run(name, entry, workdir, n=3):
    """Run the scorer contract `n` times. Returns (stats, raw_results, error)."""
    results = [run_scorer(name, entry, workdir) for _ in range(n)]
    for r in results:
        if not r.get("ok", False):
            return None, results, (r.get("error") or "scorer reported ok:false")
    values = [float(r["value"]) for r in results]
    mean = sum(values) / len(values)
    spread = max(values) - min(values)
    # "CV" here is the absolute spread measure (population stdev, same units
    # as `value`) so that `eps = max(floor, 2*CV)` lands in the same units as
    # the floors (0.5/1.0/1.0/1.5 points), not a dimensionless ratio.
    cv = statistics.pstdev(values) if len(values) > 1 else 0.0
    return (
        {"mean": mean, "values": values, "spread": spread, "cv": cv, "raw": results[-1].get("raw")},
        results,
        None,
    )


def candidate_scorers(assess_data, goal_text):
    """Which scorers *policy* proposes, before baselining/refusal."""
    cands = []
    tests = assess_data.get("tests", {}) or {}
    cands.append(("tests", {
        "name": "tests",
        "cmd": tests.get("test_cmd"),
        "coverage_cmd": tests.get("coverage_cmd"),
        "coverage_file": tests.get("coverage_file"),
    }))

    bench = assess_data.get("bench", {}) or {}
    if bench.get("present") or PERF_GOAL_RE.search(goal_text or ""):
        cands.append(("perf", {"name": "perf", "bench_cmd": bench.get("cmd")}))

    evals = assess_data.get("evals", {}) or {}
    if assess_data.get("llm_calls") and evals.get("present"):
        cands.append(("evals", {"name": "evals", "dir": evals.get("dir")}))

    ui = assess_data.get("ui", {}) or {}
    if ui.get("present"):
        cands.append(("lighthouse", {
            "name": "lighthouse", "serve_cmd": ui.get("serve_cmd"), "urls": ui.get("urls") or [],
        }))

    return cands


def guess_commands(assess_data):
    stack = assess_data.get("stack", {}) or {}
    pm = stack.get("package_manager")
    languages = stack.get("languages") or []
    manifests = stack.get("manifests") or []
    tests = assess_data.get("tests", {}) or {}
    test_cmd = tests.get("test_cmd")

    def manifest_dir(names):
        for m in manifests:
            if os.path.basename(m) in names:
                d = os.path.dirname(m)
                return d or None
        return None

    if pm == "pip" or "python" in languages:
        d = manifest_dir({"requirements.txt"})
        base = "python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
        setup_cmd = f"cd {d} && {base}" if d else base
        if not test_cmd:
            test_cmd = f"cd {d} && python3 -m pytest -q" if d else "python3 -m pytest -q"
    elif pm in ("npm", "yarn", "pnpm"):
        d = manifest_dir({"package.json"})
        installer = {
            "npm": "npm ci",
            "yarn": "yarn install --frozen-lockfile",
            "pnpm": "pnpm install --frozen-lockfile",
        }[pm]
        setup_cmd = f"cd {d} && {installer}" if d else installer
        if not test_cmd:
            test_cmd = f"cd {d} && npm test" if d else "npm test"
    elif pm == "go" or "go" in languages:
        d = manifest_dir({"go.mod"})
        setup_cmd = f"cd {d} && go mod download" if d else "go mod download"
        if not test_cmd:
            test_cmd = f"cd {d} && go test ./..." if d else "go test ./..."
    elif pm == "cargo" or "rust" in languages:
        d = manifest_dir({"Cargo.toml"})
        setup_cmd = f"cd {d} && cargo fetch" if d else "cargo fetch"
        if not test_cmd:
            test_cmd = f"cd {d} && cargo test" if d else "cargo test"
    else:
        setup_cmd = "true"

    return setup_cmd, test_cmd


def scorer_cmd_for_display(entry):
    return entry.get("cmd") or entry.get("bench_cmd") or entry.get("serve_cmd") or entry.get("dir") or ""


def build_unblock_hints(assess_data):
    hints = []
    if not (assess_data.get("bench", {}) or {}).get("present"):
        hints.append("add a bench/ script (or mention speed/latency/perf in the goal) to enable perf")
    if not ((assess_data.get("evals", {}) or {}).get("present") and assess_data.get("llm_calls")):
        hints.append("add an evals/ dir of cases (the project must also call an LLM) to enable evals")
    if not (assess_data.get("ui", {}) or {}).get("present"):
        hints.append("add a UI dev server to enable lighthouse")
    hints.append(
        "or state a goal that names one of: test/coverage/perf/speed/latency/eval/accuracy/lighthouse/a11y"
    )
    return hints


def propose(project_dir, assess_data, goal_text, cap, hours):
    project_dir = Path(project_dir).resolve()
    slug = compute_slug(project_dir)
    cands = candidate_scorers(assess_data, goal_text)

    enabled = []
    dropped = []
    for name, entry in cands:
        baseline, _raw_results, err = baseline_run(name, entry, project_dir)
        if baseline is None:
            dropped.append({"name": name, "reason": err})
            continue
        floor = FLOORS[name]
        eps = max(floor, 2 * baseline["cv"])
        runs = 3 if baseline["spread"] > eps else 1
        note = (
            f"spread {baseline['spread']:.2f} > eps {eps:.2f}: raised runs to 3"
            if runs == 3 else ""
        )
        cfg_entry = dict(entry)
        cfg_entry.update({"weight": BASE_WEIGHTS[name], "runs": runs,
                           "eps": round(eps, 3), "target": TARGETS[name]})
        enabled.append({
            "entry": cfg_entry,
            "baseline": round(baseline["mean"], 3),
            "spread": round(baseline["spread"], 3),
            "cv": round(baseline["cv"], 3),
            "note": note,
            "raw": baseline["raw"],
        })

    refused = False
    refusal_reason = None
    unblock = []
    if not enabled:
        refused = True
        refusal_reason = "no scorer could be measured on this project (every candidate failed)."
        unblock = build_unblock_hints(assess_data)
    elif len(enabled) < 2 and not REFUSAL_KEYWORD_RE.search(goal_text or ""):
        refused = True
        names_present = sorted(e["entry"]["name"] for e in enabled)
        refusal_reason = (
            f"only {len(enabled)} usable scorer(s) ({', '.join(names_present)}) "
            f"and the goal names none of them."
        )
        unblock = build_unblock_hints(assess_data)

    total_w = sum(e["entry"]["weight"] for e in enabled)
    if total_w > 0:
        for e in enabled:
            e["entry"]["weight"] = round(e["entry"]["weight"] / total_w, 4)

    first_pick = None
    if enabled:
        def weighted_headroom(e):
            raw_headroom = max(0.0, e["entry"]["target"] - e["baseline"])
            return e["entry"]["weight"] * raw_headroom

        best = max(enabled, key=weighted_headroom)
        first_pick = {"dimension": best["entry"]["name"], "headroom": round(weighted_headroom(best), 1)}

    setup_cmd, test_cmd = guess_commands(assess_data)

    ui = assess_data.get("ui", {}) or {}
    git = assess_data.get("git", {}) or {}

    proposal = {
        "version": 1,
        "project_dir": str(project_dir),
        "slug": slug,
        "main_branch": git.get("main_branch") or "main",
        "setup_cmd": setup_cmd,
        "test_cmd": test_cmd,
        "cap_usd": cap,
        "hours": hours,
        "ui": {
            "enabled": bool(ui.get("present")),
            "serve_cmd": ui.get("serve_cmd"),
            "urls": ui.get("urls") or [],
            "port_base": 4100,
        },
        "scorers": [e["entry"] for e in enabled],
        "scorer_baselines": {
            e["entry"]["name"]: {
                "baseline": e["baseline"], "spread": e["spread"], "cv": e["cv"],
                "note": e["note"], "raw": e["raw"],
            }
            for e in enabled
        },
        "dropped": dropped,
        "first_pick": first_pick,
        "refused": refused,
        "refusal_reason": refusal_reason,
        "unblock": unblock,
        "goal_path": None,
    }
    proposal.update(CONFIG_DEFAULTS)
    return proposal


def print_table(rows, headers):
    widths = list(len(str(h)) for h in headers)
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))

    def fmt(cells):
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))

    print(fmt(headers))
    print(fmt(["-" * w for w in widths]))
    for r in rows:
        print(fmt(r))


def cmd_propose(args):
    project_dir = Path(args.project or ".").resolve()

    if args.assess:
        assess_data = json.loads(Path(args.assess).read_text())
    else:
        assess_data = run_assess(project_dir)

    goal_text = ""
    goal_path = None
    if args.goal and Path(args.goal).exists():
        goal_path = str(Path(args.goal).resolve())
        goal_text = Path(args.goal).read_text()

    proposal = propose(project_dir, assess_data, goal_text, args.cap, args.hours)
    proposal["goal_path"] = goal_path

    for d in proposal["dropped"]:
        print(f"dropped {d['name']}: {d['reason']}")

    if proposal["refused"]:
        print(f"REFUSED: {proposal['refusal_reason']}")
        print("What would unblock it:")
        for hint in proposal["unblock"]:
            print(f"  - {hint}")
        return 6

    rows = []
    for s in proposal["scorers"]:
        baseline = proposal["scorer_baselines"][s["name"]]["baseline"]
        rows.append([s["name"], s["weight"], s["eps"], baseline, scorer_cmd_for_display(s)])
    print_table(rows, ["scorer", "weight", "eps", "baseline", "cmd"])

    for s in proposal["scorers"]:
        note = proposal["scorer_baselines"][s["name"]]["note"]
        if note:
            print(f"note ({s['name']}): {note}")

    fp = proposal["first_pick"]
    dims = ", ".join(s["name"] for s in proposal["scorers"])
    print(f"First pick: {fp['dimension']} (headroom {fp['headroom']}). "
          f"With this config the loop can ONLY improve: {dims}.")

    print(f"setup_cmd guess: {proposal['setup_cmd']}")
    print(f"test_cmd guess: {proposal['test_cmd']}")

    out_dir = project_dir / ".loop"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "proposed-config.json"
    out_path.write_text(json.dumps(proposal, indent=2))
    print(f"wrote {out_path}")
    return 0


# ---------------------------------------------------------------------------
# --write
# ---------------------------------------------------------------------------

def build_config(proposal):
    return {
        "version": 1,
        "project_dir": proposal["project_dir"],
        "slug": proposal["slug"],
        "main_branch": proposal["main_branch"],
        "setup_cmd": proposal["setup_cmd"],
        "test_cmd": proposal["test_cmd"],
        "cap_usd": proposal["cap_usd"],
        "per_iter_max_usd": proposal.get("per_iter_max_usd", CONFIG_DEFAULTS["per_iter_max_usd"]),
        "min_iter_usd": proposal.get("min_iter_usd", CONFIG_DEFAULTS["min_iter_usd"]),
        "hours": proposal["hours"],
        "max_flat": proposal.get("max_flat", CONFIG_DEFAULTS["max_flat"]),
        "max_fanout": proposal.get("max_fanout", CONFIG_DEFAULTS["max_fanout"]),
        "max_files_per_iteration": proposal.get(
            "max_files_per_iteration", CONFIG_DEFAULTS["max_files_per_iteration"]),
        "hypothesis_max_turns": proposal.get(
            "hypothesis_max_turns", CONFIG_DEFAULTS["hypothesis_max_turns"]),
        "child_max_turns": proposal.get("child_max_turns", CONFIG_DEFAULTS["child_max_turns"]),
        "child_timeout_min": proposal.get("child_timeout_min", CONFIG_DEFAULTS["child_timeout_min"]),
        "opus_allowed": proposal.get("opus_allowed", CONFIG_DEFAULTS["opus_allowed"]),
        "ui": proposal["ui"],
        "scorers": proposal["scorers"],
        "regress_eps": proposal.get("regress_eps", CONFIG_DEFAULTS["regress_eps"]),
    }


def _fallback_manifest(config_path, manifest_path):
    h = hashlib.sha256()
    h.update(Path(config_path).read_bytes())
    cfg = json.loads(Path(config_path).read_text())
    for s in cfg.get("scorers", []):
        f = KIT / "scorers" / f"{s['name']}.py"
        if f.exists():
            h.update(f.read_bytes())
    manifest_path.write_text(h.hexdigest() + "\n")


def write_manifest(ns_dir, config_path):
    manifest_path = Path(ns_dir) / "manifest.sha256"
    script = KIT / "score.py"
    try:
        subprocess.run(
            [sys.executable, str(script), "--manifest-write", str(manifest_path),
             "--config", str(config_path)],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        _fallback_manifest(config_path, manifest_path)
    return manifest_path


def _fallback_baseline_row(scores_path, commit):
    row = {
        "iter": 0, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": commit, "composite": None, "dims": {}, "cost_usd": 0,
        "duration_s": 0, "task_id": "none", "outcome": "baseline", "label": None,
    }
    with open(scores_path, "a") as f:
        f.write(json.dumps(row) + "\n")


def write_baseline_score(project_dir, config_path, scores_path, manifest_path):
    commit = "unknown"
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(project_dir),
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass

    script = KIT / "score.py"
    try:
        subprocess.run(
            [sys.executable, str(script), "--config", str(config_path), "--workdir", str(project_dir),
             "--iter", "0", "--commit", commit, "--cost", "0", "--duration", "0",
             "--task", "none", "--outcome", "baseline", "--manifest", str(manifest_path)],
            check=True, capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        _fallback_baseline_row(scores_path, commit)
    return commit


def _add_hook(settings, event, matcher, command, extra):
    hooks = settings.setdefault("hooks", {})
    groups = hooks.setdefault(event, [])
    for g in groups:
        for h in g.get("hooks", []):
            if h.get("command") == command:
                return  # already present — dedup by command path
    hook_entry = {"type": "command", "command": command}
    hook_entry.update(extra)
    for g in groups:
        if g.get("matcher") == matcher:
            g.setdefault("hooks", []).append(hook_entry)
            return
    new_group = {"hooks": [hook_entry]}
    if matcher is not None:
        new_group["matcher"] = matcher
    groups.append(new_group)


def merge_settings(settings_path, config):
    settings_path = Path(settings_path)
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = {}

    settings.setdefault("worktree", {})
    settings["worktree"]["baseRef"] = "head"

    # The resolved kit path, never "~/Claude_Upgrade": hook commands are not
    # shell-expanded consistently, and the kit is not always under $HOME.
    kit = str(Path(__file__).resolve().parent)
    events_cmd = f"bash {kit}/hooks/events.sh"
    budget_cmd = f"bash {kit}/hooks/budget-gate.sh"

    _add_hook(settings, "SubagentStart", None, events_cmd, {"async": True, "timeout": 5})
    _add_hook(settings, "SubagentStop", None, events_cmd, {"async": True, "timeout": 5})
    _add_hook(settings, "PreToolUse", "Agent", budget_cmd, {"timeout": 5})

    settings["statusLine"] = {
        "type": "command",
        "command": f"bash {kit}/statusline.sh",
        "refreshInterval": 5,
    }

    allow = set((settings.get("permissions") or {}).get("allow", []))
    # Same rules the driver hands the child: derived from the config's own
    # commands, so interactive sessions and the loop agree on what may run.
    sys.path.insert(0, str(Path(kit)))
    from allowlist import rules as _allow_rules
    allow.update(_allow_rules(config, kit))
    settings.setdefault("permissions", {})["allow"] = sorted(allow)

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")


def update_gitignore(path):
    path = Path(path)
    existing = path.read_text().splitlines() if path.exists() else []
    existing_set = {l.strip() for l in existing}
    to_add = [l for l in GITIGNORE_LINES if l not in existing_set]
    if not to_add:
        return
    out = list(existing)
    if out and out[-1].strip() != "":
        out.append("")
    out.append("# Nightshift")
    out.extend(to_add)
    path.write_text("\n".join(out) + "\n")


def map_dimension(title):
    for pattern, dim in DIM_KEYWORDS:
        if pattern.search(title):
            return dim
    return "none"


def is_refactor(title):
    return bool(REFACTOR_RE.search(title))


def backlog_row(id_, title, dimension, est, source, rung):
    return {
        "id": id_, "title": title, "dimension": dimension, "est": est,
        "source": source, "status": "open", "rung": rung, "attempts": 0,
        "iter_added": 0, "note": "",
    }


def least_covered_files(project_dir, proposal):
    scorers = {s["name"]: s for s in proposal.get("scorers", [])}
    tests_cfg = scorers.get("tests", {})
    cov_file = tests_cfg.get("coverage_file")
    if not cov_file:
        return []
    path = Path(cov_file)
    if not path.is_absolute():
        path = Path(project_dir) / cov_file
    if not path.exists() or path.suffix != ".json":
        return []
    try:
        data = json.loads(path.read_text())
        files = data.get("files") or {}
        entries = []
        for fname, info in files.items():
            summary = info.get("summary") or {}
            pct = summary.get("percent_covered")
            if pct is None:
                pct = (info.get("lines") or {}).get("pct")
            if pct is not None:
                entries.append((float(pct), fname))
        entries.sort(key=lambda x: x[0])
        return [f for _, f in entries[:5]]
    except Exception:
        return []


def seed_backlog(project_dir, proposal):
    rows = []
    counter = [0]

    def next_id():
        counter[0] += 1
        return f"bl-{counter[0]:03d}"

    try:
        proc = subprocess.run(
            ["gh", "issue", "list", "--json", "number,title", "--limit", "50"],
            cwd=str(project_dir), capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            issues = json.loads(proc.stdout)
            for issue in issues:
                title = issue.get("title", "")
                rows.append(backlog_row(
                    next_id(), title, map_dimension(title), "M", "issue",
                    2 if is_refactor(title) else 1,
                ))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    try:
        # Comment-form TODOs in source files only. A bare `TODO|FIXME` grep
        # seeded rows from test fixtures, docs and this kit's own code that
        # *mentions* TODOs -- and one of those noise rows would have been
        # picked ahead of the real coverage-gap task.
        proc = subprocess.run(
            ["grep", "-rnE", r"(#|//|/\*|<!--|\*)\s*(TODO|FIXME)\b", str(project_dir),
             "--include=*.py", "--include=*.js", "--include=*.ts", "--include=*.tsx",
             "--include=*.go", "--include=*.rs", "--include=*.swift", "--include=*.sh",
             "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=venv",
             "--exclude-dir=.venv", "--exclude-dir=.loop", "--exclude-dir=.claude",
             "--exclude-dir=tests", "--exclude-dir=test", "--exclude-dir=__tests__",
             "--exclude-dir=fixtures"],
            capture_output=True, text=True, timeout=30,
        )
        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            fpath, lineno, text = parts
            if os.path.basename(fpath).startswith("test_"):
                continue
            try:
                rel = os.path.relpath(fpath, str(project_dir))
            except ValueError:
                rel = fpath
            title = f"{rel}:{lineno}: {text.strip()}"[:200]
            rows.append(backlog_row(
                next_id(), title, map_dimension(title), "S", "todo",
                2 if is_refactor(title) else 1,
            ))
    except (OSError, subprocess.TimeoutExpired):
        pass

    tests_baseline = (proposal.get("scorer_baselines", {}) or {}).get("tests")
    if tests_baseline:
        raw = tests_baseline.get("raw") or {}
        cov = raw.get("coverage_pct")
        if cov is not None and cov < 90:
            files = least_covered_files(project_dir, proposal)
            if files:
                title = f"raise coverage in {', '.join(files[:5])}"
            else:
                title = f"raise coverage below 90% (currently {cov:.1f}%)"
            rows.append(backlog_row(next_id(), title, "tests", "S", "coverage-gap", 1))

    return rows


def _yaml_scalar(s):
    s = "" if s is None else str(s)
    if s == "" or s in ("true", "false", "null") or re.search(r'[:#\[\]{}"\']|^\s|\s$', s):
        escaped = s.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _fallback_backlog_dump(rows, path):
    lines = ["rows:"]
    for r in rows:
        lines.append(f"  - id: {r['id']}")
        lines.append(f"    title: {_yaml_scalar(r['title'])}")
        lines.append(f"    dimension: {r['dimension']}")
        lines.append(f"    est: {r['est']}")
        lines.append(f"    source: {r['source']}")
        lines.append(f"    status: {r['status']}")
        lines.append(f"    rung: {r['rung']}")
        lines.append(f"    attempts: {r['attempts']}")
        lines.append(f"    iter_added: {r['iter_added']}")
        lines.append(f"    note: {_yaml_scalar(r.get('note', ''))}")
    Path(path).write_text("\n".join(lines) + "\n")


def write_backlog(rows, path):
    """loop/backlog_io.py contract: load(path)->{"rows":[...]}, dump(data,path).
    Imported lazily; falls back to a minimal, dependency-free writer for the
    same fixed schema when backlog_io isn't available yet.
    """
    try:
        sys.path.insert(0, str(KIT))
        import backlog_io  # noqa: E402
        backlog_io.dump({"rows": rows}, path)
        return
    except Exception:
        _fallback_backlog_dump(rows, path)


def cmd_write(args):
    project_dir = Path(args.project or ".").resolve()
    proposal_path = Path(args.proposal) if args.proposal else project_dir / ".loop" / "proposed-config.json"
    proposal = json.loads(proposal_path.read_text())

    written = []
    slug = proposal["slug"]
    ns_dir = Path(os.path.expanduser(f"~/.claude/nightshift/{slug}"))
    ns_dir.mkdir(parents=True, exist_ok=True)

    config = build_config(proposal)
    config_path = ns_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    written.append(str(config_path))

    # goal.md — nightshift copy + project copy (only if the project doesn't
    # already have one).
    goal_dst = ns_dir / "goal.md"
    goal_src = proposal.get("goal_path")
    project_goal = project_dir / "GOAL.md"
    if goal_src and Path(goal_src).exists():
        shutil.copy(goal_src, goal_dst)
    elif project_goal.exists():
        shutil.copy(project_goal, goal_dst)
    else:
        goal_dst.write_text("# Goal\n\n(not provided)\n")
    written.append(str(goal_dst))
    if not project_goal.exists():
        shutil.copy(goal_dst, project_goal)
        written.append(str(project_goal))

    manifest_path = write_manifest(ns_dir, config_path)
    written.append(str(manifest_path))

    claude_agents = project_dir / ".claude" / "agents"
    claude_agents.mkdir(parents=True, exist_ok=True)
    ui_enabled = bool(config["ui"]["enabled"])
    for f in sorted(AGENTS_DIR.glob("*.md")):
        if f.name == "ui-auditor.md" and not ui_enabled:
            continue
        dst = claude_agents / f.name
        shutil.copy(f, dst)
        written.append(str(dst))

    settings_path = project_dir / ".claude" / "settings.json"
    merge_settings(settings_path, config)
    written.append(str(settings_path))

    gitignore_path = project_dir / ".gitignore"
    update_gitignore(gitignore_path)
    written.append(str(gitignore_path))

    loop_dir = project_dir / ".loop"
    loop_dir.mkdir(parents=True, exist_ok=True)
    backlog_path = loop_dir / "backlog.yaml"
    if not backlog_path.exists():
        rows = seed_backlog(project_dir, proposal)
        write_backlog(rows, backlog_path)
        written.append(str(backlog_path))

    scores_path = loop_dir / "scores.jsonl"
    write_baseline_score(project_dir, config_path, scores_path, manifest_path)
    written.append(str(scores_path))

    state = {
        "run": None, "iter": 0, "phase": "IDLE", "rung": 1, "dryrun_ok": False,
        "cap_usd": config["cap_usd"], "spent_usd": 0, "lockout": {}, "picks_history": [],
    }
    state_path = loop_dir / "state.json"
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    written.append(str(state_path))

    for w in written:
        print(f"wrote {w}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(description="Nightshift bootstrap")
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--assess")
    ap.add_argument("--goal")
    ap.add_argument("--proposal")
    ap.add_argument("--cap", type=float, default=25)
    ap.add_argument("--hours", type=float, default=6)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.propose:
        return cmd_propose(args)
    if args.write:
        return cmd_write(args)
    print("specify --propose or --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
