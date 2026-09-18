"""Tests for pipeline/ship_check.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest \
        pipeline/tests/test_ship_check.py -q -p no:cacheprovider
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
SHIP_CHECK = PIPELINE_DIR / "ship_check.py"

sys.path.insert(0, str(PIPELINE_DIR))
import ship_check  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)


def init_repo(path):
    git(["init"], path)
    git(["checkout", "-b", "main"], path)
    git(["config", "user.email", "test@example.com"], path)
    git(["config", "user.name", "Test"], path)


def commit_all(path, msg="commit"):
    git(["add", "-A"], path)
    git(["commit", "-m", msg, "--quiet"], path)


MINIMAL_SPEC = {
    "version": 1,
    "name": "Test Project",
    "one_liner": "x",
    "stack": {"language": "python", "runtime": "python", "ui": False, "llm": False,
              "data": "none", "deploy": {"kind": "static", "cmd": "true"}},
    "needs": [],
    "milestones": [{"id": "m1", "title": "M1", "depends_on": []}],
    "features": [{"id": "f-001", "milestone": "m1", "title": "F1",
                  "acceptance": [{"type": "manual", "what": "ok"}], "priority": 1}],
    "success": ["it works"],
    "budget": {"build_usd": 10, "nightshift_cap_usd": 5},
    "human_gates": [],
}


def write_spec(workdir, spec=None):
    spec = spec or MINIMAL_SPEC
    p = Path(workdir) / "spec.json"
    p.write_text(json.dumps(spec))
    return p


FAKE_ACCEPT = textwrap.dedent("""\
    import argparse, sys
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec")
    ap.add_argument("--milestone")
    ap.add_argument("--workdir")
    ap.add_argument("--out")
    args = ap.parse_args()
    fail_file = Path(args.workdir) / ".fail_milestones"
    fail_set = set(fail_file.read_text().split()) if fail_file.exists() else set()
    sys.exit(2 if args.milestone in fail_set else 0)
""")


def write_fake_accept(workdir):
    p = Path(workdir) / "fake_accept.py"
    p.write_text(FAKE_ACCEPT)
    return p


def run_ship_check(args, cwd):
    return subprocess.run([sys.executable, str(SHIP_CHECK)] + list(args),
                           cwd=str(cwd), capture_output=True, text=True)


def load_report(workdir, tag=None):
    p = Path(workdir) / ".pipeline" / "ship" / (tag or "untagged") / "ship-report.json"
    return json.loads(p.read_text())


def find_check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def base_project(tmp_path):
    """A clean repo on main with a spec, a passing CHANGELOG and a fake accept."""
    init_repo(tmp_path)
    write_spec(tmp_path)
    accept = write_fake_accept(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("## v1\n\n- did stuff\n")
    commit_all(tmp_path)
    return accept


# ---------------------------------------------------------------------------
# report shape + overall pass
# ---------------------------------------------------------------------------

def test_report_shape_and_overall_pass(tmp_path):
    accept = base_project(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--tag", "v1", "--accept-cmd", accept_cmd],
        tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    report = load_report(tmp_path, "v1")
    assert report["tag"] == "v1"
    assert "ts" in report
    assert report["ok"] is True
    names = [c["name"] for c in report["checks"]]
    assert names == ["acceptance", "git-clean", "changelog", "env-example",
                      "secrets", "lighthouse", "security-review", "gates"]
    for c in report["checks"]:
        assert set(c.keys()) == {"name", "ok", "detail"}
        assert c["ok"] in (True, False, None)

    assert find_check(report, "acceptance")["ok"] is True
    assert find_check(report, "git-clean")["ok"] is True
    assert find_check(report, "changelog")["ok"] is True
    # lighthouse not in spec.needs
    assert find_check(report, "lighthouse")["ok"] is None
    # never confirmed
    assert find_check(report, "security-review")["ok"] is None
    # no acceptance-index.json ever written
    assert find_check(report, "gates")["ok"] is None

    # one printed line per check plus the "wrote ..." line
    assert proc.stdout.count("\n") >= len(report["checks"])


def test_security_confirmed_flag(tmp_path):
    accept = base_project(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--tag", "v1", "--accept-cmd", accept_cmd, "--security-confirmed"],
        tmp_path,
    )
    assert proc.returncode == 0
    report = load_report(tmp_path, "v1")
    assert find_check(report, "security-review") == {
        "name": "security-review", "ok": True, "detail": "confirmed by human",
    }


def test_skip_lighthouse_and_security(tmp_path):
    spec = dict(MINIMAL_SPEC)
    spec["needs"] = ["lighthouse"]
    accept = base_project(tmp_path)
    write_spec(tmp_path, spec)
    commit_all(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd, "--skip", "lighthouse,security"],
        tmp_path,
    )
    assert proc.returncode == 0
    report = load_report(tmp_path)
    assert find_check(report, "lighthouse") == {"name": "lighthouse", "ok": None, "detail": "skipped"}
    assert find_check(report, "security-review") == {
        "name": "security-review", "ok": None, "detail": "skipped",
    }


# ---------------------------------------------------------------------------
# acceptance
# ---------------------------------------------------------------------------

def test_acceptance_fails_when_a_milestone_fails_exit2(tmp_path):
    spec = dict(MINIMAL_SPEC)
    spec["milestones"] = [{"id": "m1", "title": "A", "depends_on": []},
                           {"id": "m2", "title": "B", "depends_on": []}]
    accept = base_project(tmp_path)
    write_spec(tmp_path, spec)
    (tmp_path / ".fail_milestones").write_text("m2")
    commit_all(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    report = load_report(tmp_path)
    assert report["ok"] is False
    acc = find_check(report, "acceptance")
    assert acc["ok"] is False
    assert "m2" in acc["detail"]


def test_acceptance_missing_accept_py(tmp_path, monkeypatch):
    monkeypatch.setattr(ship_check, "PIPELINE_DIR", tmp_path / "no-accept-here")
    (tmp_path / "no-accept-here").mkdir()
    spec = MINIMAL_SPEC
    result = ship_check.check_acceptance(spec, "spec.json", tmp_path, None)
    assert result == {"name": "acceptance", "ok": None, "detail": "accept.py missing"}


def test_infra_exit_code_when_accept_cmd_cannot_run(tmp_path):
    base_project(tmp_path)
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", "/no/such/binary-xyz"],
        tmp_path,
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr
    report = load_report(tmp_path)
    assert find_check(report, "acceptance")["ok"] is None
    assert "infra" in find_check(report, "acceptance")["detail"]


# ---------------------------------------------------------------------------
# git-clean
# ---------------------------------------------------------------------------

def test_git_clean_detects_dirty_tree(tmp_path):
    accept = base_project(tmp_path)
    (tmp_path / "dirty.txt").write_text("uncommitted")
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path)
    gc = find_check(report, "git-clean")
    assert gc["ok"] is False
    assert "dirty.txt" in gc["detail"]
    assert report["ok"] is False
    assert proc.returncode == 2


def test_git_clean_detects_wrong_branch(tmp_path):
    accept = base_project(tmp_path)
    git(["checkout", "-b", "feature/x"], tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path)
    gc = find_check(report, "git-clean")
    assert gc["ok"] is False
    assert "feature/x" in gc["detail"]
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# changelog
# ---------------------------------------------------------------------------

def test_changelog_missing_tag_string(tmp_path):
    accept = base_project(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--tag", "v2", "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path, "v2")
    cl = find_check(report, "changelog")
    assert cl["ok"] is False
    assert proc.returncode == 2


def test_changelog_no_tag_needs_a_heading(tmp_path):
    accept = base_project(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("just prose, no heading\n")
    accept_cmd = f"{sys.executable} {accept}"
    commit_all(tmp_path)
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path)
    assert find_check(report, "changelog")["ok"] is False
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# env-example
# ---------------------------------------------------------------------------

def test_env_example_passes_when_key_declared(tmp_path):
    accept = base_project(tmp_path)
    (tmp_path / "app.py").write_text("import os\nAPI_KEY = os.environ['API_KEY']\n")
    (tmp_path / ".env.example").write_text("API_KEY=\n")
    commit_all(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path)
    assert find_check(report, "env-example") == {
        "name": "env-example", "ok": True, "detail": "all referenced vars present in .env.example",
    }
    assert proc.returncode == 0


def test_env_example_fails_when_key_missing(tmp_path):
    accept = base_project(tmp_path)
    (tmp_path / "app.py").write_text("import os\nAPI_KEY = os.environ['API_KEY']\n")
    commit_all(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path)
    ee = find_check(report, "env-example")
    assert ee["ok"] is False
    assert "API_KEY" in ee["detail"]
    assert proc.returncode == 2


def test_env_example_trivially_passes_with_no_refs(tmp_path):
    accept = base_project(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path)
    assert find_check(report, "env-example")["ok"] is True
    assert proc.returncode == 0


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------

def test_secrets_catches_planted_aws_key(tmp_path):
    accept = base_project(tmp_path)
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJ123456"\n')
    commit_all(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    assert proc.returncode == 2
    report = load_report(tmp_path)
    sec = find_check(report, "secrets")
    assert sec["ok"] is False
    assert "config.py:1" in sec["detail"]
    assert report["ok"] is False


def test_secrets_ignores_env_files_and_shipignore(tmp_path):
    accept = base_project(tmp_path)
    (tmp_path / ".env.example").write_text('AWS_KEY = "AKIAABCDEFGHIJ123456"\n')
    (tmp_path / "ignored.py").write_text('AWS_KEY = "AKIAABCDEFGHIJ123456"\n')
    (tmp_path / ".shipignore").write_text("ignored.py\n")
    commit_all(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    assert proc.returncode == 0
    report = load_report(tmp_path)
    assert find_check(report, "secrets")["ok"] is True


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------

def test_gates_missing_index_is_null(tmp_path):
    accept = base_project(tmp_path)
    accept_cmd = f"{sys.executable} {accept}"
    proc = run_ship_check(
        ["--spec", str(tmp_path / "spec.json"), "--workdir", str(tmp_path),
         "--accept-cmd", accept_cmd],
        tmp_path,
    )
    report = load_report(tmp_path)
    gates_check = find_check(report, "gates")
    assert gates_check["ok"] is None
    assert "acceptance-index.json" in gates_check["detail"]
    assert proc.returncode == 0


def test_lighthouse_maps_spec_paths_to_served_urls(tmp_path, monkeypatch):
    """The spec names paths ("/"); the scorer audits full urls. The step must
    hand the scorer the served url and read the score back under it."""
    fake_root = tmp_path / "root"
    (fake_root / "loop" / "scorers").mkdir(parents=True)
    scorer = fake_root / "loop" / "scorers" / "lighthouse.py"
    scorer.write_text(textwrap.dedent("""\
        import json, sys
        cfg = json.loads(sys.argv[sys.argv.index("--config") + 1])
        per_url = {u: {"a11y": 95, "perf": 85} for u in cfg["urls"]}
        print(json.dumps({"ok": True, "raw": {"per_url": per_url, "urls": cfg["urls"]}}))
    """))
    monkeypatch.setattr(ship_check, "ROOT", fake_root)
    spec = dict(MINIMAL_SPEC)
    spec["needs"] = ["lighthouse"]
    spec["stack"] = {"serve": {"cmd": "true", "port": 5173}}
    spec["features"] = [{"id": "f-001", "milestone": "m1", "title": "home",
                         "acceptance": [{"type": "lighthouse", "url": "/",
                                         "min": {"accessibility": 90, "performance": 80}}]}]
    result = ship_check.check_lighthouse(spec, tmp_path, set())
    assert result == {"name": "lighthouse", "ok": True, "detail": "all minimums met"}, result
