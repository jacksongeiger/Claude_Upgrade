"""Tests for tokens_extract.py.

Usage: run with
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest \
        /home/user/Claude_Upgrade/pipeline/tests/test_tokens_extract.py -q -p no:cacheprovider

Exit codes follow pytest's own convention (0 all passed, 1 failures/errors).
Covers: (1) the fixture-JSON -> proposal conversion (fonts, type scale
naming, spacing snapping, hex colors with roles, radii); (2) a live run
against a small HTML page served by `python3 -m http.server`, skipped
cleanly when `node` is not on PATH.
"""

import http.server
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parent.parent
TOKENS_EXTRACT_PY = PIPELINE_DIR / "tokens_extract.py"

spec = importlib.util.spec_from_file_location("tokens_extract", TOKENS_EXTRACT_PY)
tokens_extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tokens_extract)


FIXTURE = {
    "fonts": [
        {"family": "Arial", "count": 10, "roles": ["body"]},
        {"family": "Georgia", "count": 3, "roles": ["heading"]},
        {"family": "Menlo", "count": 2, "roles": ["mono"]},
    ],
    "type_scale": [
        {"px": 13, "count": 1},
        {"px": 14, "count": 2},
        {"px": 16, "count": 3},
        {"px": 24, "count": 2},
        {"px": 32, "count": 1},
    ],
    "spacing_scale": [
        {"px": 4, "count": 5},
        {"px": 8, "count": 3},
        {"px": 14, "count": 1},
        {"px": 16, "count": 2},
    ],
    "colors": [
        {"value": "rgb(34, 34, 34)", "count": 10, "roles": ["text"]},
        {"value": "rgba(0, 0, 0, 0)", "count": 5, "roles": ["background"]},
        {"value": "rgb(255, 255, 255)", "count": 8, "roles": ["background"]},
        {"value": "rgba(0, 120, 200, 0.5)", "count": 3, "roles": ["border"]},
    ],
    "radii": [
        {"value": "4px", "count": 5},
        {"value": "8px", "count": 3},
        {"value": "16px", "count": 1},
    ],
    "shadows": [
        {"value": "0 2px 4px rgba(0,0,0,0.2)", "count": 5},
        {"value": "0 4px 8px rgba(0,0,0,0.3)", "count": 2},
    ],
    "line_heights": [{"value": "normal", "count": 20}],
    "weights": [{"value": "400", "count": 18}, {"value": "700", "count": 3}],
}


def test_build_proposal_from_fixture():
    proposal = tokens_extract.build_proposal(FIXTURE, url="https://example.com", name="Example")

    assert proposal["source"]["url"] == "https://example.com"
    assert proposal["source"]["name"] == "Example"
    assert "sampled_at" in proposal["source"]

    # fonts: highest-count family per role
    assert proposal["fonts"] == {"body": "Arial", "heading": "Georgia", "mono": "Menlo"}

    # type scale: 13 (count 1) and 32 (count 1) dropped for being seen < 2 times;
    # remaining sizes named by nearest ladder entry.
    assert proposal["type_scale"] == {"sm": 14, "base": 16, "2xl": 24}

    # spacing: 14px snaps onto the 16px bucket (round(14/4)*4 == 16),
    # keyed by px // 4.
    assert proposal["spacing"] == {"1": 4, "2": 8, "4": 16}

    # colors: transparent dropped; converted to hex, ordered by count desc,
    # alpha kept only when < 1.
    colors = proposal["colors"]
    assert list(colors.keys()) == ["c1", "c2", "c3"]
    assert colors["c1"] == {"hex": "#222222", "seen_as": ["text"], "count": 10}
    assert colors["c2"] == {"hex": "#ffffff", "seen_as": ["background"], "count": 8}
    assert colors["c3"]["hex"] == "#0078c8"
    assert colors["c3"]["seen_as"] == ["border"]
    assert colors["c3"]["alpha"] == 0.5

    # radii ascending -> sm/md/lg
    assert proposal["radius"] == {"sm": "4px", "md": "8px", "lg": "16px"}

    # shadows by count desc -> sm/md
    assert proposal["shadow"] == {
        "sm": "0 2px 4px rgba(0,0,0,0.2)",
        "md": "0 4px 8px rgba(0,0,0,0.3)",
    }


def test_cli_from_json(tmp_path):
    fixture_path = tmp_path / "raw.json"
    fixture_path.write_text(json.dumps(FIXTURE))
    out_path = tmp_path / "proposed.json"

    rc = tokens_extract.main(
        ["--from-json", str(fixture_path), "--out", str(out_path), "--name", "Example"]
    )
    assert rc == 0
    assert out_path.exists()

    written = json.loads(out_path.read_text())
    assert written["fonts"]["body"] == "Arial"
    assert written["source"]["name"] == "Example"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


LIVE_HTML = """<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: Arial, sans-serif; font-size: 16px; color: #222222; }
  h1 { font-family: Georgia, serif; font-size: 32px; }
  p { font-size: 16px; margin: 12px 0; }
  button { border-radius: 6px; padding: 8px 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.2); }
</style>
</head>
<body>
  <h1>Title</h1>
  <p>One</p>
  <p>Two</p>
  <button>Click</button>
</body>
</html>
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_live_extraction(tmp_path):
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(LIVE_HTML)

    port = _free_port()
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(site_dir), **kw
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        assert _wait_for_port(port), "test HTTP server never came up"
        out_path = tmp_path / "proposed.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(TOKENS_EXTRACT_PY),
                "--url",
                "http://127.0.0.1:{}/".format(port),
                "--out",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

        data = json.loads(out_path.read_text())
        assert data["source"]["url"] == "http://127.0.0.1:{}/".format(port)
        assert "body" in data["fonts"]
        assert "heading" in data["fonts"]
        assert len(data["type_scale"]) >= 1
        assert "sm" in data["radius"] or "md" in data["radius"]
        assert "sm" in data["shadow"]
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
