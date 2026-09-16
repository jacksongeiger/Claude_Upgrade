import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import allowlist  # noqa: E402

KIT = Path(__file__).resolve().parent.parent
CFG = {"setup_cmd": "cd discovery && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt",
       "test_cmd": "cd discovery && ./venv/bin/python -m pytest -q",
       "scorers": [{"name": "tests", "cmd": "cd discovery && ./venv/bin/python -m pytest -q",
                    "coverage_cmd": "cd discovery && ./venv/bin/python -m pytest --cov=rdx"},
                   {"name": "bench", "script": "cmd", "cmd": "./bench.sh --json"}]}


def test_every_segment_first_word_becomes_a_rule():
    r = allowlist.rules(CFG, KIT)
    assert "Bash(./venv/bin/python:*)" in r
    assert "Bash(./venv/bin/pip:*)" in r
    assert "Bash(./bench.sh:*)" in r
    # the old bug: test_cmd starting with cd produced only Bash(cd:*)
    assert "Bash(cd:*)" in r and any(x.startswith("Bash(./venv/bin/python") for x in r)


def test_kit_scripts_and_runners_present():
    r = allowlist.rules({}, KIT)
    assert f"Bash(python3 {KIT}/*)" in r
    assert f"Bash(bash {KIT}/*)" in r
    assert "Bash(pytest:*)" in r and "Bash(npm test:*)" in r


def test_is_allowed_prefix_semantics():
    r = allowlist.rules(CFG, KIT)
    ok, _ = allowlist.is_allowed("cd discovery && ./venv/bin/python -m pytest -q tests/test_x.py 2>&1 | tail -20", r)
    assert ok
    ok, seg = allowlist.is_allowed("git push origin main", r)
    assert not ok and seg == "git push origin main"
    ok, seg = allowlist.is_allowed("cd discovery && npm install left-pad", r)
    assert not ok and seg.startswith("npm install")
    ok, _ = allowlist.is_allowed(f"python3 {KIT}/check_plan.py plan.json", r)
    assert ok


def test_no_git_push_or_bare_git_rule():
    r = allowlist.rules(CFG, KIT)
    assert not any(x in ("Bash(git:*)", "Bash(git push:*)", "Bash(*)") for x in r)


def test_cli_prints_json_array(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(CFG))
    out = subprocess.run([sys.executable, str(KIT / "allowlist.py"), "--config", str(cfg), "--kit", str(KIT)],
                         capture_output=True, text=True, check=True).stdout
    arr = json.loads(out)
    assert isinstance(arr, list) and "Bash(./venv/bin/python:*)" in arr
