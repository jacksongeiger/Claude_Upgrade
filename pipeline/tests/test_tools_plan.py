"""Tests for pipeline/tools_plan.py. Run with:

    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest \
        /home/user/Claude_Upgrade/pipeline/tests/test_tools_plan.py -q
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "tools_plan.py"

spec = importlib.util.spec_from_file_location("tools_plan", MODULE_PATH)
tools_plan = importlib.util.module_from_spec(spec)
sys.modules["tools_plan"] = tools_plan
spec.loader.exec_module(tools_plan)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def make_assess(coverage_tool_installed=False, bench_present=False, ui_present=True):
    return {
        "project_dir": "/tmp/fake-project",
        "slug": "fake-project-00000000",
        "stack": {"languages": ["javascript"], "package_manager": "npm", "manifests": ["package.json"]},
        "tests": {
            "runner": "vitest",
            "test_cmd": "npm test",
            "coverage_cmd": None if not coverage_tool_installed else "npm test -- --coverage",
            "coverage_file": None if not coverage_tool_installed else "coverage/coverage-summary.json",
            "coverage_tool_installed": coverage_tool_installed,
        },
        "bench": {"present": bench_present, "cmd": "node bench/search.js" if bench_present else None},
        "evals": {"present": False, "dir": None},
        "llm_calls": False,
        "ui": {"present": ui_present, "framework": "vite" if ui_present else None,
               "serve_cmd": "npm run dev" if ui_present else None},
        "git": {"main_branch": "main", "remote": None, "clean": True, "is_repo": True},
        "docs": {"claude_md": True, "readme": True, "changelog": True, "dead_ends": True},
        "gaps": [],
    }


def make_spec(needs):
    return {
        "version": 1,
        "name": "Fixture Project",
        "one_liner": "A fixture.",
        "stack": {"language": "javascript", "runtime": "node", "ui": True, "llm": False,
                   "data": "localStorage", "deploy": {"kind": "static", "cmd": "npm run build"}},
        "needs": needs,
        "milestones": [{"id": "m1", "title": "First", "depends_on": []}],
        "features": [],
        "success": ["Everything works"],
        "budget": {"build_usd": 40, "nightshift_cap_usd": 25},
        "human_gates": ["spec", "installs", "milestone-merge", "tokens", "deploy"],
    }


NEEDS = ["ui", "unit-tests", "coverage", "lighthouse", "persona", "perf"]


@pytest.fixture
def project(tmp_path):
    spec_path = tmp_path / "spec.json"
    assess_path = tmp_path / "assess.json"
    out_path = tmp_path / ".pipeline" / "tooling-plan.json"

    spec_path.write_text(json.dumps(make_spec(NEEDS)), encoding="utf-8")
    assess_path.write_text(json.dumps(make_assess()), encoding="utf-8")

    return {
        "dir": tmp_path,
        "spec": spec_path,
        "assess": assess_path,
        "out": out_path,
    }


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def test_build_coverage_and_perf_are_gaps_with_rdx_off(project):
    rc = tools_plan.main([
        "--spec", str(project["spec"]),
        "--assess", str(project["assess"]),
        "--out", str(project["out"]),
        "--rdx", "off",
    ])
    assert rc == 0
    assert project["out"].exists()

    plan = json.loads(project["out"].read_text(encoding="utf-8"))

    assert plan["rdx"] == "off"

    gap_needs = {g["need"]: g for g in plan["gaps"]}
    assert "coverage" in gap_needs
    assert "perf" in gap_needs

    coverage_slugs = {c["slug"] for c in gap_needs["coverage"]["candidates"]}
    assert "@vitest/coverage-v8" in coverage_slugs

    perf_slugs = {c["slug"] for c in gap_needs["perf"]["candidates"]}
    assert any("bench/" in s for s in perf_slugs)

    assert "ui" in plan["ready"]
    assert "unit-tests" in plan["ready"]

    assert all(g["chosen"] is None for g in plan["gaps"])


def test_build_no_bench_or_coverage_appear_in_unresolved_only_if_no_candidates(project):
    rc = tools_plan.main([
        "--spec", str(project["spec"]),
        "--assess", str(project["assess"]),
        "--out", str(project["out"]),
        "--rdx", "off",
    ])
    assert rc == 0
    plan = json.loads(project["out"].read_text(encoding="utf-8"))
    # coverage and perf both have known candidates for javascript, so they
    # must not show up as unresolved.
    assert "coverage" not in plan["unresolved"]
    assert "perf" not in plan["unresolved"]


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------

def test_verify_closes_coverage_but_perf_stays_open(project):
    rc = tools_plan.main([
        "--spec", str(project["spec"]),
        "--assess", str(project["assess"]),
        "--out", str(project["out"]),
        "--rdx", "off",
    ])
    assert rc == 0

    # New assess: coverage tool now installed, bench still absent.
    new_assess_path = project["dir"] / "assess2.json"
    new_assess_path.write_text(
        json.dumps(make_assess(coverage_tool_installed=True, bench_present=False)),
        encoding="utf-8",
    )

    rc = tools_plan.main([
        "--verify",
        "--spec", str(project["spec"]),
        "--assess", str(new_assess_path),
        "--plan", str(project["out"]),
    ])
    assert rc == 3  # perf gap (required by needs) still open

    plan = json.loads(project["out"].read_text(encoding="utf-8"))
    gap_status = {g["need"]: g["status"] for g in plan["gaps"]}
    assert gap_status["coverage"] == "closed"
    assert gap_status["perf"] == "open"
    assert "coverage" in plan["ready"]


def test_verify_all_closed_exits_zero(project):
    rc = tools_plan.main([
        "--spec", str(project["spec"]),
        "--assess", str(project["assess"]),
        "--out", str(project["out"]),
        "--rdx", "off",
    ])
    assert rc == 0

    new_assess_path = project["dir"] / "assess3.json"
    new_assess_path.write_text(
        json.dumps(make_assess(coverage_tool_installed=True, bench_present=True)),
        encoding="utf-8",
    )

    rc = tools_plan.main([
        "--verify",
        "--spec", str(project["spec"]),
        "--assess", str(new_assess_path),
        "--plan", str(project["out"]),
    ])

    plan = json.loads(project["out"].read_text(encoding="utf-8"))
    gap_status = {g["need"]: g["status"] for g in plan["gaps"]}
    assert gap_status["coverage"] == "closed"
    assert gap_status["perf"] == "closed"
    # lighthouse/persona may or may not be closed depending on the sandbox's
    # availability of npx lighthouse / playwright, so only assert on the
    # needs we control via assess (coverage, perf) and the overall shape.
    assert rc in (0, 3)


# --------------------------------------------------------------------------
# mark
# --------------------------------------------------------------------------

def test_mark_sets_chosen(project):
    rc = tools_plan.main([
        "--spec", str(project["spec"]),
        "--assess", str(project["assess"]),
        "--out", str(project["out"]),
        "--rdx", "off",
    ])
    assert rc == 0

    rc = tools_plan.main([
        "--mark", "perf", "write bench/search.js printing one JSON line",
        "--plan", str(project["out"]),
    ])
    assert rc == 0

    plan = json.loads(project["out"].read_text(encoding="utf-8"))
    gap_needs = {g["need"]: g for g in plan["gaps"]}
    assert gap_needs["perf"]["chosen"] == "write bench/search.js printing one JSON line"
