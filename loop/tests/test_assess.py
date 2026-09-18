"""Tests for loop/assess.py.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest loop/tests/test_assess.py -q
from the repo root.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

ASSESS_PATH = os.path.join(os.path.dirname(__file__), "..", "assess.py")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assess  # noqa: E402


def run_assess(project_dir):
    proc = subprocess.run(
        [sys.executable, ASSESS_PATH, "--project", str(project_dir)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, "expected exactly one JSON line, got:\n%s" % proc.stdout
    return json.loads(lines[0])


def git_init(path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True)


TOP_KEYS = {
    "project_dir", "slug", "stack", "tests", "bench", "evals",
    "llm_calls", "ui", "git", "docs", "gaps",
}


# --------------------------------------------------------------------------
# empty directory
# --------------------------------------------------------------------------

def test_empty_dir_never_crashes_and_has_all_keys(tmp_path):
    out = run_assess(tmp_path)
    assert TOP_KEYS <= set(out.keys())
    assert out["project_dir"] == os.path.realpath(str(tmp_path))
    assert out["stack"]["languages"] == []
    assert out["stack"]["package_manager"] is None
    assert out["stack"]["manifests"] == []
    assert out["tests"]["runner"] == "none"
    assert out["tests"]["test_cmd"] is None
    assert out["bench"]["present"] is False
    assert out["evals"]["present"] is False
    assert out["llm_calls"] is False
    assert out["ui"]["present"] is False
    assert out["git"]["is_repo"] is False
    assert out["git"]["main_branch"] is None
    assert out["docs"] == {
        "claude_md": False, "readme": False, "changelog": False, "dead_ends": False,
    }
    assert "no benchmark script" in out["gaps"]
    assert "missing README.md" in out["gaps"]


def test_slug_format(tmp_path):
    out = run_assess(tmp_path)
    base = os.path.basename(os.path.realpath(str(tmp_path)))
    assert out["slug"].startswith(base + "-")
    suffix = out["slug"][len(base) + 1:]
    assert len(suffix) == 8
    int(suffix, 16)  # must be hex


# --------------------------------------------------------------------------
# python project, no venv
# --------------------------------------------------------------------------

def make_python_project(root):
    (root / "requirements.txt").write_text("pytest==8.3.4\n")
    pkg = root / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("def add(a, b):\n    return a + b\n")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        textwrap.dedent(
            """
            from mypkg.core import add

            def test_add():
                assert add(1, 2) == 3
            """
        )
    )


def test_python_project_no_venv(tmp_path):
    make_python_project(tmp_path)
    out = run_assess(tmp_path)

    assert out["stack"]["languages"] == ["python"]
    assert out["stack"]["package_manager"] == "pip"
    assert out["stack"]["manifests"] == ["requirements.txt"]

    assert out["tests"]["runner"] == "pytest"
    # no venv yet, but setup_cmd creates ./venv and every loop command uses it
    assert out["tests"]["test_cmd"] == "./venv/bin/python -m pytest -q"
    # nothing declares pytest-cov -> we never claim a coverage tool is available
    assert out["tests"]["coverage_cmd"] is None
    assert out["tests"]["coverage_tool_installed"] is False
    assert any("no coverage tool for pytest" in g for g in out["gaps"])

    assert out["llm_calls"] is False
    assert out["ui"]["present"] is False


def test_python_project_with_git_repo_reports_clean(tmp_path):
    make_python_project(tmp_path)
    git_init(tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)

    out = run_assess(tmp_path)
    assert out["git"]["is_repo"] is True
    assert out["git"]["clean"] is True
    assert out["git"]["main_branch"] in ("main", "master")

    # dirty the tree
    (tmp_path / "scratch.txt").write_text("x")
    out2 = run_assess(tmp_path)
    assert out2["git"]["clean"] is False


def test_python_project_with_llm_call(tmp_path):
    make_python_project(tmp_path)
    (tmp_path / "mypkg" / "client.py").write_text(
        "import anthropic\n\ndef call():\n    return anthropic.Anthropic()\n"
    )
    out = run_assess(tmp_path)
    assert out["llm_calls"] is True
    assert "no eval cases" in out["gaps"]

    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    (evals_dir / "case1.yaml").write_text("prompt: hi\n")
    out2 = run_assess(tmp_path)
    assert out2["evals"]["present"] is True
    assert out2["evals"]["dir"] == "evals"
    assert "no eval cases" not in out2["gaps"]


def test_python_project_with_venv_shape(tmp_path):
    # Build a fake venv (no real interpreter needed for the layout check,
    # but find_venv() also requires bin/python to exist).
    make_python_project(tmp_path)
    venv_dir = tmp_path / "venv" / "bin"
    venv_dir.mkdir(parents=True)
    (venv_dir / "python").write_text("#!/bin/sh\nexit 1\n")
    os.chmod(venv_dir / "python", 0o755)

    out = run_assess(tmp_path)
    assert out["tests"]["test_cmd"] == "./venv/bin/python -m pytest -q"


def test_python_project_in_subdir_is_detected(tmp_path):
    sub = tmp_path / "backend"
    sub.mkdir()
    make_python_project(sub)

    out = run_assess(tmp_path)
    assert out["stack"]["languages"] == ["python"]
    assert out["stack"]["manifests"] == ["backend/requirements.txt"]
    assert out["tests"]["test_cmd"] == "cd backend && ./venv/bin/python -m pytest -q"


# --------------------------------------------------------------------------
# node project
# --------------------------------------------------------------------------

def test_node_project_vitest(tmp_path):
    package_json = {
        "name": "demo",
        "scripts": {"test": "vitest run", "dev": "vite"},
        "devDependencies": {"vitest": "4.0.0", "@vitest/coverage-v8": "4.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(package_json))
    (tmp_path / "vite.config.js").write_text("export default {}\n")

    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "vitest").write_text("#!/bin/sh\n")

    out = run_assess(tmp_path)
    assert out["stack"]["languages"] == ["javascript"]
    assert out["stack"]["package_manager"] == "npm"
    assert out["stack"]["manifests"] == ["package.json"]

    assert out["tests"]["runner"] == "vitest"
    assert out["tests"]["test_cmd"] == "npm test"
    assert out["tests"]["coverage_tool_installed"] is True
    # vitest flag shape, not jest's
    assert out["tests"]["coverage_cmd"] == "npm test -- --coverage --coverage.reporter=json-summary"
    assert out["tests"]["coverage_file"] == "coverage/coverage-summary.json"

    assert out["ui"]["present"] is True
    assert out["ui"]["framework"] == "vite"
    assert out["ui"]["serve_cmd"] == "npm run dev"


def test_node_project_jest_no_coverage_tool(tmp_path):
    package_json = {"name": "demo2", "scripts": {"test": "jest"}}
    (tmp_path / "package.json").write_text(json.dumps(package_json))

    out = run_assess(tmp_path)
    assert out["tests"]["runner"] == "jest"
    assert out["tests"]["coverage_tool_installed"] is False
    assert out["tests"]["coverage_cmd"] is None
    assert any("no coverage tool for jest" in g for g in out["gaps"])


def test_node_project_yarn_lock_detected(tmp_path):
    package_json = {"name": "demo3", "scripts": {"test": "jest"}}
    (tmp_path / "package.json").write_text(json.dumps(package_json))
    (tmp_path / "yarn.lock").write_text("")

    out = run_assess(tmp_path)
    assert out["stack"]["package_manager"] == "yarn"
    assert out["tests"]["test_cmd"] == "yarn test"


# --------------------------------------------------------------------------
# bench / docs
# --------------------------------------------------------------------------

def test_bench_dir_detected(tmp_path):
    make_python_project(tmp_path)
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir()
    (bench_dir / "run_bench.py").write_text("print('bench')\n")

    out = run_assess(tmp_path)
    assert out["bench"]["present"] is True
    assert out["bench"]["cmd"] == "python3 bench/run_bench.py"
    assert "no benchmark script" not in out["gaps"]


def test_docs_all_present_no_doc_gaps(tmp_path):
    for name in ("CLAUDE.md", "README.md", "CHANGELOG.md", "DEAD_ENDS.md"):
        (tmp_path / name).write_text("# doc\n")

    out = run_assess(tmp_path)
    assert out["docs"] == {
        "claude_md": True, "readme": True, "changelog": True, "dead_ends": True,
    }
    assert not any(g.startswith("missing ") for g in out["gaps"])


# --------------------------------------------------------------------------
# never crashes on garbage input
# --------------------------------------------------------------------------

def test_broken_package_json_does_not_crash(tmp_path):
    (tmp_path / "package.json").write_text("{not valid json")
    out = run_assess(tmp_path)
    assert "javascript" in out["stack"]["languages"]
    assert out["tests"]["runner"] == "none"


def test_nonexistent_project_dir_does_not_crash(tmp_path):
    ghost = tmp_path / "does-not-exist"
    out = run_assess(ghost)
    assert TOP_KEYS <= set(out.keys())
    assert isinstance(out["gaps"], list)


# --------------------------------------------------------------------------
# in-process unit tests of small helpers (no subprocess overhead)
# --------------------------------------------------------------------------

def test_compute_slug_is_deterministic():
    a = assess.compute_slug("/home/user/Claude_Upgrade")
    b = assess.compute_slug("/home/user/Claude_Upgrade")
    assert a == b
    assert a.startswith("Claude_Upgrade-")
    assert len(a.split("-")[-1]) == 8


def test_manifest_depth():
    assert assess.manifest_depth("requirements.txt") == 0
    assert assess.manifest_depth("discovery/requirements.txt") == 1
    assert assess.manifest_depth("a/b/c/setup.py") == 3


def test_pick_primary_language_prefers_shallowest():
    manifests = {
        "python": ["backend/requirements.txt"],
        "javascript": ["package.json"],
        "go": [],
        "rust": [],
        "swift": [],
    }
    lang, rep = assess.pick_primary_language(manifests)
    assert lang == "javascript"
    assert rep == "package.json"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


def test_pyproject_src_layout_with_declared_pytest_cov(tmp_path):
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent("""
        [project]
        name = "shiny"
        version = "0.1"
        optional-dependencies.tests = ["pytest>=9", "pytest-cov"]
        [tool.pytest]
        testpaths = ["tests"]
    """))
    pkg = tmp_path / "src" / "shiny"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n")
    out = run_assess(tmp_path)
    t = out["tests"]
    assert t["runner"] == "pytest"
    assert t["test_cmd"] == "./venv/bin/python -m pytest -q"
    assert t["pyproject_extras"] == ["tests"]
    # pytest-cov is declared, so the coverage command exists and targets the src package
    assert t["coverage_tool_installed"] is True
    assert t["coverage_cmd"] == "./venv/bin/python -m pytest -q --cov=shiny --cov-report=json:.loop/run/coverage.json"
    assert not any("no coverage tool" in g for g in out["gaps"])


def test_node_project_vitest_without_provider_is_a_gap(tmp_path):
    package_json = {"name": "demo4", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "4.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(package_json))
    out = run_assess(tmp_path)
    assert out["tests"]["runner"] == "vitest"
    assert out["tests"]["coverage_tool_installed"] is False
    assert any("@vitest/coverage-v8" in g for g in out["gaps"])


def test_node_project_jest_declared_counts_before_install(tmp_path):
    package_json = {"name": "demo5", "scripts": {"test": "jest"}, "devDependencies": {"jest": "30.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(package_json))
    out = run_assess(tmp_path)
    assert out["tests"]["coverage_tool_installed"] is True
    assert out["tests"]["coverage_cmd"] == "npm test -- --coverage --coverageReporters=json-summary"


def test_python_coverage_target_is_src_or_a_package_never_a_hyphenated_folder(tmp_path):
    """2026-09-18: a project named inbox-triage with a flat src/ got --cov=inbox-triage, which measures nothing."""
    import assess
    proj = tmp_path / "inbox-triage"; (proj / "src").mkdir(parents=True)
    (proj / "src" / "main.py").write_text("x = 1\n")
    assert assess.find_python_package(str(proj), "") == "src"
    pkg = tmp_path / "other-thing"; (pkg / "thing").mkdir(parents=True); (pkg / "thing" / "__init__.py").write_text("")
    assert assess.find_python_package(str(pkg), "") == "thing"
    bare = tmp_path / "bare-name"; bare.mkdir()
    assert assess.find_python_package(str(bare), "") == "."
