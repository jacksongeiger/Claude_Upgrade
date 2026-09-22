"""Tests for ui_check.py — findings from synthetic ui_measure captures and a
tmp project's source, merge/score, the backlog feed, the judge's grounding,
and the Nightshift scorer contract. Offline, deterministic (no browser).

    discovery/venv/bin/python -m pytest pipeline/tests/test_ui_check.py -q -p no:cacheprovider
"""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
LOOP = KIT.parent / "loop"
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(LOOP))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, KIT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


uk = _load("ui_check")
ud = _load("ui_direction")
backlog_io = uk.backlog_io
TELLS = uk.load_tells()
CALM = ud.propose("calm-dense", semantic=True)

CLEAN = {
    "url": "http://127.0.0.1:5173/", "viewport": "1440x900", "theme": "light", "pageBg": "#ffffff",
    "profile": {"bodyPx": 14, "chars": 2000, "sizesUsed": [12, 14, 16, 18, 20, 22, 25], "scaleRatio": 1.125,
                "largestStep": 1.14, "dominantWeight": "400", "dominantWeightSharePct": 80, "typefaces": 1,
                "distinctShadows": 1, "maxShadowBlurPx": 8, "accentHues": 1, "hasMotion": True,
                "spacingOn4Pct": 98, "distinctSpacing": 8, "distinctTextColors": 3, "dominantRadiusPx": 6,
                "centeredTextPct": 5},
    "type": {"sizesByChars": [{"value": 14, "count": 1800}, {"value": 20, "count": 200}],
             "families": [{"value": "system-ui", "count": 40}]},
    "layout": {"measureCpl": 66},
    "surface": {"cardNestingDepth": 1, "sideStripes": 0, "glowShadows": 0},
    "motion": {"transitionAll": 0, "easeIn": 0, "bounce": 0, "over300ms": 0, "reducedMotionRule": True},
    "targets": {"under24": 0, "wrappedLabels": 0},
    "focus": {"tabbed": 6, "visibleFocus": 6, "missing": []},
    "overflow": {"horizontal": False, "clippedText": 0},
    "stress": {"horizontal": False, "clippedText": 0},
    "mobile": {"viewportMeta": "width=device-width, initial-scale=1", "blocksZoom": False, "smallInputs": 0},
    "contrast": {"failures": [], "unknown": 0},
    "color": {"accents": ["#5b5bd6"]},
    "consoleErrors": [],
    "tells": {"emojiIcons": [], "purpleBlueGradientHero": False, "gradientText": 0, "emDashes": 0, "placeholders": []},
    "census": [{"sel": "main > h1", "role": "heading"}, {"sel": "header > button.btn", "role": "action"}],
}


def cap(**changes):
    c = copy.deepcopy(CLEAN)
    for key, val in changes.items():
        if isinstance(val, dict) and isinstance(c.get(key), dict):
            c[key].update(val)
        else:
            c[key] = val
    return c


def findings(c, tokens=CALM, where="/"):
    return uk.capture_findings(c, where, uk.targets_of(tokens), tokens, TELLS)


def rules(fs, kind=None):
    return sorted(f["rule"] for f in fs if kind is None or f["kind"] == kind)


# ---------------------------------------------------------------- measured findings

def test_clean_capture_has_no_findings():
    assert findings(CLEAN) == []
    # without a direction the floor still holds; the only row asks for one
    no_dir = cap(profile={"largestStep": 1.25})
    fs = findings(no_dir, tokens=None)
    assert rules(fs, "floor") == []
    assert rules(fs) == ["no-direction"]


def test_contrast_failures_make_one_finding_with_the_worst_ratio():
    c = cap(contrast={"failures": [
        {"fg": "#777777", "bg": "#ffffff", "ratio": 4.48, "need": 4.5, "count": 9, "example": "span.meta"},
        {"fg": "#ffffff", "bg": "#6366f1", "ratio": 1.4, "need": 3, "count": 2, "example": "a.btn"},
        {"fg": "#aaaaaa", "bg": "#ffffff", "ratio": 2.32, "need": 4.5, "count": 3, "example": "p.muted"},
    ], "unknown": 0})
    fs = [f for f in findings(c) if f["rule"] == "contrast"]
    assert len(fs) == 1
    f = fs[0]
    assert f["kind"] == "floor" and f["severity"] == "major"
    assert f["title"].startswith("3 text/background pairs") and "worst 1.4:1" in f["title"]
    assert "#ffffff on #6366f1" in f["title"]
    assert f["evidence"][0].startswith("1440x900 light: 1.4:1")


def test_motion_counters():
    c = cap(motion={"transitionAll": 2, "easeIn": 1, "easeInExamples": ["a.btn: all ease-in"], "bounce": 1,
                    "bounceExamples": ["b"], "over300ms": 3, "slowExamples": ["c 500ms"], "reducedMotionRule": False,
                    "ungatedHoverTransforms": 1})
    got = set(rules(findings(c)))
    assert {"transition-all", "ease-in", "bounce-easing", "slow-motion", "no-reduced-motion", "ungated-hover"} <= got
    # a reduced-motion rule, or sheets the page wouldn't let us read, and there's no claim
    assert "no-reduced-motion" not in rules(findings(cap(motion={"reducedMotionRule": True})))
    assert "no-reduced-motion" not in rules(findings(cap(motion={"reducedMotionRule": False, "unreadableSheets": 2})))
    assert "no-reduced-motion" not in rules(findings(cap(profile={"hasMotion": False}, motion={"reducedMotionRule": False})))


def test_mobile_capture():
    c = cap(viewport="375x812",
            mobile={"viewportMeta": "width=device-width, maximum-scale=1", "blocksZoom": True, "smallInputs": 2,
                    "smallInputExamples": ["form > input 13px"]},
            overflow={"horizontal": True, "offenders": ["div.wide +225px"], "clippedText": 0},
            stress={"horizontal": True, "clippedText": 0})
    got = rules(findings(c))
    assert {"zoom-blocked", "small-inputs", "h-overflow"} <= set(got)
    assert "stress-breaks" not in got  # already overflowing before the stress pass
    h = next(f for f in findings(c) if f["rule"] == "h-overflow")
    assert "375px" in h["title"] and any("div.wide" in e for e in h["evidence"])
    # body size and the direction's body are desktop questions
    small = cap(viewport="375x812", profile={"bodyPx": 12})
    assert not {"small-body", "off-body"} & set(rules(findings(small)))
    assert "no-viewport-meta" in rules(findings(cap(viewport="375x812", mobile={"viewportMeta": None})))


def test_floor_rules_from_the_rest_of_a_capture():
    c = cap(profile={"bodyPx": 12, "distinctShadows": 6, "maxShadowBlurPx": 40, "typefaces": 5},
            layout={"measureCpl": 110}, surface={"cardNestingDepth": 3},
            targets={"under24": 3, "examples": ["div.icons > button 16×16"]},
            focus={"tabbed": 6, "visibleFocus": 2, "missing": ["a"]},
            overflow={"horizontal": False, "clippedText": 1}, consoleErrors=["TypeError: x"],
            tells={"placeholders": ["p: Lorem ipsum"]})
    got = set(rules(findings(c)))
    assert {"small-body", "heavy-shadows", "too-many-fonts", "long-lines", "nested-cards", "small-targets",
            "no-focus-ring", "clipped-text", "console-errors", "placeholder-copy"} <= got
    ring = next(f for f in findings(c) if f["rule"] == "no-focus-ring")
    assert ring["title"].startswith("4 of 6")


def test_direction_findings_hold_the_page_to_its_direction():
    c = cap(profile={"bodyPx": 16, "scaleRatio": 1.333, "dominantRadiusPx": 20, "spacingOn4Pct": 60,
                     "distinctTextColors": 9, "sizesUsed": list(range(10, 30, 2))},
            color={"accents": ["#5b5bd6", "#ff6a00"]})
    got = set(rules(findings(c), "direction"))
    assert {"off-body", "off-ratio", "too-many-sizes", "off-grid", "too-many-text-colors", "radius-over", "off-palette"} <= got
    stray = next(f for f in findings(c) if f["rule"] == "off-palette")
    assert "#ff6a00" in stray["evidence"][0] and "#5b5bd6" not in stray["evidence"][0]


def test_semantic_colours_of_the_direction_are_not_extra_hues():
    light = CALM["colors"]["light"]
    ours = cap(profile={"accentHues": 3}, color={"accents": [light["accent"], light["success-text"], light["danger-text"]]})
    assert "too-many-hues" not in rules(findings(ours))
    # without a direction there's no telling them apart
    assert "too-many-hues" in rules(findings(ours, tokens=None))
    # hues the direction doesn't have still count
    foreign = cap(profile={"accentHues": 3}, color={"accents": [light["accent"], "#0891b2", "#c026d3"]})
    assert "too-many-hues" in rules(findings(foreign))


def test_fashion_tells_are_advisory():
    c = cap(tells={"emojiIcons": ["h1: ✨"], "purpleBlueGradientHero": True, "gradientText": 1, "emDashes": 9},
            color={"accents": ["#6366f1"]}, type={"families": [{"value": "Inter"}], "sizesByChars": []},
            profile={"centeredTextPct": 90}, surface={"sideStripes": 1, "glowShadows": 2})
    fashion = rules(findings(c, tokens=None), "fashion")
    assert fashion == sorted(["emoji-icons", "gradient-hero", "gradient-text", "ai-purple", "overused-font",
                              "centered-everything", "em-dashes", "side-stripe", "glow-shadow"])
    # a family the direction chose on purpose is not a tell
    chose_inter = ud.propose("calm-dense", font="Inter")
    assert "overused-font" not in rules(findings(c, tokens=chose_inter))


def test_dark_findings():
    both = ud.propose("precise", theme="both")
    light, dark = cap(theme="light", pageBg="#ffffff"), cap(theme="dark", pageBg="#ffffff")
    assert rules(uk.dark_findings([light, dark], "/", both)) == ["no-dark"]
    assert uk.dark_findings([light, cap(theme="dark", pageBg="#111111")], "/", both) == []
    assert uk.dark_findings([light, dark], "/", ud.propose("precise", theme="light")) == []


# ---------------------------------------------------------------- merge + score

def test_fashion_and_judged_never_move_the_score():
    fashion = [uk.finding(r, "/", "x") for r in ("emoji-icons", "gradient-hero", "overused-font", "shadcn-defaults")]
    judged = [{"rule": "judged-hierarchy", "kind": "judged", "severity": "major", "where": "/"}]
    assert uk.score(fashion + judged) == 100
    floor = [uk.finding("contrast", "/", "a"), uk.finding("small-inputs", "/", "b")]
    assert uk.score(floor) == 100 - 10 - 4
    assert uk.score(floor + fashion + judged) == uk.score(floor)


def test_rule_cap_bounds_one_rule():
    many = [uk.finding("contrast", f"/r{i}", "a") for i in range(5)]
    assert uk.score(many) == 100 - uk.RULE_CAP
    many += [uk.finding("hardcoded-color", f"src/c{i}.tsx", "x") for i in range(10)]
    assert uk.score(many) == 100 - 2 * uk.RULE_CAP
    assert uk.score([uk.finding(r, f"/{i}", "x") for i in range(20) for r in ("contrast", "h-overflow", "zoom-blocked",
                                                                          "small-targets", "no-focus-ring", "h-overflow")]) >= 0


def test_merge_dedupes_by_rule_and_where():
    fs = [uk.finding("contrast", "/", "minor one", ["1440 light: a"], severity="minor"),
          uk.finding("contrast", "/", "the major one", ["375 light: b", "1440 light: a"]),
          uk.finding("contrast", "/inbox", "elsewhere"),
          uk.finding("emoji-icons", "/", "tell"),
          uk.finding("off-body", "/", "direction")]
    merged = uk.merge(fs)
    assert [(f["rule"], f["where"]) for f in merged] == [("contrast", "/"), ("contrast", "/inbox"), ("off-body", "/"), ("emoji-icons", "/")]
    top = merged[0]
    assert top["severity"] == "major" and top["title"] == "the major one"
    assert top["evidence"] == ["1440 light: a", "375 light: b"]
    assert top["id"] == uk.fid(top) and top["id"].startswith("ui-")
    assert len({f["id"] for f in merged}) == len(merged)


# ---------------------------------------------------------------- static findings

def make_project(root, generated=False, components_json=True):
    (root / "src" / "components").mkdir(parents=True)
    (root / "src" / "components" / "Card.tsx").write_text(
        'export function Card() {\n  return <div style={{ color: "#ff0000" }} className="p-4 transition-all">hi</div>\n}\n')
    (root / "src" / "components" / "List.tsx").write_text(
        'import { useEffect, useState } from "react"\n'
        "export function List() {\n  const [rows, setRows] = useState([])\n"
        '  useEffect(() => { fetch("/api/rows").then(r => r.json()).then(setRows) }, [])\n'
        "  return <ul>{rows.map(r => <li key={r.id}>{r.name}</li>)}</ul>\n}\n")
    (root / "src" / "components" / "Good.tsx").write_text(
        "export function Good({ items, isLoading, error }) {\n  if (isLoading) return <p>Loading</p>\n"
        "  if (error) return <p>Something went wrong</p>\n  if (items.length === 0) return <p>No items yet</p>\n"
        "  return <ul>{items.map(i => <li key={i}>{i}</li>)}</ul>\n}\n")
    (root / "src" / "styles.css").write_text(":root {\n  --x: #fff;\n  --brand: rgb(10 20 30);\n}\n"
                                             ".a { color: var(--x); padding: var(--space-4); }\n")
    (root / "src" / "tokens.css").write_text(".t { color: #123456; }\n")  # a token file may hold literals
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / "node_modules" / "lib" / "x.css").write_text(".x { color: #abcdef; transition: all 1s; }\n")
    if generated:
        (root / "src" / "direction.css").write_text(ud.css(CALM) + ".b { transition: all 1s ease-in; color: #123456; }\n")
    if components_json:
        (root / "components.json").write_text(json.dumps({"style": "new-york", "tailwind": {"baseColor": "zinc"}}))


def by_place(fs):
    out = {}
    for f in fs:
        out.setdefault(f["where"], set()).add(f["rule"])
    return out


def test_static_findings(tmp_path):
    make_project(tmp_path)
    fs, meta = uk.static_findings(tmp_path, CALM, TELLS)
    places = by_place(fs)
    assert places["src/components/Card.tsx"] == {"hardcoded-color", "transition-all"}
    assert places["src/components/List.tsx"] == {"missing-states"}
    missing = next(f for f in fs if f["rule"] == "missing-states")
    assert "loading, error, empty" in missing["title"]
    assert "src/components/Good.tsx" not in places  # states named: nothing to say
    assert "src/styles.css" not in places  # custom-property lines are tokens, not literals
    assert "src/tokens.css" not in places
    assert not any(p.startswith("node_modules") for p in places)
    assert places["components.json"] == {"shadcn-defaults"}
    assert meta == {"reduced_motion_in_source": False}


def test_static_findings_skip_the_directions_own_css(tmp_path):
    make_project(tmp_path, generated=True)
    fs, _ = uk.static_findings(tmp_path, CALM, TELLS)
    places = by_place(fs)
    assert "src/direction.css" not in places
    assert "components.json" not in places  # the direction's tokens.css is in: not shadcn's defaults


def test_static_findings_more_rules(tmp_path):
    (tmp_path / "app.css").write_text(".c { transition: all 200ms ease-in; height: 100vh; }\n"
                                      ".d { font-size: 13px; padding: 12px 18px; }\n"
                                      "@keyframes pop { from { transform: scale(0) } }\n"
                                      "@media (prefers-reduced-motion: reduce) { .c { transition: none } }\n")
    (tmp_path / "index.html").write_text('<meta name="viewport" content="width=device-width, user-scalable=no">\n')
    (tmp_path / "Rows.jsx").write_text("export const Rows = ({ rows }) => <ul>{rows.map(r => <li>{r}</li>)}</ul>\n")
    (tmp_path / "Tw.tsx").write_text('export const T = () => <div className="bg-indigo-500 h-screen p-[13px] text-[#333]" />\n')
    fs, meta = uk.static_findings(tmp_path, None, TELLS)
    places = by_place(fs)
    assert {"transition-all", "ease-in", "vh-units", "hardcoded-px", "scale-zero"} <= places["app.css"]
    assert places["index.html"] == {"zoom-blocked"}
    assert places["Rows.jsx"] == {"missing-states"}  # a list with no empty state
    assert {"hardcoded-color", "hardcoded-px", "vh-units"} <= places["Tw.tsx"]
    assert meta["reduced_motion_in_source"] is True
    assert "components.json" not in places


def test_grep_cli(tmp_path, capsys):
    make_project(tmp_path)
    assert uk.main(["grep", "--workdir", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(ln.startswith("floor") and "src/components/List.tsx: fetches data" in ln for ln in lines)
    assert any(ln.startswith("direction") and "src/components/Card.tsx: 1 literal colours" in ln for ln in lines)
    assert lines[-1].startswith("fashion") and "components.json: shadcn/ui installed" in lines[-1]


# ---------------------------------------------------------------- backlog

def test_feed_backlog_adds_dedupes_and_closes(tmp_path):
    backlog = tmp_path / ".loop" / "backlog.yaml"
    stale = uk.merge([uk.finding("transition-all", "src/old.tsx", "transition: all in source")])[0]
    far = uk.merge([uk.finding("ease-in", "src/far.tsx", "ease-in in source")])[0]
    backlog.parent.mkdir(parents=True)
    backlog_io.dump([
        {"id": "spec-f-001", "title": "a feature", "status": "open", "note": "milestone m1"},
        {"id": stale["id"], "title": "src/old.tsx: transition", "dimension": "ui", "source": "ui", "status": "open",
         "note": f"ui floor minor @ src/old.tsx · fix: {stale['fix']}"},
        {"id": far["id"], "title": "src/far.tsx: ease-in", "dimension": "ui", "source": "ui", "status": "open",
         "note": f"ui floor minor @ src/far.tsx · fix: {far['fix']}"},
    ], str(backlog))
    fs = uk.merge([uk.finding("contrast", "/", "worst 2.1:1"), uk.finding("emoji-icons", "/", "2 emoji"),
                   uk.finding("heading-rhythm", "/", "nit")])
    checked = {"/", "src/old.tsx"}
    assert uk.feed_backlog(fs, str(backlog), checked, "m2") == (3, 1)
    rows = {r["id"]: r for r in backlog_io.load(str(backlog))}
    for f in fs:
        r = rows[f["id"]]
        assert r["dimension"] == "ui" and r["source"] == "ui" and r["status"] == "open"
        assert r["title"].startswith("/: ") and r["est"] == "S" and r["attempts"] == 0
        assert r["note"].startswith(f"ui {f['kind']} {f['severity']} @ /")
    rung = {f["rule"]: rows[f["id"]]["rung"] for f in fs}
    assert rung == {"contrast": 1, "emoji-icons": 2, "heading-rhythm": 2}
    assert rows[stale["id"]]["status"] == "done" and "superseded: clean ui check m2" in rows[stale["id"]]["note"]
    assert rows[far["id"]]["status"] == "open"  # not checked this run: left alone
    assert rows["spec-f-001"]["status"] == "open"
    # the same findings again: nothing new, nothing to close
    assert uk.feed_backlog(fs, str(backlog), checked, "m3") == (0, 0)
    assert len(backlog_io.load(str(backlog))) == 6


# ---------------------------------------------------------------- run / score / judged

def test_run_without_a_browser_writes_the_record(tmp_path, capsys):
    proj = tmp_path / "proj"
    make_project(proj)
    (proj / "design-tokens.json").write_text(json.dumps(CALM))
    out = tmp_path / "check"
    assert uk.main(["run", "--workdir", str(proj), "--no-browser", "--out", str(out), "--label", "m1",
                    "--backlog", str(proj / ".loop" / "backlog.yaml")]) == 0
    report = json.loads((out / "check.json").read_text())
    assert report["label"] == "m1" and report["direction"].endswith("design-tokens.json")
    assert report["score"] == uk.score(report["findings"]) < 100
    assert report["counts"]["fashion"] == 1  # shadcn-defaults: advisory
    assert (out / "summary.md").read_text().startswith("# UI check m1 — score")
    rows = backlog_io.load(str(proj / ".loop" / "backlog.yaml"))
    assert len(rows) == len(report["findings"]) and all(r["dimension"] == "ui" for r in rows)
    assert "backlog +" in capsys.readouterr().out


def test_score_feeds_the_nightshift_cmd_scorer(tmp_path):
    proj = tmp_path / "proj"
    make_project(proj)
    cfg = {"name": "ui", "cmd": f"{sys.executable} {KIT / 'ui_check.py'} score --workdir . --no-browser --label t"}
    proc = subprocess.run([sys.executable, str(LOOP / "scorers" / "cmd.py"), "--config", json.dumps(cfg), "--workdir", str(proj)],
                          capture_output=True, text=True, timeout=120)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is True, out
    report = json.loads((proj / ".pipeline" / "ui" / "check" / "t" / "check.json").read_text())
    assert out["value"] == report["score"]
    assert out["raw"]["measured"] is False


@pytest.mark.parametrize("with_direction", [True, False])
def test_mkconfig_makes_ui_a_pinned_cmd_scorer(tmp_path, with_direction):
    proj = tmp_path / "proj"
    (proj / ".pipeline").mkdir(parents=True)
    (proj / ".pipeline" / "scorers.proposed.json").write_text(json.dumps(
        {"scorers": [{"name": "tests", "weight": 0.4}, {"name": "ui", "weight": 0.6}]}))
    (proj / "spec.json").write_text(json.dumps({"stack": {"serve": {"cmd": "npm run dev", "port": 5173}}, "needs": ["unit-tests", "ui"]}))
    if with_direction:
        (proj / "design-tokens.json").write_text(json.dumps(CALM))
    cfg = tmp_path / "ns" / "config.json"
    proc = subprocess.run([sys.executable, str(KIT / "mkconfig.py"), "--project", str(proj), "--spec", str(proj / "spec.json"),
                           "--out", str(cfg)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    ui = next(s for s in json.loads(cfg.read_text())["scorers"] if s["name"] == "ui")
    assert ui["script"] == "cmd" and ui["weight"] == 0.6
    assert "ui_check.py score --workdir ." in ui["cmd"] and "--judge" not in ui["cmd"]
    assert (ui.get("pins") == ["design-tokens.json"]) is with_direction  # the loop can't move its own target
    if with_direction:
        assert "repo:design-tokens.json" in (tmp_path / "ns" / "manifest.sha256").read_text()


def test_judged_cli_keeps_only_grounded_findings(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    d = tmp_path / "check"
    assert uk.main(["run", "--workdir", str(proj), "--no-browser", "--out", str(d), "--label", "m1"]) == 0
    (d / "shots").mkdir()
    (d / "shots" / "home-1440-light.json").write_text(json.dumps(CLEAN))
    (d / "shots" / "not-a-capture.json").write_text(json.dumps({"hello": 1}))
    judge = {"scores": {"hierarchy": 2, "restraint": 3},
             "findings": [
                 {"sel": "main > h1", "route": "/", "dimension": "hierarchy", "severity": "major",
                  "title": "The page title is the same size as the card titles", "fix": "Use --text-2xl"},
                 {"sel": "div.imaginary", "route": "/", "dimension": "polish", "severity": "minor",
                  "title": "Points at nothing on the page", "fix": "-"},
                 {"sel": "header > button.btn", "route": "/", "dimension": "craft", "severity": "weird",
                  "title": "Primary button blends in", "fix": "Use --primary"}]}
    (d / "judge.json").write_text(json.dumps(judge))
    backlog = tmp_path / "backlog.yaml"
    assert uk.main(["judged", "--check-dir", str(d), "--backlog", str(backlog)]) == 0
    report = json.loads((d / "check.json").read_text())
    assert report["judge_scores"] == judge["scores"]
    kept = report["judged"]
    assert [f["evidence"] for f in kept] == [["main > h1"], ["header > button.btn"]]
    assert {f["kind"] for f in kept} == {"judged"} and kept[1]["severity"] == "minor"
    assert kept[0]["rule"] == "judged-hierarchy"
    assert uk.score(report["findings"] + kept) == uk.score(report["findings"])
    rows = backlog_io.load(str(backlog))
    assert sorted(r["id"] for r in rows) == sorted(f["id"] for f in kept)
    assert "The page title" in (d / "summary.md").read_text()
    assert uk.main(["judged", "--check-dir", str(tmp_path / "nowhere")]) == 1


def test_findings_cli_on_existing_captures(tmp_path, capsys):
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "a-1440-light.json").write_text(json.dumps(CLEAN))
    (shots / "a-375-light.json").write_text(json.dumps(cap(viewport="375x812", mobile={"blocksZoom": True})))
    tokens = tmp_path / "design-tokens.json"
    tokens.write_text(json.dumps(CALM))
    assert uk.main(["findings", "--captures", str(shots), "--tokens", str(tokens)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [f["rule"] for f in out["findings"]] == ["zoom-blocked"]
    assert out["score"] == 90
