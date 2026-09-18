#!/usr/bin/env python3
"""map.py — import graph -> map.json + ARCHITECTURE.md + tree text.

See loop/README.md for the full contract. Python 3.9+, stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".swift", ".sh"}
VENDOR_DIRS = {"node_modules", "venv", ".venv", "dist", "build", ".loop", ".claude"}

MAX_EDGES = 120
DIR_COLLAPSE_THRESHOLD = 40


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #

def git_ls_files_s(repo: Path) -> dict:
    """path -> blob sha, from `git ls-files -s` (tracked files only)."""
    out = subprocess.run(
        ["git", "ls-files", "-s"], cwd=repo, capture_output=True, text=True, check=True
    )
    entries = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        parts = meta.split()
        sha = parts[1]
        entries[path] = sha
    return entries


def is_vendored(path: str) -> bool:
    parts = path.split("/")
    return any(p in VENDOR_DIRS for p in parts[:-1])


def filter_code_files(all_paths):
    out = []
    for p in all_paths:
        if is_vendored(p):
            continue
        ext = "." + p.rsplit(".", 1)[-1] if "." in p.rsplit("/", 1)[-1] else ""
        if ext in CODE_EXTS:
            out.append(p)
    return sorted(out)


def compute_files_sha(paths, blob_shas) -> str:
    lines = sorted(f"{p}\0{blob_shas[p]}" for p in paths)
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Python import resolution
# --------------------------------------------------------------------------- #

def _resolve_py_module(base_dir, dotted, pyfiles):
    """base_dir: tuple of path components. dotted: 'a.b.c' or ''.
    Returns (path, is_pkg) or (None, None)."""
    parts = dotted.split(".") if dotted else []
    candidate = list(base_dir) + parts
    file_cand = "/".join(candidate) + ".py"
    pkg_cand = "/".join(candidate + ["__init__.py"])
    if file_cand in pyfiles:
        return file_cand, False
    if pkg_cand in pyfiles:
        return pkg_cand, True
    return None, None


def _resolve_py_from(target_base, module_dotted, names, pyfiles):
    edges = set()
    if module_dotted:
        path, is_pkg = _resolve_py_module(target_base, module_dotted, pyfiles)
        if path is None:
            return edges
        if is_pkg:
            pkg_dir = tuple(path.rsplit("/", 1)[0].split("/")) if "/" in path else ()
            added_any = False
            for nm in names:
                if nm == "*":
                    continue
                sub_file = "/".join(pkg_dir + (nm,)) + ".py"
                sub_pkg = "/".join(pkg_dir + (nm, "__init__.py"))
                if sub_file in pyfiles:
                    edges.add(sub_file)
                    added_any = True
                elif sub_pkg in pyfiles:
                    edges.add(sub_pkg)
                    added_any = True
            if not added_any:
                edges.add(path)
        else:
            edges.add(path)
    else:
        added_any = False
        for nm in names:
            if nm == "*":
                continue
            sub_file = "/".join(target_base + (nm,)) + ".py"
            sub_pkg = "/".join(target_base + (nm, "__init__.py"))
            if sub_file in pyfiles:
                edges.add(sub_file)
                added_any = True
            elif sub_pkg in pyfiles:
                edges.add(sub_pkg)
                added_any = True
        if not added_any:
            base_init = "/".join(target_base + ("__init__.py",)) if target_base else "__init__.py"
            if base_init in pyfiles:
                edges.add(base_init)
    return edges


def extract_py_imports(path, text, pyfiles):
    edges = set()
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError:
        return edges
    file_dir = tuple(path.split("/")[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                p, _ = _resolve_py_module((), alias.name, pyfiles)
                if p:
                    edges.add(p)
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            if node.level and node.level > 0:
                if node.level - 1 >= len(file_dir):
                    target_base = ()
                else:
                    target_base = file_dir[: len(file_dir) - (node.level - 1)]
                edges |= _resolve_py_from(target_base, node.module or "", names, pyfiles)
            else:
                edges |= _resolve_py_from((), node.module or "", names, pyfiles)
    return edges


# --------------------------------------------------------------------------- #
# JS/TS import resolution
# --------------------------------------------------------------------------- #

JS_IMPORT_FROM_RE = re.compile(r"import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]", re.DOTALL)
JS_EXPORT_FROM_RE = re.compile(r"export\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]", re.DOTALL)
JS_REQUIRE_RE = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")
JS_DYNAMIC_IMPORT_RE = re.compile(r"import\(\s*['\"]([^'\"]+)['\"]\s*\)")

JS_EXTS = (".ts", ".tsx", ".js", ".jsx")


def _resolve_js_specifier(cur_path, spec, code_files):
    if not spec.startswith("."):
        return None
    cur_dir = cur_path.split("/")[:-1]
    stack = list(cur_dir)
    for part in spec.split("/"):
        if part in ("", "."):
            continue
        elif part == "..":
            if stack:
                stack.pop()
        else:
            stack.append(part)
    base = "/".join(stack)
    candidates = []
    if any(base.endswith(ext) for ext in JS_EXTS):
        candidates.append(base)
    else:
        candidates.extend(base + ext for ext in JS_EXTS)
        candidates.extend(base + "/index" + ext for ext in JS_EXTS)
    for c in candidates:
        if c in code_files:
            return c
    return None


def extract_js_imports(path, text, code_files):
    edges = set()
    specs = set()
    for rx in (JS_IMPORT_FROM_RE, JS_EXPORT_FROM_RE, JS_REQUIRE_RE, JS_DYNAMIC_IMPORT_RE):
        specs |= set(rx.findall(text))
    for spec in specs:
        resolved = _resolve_js_specifier(path, spec, code_files)
        if resolved:
            edges.add(resolved)
    return edges


# --------------------------------------------------------------------------- #
# Go import resolution
# --------------------------------------------------------------------------- #

GO_IMPORT_BLOCK_RE = re.compile(r"import\s*\(([^)]*)\)", re.DOTALL)
GO_IMPORT_SINGLE_RE = re.compile(r'import\s+"([^"]+)"')
GO_QUOTED_RE = re.compile(r'"([^"]+)"')


def read_go_module_prefix(repo: Path):
    gomod = repo / "go.mod"
    if not gomod.exists():
        return None
    try:
        for line in gomod.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("module "):
                return line[len("module "):].strip()
    except OSError:
        pass
    return None


def extract_go_imports(path, text, code_files, module_prefix):
    edges = set()
    if not module_prefix:
        return edges
    raw_imports = []
    for m in GO_IMPORT_BLOCK_RE.finditer(text):
        raw_imports.extend(GO_QUOTED_RE.findall(m.group(1)))
    for m in GO_IMPORT_SINGLE_RE.finditer(text):
        raw_imports.append(m.group(1))
    for imp in raw_imports:
        if imp == module_prefix:
            rel = ""
        elif imp.startswith(module_prefix + "/"):
            rel = imp[len(module_prefix) + 1:]
        else:
            continue
        for f in code_files:
            if not f.endswith(".go") or f == path:
                continue
            f_dir = f.rsplit("/", 1)[0] if "/" in f else ""
            if f_dir == rel:
                edges.add(f)
    return edges


# --------------------------------------------------------------------------- #
# Rust import resolution (best effort)
# --------------------------------------------------------------------------- #

RUST_MOD_RE = re.compile(r"\bmod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;")
RUST_USE_CRATE_RE = re.compile(r"\buse\s+crate::([A-Za-z0-9_:]+)")


def extract_rust_imports(path, text, code_files):
    edges = set()
    file_parts = path.split("/")
    file_dir = file_parts[:-1]
    stem = file_parts[-1][:-3] if file_parts[-1].endswith(".rs") else file_parts[-1]
    if file_parts[-1] in ("lib.rs", "main.rs", "mod.rs"):
        base_dir = file_dir
    else:
        base_dir = file_dir + [stem]
    for m in RUST_MOD_RE.finditer(text):
        name = m.group(1)
        cand1 = "/".join(base_dir + [name]) + ".rs"
        cand2 = "/".join(base_dir + [name, "mod.rs"])
        if cand1 in code_files:
            edges.add(cand1)
        elif cand2 in code_files:
            edges.add(cand2)

    crate_root = None
    for i in range(len(file_parts) - 1, -1, -1):
        if file_parts[i] == "src":
            crate_root = file_parts[:i + 1]
            break
    if crate_root is not None:
        for m in RUST_USE_CRATE_RE.finditer(text):
            comps = [c for c in m.group(1).split("::") if c and c != "self"]
            for cut in range(len(comps), 0, -1):
                sub = comps[:cut]
                cand1 = "/".join(crate_root + sub) + ".rs"
                cand2 = "/".join(crate_root + sub + ["mod.rs"])
                if cand1 in code_files:
                    edges.add(cand1)
                    break
                if cand2 in code_files:
                    edges.add(cand2)
                    break
    return edges


# --------------------------------------------------------------------------- #
# module building
# --------------------------------------------------------------------------- #

def extract_imports(path, text, code_files, module_prefix):
    ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if ext == ".py":
        return extract_py_imports(path, text, code_files)
    if ext in (".js", ".ts", ".tsx", ".jsx"):
        return extract_js_imports(path, text, code_files)
    if ext == ".go":
        return extract_go_imports(path, text, code_files, module_prefix)
    if ext == ".rs":
        return extract_rust_imports(path, text, code_files)
    # .swift, .sh: no edges
    return set()


def build_modules(repo: Path, files, old_hash_by_path, old_desc_by_path):
    code_files = set(files)
    module_prefix = read_go_module_prefix(repo)
    modules = []
    for path in files:
        try:
            raw = (repo / path).read_bytes()
        except OSError:
            continue
        h = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8", errors="replace")
        loc = sum(1 for line in text.splitlines() if line.strip())
        imports = sorted(extract_imports(path, text, code_files, module_prefix))
        if old_hash_by_path.get(path) == h:
            desc = old_desc_by_path.get(path, "")
        else:
            desc = ""
        modules.append({"path": path, "loc": loc, "imports": imports, "hash": h, "desc": desc})
    modules.sort(key=lambda m: m["path"])
    return modules


# --------------------------------------------------------------------------- #
# ARCHITECTURE.md rendering
# --------------------------------------------------------------------------- #

def _topdir(p):
    return p.split("/")[0] if "/" in p else "(root)"


def render_architecture(modules, generated_ts):
    lines = ["# Architecture", "", f"_generated {generated_ts}_", ""]

    edges = sorted({(m["path"], imp) for m in modules for imp in m["imports"]})
    n = len(modules)
    truncated = False
    if n > DIR_COLLAPSE_THRESHOLD:
        # Collapse to the SHALLOWEST directory depth that still fits under the
        # threshold, not always the top level: an 84-module repo with all its
        # code under two top-level dirs rendered as an empty graph, because
        # every edge was a self-edge at depth 1.
        def at_depth(path, d):
            parts = path.split("/")
            return "/".join(parts[:d]) if len(parts) > d else "/".join(parts[:-1]) or "(root)"
        mode = "directory"
        graph_edges = []
        for depth in range(1, 6):
            cand = sorted({(at_depth(a, depth), at_depth(b, depth)) for a, b in edges
                           if at_depth(a, depth) != at_depth(b, depth)})
            nodes_at = {x for e in cand for x in e}
            if len(nodes_at) > DIR_COLLAPSE_THRESHOLD:
                break
            graph_edges = cand
            mode = f"directory(depth {depth})"
            if len(nodes_at) >= 4:
                # enough structure to read; go one level deeper only if it still fits
                deeper = sorted({(at_depth(a, depth + 1), at_depth(b, depth + 1)) for a, b in edges
                                 if at_depth(a, depth + 1) != at_depth(b, depth + 1)})
                if len({x for e in deeper for x in e}) > DIR_COLLAPSE_THRESHOLD:
                    break
    else:
        graph_edges = edges
        mode = "file"
    # A directory graph with fewer than six nodes says almost nothing. Show
    # the file-level graph among the most-connected modules instead.
    if mode.startswith("directory") and len({x for e in graph_edges for x in e}) < 6:
        deg = {}
        for a, b in edges:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        keep = set(sorted(deg, key=lambda k: (-deg[k], k))[:DIR_COLLAPSE_THRESHOLD])
        graph_edges = [(a, b) for a, b in edges if a in keep and b in keep]
        mode = f"file (top {len(keep)} by degree)"
    if len(graph_edges) > MAX_EDGES:
        graph_edges = graph_edges[:MAX_EDGES]
        truncated = True

    lines.append("```mermaid")
    lines.append("graph LR")
    ids = {}
    nodes = sorted({n for edge in graph_edges for n in edge})
    for node in nodes:
        ids[node] = f"n{len(ids)}"
    for node in nodes:
        lines.append(f'    {ids[node]}["{node}"]')
    for a, b in graph_edges:
        lines.append(f"    {ids[a]} --> {ids[b]}")
    lines.append("```")
    lines.append("")
    if truncated:
        lines.append(f"_({mode}-level graph truncated to {MAX_EDGES} edges)_")
        lines.append("")

    fan_in = {m["path"]: 0 for m in modules}
    for m in modules:
        for imp in m["imports"]:
            fan_in[imp] = fan_in.get(imp, 0) + 1

    top = sorted(modules, key=lambda m: (-fan_in.get(m["path"], 0), m["path"]))[:10]
    lines.append("| Path | Fan-in | Desc |")
    lines.append("|---|---|---|")
    for m in top:
        desc = (m["desc"] or "").replace("|", "\\|")
        lines.append(f"| {m['path']} | {fan_in.get(m['path'], 0)} | {desc} |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# ASCII tree rendering
# --------------------------------------------------------------------------- #

def render_tree(modules, changed_set):
    fan_in = {m["path"]: 0 for m in modules}
    for m in modules:
        for imp in m["imports"]:
            if imp in fan_in:
                fan_in[imp] += 1
    fan_out = {m["path"]: len(m["imports"]) for m in modules}

    root: dict = {}
    for m in modules:
        parts = m["path"].split("/")
        node = root
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node.setdefault("__files__", []).append(parts[-1])

    lines = []

    def walk(node, prefix, path_prefix):
        dirs = sorted(k for k in node.keys() if k != "__files__")
        files = sorted(node.get("__files__", []))
        entries = [(d, True) for d in dirs] + [(f, False) for f in files]
        for i, (name, is_dir) in enumerate(entries):
            last = i == len(entries) - 1
            connector = "└── " if last else "├── "
            if is_dir:
                lines.append(f"{prefix}{connector}{name}/")
                ext = "    " if last else "│   "
                walk(node[name], prefix + ext, path_prefix + name + "/")
            else:
                full = path_prefix + name
                marker = " *" if full in changed_set else ""
                fi = fan_in.get(full, 0)
                fo = fan_out.get(full, 0)
                lines.append(f"{prefix}{connector}{name}  ←{fi} →{fo}{marker}")

    walk(root, "", "")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Import graph -> map.json + ARCHITECTURE.md + tree")
    p.add_argument("--repo", default=".")
    p.add_argument("--out", default=".loop/map.json")
    p.add_argument("--arch", default="ARCHITECTURE.md")
    p.add_argument("--tree", action="store_true")
    p.add_argument("--changed", default=None, help="file with newline-separated changed paths")
    p.add_argument("--force", action="store_true")
    return p.parse_args(argv)


def load_changed_set(changed_arg):
    if not changed_arg:
        return set()
    try:
        text = Path(changed_arg).read_text()
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def main(argv=None):
    args = parse_args(argv)
    repo = Path(args.repo).resolve()
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = repo / out_path
    arch_path = Path(args.arch)
    if not arch_path.is_absolute():
        arch_path = repo / arch_path

    blob_shas = git_ls_files_s(repo)
    files = filter_code_files(list(blob_shas.keys()))
    files_sha = compute_files_sha(files, blob_shas)

    old_map = None
    if out_path.exists():
        try:
            old_map = json.loads(out_path.read_text())
        except (OSError, json.JSONDecodeError):
            old_map = None

    stale = args.force or old_map is None or old_map.get("files_sha") != files_sha
    changed_set_for_tree = load_changed_set(args.changed)

    if not stale:
        if not args.tree:
            print("unchanged")
            return 0
        # tree mode, not stale: render from existing map, no writes
        modules = old_map.get("modules", [])
        print(render_tree(modules, changed_set_for_tree))
        return 0

    # stale: rebuild
    old_hash_by_path = {m["path"]: m.get("hash") for m in (old_map or {}).get("modules", [])}
    old_desc_by_path = {m["path"]: m.get("desc", "") for m in (old_map or {}).get("modules", [])}

    modules = build_modules(repo, files, old_hash_by_path, old_desc_by_path)

    generated_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_map = {"generated": generated_ts, "files_sha": files_sha, "modules": modules}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(new_map, indent=2, sort_keys=False) + "\n")

    arch_text = render_architecture(modules, generated_ts)
    arch_path.parent.mkdir(parents=True, exist_ok=True)
    arch_path.write_text(arch_text + "\n")

    changed = [m["path"] for m in modules if old_hash_by_path.get(m["path"]) != m["hash"]]
    changed.sort()

    if args.tree:
        print(render_tree(modules, changed_set_for_tree))

    print("changed:")
    for c in changed:
        print(c)

    return 0


if __name__ == "__main__":
    sys.exit(main())
