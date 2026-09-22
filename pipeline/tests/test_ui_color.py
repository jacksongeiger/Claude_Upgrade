"""Tests for ui_color.py — colour parsing, WCAG contrast, OKLCH and the
12-step scales every /jg-ui direction is built from. Offline, deterministic.

    discovery/venv/bin/python -m pytest pipeline/tests/test_ui_color.py -q -p no:cacheprovider
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ui_color", KIT / "ui_color.py")
uc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uc)

RADIX_GRAY_LIGHT = ["#fcfcfc", "#f9f9f9", "#f0f0f0", "#e8e8e8", "#e0e0e0", "#d9d9d9",
                    "#cecece", "#bbbbbb", "#8d8d8d", "#838383", "#646464", "#202020"]
RADIX_GRAY_DARK = ["#111111", "#191919", "#222222", "#2a2a2a", "#313131", "#3a3a3a",
                   "#484848", "#606060", "#6e6e6e", "#7b7b7b", "#b4b4b4", "#eeeeee"]


def hexes(steps):
    return [uc.to_hex(s) for s in steps]


# ---------------------------------------------------------------- contrast

def test_contrast_known_pairs():
    assert round(uc.contrast(uc.hex_rgb("#646464"), uc.hex_rgb("#f9f9f9")), 2) == 5.62
    assert uc.contrast((0, 0, 0), (1, 1, 1)) == pytest.approx(21.0)
    assert uc.contrast(uc.hex_rgb("#000"), uc.hex_rgb("#fff")) == pytest.approx(21.0)
    # order does not matter; a colour on itself is 1:1
    a, b = uc.hex_rgb("#5b5bd6"), uc.hex_rgb("#ffffff")
    assert uc.contrast(a, b) == uc.contrast(b, a)
    assert uc.contrast(a, a) == pytest.approx(1.0)


def test_required_ratio_follows_wcag_large_text():
    assert uc.required_ratio(16, 400) == 4.5
    assert uc.required_ratio(24, 400) == 3.0
    assert uc.required_ratio(19, 700) == 3.0
    assert uc.required_ratio(19, "bold-ish") == 4.5  # unreadable weight reads as 400


def test_composite_alpha_over_background():
    assert uc.composite((0, 0, 0), 0.5, (1, 1, 1)) == pytest.approx((0.5, 0.5, 0.5))


def test_cli_contrast(capsys):
    assert uc.main(["contrast", "#646464", "#f9f9f9"]) == 0
    assert capsys.readouterr().out.strip() == "5.62"
    assert uc.main(["contrast", "#646464", "not-a-colour"]) == 1


def test_cli_scale_runs_as_a_script():
    proc = subprocess.run([sys.executable, str(KIT / "ui_color.py"), "scale", "#5b5bd6", "--theme", "light"],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("#") == 12
    assert '"#5b5bd6"' in proc.stdout  # step 9 of a light scale is the accent itself


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("value, rgb255, alpha", [
    ("#abc", (0xaa, 0xbb, 0xcc), 1.0),
    ("#ABC", (0xaa, 0xbb, 0xcc), 1.0),
    ("#abcd", (0xaa, 0xbb, 0xcc), 0xdd / 255),
    ("#aabbcc", (0xaa, 0xbb, 0xcc), 1.0),
    ("#aabbcc80", (0xaa, 0xbb, 0xcc), 0x80 / 255),
    ("rgb(10, 20, 30)", (10, 20, 30), 1.0),
    ("rgba(10,20,30,0.5)", (10, 20, 30), 0.5),
    ("rgba(0, 0, 0, 0)", (0, 0, 0), 0.0),
    ("rgb(10 20 30 / 50%)", (10, 20, 30), 0.5),
    ("rgb(10 20 30 / 0.25)", (10, 20, 30), 0.25),
    ("rgb(100% 0% 50%)", (255, 0, 127.5), 1.0),
    ("  RGB(10, 20, 30)  ", (10, 20, 30), 1.0),
    ("color(srgb 1 0 0.5)", (255, 0, 127.5), 1.0),
    ("color(srgb 1 0 0.5 / 0.4)", (255, 0, 127.5), 0.4),
    ("color(srgb-linear 1 0 0)", (255, 0, 0), 1.0),
    ("color(srgb-linear 0.214 0.214 0.214)", (127.5, 127.5, 127.5), 1.0),
    ("transparent", (0, 0, 0), 0.0),
])
def test_parse_reads_css_colours(value, rgb255, alpha):
    got = uc.parse(value)
    assert got is not None, value
    rgb, a = got
    assert [round(c * 255, 1) for c in rgb] == pytest.approx(list(rgb255), abs=0.6)
    assert a == pytest.approx(alpha, abs=1e-3)


def test_parse_oklch_and_oklab():
    # CSS red and white written in OKLCH / OKLab
    assert uc.to_hex(uc.parse("oklch(62.8% 0.2577 29.23)")[0]) == "#ff0000"
    assert uc.to_hex(uc.parse("oklch(1 0 0)")[0]) == "#ffffff"
    assert uc.to_hex(uc.parse("oklch(0 0 none)")[0]) == "#000000"
    assert uc.parse("oklch(0.7 0.1 250 / 50%)")[1] == pytest.approx(0.5)
    assert uc.to_hex(uc.parse("oklab(1 0 0)")[0]) == "#ffffff"
    # an out-of-gamut chroma is clipped into sRGB, never returned out of range
    rgb, _ = uc.parse("oklch(0.7 0.4 150)")
    assert all(0.0 <= c <= 1.0 for c in rgb)


@pytest.mark.parametrize("value", [
    "nope", "", "#12", "#12345", "#ggg", "var(--primary)", "lab(50 20 20)", "hsl(120 50% 50%)",
    "rgb(1, 2)", "rgb(a, b, c)", "color(display-p3 1 0 0)", "linear-gradient(red, blue)", None, 5,
])
def test_parse_garbage_is_none(value):
    assert uc.parse(value) is None


def test_hex_rgb_raises_on_garbage():
    with pytest.raises(ValueError):
        uc.hex_rgb("not a colour")


def test_to_hex_rounds_and_clamps():
    assert uc.to_hex((1.2, -0.1, 0.5)) == "#ff0080"


def test_oklch_round_trip():
    for h in ("#5b5bd6", "#e5484d", "#30a46c", "#ffb224", "#646464"):
        L, C, H = uc.srgb_to_oklch(uc.hex_rgb(h))
        rgb, used = uc.gamut_clip(L, C, H)
        assert uc.to_hex(rgb) == h
        assert used == pytest.approx(C, abs=1e-6)


# ---------------------------------------------------------------- scales

def test_neutral_gray_is_radix_gray():
    assert hexes(uc.neutral_scale("light", "gray")) == RADIX_GRAY_LIGHT
    assert hexes(uc.neutral_scale("dark", "gray")) == RADIX_GRAY_DARK


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("temperature", ["gray", "cool", "warm", "tinted"])
def test_neutral_text_steps_clear_their_ratio(theme, temperature):
    n = uc.neutral_scale(theme, temperature, tint_hue=280.0)
    assert len(n) == 12
    for bg in n[:3]:
        assert uc.contrast(n[10], bg) >= 4.5  # muted text
        assert uc.contrast(n[11], bg) >= 7.0  # text
    lum = [uc.luminance(s) for s in n]
    assert lum == (sorted(lum, reverse=True) if theme == "light" else sorted(lum))


@pytest.mark.parametrize("accent", ["#5b5bd6", "#ffe629", "#00c2ff", "#7cfc00", "#e5484d", "#111111", "#f5f5f5"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_accent_scale_text_step_clears_on_the_neutral(accent, theme):
    n = uc.neutral_scale(theme, "cool")
    a = uc.accent_scale(uc.hex_rgb(accent), theme, neutral=n)
    assert len(a) == 12
    for bg in n[:3]:
        assert uc.contrast(a[10], bg) >= 4.5
    if theme == "light":
        assert uc.to_hex(a[8]) == accent  # step 9 is the accent exactly
    else:
        assert 0.55 - 0.01 <= uc.srgb_to_oklch(a[8])[0] <= 0.75 + 0.01


def test_on_color_picks_white_or_ink():
    rgb, ratio = uc.on_color(uc.hex_rgb("#0078d6"))
    assert rgb == uc.WHITE and ratio >= 4.5
    rgb, ratio = uc.on_color(uc.hex_rgb("#ffe629"))
    assert rgb == uc.INK and ratio >= 4.5


def test_quantize_is_what_hex_holds():
    q = uc.quantize((0.123456, 0.5, 0.999))
    assert uc.to_hex(q) == uc.to_hex((0.123456, 0.5, 0.999))
    assert uc.hex_rgb(uc.to_hex(q)) == pytest.approx(q)


# ---------------------------------------------------------------- fit_accent_for_white

def test_fit_accent_darkens_blue_until_white_clears():
    before = uc.hex_rgb("#0090ff")
    assert uc.contrast(uc.WHITE, before) < 4.5
    rgb, note = uc.fit_accent_for_white(before)
    assert uc.contrast(uc.WHITE, rgb) >= 4.5
    assert uc.luminance(rgb) < uc.luminance(before)
    assert abs(uc.srgb_to_oklch(rgb)[2] - uc.srgb_to_oklch(before)[2]) < 3  # same hue
    assert note and "#0090ff" in note and uc.to_hex(rgb) in note


def test_fit_accent_keeps_yellow_for_dark_text():
    rgb, note = uc.fit_accent_for_white(uc.hex_rgb("#ffe629"))
    assert uc.to_hex(rgb) == "#ffe629"
    assert note and "kept" in note
    assert uc.on_color(rgb)[0] == uc.INK


def test_fit_accent_leaves_a_dark_enough_accent_alone():
    rgb, note = uc.fit_accent_for_white(uc.hex_rgb("#5b5bd6"))
    assert uc.to_hex(rgb) == "#5b5bd6" and note is None
