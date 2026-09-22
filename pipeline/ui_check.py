#!/usr/bin/env python3
"""ui_check.py — the /jg-ui craft check: measured findings, never a gate.

    ui_check.py run   --workdir <project> [--routes / /inbox ...] [--base-url URL] [--label m1]
                      [--out DIR] [--backlog .loop/backlog.yaml] [--changed-since REF]
                      [--viewports 1440x900,375x812] [--no-dark] [--no-browser] [--judge] [--judge-budget 1.0]
    ui_check.py score --workdir <project> [same options]      last stdout line {"value": N} (Nightshift cmd scorer)
    ui_check.py grep  --workdir <project> [--changed-since REF] static findings only, no browser
    ui_check.py findings --captures DIR [--tokens design-tokens.json]   findings from existing measurements

What it reads: the project's design-tokens.json (the direction; without one the
kit's floor applies and a row asks for a direction), the served app (from
--base-url, or spec.stack.serve started here), and the UI source files.
What it writes: <out>/check.json, <out>/summary.md, the screenshots, and with
--backlog one row per finding (dimension ui, source ui), deduplicated by id;
open ui rows for a place this run checked and found clean are closed.

Three kinds of finding. `floor`: durable quality (contrast, targets, focus,
overflow, motion correctness, mobile zoom). `direction`: the page against the
project's own recorded direction. `fashion`: dated tells from ui_tells.json,
advisory at rung 2 and never scored. The score (0–100) is the floor plus the
direction, measured, so Nightshift can climb it; a model's opinion never
moves it (--judge findings are rows only).

Exit codes: 0 checked (whatever it found — this never blocks) · 4 could not
measure (no browser, no server) · 1 usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parent
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(ROOT / "loop"))
import backlog_io  # noqa: E402
import ui_color as uc  # noqa: E402

MEASURE = KIT / "js" / "ui_measure.cjs"
TELLS = KIT / "ui_tells.json"

DEFAULT_TARGETS = {  # no direction recorded: the kit's floor, nothing stylistic
    "body_px": None, "max_type_sizes": 8, "max_spacing_values": 14, "spacing_off_grid_pct": 20,
    "max_text_colors": 6, "max_typefaces": 2, "max_dominant_weight": 500, "max_radius_px": None,
    "max_shadows": 4, "max_ui_duration_ms": 300, "measure_ch": [45, 80],
}

# rule -> (kind, dimension, default severity, the fix in one line)
RULES = {
    "contrast": ("floor", "color", "major", "Use the direction's text roles: text and text-muted on bg clear 4.5:1 by construction."),
    "contrast-unknown": ("floor", "color", "nit", "Text over an image or gradient can't be measured; give it a solid scrim or check it by eye."),
    "small-body": ("floor", "type", "major", "Body text at 14–16px, weight 400; density comes from spacing, not smaller type."),
    "tiny-text": ("floor", "type", "minor", "Nothing under 12px; move labels onto the scale's smallest step."),
    "flat-hierarchy": ("floor", "type", "minor", "Make adjacent size steps at least 1.25× apart and use weight and colour with size."),
    "heavy-body": ("floor", "type", "minor", "Regular weight (400) carries the page; 600+ is for emphasis."),
    "too-many-fonts": ("floor", "type", "minor", "One family, plus a mono for code."),
    "long-lines": ("floor", "layout", "minor", "Cap prose at 60–75 characters (max-width: 65ch)."),
    "tight-leading": ("floor", "type", "minor", "Body line-height 1.4–1.6."),
    "wide-tracking": ("floor", "type", "minor", "Keep body tracking near 0; tighten display type instead."),
    "heading-rhythm": ("floor", "spacing", "nit", "More space above a heading than below it, so it belongs to what follows."),
    "nested-cards": ("floor", "layout", "minor", "One surface level: drop the border and shadow on inner cards."),
    "heavy-shadows": ("floor", "surface", "minor", "One or two elevation levels, blur under 24px."),
    "too-many-hues": ("floor", "color", "minor", "One accent hue plus neutrals; semantic colours only where they carry meaning."),
    "transition-all": ("floor", "motion", "minor", "Name the properties: transition: transform 160ms var(--ease-out), opacity …"),
    "layout-transition": ("floor", "motion", "minor", "Animate transform and opacity only."),
    "ease-in": ("floor", "motion", "minor", "Enter and exit with ease-out (var(--ease-out)); never ease-in on UI."),
    "bounce-easing": ("floor", "motion", "minor", "No overshoot on UI transitions; a spring bounces only after a gesture with momentum."),
    "slow-motion": ("floor", "motion", "minor", "UI motion under 300ms: presses 100–160, dropdowns 150–250."),
    "no-reduced-motion": ("floor", "motion", "minor", "Add @media (prefers-reduced-motion: reduce) keeping fades and dropping movement (useReducedMotion in Motion)."),
    "ungated-hover": ("floor", "motion", "minor", "Put hover motion under @media (hover: hover) and (pointer: fine)."),
    "scale-zero": ("floor", "motion", "minor", "Enter from scale(0.95) with opacity 0; nothing appears from scale(0)."),
    "small-targets": ("floor", "interaction", "major", "Targets 24×24px at least (44 on touch), or spaced so a 24px circle on each touches no other."),
    "wrapped-labels": ("floor", "interaction", "minor", "Buttons and nav items stay on one line; shorten the label or widen the control."),
    "no-focus-ring": ("floor", "interaction", "major", "Give every focusable element a visible :focus-visible style (outline: 2px solid var(--ring))."),
    "h-overflow": ("floor", "layout", "major", "Nothing wider than the viewport: drop fixed widths; let tables scroll inside their container."),
    "clipped-text": ("floor", "layout", "minor", "Let text wrap, or truncate with text-overflow: ellipsis and a title."),
    "stress-breaks": ("floor", "states", "minor", "Long names and numbers must wrap or truncate without breaking the layout."),
    "zoom-blocked": ("floor", "mobile", "major", "Remove maximum-scale / user-scalable=no; fix input zoom with 16px inputs instead."),
    "no-viewport-meta": ("floor", "mobile", "major", "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."),
    "small-inputs": ("floor", "mobile", "minor", "Inputs at 16px on phones, or iOS zooms the page on focus."),
    "vh-units": ("floor", "mobile", "minor", "Use 100dvh for app shells and 100svh for heroes, not 100vh / h-screen."),
    "console-errors": ("floor", "states", "minor", "Fix the errors this page logs."),
    "placeholder-copy": ("floor", "copy", "minor", "Replace placeholder copy (Lorem ipsum, John Doe, invented stats) with the product's own."),
    "missing-states": ("floor", "states", "minor", "Show loading, empty and error states for data this component fetches or lists."),
    "hardcoded-color": ("direction", "tokens", "minor", "Use the direction's variables (var(--primary), var(--muted-foreground) …), not literal colours."),
    "hardcoded-px": ("direction", "tokens", "minor", "Use the spacing and type tokens (var(--space-4), var(--text-sm)), not pixel literals."),
    "no-direction": ("direction", "tokens", "nit", "Record a direction: /jg-ui direction (or ui_direction.py propose)."),
    "off-body": ("direction", "type", "minor", "Set the body size to the direction's base."),
    "off-ratio": ("direction", "type", "minor", "Use the direction's type scale steps (var(--text-*))."),
    "too-many-sizes": ("direction", "type", "minor", "Keep to the direction's type steps."),
    "off-grid": ("direction", "spacing", "minor", "Keep spacing on the direction's scale (var(--space-*))."),
    "too-many-spacing": ("direction", "spacing", "minor", "Fewer spacing values: a short scale beats a divisible one."),
    "too-many-text-colors": ("direction", "color", "minor", "Text uses text, text-muted and the accent; not a new grey each time."),
    "radius-over": ("direction", "surface", "minor", "Use the direction's radii (var(--radius-sm/md/lg))."),
    "off-palette": ("direction", "color", "minor", "Colours outside the direction: map each to a role variable."),
    "no-dark": ("direction", "color", "minor", "The direction asks for a dark theme: import the generated tokens.css, which switches under prefers-color-scheme."),
    "emoji-icons": ("fashion", "tells", "minor", "Use one icon set (lucide-react ships with shadcn); no emoji in UI slots."),
    "gradient-hero": ("fashion", "tells", "minor", "Drop the purple-to-blue gradient; the accent is a solid, used sparingly."),
    "gradient-text": ("fashion", "tells", "minor", "Solid heading colour; gradient text reads as generated."),
    "ai-purple": ("fashion", "tells", "minor", "Tailwind's indigo/violet as the accent is the default everyone ships; take the direction's accent."),
    "overused-font": ("fashion", "tells", "nit", "The default family reads as a default; fine if the direction chose it on purpose."),
    "centered-everything": ("fashion", "tells", "minor", "Left-align running text; centre only short hero copy and empty states."),
    "em-dashes": ("fashion", "copy", "nit", "Fewer em dashes in UI copy."),
    "side-stripe": ("fashion", "tells", "nit", "A thick coloured left border on a card is a template tell; use the surface instead."),
    "glow-shadow": ("fashion", "tells", "minor", "Coloured glow shadows read as generated; a neutral shadow or none."),
    "shadcn-defaults": ("fashion", "tokens", "minor", "shadcn's untouched defaults are the most common tell: import the direction's tokens.css after globals.css."),
}

WEIGHT = {("floor", "major"): 10, ("floor", "minor"): 4, ("floor", "nit"): 1,
          ("direction", "major"): 6, ("direction", "minor"): 3, ("direction", "nit"): 1}
RULE_CAP = 20  # no single rule takes more than this off the score
SEV_RANK = {"major": 0, "minor": 1, "nit": 2}


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_tells():
    return load_json(TELLS) or {}


def targets_of(tokens):
    t = dict(DEFAULT_TARGETS)
    t.update(((tokens or {}).get("direction") or {}).get("targets") or {})
    return t


def finding(rule, where, title, evidence=None, severity=None):
    kind, dim, sev, fix = RULES[rule]
    return {"rule": rule, "kind": kind, "dimension": dim, "severity": severity or sev, "where": where,
            "title": title, "fix": fix, "evidence": [e for e in (evidence or []) if e][:6]}


def fid(f):
    return "ui-" + hashlib.sha256(f"{f['rule']}|{f['where']}".encode()).hexdigest()[:8]


# ---------------------------------------------------------------- measured findings

def _direction_palette(tokens):
    out = set()
    for roles in ((tokens or {}).get("colors") or {}).values():
        if isinstance(roles, dict):
            out |= {v.lower() for v in roles.values() if isinstance(v, str) and v.startswith("#")}
    out |= {c.lower() for c in (tokens or {}).get("chart") or []}
    return out


def _semantic_palette(tokens):
    """The direction's danger / warning / success colours, in every theme."""
    return {v.lower() for roles in ((tokens or {}).get("colors") or {}).values() if isinstance(roles, dict)
            for k, v in roles.items() if isinstance(v, str) and v.startswith("#") and k.split("-")[0] in ("danger", "warning", "success")}


def _near(hexa, palette, tol=0.03):
    try:
        a = uc.srgb_to_oklch(uc.hex_rgb(hexa))
    except ValueError:
        return True
    for p in palette:
        try:
            b = uc.srgb_to_oklch(uc.hex_rgb(p))
        except ValueError:
            continue
        dh = min(abs(a[2] - b[2]), 360 - abs(a[2] - b[2]))
        if abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol and (dh < 12 or a[1] < 0.03):
            return True
    return False


def _desktop(cap):
    return int(str(cap.get("viewport", "0x0")).split("x")[0] or 0) > 480


def capture_findings(cap, where, targets, tokens, tells):
    """Findings from one ui_measure.cjs capture."""
    out, p = [], cap.get("profile") or {}
    tag = f"{cap.get('viewport')} {cap.get('theme')}"

    def ev(*xs):
        return [f"{tag}: {x}" for x in xs if x]

    c = cap.get("contrast") or {}
    fails = sorted(c.get("failures") or [], key=lambda x: x["ratio"])
    if fails:
        w = fails[0]
        out.append(finding("contrast", where, f"{len(fails)} text/background pairs under their ratio; worst {w['ratio']}:1 "
                                              f"({w['fg']} on {w['bg']}, needs {w['need']}:1)",
                           ev(*[f"{x['ratio']}:1 {x['fg']} on {x['bg']} ×{x['count']}, e.g. {x['example']}" for x in fails[:4]])))
    if c.get("unknown"):
        out.append(finding("contrast-unknown", where, f"{c['unknown']} text elements sit on an image or gradient", ev()))
    body = p.get("bodyPx")
    if body and body < 14 and _desktop(cap):
        out.append(finding("small-body", where, f"Body text is {body}px", ev()))
    chars = p.get("chars") or 0
    tiny = sum(x["count"] for x in (cap.get("type") or {}).get("sizesByChars") or [] if x["value"] and x["value"] < 12)
    if chars and tiny / chars > 0.01:
        out.append(finding("tiny-text", where, f"{round(100 * tiny / chars)}% of text is under 12px", ev()))
    ladder = [s for s in p.get("sizesUsed") or [] if body and s >= body]
    chosen_ratio = ((tokens or {}).get("type") or {}).get("ratio")  # a dense direction picks small steps on purpose
    if p.get("largestStep") and p["largestStep"] < 1.25 and len(ladder) >= 3 and not (chosen_ratio and chosen_ratio < 1.25):
        out.append(finding("flat-hierarchy", where, f"Largest step between sizes is {p['largestStep']}×; hierarchy reads flat", ev()))
    try:
        heavy = int(float(p.get("dominantWeight") or 400)) > targets["max_dominant_weight"] and (p.get("dominantWeightSharePct") or 0) > 50
    except ValueError:
        heavy = False
    if heavy:
        out.append(finding("heavy-body", where, f"Most text is weight {p['dominantWeight']} ({p['dominantWeightSharePct']}%)", ev()))
    if (p.get("typefaces") or 0) > max(3, targets["max_typefaces"]):
        out.append(finding("too-many-fonts", where, f"{p['typefaces']} font families", ev()))
    hi = (targets.get("measure_ch") or [45, 80])[1]
    cpl = (cap.get("layout") or {}).get("measureCpl")
    if cpl and cpl > hi:
        out.append(finding("long-lines", where, f"Prose runs {cpl} characters a line", ev()))
    t = cap.get("type") or {}
    if t.get("tightLeading"):
        out.append(finding("tight-leading", where, f"{t['tightLeading']} paragraphs set tighter than 1.25", ev(*t.get("tightExamples", [])[:2])))
    if t.get("wideTracking"):
        out.append(finding("wide-tracking", where, f"{t['wideTracking']} text runs tracked wider than 0.05em", ev(*t.get("trackingExamples", [])[:2])))
    if t.get("headingRhythm"):
        out.append(finding("heading-rhythm", where, f"{t['headingRhythm']} headings sit closer to what's above than below", ev(*t.get("rhythmExamples", [])[:2])))
    s = cap.get("surface") or {}
    if (s.get("cardNestingDepth") or 0) >= 3:
        out.append(finding("nested-cards", where, f"Cards nested {s['cardNestingDepth']} deep", ev()))
    if (p.get("distinctShadows") or 0) > targets["max_shadows"] or (p.get("maxShadowBlurPx") or 0) > 24:
        out.append(finding("heavy-shadows", where, f"{p.get('distinctShadows')} shadow styles, blur up to {p.get('maxShadowBlurPx')}px", ev()))
    hues, sem = p.get("accentHues") or 0, _semantic_palette(tokens)
    if hues > 2 and sem:  # the direction's own danger / warning / success colours carry meaning; they aren't extra hues
        listed = [h for h in (cap.get("color") or {}).get("accents", []) if uc.parse(h)]
        bucket = lambda h: int(uc.srgb_to_oklch(uc.hex_rgb(h))[2] / 30 + 0.5) % 12  # noqa: E731 — ui_measure.cjs's hue buckets
        hues -= len({bucket(h) for h in listed if _near(h, sem)} - {bucket(h) for h in listed if not _near(h, sem)})
    if hues > 2:
        out.append(finding("too-many-hues", where, f"{hues} accent hues beyond the neutrals", ev(", ".join((cap.get("color") or {}).get("accents", [])[:6]))))
    m = cap.get("motion") or {}
    if m.get("transitionAll"):
        out.append(finding("transition-all", where, f"transition: all on {m['transitionAll']} elements", ev()))
    if m.get("layoutTransitions"):
        out.append(finding("layout-transition", where, f"{m['layoutTransitions']} transitions animate layout properties", ev(*m.get("layoutExamples", [])[:2])))
    if m.get("easeIn"):
        out.append(finding("ease-in", where, f"ease-in on {m['easeIn']} UI transitions", ev(*m.get("easeInExamples", [])[:2])))
    if m.get("bounce"):
        out.append(finding("bounce-easing", where, f"{m['bounce']} transitions overshoot", ev(*m.get("bounceExamples", [])[:2])))
    if m.get("over300ms"):
        out.append(finding("slow-motion", where, f"{m['over300ms']} UI transitions over {targets['max_ui_duration_ms']}ms", ev(*m.get("slowExamples", [])[:2])))
    if p.get("hasMotion") and not m.get("reducedMotionRule") and not m.get("unreadableSheets"):
        out.append(finding("no-reduced-motion", where, "Motion without a prefers-reduced-motion rule", ev()))
    if m.get("ungatedHoverTransforms"):
        out.append(finding("ungated-hover", where, f"{m['ungatedHoverTransforms']} :hover transforms fire on touch", ev()))
    g = cap.get("targets") or {}
    if g.get("under24"):
        out.append(finding("small-targets", where, f"{g['under24']} crowded targets under 24px", ev(*g.get("examples", [])[:2])))
    if g.get("wrappedLabels"):
        out.append(finding("wrapped-labels", where, f"{g['wrappedLabels']} buttons or nav items wrap to two lines", ev(*g.get("wrappedExamples", [])[:2])))
    fo = cap.get("focus") or {}
    if fo.get("tabbed") and (fo.get("visibleFocus") or 0) < fo["tabbed"]:
        out.append(finding("no-focus-ring", where, f"{fo['tabbed'] - fo['visibleFocus']} of {fo['tabbed']} focus stops show no focus ring", ev(*fo.get("missing", [])[:2])))
    o = cap.get("overflow") or {}
    if o.get("horizontal"):
        out.append(finding("h-overflow", where, f"Page scrolls sideways at {str(cap.get('viewport', '')).split('x')[0]}px", ev(*o.get("offenders", [])[:2])))
    if o.get("clippedText"):
        out.append(finding("clipped-text", where, f"{o['clippedText']} text boxes clip without an ellipsis", ev(*o.get("clippedExamples", [])[:2])))
    st = cap.get("stress") or {}
    if (st.get("horizontal") and not o.get("horizontal")) or (st.get("clippedText") or 0) > (o.get("clippedText") or 0):
        out.append(finding("stress-breaks", where, "Tripled text breaks the layout", ev(f"overflow {st.get('horizontal')}, clipped {st.get('clippedText')}")))
    mob = cap.get("mobile") or {}
    if mob.get("blocksZoom"):
        out.append(finding("zoom-blocked", where, "The viewport meta blocks zoom", ev(mob.get("viewportMeta"))))
    if mob.get("viewportMeta") is None and not _desktop(cap):
        out.append(finding("no-viewport-meta", where, "No viewport meta; phones render the desktop page shrunk", ev()))
    if mob.get("smallInputs"):
        out.append(finding("small-inputs", where, f"{mob['smallInputs']} inputs under 16px on a phone", ev(*mob.get("smallInputExamples", [])[:2])))
    if cap.get("consoleErrors"):
        out.append(finding("console-errors", where, f"{len(cap['consoleErrors'])} console errors", ev(*cap["consoleErrors"][:2])))
    tl = cap.get("tells") or {}
    if tl.get("placeholders"):
        out.append(finding("placeholder-copy", where, "Placeholder copy on the page", ev(*tl["placeholders"][:2])))

    # the page against its own direction
    if tokens:
        want = targets.get("body_px")
        if want and body and abs(body - want) > 1 and _desktop(cap):
            out.append(finding("off-body", where, f"Body is {body}px; the direction says {want}px", ev()))
        ratio = (tokens.get("type") or {}).get("ratio")
        if ratio and p.get("scaleRatio") and abs(p["scaleRatio"] - ratio) > 0.08:
            out.append(finding("off-ratio", where, f"Type scale ratio {p['scaleRatio']}; the direction's is {ratio}", ev(f"sizes {p.get('sizesUsed')}")))
        if len(p.get("sizesUsed") or []) > targets["max_type_sizes"]:
            out.append(finding("too-many-sizes", where, f"{len(p['sizesUsed'])} type sizes; the direction has {targets['max_type_sizes'] - 1}", ev(f"{p['sizesUsed']}")))
        on4 = p.get("spacingOn4Pct")
        if on4 is not None and on4 < 100 - targets["spacing_off_grid_pct"]:
            out.append(finding("off-grid", where, f"{on4}% of spacing on the 4px grid", ev(f"values carrying 90%: {p.get('spacingCarrying90Pct')}")))
        if (p.get("distinctSpacing") or 0) > targets["max_spacing_values"]:
            out.append(finding("too-many-spacing", where, f"{p['distinctSpacing']} spacing values", ev()))
        if (p.get("distinctTextColors") or 0) > targets["max_text_colors"]:
            out.append(finding("too-many-text-colors", where, f"{p['distinctTextColors']} text colours", ev()))
        rmax = targets.get("max_radius_px")
        if rmax and (p.get("dominantRadiusPx") or 0) > rmax:
            out.append(finding("radius-over", where, f"Dominant radius {p['dominantRadiusPx']}px; the direction tops out at {rmax}px", ev()))
        palette = _direction_palette(tokens)
        stray = [h for h in (cap.get("color") or {}).get("accents", []) if palette and not _near(h, palette)]
        if stray:
            out.append(finding("off-palette", where, f"{len(stray)} colours outside the direction", ev(", ".join(stray[:6]))))
    elif _desktop(cap):
        out.append(finding("no-direction", where, "No design direction recorded; the kit's floor applies, nothing stylistic", ev()))

    # fashion: dated, advisory, never scored
    if tl.get("emojiIcons"):
        out.append(finding("emoji-icons", where, f"{len(tl['emojiIcons'])} emoji used as icons", ev(*tl["emojiIcons"][:2])))
    if tl.get("purpleBlueGradientHero"):
        out.append(finding("gradient-hero", where, "Purple-to-blue gradient behind the first screen", ev()))
    if tl.get("gradientText"):
        out.append(finding("gradient-text", where, f"{tl['gradientText']} gradient text runs", ev()))
    purple = {h.lower() for h in tells.get("ai_purple", [])}
    hits = [h for h in (cap.get("color") or {}).get("accents", []) if h.lower() in purple]
    if hits:
        out.append(finding("ai-purple", where, "Tailwind indigo/violet as the accent", ev(", ".join(hits))))
    fams = [f["value"] for f in (cap.get("type") or {}).get("families") or []]
    chosen = ((tokens or {}).get("fonts") or {}).get("body", "")
    if fams and fams[0] in set(tells.get("overused_fonts", [])) and not chosen.startswith(f"'{fams[0]}'"):
        out.append(finding("overused-font", where, f"{fams[0]} as the page's family", ev()))
    if (p.get("centeredTextPct") or 0) > tells.get("centered_text_pct", 60):
        out.append(finding("centered-everything", where, f"{p['centeredTextPct']}% of text is centred", ev()))
    if (tl.get("emDashes") or 0) >= tells.get("em_dash_max", 8):
        out.append(finding("em-dashes", where, f"{tl['emDashes']} em dashes", ev()))
    if s.get("sideStripes"):
        out.append(finding("side-stripe", where, f"{s['sideStripes']} cards with a thick left stripe", ev()))
    if s.get("glowShadows"):
        out.append(finding("glow-shadow", where, f"{s['glowShadows']} coloured glow shadows", ev()))
    return out


def dark_findings(caps, where, tokens):
    theme = ((tokens or {}).get("direction") or {}).get("theme")
    light = [c for c in caps if c.get("theme") == "light" and _desktop(c)]
    dark = [c for c in caps if c.get("theme") == "dark" and _desktop(c)]
    if theme in ("both", "dark") and light and dark and light[0].get("pageBg") == dark[0].get("pageBg"):
        return [finding("no-dark", where, "The page looks the same under prefers-color-scheme: dark", [f"page ground {dark[0].get('pageBg')}"])]
    return []


# ---------------------------------------------------------------- static findings

UI_EXT = {".css", ".scss", ".sass", ".less", ".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", ".svelte-kit", ".pipeline", ".loop", "coverage", "vendor",
             ".venv", "venv", "__pycache__", ".turbo", "out", ".claude", "fixtures", "tests", "test", "__tests__", "storybook-static"}
TOKEN_FILE = re.compile(r"(^|/)(tokens?\.css|design-tokens\.json|tailwind\.config\.[cm]?[jt]s|theme\.css)$")
RX_HEX = re.compile(r"(?<![\w&/])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
RX_FUNC = re.compile(r"\b(?:rgba?|hsla?|oklch|oklab|lch|lab)\((?![^)]*var\()")
RX_PALETTE = re.compile(r"\b(?:bg|text|border|from|via|to|ring|fill|stroke|outline|decoration|divide|placeholder|caret|accent|shadow)-"
                        r"(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|"
                        r"purple|fuchsia|pink|rose)-(?:50|[1-9]00|950)\b")
RX_ARB_COLOR = re.compile(r"-\[(?:#|rgb|hsl|oklch)")
RX_CSS_PX = re.compile(r"\b(?:font-size|padding(?:-(?:top|right|bottom|left|inline|block))?|margin(?:-(?:top|right|bottom|left|inline|block))?"
                       r"|gap|row-gap|column-gap|border-radius|line-height|letter-spacing)\s*:\s*[^;{}]*?\b(\d+(?:\.\d+)?)px")
RX_ARB_PX = re.compile(r"\b(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y|space-x|space-y|text|rounded|leading|tracking)-\[\d+(?:\.\d+)?px\]")
RX_TRANS_ALL = re.compile(r"\btransition-all\b|transition\s*:\s*all\b")
RX_EASE_IN = re.compile(r"(?<![\w-])ease-in(?![\w-])")
RX_SCALE0 = re.compile(r"scale\(\s*0\s*\)|(?<![\w-])scale-0(?![\w.-])|\bscale\s*:\s*0\s*[,}]")
RX_VH = re.compile(r"(?<![\w.])100vh\b|(?<![\w-])(?:min-)?h-screen\b")
RX_REDUCED = re.compile(r"prefers-reduced-motion|useReducedMotion|motion-reduce:|reducedMotion")
RX_DATA = re.compile(r"\bfetch\(|useQuery|useSWR|axios\.|useLoaderData|trpc\.|createResource|useInfiniteQuery")
RX_LIST = re.compile(r"\.map\(\s*\(?\s*\w")
RX_LOADING = re.compile(r"loading|isPending|pending|skeleton|spinner|Suspense|aria-busy|fallback", re.I)
RX_ERROR = re.compile(r"\berror\b|isError|catch\s*\(|ErrorBoundary|onError", re.I)
RX_EMPTY = re.compile(r"\.length\s*===?\s*0|!\s*\w+(?:\?\.)?\.length|\bempty\b|isEmpty|no \w+ (?:yet|found)|nothing (?:here|yet)", re.I)
RX_ZOOM = re.compile(r"user-scalable\s*=\s*(no|0)|maximum-scale\s*=\s*1(\.0)?\b")


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", text)


def ui_files(workdir, changed_since=None, limit=800):
    workdir = Path(workdir)
    if changed_since:
        try:
            out = subprocess.run(["git", "-C", str(workdir), "diff", "--name-only", f"{changed_since}...HEAD"],
                                 capture_output=True, text=True, timeout=30).stdout.split()
            return [workdir / f for f in out if Path(f).suffix in UI_EXT and (workdir / f).is_file()][:limit]
        except (OSError, subprocess.SubprocessError):
            pass
    files = []
    for root, dirs, names in os.walk(workdir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for n in names:
            if Path(n).suffix in UI_EXT:
                files.append(Path(root) / n)
                if len(files) >= limit:
                    return files
    return files


def static_findings(workdir, tokens, tells, changed_since=None):
    """Findings from the UI source files, no browser. Returns (findings, meta)."""
    workdir = Path(workdir)
    out, reduced, generated_css = [], False, False
    for path in ui_files(workdir, changed_since):
        rel = str(path.relative_to(workdir))
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Generated by ui_direction.py" in raw:
            generated_css = True
            continue
        if len(raw) > 400_000:
            continue
        text = _strip_comments(raw)
        reduced = reduced or bool(RX_REDUCED.search(text))
        body = "\n".join(ln for ln in text.splitlines() if not re.match(r"\s*--[\w-]+\s*:", ln))  # custom properties are tokens
        if not TOKEN_FILE.search(rel):
            n = len(RX_HEX.findall(body)) + len(RX_FUNC.findall(body)) + len(RX_PALETTE.findall(body)) + len(RX_ARB_COLOR.findall(body))
            if n:
                ex = (RX_PALETTE.findall(body) or RX_HEX.findall(body) or ["rgb()/oklch() literal"])[:3]
                out.append(finding("hardcoded-color", rel, f"{n} literal colours", [f"e.g. {', '.join(ex)}"]))
            pxs = [v for v in RX_CSS_PX.findall(body) if float(v) > 2] + RX_ARB_PX.findall(body)
            if pxs:
                out.append(finding("hardcoded-px", rel, f"{len(pxs)} pixel literals for type or spacing", [f"e.g. {', '.join(str(x) for x in pxs[:3])}"]))
        if RX_TRANS_ALL.search(body):
            out.append(finding("transition-all", rel, "transition: all in source"))
        if RX_EASE_IN.search(body):
            out.append(finding("ease-in", rel, "ease-in in source"))
        if RX_SCALE0.search(body):
            out.append(finding("scale-zero", rel, "An element enters from scale(0)"))
        if RX_VH.search(body):
            out.append(finding("vh-units", rel, "100vh / h-screen (jumps under mobile browser bars)"))
        if RX_ZOOM.search(body):
            out.append(finding("zoom-blocked", rel, "The viewport meta blocks zoom"))
        if path.suffix in (".tsx", ".jsx", ".vue", ".svelte"):
            data, listing = bool(RX_DATA.search(text)), bool(RX_LIST.search(text)) and "<" in text
            missing = []
            if data and not RX_LOADING.search(text):
                missing.append("loading")
            if data and not RX_ERROR.search(text):
                missing.append("error")
            if (data or listing) and not RX_EMPTY.search(text):
                missing.append("empty")
            if missing:
                why = "fetches data" if data else "renders a list"
                out.append(finding("missing-states", rel, f"{why}; no sign of a {', '.join(missing)} state (checked by name)"))
    comp = load_json(workdir / "components.json")
    if comp is not None and not generated_css:
        out.append(finding("shadcn-defaults", "components.json", "shadcn/ui installed without the direction's tokens.css",
                           [f"baseColor {((comp.get('tailwind') or {}).get('baseColor'))}"]))
    return out, {"reduced_motion_in_source": reduced}


# ---------------------------------------------------------------- merge, score, backlog

def merge(findings):
    by = {}
    for f in findings:
        k = (f["rule"], f["where"])
        if k not in by:
            by[k] = dict(f, evidence=list(f["evidence"]))
            continue
        cur = by[k]
        if SEV_RANK[f["severity"]] < SEV_RANK[cur["severity"]]:
            cur["severity"], cur["title"] = f["severity"], f["title"]
        cur["evidence"] = (cur["evidence"] + [e for e in f["evidence"] if e not in cur["evidence"]])[:6]
    out = list(by.values())
    for f in out:
        f["id"] = fid(f)
    order = {"floor": 0, "direction": 1, "fashion": 2, "judged": 3}
    return sorted(out, key=lambda f: (order.get(f["kind"], 4), SEV_RANK[f["severity"]], f["where"], f["rule"]))


def score(findings):
    """100 minus the weighted floor and direction findings; fashion and
    judged findings never count."""
    per_rule = {}
    for f in findings:
        w = WEIGHT.get((f["kind"], f["severity"]), 0)
        per_rule[f["rule"]] = min(RULE_CAP, per_rule.get(f["rule"], 0) + w)
    return max(0, round(100 - sum(per_rule.values())))


def feed_backlog(findings, backlog_path, checked, label):
    """One row per finding, deduplicated by id; open ui rows for a place
    this run checked that no longer fire are closed. Returns (added, closed)."""
    rows = backlog_io.load(backlog_path) if os.path.exists(backlog_path) else []
    ids = {r.get("id") for r in rows}
    live = {f["id"] for f in findings}
    added = closed = 0
    for f in findings:
        if f["id"] in ids:
            continue
        rows.append({"id": f["id"], "title": f"{f['where']}: {f['title']}", "dimension": "ui", "source": "ui", "status": "open",
                     "rung": 2 if f["kind"] == "fashion" or f["severity"] == "nit" else 1, "est": "S", "attempts": 0,
                     "iter_added": 0, "note": f"ui {f['kind']} {f['severity']} @ {f['where']} · fix: {f['fix']}"})
        added += 1
    for r in rows:
        m = re.match(r"ui \w+ \w+ @ (\S+)", str(r.get("note", "")))
        if (str(r.get("id", "")).startswith("ui-") and r.get("status") == "open" and m and m.group(1) in checked
                and r["id"] not in live):
            r["status"] = "done"
            r["note"] = f"{r.get('note', '')}; superseded: clean ui check {label}"
            closed += 1
    if added or closed:
        Path(backlog_path).parent.mkdir(parents=True, exist_ok=True)
        backlog_io.dump(rows, backlog_path)
    return added, closed


# ---------------------------------------------------------------- measuring

def spec_routes(spec):
    urls = []
    for f in (spec or {}).get("features") or []:
        for c in f.get("acceptance") or []:
            u = c.get("url")
            if isinstance(u, str) and u.startswith("/"):
                urls.append(u)
    return list(dict.fromkeys(["/"] + urls))[:8]


def measure(urls, out_dir, viewports, themes, stress=True):
    cmd = ["node", str(MEASURE), "--out", str(out_dir), "--viewports", ",".join(viewports), "--themes", ",".join(themes)]
    if stress:
        cmd.append("--stress")
    for u in urls:
        cmd += ["--url", u]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120 + 60 * len(urls) * len(viewports) * len(themes))
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"could not run ui_measure.cjs: {e}"
    caps = []
    for line in proc.stdout.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        cap = load_json(r["json"]) if r.get("ok") and r.get("json") else None
        if cap:
            caps.append(cap)
    return caps, None if caps else (proc.stderr.strip()[-300:] or "no capture succeeded")


def route_of(url, base):
    return (url[len(base):] or "/") if base and url.startswith(base) else url


def judge(out_dir, caps, tokens_path, budget, model="sonnet"):
    """The craft judge: a fresh ui-judge agent grades the screenshots before
    it sees any measured finding. Returns its grounded findings (rows only)."""
    try:
        import persona_run  # the kit's metered claude -p spawner
    except ImportError:
        return [], "persona_run.py missing", 0.0
    census = {c["screenshot"]: [e["sel"] for e in c.get("census") or []] for c in caps if c.get("screenshot")}
    if tokens_path:  # the child may only read inside its own directory
        direction = Path(out_dir) / "direction.json"
        direction.write_text(Path(tokens_path).read_text())
        tokens_path = direction
    rubric = Path(out_dir) / "ui-rubric.md"
    rubric.write_text((KIT / "prompts" / "ui-rubric.md").read_text())
    inp = {"shots": list(census), "direction": str(tokens_path) if tokens_path else "none", "census": census,
           "rubric": str(rubric), "out": str(Path(out_dir) / "judge.json")}
    (Path(out_dir) / "judge-input.json").write_text(json.dumps(inp, indent=2))
    prompt = f"Invoke the ui-judge agent with exactly this JSON and nothing else: {json.dumps(inp)} Then stop."
    cost, err = persona_run.claude(prompt, model, budget, str(out_dir), {}, ["ui-judge"], turns=12, run_dir=str(out_dir), tag="ui-judge")
    j = load_json(Path(out_dir) / "judge.json")
    if not j:  # it answered in its final message but couldn't write the file: keep the verdict
        text = ((load_json(Path(out_dir) / "ui-judge.claude.json") or {}).get("result") or "")
        s, e = text.find("{"), text.rfind("}")
        try:
            j = json.loads(text[s:e + 1]) if 0 <= s < e else None
        except ValueError:
            j = None
        if j and "scores" in j:
            (Path(out_dir) / "judge.json").write_text(json.dumps(j, indent=2) + "\n")
        else:
            return [], err or "the judge wrote nothing", cost
    return ground(j, caps), None, cost


def ground(j, caps):
    """The judge's findings that point at a real element on a captured page
    (verdict's grounding rule); the rest are dropped."""
    known = {e["sel"] for c in caps for e in c.get("census") or []}
    grounded = []
    for f in j.get("findings") or []:
        if f.get("sel") not in known:
            continue  # points at nothing on the page: dropped (verdict's grounding rule)
        dim = str(f.get("dimension", "craft"))
        grounded.append({"rule": f"judged-{dim}", "kind": "judged", "dimension": dim,
                         "severity": f.get("severity") if f.get("severity") in SEV_RANK else "minor",
                         "where": str(f.get("route", "/")), "title": str(f.get("title", ""))[:140],
                         "fix": str(f.get("fix", ""))[:200], "evidence": [str(f.get("sel"))]})
    for f in grounded:
        f["id"] = fid(f)
    return grounded


# ---------------------------------------------------------------- CLI

def _base_and_server(a, spec):
    if a.base_url:
        return a.base_url.rstrip("/"), None
    serve = ((spec or {}).get("stack") or {}).get("serve") or {}
    if not serve.get("cmd") or not serve.get("port"):
        return None, None
    import accept  # the kit's one-server-per-run helper
    return accept.SERVER.ensure(serve["cmd"], serve["port"], str(a.workdir)), accept.SERVER


def summary_md(report):
    lines = [f"# UI check {report['label']} — score {report['score']}", "",
             f"Measured {report['measured_at']} · routes {', '.join(report['routes']) or 'none'} · "
             f"direction {report['direction'] or 'none recorded'}", ""]
    lines += [f"> {n}" for n in report["notes"]]
    lines += ["", "| kind | severity | where | what | fix |", "|---|---|---|---|---|"]
    for f in report["findings"] + report.get("judged", []):
        lines.append(f"| {f['kind']} | {f['severity']} | `{f['where']}` | {f['title']} | {f['fix']} |")
    lines += ["", "Never a gate: every row is advice, recorded in the backlog (dimension `ui`).", ""]
    return "\n".join(lines)


def run(a, want_score=False):
    workdir = Path(a.workdir).resolve()
    tokens_path = Path(a.tokens) if a.tokens else workdir / "design-tokens.json"
    tokens = load_json(tokens_path) if tokens_path.exists() else None
    if tokens is not None and not isinstance(tokens.get("direction"), dict):
        tokens = dict(tokens, direction={"targets": {}})  # extracted tokens: a palette, no stated direction
    tells, targets = load_tells(), targets_of(tokens)
    spec = load_json(workdir / "spec.json") or {}
    label = a.label or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(a.out) if a.out else workdir / ".pipeline" / "ui" / "check" / label
    out_dir.mkdir(parents=True, exist_ok=True)

    findings, caps, notes, routes = [], [], [], []
    static, meta = static_findings(workdir, tokens, tells, a.changed_since)
    checked = {str(p.relative_to(workdir)) for p in ui_files(workdir, a.changed_since)} | {"components.json"}
    if not a.no_browser:
        base, server = _base_and_server(a, spec)
        try:
            if not base:
                notes.append("not measured: no --base-url and no spec.stack.serve")
            else:
                routes = a.routes or spec_routes(spec)
                caps, err = measure([base + r for r in routes], out_dir / "shots", a.viewports.split(","),
                                    ["light"] if a.no_dark else ["light", "dark"])
                if err:
                    notes.append(f"measure: {err}")
                by_route = {}
                for c in caps:
                    by_route.setdefault(route_of(c.get("url", ""), base), []).append(c)
                for r, cs in by_route.items():
                    for c in cs:
                        findings += capture_findings(c, r, targets, tokens, tells)
                    findings += dark_findings(cs, r, tokens)
                    checked.add(r)
        finally:
            if server:
                server.stop()
    if meta["reduced_motion_in_source"]:  # a JS reduced-motion hook satisfies it without a CSS media query
        findings = [f for f in findings if f["rule"] != "no-reduced-motion"]
    findings = merge(findings + static)
    judged, judge_cost = [], 0.0
    if a.judge and caps:
        judged, jerr, judge_cost = judge(out_dir, caps, tokens_path if tokens else None, a.judge_budget)
        judge_cost = judge_cost or 0.0
        if jerr:
            notes.append(f"judge: {jerr}")
        if judge_cost:
            ledger = Path(a.ledger) if a.ledger else workdir / ".pipeline" / "ledger.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a") as fh:
                fh.write(json.dumps({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "stage": "ui",
                                     "id": label, "cost_usd": round(judge_cost, 4)}) + "\n")
    value = score(findings)
    counts = {k: sum(1 for f in findings if f["kind"] == k) for k in ("floor", "direction", "fashion")}
    report = {"label": label, "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "score": value,
              "direction": str(tokens_path) if tokens else None, "routes": routes, "notes": notes, "counts": counts,
              "findings": findings, "judged": judged, "judge_cost_usd": round(judge_cost, 4),
              "captures": [{"url": c.get("url"), "viewport": c.get("viewport"), "theme": c.get("theme"),
                            "screenshot": c.get("screenshot"), "profile": c.get("profile")} for c in caps]}
    (out_dir / "check.json").write_text(json.dumps(report, indent=2) + "\n")
    (out_dir / "summary.md").write_text(summary_md(report))
    added = closed = 0
    if a.backlog:
        added, closed = feed_backlog(findings + judged, a.backlog, checked, label)
    majors = sum(1 for f in findings if f["kind"] == "floor" and f["severity"] == "major")
    print(f"ui check {label}: score {value} · {counts['floor']} floor ({majors} major) · {counts['direction']} direction · "
          f"{counts['fashion']} fashion (advisory) · {len(routes)} routes measured"
          + (f" · {len(judged)} judged" if a.judge else "") + (f" · backlog +{added} closed {closed}" if a.backlog else ""))
    for n in notes:
        print(f"note: {n}")
    if want_score:
        print(json.dumps({"value": value, "floor": counts["floor"], "direction": counts["direction"], "measured": bool(caps)}))
    return 4 if (not caps and not a.no_browser) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "score", "grep"):
        p = sub.add_parser(name)
        p.add_argument("--workdir", default=".")
        p.add_argument("--tokens")
        p.add_argument("--changed-since")
        p.add_argument("--label")
        p.add_argument("--out")
        p.add_argument("--backlog")
        if name != "grep":
            p.add_argument("--routes", nargs="*")
            p.add_argument("--base-url")
            p.add_argument("--viewports", default="1440x900,375x812")
            p.add_argument("--no-dark", action="store_true")
            p.add_argument("--no-browser", action="store_true")
            p.add_argument("--judge", action="store_true")
            p.add_argument("--judge-budget", type=float, default=1.0)
            p.add_argument("--ledger", help="where the judge's cost is recorded (default <workdir>/.pipeline/ledger.jsonl)")
    fp = sub.add_parser("findings")
    fp.add_argument("--captures", required=True)
    fp.add_argument("--tokens")
    jp = sub.add_parser("judged", help="merge a ui-judge's judge.json into a check's record and the backlog")
    jp.add_argument("--check-dir", required=True)
    jp.add_argument("--backlog")
    a = ap.parse_args(argv)
    if a.cmd == "judged":
        d = Path(a.check_dir)
        report, j = load_json(d / "check.json"), load_json(d / "judge.json")
        if not report or not j:
            print(f"need {d / 'check.json'} and {d / 'judge.json'}", file=sys.stderr)
            return 1
        caps = [c for c in (load_json(p) for p in sorted((d / "shots").glob("*.json"))) if c and "census" in c]
        judged = ground(j, caps)
        report["judged"], report["judge_scores"] = judged, j.get("scores")
        (d / "check.json").write_text(json.dumps(report, indent=2) + "\n")
        (d / "summary.md").write_text(summary_md(report))
        added = feed_backlog(judged, a.backlog, set(), report["label"])[0] if a.backlog else 0
        dropped = len(j.get("findings") or []) - len(judged)
        print(f"judged: scores {j.get('scores')} · {len(judged)} findings kept, {dropped} dropped (no element) · backlog +{added}")
        return 0
    if a.cmd == "grep":
        tokens = load_json(a.tokens) if a.tokens else load_json(Path(a.workdir) / "design-tokens.json")
        fs, _ = static_findings(a.workdir, tokens, load_tells(), a.changed_since)
        for f in merge(fs):
            print(f"{f['kind']:9} {f['severity']:5} {f['where']}: {f['title']}")
        return 0
    if a.cmd == "findings":
        tokens = load_json(a.tokens) if a.tokens else None
        tells, targets = load_tells(), targets_of(tokens)
        caps = [c for c in (load_json(p) for p in sorted(Path(a.captures).glob("*.json"))) if c and "profile" in c]
        fs = merge([f for c in caps for f in capture_findings(c, c.get("url", "?"), targets, tokens, tells)])
        print(json.dumps({"score": score(fs), "findings": fs}, indent=2))
        return 0
    return run(a, want_score=(a.cmd == "score"))


if __name__ == "__main__":
    sys.exit(main())
