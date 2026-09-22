#!/usr/bin/env python3
"""ui_color.py — colour maths for /jg-ui, stdlib only.

Parsing (hex, rgb/rgba, oklch, oklab, color(srgb …)), WCAG 2 relative
luminance and contrast, alpha compositing, OKLCH <-> sRGB with gamut
clipping, and the 12-step role scales a design direction is built from.

Channels are sRGB floats in 0–1; alpha travels separately. Nothing here
reads files or the network.

    python3 ui_color.py contrast '#646464' '#f9f9f9'     -> 5.62
    python3 ui_color.py scale '#5b5bd6' --theme light    -> 12 hex steps
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys

# ---------------------------------------------------------------- parsing

_HEX = re.compile(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.I)
_FUNC = re.compile(r"^(rgba?|oklch|oklab|color)\((.*)\)$", re.I)


def _num(tok, pct_of=1.0):
    """'0.5' -> 0.5 · '50%' -> half of pct_of · '120deg' -> 120 · 'none' -> 0."""
    tok = tok.strip()
    if tok == "none":
        return 0.0
    if tok.endswith("%"):
        return float(tok[:-1]) / 100.0 * pct_of
    if tok.endswith("deg"):
        return float(tok[:-3])
    return float(tok)


def _split(args):
    """'1, 2, 3' | '1 2 3 / 50%' | '1, 2, 3, .5' -> ([parts], alpha token or None)."""
    alpha = None
    if "/" in args:
        args, alpha = args.split("/", 1)
    parts = [p for p in re.split(r"[\s,]+", args.strip()) if p]
    if alpha is None and len(parts) == 4 and not parts[0][:1].isalpha():  # 'srgb-linear' is a name, not a channel
        alpha = parts.pop()
    return parts, alpha


def parse(value):
    """A CSS colour string -> ((r, g, b), alpha), channels 0–1, or None when
    it is not a colour this module reads (lab(), a gradient, a var())."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v == "transparent":
        return (0.0, 0.0, 0.0), 0.0
    m = _HEX.match(v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        rgb = tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        alpha = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
        return rgb, alpha
    m = _FUNC.match(v)
    if not m:
        return None
    kind, args = m.group(1), m.group(2)
    try:
        parts, alpha_tok = _split(args)
        alpha = _num(alpha_tok) if alpha_tok is not None else 1.0
        if kind in ("rgb", "rgba"):
            if len(parts) != 3:
                return None
            rgb = tuple(_num(p, 255.0) / 255.0 for p in parts)
        elif kind == "oklch":
            if len(parts) != 3:
                return None
            rgb, _ = gamut_clip(_num(parts[0]), _num(parts[1], 0.4), _num(parts[2]))
        elif kind == "oklab":
            if len(parts) != 3:
                return None
            lin = _oklab_to_linear(_num(parts[0]), _num(parts[1], 0.4), _num(parts[2], 0.4))
            rgb = tuple(_encode(c) for c in lin)
        else:  # color(srgb r g b) / color(srgb-linear r g b)
            if len(parts) != 4 or parts[0] not in ("srgb", "srgb-linear"):
                return None
            rgb = tuple(_num(p) for p in parts[1:])
            if parts[0] == "srgb-linear":
                rgb = tuple(_encode(c) for c in rgb)
    except ValueError:
        return None
    return tuple(min(1.0, max(0.0, c)) for c in rgb), min(1.0, max(0.0, alpha))


def to_hex(rgb):
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def hex_rgb(value):
    """'#5b5bd6' -> (r, g, b). Raises ValueError on anything unreadable."""
    p = parse(value)
    if not p:
        raise ValueError(f"not a colour: {value!r}")
    return p[0]


# ---------------------------------------------------------------- WCAG


def _linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _encode(c):
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def luminance(rgb):
    """WCAG 2 relative luminance of an sRGB colour."""
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    """WCAG 2 contrast ratio of two sRGB colours; order does not matter."""
    a, b = luminance(fg), luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def composite(fg, alpha, bg):
    """fg at `alpha` painted over an opaque bg."""
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))


def is_large_text(px, weight):
    """WCAG 'large scale': 24px, or 18.66px (14pt) at bold weight."""
    try:
        w = int(float(weight or 400))
    except ValueError:
        w = 400
    return px >= 24 or (px >= 18.66 and w >= 700)


def required_ratio(px, weight):
    return 3.0 if is_large_text(px, weight) else 4.5


# ---------------------------------------------------------------- OKLCH


def _oklab_to_linear(L, a, b):
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def _lin_at(L, C, H):
    h = math.radians(H)
    return _oklab_to_linear(L, C * math.cos(h), C * math.sin(h))


def in_gamut(L, C, H, eps=1e-5):
    return all(-eps <= c <= 1 + eps for c in _lin_at(L, C, H))


def gamut_clip(L, C, H):
    """The most saturated in-gamut colour at this lightness and hue, never
    more saturated than C. Returns (sRGB, chroma actually used)."""
    L = min(1.0, max(0.0, L))
    if not in_gamut(L, C, H):
        lo, hi = 0.0, C
        for _ in range(32):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if in_gamut(L, mid, H) else (lo, mid)
        C = lo
    return tuple(_encode(c) for c in _lin_at(L, C, H)), C


def srgb_to_oklch(rgb):
    r, g, b = (_linear(c) for c in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(a, bb)
    H = math.degrees(math.atan2(bb, a)) % 360 if C > 1e-6 else 0.0
    return L, C, H


# ---------------------------------------------------------------- scales
#
# Twelve steps, each with one job (the Radix Colors model, MIT): 1 app
# background · 2 subtle background · 3 component · 4 hovered · 5 active ·
# 6 subtle border · 7 border · 8 hovered border · 9 solid accent · 10 hovered
# solid · 11 low-contrast text · 12 high-contrast text. The lightness ladders
# are Radix gray's own steps measured in OKLab. Steps 9–10 of an accent scale
# take the accent's lightness instead, and text steps are pushed until they
# clear their ratio on steps 1–3, so contrast is a property of the scale and
# not something a builder has to remember.

LIGHT_L = [0.991, 0.982, 0.955, 0.931, 0.907, 0.885, 0.852, 0.792, 0.643, 0.610, 0.503, 0.243]
DARK_L = [0.178, 0.213, 0.252, 0.285, 0.313, 0.348, 0.402, 0.489, 0.538, 0.583, 0.770, 0.949]
# the share of the accent's chroma each step carries
CHROMA = [0.03, 0.07, 0.14, 0.22, 0.30, 0.38, 0.48, 0.62, 1.0, 1.0, 0.85, 0.45]
# neutrals: tint strongest mid-scale, faint at the ends
NEUTRAL_SHAPE = [0.3, 0.4, 0.6, 0.7, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 0.9, 0.6]
NEUTRALS = {"gray": (0.0, 0.0), "cool": (0.012, 255.0), "warm": (0.010, 85.0)}
WHITE = (1.0, 1.0, 1.0)
INK = (0.067, 0.067, 0.075)


def quantize(rgb):
    """Round to what a hex colour can hold, so a ratio checked here is the
    ratio of the colour actually written out."""
    return tuple(round(min(1.0, max(0.0, c)) * 255) / 255 for c in rgb)


def _at(L, C, H):
    return quantize(gamut_clip(L, C, H)[0])


def _push(steps, i, theme, ratio, C, H, backgrounds=(0, 1, 2)):
    """Move step i away from the backgrounds (darker in light, lighter in
    dark) until it clears `ratio` on each of them."""
    L = srgb_to_oklch(steps[i])[0]
    step = -0.004 if theme == "light" else 0.004
    for _ in range(250):
        if all(contrast(steps[i], steps[b]) >= ratio for b in backgrounds):
            return
        L = min(1.0, max(0.0, L + step))
        steps[i] = _at(L, C, H)


def neutral_scale(theme="light", temperature="cool", tint_hue=None):
    """Twelve grays, tinted cool/warm, or toward `tint_hue` when temperature
    is 'tinted'."""
    ladder = LIGHT_L if theme == "light" else DARK_L
    if temperature == "tinted" and tint_hue is not None:
        C, H = 0.014, tint_hue
    else:
        C, H = NEUTRALS.get(temperature, NEUTRALS["gray"])
    steps = [_at(L, C * s, H) for L, s in zip(ladder, NEUTRAL_SHAPE)]
    _push(steps, 10, theme, 4.5, C * NEUTRAL_SHAPE[10], H)
    _push(steps, 11, theme, 7.0, C * NEUTRAL_SHAPE[11], H)
    return steps


def accent_scale(accent_rgb, theme="light", neutral=None):
    """Twelve steps of the accent's hue. In the light theme step 9 is the
    accent exactly; in the dark theme its lightness is held in 0.55–0.75 so
    the fill still reads as a fill. Step 11 (accent text, links) is pushed
    until it clears 4.5:1 on the neutral's steps 1–3."""
    L9, C9, H = srgb_to_oklch(accent_rgb)
    ladder = list(LIGHT_L if theme == "light" else DARK_L)
    if theme == "light":
        ladder[8], ladder[9] = L9, max(0.0, L9 - 0.04)
    else:
        L9 = min(max(L9, 0.55), 0.75)
        ladder[8], ladder[9] = L9, min(1.0, L9 + 0.04)
    steps = [_at(L, C9 * c, H) for L, c in zip(ladder, CHROMA)]
    if theme == "light":
        steps[8] = quantize(accent_rgb)
    base = neutral if neutral else steps
    probe = [base[0], base[1], base[2]] + steps[3:]
    _push(probe, 10, theme, 4.5, C9 * CHROMA[10], H)
    steps[10] = probe[10]
    return steps


def on_color(fill):
    """Text for a solid fill: white when it clears 4.5:1 (or beats ink),
    else near-black. Returns (rgb, ratio)."""
    cw, ci = contrast(WHITE, fill), contrast(INK, fill)
    return (WHITE, cw) if cw >= 4.5 or cw >= ci else (INK, ci)


def fit_accent_for_white(accent_rgb, max_shift=0.12):
    """Darken the accent along its own hue until white text on it clears
    4.5:1, when that costs no more than `max_shift` of OKLab lightness.
    Bright hues (yellow, lime, cyan) would need more; they keep their colour
    and take dark text. Returns (rgb, note or None)."""
    accent_rgb = quantize(accent_rgb)
    if contrast(WHITE, accent_rgb) >= 4.5:
        return accent_rgb, None
    L, C, H = srgb_to_oklch(accent_rgb)
    target = L
    while target > L - max_shift:
        target -= 0.004
        rgb = _at(target, C, H)
        if contrast(WHITE, rgb) >= 4.5:
            return rgb, (f"accent darkened from {to_hex(accent_rgb)} to {to_hex(rgb)} "
                         "so white text on it clears 4.5:1")
    return tuple(accent_rgb), (f"accent {to_hex(accent_rgb)} kept; it takes dark text "
                               "(white would need a much darker fill)")


# ---------------------------------------------------------------- CLI


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("contrast", help="WCAG ratio of two colours")
    c.add_argument("fg")
    c.add_argument("bg")
    s = sub.add_parser("scale", help="the 12-step scale of an accent colour")
    s.add_argument("accent")
    s.add_argument("--theme", default="light", choices=["light", "dark"])
    a = ap.parse_args(argv)
    try:
        if a.cmd == "contrast":
            print(round(contrast(hex_rgb(a.fg), hex_rgb(a.bg)), 2))
        else:
            print(json.dumps([to_hex(x) for x in accent_scale(hex_rgb(a.accent), a.theme)]))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
