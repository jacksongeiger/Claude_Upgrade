#!/usr/bin/env python3
"""Nightshift permission allowlist — the one place the child's Bash rules come from.

The child `claude -p` runs with `--permission-prompts none`: anything not on
this list is denied, for the planner and for every executor. The third paid
dryrun proved why this must be derived and not hand-typed: the test command
was `cd discovery && ./venv/bin/python -m pytest -q`, the old rule took its
first word, allowlisted `cd`, and three executors could not run a single
test.

Rules:
  - a fixed read-only/plumbing set (ls, cat, git diff, …);
  - the kit's own scripts (`python3 <kit>/*`, `bash <kit>/*`);
  - common interpreters and test runners;
  - the first word of EVERY segment of every configured command
    (setup_cmd, test_cmd, each scorer's cmd/coverage_cmd), so whatever the
    project actually runs is runnable.

`is_allowed(cmd, rules)` mirrors the prefix semantics closely enough for
check_plan.py to refuse an acceptance_cmd an executor could not run.

Usage:
  allowlist.py --config <config.json> --kit <kit dir>     # prints a JSON array
"""
import argparse
import json
import re
import sys
from pathlib import Path

FIXED = [
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git diff:*)", "Bash(git log:*)",
    "Bash(git status:*)", "Bash(git rev-parse:*)", "Bash(git show:*)",
    "Bash(git branch --show-current:*)", "Bash(git ls-files:*)", "Bash(git stash:*)",
    "Bash(rdx search:*)",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)", "Bash(grep:*)",
    "Bash(find:*)", "Bash(mkdir:*)", "Bash(pwd)", "Bash(test:*)", "Bash(true)", "Bash(cd:*)",
    "Bash(echo:*)", "Bash(printf:*)", "Bash(diff:*)", "Bash(sort:*)", "Bash(uniq:*)",
    "Bash(cut:*)", "Bash(tr:*)", "Bash(which:*)", "Bash(date:*)", "Bash(touch:*)",
    "Bash(cp:*)", "Bash(mv:*)", "Bash(rm:*)", "Bash(xargs:*)", "Bash(sed:*)", "Bash(awk:*)",
]

# Interpreters and runners an executor plausibly needs regardless of what the
# config names. Acquisition (npm install, pip install <pkg>) is still denied by
# the executor guard hook; this list only says "may run".
RUNNERS = [
    "python3", "python", "pytest", "uv run", "poetry run", "coverage",
    "./venv/bin/python", "./venv/bin/pytest", "./venv/bin/pip", "./venv/bin/coverage",
    "venv/bin/python", "venv/bin/pytest", "venv/bin/pip",
    ".venv/bin/python", ".venv/bin/pytest", ".venv/bin/pip",
    "node", "npm test", "npm run", "npm ci", "npx", "yarn test", "yarn run", "pnpm test", "pnpm run",
    "go test", "go build", "go vet", "go mod download",
    "cargo test", "cargo build", "cargo check", "cargo fetch",
    "make", "swift test", "swift build",
]

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def segments(cmd):
    """Top-level command segments of a shell line (no quote awareness on
    purpose: an over-split only adds a rule, never drops one)."""
    return [s.strip() for s in _SPLIT.split(cmd or "") if s.strip()]


def first_word(seg):
    seg = seg.strip()
    # skip leading VAR=value assignments and redirections
    while True:
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=[^\s]*\s+", seg)
        if not m:
            break
        seg = seg[m.end():]
    return seg.split()[0] if seg.split() else ""


def config_commands(config):
    out = []
    for k in ("setup_cmd", "test_cmd"):
        if config.get(k):
            out.append(config[k])
    for sc in config.get("scorers") or []:
        for k in ("cmd", "coverage_cmd", "bench_cmd", "eval_cmd", "build_cmd"):
            if sc.get(k):
                out.append(sc[k])
    return out


def rules(config, kit):
    kit = str(Path(kit).resolve()) if kit else ""
    out = list(FIXED)
    if kit:
        out += [f"Bash(python3 {kit}/*)", f"Bash(bash {kit}/*)"]
    out += [f"Bash({r}:*)" for r in RUNNERS]
    for cmd in config_commands(config):
        for seg in segments(cmd):
            w = first_word(seg)
            if w and w not in ("cd",) and re.match(r"^[\w./-]+$", w):
                out.append(f"Bash({w}:*)")
    seen, dedup = set(), []
    for r in out:
        if r not in seen:
            seen.add(r)
            dedup.append(r)
    return dedup


def _prefixes(rule_list):
    pre = []
    for r in rule_list:
        m = re.match(r"^Bash\((.*)\)$", r)
        if not m:
            continue
        body = m.group(1)
        if body.endswith(":*"):
            pre.append(("prefix", body[:-2]))
        elif body.endswith("/*"):
            pre.append(("prefix", body[:-1]))
        else:
            pre.append(("exact", body))
    return pre


def is_allowed(cmd, rule_list):
    """True when every segment of cmd matches some rule (prefix or exact)."""
    pre = _prefixes(rule_list)
    for seg in segments(cmd):
        s = seg.strip()
        ok = False
        for kind, body in pre:
            if kind == "exact" and s == body:
                ok = True
                break
            if kind == "prefix" and (s == body or s.startswith(body + " ") or s.startswith(body)):
                # startswith(body) without a trailing space covers
                # "python3 /kit/x.py" against "python3 /kit/"
                if s == body or s.startswith(body + " ") or body.endswith("/"):
                    ok = True
                    break
        if not ok:
            return False, seg
    return True, None


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--kit", required=True)
    a = p.parse_args(argv)
    config = json.loads(Path(a.config).read_text())
    print(json.dumps(rules(config, a.kit)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
