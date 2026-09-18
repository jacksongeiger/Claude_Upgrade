"""persona_run.py with a fake claude: the double writes result.json (and, on the
judge call, judge.json) into PERSONA_RUN_DIR; ux_score then scores it."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent


def write_fake(path):
    path.write_text("""#!/usr/bin/env bash
# fake claude: persona call → result.json + findings.json; judge call → judge.json
run="$PERSONA_RUN_DIR"; mkdir -p "$run/shots"
if printf '%s' "$*" | grep -q persona-judge; then
  printf '{"scores":{"findability":2,"feedback":1,"recovery":2,"consistency":2,"wording":1},"total":8,"evidence":["step 2: ok"]}' > "$run/judge.json"
else
  printf '[{"step":1,"action":"goto"},{"step":2,"action":"click"},{"step":3,"action":"done"}]' > "$run/trail.json"
  printf '{"status":"complete","steps":3}' > "$run/result.json"
  printf '{"dead_ends":[],"confusions":["the save button was below the fold"]}' > "$run/findings.json"
fi
echo '{"type":"result","total_cost_usd":0.11,"result":"ok"}'
""")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_persona_run_scores_and_ledgers(tmp_path):
    fake = tmp_path / "fake_claude.sh"
    write_fake(fake)
    (tmp_path / ".loop").mkdir()
    (tmp_path / ".loop" / "backlog.yaml").write_text("rows: []\n")
    run_dir = tmp_path / ".pipeline" / "ux" / "f-1-1"
    env = dict(os.environ, NIGHTSHIFT_CLAUDE=str(fake))
    proc = subprocess.run([sys.executable, str(KIT / "persona_run.py"), "--url", "http://127.0.0.1:1/", "--task", "do it",
                           "--max-steps", "4", "--run-dir", str(run_dir), "--workdir", str(tmp_path)],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    score = json.loads(proc.stdout.strip().splitlines()[-1])
    assert score["completed"] is True and score["judged"] is True and score["judge_total"] == 8
    assert (run_dir / "judge.json").exists()
    ledger = [json.loads(l) for l in (tmp_path / ".pipeline" / "ledger.jsonl").read_text().splitlines()]
    assert {e["stage"] for e in ledger} == {"persona", "persona-judge"}
    assert "the save button was below the fold" in (tmp_path / ".loop" / "backlog.yaml").read_text()


def test_persona_run_without_result_is_infra(tmp_path):
    fake = tmp_path / "fake_claude.sh"
    fake.write_text("#!/usr/bin/env bash\necho '{\"total_cost_usd\":0.01}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, NIGHTSHIFT_CLAUDE=str(fake))
    proc = subprocess.run([sys.executable, str(KIT / "persona_run.py"), "--url", "http://127.0.0.1:1/", "--task", "x",
                           "--run-dir", str(tmp_path / "r")], capture_output=True, text=True, env=env)
    assert proc.returncode == 4
