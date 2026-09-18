import json
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SCORER = KIT / "scorers" / "cmd.py"


def run(cfg, workdir):
    p = subprocess.run([sys.executable, str(SCORER), "--config", json.dumps(cfg), "--workdir", str(workdir)],
                       capture_output=True, text=True)
    assert p.returncode == 0
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_value_line_is_found_among_noise(tmp_path):
    out = run({"name": "x", "cmd": "echo noise; echo '{\"value\": 42.5, \"extra\": 1}'; echo more"}, tmp_path)
    assert out["ok"] is True and out["value"] == 42.5 and out["raw"]["extra"] == 1


def test_no_value_line_is_not_ok(tmp_path):
    out = run({"name": "x", "cmd": "echo hello"}, tmp_path)
    assert out["ok"] is False and "no JSON line" in out["error"]


def test_clipped_to_0_100(tmp_path):
    assert run({"name": "x", "cmd": "echo '{\"value\": 250}'"}, tmp_path)["value"] == 100.0
    assert run({"name": "x", "cmd": "echo '{\"value\": -3}'"}, tmp_path)["value"] == 0.0


def test_unconfigured(tmp_path):
    out = run({"name": "x"}, tmp_path)
    assert out["ok"] is False and "not configured" in out["error"]


def test_timeout(tmp_path):
    out = run({"name": "x", "cmd": "sleep 3; echo '{\"value\": 1}'", "timeout_s": 1}, tmp_path)
    assert out["ok"] is False and "timeout" in out["error"]


def test_metric_and_scale_read_a_named_fraction(tmp_path):
    """Inbox Triage: evals/run.py prints {"accuracy_tags": 0.725, "n": 40}; the spec's evals check names the key."""
    out = run({"name": "evals", "cmd": "echo '{\"accuracy_tags\": 0.725, \"n\": 40}'", "metric": "accuracy_tags", "scale": 100}, tmp_path)
    assert out["ok"] is True and abs(out["value"] - 72.5) < 1e-9 and out["raw"]["n"] == 40 and out["raw"]["metric"] == "accuracy_tags"
    out = run({"name": "evals", "cmd": "echo '{\"value\": 5}'", "metric": "accuracy_tags"}, tmp_path)
    assert out["ok"] is False and "accuracy_tags" in out["error"]
