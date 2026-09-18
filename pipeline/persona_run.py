#!/usr/bin/env python3
"""persona_run.py — one fresh-eyes walkthrough plus its judgment, as files.

    persona_run.py --url <base url> --task "<task>" --max-steps N --run-dir <dir>
                   [--persona "<who>"] [--no-judge] [--model sonnet] [--budget 1.5] [--workdir <dir>]

Spawns `claude -p` twice with the kit's agent definitions: first the
`persona` agent (browser only, no code), then the `persona-judge` agent
(fresh context, fixed rubric), each as a subagent of a tiny parent prompt.
Writes into <run-dir>: trail.json + shots/ (the driver), result.json and
findings.json (the persona), judge.json (the judge). Then runs ux_score.py
and prints its one-line score. Appends the two costs to
<workdir>/.pipeline/ledger.jsonl when --workdir is given.

NIGHTSHIFT_CLAUDE overrides the claude binary (test double: it receives the
same arguments plus PERSONA_RUN_DIR / PERSONA_TASK / PERSONA_URL in the
environment and is expected to write result.json itself).

Exit: 0 scored · 2 the task was not completed (score.json still written) ·
4 the persona produced no result.json · 1 usage.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parent
sys.path.insert(0, str(ROOT / "loop"))
import agents_json  # noqa: E402

RUN_TIMEOUT_S = 25 * 60


def agents(names):
    out = {}
    for n in names:
        p = ROOT / "agents" / f"{n}.md"
        if p.exists():
            name, spec = agents_json.parse_agent(p)
            out[name] = spec
    return json.dumps(out) if out else None


def settings_for(run_dir):
    """The child runs with --permission-prompts none: only what this allowlist
    names may run. The persona needs the driver and its run directory; the
    judge needs nothing but Read/Write. Everything else stays denied."""
    driver = str(KIT / "js" / "persona_driver.cjs")
    allow = [f"Bash(node {driver}:*)", f"Bash(mkdir -p {run_dir}:*)", f"Bash(ls {run_dir}:*)",
             f"Bash(cat {run_dir}:*)", "Bash(mkdir:*)", "Bash(ls:*)", "Bash(cat:*)"]
    path = os.path.join(run_dir, "child-settings.json")
    with open(path, "w") as f:
        json.dump({"permissions": {"allow": allow}}, f)
    return path


def claude(prompt, model, budget, cwd, env_extra, agent_names, turns=30, run_dir=None, tag="persona"):
    claude_bin = os.environ.get("NIGHTSHIFT_CLAUDE") or "claude"
    if shutil.which(claude_bin) is None and not os.path.exists(claude_bin):
        return None, f"claude not found: {claude_bin}"
    cmd = [claude_bin, "-p", prompt, "--model", model, "--max-turns", str(turns),
           "--max-budget-usd", str(budget), "--permission-mode", "acceptEdits",
           "--permission-prompts", "none", "--output-format", "json"]
    if run_dir:
        cmd += ["--settings", settings_for(run_dir), "--add-dir", run_dir]
    a = agents(agent_names)
    if a:
        cmd += ["--agents", a]
    env = dict(os.environ)
    env.update(env_extra)
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, f"claude failed: {e}"
    if run_dir:
        # keep the child's own account of what happened, for the human and for debugging
        with open(os.path.join(run_dir, f"{tag}.claude.json"), "w") as f:
            f.write(proc.stdout or "")
        if proc.stderr:
            with open(os.path.join(run_dir, f"{tag}.claude.err"), "w") as f:
                f.write(proc.stderr)
    cost = 0.0
    result_text = ""
    try:
        data = json.loads(proc.stdout)
        cost = float(data.get("total_cost_usd") or 0.0)
        result_text = data.get("result") or ""
    except Exception:  # noqa: BLE001
        pass
    if run_dir and result_text:
        salvage(run_dir, tag, result_text)
    return cost, None


def salvage(run_dir, tag, text):
    """An agent that answered in its final message but could not write its
    file (a denied path, a missed instruction) still produced the verdict;
    keep it. Never overwrites a file the agent did write."""
    want = {"persona": ("result.json", ("status", "steps")), "judge": ("judge.json", ("scores", "total"))}
    if tag not in want:
        return
    fname, keys = want[tag]
    path = os.path.join(run_dir, fname)
    if os.path.exists(path):
        return
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return
    if not all(k in obj for k in keys):
        return
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    if tag == "persona" and not os.path.exists(os.path.join(run_dir, "findings.json")):
        with open(os.path.join(run_dir, "findings.json"), "w") as f:
            json.dump({"dead_ends": obj.get("dead_ends", []), "confusions": obj.get("confusions", [])}, f, indent=2)


def ledger(workdir, stage, ident, cost):
    if not workdir:
        return
    p = Path(workdir) / ".pipeline" / "ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    import time
    with p.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stage": stage,
                            "id": ident, "cost_usd": round(cost or 0.0, 4)}) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--max-steps", type=int, default=6)
    ap.add_argument("--persona", default="a first-time user who has never seen this product")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--budget", type=float, default=1.5)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--setup", default="[]", help="JSON list of driver actions that create the task's starting state")
    a = ap.parse_args(argv)
    try:
        setup = json.loads(a.setup) if a.setup else []
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": "--setup is not JSON"}))
        return 1
    run_dir = str(Path(a.run_dir).resolve())
    os.makedirs(run_dir, exist_ok=True)
    driver = str(KIT / "js" / "persona_driver.cjs")
    cwd = a.workdir or os.path.dirname(run_dir)

    spec = {"url": a.url, "task": a.task, "persona": a.persona, "max_steps": a.max_steps,
            "run_dir": run_dir, "driver": driver, "setup": setup}
    prompt = ("Invoke the `persona` agent exactly once with this JSON and nothing else, "
              "then reply with its final JSON verbatim:\n" + json.dumps(spec))
    cost, err = claude(prompt, a.model, a.budget, cwd,
                       {"PERSONA_RUN_DIR": run_dir, "PERSONA_TASK": a.task, "PERSONA_URL": a.url},
                       ["persona"], turns=12, run_dir=run_dir, tag="persona")
    ledger(a.workdir, "persona", os.path.basename(run_dir), cost or 0.0)
    if err:
        print(json.dumps({"ok": False, "error": err}))
        return 4
    if not os.path.exists(os.path.join(run_dir, "result.json")):
        print(json.dumps({"ok": False, "error": "persona produced no result.json", "run_dir": run_dir}))
        return 4

    if not a.no_judge:
        # the judge may only read its run directory: give it the rubric there
        rubric = os.path.join(run_dir, "rubric.md")
        shutil.copy(str(KIT / "prompts" / "rubric.md"), rubric)
        jspec = {"run_dir": run_dir, "task": a.task, "rubric": rubric}
        jprompt = ("Invoke the `persona-judge` agent exactly once with this JSON and nothing else, "
                   "then reply with its final JSON verbatim:\n" + json.dumps(jspec))
        jcost, jerr = claude(jprompt, a.model, min(a.budget, 1.0), cwd, {"PERSONA_RUN_DIR": run_dir},
                             ["persona-judge"], turns=8, run_dir=run_dir, tag="judge")
        ledger(a.workdir, "persona-judge", os.path.basename(run_dir), jcost or 0.0)
        # a missing judge is not fatal: ux_score redistributes its share

    check = json.dumps({"type": "persona", "task": a.task, "max_steps": a.max_steps, "must": "complete"})
    cmd = [sys.executable, str(KIT / "ux_score.py"), "--run", run_dir, "--check", check]
    backlog = Path(cwd) / ".loop" / "backlog.yaml"
    if backlog.exists():
        cmd += ["--backlog", str(backlog)]
        # judgment, metered here (never inside the scorer): drop findings an
        # open row already covers, and let a clean walkthrough close only the
        # rows it actually went through. The hash and name rules in
        # ux_score.py remain the fallback when the model is unavailable.
        decided = apply_judgment(run_dir, str(backlog), a.task, a.workdir)
        if decided:
            cmd.append("--no-supersede")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    return proc.returncode


def apply_judgment(run_dir, backlog_path, task, workdir):
    """Returns True when the resolve decision was made here (so ux_score must
    not apply its name rule), False when the model was unavailable."""
    sys.path.insert(0, str(KIT))
    import classify  # noqa: E402
    sys.path.insert(0, str(ROOT / "loop"))
    import backlog_io  # noqa: E402
    try:
        rows = backlog_io.load(backlog_path)
    except (OSError, ValueError):
        return False
    tag = f'task "{task}"'
    open_rows = {r["id"]: r.get("title", "") for r in rows
                 if str(r.get("id", "")).startswith("ux-") and r.get("status") == "open" and str(r.get("note", "")).startswith(tag)}
    fpath = os.path.join(run_dir, "findings.json")
    findings = {"dead_ends": [], "confusions": []}
    if os.path.exists(fpath):
        try:
            with open(fpath) as f:
                findings = json.load(f)
        except (OSError, ValueError):
            pass
    available = True
    dropped = []
    for key in ("dead_ends", "confusions"):
        kept = []
        for finding in findings.get(key) or []:
            same = classify.duplicates(str(finding), open_rows, workdir) if open_rows else []
            if same is None:
                available = False
                kept.append(finding)
            elif same:
                dropped.append({"finding": finding, "same_as": same})
            else:
                kept.append(finding)
        findings[key] = kept
    if dropped:
        with open(fpath, "w") as f:
            json.dump(findings, f, indent=2)
        with open(os.path.join(run_dir, "findings.dedup.json"), "w") as f:
            json.dump({"dropped": dropped}, f, indent=2)
    if not available:
        return False
    # a clean walkthrough: which open rows did it actually resolve?
    try:
        with open(os.path.join(run_dir, "result.json")) as f:
            status = json.load(f).get("status")
    except (OSError, ValueError):
        status = None
    clean = status == "complete" and not any(findings.get(k) for k in ("dead_ends", "confusions"))
    if clean and open_rows:
        trail = []
        tpath = os.path.join(run_dir, "trail.json")
        if os.path.exists(tpath):
            try:
                with open(tpath) as f:
                    for i, e in enumerate((json.load(f) or [])[:15]):
                        if isinstance(e, dict):
                            trail.append(f"  {i + 1}. {e.get('action') or e.get('did') or ''} -> {str(e.get('saw') or e.get('page') or e.get('result') or '')[:120]}")
            except (OSError, ValueError):
                pass
        which = classify.resolved(open_rows, "\n".join(trail) or "  (no trail recorded)", task, workdir)
        if which is None:
            return False
        if which:
            for r in rows:
                if r.get("id") in which:
                    r["status"] = "done"
                    r["note"] = f"{r.get('note', '')}; resolved: clean walkthrough {os.path.basename(run_dir)} (judged)"
            backlog_io.dump(rows, backlog_path)
    return True


if __name__ == "__main__":
    sys.exit(main())
