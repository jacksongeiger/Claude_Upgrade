#!/usr/bin/env python3
"""Nightshift stack/tests/bench/evals/UI detector.

`python3 assess.py [--project DIR]` prints exactly one JSON object describing
the project at DIR (default: cwd) and exits 0. See loop/README.md for the
contract this implements. Python 3.9+, stdlib only, must never crash: any
detection failure is swallowed and turned into a `gaps` entry.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {
    "venv", ".venv", "node_modules", ".git", ".loop", "__pycache__",
    "dist", "build", "target", ".pytest_cache", ".mypy_cache", ".tox",
    "coverage", ".next", ".nuxt", "vendor",
}

LANG_PRIORITY = ["python", "javascript", "go", "rust", "swift"]

# An IMPORT or client construction, not a bare mention: the kit's own rdx
# package names "anthropic" in marketplace URLs and was detected as an LLM
# product. A project calls a model when it imports a client library.
LLM_KEYWORDS = re.compile(
    r"^\s*(?:import\s+(?:anthropic|openai|litellm|google\.generativeai)\b"
    r"|from\s+(?:anthropic|openai|litellm|google\.generativeai|google)\s+import\b"
    r"|(?:import|require)\s*\(?\s*['\"](?:@anthropic-ai/sdk|openai|@google/generative-ai)['\"])"
    r"|\b(?:Anthropic|OpenAI|AsyncAnthropic|AsyncOpenAI)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)

SOURCE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".swift", ".rb", ".java",
}

DOC_FILES = ["CLAUDE.md", "README.md", "CHANGELOG.md", "DEAD_ENDS.md"]


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------

def walk_limited(root, max_depth=3, skip=SKIP_DIRS):
    """Yield (dirpath, dirnames, filenames) up to max_depth below root,
    pruning skip dirs. Depth 0 is root itself."""
    root = str(root)
    root_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        if depth >= max_depth:
            dirnames[:] = []
        yield dirpath, dirnames, filenames


def rel(root, path):
    try:
        return os.path.relpath(str(path), str(root))
    except Exception:
        return str(path)


def run_cmd(args, cwd=None, timeout=10):
    try:
        return subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except Exception:
        return None


def interpreter_can_import(interpreter, module):
    proc = run_cmd([interpreter, "-c", "import %s" % module])
    return bool(proc is not None and proc.returncode == 0)


def which(name):
    from shutil import which as _which
    return _which(name)


# --------------------------------------------------------------------------
# manifest discovery
# --------------------------------------------------------------------------

def find_manifests(root):
    """Return {lang: [relative manifest paths]} found up to depth 3."""
    found = {"python": [], "javascript": [], "go": [], "rust": [], "swift": []}
    for dirpath, dirnames, filenames in walk_limited(root, max_depth=3):
        fset = set(filenames)
        for fn in fset:
            if fn == "pyproject.toml" or fn == "setup.py" or re.match(r"^requirements.*\.txt$", fn):
                found["python"].append(rel(root, os.path.join(dirpath, fn)))
            elif fn == "package.json":
                found["javascript"].append(rel(root, os.path.join(dirpath, fn)))
            elif fn == "go.mod":
                found["go"].append(rel(root, os.path.join(dirpath, fn)))
            elif fn == "Cargo.toml":
                found["rust"].append(rel(root, os.path.join(dirpath, fn)))
            elif fn == "Package.swift":
                found["swift"].append(rel(root, os.path.join(dirpath, fn)))
    return found


def manifest_depth(relpath):
    return relpath.replace("\\", "/").count("/")


def pick_representative(paths):
    """Shallowest manifest wins; tie-break alphabetically."""
    return sorted(paths, key=lambda p: (manifest_depth(p), p))[0]


def pick_primary_language(manifests_by_lang):
    candidates = []
    for lang in LANG_PRIORITY:
        paths = manifests_by_lang.get(lang) or []
        if not paths:
            continue
        rep = pick_representative(paths)
        candidates.append((manifest_depth(rep), LANG_PRIORITY.index(lang), lang, rep))
    if not candidates:
        return None, None
    candidates.sort()
    _, _, lang, rep = candidates[0]
    return lang, rep


# --------------------------------------------------------------------------
# per-language test/coverage detection
# --------------------------------------------------------------------------

def manifest_dirname(root, manifest_rel):
    d = os.path.dirname(manifest_rel)
    return d  # "" means project root


def has_tests_evidence(root, manifest_dir_abs):
    """tests/ dir, or any test_*.py / *_test.py file, under manifest_dir_abs
    (bounded scan, skipping the usual noise dirs)."""
    for dirpath, dirnames, filenames in walk_limited(manifest_dir_abs, max_depth=3):
        base = os.path.basename(dirpath)
        if base in ("tests", "test") and dirpath != manifest_dir_abs:
            return True
        for fn in filenames:
            if re.match(r"^test_.*\.py$", fn) or re.match(r".*_test\.py$", fn):
                return True
    # also check the manifest dir itself for a tests/ sibling
    for name in ("tests", "test"):
        if os.path.isdir(os.path.join(manifest_dir_abs, name)):
            return True
    return False


def find_venv(manifest_dir_abs):
    for name in ("venv", ".venv"):
        cand = os.path.join(manifest_dir_abs, name)
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "bin", "python")):
            return name
    return None


def manifest_declares(manifest_dir_abs, needles):
    """True when pyproject.toml / setup.py / requirements*.txt name one of
    `needles` — a dev tool the setup command will install even though it is
    not importable yet."""
    try:
        for fn in os.listdir(manifest_dir_abs):
            if fn in ("pyproject.toml", "setup.py", "setup.cfg") or re.match(r"^requirements.*\.txt$", fn):
                text = open(os.path.join(manifest_dir_abs, fn), encoding="utf-8", errors="replace").read()
                if any(n in text for n in needles):
                    return True
    except OSError:
        pass
    return False


def pyproject_test_extras(manifest_dir_abs):
    """Names of [project.optional-dependencies] groups that look like test/dev
    groups, e.g. ["tests"]; [] when none or no pyproject."""
    path = os.path.join(manifest_dir_abs, "pyproject.toml")
    if not os.path.isfile(path):
        return []
    try:
        import tomllib
        with open(path, "rb") as f:
            data = tomllib.load(f)
        groups = (data.get("project") or {}).get("optional-dependencies") or {}
    except Exception:
        return []
    wanted = ("test", "tests", "testing", "dev", "develop", "development")
    return [g for g in groups if g.lower() in wanted]


def find_python_package(manifest_dir_abs, manifest_dir_rel):
    """Best-effort package name for --cov=<pkg>: first subdir with __init__.py
    (src layout first: src/<pkg>)."""
    try:
        src = os.path.join(manifest_dir_abs, "src")
        if os.path.isdir(src):
            for entry in sorted(os.listdir(src)):
                if os.path.isfile(os.path.join(src, entry, "__init__.py")):
                    return entry
        for entry in sorted(os.listdir(manifest_dir_abs)):
            if entry in SKIP_DIRS or entry.startswith("."):
                continue
            full = os.path.join(manifest_dir_abs, entry)
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                return entry
    except Exception:
        pass
    base = os.path.basename(manifest_dir_rel) if manifest_dir_rel else os.path.basename(manifest_dir_abs)
    return base or "."


def detect_python_tests(root, manifest_rel, gaps):
    manifest_dir_rel = manifest_dirname(root, manifest_rel)
    manifest_dir_abs = os.path.join(root, manifest_dir_rel) if manifest_dir_rel else root
    cd_prefix = "cd %s && " % manifest_dir_rel if manifest_dir_rel else ""

    venv_name = find_venv(manifest_dir_abs)
    if venv_name:
        interpreter = os.path.join(manifest_dir_abs, venv_name, "bin", "python")
        python_bin = "./%s/bin/python" % venv_name
    else:
        # No venv yet: the proposed setup_cmd creates ./venv, and every command
        # the loop runs (tests, coverage, executors' acceptance) must use that
        # interpreter, not whatever python3 is on PATH.
        interpreter = "python3"
        python_bin = "./venv/bin/python"

    pytest_importable = interpreter_can_import(interpreter, "pytest")
    tests_evidence = has_tests_evidence(root, manifest_dir_abs)

    if tests_evidence or pytest_importable:
        runner = "pytest"
    else:
        # fall back: any test*.py anywhere under the manifest dir at all
        runner = "none"
        for dirpath, dirnames, filenames in walk_limited(manifest_dir_abs, max_depth=3):
            if any(re.match(r"^test.*\.py$", fn) for fn in filenames):
                runner = "unittest"
                break

    if runner == "none":
        return {
            "runner": "none", "test_cmd": None, "coverage_cmd": None,
            "coverage_file": None, "coverage_tool_installed": False,
        }

    if runner == "pytest":
        test_cmd = "%s%s -m pytest -q" % (cd_prefix, python_bin)
    else:
        test_cmd = "%s%s -m unittest discover -q" % (cd_prefix, python_bin)

    coverage_tool_installed = interpreter_can_import(interpreter, "pytest_cov") or \
        manifest_declares(manifest_dir_abs, ("pytest-cov", "pytest_cov"))

    coverage_cmd = None
    coverage_file = None
    if runner == "pytest" and coverage_tool_installed:
        pkg = find_python_package(manifest_dir_abs, manifest_dir_rel)
        coverage_file = ".loop/run/coverage.json"
        # The command runs after `cd <manifest dir>`, so the report path inside
        # it must climb back to the project root — otherwise pytest-cov writes
        # discovery/.loop/run/coverage.json and the scorer, reading relative to
        # the root, finds nothing and silently scores without coverage.
        if manifest_dir_rel in ("", "."):
            report_path = coverage_file
        else:
            report_path = os.path.normpath(os.path.join(
                os.path.relpath(".", manifest_dir_rel), coverage_file))
        coverage_cmd = "%s%s -m pytest -q --cov=%s --cov-report=json:%s" % (
            cd_prefix, python_bin, pkg, report_path,
        )
    elif runner == "pytest":
        gaps.append("no coverage tool for pytest (pip install pytest-cov)")

    return {
        "runner": runner, "test_cmd": test_cmd, "coverage_cmd": coverage_cmd,
        "coverage_file": coverage_file, "coverage_tool_installed": coverage_tool_installed,
        "pyproject_extras": pyproject_test_extras(manifest_dir_abs),
    }


def detect_node_tests(root, manifest_rel, gaps):
    manifest_dir_rel = manifest_dirname(root, manifest_rel)
    manifest_dir_abs = os.path.join(root, manifest_dir_rel) if manifest_dir_rel else root
    cd_prefix = "cd %s && " % manifest_dir_rel if manifest_dir_rel else ""

    try:
        with open(os.path.join(manifest_dir_abs, "package.json"), "r", encoding="utf-8") as fh:
            pkg_json = json.load(fh)
    except Exception:
        pkg_json = {}
    scripts = pkg_json.get("scripts") or {}
    test_script = scripts.get("test") or ""

    bin_dir = os.path.join(manifest_dir_abs, "node_modules", ".bin")
    has_bin = lambda name: os.path.isfile(os.path.join(bin_dir, name))

    if re.search(r"vitest", test_script, re.IGNORECASE):
        runner = "vitest"
    elif re.search(r"jest", test_script, re.IGNORECASE):
        runner = "jest"
    elif has_bin("vitest"):
        runner = "vitest"
    elif has_bin("jest"):
        runner = "jest"
    elif test_script:
        runner = "jest"
    else:
        runner = "none"

    if runner == "none":
        return {
            "runner": "none", "test_cmd": None, "coverage_cmd": None,
            "coverage_file": None, "coverage_tool_installed": False,
        }

    if os.path.isfile(os.path.join(manifest_dir_abs, "pnpm-lock.yaml")):
        pm_run = "pnpm test"
    elif os.path.isfile(os.path.join(manifest_dir_abs, "yarn.lock")):
        pm_run = "yarn test"
    else:
        pm_run = "npm test"

    test_cmd = "%s%s" % (cd_prefix, pm_run)

    # A coverage tool counts when it is DECLARED (node_modules may not exist
    # before setup_cmd runs). jest brings its own; vitest needs a provider
    # package; anything else needs c8/nyc.
    dev = dict(pkg_json.get("devDependencies") or {})
    dev.update(pkg_json.get("dependencies") or {})
    if runner == "vitest":
        coverage_tool_installed = any(k.startswith("@vitest/coverage-") for k in dev) or has_bin("c8")
        coverage_flags = "--coverage --coverage.reporter=json-summary"
    elif runner == "jest":
        coverage_tool_installed = "jest" in dev or has_bin("jest")
        coverage_flags = "--coverage --coverageReporters=json-summary"
    else:
        coverage_tool_installed = "c8" in dev or "nyc" in dev or has_bin("c8")
        coverage_flags = "--coverage --coverageReporters=json-summary"
    coverage_cmd = None
    coverage_file = None
    if coverage_tool_installed:
        coverage_cmd = "%s -- %s" % (test_cmd, coverage_flags)
        coverage_file = "coverage/coverage-summary.json"
    else:
        gaps.append("no coverage tool for %s (%s)" % (
            runner, "add @vitest/coverage-v8 as a pinned devDependency" if runner == "vitest"
            else "add c8 or nyc as a pinned devDependency"))

    return {
        "runner": runner, "test_cmd": test_cmd, "coverage_cmd": coverage_cmd,
        "coverage_file": coverage_file, "coverage_tool_installed": coverage_tool_installed,
    }


def detect_go_tests(root, manifest_rel, gaps):
    manifest_dir_rel = manifest_dirname(root, manifest_rel)
    cd_prefix = "cd %s && " % manifest_dir_rel if manifest_dir_rel else ""
    go_installed = which("go") is not None
    test_cmd = "%sgo test ./..." % cd_prefix
    coverage_file = ".loop/run/cover.out"
    coverage_cmd = "%s -coverprofile=%s" % (test_cmd, coverage_file)
    if not go_installed:
        gaps.append("go toolchain not installed")
        coverage_cmd = None
        coverage_file = None
    return {
        "runner": "go", "test_cmd": test_cmd, "coverage_cmd": coverage_cmd,
        "coverage_file": coverage_file, "coverage_tool_installed": go_installed,
    }


def detect_cargo_tests(root, manifest_rel, gaps):
    manifest_dir_rel = manifest_dirname(root, manifest_rel)
    cd_prefix = "cd %s && " % manifest_dir_rel if manifest_dir_rel else ""
    test_cmd = "%scargo test" % cd_prefix
    gaps.append("no coverage tool for cargo")
    return {
        "runner": "cargo", "test_cmd": test_cmd, "coverage_cmd": None,
        "coverage_file": None, "coverage_tool_installed": False,
    }


def detect_swift_tests(root, manifest_rel, gaps):
    manifest_dir_rel = manifest_dirname(root, manifest_rel)
    cd_prefix = "cd %s && " % manifest_dir_rel if manifest_dir_rel else ""
    test_cmd = "%sswift test" % cd_prefix
    gaps.append("no coverage tool for swift")
    return {
        "runner": "swift", "test_cmd": test_cmd, "coverage_cmd": None,
        "coverage_file": None, "coverage_tool_installed": False,
    }


# --------------------------------------------------------------------------
# package manager
# --------------------------------------------------------------------------

def detect_package_manager(root, lang, manifest_rel):
    if lang is None:
        return None
    manifest_dir_rel = manifest_dirname(root, manifest_rel)
    manifest_dir_abs = os.path.join(root, manifest_dir_rel) if manifest_dir_rel else root
    if lang == "python":
        if os.path.isfile(os.path.join(manifest_dir_abs, "uv.lock")):
            return "uv"
        return "pip"
    if lang == "javascript":
        if os.path.isfile(os.path.join(manifest_dir_abs, "pnpm-lock.yaml")):
            return "pnpm"
        if os.path.isfile(os.path.join(manifest_dir_abs, "yarn.lock")):
            return "yarn"
        return "npm"
    if lang == "go":
        return "go"
    if lang == "rust":
        return "cargo"
    if lang == "swift":
        return "swift"
    return None


# --------------------------------------------------------------------------
# bench / evals / llm_calls / ui
# --------------------------------------------------------------------------

def detect_bench(root):
    for name in ("bench", "benchmarks", "perf"):
        cand = os.path.join(root, name)
        if os.path.isdir(cand):
            # guess a runnable entry
            for dirpath, dirnames, filenames in walk_limited(cand, max_depth=2):
                for fn in sorted(filenames):
                    if fn.endswith(".py"):
                        return True, "python3 %s" % rel(root, os.path.join(dirpath, fn))
                    if fn.endswith((".js", ".ts", ".mjs")):
                        return True, "node %s" % rel(root, os.path.join(dirpath, fn))
            return True, None

    pkg_path = os.path.join(root, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if "bench" in (data.get("scripts") or {}):
                return True, "npm run bench"
        except Exception:
            pass

    for dirpath, dirnames, filenames in walk_limited(root, max_depth=3):
        for fn in filenames:
            low = fn.lower()
            if "bench" in low and (fn.endswith(".py") or fn.endswith(".ts") or fn.endswith(".js")):
                full = os.path.join(dirpath, fn)
                cmd = "python3 %s" % rel(root, full) if fn.endswith(".py") else "node %s" % rel(root, full)
                return True, cmd

    return False, None


def detect_evals(root):
    for name in ("evals", "eval"):
        cand = os.path.join(root, name)
        if os.path.isdir(cand):
            for dirpath, dirnames, filenames in walk_limited(cand, max_depth=3):
                for fn in filenames:
                    if fn.endswith((".yaml", ".yml", ".json")):
                        return True, rel(root, cand)
            return False, None
    return False, None


def detect_llm_calls(root):
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Tests that mock or call a model do not make the product an LLM
            # product (and this kit's own test fixtures contain literal
            # `import anthropic` strings).
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                           and d not in ("tests", "test", "__tests__", "spec")]
            for fn in filenames:
                ext = os.path.splitext(fn)[1]
                if ext not in SOURCE_EXTS or fn.startswith("test_") or fn.endswith(("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js")):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                except Exception:
                    continue
                if LLM_KEYWORDS.search(content):
                    return True
    except Exception:
        return False
    return False


def detect_ui(root):
    framework = None
    has_config = False
    for dirpath, dirnames, filenames in walk_limited(root, max_depth=3):
        for fn in filenames:
            if re.match(r"^next\.config\.(js|mjs|ts|cjs)$", fn):
                framework = "next"
                has_config = True
            elif re.match(r"^vite\.config\.(js|mjs|ts|cjs)$", fn) and framework is None:
                framework = "vite"
                has_config = True

    index_html = False
    if not has_config:
        for dirpath, dirnames, filenames in walk_limited(root, max_depth=3):
            rp = rel(root, dirpath)
            if rp != "." and re.search(r"(^|/)docs($|/)", rp):
                continue
            if "index.html" in filenames:
                index_html = True
                break

    present = has_config or index_html
    if present and framework is None:
        framework = "static"

    serve_cmd = None
    if present:
        pkg_path = os.path.join(root, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                dev = (data.get("scripts") or {}).get("dev")
                if dev:
                    serve_cmd = "npm run dev"
            except Exception:
                pass
        if serve_cmd is None:
            serve_cmd = "python3 -m http.server"

    return {"present": present, "framework": framework if present else None, "serve_cmd": serve_cmd}


# --------------------------------------------------------------------------
# git / docs
# --------------------------------------------------------------------------

def detect_git(root):
    proc = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
    is_repo = bool(proc and proc.returncode == 0 and proc.stdout.strip() == "true")
    if not is_repo:
        return {"main_branch": None, "remote": None, "clean": True, "is_repo": False}

    main_branch = None
    for candidate in ("main", "master"):
        p = run_cmd(["git", "show-ref", "--verify", "--quiet", "refs/heads/%s" % candidate], cwd=root)
        if p and p.returncode == 0:
            main_branch = candidate
            break

    remote = None
    p = run_cmd(["git", "remote", "get-url", "origin"], cwd=root)
    if p and p.returncode == 0 and p.stdout.strip():
        remote = p.stdout.strip()

    clean = True
    p = run_cmd(["git", "status", "--porcelain"], cwd=root)
    if p and p.returncode == 0:
        clean = p.stdout.strip() == ""

    return {"main_branch": main_branch, "remote": remote, "clean": clean, "is_repo": True}


def detect_docs(root):
    existing = set(os.listdir(root)) if os.path.isdir(root) else set()
    return {
        "claude_md": "CLAUDE.md" in existing,
        "readme": "README.md" in existing,
        "changelog": "CHANGELOG.md" in existing,
        "dead_ends": "DEAD_ENDS.md" in existing,
    }


# --------------------------------------------------------------------------
# main assembly
# --------------------------------------------------------------------------

def compute_slug(realpath):
    base = os.path.basename(realpath.rstrip(os.sep)) or "root"
    digest = hashlib.sha256(realpath.encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (base, digest)


def assess(project_dir):
    gaps = []
    realpath = os.path.realpath(project_dir)
    slug = compute_slug(realpath)

    # --- stack -------------------------------------------------------
    try:
        manifests_by_lang = find_manifests(realpath)
    except Exception:
        manifests_by_lang = {}
        gaps.append("manifest discovery failed")

    try:
        primary_lang, primary_manifest = pick_primary_language(manifests_by_lang)
    except Exception:
        primary_lang, primary_manifest = None, None
        gaps.append("could not determine primary stack")

    languages = [lang for lang in LANG_PRIORITY if manifests_by_lang.get(lang)]
    all_manifests = []
    for lang in LANG_PRIORITY:
        all_manifests.extend(manifests_by_lang.get(lang) or [])
    all_manifests = sorted(set(all_manifests), key=lambda p: (manifest_depth(p), p))

    try:
        package_manager = detect_package_manager(realpath, primary_lang, primary_manifest)
    except Exception:
        package_manager = None
        gaps.append("could not determine package manager")

    stack = {
        "languages": languages,
        "package_manager": package_manager,
        "submodules": os.path.isfile(os.path.join(realpath, ".gitmodules")),
        "manifests": all_manifests,
    }

    # --- tests ---------------------------------------------------------
    try:
        if primary_lang == "python":
            tests = detect_python_tests(realpath, primary_manifest, gaps)
        elif primary_lang == "javascript":
            tests = detect_node_tests(realpath, primary_manifest, gaps)
        elif primary_lang == "go":
            tests = detect_go_tests(realpath, primary_manifest, gaps)
        elif primary_lang == "rust":
            tests = detect_cargo_tests(realpath, primary_manifest, gaps)
        elif primary_lang == "swift":
            tests = detect_swift_tests(realpath, primary_manifest, gaps)
        else:
            tests = {
                "runner": "none", "test_cmd": None, "coverage_cmd": None,
                "coverage_file": None, "coverage_tool_installed": False,
            }
    except Exception:
        tests = {
            "runner": "none", "test_cmd": None, "coverage_cmd": None,
            "coverage_file": None, "coverage_tool_installed": False,
        }
        gaps.append("test detection failed")

    # --- bench -----------------------------------------------------
    try:
        bench_present, bench_cmd = detect_bench(realpath)
    except Exception:
        bench_present, bench_cmd = False, None
        gaps.append("bench detection failed")
    bench = {"present": bench_present, "cmd": bench_cmd}
    if not bench_present:
        gaps.append("no benchmark script")

    # --- llm_calls / evals -------------------------------------------
    try:
        llm_calls = detect_llm_calls(realpath)
    except Exception:
        llm_calls = False
        gaps.append("llm-call detection failed")

    try:
        evals_present, evals_dir = detect_evals(realpath)
    except Exception:
        evals_present, evals_dir = False, None
        gaps.append("eval detection failed")
    evals = {"present": evals_present, "dir": evals_dir}
    if llm_calls and not evals_present:
        gaps.append("no eval cases")

    # --- ui --------------------------------------------------------
    try:
        ui = detect_ui(realpath)
    except Exception:
        ui = {"present": False, "framework": None, "serve_cmd": None}
        gaps.append("ui detection failed")
    if ui["present"]:
        p = run_cmd(["npx", "lighthouse", "--version"], cwd=realpath, timeout=20)
        if not (p and p.returncode == 0):
            gaps.append("no lighthouse available for a11y audit")

    # --- git ---------------------------------------------------------
    try:
        git = detect_git(realpath)
    except Exception:
        git = {"main_branch": None, "remote": None, "clean": True, "is_repo": False}
        gaps.append("git detection failed")

    # --- docs ----------------------------------------------------------
    try:
        docs = detect_docs(realpath)
    except Exception:
        docs = {"claude_md": False, "readme": False, "changelog": False, "dead_ends": False}
        gaps.append("docs detection failed")
    doc_labels = {
        "claude_md": "CLAUDE.md", "readme": "README.md",
        "changelog": "CHANGELOG.md", "dead_ends": "DEAD_ENDS.md",
    }
    for key, present in docs.items():
        if not present:
            gaps.append("missing %s" % doc_labels[key])

    return {
        "project_dir": realpath,
        "slug": slug,
        "stack": stack,
        "tests": tests,
        "bench": bench,
        "evals": evals,
        "llm_calls": llm_calls,
        "ui": ui,
        "git": git,
        "docs": docs,
        "gaps": gaps,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Nightshift project assessor")
    parser.add_argument("--project", default=os.getcwd())
    args = parser.parse_args(argv)

    try:
        result = assess(args.project)
    except Exception as exc:  # never crash — this is the contract
        realpath = os.path.realpath(args.project)
        result = {
            "project_dir": realpath,
            "slug": compute_slug(realpath),
            "stack": {"languages": [], "package_manager": None, "manifests": []},
            "tests": {"runner": "none", "test_cmd": None, "coverage_cmd": None,
                      "coverage_file": None, "coverage_tool_installed": False},
            "bench": {"present": False, "cmd": None},
            "evals": {"present": False, "dir": None},
            "llm_calls": False,
            "ui": {"present": False, "framework": None, "serve_cmd": None},
            "git": {"main_branch": None, "remote": None, "clean": True, "is_repo": False},
            "docs": {"claude_md": False, "readme": False, "changelog": False, "dead_ends": False},
            "gaps": ["assess.py crashed: %s" % exc],
        }

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
