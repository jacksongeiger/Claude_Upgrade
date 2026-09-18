import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
SCORER = KIT / "loop" / "scorers" / "persona.py"


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_fake_claude(path, body):
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)


def run_scorer(cfg, workdir, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCORER), "--config", json.dumps(cfg), "--workdir", str(workdir)],
        capture_output=True, text=True, timeout=60, env=env,
    )


def test_persona_scorer_end_to_end(tmp_path):
    (tmp_path / "index.html").write_text("<html><body>hi</body></html>")

    fake_claude = tmp_path / "fake_claude.py"
    write_fake_claude(fake_claude, textwrap.dedent("""
        import json, os
        run_dir = os.environ["PERSONA_RUN_DIR"]
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "result.json"), "w") as f:
            json.dump({"status": "complete", "steps": 3}, f)
        with open(os.path.join(run_dir, "findings.json"), "w") as f:
            json.dump({"dead_ends": [], "confusions": ["nothing confusing"]}, f)
        print(json.dumps({"result": "ok"}))
    """))

    port = free_port()
    cfg = {
        "name": "persona",
        "serve_cmd": f"{sys.executable} -m http.server {port}",
        "port": port,
        "tasks": [{"task": "Find the greeting", "max_steps": 4, "persona": "impatient new user"}],
        "runs": 1,
    }
    proc = run_scorer(cfg, tmp_path, env_extra={"NIGHTSHIFT_CLAUDE": str(fake_claude)})
    assert proc.returncode == 0, proc.stderr

    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    out = json.loads(lines[0])
    assert out["name"] == "persona"
    assert out["ok"] is True

    # steps=3 <= max_steps*1.5=6, status complete -> completed True;
    # step_ratio = min(1, 4/3) = 1.0; no judge.json -> unjudged formula.
    expected = 100.0 * (0.625 * 1 + 0.375 * 1.0)
    assert abs(out["value"] - expected) < 1e-6

    assert "Find the greeting" in out["raw"]["per_task"]
    task_raw = out["raw"]["per_task"]["Find the greeting"]
    assert abs(task_raw["value"] - expected) < 1e-6
    run_dir = task_raw["runs"][0]["run_dir"]
    assert os.path.isdir(run_dir)
    assert os.path.exists(os.path.join(run_dir, "score.json"))


def test_persona_scorer_no_result_json_is_ok_false(tmp_path):
    fake_claude = tmp_path / "fake_claude_noop.py"
    write_fake_claude(fake_claude, "print('did nothing, wrote no files')\n")

    port = free_port()
    cfg = {
        "name": "persona",
        "serve_cmd": f"{sys.executable} -m http.server {port}",
        "port": port,
        "tasks": [{"task": "Do a thing", "max_steps": 4, "persona": "x"}],
        "runs": 1,
    }
    proc = run_scorer(cfg, tmp_path, env_extra={"NIGHTSHIFT_CLAUDE": str(fake_claude)})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False
    assert "result.json" in out["error"]


def test_persona_scorer_server_never_comes_up(tmp_path):
    fake_claude = tmp_path / "fake_claude_unused.py"
    write_fake_claude(fake_claude, "print('should never run')\n")

    cfg = {
        "name": "persona",
        "serve_cmd": "sleep 5",
        "port": free_port(),
        "tasks": [{"task": "Do a thing", "max_steps": 4, "persona": "x"}],
        "runs": 1,
    }
    proc = run_scorer(cfg, tmp_path, env_extra={"NIGHTSHIFT_CLAUDE": str(fake_claude)})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False
    assert "port" in out["error"]


def test_persona_scorer_claude_missing(tmp_path):
    port = free_port()
    cfg = {
        "name": "persona",
        "serve_cmd": f"{sys.executable} -m http.server {port}",
        "port": port,
        "tasks": [{"task": "Do a thing", "max_steps": 4, "persona": "x"}],
        "runs": 1,
    }
    proc = run_scorer(cfg, tmp_path, env_extra={"NIGHTSHIFT_CLAUDE": "/no/such/claude-binary"})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False
    assert "claude" in out["error"]
