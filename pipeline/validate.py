#!/usr/bin/env python3
"""validate.py — the referee of the validation stage. Never reads the idea.

    validate.py init     --project P --idea TEXT --build-usd N [--toml T]
    validate.py schema   --dir D --file claims|plan|skeptic|judge
    validate.py redact   --dir D
    validate.py freeze   --dir D
    validate.py check-frozen --dir D
    validate.py verdict  --dir D [--reachable R] [--fetcher SCRIPT] [--no-refetch] [--toml T]
    validate.py overrule --dir D --by NAME --reason TEXT --measure M --kill-value V --direction min|max
    validate.py handoff  --dir D --spec spec.json

Roles write files; this script checks their shape, freezes the plan before
any evidence is fetched, and computes the verdict from the ledger against
the frozen numbers. A model judges what to measure and what it means; the
script only holds it to the numbers it wrote before looking.

Directory: <project>/.pipeline/validate/<slug>/ where slug = sha256(idea)[:8].

Files and shapes (every one is JSON):
  idea.json          {idea, slug, build_usd, band, started, project}
  claims.json        {"claims":[{"id","core":bool,"who","pain","statement","metric_hint"}]}
                     3–6 claims, exactly one core
  plan.json          {"claims":[{"id","measures":[{"name","unit","direction":"min|max",
                       "kill_value":N,"sources":[{"kind":"url|experiment","where","extract"}]}]}]}
  plan.redacted.json plan.json with every kill_value removed (what the skeptic sees)
  skeptic.json       {"claims":[{"id","kill_numbers":{measure:N},
                       "added_measures":[{...as plan measures...}],
                       "disconfirming_sources":[{"where","extract","measure","note"}]}]}
  plan.frozen.json   the merge: per measure the stricter kill number, the
                     skeptic's added measures, and required sources; plus
                     frozen_at and the sha the driver checks afterwards
  ledger.jsonl       rows fetch.py writes: {claim, measure, source{kind,url,
                     extracted_by,body_hash,body_path}, value, unit, date,
                     origin, required, note, reason}
  judge.json         {"rows":[{"index":i,"score":0|1|2,"quote":"..."}]}
  verdict.json       {verdict, exit, band, required, claims:{id:{...}},
                     rows:[...], contradictions:[], infra:[], overruled?,
                     validated_budget_usd, idea, slug, computed_at}
  VERDICT.md         the readable form, every source linked

Exit codes: 0 GO · 2 NO-GO / bad shape · 3 needs-human · 4 infra · 5 PIVOT
· 1 usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

KIT = Path(__file__).resolve().parent
DEFAULT_TOML = KIT / "validate.toml"
FETCH = KIT / "fetch.py"

EXIT_GO, EXIT_NOGO, EXIT_HUMAN, EXIT_INFRA, EXIT_PIVOT, EXIT_USAGE = 0, 2, 3, 4, 5, 1

ORIGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,127}$")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")


def read_toml(path):
    with open(path or DEFAULT_TOML, "rb") as f:
        return tomllib.load(f)


def band_for(build_usd, cfg):
    b = cfg["bands"]
    if build_usd < b["small_max_usd"]:
        return "small"
    if build_usd <= b["mid_max_usd"]:
        return "mid"
    return "large"


def slug_for(idea):
    return sha256_bytes(idea.strip().encode("utf-8"))[:8]


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(a):
    cfg = read_toml(a.toml)
    idea = a.idea.strip()
    if not idea:
        print(json.dumps({"ok": False, "error": "empty idea"}))
        return EXIT_USAGE
    slug = slug_for(idea)
    d = Path(a.project) / ".pipeline" / "validate" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "bodies").mkdir(exist_ok=True)
    band = band_for(float(a.build_usd), cfg)
    dump(d / "idea.json", {"idea": idea, "slug": slug, "build_usd": float(a.build_usd), "band": band,
                           "started": now(), "project": str(Path(a.project).resolve())})
    roles = ["author", "setter", "fetcher", "referee"] if band == "small" else \
            ["author", "setter", "skeptic", "freeze", "fetcher", "judge", "referee"]
    cap = cfg["budget"]["usd_small"] if band == "small" else cfg["budget"]["usd"]
    print(json.dumps({"ok": True, "dir": str(d), "slug": slug, "band": band, "roles": roles, "cap_usd": cap}))
    return 0


# ---------------------------------------------------------------------------
# schema checks (a child's file, before the next role reads it)
# ---------------------------------------------------------------------------

def _measure_ok(m, problems, where):
    if not isinstance(m, dict):
        problems.append(f"{where}: measure is not an object"); return
    for k in ("name", "unit", "direction"):
        if not isinstance(m.get(k), str) or not m.get(k):
            problems.append(f"{where}: measure missing {k}")
    if m.get("direction") not in ("min", "max"):
        problems.append(f"{where}: direction must be min or max")
    if not isinstance(m.get("kill_value"), (int, float)) or isinstance(m.get("kill_value"), bool):
        problems.append(f"{where}: kill_value must be a number")
    srcs = m.get("sources")
    if not isinstance(srcs, list) or not srcs:
        problems.append(f"{where}: at least one source")
    else:
        for i, s in enumerate(srcs):
            if not isinstance(s, dict) or not s.get("where"):
                problems.append(f"{where}: source {i} needs 'where'")
            if s.get("kind", "url") not in ("url", "experiment", "cmd"):
                problems.append(f"{where}: source {i} kind must be url, cmd or experiment")


def check_claims(obj):
    p = []
    claims = obj.get("claims") if isinstance(obj, dict) else None
    if not isinstance(claims, list) or not (3 <= len(claims) <= 6):
        return ["claims: 3 to 6 claims required"]
    ids = set()
    cores = 0
    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            p.append(f"claim {i}: not an object"); continue
        cid = c.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"c[0-9]+", cid):
            p.append(f"claim {i}: id must look like c1")
        elif cid in ids:
            p.append(f"claim {cid}: duplicate id")
        ids.add(cid)
        if not isinstance(c.get("who"), str) or len(c.get("who", "")) < 3:
            p.append(f"claim {cid}: who must name the people")
        for k in ("pain", "statement"):
            if not isinstance(c.get(k), str) or len(c.get(k, "")) < 8:
                p.append(f"claim {cid}: {k} must be a sentence")
        if c.get("core") is True:
            cores += 1
        if any(ch.isdigit() for ch in str(c.get("statement", ""))) and re.search(r"\b(at least|more than|under|over|<|>|≥|≤)\b\s*\d", str(c.get("statement", ""))):
            p.append(f"claim {cid}: the statement carries a number; numbers belong to the setter")
    if cores != 1:
        p.append(f"exactly one claim must be core (got {cores})")
    return p


def check_plan(obj, claims_obj):
    p = []
    claims = obj.get("claims") if isinstance(obj, dict) else None
    if not isinstance(claims, list):
        return ["plan: claims must be a list"]
    want = {c["id"] for c in claims_obj["claims"]}
    got = set()
    for c in claims:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid not in want:
            p.append(f"plan: unknown claim {cid}"); continue
        got.add(cid)
        ms = c.get("measures")
        if not isinstance(ms, list) or not ms:
            p.append(f"plan {cid}: at least one measure"); continue
        names = set()
        for m in ms:
            _measure_ok(m, p, f"plan {cid}")
            if isinstance(m, dict) and m.get("name") in names:
                p.append(f"plan {cid}: duplicate measure {m.get('name')}")
            if isinstance(m, dict):
                names.add(m.get("name"))
    for cid in want - got:
        p.append(f"plan: claim {cid} has no measures")
    return p


def check_skeptic(obj, plan_obj):
    p = []
    claims = obj.get("claims") if isinstance(obj, dict) else None
    if not isinstance(claims, list):
        return ["skeptic: claims must be a list"]
    plan_measures = {c["id"]: {m["name"]: m for m in c["measures"]} for c in plan_obj["claims"]}
    for c in claims:
        cid = c.get("id") if isinstance(c, dict) else None
        if cid not in plan_measures:
            p.append(f"skeptic: unknown claim {cid}"); continue
        kn = c.get("kill_numbers") or {}
        if not isinstance(kn, dict):
            p.append(f"skeptic {cid}: kill_numbers must be an object")
        else:
            for name, v in kn.items():
                if name not in plan_measures[cid] and name not in {m.get("name") for m in (c.get("added_measures") or [])}:
                    p.append(f"skeptic {cid}: kill number on unknown measure {name!r}; measures are the setter's, add your own under added_measures")
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    p.append(f"skeptic {cid}: kill number for {name} must be a number")
        for m in c.get("added_measures") or []:
            _measure_ok(m, p, f"skeptic {cid} added")
        ds = c.get("disconfirming_sources")
        if not isinstance(ds, list) or len(ds) < 2:
            p.append(f"skeptic {cid}: two disconfirming sources required")
        else:
            for i, s in enumerate(ds):
                if not isinstance(s, dict) or not s.get("where"):
                    p.append(f"skeptic {cid}: disconfirming source {i} needs 'where'")
                elif s.get("measure") and s["measure"] not in plan_measures[cid] and s["measure"] not in {m.get("name") for m in (c.get("added_measures") or [])}:
                    p.append(f"skeptic {cid}: disconfirming source {i} names unknown measure {s['measure']!r}")
    return p


def check_judge(obj, ledger_rows):
    p = []
    rows = obj.get("rows") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        return ["judge: rows must be a list"]
    seen = set()
    for r in rows:
        i = r.get("index") if isinstance(r, dict) else None
        if not isinstance(i, int) or i < 0 or i >= len(ledger_rows):
            p.append(f"judge: bad row index {i}"); continue
        seen.add(i)
        if r.get("score") not in (0, 1, 2):
            p.append(f"judge row {i}: score must be 0, 1 or 2")
        if r.get("score", 0) > 0 and not (isinstance(r.get("quote"), str) and r["quote"].strip()):
            p.append(f"judge row {i}: a non-zero score needs the quoted line")
    fetched = {i for i, r in enumerate(ledger_rows) if r.get("source", {}).get("body_hash")}
    for i in fetched - seen:
        p.append(f"judge: fetched row {i} was not judged")
    return p


def cmd_schema(a):
    d = Path(a.dir)
    try:
        if a.file == "claims":
            problems = check_claims(load(d / "claims.json"))
        elif a.file == "plan":
            problems = check_plan(load(d / "plan.json"), load(d / "claims.json"))
        elif a.file == "skeptic":
            problems = check_skeptic(load(d / "skeptic.json"), load(d / "plan.json"))
        elif a.file == "judge":
            problems = check_judge(load(d / "judge.json"), read_ledger(d))
        else:
            print(json.dumps({"ok": False, "error": f"unknown file {a.file}"})); return EXIT_USAGE
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "error": f"{a.file}: {e}"})); return EXIT_NOGO
    if problems:
        print(json.dumps({"ok": False, "problems": problems})); return EXIT_NOGO
    print(json.dumps({"ok": True, "file": a.file})); return 0


# ---------------------------------------------------------------------------
# redact / freeze / check-frozen
# ---------------------------------------------------------------------------

def cmd_redact(a):
    d = Path(a.dir)
    plan = load(d / "plan.json")
    red = {"claims": []}
    for c in plan["claims"]:
        red["claims"].append({"id": c["id"], "measures": [
            {k: v for k, v in m.items() if k != "kill_value"} for m in c["measures"]]})
    dump(d / "plan.redacted.json", red)
    print(json.dumps({"ok": True, "file": str(d / "plan.redacted.json")}))
    return 0


def stricter(direction, a_val, b_val):
    """The kill number harder to clear. direction min: the claim needs value >= kill,
    so the larger kill is stricter; max: the claim needs value <= kill, smaller is stricter."""
    return max(a_val, b_val) if direction == "min" else min(a_val, b_val)


def cmd_freeze(a):
    d = Path(a.dir)
    claims = load(d / "claims.json")
    plan = load(d / "plan.json")
    core_ids = {c["id"] for c in claims["claims"] if c.get("core")}
    skeptic = load(d / "skeptic.json") if (d / "skeptic.json").exists() else {"claims": []}
    sk = {c["id"]: c for c in skeptic.get("claims", [])}
    frozen = {"claims": [], "frozen_at": now(), "band": load(d / "idea.json")["band"]}
    unmatched = []
    for c in plan["claims"]:
        cid = c["id"]
        measures = []
        for m in c["measures"]:
            mm = dict(m)
            mm["set_by"] = "setter"
            s = sk.get(cid, {})
            kn = (s.get("kill_numbers") or {})
            if m["name"] in kn:
                mm["kill_value"] = stricter(m["direction"], m["kill_value"], kn[m["name"]])
                mm["set_by"] = "both" if mm["kill_value"] == m["kill_value"] == kn[m["name"]] else \
                    ("skeptic" if mm["kill_value"] == kn[m["name"]] else "setter")
                mm["setter_value"], mm["skeptic_value"] = m["kill_value"], kn[m["name"]]
            measures.append(mm)
        names = {m["name"] for m in measures}
        for am in (sk.get(cid, {}).get("added_measures") or []):
            if am["name"] in names:
                unmatched.append(f"{cid}: skeptic added measure {am['name']!r} that the setter already has")
                continue
            am = dict(am); am["set_by"] = "skeptic"; measures.append(am); names.add(am["name"])
        for name in (sk.get(cid, {}).get("kill_numbers") or {}):
            if name not in names:
                unmatched.append(f"{cid}: skeptic kill number on {name!r}, not a measure of this claim")
        required = []
        for src in (sk.get(cid, {}).get("disconfirming_sources") or []):
            required.append({"where": src["where"], "extract": src.get("extract", "text"),
                             "measure": src.get("measure"), "note": src.get("note", ""), "required": True})
        frozen["claims"].append({"id": cid, "core": cid in core_ids, "measures": measures, "required_sources": required})
    if unmatched:
        print(json.dumps({"ok": False, "problems": unmatched, "exit": EXIT_HUMAN}))
        return EXIT_HUMAN
    body = json.dumps(frozen, indent=2, sort_keys=True) + "\n"
    (d / "plan.frozen.json").write_text(body, encoding="utf-8")
    digest = sha256_bytes(body.encode("utf-8"))
    (d / "freeze.sha").write_text(digest + "\n")
    print(json.dumps({"ok": True, "sha256": digest, "measures": sum(len(c["measures"]) for c in frozen["claims"]),
                      "required_sources": sum(len(c["required_sources"]) for c in frozen["claims"])}))
    return 0


def frozen_intact(d):
    d = Path(d)
    if not (d / "plan.frozen.json").exists() or not (d / "freeze.sha").exists():
        return False, "no frozen plan"
    want = (d / "freeze.sha").read_text().strip()
    got = sha256_file(d / "plan.frozen.json")
    return (want == got), ("frozen plan was edited after freeze" if want != got else "ok")


def cmd_check_frozen(a):
    ok, why = frozen_intact(a.dir)
    print(json.dumps({"ok": ok, "detail": why}))
    return 0 if ok else EXIT_NOGO


# ---------------------------------------------------------------------------
# ledger, tiers, verdict
# ---------------------------------------------------------------------------

def read_ledger(d):
    p = Path(d) / "ledger.jsonl"
    rows = []
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def passes(direction, value, kill):
    if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return value >= kill if direction == "min" else value <= kill


def refetch(row, fetcher, run_dir):
    """Re-run a re-runnable source. Returns (value, body_hash, reason)."""
    src = row.get("source") or {}
    url = src.get("url")
    if not url:
        return None, None, "no-url"
    cmd = [sys.executable, str(fetcher), "get", url, "--extract", src.get("extracted_by") or "text",
           "--run-dir", str(run_dir)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        out = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
        return None, None, "refetch-failed"
    return out.get("value"), out.get("body_hash"), out.get("reason")


def host_of(url):
    try:
        from urllib.parse import urlsplit
        return urlsplit(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""


def cmd_verdict(a):
    cfg = read_toml(a.toml)
    d = Path(a.dir)
    idea = load(d / "idea.json")
    band = idea["band"]
    frozen = load(d / "plan.frozen.json")
    ok, why = frozen_intact(d)
    if not ok:
        print(json.dumps({"ok": False, "error": why, "exit": EXIT_NOGO}))
        return EXIT_NOGO
    rows = read_ledger(d)
    judge = load(d / "judge.json") if (d / "judge.json").exists() else {"rows": []}
    jscore = {r["index"]: r for r in judge.get("rows", []) if isinstance(r, dict) and "index" in r}
    reachable = load(a.reachable) if a.reachable and Path(a.reachable).exists() else \
        (load(d / "reachable.json") if (d / "reachable.json").exists() else None)
    require = cfg["require"][band]
    per_point = int(cfg["tiers"].get("anecdotal_rows_per_point", 3))
    fetcher = Path(a.fetcher) if a.fetcher else FETCH

    # measures by claim
    measures = {c["id"]: {m["name"]: m for m in c["measures"]} for c in frozen["claims"]}
    required = {c["id"]: c.get("required_sources", []) for c in frozen["claims"]}

    graded = []
    contradictions = []
    infra = []
    for i, row in enumerate(rows):
        g = {"index": i, "claim": row.get("claim"), "measure": row.get("measure"), "value": row.get("value"),
             "unit": row.get("unit"), "origin": row.get("origin"), "url": (row.get("source") or {}).get("url"),
             "extracted_by": (row.get("source") or {}).get("extracted_by"), "kind": (row.get("source") or {}).get("kind", "url"),
             "reason": row.get("reason"), "required": bool(row.get("required")), "tier": 0, "note": ""}
        if g["claim"] not in measures:
            g["note"] = "unknown claim"; graded.append(g); continue
        if row.get("reason"):
            g["note"] = f"unobtainable: {row['reason']}"
            if row["reason"] in ("egress", "timeout") and g["required"]:
                h = host_of(g["url"] or "")
                blocked = bool(reachable and h in reachable.get("hosts", {}) and not reachable["hosts"][h].get("reachable"))
                if blocked or not reachable:
                    infra.append(f"required source {g['url']} unobtainable here ({row['reason']})")
            graded.append(g); continue
        origin = row.get("origin") or ""
        if not ORIGIN_RE.match(origin):
            g["note"] = "origin is not a host or handle"; graded.append(g); continue
        js = jscore.get(i)
        if js is not None and js.get("score") == 0:
            g["note"] = "judge: source does not say this"; graded.append(g); continue
        if g["kind"] == "experiment" and isinstance(g["value"], (int, float)):
            g["tier"] = 3
        elif g["extracted_by"] and g["extracted_by"] != "text" and isinstance(g["value"], (int, float)) and not isinstance(g["value"], bool):
            g["tier"] = 2
            if not a.no_refetch and g["kind"] in ("url", "cmd"):
                v2, h2, r2 = refetch(row, fetcher, d)
                g["refetch"] = {"value": v2, "body_hash": h2, "reason": r2}
                m = measures[g["claim"]].get(g["measure"])
                if v2 is None:
                    g["tier"] = 0; g["note"] = "drift: value absent on re-fetch"
                elif m and passes(m["direction"], g["value"], m["kill_value"]) != passes(m["direction"], v2, m["kill_value"]):
                    g["tier"] = 0; g["note"] = f"drift: value moved across the kill number ({g['value']} -> {v2})"
        elif (row.get("source") or {}).get("body_hash"):
            g["tier"] = 1 if (js is None or js.get("score", 1) >= 1) else 0
            if js is None:
                g["note"] = "unjudged"
        graded.append(g)

    # per-claim status
    claims_out = {}
    for c in frozen["claims"]:
        cid = c["id"]
        st = {"core": c["core"], "required_tier": require["core"] if c["core"] else require["other"],
              "tier": 0, "status": "unobtainable", "killed_by": [], "supported_by": [], "anecdotal_points": 0}
        crows = [g for g in graded if g["claim"] == cid]
        # tier 2/3 rows checked against their measure's kill number
        for g in crows:
            if g["tier"] >= 2 and g["measure"] in measures[cid]:
                m = measures[cid][g["measure"]]
                p = passes(m["direction"], g["value"], m["kill_value"])
                if p is False:
                    st["killed_by"].append(f"{g['measure']}={g['value']} vs {m['direction']} {m['kill_value']} ({g['url']})")
                elif p is True:
                    st["supported_by"].append(f"{g['measure']}={g['value']} clears {m['direction']} {m['kill_value']} ({g['url']})")
                    st["tier"] = max(st["tier"], g["tier"])
            elif g["tier"] >= 2:
                st["supported_by"].append(f"{g['measure']}={g['value']} (no kill number on this measure; informational)")
        # anecdotal points come from the fetcher's own finds; the skeptic's
        # required sources are there to be seen, never to support
        origins = {g["origin"] for g in crows if g["tier"] == 1 and not g["required"]}
        st["anecdotal_points"] = len(origins) // per_point
        if st["tier"] == 0 and st["anecdotal_points"] >= 1:
            st["tier"] = 1
        if st["killed_by"]:
            st["status"] = "killed"
        elif st["tier"] >= st["required_tier"] and st["required_tier"] > 0:
            st["status"] = "supported"
        elif st["required_tier"] == 0:
            st["status"] = "supported" if st["tier"] > 0 else "not-required"
        elif st["tier"] > 0:
            st["status"] = "below-tier"
        # required (skeptic) sources must each have a row
        missing = []
        for rs in required.get(cid, []):
            if not any((g["url"] or "") == rs["where"] or (g["url"] or "").startswith(rs["where"]) for g in crows):
                missing.append(rs["where"])
        if missing:
            st["missing_required"] = missing
            contradictions.append(f"{cid}: required disconfirming sources never fetched: {missing}")
        claims_out[cid] = st

    core = next(v for v in claims_out.values() if v["core"])
    overruled = load(d / "overrule.json") if (d / "overrule.json").exists() else None
    if infra and core["status"] in ("unobtainable", "below-tier"):
        verdict, code = "INFRA", EXIT_INFRA
    elif core["status"] in ("killed", "unobtainable", "below-tier"):
        verdict, code = "NO-GO", EXIT_NOGO
    elif any(v["status"] in ("killed", "below-tier", "unobtainable") for v in claims_out.values() if not v["core"]):
        verdict, code = "PIVOT", EXIT_PIVOT
    else:
        verdict, code = "GO", EXIT_GO
    if contradictions and verdict == "GO":
        verdict, code = "PIVOT", EXIT_PIVOT
    if verdict == "NO-GO" and overruled:
        verdict, code = "GO", EXIT_GO
    prior = load(d / "verdict.json") if (d / "verdict.json").exists() else None
    if verdict == "PIVOT" and prior and prior.get("verdict") == "PIVOT":
        verdict, code = "NO-GO", EXIT_NOGO
        contradictions.append("second PIVOT on the same idea")

    out = {"verdict": verdict, "exit": code, "band": band, "required": require, "claims": claims_out, "rows": graded,
           "contradictions": contradictions, "infra": infra, "overruled": overruled,
           "validated_budget_usd": idea["build_usd"], "idea": idea["idea"], "slug": idea["slug"],
           "computed_at": now(), "has_tier3": any(g["tier"] == 3 for g in graded),
           "frozen_sha256": (d / "freeze.sha").read_text().strip()}
    dump(d / "verdict.json", out)
    (d / "VERDICT.md").write_text(render_md(out, frozen), encoding="utf-8")
    if verdict == "NO-GO":
        append_dead_end(Path(idea["project"]), out)
    print(json.dumps({"ok": True, "verdict": verdict, "exit": code,
                      "claims": {k: v["status"] for k, v in claims_out.items()}, "infra": infra}))
    return code


def render_md(v, frozen):
    lines = [f"# Validation verdict: {v['verdict']}", "",
             f"Idea: {v['idea']}", "",
             f"Band: {v['band']} (validated at ${v['validated_budget_usd']:.0f} build budget) · computed {v['computed_at']}", ""]
    if v.get("overruled"):
        o = v["overruled"]
        lines += [f"**Overruled** by {o.get('by')}: {o.get('reason')} (re-checked by /jg-feedback as {o.get('measure')} {o.get('direction')} {o.get('kill_value')})", ""]
    lines += ["## Claims", ""]
    for c in frozen["claims"]:
        st = v["claims"][c["id"]]
        tag = "core" if c["core"] else "supporting"
        lines.append(f"### {c['id']} ({tag}) — {st['status']} at tier {st['tier']} (needs {st['required_tier']})")
        for m in c["measures"]:
            lines.append(f"- kill: {m['name']} {m['direction']} {m['kill_value']} {m['unit']} (set by {m.get('set_by', 'setter')})")
        for s in st["supported_by"]:
            lines.append(f"- supported: {s}")
        for s in st["killed_by"]:
            lines.append(f"- killed: {s}")
        if st.get("missing_required"):
            lines.append(f"- required sources never fetched: {', '.join(st['missing_required'])}")
        lines.append("")
    lines += ["## Evidence", ""]
    for g in v["rows"]:
        link = f"[{g['url']}]({g['url']})" if g.get("url") else "(no url)"
        val = "" if g["value"] is None else f" = {g['value']} {g.get('unit') or ''}".rstrip()
        lines.append(f"- t{g['tier']} {g['claim']}/{g['measure']}{val} · {link} · origin {g.get('origin')}" + (f" · {g['note']}" if g.get("note") else ""))
    if v["contradictions"]:
        lines += ["", "## Contradictions", ""] + [f"- {c}" for c in v["contradictions"]]
    if v["infra"]:
        lines += ["", "## Blocked here (infra)", ""] + [f"- {c}" for c in v["infra"]] + ["", "Run this validation on a machine that reaches these hosts."]
    return "\n".join(lines) + "\n"


def append_dead_end(project, v):
    p = project / "DEAD_ENDS.md"
    entry = [f"\n---\n### {v['idea'][:90]}", f"**Idea:** {v['idea']}",
             f"**Investigated:** {v['computed_at'][:10]} — /jg-validate, band {v['band']}, {len(v['rows'])} evidence rows, ledger at `.pipeline/validate/{v['slug']}/`.",
             "**Ruled out because:** " + "; ".join(
                 [f"{k} {s['status']}" + (f" ({'; '.join(s['killed_by'])})" if s["killed_by"] else "") for k, s in v["claims"].items()]) + "."]
    if not p.exists():
        p.write_text("# Dead ends\n")
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n".join(entry) + "\n")


def cmd_overrule(a):
    d = Path(a.dir)
    dump(d / "overrule.json", {"by": a.by, "reason": a.reason, "measure": a.measure, "kill_value": float(a.kill_value),
                               "direction": a.direction, "at": now()})
    print(json.dumps({"ok": True, "note": "re-run `validate.py verdict` to apply; /jg-feedback re-checks the number after ship"}))
    return 0


def cmd_handoff(a):
    """Write the GO's anchors, kill numbers and validation block into spec.json."""
    d = Path(a.dir)
    v = load(d / "verdict.json")
    if v["verdict"] != "GO":
        print(json.dumps({"ok": False, "error": f"verdict is {v['verdict']}, nothing to hand off"})); return EXIT_NOGO
    frozen = load(d / "plan.frozen.json")
    claims = load(d / "claims.json")
    spec = load(a.spec) if Path(a.spec).exists() else {}
    spec["anchors"] = [c["statement"] for c in claims["claims"]]
    lines = []
    for c in frozen["claims"]:
        for m in c["measures"]:
            lines.append(f"{c['id']}: {m['name']} stays {'>=' if m['direction'] == 'min' else '<='} {m['kill_value']} {m['unit']}"[:120])
    succ = [s for s in (spec.get("success") or []) if not re.match(r"^c[0-9]+: ", s)]
    spec["success"] = succ + lines
    spec["validation"] = {"slug": v["slug"], "verdict_sha256": sha256_file(d / "verdict.json"),
                          "validated_budget_usd": v["validated_budget_usd"]}
    dump(a.spec, spec)
    print(json.dumps({"ok": True, "anchors": len(spec["anchors"]), "success_lines": len(lines)}))
    return 0


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="validate.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--project", required=True); s.add_argument("--idea", required=True)
    s.add_argument("--build-usd", required=True, type=float); s.add_argument("--toml"); s.set_defaults(func=cmd_init)
    s = sub.add_parser("schema"); s.add_argument("--dir", required=True); s.add_argument("--file", required=True); s.set_defaults(func=cmd_schema)
    s = sub.add_parser("redact"); s.add_argument("--dir", required=True); s.set_defaults(func=cmd_redact)
    s = sub.add_parser("freeze"); s.add_argument("--dir", required=True); s.set_defaults(func=cmd_freeze)
    s = sub.add_parser("check-frozen"); s.add_argument("--dir", required=True); s.set_defaults(func=cmd_check_frozen)
    s = sub.add_parser("verdict"); s.add_argument("--dir", required=True); s.add_argument("--reachable"); s.add_argument("--fetcher")
    s.add_argument("--no-refetch", action="store_true"); s.add_argument("--toml"); s.set_defaults(func=cmd_verdict)
    s = sub.add_parser("overrule"); s.add_argument("--dir", required=True); s.add_argument("--by", required=True); s.add_argument("--reason", required=True)
    s.add_argument("--measure", required=True); s.add_argument("--kill-value", required=True); s.add_argument("--direction", choices=["min", "max"], required=True)
    s.set_defaults(func=cmd_overrule)
    s = sub.add_parser("handoff"); s.add_argument("--dir", required=True); s.add_argument("--spec", required=True); s.set_defaults(func=cmd_handoff)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
