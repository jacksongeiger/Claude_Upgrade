"""Tests for pipeline/gates.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest \
        pipeline/tests/test_gates.py -q -p no:cacheprovider
"""
import http.server
import json
import shutil
import socket
import struct
import subprocess
import sys
import threading
import zlib
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
GATES = PIPELINE_DIR / "gates.py"

sys.path.insert(0, str(PIPELINE_DIR))
import gates  # noqa: E402


# ---------------------------------------------------------------------------
# A tiny stdlib PNG writer (zlib + struct), independent of gates.py's own
# decoder, so the diff tests exercise gates.py against known-good input.
# ---------------------------------------------------------------------------

def _chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path, width, height, get_pixel):
    """get_pixel(x, y) -> (r, g, b), 8-bit RGB, no interlacing, filter type 0."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: None
        for x in range(width):
            r, g, b = get_pixel(x, y)
            raw += bytes((r, g, b))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    path.write_bytes(png)


def run_gates(args, cwd=None):
    proc = subprocess.run([sys.executable, str(GATES)] + args, cwd=cwd,
                           capture_output=True, text=True)
    return proc


# ---------------------------------------------------------------------------
# Pure diff tests
# ---------------------------------------------------------------------------

def test_diff_identical(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    write_png(a, 10, 10, lambda x, y: (100, 150, 200))
    write_png(b, 10, 10, lambda x, y: (100, 150, 200))
    assert gates.diff_fraction(a, b) == 0.0


def test_diff_slightly_different(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    write_png(a, 10, 10, lambda x, y: (100, 100, 100))

    def pixel_b(x, y):
        if x == 0 and y == 0:
            return (255, 0, 0)  # one pixel changed a lot
        return (100, 100, 100)

    write_png(b, 10, 10, pixel_b)
    frac = gates.diff_fraction(a, b)
    assert 0.0 < frac < 0.02
    assert frac == pytest.approx(1 / 100)


def test_diff_within_tolerance_does_not_count(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    write_png(a, 5, 5, lambda x, y: (100, 100, 100))
    write_png(b, 5, 5, lambda x, y: (105, 100, 100))  # diff of 5, under tolerance of 8
    assert gates.diff_fraction(a, b) == 0.0


def test_diff_very_different(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    write_png(a, 10, 10, lambda x, y: (0, 0, 0))
    write_png(b, 10, 10, lambda x, y: (255, 255, 255))
    assert gates.diff_fraction(a, b) == 1.0


def test_diff_size_mismatch_raises(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    write_png(a, 10, 10, lambda x, y: (0, 0, 0))
    write_png(b, 5, 5, lambda x, y: (0, 0, 0))
    with pytest.raises(gates.PngError):
        gates.diff_fraction(a, b)


# ---------------------------------------------------------------------------
# perf checks via the CLI
# ---------------------------------------------------------------------------

def test_perf_pass(tmp_path):
    check = {"name": "p", "kind": "perf", "cmd": "echo '{\"ms_p95\": 40}'", "metric": "ms_p95", "max": 50}
    proc = run_gates(["run", "--check", json.dumps(check), "--workdir", str(tmp_path)])
    assert proc.returncode == 0
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True and out["kind"] == "perf" and out["diff_fraction"] is None


def test_perf_fail(tmp_path):
    check = {"name": "p", "kind": "perf", "cmd": "echo '{\"ms_p95\": 90}'", "metric": "ms_p95", "max": 50}
    proc = run_gates(["run", "--check", json.dumps(check), "--workdir", str(tmp_path)])
    assert proc.returncode == 2
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False


def test_perf_min(tmp_path):
    check = {"name": "p", "kind": "perf", "cmd": "echo '{\"fps\": 61}'", "metric": "fps", "min": 60}
    proc = run_gates(["run", "--check", json.dumps(check), "--workdir", str(tmp_path)])
    assert proc.returncode == 0


def test_perf_infra_no_json(tmp_path):
    check = {"name": "p", "kind": "perf", "cmd": "echo not-json", "metric": "x", "max": 1}
    proc = run_gates(["run", "--check", json.dumps(check), "--workdir", str(tmp_path)])
    assert proc.returncode == 4


def test_perf_infra_missing_metric(tmp_path):
    check = {"name": "p", "kind": "perf", "cmd": "echo '{\"other\": 1}'", "metric": "x", "max": 1}
    proc = run_gates(["run", "--check", json.dumps(check), "--workdir", str(tmp_path)])
    assert proc.returncode == 4


def test_perf_noise_before_json_line(tmp_path):
    check = {"name": "p", "kind": "perf", "cmd": "echo noise; echo '{\"ms\": 5}'", "metric": "ms", "max": 10}
    proc = run_gates(["run", "--check", json.dumps(check), "--workdir", str(tmp_path)])
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# accept subcommand
# ---------------------------------------------------------------------------

def test_accept_copies_last_to_baseline(tmp_path):
    baseline_dir = tmp_path / "baseline"
    last_dir = tmp_path / "last"
    last_dir.mkdir()
    write_png(last_dir / "home.png", 4, 4, lambda x, y: (1, 2, 3))

    proc = run_gates(["accept", "home", "--baseline-dir", str(baseline_dir), "--last-dir", str(last_dir)])
    assert proc.returncode == 0
    assert (baseline_dir / "home.png").exists()
    assert (baseline_dir / "home.png").read_bytes() == (last_dir / "home.png").read_bytes()


def test_accept_missing_last_is_infra(tmp_path):
    proc = run_gates(["accept", "nope", "--baseline-dir", str(tmp_path / "b"), "--last-dir", str(tmp_path / "l")])
    assert proc.returncode == 4


# ---------------------------------------------------------------------------
# Screenshot gate end to end: needs-baseline -> accept -> pass.
# Requires node + a global playwright install (this environment has both);
# skips cleanly otherwise.
# ---------------------------------------------------------------------------

def _node_and_playwright_available():
    if shutil.which("node") is None:
        return False
    try:
        subprocess.run(
            ["node", "-e", "require('./pw.cjs').loadPlaywright()"],
            cwd=str(PIPELINE_DIR / "js"),
            capture_output=True, text=True, timeout=30,
        ).check_returncode()
        return True
    except Exception:
        return False


NODE_OK = _node_and_playwright_available()


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a, **k):
        pass


@pytest.fixture
def static_server(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><html><body><h1>Hi</h1></body></html>")
    port = _free_port()
    handler = lambda *a, **k: _QuietHandler(*a, directory=str(tmp_path), **k)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/index.html"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


@pytest.mark.skipif(not NODE_OK, reason="node/playwright not available")
def test_screenshot_gate_needs_baseline_then_accept_then_pass(tmp_path, static_server):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    check = {
        "name": "home", "kind": "screenshot", "url": static_server,
        "viewport": [320, 240], "theme": "light", "threshold": 0.01, "wait_ms": 100,
    }

    proc1 = run_gates(["run", "--check", json.dumps(check), "--workdir", str(workdir)])
    assert proc1.returncode == 3, proc1.stdout + proc1.stderr
    result1 = json.loads(proc1.stdout.strip().splitlines()[-1])
    assert "no baseline" in result1["detail"]

    # accept has no --workdir flag; run it with cwd set to workdir so its
    # default relative dirs (.pipeline/gates/...) land under workdir, same
    # as `run` resolved them there.
    proc2 = subprocess.run(
        [sys.executable, str(GATES), "accept", "home"],
        cwd=str(workdir), capture_output=True, text=True,
    )
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr

    proc3 = run_gates(["run", "--check", json.dumps(check), "--workdir", str(workdir)])
    assert proc3.returncode == 0, proc3.stdout + proc3.stderr
    result3 = json.loads(proc3.stdout.strip().splitlines()[-1])
    assert result3["ok"] is True


# ---------------------------------------------------------------------------
# run-all: only gate checks, relative urls need --base-url
# ---------------------------------------------------------------------------

def test_resolve_url():
    assert gates.resolve_url("http://x/y", None) == "http://x/y"
    assert gates.resolve_url("/", "http://127.0.0.1:5173") == "http://127.0.0.1:5173/"
    assert gates.resolve_url("settings", "http://127.0.0.1:5173/") == "http://127.0.0.1:5173/settings"
    assert gates.resolve_url("/", None) is None


def test_relative_url_without_base_is_infra(tmp_path):
    check = {"name": "home", "kind": "screenshot", "url": "/"}
    proc = run_gates(["run", "--check", json.dumps(check), "--workdir", str(tmp_path)])
    assert proc.returncode == 4
    assert "base-url" in json.loads(proc.stdout.strip().splitlines()[-1])["detail"]


def test_run_all_skips_non_gate_checks(tmp_path):
    index = {
        "f-001": {"milestone": "m1", "checks": [
            {"type": "test", "cmd": "false", "must": "pass"},
            {"type": "persona", "task": "do a thing", "max_steps": 3},
            {"type": "gate", "name": "p", "kind": "perf", "cmd": "echo '{\"ms\": 5}'", "metric": "ms", "max": 10},
        ]},
        "f-002": {"milestone": "m2", "checks": [
            {"type": "gate", "name": "q", "kind": "perf", "cmd": "echo '{\"ms\": 50}'", "metric": "ms", "max": 10},
        ]},
    }
    (tmp_path / "index.json").write_text(json.dumps(index))
    proc = run_gates(["run-all", "--index", str(tmp_path / "index.json"), "--milestone", "m1",
                      "--workdir", str(tmp_path)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [json.loads(l) for l in proc.stdout.strip().splitlines()]
    assert [l["name"] for l in lines] == ["p"]
    proc = run_gates(["run-all", "--index", str(tmp_path / "index.json"), "--milestone", "m2",
                      "--workdir", str(tmp_path)])
    assert proc.returncode == 2
