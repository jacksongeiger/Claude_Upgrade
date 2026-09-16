#!/usr/bin/env python3
"""Nightshift persona scorer — the "how does it feel" dimension.

python3 scorers/persona.py --config '<json of the scorer's config.json entry>' --workdir <dir>

Config keys:
  serve_cmd   (required) command, run via `bash -c` in --workdir, that serves
              the app and keeps running (started in the background, killed
              when this scorer finishes)
  port        (required) the port serve_cmd binds to; polled until it accepts
              connections before any task runs
  tasks       (required) list of {"task": "...", "max_steps": N, "persona": "..."}
  runs        (optional, default 1) times each task is repeated; a task's
              value is the mean of its runs, and the scorer's value is the
              mean of the tasks' values
  claude_bin  (optional, default "claude"); overridden by the NIGHTSHIFT_CLAUDE
              env var exactly as loop/run.sh does. When it points at a script,
              that script is executed with the same arguments a real `claude`
              binary would get — the test double.

For every (task, run) this spawns:
    <claude_bin> -p "<prompt>" --model sonnet --max-turns 40
        --permission-mode acceptEdits --permission-prompts none
        [--agents '<json with the persona agent>'] --output-format json
into a run directory <workdir>/.pipeline/ux/<slug of task>-<n>/ (<n> is the
1-based run index), asking the persona to drive http://127.0.0.1:<port> with
pipeline/js/persona_driver.cjs and write its result there. The persona agent
definition is read from agents/persona.md (two levels above this file) if
that file exists, parsed the way loop/agents_json.py parses every agent file;
if it is absent, `claude` is invoked without --agents. For convenience — real
`claude` ignores unknown env vars, and pipeline/tests/test_scorer_persona.py's
fake claude script uses this instead of scraping the prompt text — the child
also gets PERSONA_RUN_DIR, PERSONA_TASK and PERSONA_URL in its environment.

Each run directory is then scored with pipeline/ux_score.py:
    ux_score.py --run <dir> --check '{"type":"persona","task":...,
                                       "max_steps":...,"must":"complete"}'

Prints exactly one JSON line and always exits 0, per the Scorer contract:
    {"name":"persona","value":72.5,"ok":true,"error":null,"raw":{...}}

ok:false + error when: serve_cmd never opens `port`, claude_bin can't be
found, or any run produced no result.json (ux_score.py exit 4). The server is
always stopped before this process exits.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT_ROOT))
from loop import agents_json  # noqa: E402

SERVER_READY_TIMEOUT_S = 20
CLAUDE_RUN_TIMEOUT_S = 20 * 60
PERSONA_AGENT_PATH = KIT_ROOT / "agents" / "persona.md"
UX_SCORE = KIT_ROOT / "pipeline" / "ux_score.py"


def emit(name, value, ok, error, raw):
    print(json.dumps({"name": name, "value": value, "ok": ok, "error": error, "raw": raw}))


def wait_for_port(port, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def slugify(text, max_len=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "task")[:max_len]


def build_agents_json():
    if not PERSONA_AGENT_PATH.exists():
        return None
    name, spec = agents_json.parse_agent(PERSONA_AGENT_PATH)
    return json.dumps({name: spec})


def run_task(claude_bin, workdir, url, task_cfg, run_idx):
    """One walkthrough + judgment, through pipeline/persona_run.py (the same
    runner accept.py uses), so Nightshift and the build score UX identically.
    NIGHTSHIFT_CLAUDE is inherited by the runner as the claude binary."""
    task = task_cfg["task"]
    max_steps = task_cfg.get("max_steps", 6)
    persona = task_cfg.get("persona", "")
    slug = slugify(task)
    run_dir = os.path.join(workdir, ".pipeline", "ux", f"{slug}-{run_idx}")
    os.makedirs(run_dir, exist_ok=True)
    runner = KIT_ROOT / "pipeline" / "persona_run.py"
    cmd = [sys.executable, str(runner), "--url", url, "--task", task, "--max-steps", str(max_steps),
           "--run-dir", run_dir, "--workdir", workdir]
    if persona:
        cmd += ["--persona", persona]
    env = dict(os.environ)
    if claude_bin and claude_bin != "claude":
        env["NIGHTSHIFT_CLAUDE"] = claude_bin
    try:
        proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True, timeout=CLAUDE_RUN_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"run_dir": run_dir, "ok": False, "error": f"persona_run failed for task {task!r}: {e}"}
    if proc.returncode == 4:
        return {"run_dir": run_dir, "ok": False, "error": f"no result.json for task {task!r} run {run_idx}"}
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        return {"run_dir": run_dir, "ok": False, "error": f"persona_run produced no output for {run_dir}"}
    try:
        score = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"run_dir": run_dir, "ok": False, "error": f"unreadable score for {run_dir}: {lines[-1][:120]}"}
    return {"run_dir": run_dir, "ok": True, "score": score}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args(argv)

    name = "persona"
    server = None
    try:
        cfg = json.loads(args.config)
        name = cfg.get("name", "persona")
        workdir = args.workdir
        serve_cmd = cfg.get("serve_cmd")
        port = cfg.get("port")
        tasks = cfg.get("tasks") or []
        runs = int(cfg.get("runs", 1))

        if not serve_cmd or not port or not tasks:
            emit(name, 0, False, "not configured: serve_cmd, port, and tasks required", {})
            return 0

        claude_bin = os.environ.get("NIGHTSHIFT_CLAUDE") or cfg.get("claude_bin", "claude")
        if shutil.which(claude_bin) is None:
            emit(name, 0, False, f"claude not found: {claude_bin}", {})
            return 0

        try:
            server = subprocess.Popen(
                ["bash", "-c", serve_cmd], cwd=workdir,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            if not wait_for_port(port, SERVER_READY_TIMEOUT_S):
                emit(name, 0, False, f"serve_cmd did not open port {port} within {SERVER_READY_TIMEOUT_S}s", {})
                return 0

            url = f"http://127.0.0.1:{port}"
            per_task = {}
            for task_cfg in tasks:
                task = task_cfg["task"]
                run_results = []
                for i in range(1, runs + 1):
                    r = run_task(claude_bin, workdir, url, task_cfg, i)
                    run_results.append(r)
                    if not r["ok"]:
                        emit(name, 0, False, r["error"], {"per_task": per_task})
                        return 0
                values = [r["score"]["value"] for r in run_results]
                per_task[task] = {"value": sum(values) / len(values), "runs": run_results}

            value = sum(t["value"] for t in per_task.values()) / len(per_task)
            emit(name, value, True, None, {"per_task": per_task})
            return 0
        finally:
            if server is not None and server.poll() is None:
                try:
                    os.killpg(os.getpgid(server.pid), signal.SIGTERM)
                    server.wait(timeout=5)
                except Exception:
                    try:
                        os.killpg(os.getpgid(server.pid), signal.SIGKILL)
                    except Exception:
                        pass
    except Exception as e:
        emit(name, 0, False, f"scorer crashed: {e}", {})
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
