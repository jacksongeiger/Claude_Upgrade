#!/usr/bin/env python3
"""classify.py — one cheap, metered model call behind a rule.

Some decisions in the pipeline need judgment a keyword rule cannot give:
which scorer a complaint belongs to, whether two findings are the same
problem, whether a clean walkthrough really resolved a finding. Each is a
Haiku call that returns a small JSON object, validated by the caller, with
the rule kept as the fallback when the model is unavailable or answers
badly. Every call is recorded in the project's ledger like any other child
(`stage: classify:<what>`) and capped per call. Nothing below score.py may
import this: scoring stays pure.

    classify.ask(what, prompt, schema_check, workdir, budget=0.05) -> obj | None

`schema_check(obj) -> bool` is the caller's shape test; a False, a timeout,
a missing binary or unparsable output all return None. NIGHTSHIFT_CLAUDE
overrides the binary (tests use a double that reads the prompt and prints
JSON). CLASSIFY_OFF=1 disables every call (the rules run alone).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MODEL = "haiku"
TIMEOUT_S = 90


def _ledger(workdir, what, cost):
    if not workdir:
        return
    p = Path(workdir) / ".pipeline" / "ledger.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "stage": f"classify:{what}", "id": what, "cost_usd": round(float(cost or 0.0), 5)}) + "\n")
    except OSError:
        pass


def ask(what, prompt, schema_check, workdir=None, budget=0.05, model=MODEL):
    """Returns the parsed JSON object the model produced, or None."""
    if os.environ.get("CLASSIFY_OFF"):
        return None
    claude_bin = os.environ.get("NIGHTSHIFT_CLAUDE") or "claude"
    if shutil.which(claude_bin) is None and not os.path.exists(claude_bin):
        return None
    full = (prompt.rstrip() + "\n\nReply with one JSON object on one line and nothing else. "
            "No prose, no code fence.")
    cmd = [claude_bin, "-p", full, "--model", model, "--max-turns", "1", "--max-budget-usd", str(budget),
           "--permission-mode", "acceptEdits", "--permission-prompts", "none", "--output-format", "json",
           "--tools", ""]
    env = dict(os.environ)
    env["CLASSIFY_WHAT"] = what
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=env,
                              cwd=workdir or None)
    except (subprocess.TimeoutExpired, OSError):
        return None
    cost = 0.0
    text = proc.stdout or ""
    try:
        data = json.loads(text)
        cost = float(data.get("total_cost_usd") or 0.0)
        text = data.get("result") or ""
    except ValueError:
        pass
    _ledger(workdir, what, cost)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    try:
        ok = bool(schema_check(obj))
    except Exception:  # noqa: BLE001 - a broken check is a bad answer
        ok = False
    return obj if ok else None


# ---------------------------------------------------------------------------
# the three questions the pipeline asks
# ---------------------------------------------------------------------------

DIMENSIONS = ("tests", "perf", "persona", "lighthouse", "evals", "none")


def dimension(title, workdir=None):
    """Which scorer a complaint belongs to. None when the model is unavailable."""
    prompt = ("A user of a software product wrote this feedback:\n\n"
              f"  {title!r}\n\n"
              "Which measurable dimension of the product does it belong to?\n"
              "- tests: something is wrong, crashes, errors, data loss, a bug\n"
              "- perf: slowness, lag, timeouts, resource use\n"
              "- persona: hard to find, confusing, unclear wording, a flow that loses people\n"
              "- lighthouse: accessibility, contrast, keyboard, screen readers, page weight\n"
              "- evals: the model or algorithm gives wrong or poor answers\n"
              "- none: a wish for a new feature, praise, or nothing measurable\n"
              'Answer as {"dimension": "<one of the six>", "why": "<ten words>"}')
    obj = ask("dimension", prompt, lambda o: o.get("dimension") in DIMENSIONS, workdir)
    return obj["dimension"] if obj else None


def duplicates(title, candidates, workdir=None):
    """Ids among `candidates` ({id: title}) that describe the same problem as
    `title`. None when unavailable; [] when the model says none."""
    if not candidates:
        return []
    listing = "\n".join(f"  {cid}: {t}" for cid, t in candidates.items())
    prompt = ("New feedback about a software product:\n\n"
              f"  {title!r}\n\n"
              "Existing backlog rows:\n" + listing + "\n\n"
              "Which existing rows describe the same underlying problem (not merely the same area)? "
              'Answer as {"same": ["<id>", ...]} with an empty list when none do.')
    obj = ask("duplicates", prompt, lambda o: isinstance(o.get("same"), list) and all(s in candidates for s in o["same"]), workdir)
    return obj["same"] if obj else None


def resolved(open_rows, trail_summary, task, workdir=None):
    """Which open persona rows ({id: title}) a clean walkthrough of `task`
    actually resolved, given the trail. None when unavailable."""
    if not open_rows:
        return []
    listing = "\n".join(f"  {rid}: {t}" for rid, t in open_rows.items())
    prompt = ("A fresh user just completed this task in a product without getting stuck:\n\n"
              f"  task: {task}\n  what happened, step by step:\n{trail_summary}\n\n"
              "These open findings were raised by earlier walkthroughs of the same task:\n" + listing + "\n\n"
              "Which of them did this walkthrough show to be fixed, meaning the user went through the very "
              "spot the finding describes without the problem? A finding the walkthrough did not touch is not resolved. "
              'Answer as {"resolved": ["<id>", ...]}')
    obj = ask("resolved", prompt, lambda o: isinstance(o.get("resolved"), list) and all(r in open_rows for r in o["resolved"]), workdir)
    return obj["resolved"] if obj else None


def contradictions(bullets, anchors, workdir=None):
    """Inbox bullets that contradict a validation anchor or kill number.
    Returns [{"bullet","anchor","why"}] or None."""
    if not bullets or not anchors:
        return []
    prompt = ("A product was built on these validated claims and kill numbers:\n" +
              "\n".join(f"  - {a}" for a in anchors) +
              "\n\nReal users then said:\n" + "\n".join(f"  - {b}" for b in bullets) +
              "\n\nWhich user statements contradict a claim (the pain is not real, the number was wrong, "
              "the audience is different)? Be strict: a bug report is not a contradiction. "
              'Answer as {"contradictions": [{"bullet": "<verbatim>", "anchor": "<verbatim>", "why": "<ten words>"}]}')
    def ok(o):
        c = o.get("contradictions")
        return isinstance(c, list) and all(isinstance(x, dict) and x.get("bullet") in bullets and x.get("anchor") in anchors for x in c)
    obj = ask("contradictions", prompt, ok, workdir)
    return obj["contradictions"] if obj else None


if __name__ == "__main__":
    print(json.dumps({"dimension": dimension(" ".join(sys.argv[1:]) or "it crashed")}))
