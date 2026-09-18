"""Tests for pipeline/validate.py: the referee never reads the idea, so every
rule here is about shapes, the freeze, tiers and arithmetic."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
VALIDATE = KIT / "validate.py"


def run(args, cwd=None):
    proc = subprocess.run([sys.executable, str(VALIDATE)] + args, capture_output=True, text=True, cwd=cwd)
    out = None
    if proc.stdout.strip():
        try:
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        except ValueError:
            out = {"raw": proc.stdout}
    return proc.returncode, out, proc.stderr


def w(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2) + "\n")


CLAIMS = {"claims": [
    {"id": "c1", "core": True, "who": "solo developers", "pain": "changelogs are written by hand and go stale",
     "statement": "developers want a changelog generated from git history", "metric_hint": "downloads of existing tools"},
    {"id": "c2", "core": False, "who": "maintainers", "pain": "release notes take an hour",
     "statement": "existing tools require conventional commits", "metric_hint": "issues complaining"},
    {"id": "c3", "core": False, "who": "teams", "pain": "nobody reads the changelog",
     "statement": "people would pay for a better one", "metric_hint": "sponsorships"},
]}

PLAN = {"claims": [
    {"id": "c1", "measures": [{"name": "monthly_downloads", "unit": "downloads/month", "direction": "min", "kill_value": 10000,
                               "sources": [{"kind": "url", "where": "https://registry.npmjs.org/-/v1/search?text=changelog", "extract": "json:objects[0].downloads.monthly"}]}]},
    {"id": "c2", "measures": [{"name": "issues_mentioning", "unit": "issues", "direction": "min", "kill_value": 5,
                               "sources": [{"kind": "url", "where": "https://example.test/issues", "extract": "count:conventional"}]}]},
    {"id": "c3", "measures": [{"name": "sponsors", "unit": "sponsors", "direction": "min", "kill_value": 3,
                               "sources": [{"kind": "url", "where": "https://example.test/sponsors", "extract": "json:count"}]}]},
]}

SKEPTIC = {"claims": [
    {"id": "c1", "kill_numbers": {"monthly_downloads": 50000}, "added_measures": [],
     "disconfirming_sources": [{"where": "https://example.test/dead-tools", "extract": "text", "note": "abandoned changelog tools"},
                               {"where": "https://example.test/complaints", "extract": "text"}]},
    {"id": "c2", "kill_numbers": {"issues_mentioning": 2}, "added_measures": [],
     "disconfirming_sources": [{"where": "https://example.test/a"}, {"where": "https://example.test/b"}]},
    {"id": "c3", "kill_numbers": {}, "added_measures": [
        {"name": "paying_users_of_nearest_competitor", "unit": "users", "direction": "min", "kill_value": 100,
         "sources": [{"kind": "url", "where": "https://example.test/competitor", "extract": "json:paying"}]}],
     "disconfirming_sources": [{"where": "https://example.test/c"}, {"where": "https://example.test/d"}]},
]}


def project(tmp_path, build_usd=50):
    rc, out, err = run(["init", "--project", str(tmp_path), "--idea", "a changelog generator from git history", "--build-usd", str(build_usd)])
    assert rc == 0, err
    return Path(out["dir"]), out


def row(claim, measure, value, url, origin="example.test", extracted_by="json:x", reason=None, required=False, kind="url", body=True):
    return {"claim": claim, "measure": measure,
            "source": {"kind": kind, "url": url, "extracted_by": extracted_by, "body_hash": ("h" * 64 if body and not reason else None), "body_path": None},
            "value": value, "unit": None, "date": "2026-09-18T00:00:00Z", "origin": origin, "required": required, "note": "", "reason": reason}


def ledger(d, rows):
    (d / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def judge_all(d, score=2):
    rows = [json.loads(l) for l in (d / "ledger.jsonl").read_text().splitlines() if l.strip()]
    w(d / "judge.json", {"rows": [{"index": i, "score": score, "quote": "the page says so"} for i, r in enumerate(rows) if r["source"].get("body_hash")]})


# ---------------------------------------------------------------------------
# init and bands
# ---------------------------------------------------------------------------

def test_init_bands_and_roles(tmp_path):
    d, out = project(tmp_path, 10)
    assert out["band"] == "small" and out["roles"] == ["author", "setter", "fetcher", "referee"] and out["cap_usd"] == 4.5
    assert len(out["slug"]) == 8 and (d / "idea.json").exists() and (d / "bodies").is_dir()
    _, out2 = project(tmp_path / "b", 50)
    assert out2["band"] == "mid" and "skeptic" in out2["roles"] and "judge" in out2["roles"]
    _, out3 = project(tmp_path / "c", 500)
    assert out3["band"] == "large"


def test_same_idea_same_slug(tmp_path):
    _, a = project(tmp_path / "a"); _, b = project(tmp_path / "b")
    assert a["slug"] == b["slug"]


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------

def test_claims_schema_needs_one_core_and_no_numbers(tmp_path):
    d, _ = project(tmp_path)
    bad = json.loads(json.dumps(CLAIMS)); bad["claims"][1]["core"] = True
    w(d / "claims.json", bad)
    rc, out, _ = run(["schema", "--dir", str(d), "--file", "claims"])
    assert rc == 2 and any("exactly one claim must be core" in p for p in out["problems"])
    bad = json.loads(json.dumps(CLAIMS)); bad["claims"][0]["statement"] = "developers want at least 5000 downloads"
    w(d / "claims.json", bad)
    rc, out, _ = run(["schema", "--dir", str(d), "--file", "claims"])
    assert rc == 2 and any("numbers belong to the setter" in p for p in out["problems"])
    w(d / "claims.json", CLAIMS)
    assert run(["schema", "--dir", str(d), "--file", "claims"])[0] == 0


def test_plan_schema_covers_every_claim_with_numbers(tmp_path):
    d, _ = project(tmp_path); w(d / "claims.json", CLAIMS)
    bad = json.loads(json.dumps(PLAN)); del bad["claims"][2]
    w(d / "plan.json", bad)
    rc, out, _ = run(["schema", "--dir", str(d), "--file", "plan"])
    assert rc == 2 and any("c3 has no measures" in p for p in out["problems"])
    bad = json.loads(json.dumps(PLAN)); bad["claims"][0]["measures"][0]["kill_value"] = "lots"
    w(d / "plan.json", bad)
    rc, out, _ = run(["schema", "--dir", str(d), "--file", "plan"])
    assert rc == 2 and any("kill_value must be a number" in p for p in out["problems"])
    w(d / "plan.json", PLAN)
    assert run(["schema", "--dir", str(d), "--file", "plan"])[0] == 0


def test_skeptic_schema_two_disconfirming_sources_and_known_measures(tmp_path):
    d, _ = project(tmp_path); w(d / "claims.json", CLAIMS); w(d / "plan.json", PLAN)
    bad = json.loads(json.dumps(SKEPTIC)); bad["claims"][0]["disconfirming_sources"] = [{"where": "https://x"}]
    w(d / "skeptic.json", bad)
    rc, out, _ = run(["schema", "--dir", str(d), "--file", "skeptic"])
    assert rc == 2 and any("two disconfirming sources" in p for p in out["problems"])
    bad = json.loads(json.dumps(SKEPTIC)); bad["claims"][0]["kill_numbers"] = {"vibes": 3}
    w(d / "skeptic.json", bad)
    rc, out, _ = run(["schema", "--dir", str(d), "--file", "skeptic"])
    assert rc == 2 and any("unknown measure 'vibes'" in p for p in out["problems"])
    w(d / "skeptic.json", SKEPTIC)
    assert run(["schema", "--dir", str(d), "--file", "skeptic"])[0] == 0


# ---------------------------------------------------------------------------
# redact and freeze
# ---------------------------------------------------------------------------

def test_redaction_removes_every_kill_value(tmp_path):
    d, _ = project(tmp_path); w(d / "plan.json", PLAN)
    assert run(["redact", "--dir", str(d)])[0] == 0
    red = (d / "plan.redacted.json").read_text()
    assert "kill_value" not in red and "monthly_downloads" in red


def test_freeze_takes_the_stricter_number_and_adds_skeptic_measures(tmp_path):
    d, _ = project(tmp_path); w(d / "claims.json", CLAIMS); w(d / "plan.json", PLAN); w(d / "skeptic.json", SKEPTIC)
    rc, out, err = run(["freeze", "--dir", str(d)])
    assert rc == 0, err
    fz = json.loads((d / "plan.frozen.json").read_text())
    c1 = next(c for c in fz["claims"] if c["id"] == "c1")
    m = c1["measures"][0]
    assert m["kill_value"] == 50000 and m["set_by"] == "skeptic" and m["setter_value"] == 10000
    assert c1["core"] is True and len(c1["required_sources"]) == 2
    c2 = next(c for c in fz["claims"] if c["id"] == "c2")
    assert c2["measures"][0]["kill_value"] == 5 and c2["measures"][0]["set_by"] == "setter"  # min: 5 is stricter than 2
    c3 = next(c for c in fz["claims"] if c["id"] == "c3")
    assert [mm["name"] for mm in c3["measures"]] == ["sponsors", "paying_users_of_nearest_competitor"]
    assert (d / "freeze.sha").read_text().strip() and out["required_sources"] == 6


def test_freeze_rejects_a_kill_number_on_an_unmatched_measure(tmp_path):
    d, _ = project(tmp_path); w(d / "claims.json", CLAIMS); w(d / "plan.json", PLAN)
    bad = json.loads(json.dumps(SKEPTIC)); bad["claims"][1]["kill_numbers"] = {"complaints_per_week": 4}
    w(d / "skeptic.json", bad)
    rc, out, _ = run(["freeze", "--dir", str(d)])
    assert rc == 3 and any("not a measure" in p for p in out["problems"])


def test_editing_the_frozen_plan_is_caught(tmp_path):
    d, _ = project(tmp_path); w(d / "claims.json", CLAIMS); w(d / "plan.json", PLAN); w(d / "skeptic.json", SKEPTIC)
    run(["freeze", "--dir", str(d)])
    assert run(["check-frozen", "--dir", str(d)])[0] == 0
    fz = json.loads((d / "plan.frozen.json").read_text()); fz["claims"][0]["measures"][0]["kill_value"] = 1
    w(d / "plan.frozen.json", fz)
    rc, out, _ = run(["check-frozen", "--dir", str(d)])
    assert rc == 2 and "edited after freeze" in out["detail"]
    ledger(d, []); rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 2 and "edited" in out["error"]


# ---------------------------------------------------------------------------
# the verdict arithmetic
# ---------------------------------------------------------------------------

def frozen_project(tmp_path, build_usd=50, skeptic=True):
    d, _ = project(tmp_path, build_usd)
    w(d / "claims.json", CLAIMS); w(d / "plan.json", PLAN)
    if skeptic:
        w(d / "skeptic.json", SKEPTIC)
    assert run(["freeze", "--dir", str(d)])[0] == 0
    return d


def good_rows():
    return [
        row("c1", "monthly_downloads", 120000, "https://registry.npmjs.org/-/v1/search?text=changelog", origin="registry.npmjs.org"),
        row("c1", None, None, "https://example.test/dead-tools", extracted_by="text", origin="example.test", required=True),
        row("c1", None, None, "https://example.test/complaints", extracted_by="text", origin="forum.example", required=True),
        row("c2", "issues_mentioning", 9, "https://example.test/issues", extracted_by="count:conventional", origin="example.test"),
        row("c2", None, None, "https://example.test/a", extracted_by="text", origin="a.example", required=True),
        row("c2", None, None, "https://example.test/b", extracted_by="text", origin="b.example", required=True),
        row("c3", "sponsors", 4, "https://example.test/sponsors", origin="example.test"),
        row("c3", "paying_users_of_nearest_competitor", 250, "https://example.test/competitor", origin="competitor.example"),
        row("c3", None, None, "https://example.test/c", extracted_by="text", origin="c.example", required=True),
        row("c3", None, None, "https://example.test/d", extracted_by="text", origin="d.example", required=True),
    ]


def test_go_when_every_claim_clears_its_frozen_number(tmp_path):
    d = frozen_project(tmp_path)
    ledger(d, good_rows()); judge_all(d)
    rc, out, err = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 0, err
    assert out["verdict"] == "GO" and out["claims"] == {"c1": "supported", "c2": "supported", "c3": "supported"}
    v = json.loads((d / "verdict.json").read_text())
    assert v["claims"]["c1"]["tier"] == 2 and v["validated_budget_usd"] == 50 and v["frozen_sha256"]
    md = (d / "VERDICT.md").read_text()
    assert "# Validation verdict: GO" in md and "https://registry.npmjs.org" in md and "set by skeptic" in md


def test_core_below_the_stricter_number_is_no_go_and_lands_in_dead_ends(tmp_path):
    d = frozen_project(tmp_path)
    rows = good_rows(); rows[0]["value"] = 20000  # clears the setter's 10000, not the skeptic's 50000
    ledger(d, rows); judge_all(d)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 2 and out["verdict"] == "NO-GO" and out["claims"]["c1"] == "killed"
    dead = (tmp_path / "DEAD_ENDS.md").read_text()
    assert "changelog generator" in dead and "monthly_downloads=20000" in dead


def test_non_core_killed_is_pivot_and_a_second_pivot_is_no_go(tmp_path):
    d = frozen_project(tmp_path)
    rows = good_rows(); rows[6]["value"] = 1  # sponsors below 3
    ledger(d, rows); judge_all(d)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 5 and out["verdict"] == "PIVOT" and out["claims"]["c3"] == "killed"
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 2 and out["verdict"] == "NO-GO"


def test_core_never_satisfied_by_anecdotes(tmp_path):
    d = frozen_project(tmp_path)
    rows = [r for r in good_rows() if r["claim"] != "c1"]
    rows += [row("c1", None, None, f"https://forum{i}.example/post", extracted_by="text", origin=f"user{i}") for i in range(4)]
    ledger(d, rows); judge_all(d)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 2 and out["claims"]["c1"] == "below-tier"


def test_three_independent_anecdotes_make_one_point_same_origin_does_not(tmp_path):
    d = frozen_project(tmp_path)
    rows = [r for r in good_rows() if r["claim"] != "c2"]
    rows += [row("c2", None, None, f"https://example.test/p{i}", extracted_by="text", origin="example.test") for i in range(3)]
    rows += [row("c2", None, None, "https://example.test/a", extracted_by="text", origin="example.test", required=True),
             row("c2", None, None, "https://example.test/b", extracted_by="text", origin="example.test", required=True)]
    ledger(d, rows); judge_all(d)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert out["claims"]["c2"] == "unobtainable" and rc == 5
    rows = [r for r in good_rows() if r["claim"] != "c2"]
    rows += [row("c2", None, None, f"https://s{i}.example/p", extracted_by="text", origin=f"s{i}.example") for i in range(3)]
    rows += [row("c2", None, None, "https://example.test/a", extracted_by="text", origin="a.example", required=True),
             row("c2", None, None, "https://example.test/b", extracted_by="text", origin="b.example", required=True)]
    ledger(d, rows); judge_all(d)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 0 and out["claims"]["c2"] == "supported"


def test_judge_zero_drops_the_row(tmp_path):
    d = frozen_project(tmp_path)
    rows = [r for r in good_rows() if r["claim"] != "c2"]
    rows += [row("c2", None, None, f"https://s{i}.example/p", extracted_by="text", origin=f"s{i}.example") for i in range(3)]
    rows += [row("c2", None, None, "https://example.test/a", extracted_by="text", origin="a.example", required=True),
             row("c2", None, None, "https://example.test/b", extracted_by="text", origin="b.example", required=True)]
    ledger(d, rows)
    judge_all(d)
    j = json.loads((d / "judge.json").read_text())
    for r in j["rows"]:
        if rows[r["index"]]["claim"] == "c2" and rows[r["index"]]["origin"] == "s0.example":
            r["score"] = 0; r["quote"] = ""
    w(d / "judge.json", j)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert out["claims"]["c2"] != "supported"


def test_missing_required_source_is_a_contradiction_not_a_go(tmp_path):
    d = frozen_project(tmp_path)
    rows = [r for r in good_rows() if r["source"]["url"] != "https://example.test/dead-tools"]
    ledger(d, rows); judge_all(d)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 5
    v = json.loads((d / "verdict.json").read_text())
    assert any("dead-tools" in c for c in v["contradictions"])


def test_blocked_required_source_is_infra_not_no_go(tmp_path):
    d = frozen_project(tmp_path)
    rows = good_rows()
    rows[0] = row("c1", "monthly_downloads", None, "https://registry.npmjs.org/-/v1/search?text=changelog", origin="registry.npmjs.org", reason="egress", required=True)
    rows[1] = row("c1", None, None, "https://example.test/dead-tools", extracted_by="text", origin="example.test", required=True, reason="egress")
    ledger(d, rows); judge_all(d)
    w(d / "reachable.json", {"hosts": {"example.test": {"reachable": False, "reason": "egress"}, "registry.npmjs.org": {"reachable": False, "reason": "egress"}}})
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 4 and out["verdict"] == "INFRA" and out["infra"]
    assert not (tmp_path / "DEAD_ENDS.md").exists()


def test_origin_free_text_is_tier_zero(tmp_path):
    d = frozen_project(tmp_path)
    rows = good_rows(); rows[0]["origin"] = "some guy on a forum"
    ledger(d, rows); judge_all(d)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 2 and out["claims"]["c1"] in ("unobtainable", "below-tier")


def test_refetch_drift_across_the_kill_number_drops_the_row(tmp_path):
    d = frozen_project(tmp_path)
    ledger(d, good_rows()); judge_all(d)
    fake = tmp_path / "fake_fetch.py"
    fake.write_text("import json,sys\nu=sys.argv[2]\nv=10 if 'npmjs' in u else 999\nprint(json.dumps({'value':v,'body_hash':'b'*64,'reason':None}))\n")
    rc, out, _ = run(["verdict", "--dir", str(d), "--fetcher", str(fake)])
    assert rc == 2 and out["claims"]["c1"] in ("unobtainable", "below-tier")
    v = json.loads((d / "verdict.json").read_text())
    assert any("drift" in g["note"] for g in v["rows"])


def test_refetch_that_still_clears_keeps_the_row_even_if_the_value_moved(tmp_path):
    d = frozen_project(tmp_path)
    ledger(d, good_rows()); judge_all(d)
    fake = tmp_path / "fake_fetch.py"
    fake.write_text("import json,sys\nprint(json.dumps({'value':777777,'body_hash':'c'*64,'reason':None}))\n")
    rc, out, _ = run(["verdict", "--dir", str(d), "--fetcher", str(fake)])
    assert rc == 0 and out["verdict"] == "GO"


def test_small_band_needs_only_the_core_at_tier_two(tmp_path):
    d = frozen_project(tmp_path, build_usd=10, skeptic=False)
    rows = [row("c1", "monthly_downloads", 120000, "https://registry.npmjs.org/x", origin="registry.npmjs.org")]
    ledger(d, rows)
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 0 and out["verdict"] == "GO" and out["claims"]["c2"] == "not-required"


def test_overrule_turns_no_go_into_go_and_is_recorded(tmp_path):
    d = frozen_project(tmp_path)
    rows = good_rows(); rows[0]["value"] = 20000
    ledger(d, rows); judge_all(d)
    assert run(["verdict", "--dir", str(d), "--no-refetch"])[0] == 2
    rc, _, _ = run(["overrule", "--dir", str(d), "--by", "jackson", "--reason", "I have three paying users already",
                    "--measure", "paying_users", "--kill-value", "3", "--direction", "min"])
    assert rc == 0
    rc, out, _ = run(["verdict", "--dir", str(d), "--no-refetch"])
    assert rc == 0 and out["verdict"] == "GO"
    assert "Overruled" in (d / "VERDICT.md").read_text()


def test_handoff_writes_anchors_success_lines_and_validation_block(tmp_path):
    d = frozen_project(tmp_path)
    ledger(d, good_rows()); judge_all(d)
    assert run(["verdict", "--dir", str(d), "--no-refetch"])[0] == 0
    spec = tmp_path / "spec.json"; w(spec, {"success": ["Every acceptance check passes"], "budget": {"build_usd": 40}})
    rc, out, _ = run(["handoff", "--dir", str(d), "--spec", str(spec)])
    assert rc == 0
    s = json.loads(spec.read_text())
    assert len(s["anchors"]) == 3 and s["validation"]["slug"] and len(s["validation"]["verdict_sha256"]) == 64
    assert s["validation"]["validated_budget_usd"] == 50
    assert any(l.startswith("c1: monthly_downloads stays >= 50000") for l in s["success"]) and s["success"][0] == "Every acceptance check passes"
