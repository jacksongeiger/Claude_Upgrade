import importlib.util
import json
import os
import subprocess
import sys

LOOP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCORE = os.path.join(LOOP_DIR, "score.py")

spec = importlib.util.spec_from_file_location("score", SCORE)
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)


def run_score(args):
    proc = subprocess.run(
        [sys.executable, SCORE] + args,
        capture_output=True, text=True,
    )
    return proc


def write_config(path, scorers):
    cfg = {
        "version": 1, "project_dir": str(path.parent), "slug": "x",
        "main_branch": "main", "scorers": scorers, "regress_eps": 1.0,
    }
    path.write_text(json.dumps(cfg))
    return cfg


def base_args(config, workdir, iter_=1, commit="abc123", cost=0.1, duration=1,
               task="bl-001", outcome="kept", out=None, manifest=None, dry=False):
    args = [
        "--config", str(config), "--workdir", str(workdir),
        "--iter", str(iter_), "--commit", commit, "--cost", str(cost),
        "--duration", str(duration), "--task", task, "--outcome", outcome,
    ]
    if out is not None:
        args += ["--out", str(out)]
    if manifest is not None:
        args += ["--manifest", str(manifest)]
    if dry:
        args.append("--dry")
    return args


def test_project_root_for_wt_layout():
    assert score.project_root_for("/proj/.loop/wt/t-014a") == "/proj"
    assert score.project_root_for("/proj/.loop/wt/loop") == "/proj"


def test_project_root_for_plain_workdir():
    assert score.project_root_for("/some/plain/dir") == "/some/plain/dir"


def test_scores_a_simple_passing_config(tmp_path):
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 1.0, "runs": 1, "cmd": "exit 0"},
    ])
    out_path = tmp_path / "scores.jsonl"
    proc = run_score(base_args(config, tmp_path, out=out_path))
    assert proc.returncode == 0, proc.stderr
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    assert row["composite"] == 100.0
    assert row["dims"]["tests"]["ok"] is True
    assert out_path.exists()
    lines = out_path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["iter"] == 1


def test_manifest_write_then_verify_ok(tmp_path):
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 1.0, "runs": 1, "cmd": "exit 0"},
    ])
    manifest = tmp_path / "manifest.sha256"
    proc = run_score(["--manifest-write", str(manifest), "--config", str(config)])
    assert proc.returncode == 0, proc.stderr
    assert manifest.exists()
    content = manifest.read_text()
    assert "config.json" in content
    assert "scorers/tests.py" in content

    out_path = tmp_path / "scores.jsonl"
    proc2 = run_score(base_args(config, tmp_path, out=out_path, manifest=manifest))
    assert proc2.returncode == 0, proc2.stderr
    row = json.loads(proc2.stdout.strip().splitlines()[-1])
    assert row["composite"] == 100.0


def test_manifest_mismatch_exits_4_and_composite_null(tmp_path):
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 1.0, "runs": 1, "cmd": "exit 0"},
    ])
    manifest = tmp_path / "manifest.sha256"
    # deliberately wrong hash for both entries
    manifest.write_text(
        "0" * 64 + "  config.json\n" + "0" * 64 + "  scorers/tests.py\n"
    )
    out_path = tmp_path / "scores.jsonl"
    proc = run_score(base_args(config, tmp_path, out=out_path, manifest=manifest))
    assert proc.returncode == 4
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["ok"] is False
    assert "manifest mismatch" in result["error"]

    lines = out_path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["composite"] is None
    assert row["infra"] == "manifest"
    assert row["outcome"] == "kept"


def test_manifest_mismatch_dry_run_does_not_append(tmp_path):
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 1.0, "runs": 1, "cmd": "exit 0"},
    ])
    manifest = tmp_path / "manifest.sha256"
    manifest.write_text("0" * 64 + "  config.json\n")
    out_path = tmp_path / "scores.jsonl"
    proc = run_score(base_args(config, tmp_path, out=out_path, manifest=manifest, dry=True))
    assert proc.returncode == 4
    assert not out_path.exists()


def test_median_over_three_runs(tmp_path):
    # A fixture script that returns a different pass count on each of its 3
    # invocations (tracked via a counter file), so the scorer's median over
    # `runs: 3` is meaningfully exercised.
    script = tmp_path / "flaky.sh"
    script.write_text("""#!/usr/bin/env bash
counter="$1"
n=$(cat "$counter" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$counter"
case "$n" in
  1) echo "8 passed, 2 failed in 0.1s" ;;
  2) echo "9 passed, 1 failed in 0.1s" ;;
  3) echo "10 passed in 0.1s" ;;
esac
exit 0
""")
    script.chmod(0o755)
    counter = tmp_path / "counter"

    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 1.0, "runs": 3, "cmd": f"bash {script} {counter}"},
    ])
    out_path = tmp_path / "scores.jsonl"
    proc = run_score(base_args(config, tmp_path, out=out_path))
    assert proc.returncode == 0, proc.stderr
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    # values: 80.0, 90.0, 100.0 -> median 90.0
    assert abs(row["dims"]["tests"]["value"] - 90.0) < 1e-6
    assert abs(row["composite"] - 90.0) < 1e-6
    assert counter.read_text().strip() == "3"


def test_unconfigured_scorer_makes_composite_null(tmp_path):
    # perf.py without bench_cmd/baseline reports ok:false -> composite null,
    # exercising the "a scorer that crashes/fails" -> composite null path.
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 0.5, "runs": 1, "cmd": "exit 0"},
        {"name": "perf", "weight": 0.5, "runs": 1},
    ])
    out_path = tmp_path / "scores.jsonl"
    proc = run_score(base_args(config, tmp_path, out=out_path))
    assert proc.returncode == 4
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    assert row["composite"] is None
    assert row["dims"]["tests"]["ok"] is True
    assert row["dims"]["perf"]["ok"] is False


def test_nonexistent_scorer_file_is_ok_false_not_a_crash(tmp_path):
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "does-not-exist", "weight": 1.0, "runs": 1},
    ])
    out_path = tmp_path / "scores.jsonl"
    proc = run_score(base_args(config, tmp_path, out=out_path))
    assert proc.returncode == 4
    row = json.loads(proc.stdout.strip().splitlines()[-1])
    assert row["composite"] is None
    assert row["dims"]["does-not-exist"]["ok"] is False


def test_dry_run_does_not_append(tmp_path):
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 1.0, "runs": 1, "cmd": "exit 0"},
    ])
    out_path = tmp_path / "scores.jsonl"
    proc = run_score(base_args(config, tmp_path, out=out_path, dry=True))
    assert proc.returncode == 0
    assert not out_path.exists()


def test_default_out_path_under_project_root(tmp_path):
    project = tmp_path / "proj"
    wt = project / ".loop" / "wt" / "t-014a"
    wt.mkdir(parents=True)
    config = tmp_path / "config.json"
    write_config(config, [
        {"name": "tests", "weight": 1.0, "runs": 1, "cmd": "exit 0"},
    ])
    proc = run_score(base_args(config, wt))
    assert proc.returncode == 0, proc.stderr
    expected_out = project / ".loop" / "scores.jsonl"
    assert expected_out.exists()


# ---------------------------------------------------------------------------
# pins: in-repo judges are hashed where the scorers run
# ---------------------------------------------------------------------------

def _pinned_project(tmp_path):
    """A project whose eval lives inside the repo, plus a worktree copy."""
    proj = tmp_path / "proj"
    (proj / "eval").mkdir(parents=True)
    (proj / "corpora").mkdir()
    (proj / "eval" / "harness.py").write_text("print('{\"ok\": true, \"value\": 90}')\n")
    (proj / "corpora" / "cases.yaml").write_text("- id: a\n")
    config = proj / "config.json"
    write_config(config, [
        {"name": "eval", "script": "cmd", "weight": 1.0, "runs": 1,
         "cmd": "python3 eval/harness.py", "pins": ["eval/harness.py", "corpora/*.yaml"]},
    ])
    import shutil
    wt = tmp_path / "wt"
    shutil.copytree(proj, wt)
    return proj, config, wt


def test_pins_are_written_repo_relative_and_verified_in_the_workdir(tmp_path):
    proj, config, wt = _pinned_project(tmp_path)
    manifest = tmp_path / "manifest.sha256"
    proc = run_score(["--manifest-write", str(manifest), "--config", str(config)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = manifest.read_text()
    assert "repo:eval/harness.py" in text and "repo:corpora/cases.yaml" in text
    # an untouched worktree verifies
    proc = run_score(base_args(config, wt, out=tmp_path / "s.jsonl", manifest=manifest))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_editing_a_pinned_file_in_the_worktree_is_a_manifest_mismatch(tmp_path):
    proj, config, wt = _pinned_project(tmp_path)
    manifest = tmp_path / "manifest.sha256"
    run_score(["--manifest-write", str(manifest), "--config", str(config)])
    (wt / "corpora" / "cases.yaml").write_text("- id: a\n- id: relabelled\n")
    proc = run_score(base_args(config, wt, out=tmp_path / "s.jsonl", manifest=manifest))
    assert proc.returncode == 4
    assert "repo:corpora/cases.yaml" in proc.stdout


def test_a_new_file_matching_a_pin_glob_is_a_mismatch_too(tmp_path):
    proj, config, wt = _pinned_project(tmp_path)
    manifest = tmp_path / "manifest.sha256"
    run_score(["--manifest-write", str(manifest), "--config", str(config)])
    (wt / "corpora" / "extra.yaml").write_text("- id: planted\n")
    proc = run_score(base_args(config, wt, out=tmp_path / "s.jsonl", manifest=manifest))
    assert proc.returncode == 4
    assert "repo:corpora/extra.yaml" in proc.stdout


def test_deleting_a_pinned_file_is_a_mismatch(tmp_path):
    proj, config, wt = _pinned_project(tmp_path)
    manifest = tmp_path / "manifest.sha256"
    run_score(["--manifest-write", str(manifest), "--config", str(config)])
    (wt / "corpora" / "cases.yaml").unlink()
    proc = run_score(base_args(config, wt, out=tmp_path / "s.jsonl", manifest=manifest))
    assert proc.returncode == 4


def test_pins_that_match_nothing_refuse_to_write(tmp_path):
    proj, config, wt = _pinned_project(tmp_path)
    write_config(config, [{"name": "eval", "script": "cmd", "weight": 1.0, "runs": 1,
                           "cmd": "true", "pins": ["nowhere/*.py"]}])
    proc = run_score(["--manifest-write", str(tmp_path / "m"), "--config", str(config)])
    assert proc.returncode == 1 and "pins match no file" in proc.stdout
