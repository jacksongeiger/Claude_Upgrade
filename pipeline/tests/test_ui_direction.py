"""Tests for ui_direction.py — looks, proposals, validation, the CSS and
DESIGN.md it writes, imports (DESIGN.md, a measured capture) and the picker.
Offline, deterministic.

    discovery/venv/bin/python -m pytest pipeline/tests/test_ui_direction.py -q -p no:cacheprovider
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))  # ui_direction imports ui_color by name
_spec = importlib.util.spec_from_file_location("ui_direction", KIT / "ui_direction.py")
ud = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ud)
uc = ud.uc

ACCENTS = ["#ffe629", "#00c2ff", "#7cfc00", "#ff00ff", "#111111", "#f5f5f5", "#8e4ec6", "#e5484d"]
NEUTRALS = ["cool", "warm", "gray", "tinted"]


def block(css_text, opener):
    """The declarations of the first block that opens with `opener`, as {var: value}."""
    i = css_text.index(opener)
    body = css_text[i + len(opener):css_text.index("}", i)]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))


# ---------------------------------------------------------------- propose + validate

@pytest.mark.parametrize("accent", ACCENTS)
@pytest.mark.parametrize("look", sorted(ud.LOOKS))
def test_every_look_accent_and_neutral_validates(look, accent):
    for neutral in NEUTRALS:
        tokens = ud.propose(look, accent=accent, neutral=neutral, semantic=True)
        assert ud.validate(tokens) == [], (look, accent, neutral)
        light = tokens["colors"]["light"]
        assert {"danger", "warning", "success", "danger-text"} <= set(light)
        assert tokens["direction"]["neutral"] == neutral


@pytest.mark.parametrize("look", sorted(ud.LOOKS))
def test_type_scale_of_every_look(look):
    p = ud.LOOKS[look]
    sizes = list(ud.type_scale(p["base"], p["ratio"]).values())
    assert len(sizes) == 7
    assert all(s >= 12 for s in sizes)
    assert all(b > a for a, b in zip(sizes, sizes[1:]))
    assert ud.type_scale(p["base"], p["ratio"])["base"] == p["base"]


def test_propose_shape_and_numbers():
    t = ud.propose("calm-dense", accent="#0090ff", name="Ledger", avoid=["purple gradients"],
                   references=["https://example.com"], semantic=True)
    d = t["direction"]
    assert d["name"] == "Ledger" and d["look"] == "calm-dense" and d["theme"] == "both"
    assert d["accent"] == t["colors"]["light"]["accent"] == t["scales"]["accent"]["light"][8]
    assert uc.contrast(uc.WHITE, uc.hex_rgb(d["accent"])) >= 4.5  # darkened for white text
    assert any("#0090ff" in n for n in d["notes"])
    assert d["references"] == [{"url": "https://example.com"}] and d["avoid"] == ["purple gradients"]
    assert list(t["spacing"].values()) == ud.SPACING["compact"]
    assert t["radius"] == {"sm": "4px", "md": "6px", "lg": "8px", "full": "9999px"}
    assert max(t["motion"]["duration_ms"].values()) <= 300
    assert t["motion"]["spring"]["bounce"] == 0.0
    assert len(t["chart"]) == 5 and t["chart"][0] == d["accent"]
    for theme in ("light", "dark"):
        assert len(t["scales"]["neutral"][theme]) == len(t["scales"]["accent"][theme]) == 12
    assert d["targets"]["body_px"] == 14 and d["targets"]["max_radius_px"] == 8


def test_propose_overrides_replace_the_looks_numbers():
    t = ud.propose("editorial", overrides={"base": 15, "ratio": 1.2, "radius": [3, 5, 7], "motion": [100, 150, 200],
                                            "shadow": "border", "bounce": None})
    assert t["type_scale"]["base"] == 15 and t["type"]["ratio"] == 1.2
    assert t["radius"]["md"] == "5px"
    assert t["motion"]["duration_ms"] == {"fast": 100, "base": 150, "slow": 200}
    assert t["shadow"] == ud.SHADOWS["border"]


def test_propose_unknown_look_raises():
    with pytest.raises(ValueError, match="unknown look"):
        ud.propose("brutalist")


def test_validate_reports_broken_tokens():
    t = ud.propose("precise", semantic=True)
    t["colors"]["light"]["text-muted"] = "#dddddd"
    t["colors"]["dark"]["ring"] = "not-a-colour"
    t["type_scale"] = {"sm": 11, "base": 14, "lg": 13}
    t["spacing"] = {"1": 4, "2": 4}
    t["radius"] = {"sm": "8px", "md": "4px", "lg": "12px", "full": "9999px"}
    t["motion"]["duration_ms"] = {"fast": 120, "base": 450, "slow": 600}
    problems = "\n".join(ud.validate(t))
    assert "light: text-muted #dddddd on bg" in problems
    assert "dark: ring or bg is not a colour" in problems
    assert "not strictly increasing: [11, 14, 13]" in problems
    assert "under 12px: 11" in problems
    assert "spacing scale is not strictly increasing" in problems
    assert "radii are not ascending" in problems
    assert "motion base is 450 ms" in problems and "motion slow" not in problems


def test_validate_skips_an_extracted_palette():
    extracted = {"colors": {"c1": {"hex": "#222222", "count": 3}}, "type_scale": {"sm": 14, "base": 16}}
    assert ud.validate(extracted) == []


# ---------------------------------------------------------------- CSS

def test_css_carries_kit_and_shadcn_names_with_dark_blocks():
    t = ud.propose("calm-dense", semantic=True)
    out = ud.css(t)
    assert out.startswith("/* Generated by ui_direction.py")  # ui_check skips its own output by this line
    root = block(out, ":root {")
    for var in ("--primary", "--primary-foreground", "--background", "--foreground", "--ring", "--brand",
                "--muted-foreground", "--destructive", "--input", "--sidebar", "--radius", "--font-sans",
                "--text-base", "--space-4", "--duration-fast", "--ease-out", "--chart-1", "--border-default"):
        assert var in root, var
    light, dark = t["colors"]["light"], t["colors"]["dark"]
    assert root["--primary"] == root["--brand"] == light["accent"]
    assert root["--background"] == light["bg"] and root["--ring"] == light["ring"]
    assert root["--accent"] == light["surface-hover"]  # shadcn's --accent is a hover surface, not the brand
    assert root["--border"] == light["border-subtle"] and root["--border-default"] == light["border"]
    assert root["--text-base"] == "0.875rem" and root["--radius"] == "6px"

    assert "@media (prefers-color-scheme: dark)" in out
    media = block(out, ':root:not([data-theme="light"]) {')
    forced = block(out, ':root[data-theme="dark"] {')
    assert media == forced
    assert forced["--background"] == dark["bg"] != root["--background"]
    assert forced["--primary"] == dark["accent"] and forced["--foreground"] == dark["text"]


def test_css_without_semantic_roles_still_has_destructive():
    t = ud.propose("friendly", semantic=False)
    assert "danger" not in t["colors"]["light"]
    out = ud.css(t)
    assert uc.parse(block(out, ":root {")["--destructive"])
    assert uc.parse(block(out, ':root[data-theme="dark"] {')["--destructive"])


# ---------------------------------------------------------------- DESIGN.md

def test_designmd_round_trip():
    t = ud.propose("editorial", accent="#1a7f5a", name='Quote "Studio"', avoid=["stock photos"], semantic=True)
    md = ud.designmd(t)
    fm = ud.parse_designmd(md)
    assert fm["name"] == 'Quote "Studio"'
    assert fm["colors"]["primary"] == t["direction"]["accent"]
    assert fm["colors"]["background"] == t["colors"]["light"]["bg"]
    assert fm["colors"]["primary-dark"] == t["colors"]["dark"]["accent"]
    assert fm["typography"]["body-md"]["fontSize"] == f"{t['type_scale']['base']}px"
    assert fm["rounded"]["md"] == t["radius"]["md"]
    assert fm["spacing"]["4"] == "16px"
    assert fm["components"]["button-primary"]["backgroundColor"] == "{colors.primary}"
    headings = re.findall(r"^## (.+)$", md, re.M)
    assert headings == ud.DESIGN_SECTIONS
    assert "- Don't: stock photos." in md


MINIMAL_DESIGN_MD = """---
name: Harbor
colors:
  primary: "#1a73e8"
  background: "#fdfdfd"
  text: '#1b1b1f'
typography:
  body-md:
    fontFamily: Inter
    fontSize: 15px
  h1:
    fontSize: 32px
rounded:
  sm: 4px
  md: 8px
  lg: 12px
---

# Harbor
Anything below the front matter is prose.
"""


def test_from_designmd_keeps_their_colours_and_numbers():
    t = ud.from_designmd(MINIMAL_DESIGN_MD)
    light = t["colors"]["light"]
    assert light["accent"] == "#1a73e8" and light["bg"] == "#fdfdfd" and light["text"] == "#1b1b1f"
    assert t["direction"]["name"] == "Harbor"
    assert t["type_scale"]["base"] == 15 and t["type_scale"]["4xl"] == 32
    assert t["radius"]["md"] == "8px"
    assert t["fonts"]["body"].startswith("'Inter'")
    assert t["source"]["kind"] == "design.md"
    assert "primary→accent" in t["direction"]["notes"][-1]
    assert ud.validate(t) == []


def test_from_designmd_in_shadcn_vocabulary_keeps_the_primary():
    # shadcn's own names: --accent is a hover surface and --muted a background,
    # the same meanings the kit's tokens.css writes; the brand colour is primary
    md = """---
name: Tidy
colors:
  primary: "#1d4ed8"
  accent: "#f4f4f5"
  background: "#ffffff"
  foreground: "#09090b"
  muted: "#f4f4f5"
  muted-foreground: "#71717a"
  border: "#e4e4e7"
typography:
  body:
    fontFamily: Geist
    fontSize: 14px
---
"""
    t = ud.from_designmd(md)
    light = t["colors"]["light"]
    assert light["accent"] == t["direction"]["accent"] == "#1d4ed8"
    assert light["text-muted"] == "#71717a"
    assert light["text"] == "#09090b" and light["bg"] == "#ffffff" and light["border-subtle"] == "#e4e4e7"
    assert ud.validate(t) == []


def test_from_designmd_reports_a_brand_pair_under_ratio():
    md = MINIMAL_DESIGN_MD.replace('"#1a73e8"', '"#0090ff"')
    t = ud.from_designmd(md)
    assert t["colors"]["light"]["accent"] == "#0090ff"  # their literal colour is kept
    assert any("on-accent" in p and "accent #0090ff" in p for p in ud.validate(t))


def test_parse_designmd_without_front_matter():
    assert ud.parse_designmd("# Just prose\n") == {}


# ---------------------------------------------------------------- from a measured capture

def test_from_capture_adopts_the_measured_numbers():
    cap = {"url": "https://ref.example", "rendersDark": False,
           "profile": {"bodyPx": 15, "scaleRatio": 1.25, "dominantRadiusPx": 10},
           "color": {"background": [{"hex": "#ffffff"}, {"hex": "#635bff"}], "accents": ["#00d4ff"]}}
    t = ud.from_capture(cap, name="Mine")
    assert t["direction"]["look"] == "editorial"
    assert t["type_scale"]["base"] == 15 and t["type"]["ratio"] == 1.25
    assert t["radius"] == {"sm": "5px", "md": "10px", "lg": "15px", "full": "9999px"}
    assert t["direction"]["accent"] == "#635bff"  # the saturated fill, not a text accent
    assert t["direction"]["references"] == [{"url": "https://ref.example"}]
    assert t["source"] == {"kind": "reference", "by": "ui_direction.py adopt",
                           "made_at": t["source"]["made_at"], "url": "https://ref.example"}
    assert "https://ref.example" in t["direction"]["notes"][-1]
    assert ud.validate(t) == []


def test_from_capture_dark_reference_and_out_of_range_numbers():
    dark = ud.from_capture({"url": "https://dark.example", "rendersDark": True,
                            "profile": {"bodyPx": 14, "scaleRatio": 1.15, "dominantRadiusPx": 8},
                            "color": {"background": [{"hex": "#101010"}], "accents": ["#ff6363"]}})
    assert dark["direction"]["look"] == "dark-compact" and dark["direction"]["theme"] == "dark"
    wild = ud.from_capture({"url": "u", "profile": {"bodyPx": 40, "scaleRatio": 3.0, "dominantRadiusPx": 99}})
    look = ud.LOOKS[wild["direction"]["look"]]
    assert wild["type_scale"]["base"] == look["base"] and wild["type"]["ratio"] == look["ratio"]
    assert ud.validate(wild) == []


def test_closest_look_recovers_each_preset():
    for name, p in ud.LOOKS.items():
        assert ud.closest_look(p["base"], p["ratio"], p["radius"][1], p.get("theme") == "dark") == name


# ---------------------------------------------------------------- CLI

def test_cli_propose_refuses_to_overwrite(tmp_path, capsys):
    out = tmp_path / "design-tokens.json"
    css_out, md_out = tmp_path / "tokens.css", tmp_path / "DESIGN.md"
    args = ["propose", "--look", "precise", "--semantic", "--out", str(out), "--css", str(css_out), "--designmd", str(md_out)]
    assert ud.main(args) == 0
    first = out.read_text()
    assert json.loads(first)["direction"]["look"] == "precise"
    assert css_out.read_text().startswith("/* Generated by ui_direction.py")
    assert ud.parse_designmd(md_out.read_text())["colors"]["primary"]
    capsys.readouterr()
    assert ud.main(["propose", "--look", "friendly", "--out", str(out)]) == 1
    assert "not overwriting" in capsys.readouterr().err
    assert out.read_text() == first
    assert ud.main(["propose", "--look", "friendly", "--out", str(out), "--force"]) == 0
    assert json.loads(out.read_text())["direction"]["look"] == "friendly"


def test_cli_validate_css_designmd_and_looks(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(ud.propose("expressive", semantic=True)))
    assert ud.main(["validate", str(good)]) == 0
    bad = json.loads(good.read_text())
    bad["colors"]["light"]["text"] = "#eeeeee"
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    assert ud.main(["validate", str(tmp_path / "bad.json")]) == 2
    assert ud.main(["css", str(good), "--out", str(tmp_path / "t.css")]) == 0
    assert "--primary:" in (tmp_path / "t.css").read_text()
    assert ud.main(["designmd", str(good), "--out", str(tmp_path / "D.md")]) == 0
    assert (tmp_path / "D.md").read_text().startswith("---\nversion: alpha")
    capsys.readouterr()
    assert ud.main(["looks", "--json"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == set(ud.LOOKS)


def test_cli_import_and_adopt(tmp_path):
    md = tmp_path / "DESIGN.md"
    md.write_text(MINIMAL_DESIGN_MD)
    out = tmp_path / "imported.json"
    assert ud.main(["import", str(md), "--out", str(out), "--css", str(tmp_path / "i.css")]) == 0
    assert json.loads(out.read_text())["colors"]["light"]["accent"] == "#1a73e8"
    cap = tmp_path / "cap.json"
    cap.write_text(json.dumps({"url": "https://ref.example", "profile": {"bodyPx": 16, "scaleRatio": 1.2, "dominantRadiusPx": 12}}))
    assert ud.main(["adopt", str(cap), "--out", str(tmp_path / "adopted.json")]) == 0
    not_a_capture = tmp_path / "tokens.json"
    not_a_capture.write_text(json.dumps({"colors": {}}))
    assert ud.main(["adopt", str(not_a_capture), "--out", str(tmp_path / "x.json")]) == 1
    assert not (tmp_path / "x.json").exists()


# ---------------------------------------------------------------- picker

def test_picker_scopes_each_direction_and_inlines_the_bar(tmp_path):
    a, b = ud.propose("calm-dense", semantic=True), ud.propose("friendly", semantic=True)
    html = ud.picker([a, b], title="Ledger & Co")
    assert 'data-variant="1"' in html and 'data-variant="2"' in html
    assert 'data-name="calm-dense"' in html and 'data-name="friendly"' in html
    assert ud.PICKER_JS.read_text() in html
    assert "jgui-picker" in html and "Ledger &amp; Co" in html
    assert '[data-variant="1"] {' in html and ":root {" not in html  # every direction scoped to its wrapper
    assert ':root[data-theme="dark"] [data-variant="2"] {' in html
    assert ':root:not([data-theme="light"]) [data-variant="1"] {' in html
    ta, tb = tmp_path / "a.json", tmp_path / "b.json"
    ta.write_text(json.dumps(a))
    tb.write_text(json.dumps(b))
    out = tmp_path / "picker.html"
    assert ud.main(["picker", str(ta), str(tb), "--out", str(out), "--title", "Ledger"]) == 0
    assert 'data-variant="2"' in out.read_text()
