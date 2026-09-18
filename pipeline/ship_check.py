#!/usr/bin/env python3
"""Pre-deploy checklist for /jg-ship (pipeline README, "Stage 6 — /jg-ship").

    python3 ship_check.py --spec spec.json --workdir <dir> [--tag vX]
                           [--accept-cmd "<override>"] [--skip lighthouse,security]
                           [--security-confirmed]

Runs, in order: acceptance, git-clean, changelog, env-example, secrets,
lighthouse, security-review, gates. Writes
`<workdir>/.pipeline/ship/<tag>/ship-report.json`:

    {"tag": "vX"|null, "ts": "<UTC iso>",
     "checks": [{"name": "...", "ok": true|false|null, "detail": "..."}, ...],
     "ok": true|false}

and prints one line per check (PASS/FAIL/SKIP — detail).

Exit codes: 0 ok (no check false) · 2 at least one check is false ·
4 infra (a subprocess could not even be run — as opposed to a clean
non-zero exit, which is a normal check failure) · 1 usage error.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
ROOT = PIPELINE_DIR.parent

SKIP_DIR_PARTS = {"node_modules", "venv", ".git", "dist", "build", "tests"}

ENV_PATTERNS = [
    re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"process\.env\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),
    re.compile(r"os\.environ\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),
    re.compile(r"os\.environ\.get\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
    re.compile(r"os\.getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
]

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]",
        re.I,
    ),
]

LIGHTHOUSE_CATEGORY_MAP = {
    "accessibility": "a11y", "a11y": "a11y",
    "performance": "perf", "perf": "perf",
    "best-practices": "best", "best": "best",
    "seo": "seo",
}


class InfraError(Exception):
    """A subprocess could not be run at all (not a clean non-zero exit)."""


def run(cmd, cwd=None, timeout=120):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise InfraError(f"{' '.join(str(c) for c in cmd)}: {e}") from e


def tracked_files(workdir):
    proc = run(["git", "ls-files"], cwd=str(workdir))
    if proc.returncode != 0:
        return []
    out = []
    for f in proc.stdout.splitlines():
        f = f.strip()
        if not f:
            continue
        if any(part in SKIP_DIR_PARTS for part in Path(f).parts):
            continue
        out.append(f)
    return out


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def check_acceptance(spec, spec_path, workdir, accept_cmd):
    milestones = spec.get("milestones") or []
    if not milestones:
        return {"name": "acceptance", "ok": True, "detail": "no milestones in spec"}

    accept_script = PIPELINE_DIR / "accept.py"
    if not accept_cmd and not accept_script.exists():
        return {"name": "acceptance", "ok": None, "detail": "accept.py missing"}

    failed = []
    for m in milestones:
        mid = m.get("id")
        base = shlex.split(accept_cmd) if accept_cmd else [sys.executable, str(accept_script)]
        cmd = base + ["--spec", str(spec_path), "--milestone", str(mid), "--workdir", str(workdir)]
        proc = run(cmd, cwd=str(workdir), timeout=600)
        if proc.returncode != 0:
            failed.append(f"{mid} (exit {proc.returncode})")

    ok = not failed
    detail = "all milestones accepted" if ok else "failed: " + ", ".join(failed)
    return {"name": "acceptance", "ok": ok, "detail": detail}


def check_git_clean(workdir):
    status = run(["git", "status", "--porcelain"], cwd=str(workdir))
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(workdir))

    if status.returncode != 0:
        return {"name": "git-clean", "ok": False, "detail": f"git status failed: {status.stderr.strip()}"}

    # the pipeline's own state (.pipeline/, .loop/) is written by these very
    # stages; it is not a dirty product tree
    offenders = [l for l in status.stdout.splitlines() if l.strip()
                 and not l[3:].startswith((".pipeline/", ".loop/"))]
    cur_branch = branch.stdout.strip() if branch.returncode == 0 else "?"
    if cur_branch not in ("main", "master"):
        offenders.append(f"branch={cur_branch}")

    ok = not offenders
    detail = ("clean on " + cur_branch) if ok else "; ".join(offenders)
    return {"name": "git-clean", "ok": ok, "detail": detail}


def check_changelog(workdir, tag):
    path = Path(workdir) / "CHANGELOG.md"
    if not path.exists():
        return {"name": "changelog", "ok": False, "detail": "CHANGELOG.md missing"}
    text = _read_text(path) or ""
    if tag:
        ok = tag in text
        detail = f"contains {tag}" if ok else f"no entry for {tag}"
    else:
        ok = bool(re.search(r"^#{2,3}\s", text, re.M))
        detail = "has a version heading" if ok else "no ##/### heading found"
    return {"name": "changelog", "ok": ok, "detail": detail}


def check_env_example(workdir):
    files = tracked_files(workdir)
    referenced = set()
    for f in files:
        text = _read_text(Path(workdir) / f)
        if text is None:
            continue
        for pat in ENV_PATTERNS:
            for m in pat.finditer(text):
                referenced.add(m.group(1))

    if not referenced:
        return {"name": "env-example", "ok": True, "detail": "no env vars referenced"}

    env_example = Path(workdir) / ".env.example"
    defined = set()
    if env_example.exists():
        for line in (_read_text(env_example) or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            defined.add(line.split("=", 1)[0].strip())

    missing = sorted(referenced - defined)
    ok = not missing
    detail = "all referenced vars present in .env.example" if ok else "missing: " + ", ".join(missing)
    return {"name": "env-example", "ok": ok, "detail": detail}


def _shipignore(workdir):
    path = Path(workdir) / ".shipignore"
    if not path.exists():
        return set()
    names = set()
    for line in (_read_text(path) or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(line)
    return names


def check_secrets(workdir):
    files = tracked_files(workdir)
    ignored = _shipignore(workdir)
    hits = []
    for f in files:
        if os.path.basename(f).startswith(".env"):
            continue
        if f in ignored:
            continue
        text = _read_text(Path(workdir) / f)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if any(pat.search(line) for pat in SECRET_PATTERNS):
                hits.append(f"{f}:{i}")

    ok = not hits
    detail = "no secrets found" if ok else "; ".join(hits)
    return {"name": "secrets", "ok": ok, "detail": detail}


def check_lighthouse(spec, workdir, skip_set):
    needs = spec.get("needs") or []
    if "lighthouse" not in needs:
        return {"name": "lighthouse", "ok": None, "detail": "not in spec.needs"}
    if "lighthouse" in skip_set:
        return {"name": "lighthouse", "ok": None, "detail": "skipped"}

    entries = []
    for feat in spec.get("features") or []:
        for acc in feat.get("acceptance") or []:
            if acc.get("type") == "lighthouse":
                entries.append((acc.get("url"), acc.get("min") or {}))
    if not entries:
        return {"name": "lighthouse", "ok": None, "detail": "no lighthouse acceptance checks in spec"}

    serve = ((spec.get("stack") or {}).get("serve")) or {}
    if not serve.get("cmd") or not serve.get("port"):
        return {"name": "lighthouse", "ok": None, "detail": "spec.stack.serve not configured"}

    script = ROOT / "loop" / "scorers" / "lighthouse.py"
    if not script.exists():
        return {"name": "lighthouse", "ok": None, "detail": "loop/scorers/lighthouse.py missing"}

    # the scorer audits full urls; the spec names paths on the served app
    base = f"http://localhost:{serve['port']}"
    full = {}
    for u, _ in entries:
        if not u:
            continue
        full[u] = u if u.startswith(("http://", "https://")) else base + "/" + u.lstrip("/")
    urls = sorted(set(full.values()))
    cfg = {"serve_cmd": serve["cmd"], "port": serve["port"], "urls": urls}
    proc = run([sys.executable, str(script), "--config", json.dumps(cfg), "--workdir", str(workdir)],
               cwd=str(workdir), timeout=900)

    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        return {"name": "lighthouse", "ok": False,
                "detail": f"no output from lighthouse.py (exit {proc.returncode}): {proc.stderr.strip()[:300]}"}
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as e:
        return {"name": "lighthouse", "ok": False, "detail": f"bad lighthouse output: {e}"}

    if not result.get("ok"):
        return {"name": "lighthouse", "ok": False, "detail": result.get("error") or "lighthouse scorer failed"}

    per_url = (result.get("raw") or {}).get("per_url") or {}
    failures = []
    for url, mins in entries:
        scores = per_url.get(full.get(url, url))
        if scores is None:
            failures.append(f"{url}: no lighthouse data")
            continue
        for cat, min_val in mins.items():
            key = LIGHTHOUSE_CATEGORY_MAP.get(cat, cat)
            got = scores.get(key)
            if got is None:
                failures.append(f"{url}/{cat}: no score")
            elif got < min_val:
                failures.append(f"{url}/{cat}: {got:.1f} < {min_val}")

    ok = not failures
    detail = "all minimums met" if ok else "; ".join(failures)
    return {"name": "lighthouse", "ok": ok, "detail": detail}


def _human_override(workdir, kind, what):
    try:
        from datetime import datetime, timezone
        p = Path(workdir) / ".pipeline" / "events.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} HUMAN_OVERRIDE kind={kind} what={what}\n")
    except OSError:
        pass


def check_security_review(security_confirmed, skip_set):
    if skip_set & {"security", "security-review"}:
        return {"name": "security-review", "ok": None, "detail": "skipped"}
    if security_confirmed:
        return {"name": "security-review", "ok": True, "detail": "confirmed by human"}
    return {"name": "security-review", "ok": None,
            "detail": "human: run /security-review this session and confirm"}


def check_gates(spec, workdir):
    gates_script = PIPELINE_DIR / "gates.py"
    index_path = Path(workdir) / ".pipeline" / "acceptance-index.json"
    missing = []
    if not gates_script.exists():
        missing.append("gates.py")
    if not index_path.exists():
        missing.append("acceptance-index.json")
    if missing:
        return {"name": "gates", "ok": None, "detail": "missing: " + ", ".join(missing)}

    milestones = spec.get("milestones") or []
    if not milestones:
        return {"name": "gates", "ok": True, "detail": "no milestones in spec"}

    # screenshot gates need the served app: start it once from spec.stack.serve
    serve = ((spec.get("stack") or {}).get("serve")) or {}
    base_args = []
    server = None
    if serve.get("cmd") and serve.get("port"):
        import socket, time, subprocess as _sp
        server = _sp.Popen(["bash", "-c", serve["cmd"]], cwd=str(workdir), stdout=_sp.DEVNULL,
                           stderr=_sp.DEVNULL, start_new_session=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", int(serve["port"])), timeout=1):
                    base_args = ["--base-url", f"http://127.0.0.1:{serve['port']}"]
                    break
            except OSError:
                time.sleep(0.5)
    codes = {}
    try:
        for m in milestones:
            mid = m.get("id")
            proc = run([sys.executable, str(gates_script), "run-all", "--index", str(index_path),
                        "--milestone", str(mid)] + base_args, cwd=str(workdir), timeout=600)
            codes[mid] = proc.returncode
    finally:
        if server is not None and server.poll() is None:
            import os as _os, signal as _sig
            try:
                _os.killpg(server.pid, _sig.SIGTERM)
            except OSError:
                pass

    failed = [mid for mid, c in codes.items() if c == 2]
    baseline = [mid for mid, c in codes.items() if c == 3]
    other = [mid for mid, c in codes.items() if c not in (0, 2, 3)]

    if failed:
        return {"name": "gates", "ok": False, "detail": "failed: " + ", ".join(failed)}
    if baseline:
        return {"name": "gates", "ok": None, "detail": "needs-baseline: " + ", ".join(baseline)}
    if other:
        return {"name": "gates", "ok": None, "detail": f"unexpected exit codes: {codes}"}
    return {"name": "gates", "ok": True, "detail": "all milestones passed"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(description="Pre-deploy checklist (/jg-ship, stage 6)")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--tag")
    ap.add_argument("--accept-cmd")
    ap.add_argument("--skip", default="")
    ap.add_argument("--security-confirmed", action="store_true")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    workdir = Path(args.workdir).resolve()

    try:
        spec = json.loads(Path(args.spec).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read spec {args.spec}: {e}", file=sys.stderr)
        return 1

    skip_set = {s.strip() for s in (args.skip or "").split(",") if s.strip()}
    tag = args.tag

    checks = []
    infra = False

    def add(name, fn):
        nonlocal infra
        try:
            result = fn()
        except InfraError as e:
            result = {"name": name, "ok": None, "detail": f"infra: {e}"}
            infra = True
        checks.append(result)

    add("acceptance", lambda: check_acceptance(spec, args.spec, workdir, args.accept_cmd))
    add("git-clean", lambda: check_git_clean(workdir))
    add("changelog", lambda: check_changelog(workdir, tag))
    add("env-example", lambda: check_env_example(workdir))
    add("secrets", lambda: check_secrets(workdir))
    add("lighthouse", lambda: check_lighthouse(spec, workdir, skip_set))
    if args.security_confirmed:
        _human_override(workdir, "security-confirmed", args.tag)
    add("security-review", lambda: check_security_review(args.security_confirmed, skip_set))
    add("gates", lambda: check_gates(spec, workdir))

    overall_ok = all(c["ok"] is not False for c in checks)

    report = {
        "tag": tag,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": checks,
        "ok": overall_ok,
    }

    out_dir = workdir / ".pipeline" / "ship" / (tag or "untagged")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "ship-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    status_word = {True: "PASS", False: "FAIL", None: "SKIP"}
    for c in checks:
        line = f"{c['name']}: {status_word[c['ok']]}"
        if c.get("detail"):
            line += f" — {c['detail']}"
        print(line)
    print(f"wrote {report_path}")

    if infra:
        return 4
    if not overall_ok:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
