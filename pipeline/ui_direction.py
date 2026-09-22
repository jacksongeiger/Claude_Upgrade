#!/usr/bin/env python3
"""ui_direction.py — author a project's design direction as tokens, stdlib only.

A direction is one recorded answer to "what should a finished screen look
like?" for THIS project, expressed as numbers a builder can be held to: a
type scale, a spacing scale, colour roles for light and dark, radii, shadows
and motion. Extraction (tokens_extract.py) needs a page that already looks
good; this writes the system for a project that has none.

    ui_direction.py looks [--json]                         the looks, one per line
    ui_direction.py propose --look calm-dense [--accent '#5b5bd6'] [--neutral cool|warm|gray|tinted]
                            [--theme light|dark|both] [--density compact|comfortable] [--font system|<family>]
                            [--semantic] [--avoid '<text>' ...] [--reference <url> ...] [--name <project>]
                            --out design-tokens.json [--css tokens.css] [--force]
    ui_direction.py validate design-tokens.json            contrast and scale checks
    ui_direction.py css design-tokens.json --out tokens.css
    ui_direction.py designmd design-tokens.json --out DESIGN.md
    ui_direction.py import DESIGN.md --out <proposal.json>  a DESIGN.md (Claude Design export, brand system) -> a direction
    ui_direction.py adopt <capture.json> --out <proposal.json>  a measured reference page -> a direction
    ui_direction.py picker a.json b.json [c.json ...] --out picker.html [--title <product>]

`propose`, `import` and `adopt` also take --css and --designmd to write the
companions in the same call.

`propose` never overwrites an existing --out without --force, and the
/jg-ui command never passes --force for the project's own
design-tokens.json: adopting a direction is the human's call. The CSS
carries the kit's variable names and the shadcn/ui names (--background,
--primary, --ring …), so components pulled from shadcn-style registries
(shadcn/ui, Kokonut UI, Bklit) follow the direction without edits.

Exit codes: 0 ok · 2 validation failed (problems on stdout) · 1 usage.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_color as uc  # noqa: E402

# ---------------------------------------------------------------- looks
#
# A look is a starting point, not a brand: the human picks one per project
# and its numbers become that project's targets. Motion values follow Emil
# Kowalski's animation standards (github.com/emilkowalski/skills, MIT, read
# at 85e8e23): ease-out on enter and exit, UI under 300 ms, springs with no
# bounce unless a gesture carried momentum.

LOOKS = {
    "calm-dense": {
        "words": "Calm and dense, like Linear",
        "fits": "dashboards, internal tools, consoles, data-heavy apps",
        "base": 14, "ratio": 1.125, "density": "compact", "neutral": "cool", "accent": "#5b5bd6",
        "radius": [4, 6, 8], "shadow": "flat", "motion": [120, 160, 220], "bounce": 0.0,
        "reference": "https://linear.app",
    },
    "precise": {
        "words": "Stark and precise, like Vercel",
        "fits": "developer tools, status pages, docs, deploy dashboards",
        "base": 14, "ratio": 1.2, "density": "comfortable", "neutral": "gray", "accent": "#0070f3",
        "radius": [6, 8, 12], "shadow": "border", "motion": [120, 180, 240], "bounce": 0.0,
        "reference": "https://vercel.com",
    },
    "editorial": {
        "words": "Clean and editorial, like Stripe",
        "fits": "marketing sites, documentation, content, landing pages that stay serious",
        "base": 16, "ratio": 1.25, "density": "comfortable", "neutral": "cool", "accent": "#635bff",
        "radius": [4, 8, 12], "shadow": "soft", "motion": [150, 220, 300], "bounce": 0.0,
        "reference": "https://stripe.com",
    },
    "dark-compact": {
        "words": "Dark-first and compact, like Raycast",
        "fits": "launchers, command palettes, power tools, desktop-feel apps",
        "base": 14, "ratio": 1.15, "density": "compact", "neutral": "cool", "accent": "#ff6363",
        "radius": [6, 8, 12], "shadow": "soft", "motion": [100, 150, 200], "bounce": 0.0,
        "reference": "https://www.raycast.com", "theme": "dark",
    },
    "friendly": {
        "words": "Warm and friendly, like Airbnb",
        "fits": "consumer apps, marketplaces, personal finance, anything people use at home",
        "base": 16, "ratio": 1.2, "density": "comfortable", "neutral": "warm", "accent": "#e5484d",
        "radius": [8, 12, 16], "shadow": "soft", "motion": [160, 220, 300], "bounce": 0.15,
        "reference": "https://www.airbnb.com",
    },
    "expressive": {
        "words": "Bold and expressive, like an Apple product page",
        "fits": "launches, portfolios, campaigns, one-page products",
        "base": 16, "ratio": 1.333, "density": "comfortable", "neutral": "gray", "accent": "#12a594",
        "radius": [8, 12, 20], "shadow": "soft", "motion": [180, 260, 400], "bounce": 0.2,
        "reference": "https://www.apple.com",
    },
}

SPACING = {
    "compact": [2, 4, 6, 8, 12, 16, 24, 32, 48],
    "comfortable": [4, 8, 12, 16, 24, 32, 48, 64, 96],
}
SIZE_NAMES_BELOW = ["sm", "xs"]
SIZE_NAMES_ABOVE = ["lg", "xl", "2xl", "3xl", "4xl", "5xl"]
SYSTEM_SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
SYSTEM_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
EASE = {  # cubic-bezier control points
    "out": [0.23, 1, 0.32, 1],          # enter / exit, most UI
    "in_out": [0.77, 0, 0.175, 1],      # moving or morphing on screen
    "drawer": [0.32, 0.72, 0, 1],       # sheets and drawers
}
SEMANTIC_HUES = {"danger": "#e5484d", "warning": "#ffb224", "success": "#30a46c"}
SHADOWS = {
    "flat": {"sm": "0 1px 2px rgb(0 0 0 / 0.05)", "md": "0 2px 8px rgb(0 0 0 / 0.08)"},
    "border": {"sm": "0 0 0 1px rgb(0 0 0 / 0.08)", "md": "0 0 0 1px rgb(0 0 0 / 0.08), 0 4px 12px rgb(0 0 0 / 0.06)"},
    "soft": {"sm": "0 1px 3px rgb(0 0 0 / 0.08)", "md": "0 4px 16px rgb(0 0 0 / 0.10)"},
}


def type_scale(base, ratio):
    """Seven sizes: one step below the base, the base, five above; integers,
    nothing under 12px. Named sm, base, lg, xl, 2xl, 3xl, 4xl."""
    sizes = []
    for k in range(-1, 6):
        s = max(12, round(base * ratio ** k))
        if not sizes or s > sizes[-1]:
            sizes.append(s)
    i_base = sizes.index(base) if base in sizes else 1
    out = {}
    for i, s in enumerate(sizes):
        if i < i_base:
            name = SIZE_NAMES_BELOW[i_base - i - 1]
        elif i == i_base:
            name = "base"
        else:
            name = SIZE_NAMES_ABOVE[i - i_base - 1]
        out[name] = s
    return out


def leading(px):
    """Line height by size: loose for reading, tight for display."""
    if px <= 16:
        return 1.5
    if px < 24:
        return 1.35
    if px < 36:
        return 1.2
    return 1.1


def tracking(px):
    """Tracking by size: negative as type grows, a hair positive when small."""
    if px >= 36:
        return "-0.02em"
    if px >= 24:
        return "-0.01em"
    if px <= 12:
        return "0.01em"
    return "0"


def font_stack(font):
    if not font or font == "system":
        return SYSTEM_SANS
    return f"'{font}', {SYSTEM_SANS}"


def _roles(theme, n, a, on_accent, border_strong, semantic):
    hx = uc.to_hex
    r = {
        "bg": hx(n[0]), "bg-subtle": hx(n[1]),
        "card": "#ffffff" if theme == "light" else hx(n[1]),
        "surface": hx(n[2]), "surface-hover": hx(n[3]), "surface-active": hx(n[4]),
        "border-subtle": hx(n[5]), "border": hx(n[6]), "border-hover": hx(n[7]),
        "border-strong": hx(border_strong),
        "text-muted": hx(n[10]), "text": hx(n[11]),
        "accent": hx(a[8]), "accent-hover": hx(a[9]), "accent-subtle": hx(a[2]),
        "accent-border": hx(a[6]), "accent-text": hx(a[10]), "on-accent": hx(on_accent),
        "ring": hx(_ring(n, a, theme)),
    }
    for name, scale in semantic.items():
        r[name] = hx(scale[8])
        r[f"{name}-subtle"] = hx(scale[2])
        r[f"{name}-text"] = hx(scale[10])
    return r


def _ring(n, a, theme):
    """The focus ring: the accent when it clears 3:1 on the page and the
    card, else the accent's text step (a bright yellow ring on white is
    invisible; its dark text step is not)."""
    card = (1.0, 1.0, 1.0) if theme == "light" else n[1]
    for step in (a[8], a[10]):
        if uc.contrast(step, n[0]) >= 3.0 and uc.contrast(step, card) >= 3.0:
            return step
    return a[10]


def _border_strong(n, theme):
    """A neutral that clears 3:1 against the page, for input and control
    edges (WCAG 1.4.11). Decorative borders stay softer."""
    probe = list(n)
    uc._push(probe, 7, theme, 3.0, 0.0, 0.0)
    return probe[7]


def _chart_colors(accent_rgb):
    """Five series colours: the accent, then hues 72° apart at its lightness."""
    L, C, H = uc.srgb_to_oklch(accent_rgb)
    C = max(C, 0.12)
    return [uc.to_hex(accent_rgb)] + [uc.to_hex(uc.gamut_clip(L, C, (H + 72 * i) % 360)[0]) for i in range(1, 5)]


def propose(look="calm-dense", accent=None, neutral=None, theme=None, density=None, font="system",
            semantic=True, avoid=None, references=None, name=None, overrides=None):
    """A full direction from a look. `overrides` replaces the look's own
    numbers (base, ratio, radius [sm, md, lg], motion [fast, base, slow],
    shadow, bounce) — how a measured reference or an imported DESIGN.md
    becomes a direction without being forced into a preset."""
    if look not in LOOKS:
        raise ValueError(f"unknown look {look!r}; one of: {', '.join(LOOKS)}")
    p = dict(LOOKS[look], **{k: v for k, v in (overrides or {}).items() if v is not None})
    neutral = neutral or p["neutral"]
    theme = theme or p.get("theme", "both")
    density = density or p["density"]
    notes = []

    accent_rgb, note = uc.fit_accent_for_white(uc.hex_rgb(accent or p["accent"]))
    if note:
        notes.append(note)
    tint = uc.srgb_to_oklch(accent_rgb)[2]
    colors, scales = {}, {"neutral": {}, "accent": {}}
    for t in ("light", "dark"):
        n = uc.neutral_scale(t, neutral, tint_hue=tint)
        a = uc.accent_scale(accent_rgb, t, neutral=n)
        on, _ = uc.on_color(a[8])
        sem = {}
        if semantic:
            for sname, shex in SEMANTIC_HUES.items():
                sem[sname] = uc.accent_scale(uc.hex_rgb(shex), t, neutral=n)
        colors[t] = _roles(t, n, a, on, _border_strong(n, t), sem)
        scales["neutral"][t] = [uc.to_hex(x) for x in n]
        scales["accent"][t] = [uc.to_hex(x) for x in a]
    sizes = type_scale(p["base"], p["ratio"])
    radii = p["radius"]
    fast, base_ms, slow = p["motion"]
    return {
        "source": {"kind": "direction", "look": look, "by": "ui_direction.py",
                   "made_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "direction": {
            "name": name or "",
            "look": look, "words": p["words"], "fits": p["fits"],
            "theme": theme, "density": density, "accent": uc.to_hex(accent_rgb), "neutral": neutral,
            "references": [{"url": u} for u in (list(references or []) or [p["reference"]])],
            "avoid": list(avoid or []),
            "components": {
                "primitives": "shadcn/ui, style new-york-v4 (ui.shadcn.com/r/styles/new-york-v4/<name>.json)",
                "decorative": "Kokonut UI (kokonutui.com/r/<name>.json) — marketing, showcase, AI-chat pieces",
                "charts": "Bklit (ui.bklit.com/r/<name>.json); recharts; Liveline for streaming data",
                "motion": "Motion (motion.dev, import from motion/react) for springs, layout and exit; CSS transitions for hover and fades",
                "toasts": "Sonner", "command_menu": "cmdk", "drawers": "Vaul", "numbers": "NumberFlow",
            },
            "targets": {
                "body_px": p["base"], "max_type_sizes": len(sizes) + 1, "max_spacing_values": len(SPACING[density]) + 3,
                "spacing_off_grid_pct": 10, "max_text_colors": 5, "max_typefaces": 2, "max_dominant_weight": 500,
                "max_radius_px": radii[-1], "max_shadows": 3, "max_ui_duration_ms": 300, "measure_ch": [45, 80],
            },
            "notes": notes,
        },
        "fonts": {"body": font_stack(font), "heading": font_stack(font), "mono": SYSTEM_MONO},
        "type_scale": sizes,
        "type": {
            "ratio": p["ratio"],
            "line_height": {k: leading(v) for k, v in sizes.items()},
            "tracking": {k: tracking(v) for k, v in sizes.items()},
            "weights": {"regular": 400, "medium": 500, "semibold": 600},
        },
        "spacing": {("%g" % (v / 4)): v for v in SPACING[density]},
        "colors": colors,
        "scales": scales,
        "chart": _chart_colors(accent_rgb),
        "radius": {"sm": f"{radii[0]}px", "md": f"{radii[1]}px", "lg": f"{radii[2]}px", "full": "9999px"},
        "shadow": SHADOWS[p["shadow"]],
        "motion": {
            "duration_ms": {"fast": fast, "base": base_ms, "slow": slow},
            "ease": EASE,
            "spring": {"type": "spring", "bounce": 0.0, "duration": 0.4},
            "spring_momentum": {"type": "spring", "bounce": max(0.15, p["bounce"]), "duration": 0.4},
            "press_scale": 0.97,
            "stagger_ms": 50,
            "rules": ["ease-out on enter and exit, never ease-in",
                      "UI transitions under 300 ms; no animation on keyboard-driven or 100+/day actions",
                      "animate transform and opacity only; never transition: all",
                      "enter from scale(0.95) and opacity 0, never scale(0); popovers scale from their trigger",
                      "reduced motion keeps opacity and colour changes, drops movement",
                      "hover motion only under @media (hover: hover) and (pointer: fine)"],
        },
    }


# ---------------------------------------------------------------- validation

TEXT_PAIRS = [  # (foreground role, background role, required ratio)
    ("text", "bg", 4.5), ("text", "bg-subtle", 4.5), ("text", "surface", 4.5), ("text", "card", 4.5),
    ("text-muted", "bg", 4.5), ("text-muted", "bg-subtle", 4.5), ("text-muted", "card", 4.5),
    ("accent-text", "bg", 4.5), ("accent-text", "card", 4.5),
    ("on-accent", "accent", 4.5),
    ("ring", "bg", 3.0), ("border-strong", "bg", 3.0),
]


def _px(v):
    s = str(v).strip()
    return int(s[:-2]) if s.endswith("px") and s[:-2].isdigit() else None


def validate(tokens):
    """Problems as sentences; empty when the direction holds."""
    problems = []
    for theme, roles in (tokens.get("colors") or {}).items():
        if not isinstance(roles, dict) or not all(isinstance(v, str) for v in roles.values()):
            continue  # an extracted palette (tokens_extract.py) has no roles to pair
        pairs = list(TEXT_PAIRS) + [(f"{s}-text", "bg", 4.5) for s in SEMANTIC_HUES if f"{s}-text" in roles]
        for fg, bg, need in pairs:
            if fg not in roles or bg not in roles:
                continue
            try:
                ratio = uc.contrast(uc.hex_rgb(roles[fg]), uc.hex_rgb(roles[bg]))
            except ValueError:
                problems.append(f"{theme}: {fg} or {bg} is not a colour")
                continue
            if ratio < need:
                problems.append(f"{theme}: {fg} {roles[fg]} on {bg} {roles[bg]} is {ratio:.2f}:1, needs {need}:1")
    sizes = list((tokens.get("type_scale") or {}).values())
    if sizes != sorted(sizes) or len(set(sizes)) != len(sizes):
        problems.append(f"type scale is not strictly increasing: {sizes}")
    if sizes and min(sizes) < 12:
        problems.append(f"type scale has text under 12px: {min(sizes)}")
    if len(sizes) > 8:
        problems.append(f"type scale has {len(sizes)} sizes; a scale has 5–8")
    space = list((tokens.get("spacing") or {}).values())
    if space != sorted(space) or len(set(space)) != len(space):
        problems.append(f"spacing scale is not strictly increasing: {space}")
    radii = [_px(v) for k, v in (tokens.get("radius") or {}).items() if k != "full"]
    radii = [r for r in radii if r is not None]
    if radii != sorted(radii):
        problems.append(f"radii are not ascending: {radii}")
    for k, ms in ((tokens.get("motion") or {}).get("duration_ms") or {}).items():
        if k != "slow" and ms > 300:
            problems.append(f"motion {k} is {ms} ms; UI motion stays under 300 ms")
    return problems


# ---------------------------------------------------------------- CSS

SHADCN = {  # shadcn/ui variable -> direction role
    "background": "bg", "foreground": "text", "card": "card", "card-foreground": "text",
    "popover": "card", "popover-foreground": "text", "primary": "accent", "primary-foreground": "on-accent",
    "secondary": "surface", "secondary-foreground": "text", "muted": "bg-subtle", "muted-foreground": "text-muted",
    "accent": "surface-hover", "accent-foreground": "text", "destructive": "danger", "border": "border-subtle",
    "input": "border-strong", "ring": "ring", "sidebar": "bg-subtle", "sidebar-foreground": "text",
    "sidebar-primary": "accent", "sidebar-primary-foreground": "on-accent", "sidebar-accent": "surface-hover",
    "sidebar-accent-foreground": "text", "sidebar-border": "border-subtle", "sidebar-ring": "ring",
}


def _rem(px):
    return f"{px / 16:g}rem"


# direction roles whose names shadcn uses for something else
RENAMED = {"accent": "brand", "border": "border-default"}


def _theme_block(roles, theme):
    # the direction's own names first, then shadcn's, so a component pulled
    # from a registry reads the same colours. Two names collide: shadcn's
    # --accent is a hover surface and its --border is the subtle one, so the
    # direction's accent is written as --brand (and --primary) and its
    # component border as --border-default.
    lines = [f"  --{RENAMED.get(k, k)}: {v};" for k, v in roles.items()]
    danger = roles.get("danger") or uc.to_hex(uc.accent_scale(uc.hex_rgb(SEMANTIC_HUES["danger"]), theme)[8])
    for var, role in SHADCN.items():
        value = danger if role == "danger" else roles.get(role)
        if value:
            lines.append(f"  --{var}: {value};")
    return lines


def css(tokens):
    """tokens -> CSS custom properties: light on :root, dark under
    prefers-color-scheme (unless data-theme=light) and under data-theme=dark."""
    out = ["/* Generated by ui_direction.py from design-tokens.json — edit the JSON, not this file. */", ":root {"]
    fonts = tokens.get("fonts") or {}
    out += [f"  --font-sans: {fonts.get('body', SYSTEM_SANS)};", f"  --font-heading: {fonts.get('heading', SYSTEM_SANS)};",
            f"  --font-mono: {fonts.get('mono', SYSTEM_MONO)};"]
    t = tokens.get("type") or {}
    for name, px in (tokens.get("type_scale") or {}).items():
        out.append(f"  --text-{name}: {_rem(px)};")
        lh = (t.get("line_height") or {}).get(name)
        if lh:
            out.append(f"  --leading-{name}: {lh};")
        tr = (t.get("tracking") or {}).get(name)
        if tr and tr != "0":
            out.append(f"  --tracking-{name}: {tr};")
    for name, w in (t.get("weights") or {}).items():
        out.append(f"  --weight-{name}: {w};")
    for key, px in (tokens.get("spacing") or {}).items():
        out.append(f"  --space-{str(key).replace('.', '_')}: {_rem(px)};")
    for name, v in (tokens.get("radius") or {}).items():
        out.append(f"  --radius-{name}: {v};")
    if (tokens.get("radius") or {}).get("md"):
        out.append(f"  --radius: {tokens['radius']['md']};")
    for name, v in (tokens.get("shadow") or {}).items():
        out.append(f"  --shadow-{name}: {v};")
    m = tokens.get("motion") or {}
    for name, ms in (m.get("duration_ms") or {}).items():
        out.append(f"  --duration-{name}: {ms}ms;")
    for name, pts in (m.get("ease") or {}).items():
        out.append(f"  --ease-{name.replace('_', '-')}: cubic-bezier({', '.join('%g' % x for x in pts)});")
    for i, c in enumerate(tokens.get("chart") or [], 1):
        out.append(f"  --chart-{i}: {c};")
    colors = tokens.get("colors") or {}
    light, dark = colors.get("light") or {}, colors.get("dark") or {}
    if light and all(isinstance(v, str) for v in light.values()):
        out += _theme_block(light, "light")
    out.append("}")
    if dark and all(isinstance(v, str) for v in dark.values()):
        block = _theme_block(dark, "dark")
        out += ["@media (prefers-color-scheme: dark) {", '  :root:not([data-theme="light"]) {']
        out += ["  " + line for line in block] + ["  }", "}", ':root[data-theme="dark"] {'] + block + ["}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- picker
#
# One direction at a time, full size, in a realistic screen; keys 1–9 and
# the arrows flip, D flips light/dark. The picker bar is chrome: it never
# takes a direction's colours, so it cannot flatter one of them. The pattern
# (divergent variants behind a switcher, the human picks) is Emil Kowalski's
# `prototype` skill; the screen is the kit's own.

PICKER_CSS = """
*{box-sizing:border-box}html,body{margin:0}
[data-variant]{font-family:var(--font-sans);background:var(--background);color:var(--foreground);font-size:var(--text-base);line-height:var(--leading-base);min-height:100dvh}
.page{max-width:1200px;margin:0 auto;padding:var(--space-6) var(--space-6) 120px}
.top{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4);padding-bottom:var(--space-4);border-bottom:1px solid var(--border)}
.brand{font-weight:var(--weight-semibold);font-size:var(--text-lg)}
nav{display:flex;gap:var(--space-4);color:var(--muted-foreground);font-size:var(--text-sm)}nav a{color:inherit;text-decoration:none}nav a[aria-current]{color:var(--foreground);font-weight:var(--weight-medium)}
h1{font-size:var(--text-2xl);line-height:var(--leading-2xl);letter-spacing:var(--tracking-2xl,0);margin:var(--space-6) 0 var(--space-1);font-weight:var(--weight-semibold)}
.sub{color:var(--muted-foreground);margin:0 0 var(--space-6);max-width:62ch}
.bar{display:flex;gap:var(--space-2);align-items:center;flex-wrap:wrap;margin-bottom:var(--space-4)}
.btn{font:inherit;font-size:var(--text-sm);font-weight:var(--weight-medium);border-radius:var(--radius-md);padding:var(--space-2) var(--space-4);border:1px solid var(--input);background:var(--card);color:var(--foreground);cursor:pointer;transition:transform var(--duration-fast) var(--ease-out),background-color var(--duration-fast) var(--ease-out);min-height:36px}
.btn:active{transform:scale(0.97)}.btn.primary{background:var(--primary);border-color:var(--primary);color:var(--primary-foreground)}
@media (hover:hover) and (pointer:fine){.btn:hover{background:var(--accent)}.btn.primary:hover{background:var(--accent-hover)}}
.btn:focus-visible,.field:focus-visible{outline:2px solid var(--ring);outline-offset:2px}
.field{font:inherit;font-size:16px;border:1px solid var(--input);border-radius:var(--radius-md);padding:var(--space-2) var(--space-3);background:var(--card);color:var(--foreground);min-width:220px;min-height:36px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:var(--space-4);margin-bottom:var(--space-6)}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:var(--space-4);box-shadow:var(--shadow-sm)}
.stat{font-size:var(--text-xl);font-weight:var(--weight-semibold);font-variant-numeric:tabular-nums}.label{color:var(--muted-foreground);font-size:var(--text-sm)}
table{width:100%;border-collapse:collapse;font-size:var(--text-sm)}th{text-align:left;color:var(--muted-foreground);font-weight:var(--weight-medium);padding:var(--space-2) var(--space-3);border-bottom:1px solid var(--border)}
td{padding:var(--space-3);border-bottom:1px solid var(--border)}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}td:first-child,td.num,.badge{white-space:nowrap}.table-wrap{overflow-x:auto}
.badge{display:inline-block;font-size:var(--text-sm);padding:0 var(--space-2);border-radius:var(--radius-full);background:var(--accent-subtle);color:var(--accent-text)}
.badge.muted{background:var(--secondary);color:var(--muted-foreground)}.badge.danger{background:var(--danger-subtle,var(--secondary));color:var(--danger-text,var(--foreground))}
.badge.success{background:var(--success-subtle,var(--secondary));color:var(--success-text,var(--foreground))}
.alert{border:1px solid var(--warning,var(--border));background:var(--warning-subtle,var(--bg-subtle));color:var(--foreground);border-radius:var(--radius-md);padding:var(--space-3) var(--space-4);margin:var(--space-6) 0}
.empty{text-align:center;padding:var(--space-8) var(--space-4);border:1px dashed var(--input);border-radius:var(--radius-lg);color:var(--muted-foreground)}
.empty strong{display:block;color:var(--foreground);font-size:var(--text-lg);margin-bottom:var(--space-1)}
@media (max-width:640px){.page{padding:var(--space-4) var(--space-4) 120px}nav{display:none}.hide-sm{display:none}.field{flex:1 1 100%;min-width:0}.card.flush{padding:0}td,th{padding:var(--space-3) var(--space-2)}}
@media (prefers-reduced-motion:reduce){.btn{transition:background-color var(--duration-fast) linear}.btn:active{transform:none}}
"""

PICKER_JS = Path(__file__).resolve().parent / "js" / "picker.js"  # the same bar /jg-ui fix drops into a project


def _scoped(css_text, n):
    """A direction's custom properties, scoped to its variant wrapper."""
    body = css_text.replace(":root {", f'[data-variant="{n}"] {{', 1)
    body = body.replace(':root:not([data-theme="light"]) {', f':root:not([data-theme="light"]) [data-variant="{n}"] {{')
    return body.replace(':root[data-theme="dark"] {', f':root[data-theme="dark"] [data-variant="{n}"] {{')


BADGE = {"Paid": " success", "Due in 5 days": " muted", "Overdue": " danger"}


def _screen(title, tokens):
    d = tokens.get("direction") or {}
    esc = html.escape
    rows = [("INV-2041", "Northwind Traders", "Paid", "4,120.00"), ("INV-2040", "Halden & Co.", "Due in 5 days", "860.00"),
            ("INV-2039", "Blue Harbor Co.", "Paid", "12,400.00"), ("INV-2038", "Lumen Labs", "Overdue", "1,975.50")]
    trs = "".join(f"<tr><td>{a}</td><td class=\"hide-sm\">{esc(b)}</td><td><span class=\"badge{BADGE[c]}\">{esc(c)}</span></td>"
                  f"<td class=\"num\">${amt}</td></tr>" for a, b, c, amt in rows)
    return f"""<div class="page">
<header class="top"><span class="brand">{esc(title)}</span>
<nav aria-label="Main"><a href="#" aria-current="page">Invoices</a><a href="#">Customers</a><a href="#">Reports</a><a href="#">Settings</a></nav>
<button class="btn primary" type="button">New invoice</button></header>
<main><h1>Invoices</h1><p class="sub">{esc(d.get('words', ''))}. Every size, colour, radius and duration on this screen comes from the direction's tokens.</p>
<div class="bar"><input class="field" type="search" name="q" placeholder="Search invoices" aria-label="Search invoices"><button class="btn" type="button">Filter</button><button class="btn" type="button">Export</button></div>
<div class="grid"><div class="card"><div class="label">Outstanding</div><div class="stat">$2,835.50</div></div>
<div class="card"><div class="label">Paid this month</div><div class="stat">$16,520.00</div></div>
<div class="card hide-sm"><div class="label">Average days to pay</div><div class="stat">11.4</div></div></div>
<div class="card flush"><div class="table-wrap"><table><thead><tr><th scope="col">Invoice</th><th scope="col" class="hide-sm">Customer</th><th scope="col">Status</th><th scope="col" class="num">Amount</th></tr></thead><tbody>{trs}</tbody></table></div></div>
<div class="alert" role="status">Lumen Labs is 3 days overdue. A reminder goes out tomorrow at 9:00.</div>
<div class="empty"><strong>No drafts</strong>Invoices you start and don't send land here.<br><button class="btn" type="button" style="margin-top:12px">Start a draft</button></div>
</main></div>"""


def picker(token_list, title="Your product"):
    styles, bodies = [], []
    for n, t in enumerate(token_list, 1):
        styles.append(_scoped(css(t), n))
        look = (t.get("direction") or {}).get("look", f"direction {n}")
        bodies.append(f'<div data-variant="{n}" data-name="{html.escape(look)}" hidden>{_screen(title, t)}</div>')
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)} — directions</title><style>{''.join(styles)}{PICKER_CSS}</style></head><body>"
            + "".join(bodies) + f"<script>{PICKER_JS.read_text()}</script></body></html>\n")


# ---------------------------------------------------------------- DESIGN.md
#
# The google-labs-code DESIGN.md format (github.com/google-labs-code/design.md):
# YAML front matter of colors / typography / rounded / spacing / components,
# then eight sections in a fixed order. It is where builders and other tools
# (Claude Design exports, impeccable, hallmark) increasingly look, so the
# direction is written there too. design-tokens.json stays the file the kit's
# scripts read; DESIGN.md is generated from it and never edited by hand.

DESIGN_SECTIONS = ["Overview", "Colors", "Typography", "Layout", "Elevation & Depth", "Shapes", "Components", "Do's and Don'ts"]
_TYPE_ROLES = [("display", "4xl", 600), ("h1", "3xl", 600), ("h2", "2xl", 600), ("h3", "xl", 600), ("title", "lg", 500),
               ("body-md", "base", 400), ("body-sm", "sm", 400), ("label", "sm", 500)]


def _first_family(stack):
    return str(stack or SYSTEM_SANS).split(",")[0].strip().strip("'\"")


def designmd(tokens):
    d = tokens.get("direction") or {}
    light = (tokens.get("colors") or {}).get("light") or {}
    dark = (tokens.get("colors") or {}).get("dark") or {}
    sizes = tokens.get("type_scale") or {}
    t = tokens.get("type") or {}
    fam = _first_family((tokens.get("fonts") or {}).get("body"))
    q = lambda s: json.dumps(str(s), ensure_ascii=False)  # noqa: E731 — YAML accepts JSON strings
    colors = {"primary": light.get("accent"), "on-primary": light.get("on-accent"), "primary-hover": light.get("accent-hover"),
              "background": light.get("bg"), "surface": light.get("card"), "surface-muted": light.get("bg-subtle"),
              "text": light.get("text"), "text-muted": light.get("text-muted"), "border": light.get("border-subtle"),
              "border-strong": light.get("border-strong"), "ring": light.get("ring")}
    for k in ("danger", "warning", "success"):
        if light.get(k):
            colors[k] = light[k]
    for k in ("accent", "bg", "card", "text", "text-muted", "border-subtle"):
        if dark.get(k):
            colors[{"accent": "primary", "bg": "background", "card": "surface", "border-subtle": "border"}.get(k, k) + "-dark"] = dark[k]
    fm = ["---", "version: alpha", f"name: {q(d.get('name') or d.get('words') or 'Design direction')}",
          f"description: {q(d.get('words', '') + ' — ' + d.get('fits', ''))}", "colors:"]
    fm += [f"  {k}: {q(v)}" for k, v in colors.items() if v]
    fm.append("typography:")
    for role, step, weight in _TYPE_ROLES:
        if step not in sizes:
            continue
        fm += [f"  {role}:", f"    fontFamily: {q(fam)}", f"    fontSize: {sizes[step]}px", f"    fontWeight: {weight}",
               f"    lineHeight: {(t.get('line_height') or {}).get(step, leading(sizes[step]))}"]
        tr = (t.get("tracking") or {}).get(step)
        if tr and tr != "0":
            fm.append(f"    letterSpacing: {tr}")
    fm.append("rounded:")
    fm += [f"  {k}: {v}" for k, v in (tokens.get("radius") or {}).items()]
    fm.append("spacing:")
    fm += [f"  {q(k)}: {v}px" for k, v in (tokens.get("spacing") or {}).items()]
    pad = f"{(tokens.get('spacing') or {}).get('2', 8)}px {(tokens.get('spacing') or {}).get('4', 16)}px"
    fm += ["components:",
           "  button-primary:", '    backgroundColor: "{colors.primary}"', '    textColor: "{colors.on-primary}"',
           '    rounded: "{rounded.md}"', f"    padding: {pad}", "    height: 36px",
           "  button-primary-hover:", '    backgroundColor: "{colors.primary-hover}"',
           "  button-secondary:", '    backgroundColor: "{colors.surface}"', '    textColor: "{colors.text}"', '    rounded: "{rounded.md}"',
           "  input:", '    backgroundColor: "{colors.surface}"', '    textColor: "{colors.text}"', '    rounded: "{rounded.md}"', "    height: 36px",
           "  card:", '    backgroundColor: "{colors.surface}"', '    rounded: "{rounded.lg}"', f"    padding: {(tokens.get('spacing') or {}).get('4', 16)}px",
           "---", ""]
    m = tokens.get("motion") or {}
    dur = m.get("duration_ms") or {}
    refs = ", ".join(r.get("url", "") for r in d.get("references") or []) or "none recorded"
    body = [f"# {d.get('name') or 'Design direction'}", "",
            "## Overview", "", f"{d.get('words', '')}. For {d.get('fits', 'this product')}. Theme: {d.get('theme', 'both')}; "
            f"density: {d.get('density', '')}. References (read for their system, never copied): {refs}.", "",
            "## Colors", "", "Colour is chosen by role, never by eye. The accent (primary) is the only saturated colour and appears rarely; "
            "text and muted text clear 4.5:1 on every background by construction; dark mode uses its own scale, not an inversion.", "",
            "## Typography", "", f"{_first_family((tokens.get('fonts') or {}).get('body'))}, one family plus mono. Body {sizes.get('base')}px at 400; "
            f"scale ratio {t.get('ratio')} ({', '.join(str(v) for v in sizes.values())}px). Tracking tightens as type grows; "
            "leading loosens as it shrinks. Hierarchy uses size, weight and colour together.", "",
            "## Layout", "", f"Spacing scale {', '.join(str(v) for v in (tokens.get('spacing') or {}).values())}px — a short scale; "
            "tight within groups, generous between them. Prose capped at 60–75 characters.", "",
            "## Elevation & Depth", "", "One elevation level per surface; no cards inside cards. Borders before shadows.", "",
            "## Shapes", "", f"Radii {', '.join(f'{k} {v}' for k, v in (tokens.get('radius') or {}).items())}.", "",
            "## Components", "", "Real components, themed by these tokens: " + "; ".join(f"{k}: {v}" for k, v in (d.get("components") or {}).items()) + ".", "",
            "## Do's and Don'ts", "",
            f"- Do: motion {dur.get('fast')}/{dur.get('base')}/{dur.get('slow')} ms, ease-out on enter and exit, springs without bounce unless a gesture carried momentum.",
            "- Do: a visible focus ring on every control; loading, empty and error states for every list.",
            "- Do: use the CSS variables in tokens.css; never a literal colour or pixel value in a component."]
    body += [f"- Don't: {r}." for r in m.get("rules", [])[2:]]
    body += [f"- Don't: {a}." for a in d.get("avoid") or []]
    return "\n".join(fm + body) + "\n"


def parse_designmd(text):
    """The front matter of a DESIGN.md as nested dicts: the YAML subset the
    format uses (maps of maps of scalars, two-space indents)."""
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
    if not m:
        return {}
    root, stack = {}, [(-1, None)]
    stack[0] = (-1, root)
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, val = raw.strip().partition(":")
        key = key.strip().strip("'\"")
        val = val.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            if val[:1] in "'\"":
                try:
                    val = json.loads(val) if val[0] == '"' else val.strip("'")
                except ValueError:
                    val = val.strip("'\"")
            parent[key] = val
    return root


DESIGN_ROLE_NAMES = {  # common DESIGN.md colour names -> direction roles; the first name present wins a role
    "primary": "accent", "brand": "accent", "accent": "accent", "on-primary": "on-accent",
    "background": "bg", "bg": "bg", "surface": "card", "card": "card", "surface-muted": "bg-subtle",
    "text": "text", "on-surface": "text", "on-background": "text", "foreground": "text",
    "text-muted": "text-muted", "muted-foreground": "text-muted", "muted": "text-muted", "secondary-text": "text-muted",
    "border": "border-subtle", "outline": "border-subtle", "danger": "danger", "error": "danger",
}


def _px_num(v):
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(px|rem)?", str(v))
    if not m:
        return None
    n = float(m.group(1))
    return round(n * 16) if m.group(2) == "rem" else round(n)


def closest_look(body=None, ratio=None, radius=None, dark=False):
    """The preset nearest to measured numbers."""
    def dist(p):
        d = abs((body or p["base"]) - p["base"]) / 2 + abs((ratio or p["ratio"]) - p["ratio"]) * 10
        d += abs((radius or p["radius"][1]) - p["radius"][1]) / 4
        return d + (0 if bool(dark) == (p.get("theme") == "dark") else 1.5)
    return min(LOOKS, key=lambda k: dist(LOOKS[k]))


def from_designmd(text, name=None):
    """A direction from someone else's DESIGN.md (a Claude Design export, a
    brand system): their accent, fonts, body size and radii; the kit fills
    the scales around them and keeps their literal colours where the role
    is clear. Validation still runs, so a brand pair under 4.5:1 is reported."""
    fm = parse_designmd(text)
    colors = {str(k).lower(): v for k, v in (fm.get("colors") or {}).items() if isinstance(v, str)}
    typ = fm.get("typography") or {}
    body = next((typ[k] for k in ("body-md", "body", "body-lg", "paragraph") if isinstance(typ.get(k), dict)), None)
    fam = (body or {}).get("fontFamily") or next((v.get("fontFamily") for v in typ.values() if isinstance(v, dict) and v.get("fontFamily")), None)
    base = _px_num((body or {}).get("fontSize")) if body else None
    heads = [_px_num(v.get("fontSize")) for k, v in typ.items() if isinstance(v, dict) and re.match(r"(h\d|display|title|headline)", k)]
    heads = sorted(h for h in heads if h and base and h > base)
    ratio = round((heads[-1] / base) ** (1 / 5), 3) if heads and base else None
    rounded = fm.get("rounded") or {}
    radius = [_px_num(rounded.get(k)) for k in ("sm", "md", "lg")]
    radius = radius if all(radius) else None
    accent = colors.get("primary") or colors.get("brand") or colors.get("accent")
    look = closest_look(base, ratio, radius[1] if radius else None)
    tokens = propose(look, accent=accent if accent and uc.parse(accent) else None, font=fam or "system", name=name or fm.get("name"),
                     overrides={"base": base if base and 11 <= base <= 20 else None, "ratio": ratio if ratio and 1.05 <= ratio <= 1.6 else None,
                                "radius": radius})
    kept, taken = [], set()
    for src, role in DESIGN_ROLE_NAMES.items():
        v = colors.get(src)
        if v and uc.parse(v) and role in tokens["colors"]["light"] and role not in taken:
            tokens["colors"]["light"][role] = uc.to_hex(uc.parse(v)[0])
            taken.add(role)
            kept.append(f"{src}→{role}")
    tokens["source"] = {"kind": "design.md", "by": "ui_direction.py import", "made_at": tokens["source"]["made_at"]}
    tokens["direction"]["notes"].append(f"imported from DESIGN.md (nearest look {look}); colours kept: {', '.join(kept) or 'none'}")
    return tokens


def from_capture(cap, name=None, reference=None):
    """A direction from one measured reference page (ui_measure.cjs output):
    its body size, scale ratio, radius and accent; the look nearest those
    numbers supplies everything a measurement cannot."""
    p = cap.get("profile") or {}
    body = p.get("bodyPx")
    base = int(round(body)) if body and 12 <= body <= 20 else None
    ratio = p.get("scaleRatio") if p.get("scaleRatio") and 1.05 <= p["scaleRatio"] <= 1.6 else None
    r = p.get("dominantRadiusPx")
    radius = [max(2, round(r * 0.5)), round(r), round(r * 1.5)] if r and 2 <= r <= 24 else None
    # the accent is the most-used saturated fill (a button, a badge), not the
    # tinted text a dark theme lightens; text accents are the fallback
    fills = [b.get("hex") for b in (cap.get("color") or {}).get("background") or [] if b.get("hex")]
    accents = [h for h in fills if uc.parse(h) and uc.srgb_to_oklch(uc.parse(h)[0])[1] >= 0.045] + \
              list((cap.get("color") or {}).get("accents") or [])
    look = closest_look(base, ratio, r, cap.get("rendersDark"))
    tokens = propose(look, accent=accents[0] if accents else None, theme="dark" if cap.get("rendersDark") else None,
                     references=[reference or cap.get("url")], name=name,
                     overrides={"base": base, "ratio": ratio, "radius": radius})
    tokens["source"] = {"kind": "reference", "by": "ui_direction.py adopt", "made_at": tokens["source"]["made_at"], "url": cap.get("url")}
    tokens["direction"]["notes"].append(f"numbers adopted from {cap.get('url')} (nearest look {look}): body {base}, ratio {ratio}, radius {r}")
    return tokens


# ---------------------------------------------------------------- CLI


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("looks")
    lk.add_argument("--json", action="store_true")
    pr = sub.add_parser("propose")
    pr.add_argument("--look", required=True, choices=sorted(LOOKS))
    pr.add_argument("--accent")
    pr.add_argument("--neutral", choices=["cool", "warm", "gray", "tinted"])
    pr.add_argument("--theme", choices=["light", "dark", "both"])
    pr.add_argument("--density", choices=["compact", "comfortable"])
    pr.add_argument("--font", default="system")
    pr.add_argument("--semantic", action="store_true", default=True, help="danger / warning / success roles (the default)")
    pr.add_argument("--no-semantic", dest="semantic", action="store_false", help="leave the semantic roles out")
    pr.add_argument("--avoid", action="append", default=[])
    pr.add_argument("--reference", action="append", default=[])
    pr.add_argument("--name")
    im = sub.add_parser("import")
    im.add_argument("designmd")
    im.add_argument("--name")
    ad = sub.add_parser("adopt")
    ad.add_argument("capture")
    ad.add_argument("--name")
    for p in (pr, im, ad):
        p.add_argument("--out", required=True)
        p.add_argument("--css")
        p.add_argument("--designmd", dest="designmd_out")
        p.add_argument("--force", action="store_true", help="overwrite --out (never the project's own design-tokens.json)")
    va = sub.add_parser("validate")
    va.add_argument("tokens")
    cs = sub.add_parser("css")
    cs.add_argument("tokens")
    cs.add_argument("--out", required=True)
    dm = sub.add_parser("designmd")
    dm.add_argument("tokens")
    dm.add_argument("--out", required=True)
    pk = sub.add_parser("picker")
    pk.add_argument("tokens", nargs="+")
    pk.add_argument("--out", required=True)
    pk.add_argument("--title", default="Your product")
    a = ap.parse_args(argv)

    if a.cmd == "looks":
        if a.json:
            print(json.dumps({k: {"words": v["words"], "fits": v["fits"], "reference": v["reference"]}
                              for k, v in LOOKS.items()}, indent=2))
        else:
            for k, v in LOOKS.items():
                print(f"{k:13} {v['words']} — {v['fits']}")
        return 0
    if a.cmd in ("propose", "import", "adopt"):
        out = Path(a.out)
        if out.exists() and not a.force:
            print(f"{out} exists; not overwriting (adopting a direction is the human's call)", file=sys.stderr)
            return 1
        try:
            if a.cmd == "propose":
                tokens = propose(a.look, a.accent, a.neutral, a.theme, a.density, a.font, a.semantic,
                                 a.avoid, a.reference, a.name)
            elif a.cmd == "import":
                tokens = from_designmd(Path(a.designmd).read_text(encoding="utf-8"), a.name)
            else:
                cap = _load(a.capture)
                if not cap or "profile" not in cap:
                    raise ValueError(f"{a.capture} is not a ui_measure.cjs capture")
                tokens = from_capture(cap, a.name)
        except (ValueError, OSError) as e:
            print(str(e), file=sys.stderr)
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(tokens, indent=2) + "\n")
        for target, render in ((a.css, css), (a.designmd_out, designmd)):
            if target:
                Path(target).parent.mkdir(parents=True, exist_ok=True)
                Path(target).write_text(render(tokens))
        problems = validate(tokens)
        d = tokens["direction"]
        print(f"{d['look']}: body {tokens['type_scale']['base']}px · sizes {list(tokens['type_scale'].values())} · "
              f"spacing {list(tokens['spacing'].values())} · accent {d['accent']} · "
              f"{'valid' if not problems else str(len(problems)) + ' problems'}")
        for n in d["notes"]:
            print(f"note: {n}")
        for p in problems:
            print(f"problem: {p}")
        return 2 if problems else 0
    if a.cmd == "validate":
        problems = validate(_load(a.tokens))
        for p in problems:
            print(p)
        print("valid" if not problems else f"{len(problems)} problems")
        return 2 if problems else 0
    if a.cmd in ("css", "designmd"):
        Path(a.out).write_text((css if a.cmd == "css" else designmd)(_load(a.tokens)))
        print(f"wrote {a.out}")
        return 0
    Path(a.out).write_text(picker([_load(p) for p in a.tokens], a.title))
    print(f"wrote {a.out} ({len(a.tokens)} directions; keys 1–{len(a.tokens)}, arrows, D for dark)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
