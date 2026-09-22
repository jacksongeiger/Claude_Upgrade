"""Tests for ui_inspire.py — numbers cards, their median and markdown from
synthetic ui_measure captures, and the CLI (cards, adopt, run with the
browser stubbed out). Offline, deterministic.

    discovery/venv/bin/python -m pytest pipeline/tests/test_ui_inspire.py -q -p no:cacheprovider
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
_spec = importlib.util.spec_from_file_location("ui_inspire", KIT / "ui_inspire.py")
ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui)
ud = ui.ui_direction


def capture(url, theme, page_bg, body=16, ratio=1.25, radius=8, ms="150ms", words=120, renders_dark=False):
    slug = url.split("//")[1].replace(".", "-")
    return {
        "url": url, "theme": theme, "viewport": "1440x900", "title": f"{slug} home", "pageBg": page_bg,
        "rendersDark": renders_dark, "screenshot": f"/shots/{slug}-1440-{theme}.png", "overlaysHidden": ["cookie banner"],
        "profile": {"bodyPx": body, "sizesUsed": [body, round(body * ratio), round(body * ratio ** 2)], "scaleRatio": ratio,
                    "nearestScale": "major third", "largestStep": ratio, "spacingOn4Pct": 92, "spacingOn8Pct": 70,
                    "spacingCarrying90Pct": [8, 16, 24], "distinctSpacing": 7, "typefaces": 1, "dominantWeight": "400",
                    "distinctTextColors": 3, "accentColors": 2, "accentHues": 1, "dominantRadiusPx": radius,
                    "distinctShadows": 1, "maxShadowBlurPx": 12, "measureCpl": 68, "hierarchyLevels": 4,
                    "firstViewport": {"words": words, "interactive": 9, "textCoveragePct": 14}},
        "motion": {"durations": [{"value": ms, "count": 4}], "easings": [{"value": "cubic-bezier(0.23, 1, 0.32, 1)", "count": 4}]},
        "color": {"accents": ["#635bff", "#00d4ff"], "background": [{"hex": "#635bff", "count": 3}]},
    }


CAPS = [
    capture("https://a.example", "light", "#ffffff", body=16, ratio=1.25, radius=8, ms="150ms", words=120),
    capture("https://a.example", "dark", "#0a0a0a", body=16, ratio=1.25, radius=8, ms="150ms", words=120),
    capture("https://b.example", "light", "#fafafa", body=14, ratio=1.2, radius=6.4, ms="200ms", words=80),
    capture("https://b.example", "dark", "#fafafa", body=14, ratio=1.2, radius=6.4, ms="200ms", words=80),
    capture("https://c.example", "light", "#050505", body=15, ratio=1.125, radius=12, ms="300ms", words=60, renders_dark=True),
]


def test_build_cards_pairs_themes_per_url():
    data = ui.build_cards(CAPS, "an invoicing app")
    assert data["for"] == "an invoicing app" and data["made_at"].endswith("Z")
    cards = {c["url"]: c for c in data["sites"]}
    assert list(cards) == ["https://a.example", "https://b.example", "https://c.example"]
    a, b, c = cards["https://a.example"], cards["https://b.example"], cards["https://c.example"]
    assert a["numbers"]["has_dark_theme"] is True  # the dark capture paints a different ground
    assert b["numbers"]["has_dark_theme"] is False  # same ground under prefers-color-scheme: dark
    assert c["numbers"]["has_dark_theme"] is None and c["numbers"]["renders_dark"] is True  # no dark capture
    assert a["screenshot"].endswith("-1440-light.png") and a["dark_screenshot"].endswith("-1440-dark.png")
    assert c["dark_screenshot"] is None and a["title"] == "a-example home"
    n = a["numbers"]
    assert n["body_px"] == 16 and n["scale_ratio"] == 1.25 and n["radius_px"] == 8
    assert n["motion_ms"] == ["150ms"] and n["accents"] == ["#635bff", "#00d4ff"]
    assert n["words_first_screen"] == 120 and n["controls_first_screen"] == 9
    assert b["numbers"]["radius_px"] == 6.4


def test_take_lines_restate_the_numbers():
    data = ui.build_cards(CAPS)
    take = {c["url"]: c["take"] for c in data["sites"]}
    a = take["https://a.example"]
    assert a[0] == "type: 16px body on a 1.25 (major third) scale, weight 400 carries the page"
    assert "spacing: 92% on a 4px grid; 8, 16, 24 carry 90%" in a
    assert a[-1] == "theme: follows the system dark mode"
    assert take["https://b.example"][-1] == "theme: light only"
    assert not any(t.startswith("theme:") for t in take["https://c.example"])
    assert ui.take_lines({}) == []


def test_median_of_the_cards():
    md = ui.build_cards(CAPS)["median"]
    assert md["sites"] == 3
    assert md["body_px"] == 15 and md["scale_ratio"] == 1.2 and md["radius_px"] == 8
    assert md["motion_ms"] == 200 and md["words_first_screen"] == 80 and md["spacing_on_4px_pct"] == 92
    assert ui.median([])["sites"] == 0 and ui.median([])["body_px"] is None
    assert ui.build_cards([])["median"] == {} and ui.build_cards([])["sites"] == []


def test_median_skips_non_numbers():
    cards = [{"numbers": {"body_px": 16, "motion_ms": ["0.3s"]}}, {"numbers": {"body_px": True, "motion_ms": []}},
             {"numbers": {"body_px": None}}]
    md = ui.median(cards)
    assert md["body_px"] == 16 and md["motion_ms"] is None


def test_to_md():
    data = ui.build_cards(CAPS, "an invoicing app")
    md = ui.to_md(data)
    assert md.startswith("# Inspiration — an invoicing app")
    assert "| site | body | scale | 4px grid | accents | radius | motion | words above fold |" in md
    assert "| https://a.example | 16px | 1.25 (major third) | 92% | 2 | 8px | 150ms | 120 |" in md
    assert "**Median of 3:** 15px body · scale 1.2" in md
    assert md.count("- take: _(written by the session that read the screenshot)_") == 3
    assert "## a-example home" in md and "the direction wins" in md


def write_caps(d, caps):
    d.mkdir(parents=True, exist_ok=True)
    for c in caps:
        slug = c["url"].split("//")[1].replace(".", "-")
        (d / f"{slug}-1440-{c['theme']}.json").write_text(json.dumps(c))


def test_cli_cards(tmp_path):
    shots = tmp_path / "shots"
    write_caps(shots, CAPS)
    (shots / "junk.json").write_text(json.dumps({"not": "a capture"}))
    out = tmp_path / "inspire"
    assert ui.main(["cards", "--captures", str(shots), "--for", "invoices", "--out", str(out)]) == 0
    data = json.loads((out / "inspiration.json").read_text())
    assert len(data["sites"]) == 3 and data["for"] == "invoices"
    assert (out / "inspiration.md").read_text().startswith("# Inspiration — invoices")
    (tmp_path / "empty").mkdir()
    assert ui.main(["cards", "--captures", str(tmp_path / "empty"), "--out", str(tmp_path / "o2")]) == 3


def test_cli_adopt_a_capture_writes_a_valid_proposal(tmp_path):
    cap = tmp_path / "a-example-1440-light.json"
    cap.write_text(json.dumps(CAPS[0]))
    out, css, md = tmp_path / "proposal.json", tmp_path / "tokens.css", tmp_path / "DESIGN.md"
    assert ui.main(["adopt", str(cap), "--out", str(out), "--css", str(css), "--designmd", str(md), "--name", "Invoices"]) == 0
    tokens = json.loads(out.read_text())
    assert ud.validate(tokens) == []
    assert tokens["source"]["kind"] == "reference" and tokens["source"]["url"] == "https://a.example"
    assert tokens["direction"]["name"] == "Invoices" and tokens["type_scale"]["base"] == 16
    assert tokens["direction"]["accent"] == "#635bff"  # the measured fill
    assert css.read_text().startswith("/* Generated by ui_direction.py") and md.read_text().startswith("---")
    assert ui.main(["adopt", str(cap), "--out", str(out)]) == 1  # never overwrites without --force
    assert ui.main(["adopt", str(cap), "--out", str(out), "--force"]) == 0


def test_cli_adopt_by_site_slug(tmp_path):
    inspire = tmp_path / "inspire"
    write_caps(inspire / "shots", CAPS)
    out = tmp_path / "p.json"
    assert ui.main(["adopt", "https://b.example", "--inspire-dir", str(inspire), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["source"]["url"] == "https://b.example"
    assert ui.main(["adopt", "https://never-measured.example", "--inspire-dir", str(inspire), "--out", str(tmp_path / "q.json")]) == 1


def test_cli_run_measures_the_urls_given(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_measure(urls, out_dir, viewports, themes, stress=True):
        calls.append((list(urls), viewports, themes, stress))
        return [c for c in CAPS if c["url"] in urls], None

    monkeypatch.setattr(ui.ui_check, "measure", fake_measure)
    out = tmp_path / "inspire"
    assert ui.main(["run", "--url", "https://a.example", "--url", "https://b.example", "--out", str(out)]) == 0
    assert calls == [(["https://a.example", "https://b.example"], ["1440x900"], ["light", "dark"], False)]
    data = json.loads((out / "inspiration.json").read_text())
    assert [s["url"] for s in data["sites"]] == ["https://a.example", "https://b.example"] and data["notes"] == []
    assert "inspire: 2 of 2 sites measured" in capsys.readouterr().out
    assert ui.main(["run", "--out", str(out)]) == 1  # nothing to go on


def test_cli_run_without_galleries_uses_the_vetted_sites(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(ui.ui_check, "measure", lambda urls, *a, **k: (seen.extend(urls) or [], "no capture succeeded"))
    assert ui.main(["run", "--for", "analytics dashboard", "--no-galleries", "--no-dark", "--limit", "2", "--out", str(tmp_path)]) == 3
    assert seen == ["https://linear.app", "https://posthog.com"]
    assert json.loads((tmp_path / "inspiration.json").read_text())["notes"] == ["no capture succeeded"]
