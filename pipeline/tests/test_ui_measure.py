"""Live tests for js/ui_measure.cjs through ui_check.measure() — the one
Playwright entry point of /jg-ui. Skipped cleanly when pw.cjs can't load
Playwright (conftest.playwright_ok). About 15 s.

1. fixtures/ui/slop.html, a page built to fail, measured at 1440x900 and
   375x812: every planted defect must be found.
2. A picker built from three of the kit's own directions (calm-dense,
   friendly, expressive, with semantic roles), light and dark at both widths:
   nothing may fail, and the check scores it 100 against calm-dense.

    discovery/venv/bin/python -m pytest pipeline/tests/test_ui_measure.py -q -p no:cacheprovider
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import PLAYWRIGHT_MISSING, playwright_ok

KIT = Path(__file__).resolve().parent.parent
SLOP = Path(__file__).resolve().parent / "fixtures" / "ui" / "slop.html"
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(KIT.parent / "loop"))

pytestmark = pytest.mark.skipif(not playwright_ok(), reason=PLAYWRIGHT_MISSING)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, KIT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


uk = _load("ui_check")
ud = _load("ui_direction")


@pytest.fixture(scope="module")
def slop(tmp_path_factory):
    out = tmp_path_factory.mktemp("slop")
    caps, err = uk.measure([SLOP.as_uri()], out, ["1440x900", "375x812"], ["light"])
    assert err is None and len(caps) == 2, err
    by = {c["viewport"]: c for c in caps}
    return by["1440x900"], by["375x812"], out


def test_slop_desktop_defects_are_measured(slop):
    desk, _, _ = slop
    assert desk["contrast"]["failing"] >= 1
    m = desk["motion"]
    assert m["transitionAll"] >= 1 and m["easeIn"] >= 1 and m["bounce"] >= 1
    assert m["reducedMotionRule"] is False
    assert desk["mobile"]["blocksZoom"] is True
    assert desk["tells"]["emojiIcons"] and desk["tells"]["purpleBlueGradientHero"] is True
    assert desk["tells"]["placeholders"]
    assert desk["surface"]["cardNestingDepth"] >= 3
    assert desk["targets"]["under24"] >= 1
    assert desk["overflow"]["horizontal"] is False  # 600px fits at 1440
    assert Path(desk["screenshot"]).stat().st_size > 0


def test_slop_mobile_defects_are_measured(slop):
    _, phone, _ = slop
    assert phone["overflow"]["horizontal"] is True
    assert any("div.wide" in o for o in phone["overflow"]["offenders"])
    assert phone["mobile"]["blocksZoom"] is True
    assert phone["mobile"]["smallInputs"] >= 1


def test_slop_scores_badly_and_names_its_defects(slop):
    _, _, out = slop
    proc = subprocess.run([sys.executable, str(KIT / "ui_check.py"), "findings", "--captures", str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    got = {f["rule"] for f in report["findings"]}
    assert {"contrast", "transition-all", "ease-in", "bounce-easing", "no-reduced-motion", "zoom-blocked",
            "h-overflow", "small-inputs", "nested-cards", "small-targets", "placeholder-copy"} <= got
    assert {"emoji-icons", "gradient-hero", "gradient-text", "ai-purple"} <= got  # fashion: reported, never scored
    assert report["score"] < 40


@pytest.fixture(scope="module")
def picker(tmp_path_factory):
    d = tmp_path_factory.mktemp("picker")
    paths = []
    for look in ("calm-dense", "friendly", "expressive"):
        p = d / f"{look}.json"
        assert ud.main(["propose", "--look", look, "--semantic", "--out", str(p)]) == 0
        paths.append(str(p))
    html = d / "picker.html"
    assert ud.main(["picker", *paths, "--out", str(html), "--title", "Ledger"]) == 0
    shots = d / "shots"
    caps, err = uk.measure([html.as_uri()], shots, ["1440x900", "375x812"], ["light", "dark"])
    assert err is None and len(caps) == 4, err
    return caps, shots, d / "calm-dense.json"


def test_picker_passes_its_own_floor(picker):
    caps, _, _ = picker
    for c in caps:
        tag = f"{c['viewport']} {c['theme']}"
        assert c["contrast"]["failing"] == 0, (tag, c["contrast"]["failures"])
        assert c["focus"]["tabbed"] > 0 and c["focus"]["visibleFocus"] == c["focus"]["tabbed"], (tag, c["focus"])
        assert c["overflow"]["horizontal"] is False, (tag, c["overflow"]["offenders"])
        assert c["consoleErrors"] == [], (tag, c["consoleErrors"])
    desk = {c["theme"]: c for c in caps if c["viewport"] == "1440x900"}
    assert desk["light"]["profile"]["bodyPx"] == 14  # variant 1 is the one shown: calm-dense
    assert desk["light"]["pageBg"] != desk["dark"]["pageBg"]  # the dark block applies under prefers-color-scheme


def test_picker_scores_100_against_its_direction(picker):
    _, shots, calm = picker
    proc = subprocess.run([sys.executable, str(KIT / "ui_check.py"), "findings", "--captures", str(shots), "--tokens", str(calm)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["score"] == 100, [(f["rule"], f["title"]) for f in report["findings"]]
