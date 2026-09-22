import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import PLAYWRIGHT_MISSING, playwright_ok

KIT = Path(__file__).resolve().parents[1]
DRIVER = KIT / "js" / "persona_driver.cjs"

pytestmark = pytest.mark.skipif(not playwright_ok(), reason=PLAYWRIGHT_MISSING)

PAGE = """<!doctype html>
<html><body>
<button onclick="document.getElementById('msg').textContent='revealed!'">Reveal</button>
<div id="msg">hidden</div>
</body></html>
"""


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


@pytest.fixture
def served_page(tmp_path):
    (tmp_path / "index.html").write_text(PAGE)
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port)],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_port(port), "python3 -m http.server did not start"
        yield f"http://127.0.0.1:{port}/index.html"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_one_shot_goto_click_read(tmp_path, served_page):
    run_dir = tmp_path / "run"
    actions = [
        {"action": "goto", "url": served_page},
        {"action": "click", "target": "Reveal"},
        {"action": "read"},
    ]
    proc = subprocess.run(
        ["node", str(DRIVER), "--run", str(run_dir), "--url", served_page,
         "--actions", json.dumps(actions)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 3

    assert lines[0]["step"] == 1
    assert lines[0]["action"] == "goto"
    assert lines[0]["ok"] is True

    assert lines[1]["action"] == "click"
    assert lines[1]["ok"] is True

    assert lines[2]["action"] == "read"
    assert lines[2]["ok"] is True

    # the visible text changed after the click
    assert "revealed!" not in lines[0]["visible_text"]
    assert "revealed!" in lines[2]["visible_text"]

    trail = json.loads((run_dir / "trail.json").read_text())
    assert len(trail) == 3
    assert trail[-1]["action"] == "read"

    shot = Path(lines[1]["shot"])
    assert shot.exists()
    assert shot.stat().st_size > 0

    # no `done` action was sent, so no result.json is expected yet
    assert not (run_dir / "result.json").exists()


def test_one_shot_done_writes_result(tmp_path, served_page):
    run_dir = tmp_path / "run2"
    actions = [
        {"action": "goto", "url": served_page},
        {"action": "click", "target": "Reveal"},
        {"action": "done", "status": "complete"},
    ]
    proc = subprocess.run(
        ["node", str(DRIVER), "--run", str(run_dir), "--url", served_page,
         "--actions", json.dumps(actions)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads((run_dir / "result.json").read_text())
    # steps = the persona's own actions before `done` (goto, click), never done itself
    assert result == {"status": "complete", "steps": 2}


def test_click_reports_error_without_crashing(tmp_path, served_page):
    run_dir = tmp_path / "run3"
    actions = [
        {"action": "goto", "url": served_page},
        {"action": "click", "target": "Does Not Exist Anywhere"},
        {"action": "read"},
    ]
    proc = subprocess.run(
        ["node", str(DRIVER), "--run", str(run_dir), "--url", served_page,
         "--actions", json.dumps(actions)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 3
    assert lines[1]["ok"] is False
    assert lines[1]["error"]
    # the session keeps going after a failed action
    assert lines[2]["action"] == "read"
