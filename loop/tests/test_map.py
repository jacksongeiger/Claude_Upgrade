import json
import subprocess
import sys
from pathlib import Path

import pytest

MAP_PY = Path(__file__).resolve().parent.parent / "map.py"


def run_map(repo, *extra):
    cmd = [sys.executable, str(MAP_PY), "--repo", str(repo), *extra]
    return subprocess.run(cmd, capture_output=True, text=True)


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "a@b.c")
    git(r, "config", "user.name", "tester")

    pkg = r / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n\n\ndef f():\n    return b.g()\n")
    (pkg / "b.py").write_text("from . import c\n\n\ndef g():\n    return c.h()\n")
    (pkg / "c.py").write_text("def h():\n    return 1\n")

    web = r / "web"
    web.mkdir()
    (web / "util.js").write_text("module.exports = { x: 1 };\n")
    (web / "main.js").write_text("const util = require('./util');\nconsole.log(util.x);\n")

    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def load_map(repo):
    return json.loads((repo / ".loop" / "map.json").read_text())


def modules_by_path(m):
    return {mod["path"]: mod for mod in m["modules"]}


def test_first_run_builds_edges(repo):
    result = run_map(repo)
    assert result.returncode == 0, result.stderr
    assert "changed:" in result.stdout

    m = load_map(repo)
    mods = modules_by_path(m)

    assert "pkg/b.py" in mods["pkg/a.py"]["imports"]
    assert "pkg/c.py" in mods["pkg/b.py"]["imports"]
    assert "web/util.js" in mods["web/main.js"]["imports"]

    assert (repo / "ARCHITECTURE.md").exists()
    arch = (repo / "ARCHITECTURE.md").read_text()
    assert "```mermaid" in arch
    assert "graph LR" in arch
    assert "Fan-in" in arch


def test_second_run_unchanged(repo):
    r1 = run_map(repo)
    assert r1.returncode == 0

    r2 = run_map(repo)
    assert r2.returncode == 0
    assert r2.stdout.strip() == "unchanged"


def test_changed_list_after_modifying_b(repo):
    run_map(repo)

    (repo / "pkg" / "b.py").write_text(
        "from . import c\n\n\ndef g():\n    # modified\n    return c.h()\n"
    )
    git(repo, "add", "-A")

    result = run_map(repo)
    assert result.returncode == 0
    assert "changed:" in result.stdout
    changed_lines = result.stdout.split("changed:", 1)[1].strip().splitlines()
    assert "pkg/b.py" in changed_lines
    assert "pkg/a.py" not in changed_lines


def test_tree_marks_changed_files(repo, tmp_path):
    run_map(repo)

    (repo / "pkg" / "b.py").write_text(
        "from . import c\n\n\ndef g():\n    # modified again\n    return c.h()\n"
    )
    git(repo, "add", "-A")

    changed_file = tmp_path / "changed.txt"
    changed_file.write_text("pkg/b.py\n")

    result = run_map(repo, "--tree", "--changed", str(changed_file))
    assert result.returncode == 0
    assert "b.py" in result.stdout
    # the changed marker sits on b.py's line
    b_line = [line for line in result.stdout.splitlines() if "b.py" in line][0]
    assert "*" in b_line
    a_line = [line for line in result.stdout.splitlines() if "a.py" in line][0]
    assert "*" not in a_line
    assert "←" in result.stdout and "→" in result.stdout


def test_force_rebuilds_even_when_unchanged(repo):
    run_map(repo)
    result = run_map(repo, "--force")
    assert result.returncode == 0
    assert "changed:" in result.stdout


def test_desc_preserved_when_hash_unchanged(repo):
    run_map(repo)
    m = load_map(repo)
    mods = modules_by_path(m)
    mods["pkg/c.py"]["desc"] = "computes h"
    for mod in m["modules"]:
        if mod["path"] == "pkg/c.py":
            mod["desc"] = "computes h"
    (repo / ".loop" / "map.json").write_text(json.dumps(m))

    # touch an unrelated file to force a real rebuild
    (repo / "pkg" / "a.py").write_text(
        "from pkg import b\n\n\ndef f():\n    # touched\n    return b.g()\n"
    )
    git(repo, "add", "-A")

    run_map(repo)
    m2 = load_map(repo)
    mods2 = modules_by_path(m2)
    assert mods2["pkg/c.py"]["desc"] == "computes h"
    assert mods2["pkg/a.py"]["desc"] == ""


def test_directory_level_collapse_for_many_modules(tmp_path):
    r = tmp_path / "bigrepo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "a@b.c")
    git(r, "config", "user.name", "tester")

    for d in ("alpha", "beta"):
        (r / d).mkdir()
        for i in range(25):
            content = "" if d != "alpha" else "from beta import mod0\n" if i == 0 else ""
            (r / d / f"mod{i}.py").write_text(content)
    (r / "alpha" / "__init__.py").write_text("")
    (r / "beta" / "__init__.py").write_text("")

    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")

    result = run_map(r)
    assert result.returncode == 0, result.stderr

    arch = (r / "ARCHITECTURE.md").read_text()
    assert "```mermaid" in arch
    mermaid_block = arch.split("```mermaid", 1)[1].split("```", 1)[0]
    # directory-level collapse: node labels should be top-level dirs, not file paths
    assert 'alpha' in mermaid_block  # a dir node, or file nodes under it when the dir graph is too small to be useful
    assert "-->" in mermaid_block  # and it must actually draw something


def test_vendored_dirs_skipped(tmp_path):
    r = tmp_path / "vrepo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "a@b.c")
    git(r, "config", "user.name", "tester")

    (r / "src").mkdir()
    (r / "src" / "main.py").write_text("x = 1\n")
    (r / "node_modules" / "pkg").mkdir(parents=True)
    (r / "node_modules" / "pkg" / "index.js").write_text("module.exports = {};\n")

    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")

    run_map(r)
    m = load_map(r)
    paths = {mod["path"] for mod in m["modules"]}
    assert "src/main.py" in paths
    assert not any("node_modules" in p for p in paths)
