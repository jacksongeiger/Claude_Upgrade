#!/usr/bin/env python3
"""corpus_run.py — the kit's labelled regression corpus, run through the real
scripts. Each case is a mistake the system once made, turned into a check
the scripts must now get right. The pass rate is what kit-eval scores.

    corpus_run.py [--corpus retro/corpus] [--json]

Case file: retro/corpus/cases.json, a list of
    {"id", "kind", "note", ...kind fields}
kinds:
    check_plan   {"plan": <plan.json object>, "config": <config object>, "expect": "ok"|"reject", "reject_match": "<substring>"}
                 runs loop/check_plan.py on the plan with a temp repo
    ux_supersede {"open_rows": [...], "run": {"result": {...}, "findings": {...}}, "task": "...", "expect_done": ["ids"], "expect_open": ["ids"]}
                 runs pipeline/ux_score.py --backlog with the model off
    freeze       {"claims": {...}, "plan": {...}, "skeptic": {...}, "expect_kill": {"<claim>/<measure>": N}}
                 runs pipeline/validate.py freeze and checks the frozen numbers
    verdict      {"band_usd": N, "claims", "plan", "skeptic"?, "ledger": [...], "judge"?: {...}, "expect": "GO|PIVOT|NO-GO|INFRA"}
                 runs validate.py freeze + verdict --no-refetch
    schema       {"file": "plan|skeptic|claims|judge", "claims"?, "plan"?, "obj": {...}, "expect": "ok"|"reject", "reject_match": "<substring>"}
                 runs validate.py schema on the file (claims/plan supplied so the cross-checks have context)
    handoff      {"band_usd", "claims", "plan", "skeptic"?, "ledger", "overrule"?, "expect_success_lines": N}
                 verdict (+ overrule) then handoff into a spec; counts the success lines and keeps the spec's note
    gate_silent  {"prompt": "..."}  and  gate_fire {"prompt": "...", "expect_slug": "..."}
                 run discovery's retrieve.evaluate against the real index when present, else skipped
Prints one JSON line: {"ok", "passed", "failed", "skipped", "cases": [...]}.
Exit 0 when nothing failed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / "loop"
PIPE = ROOT / "pipeline"


def sh(cmd, cwd=None, env=None):
    e = dict(os.environ); e["CLASSIFY_OFF"] = "1"
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=e)


def w(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2) + "\n")


def case_check_plan(c, tmp):
    repo = Path(tmp) / "repo"; repo.mkdir()
    for f in c.get("files", []):
        (repo / f).parent.mkdir(parents=True, exist_ok=True); (repo / f).write_text("x\n")
    cfg = dict(c.get("config") or {}); cfg.setdefault("project_dir", str(repo)); cfg.setdefault("max_fanout", 3)
    w(Path(tmp) / "config.json", cfg)
    it = Path(tmp) / "it"; it.mkdir()
    w(it / "plan.json", c["plan"]); w(it / "target.json", {"iter": 1, "task_ids": c["plan"].get("task_ids", [])})
    proc = sh([sys.executable, str(LOOP / "check_plan.py"), str(it / "plan.json"), "--config", str(Path(tmp) / "config.json"), "--repo", str(repo)])
    if c["expect"] == "ok":
        return proc.returncode == 0, proc.stdout.strip()[:200]
    ok = proc.returncode == 2 and (c.get("reject_match", "") in proc.stdout)
    return ok, proc.stdout.strip()[:200]


def case_ux_supersede(c, tmp):
    sys.path.insert(0, str(LOOP)); import backlog_io  # noqa: E402
    bl = Path(tmp) / ".loop" / "backlog.yaml"; bl.parent.mkdir()
    backlog_io.dump(c["open_rows"], str(bl))
    run = Path(tmp) / "run"; run.mkdir()
    w(run / "result.json", c["run"]["result"]); w(run / "findings.json", c["run"].get("findings", {"dead_ends": [], "confusions": []}))
    check = json.dumps({"type": "persona", "task": c["task"], "max_steps": 4, "must": "complete"})
    proc = sh([sys.executable, str(PIPE / "ux_score.py"), "--run", str(run), "--check", check, "--backlog", str(bl)])
    rows = {r["id"]: r for r in backlog_io.load(str(bl))}
    ok = all(rows.get(i, {}).get("status") == "done" for i in c.get("expect_done", [])) and \
         all(rows.get(i, {}).get("status") == "open" for i in c.get("expect_open", []))
    return ok, json.dumps({i: rows.get(i, {}).get("status") for i in list(c.get("expect_done", [])) + list(c.get("expect_open", []))})


def _validate_dir(c, tmp):
    proc = sh([sys.executable, str(PIPE / "validate.py"), "init", "--project", tmp, "--idea", c.get("idea", "corpus idea " + c["id"]), "--build-usd", str(c.get("band_usd", 50))])
    d = Path(json.loads(proc.stdout.strip().splitlines()[-1])["dir"])
    w(d / "claims.json", c["claims"]); w(d / "plan.json", c["plan"])
    if c.get("skeptic"):
        w(d / "skeptic.json", c["skeptic"])
    fr = sh([sys.executable, str(PIPE / "validate.py"), "freeze", "--dir", str(d)])
    return d, fr


def case_freeze(c, tmp):
    d, fr = _validate_dir(c, tmp)
    if fr.returncode != 0:
        return False, fr.stdout.strip()[:200]
    fz = json.loads((d / "plan.frozen.json").read_text())
    got = {f"{cl['id']}/{m['name']}": m["kill_value"] for cl in fz["claims"] for m in cl["measures"]}
    ok = all(got.get(k) == v for k, v in c["expect_kill"].items())
    return ok, json.dumps({k: got.get(k) for k in c["expect_kill"]})


def case_verdict(c, tmp):
    d, fr = _validate_dir(c, tmp)
    if fr.returncode != 0:
        return False, fr.stdout.strip()[:200]
    (d / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in c["ledger"]))
    if c.get("judge"):
        w(d / "judge.json", c["judge"])
    if c.get("reachable"):
        w(d / "reachable.json", c["reachable"])
    proc = sh([sys.executable, str(PIPE / "validate.py"), "verdict", "--dir", str(d), "--no-refetch"])
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, proc.stdout[:200] + proc.stderr[:200]
    return out.get("verdict") == c["expect"], f"{out.get('verdict')} {out.get('claims')}"


def case_gate(c, tmp, fire):
    dbp = Path.home() / ".claude" / "rdx" / "index.db"
    if not dbp.exists():
        return None, "no rdx index on this machine"
    code = ("import sys, sqlite3, json\n"
            f"sys.path.insert(0, {str(ROOT / 'discovery')!r})\n"
            "from rdx import config, db, retrieve\n"
            f"conn = db.connect(str({str(dbp)!r})) if hasattr(db, 'connect') else sqlite3.connect({str(dbp)!r})\n"
            "conn.row_factory = sqlite3.Row\n"
            "cfg = config.load_config(shadow=False)\n"
            f"d = retrieve.evaluate({c['prompt']!r}, conn, cfg=cfg)\n"
            "print(json.dumps({'inject': d.inject, 'reason': d.reason, 'top': d.items[0].resource.slug if d.items else None}))\n")
    py = ROOT / "discovery" / "venv" / "bin" / "python"
    proc = sh([str(py) if py.exists() else sys.executable, "-c", code])
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, (proc.stderr or proc.stdout)[-200:]
    if fire:
        return bool(out["inject"]) and (not c.get("expect_slug") or out["top"] == c["expect_slug"]), json.dumps(out)
    return not out["inject"], json.dumps(out)


def case_schema(c, tmp):
    """{"file": "claims|plan|skeptic|judge", "claims"?, "plan"?, "skeptic"?, "obj": <the file under test>, "expect": "ok"|"reject", "reject_match": "..."}"""
    d, _ = _validate_dir({**c, "claims": c.get("claims") or c["obj"], "plan": c.get("plan") or {"claims": []}}, tmp) if c["file"] != "claims" else _validate_dir({**c, "claims": c["obj"], "plan": {"claims": []}}, tmp)
    w(d / f"{c['file']}.json", c["obj"])
    proc = sh([sys.executable, str(PIPE / "validate.py"), "schema", "--dir", str(d), "--file", c["file"]])
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, proc.stdout[:200] + proc.stderr[:200]
    problems = " | ".join(out.get("problems") or [])
    if c["expect"] == "ok":
        return bool(out.get("ok")), problems[:200] or "ok"
    return (not out.get("ok")) and c.get("reject_match", "") in problems, problems[:200]


def case_handoff(c, tmp):
    """{"band_usd", "claims", "plan", "skeptic"?, "ledger", "judge"?, "expect_success_lines": N}: verdict then handoff into an empty spec."""
    d, fr = _validate_dir(c, tmp)
    if fr.returncode != 0:
        return False, fr.stdout.strip()[:200]
    (d / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in c["ledger"]))
    if c.get("judge"):
        w(d / "judge.json", c["judge"])
    sh([sys.executable, str(PIPE / "validate.py"), "verdict", "--dir", str(d), "--no-refetch"])
    if c.get("overrule"):
        sh([sys.executable, str(PIPE / "validate.py"), "overrule", "--dir", str(d), "--by", "corpus", "--reason", c["overrule"]])
        sh([sys.executable, str(PIPE / "validate.py"), "verdict", "--dir", str(d), "--no-refetch"])
    spec = Path(tmp) / "spec.json"; w(spec, {"success": ["Every acceptance check passes"], "validation": {"note": "kept"}})
    proc = sh([sys.executable, str(PIPE / "validate.py"), "handoff", "--dir", str(d), "--spec", str(spec)])
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, proc.stdout[:200] + proc.stderr[:200]
    got = json.loads(spec.read_text())
    ok = out.get("success_lines") == c["expect_success_lines"] and got.get("validation", {}).get("note") == "kept"
    return ok, json.dumps({"success_lines": out.get("success_lines"), "frozen": out.get("measures_frozen"), "note_kept": got.get("validation", {}).get("note") == "kept"})


RUNNERS = {"check_plan": case_check_plan, "schema": case_schema, "handoff": case_handoff, "ux_supersede": case_ux_supersede, "freeze": case_freeze, "verdict": case_verdict,
           "gate_silent": lambda c, t: case_gate(c, t, False), "gate_fire": lambda c, t: case_gate(c, t, True)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="corpus_run.py")
    ap.add_argument("--corpus", default=str(ROOT / "retro" / "corpus"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cases = json.loads((Path(a.corpus) / "cases.json").read_text())
    results = []
    for c in cases:
        runner = RUNNERS.get(c.get("kind"))
        with tempfile.TemporaryDirectory() as tmp:
            try:
                ok, detail = runner(c, tmp) if runner else (False, f"unknown kind {c.get('kind')}")
            except Exception as e:  # noqa: BLE001 - one broken case must not hide the rest
                ok, detail = False, f"crashed: {e}"
        results.append({"id": c["id"], "kind": c.get("kind"), "ok": ok, "detail": detail, "note": c.get("note", "")})
    passed = sum(1 for r in results if r["ok"] is True)
    failed = sum(1 for r in results if r["ok"] is False)
    skipped = sum(1 for r in results if r["ok"] is None)
    out = {"ok": failed == 0, "passed": passed, "failed": failed, "skipped": skipped,
           "rate": round(passed / (passed + failed), 4) if (passed + failed) else 1.0, "cases": results}
    if a.json:
        print(json.dumps(out))
    else:
        for r in results:
            print(f"  {'ok  ' if r['ok'] else ('skip' if r['ok'] is None else 'FAIL')} {r['id']} ({r['kind']}) {r['detail'][:100]}")
        print(f"corpus: {passed} passed, {failed} failed, {skipped} skipped")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
