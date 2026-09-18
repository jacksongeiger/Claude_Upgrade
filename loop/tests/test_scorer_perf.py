import json
import os
import subprocess
import sys

SCORER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scorers", "perf.py")


def run(cfg, workdir):
    proc = subprocess.run([sys.executable, SCORER, "--config", json.dumps(cfg), "--workdir", str(workdir)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads([l for l in proc.stdout.splitlines() if l.strip()][-1])


def test_pipeline_bench_shape_scores_against_budget(tmp_path):
    bench = tmp_path / "bench.sh"
    bench.write_text("#!/usr/bin/env bash\necho '{\"ms_p95\": 15}'\n")
    bench.chmod(0o755)
    out = run({"name": "perf", "bench_cmd": f"bash {bench}", "baseline": {"ms_p95": 30}, "lower_is_better": True}, tmp_path)
    assert out["ok"] is True
    # twice as fast as the budget → ratio 2 clipped to 1.5 → 100
    assert abs(out["value"] - 100.0) < 1e-6
    rows = [json.loads(l) for l in (tmp_path / "perf" / "results.jsonl").read_text().splitlines()]
    assert rows[-1]["metric"] == "ms_p95" and rows[-1]["value"] == 15
