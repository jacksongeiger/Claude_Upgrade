"""Tests for loop/init.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest loop/tests/test_init.py -q
from the repo root.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

LOOP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOOP_DIR))

import init  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def git_init(path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True)
    (path / "README.md").write_text("# demo\n")
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True)


def assess_stub(**overrides):
    base = {
        "project_dir": "/tmp/x", "slug": "x-aaaaaaaa",
        "stack": {"languages": ["python"], "package_manager": "pip", "manifests": ["requirements.txt"]},
        "tests": {"runner": "pytest", "test_cmd": "python3 -m pytest -q",
                  "coverage_cmd": None, "coverage_file": None, "coverage_tool_installed": False},
        "bench": {"present": False, "cmd": None},
        "evals": {"present": False, "dir": None},
        "llm_calls": False,
        "ui": {"present": False, "framework": None, "serve_cmd": None},
        "git": {"main_branch": "main", "remote": None, "clean": True, "is_repo": True},
        "docs": {"claude_md": True, "readme": True, "changelog": True, "dead_ends": True},
        "gaps": [],
    }
    base.update(overrides)
    return base


def fake_ok(value, raw=None):
    return {"name": "tests", "value": value, "ok": True, "error": None, "raw": raw or {}}


def fake_fail(name, error="not implemented"):
    return {"name": name, "value": 0, "ok": False, "error": error, "raw": {}}


# ---------------------------------------------------------------------------
# eps computation
# ---------------------------------------------------------------------------

def test_eps_uses_floor_when_stable(monkeypatch, tmp_path):
    calls = {"n": 0}

    def stub(name, entry, workdir, kit=init.KIT):
        calls["n"] += 1
        return {"name": name, "value": 80.0, "ok": True, "error": None, "raw": {"coverage_pct": 50}}

    monkeypatch.setattr(init, "run_scorer", stub)
    proposal = init.propose(tmp_path, assess_stub(), "", 25, 6)
    tests_entry = next(s for s in proposal["scorers"] if s["name"] == "tests")
    assert tests_entry["eps"] == 0.5  # floor: identical values -> cv 0
    assert tests_entry["runs"] == 1
    assert calls["n"] == 3


def test_eps_raises_runs_when_noisy(monkeypatch, tmp_path):
    values = iter([70.0, 90.0, 80.0])

    def stub(name, entry, workdir, kit=init.KIT):
        return {"name": name, "value": next(values), "ok": True, "error": None, "raw": {}}

    monkeypatch.setattr(init, "run_scorer", stub)
    proposal = init.propose(tmp_path, assess_stub(), "", 25, 6)
    tests_entry = next(s for s in proposal["scorers"] if s["name"] == "tests")
    # spread=20, cv=pstdev([70,90,80])=8.16, eps=max(0.5, 2*8.16)=16.3 -> spread>eps
    assert tests_entry["eps"] > 0.5
    assert tests_entry["runs"] == 3
    baseline_info = proposal["scorer_baselines"]["tests"]
    assert "raised runs to 3" in baseline_info["note"]


# ---------------------------------------------------------------------------
# refusal rule
# ---------------------------------------------------------------------------

def test_refusal_fires_with_one_scorer_and_no_keyword(monkeypatch, tmp_path):
    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(80.0) if name == "tests" else fake_fail(name)

    monkeypatch.setattr(init, "run_scorer", stub)
    assess = assess_stub(bench={"present": True, "cmd": "python3 bench.py"})
    proposal = init.propose(tmp_path, assess, "make the widgets nicer", 25, 6)
    assert proposal["refused"] is True
    assert len(proposal["scorers"]) == 1
    assert proposal["unblock"]


def test_refusal_clears_when_goal_names_the_scorer(monkeypatch, tmp_path):
    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(80.0) if name == "tests" else fake_fail(name)

    monkeypatch.setattr(init, "run_scorer", stub)
    assess = assess_stub(bench={"present": True, "cmd": "python3 bench.py"})
    proposal = init.propose(tmp_path, assess, "improve test coverage this run", 25, 6)
    assert proposal["refused"] is False
    assert len(proposal["scorers"]) == 1
    assert proposal["scorers"][0]["weight"] == 1.0  # renormalised over the one enabled scorer


def test_refusal_clears_with_two_scorers(monkeypatch, tmp_path):
    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(80.0) if name == "tests" else \
            {"name": name, "value": 60.0, "ok": True, "error": None, "raw": {}}

    monkeypatch.setattr(init, "run_scorer", stub)
    assess = assess_stub(bench={"present": True, "cmd": "python3 bench.py"})
    proposal = init.propose(tmp_path, assess, "", 25, 6)
    assert proposal["refused"] is False
    assert {s["name"] for s in proposal["scorers"]} == {"tests", "perf"}
    weights = {s["name"]: s["weight"] for s in proposal["scorers"]}
    assert abs(weights["tests"] - 0.625) < 1e-6
    assert abs(weights["perf"] - 0.375) < 1e-6


def test_cmd_propose_refusal_exit_code_and_message(monkeypatch, tmp_path, capsys):
    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(80.0) if name == "tests" else fake_fail(name)

    monkeypatch.setattr(init, "run_scorer", stub)
    assess = assess_stub(bench={"present": True, "cmd": "python3 bench.py"})
    assess_path = tmp_path / "assess.json"
    assess_path.write_text(json.dumps(assess))

    args = init.build_parser().parse_args(
        ["--propose", "--project", str(tmp_path), "--assess", str(assess_path)]
    )
    code = init.cmd_propose(args)
    out = capsys.readouterr().out
    assert code == 6
    assert "REFUSED:" in out
    assert "unblock it" in out


def test_cmd_propose_success_writes_proposal_and_preview(monkeypatch, tmp_path, capsys):
    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(80.0) if name == "tests" else \
            {"name": name, "value": 60.0, "ok": True, "error": None, "raw": {}}

    monkeypatch.setattr(init, "run_scorer", stub)
    assess = assess_stub(bench={"present": True, "cmd": "python3 bench.py"})
    assess_path = tmp_path / "assess.json"
    assess_path.write_text(json.dumps(assess))

    args = init.build_parser().parse_args(
        ["--propose", "--project", str(tmp_path), "--assess", str(assess_path), "--cap", "10", "--hours", "3"]
    )
    code = init.cmd_propose(args)
    out = capsys.readouterr().out
    assert code == 0
    assert "First pick:" in out
    assert "can ONLY improve" in out

    proposal_path = tmp_path / ".loop" / "proposed-config.json"
    assert proposal_path.exists()
    proposal = json.loads(proposal_path.read_text())
    assert proposal["cap_usd"] == 10
    assert proposal["hours"] == 3


# ---------------------------------------------------------------------------
# settings.json merge
# ---------------------------------------------------------------------------

def test_merge_settings_idempotent_and_preserves_existing(tmp_path):
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    existing = {
        "customKey": "keep-me",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo custom", "timeout": 5}]}
            ]
        },
        "permissions": {"allow": ["Bash(ls:*)"]},
    }
    settings_path.write_text(json.dumps(existing))

    config = {"test_cmd": "python3 -m pytest -q"}
    init.merge_settings(settings_path, config)
    first = json.loads(settings_path.read_text())

    assert first["customKey"] == "keep-me"
    bash_group = [g for g in first["hooks"]["PreToolUse"] if g.get("matcher") == "Bash"]
    assert bash_group and bash_group[0]["hooks"][0]["command"] == "echo custom"
    agent_group = [g for g in first["hooks"]["PreToolUse"] if g.get("matcher") == "Agent"]
    assert len(agent_group) == 1
    assert "budget-gate.sh" in agent_group[0]["hooks"][0]["command"]
    assert len(first["hooks"]["SubagentStart"]) == 1
    assert len(first["hooks"]["SubagentStart"][0]["hooks"]) == 1
    assert len(first["hooks"]["SubagentStop"][0]["hooks"]) == 1
    assert first["worktree"] == {"baseRef": "head"}
    assert first["statusLine"]["command"].endswith("statusline.sh")
    assert "Bash(ls:*)" in first["permissions"]["allow"]
    # derived from test_cmd "python3 -m pytest -q": the first word, not the whole line
    assert "Bash(python3:*)" in first["permissions"]["allow"]
    assert "Bash(rdx search:*)" in first["permissions"]["allow"]

    init.merge_settings(settings_path, config)
    second = json.loads(settings_path.read_text())
    assert second == first
    assert len(second["hooks"]["PreToolUse"]) == len(first["hooks"]["PreToolUse"])
    assert len(second["hooks"]["SubagentStart"][0]["hooks"]) == 1  # not duplicated


# ---------------------------------------------------------------------------
# backlog seeding
# ---------------------------------------------------------------------------

def test_seed_backlog_tags_refactor_todos_rung_2(tmp_path):
    (tmp_path / "mod.py").write_text(
        "# TODO refactor the ingest pipeline\n"
        "def f():\n"
        "    pass\n"
        "# TODO add more tests here\n"
    )
    proposal = {"scorers": [], "scorer_baselines": {}}
    rows = init.seed_backlog(tmp_path, proposal)
    todo_rows = [r for r in rows if r["source"] == "todo"]
    assert len(todo_rows) == 2

    refactor_row = next(r for r in todo_rows if "refactor" in r["title"].lower())
    assert refactor_row["rung"] == 2

    test_row = next(
        r for r in todo_rows if "tests" in r["title"].lower() and "refactor" not in r["title"].lower()
    )
    assert test_row["rung"] == 1
    assert test_row["dimension"] == "tests"


def test_seed_backlog_coverage_gap_least_covered_files(tmp_path):
    cov_file = tmp_path / "coverage.json"
    cov_file.write_text(json.dumps({
        "files": {
            "a.py": {"summary": {"percent_covered": 10.0}},
            "b.py": {"summary": {"percent_covered": 95.0}},
            "c.py": {"summary": {"percent_covered": 40.0}},
        }
    }))
    proposal = {
        "scorers": [{"name": "tests", "coverage_file": "coverage.json"}],
        "scorer_baselines": {"tests": {"raw": {"coverage_pct": 55.0}}},
    }
    rows = init.seed_backlog(tmp_path, proposal)
    cov_rows = [r for r in rows if r["source"] == "coverage-gap"]
    assert len(cov_rows) == 1
    assert cov_rows[0]["dimension"] == "tests"
    assert cov_rows[0]["est"] == "S"
    assert "a.py" in cov_rows[0]["title"]
    assert cov_rows[0]["title"].index("a.py") < cov_rows[0]["title"].index("b.py")


def test_seed_backlog_coverage_gap_when_below_target_even_above_90(tmp_path):
    git_init(tmp_path)
    proposal = {"scorers": [{"name": "tests", "target": 100}],
                "scorer_baselines": {"tests": {"raw": {"coverage_pct": 95.5}}}}
    rows = init.seed_backlog(tmp_path, proposal)
    gap = [r for r in rows if r["source"] == "coverage-gap"]
    assert len(gap) == 1 and gap[0]["dimension"] == "tests"


def test_seed_backlog_no_coverage_gap_row_above_90(tmp_path):
    proposal = {
        "scorers": [],
        "scorer_baselines": {"tests": {"raw": {"coverage_pct": 95.0}}},
    }
    rows = init.seed_backlog(tmp_path, proposal)
    assert not [r for r in rows if r["source"] == "coverage-gap"]


# ---------------------------------------------------------------------------
# --write end to end
# ---------------------------------------------------------------------------

def test_write_flow_end_to_end(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    project = tmp_path / "proj"
    project.mkdir()
    git_init(project)

    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(80.0) if name == "tests" else fake_fail(name)

    monkeypatch.setattr(init, "run_scorer", stub)
    assess = assess_stub(bench={"present": True, "cmd": "python3 bench.py"})
    proposal = init.propose(project, assess, "raise test coverage", 25, 6)

    proposal_path = project / ".loop" / "proposed-config.json"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(json.dumps(proposal))

    args = init.build_parser().parse_args(["--write", "--project", str(project)])
    code = init.cmd_write(args)
    assert code == 0

    ns_dir = home / ".claude" / "nightshift" / proposal["slug"]
    config = json.loads((ns_dir / "config.json").read_text())
    assert config["main_branch"] == "main"
    assert config["cap_usd"] == 25
    assert (ns_dir / "goal.md").exists()
    assert (ns_dir / "manifest.sha256").exists()
    assert (project / "GOAL.md").exists()

    agents_dir = project / ".claude" / "agents"
    assert (agents_dir / "exec-sonnet.md").exists()
    assert not (agents_dir / "ui-auditor.md").exists()  # ui disabled in this assess

    settings = json.loads((project / ".claude" / "settings.json").read_text())
    assert settings["worktree"] == {"baseRef": "head"}
    assert settings["statusLine"]["type"] == "command"

    gitignore = (project / ".gitignore").read_text()
    assert ".loop/state.json" in gitignore
    assert ".claude/worktrees/" in gitignore

    backlog_path = project / ".loop" / "backlog.yaml"
    assert backlog_path.exists()

    scores_lines = (project / ".loop" / "scores.jsonl").read_text().strip().splitlines()
    assert len(scores_lines) == 1
    row0 = json.loads(scores_lines[0])
    assert row0["iter"] == 0
    assert row0["outcome"] == "baseline"

    state = json.loads((project / ".loop" / "state.json").read_text())
    assert state["phase"] == "IDLE"
    assert state["cap_usd"] == 25
    assert state["run"] is None


def test_write_flow_enables_ui_auditor_when_ui_present(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    project = tmp_path / "proj"
    project.mkdir()
    git_init(project)

    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(80.0) if name == "tests" else fake_fail(name)

    monkeypatch.setattr(init, "run_scorer", stub)
    assess = assess_stub(ui={"present": True, "framework": "vite", "serve_cmd": "npm run dev"})
    proposal = init.propose(project, assess, "improve accessibility", 25, 6)

    proposal_path = project / ".loop" / "proposed-config.json"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(json.dumps(proposal))

    args = init.build_parser().parse_args(["--write", "--project", str(project)])
    assert init.cmd_write(args) == 0

    agents_dir = project / ".claude" / "agents"
    assert (agents_dir / "ui-auditor.md").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_guess_commands_pyproject_uses_editable_install_with_extras():
    assess_data = {
        "stack": {"languages": ["python"], "package_manager": "pip", "manifests": ["pyproject.toml"]},
        "tests": {"test_cmd": None, "pyproject_extras": ["tests"]},
    }
    setup_cmd, test_cmd = init.guess_commands(assess_data)
    assert setup_cmd == "python3 -m venv venv && ./venv/bin/pip install -e '.[tests]'"
    assert test_cmd == "./venv/bin/python -m pytest -q"


def test_guess_commands_pyproject_without_extras_and_in_subdir():
    assess_data = {
        "stack": {"languages": ["python"], "package_manager": "pip", "manifests": ["lib/pyproject.toml"]},
        "tests": {"test_cmd": None, "pyproject_extras": []},
    }
    setup_cmd, test_cmd = init.guess_commands(assess_data)
    assert setup_cmd == "cd lib && python3 -m venv venv && ./venv/bin/pip install -e ."
    assert test_cmd == "cd lib && ./venv/bin/python -m pytest -q"


def test_guess_commands_requirements_txt_still_wins():
    assess_data = {
        "stack": {"languages": ["python"], "package_manager": "pip", "manifests": ["requirements.txt", "pyproject.toml"]},
        "tests": {"test_cmd": None, "pyproject_extras": ["dev"]},
    }
    setup_cmd, _ = init.guess_commands(assess_data)
    assert setup_cmd == "python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"


def test_target_rises_above_a_baseline_already_past_the_default(monkeypatch, tmp_path):
    def stub(name, entry, workdir, kit=init.KIT):
        return fake_ok(97.8) if name == "tests" else fake_fail(name)

    monkeypatch.setattr(init, "run_scorer", stub)
    proposal = init.propose(tmp_path, assess_stub(), "raise test coverage", 25, 6)
    tests_entry = next(s for s in proposal["scorers"] if s["name"] == "tests")
    assert tests_entry["target"] == 100
    assert proposal["first_pick"]["headroom"] > 0


def test_least_covered_files_reads_istanbul_summary(tmp_path):
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "coverage-summary.json").write_text(json.dumps({
        "total": {"lines": {"total": 100, "covered": 90, "pct": 90}},
        "/abs/src/a.ts": {"lines": {"total": 10, "covered": 5, "pct": 50}},
        "/abs/src/b.ts": {"lines": {"total": 10, "covered": 10, "pct": 100}},
        "/abs/src/c.ts": {"lines": {"total": 10, "covered": 8, "pct": 80}},
    }))
    proposal = {"scorers": [{"name": "tests", "coverage_file": "coverage/coverage-summary.json"}]}
    files = init.least_covered_files(tmp_path, proposal)
    assert files[0].endswith("a.ts") and files[1].endswith("c.ts")
    assert not any(f.endswith("b.ts") for f in files)


def test_guess_commands_prefixes_submodule_init_when_gitmodules_present():
    assess_data = {
        "stack": {"languages": ["javascript"], "package_manager": "npm", "manifests": ["package.json"], "submodules": True},
        "tests": {"test_cmd": "npm test"},
    }
    setup_cmd, _ = init.guess_commands(assess_data)
    assert setup_cmd == "git submodule update --init --recursive && npm ci"


def test_in_repo_judges_get_pinned_by_default(tmp_path):
    """A bench script or an evals dir named by a scorer is a judge of the
    code; init pins it so the loop cannot edit what scores it."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_mod", LOOP_DIR / "init.py")
    init_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init_mod)
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "run.mjs").write_text("x")
    (tmp_path / "evals").mkdir()
    assess = {"tests": {"test_cmd": "pytest -q"}, "bench": {"present": True, "cmd": "node bench/run.mjs"},
              "evals": {"present": True, "dir": "evals"}, "llm_calls": True, "ui": {"present": False}}
    cands = dict(init_mod.candidate_scorers(assess, "", project_dir=tmp_path))
    assert cands["perf"]["pins"] == ["bench/run.mjs"]
    assert cands["evals"]["pins"] == ["evals/*"]
    assert "pins" not in cands["tests"]
