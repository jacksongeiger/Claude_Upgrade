"""Tests for pipeline/fetch.py against a local HTTP server and a closed port."""
import http.server
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
FETCH = KIT / "fetch.py"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path.startswith("/api"):
            body = json.dumps({"downloads": {"monthly": "12,345"}, "objects": [{"package": {"version": "1.2.3"}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/page"):
            body = b"<html><head><style>x{}</style><script>var a=1;</script></head><body><h1>Pain &amp; gain</h1><p>I hate changelogs. I hate them. 42 users agree.</p></body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html")
        elif self.path.startswith("/gone"):
            body = b"nope"; self.send_response(404)
        elif self.path.startswith("/paid"):
            body = b"login"; self.send_response(403)
        else:
            body = b"hello"; self.send_response(200)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def run(args, env_no_proxy=True):
    import os
    env = dict(os.environ)
    # the local server must not go through the sandbox proxy
    env["NO_PROXY"] = "127.0.0.1,localhost"; env["no_proxy"] = "127.0.0.1,localhost"
    proc = subprocess.run([sys.executable, str(FETCH)] + args, capture_output=True, text=True, env=env)
    return proc.returncode, (json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else None), proc.stderr


def test_json_extract_writes_body_and_numeric_value(server, tmp_path):
    rc, out, err = run(["get", f"{server}/api", "--extract", "json:downloads.monthly", "--run-dir", str(tmp_path)])
    assert rc == 0, err
    assert out["ok"] and out["value"] == 12345 and out["reason"] is None
    assert Path(out["body_path"]).exists() and out["body_hash"] and len(out["body_hash"]) == 64
    assert out["extracted_by"] == "json:downloads.monthly"


def test_json_index_path(server, tmp_path):
    rc, out, _ = run(["get", f"{server}/api", "--extract", "json:objects[0].package.version", "--run-dir", str(tmp_path)])
    assert rc == 0 and out["value"] == "1.2.3"


def test_html_is_stripped_and_regex_and_count_work(server, tmp_path):
    rc, out, _ = run(["get", f"{server}/page", "--extract", "regex:(\\d+) users agree", "--run-dir", str(tmp_path)])
    assert rc == 0 and out["value"] == 42
    body = Path(out["body_path"]).read_text()
    assert "<script>" not in body and "var a=1" not in body and "Pain & gain" in body
    rc, out, _ = run(["get", f"{server}/page", "--extract", "count:I hate", "--run-dir", str(tmp_path)])
    assert rc == 0 and out["value"] == 2


def test_missing_extract_is_exit_2_with_reason(server, tmp_path):
    rc, out, _ = run(["get", f"{server}/api", "--extract", "json:nothing.here", "--run-dir", str(tmp_path)])
    assert rc == 2 and out["reason"] == "extract" and out["ok"] is False and out["body_hash"]


def test_404_and_paywall_reasons(server, tmp_path):
    rc, out, _ = run(["get", f"{server}/gone", "--run-dir", str(tmp_path)])
    assert rc == 4 and out["reason"] == "404" and out["status"] == 404
    rc, out, _ = run(["get", f"{server}/paid", "--run-dir", str(tmp_path)])
    assert rc == 4 and out["reason"] == "paywall"


def test_unreachable_host_is_egress(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    rc, out, _ = run(["get", f"http://127.0.0.1:{port}/x", "--run-dir", str(tmp_path)])
    assert rc == 4 and out["reason"] in ("egress", "timeout")


def test_ledger_row_shape_and_origin_rule(server, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rc, out, _ = run(["get", f"{server}/api", "--extract", "json:downloads.monthly", "--run-dir", str(tmp_path),
                      "--ledger", str(ledger), "--claim", "c1", "--measure", "monthly_downloads", "--unit", "downloads/month",
                      "--origin", "registry.npmjs.org", "--required"])
    assert rc == 0
    row = json.loads(ledger.read_text().splitlines()[-1])
    assert row["claim"] == "c1" and row["measure"] == "monthly_downloads" and row["value"] == 12345
    assert row["source"]["url"].endswith("/api") and row["source"]["body_hash"] == out["body_hash"]
    assert row["origin"] == "registry.npmjs.org" and row["required"] is True and row["reason"] is None
    rc, out, _ = run(["get", f"{server}/api", "--ledger", str(ledger), "--claim", "c1", "--measure", "m",
                      "--origin", "some guy on a forum said so", "--run-dir", str(tmp_path)])
    assert rc == 1 and "host or a handle" in out["error"]


def test_failed_fetch_still_writes_a_ledger_row_with_reason(server, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rc, out, _ = run(["get", f"{server}/gone", "--ledger", str(ledger), "--claim", "c2", "--measure", "m", "--run-dir", str(tmp_path)])
    assert rc == 4
    row = json.loads(ledger.read_text().splitlines()[-1])
    assert row["value"] is None and row["reason"] == "404" and row["origin"] == "127.0.0.1"


def test_probe_writes_reachable_set(server, tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    hosts = tmp_path / "hosts.txt"
    hosts.write_text(f"up {server}/api\ngone {server}/gone\ndown http://127.0.0.1:{port}/\n")
    out_path = tmp_path / "reachable.json"
    rc, out, err = run(["probe", "--hosts", str(hosts), "--out", str(out_path)])
    assert rc == 0, err
    data = json.loads(out_path.read_text())
    assert data["hosts"]["up"]["reachable"] is True
    assert data["hosts"]["gone"]["reachable"] is False and data["hosts"]["gone"]["reason"] == "404"
    assert data["hosts"]["down"]["reachable"] is False
    assert out["reachable"] == ["up"] and out["blocked"] == ["down", "gone"]
