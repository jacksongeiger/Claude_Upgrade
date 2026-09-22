#!/usr/bin/env python3
"""ui_inspire.py — read well-designed sites as numbers, stdlib only (/jg-ui inspire).

    ui_inspire.py run --for "<what the product is>" [--url U ...] [--limit 4] [--out .pipeline/ui/inspire]
                      [--no-galleries] [--no-dark]
    ui_inspire.py cards --captures DIR [--for "<what>"] --out DIR     cards from existing measurements
    ui_inspire.py adopt <site slug | capture.json> [--inspire-dir DIR] --out <proposal.json> [--css F] [--designmd F]

`run` picks references (the URLs given, else the kit's vetted sites that fit
plus design galleries for the product's category), measures each one with
ui_measure.cjs — scrolled for lazy content, light and dark, consent and chat
overlays hidden — and writes one numbers card per site and their median:

    inspiration.json   {"made_at", "for", "sites": [{"url", "title", "screenshot", "numbers": {...}, "take": [...]}], "median": {...}}
    inspiration.md     the same as tables, for the human and for the build's planner

"1.25 scale on an 8px rhythm with two accent colours" is something a builder
can act on; "looks premium" is not, so a card holds numbers only. What to
take from a site (a density, a scale, a restraint — never an asset or a
look) is said in words by whoever reads the screenshots: the session, never
this script. `adopt` makes a direction proposal from one site's numbers.

Exit codes: 0 ok · 3 nothing could be measured · 1 usage.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
import ui_check  # noqa: E402  (measure(): the one Playwright entry point)
import ui_direction  # noqa: E402
import ui_sources  # noqa: E402


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def numbers(cap, dark=None):
    """The numbers a builder can act on, from one desktop capture."""
    p = cap.get("profile") or {}
    m = cap.get("motion") or {}
    fv = p.get("firstViewport") or {}
    return {
        "body_px": p.get("bodyPx"), "sizes_px": p.get("sizesUsed"), "scale_ratio": p.get("scaleRatio"),
        "nearest_scale": p.get("nearestScale"), "largest_step": p.get("largestStep"),
        "spacing_on_4px_pct": p.get("spacingOn4Pct"), "spacing_on_8px_pct": p.get("spacingOn8Pct"),
        "spacing_carrying_90pct": p.get("spacingCarrying90Pct"), "distinct_spacing": p.get("distinctSpacing"),
        "typefaces": p.get("typefaces"), "dominant_weight": p.get("dominantWeight"),
        "text_colours": p.get("distinctTextColors"), "accent_colours": p.get("accentColors"), "accent_hues": p.get("accentHues"),
        "accents": (cap.get("color") or {}).get("accents", [])[:6],
        "radius_px": round(p["dominantRadiusPx"], 1) if _num(p.get("dominantRadiusPx")) is not None else None,
        "shadows": p.get("distinctShadows"), "max_shadow_blur_px": p.get("maxShadowBlurPx"),
        "motion_ms": [d["value"] for d in m.get("durations") or []][:3], "easings": [e["value"] for e in m.get("easings") or []][:2],
        "measure_cpl": p.get("measureCpl"), "words_first_screen": fv.get("words"), "controls_first_screen": fv.get("interactive"),
        "text_coverage_pct": fv.get("textCoveragePct"), "hierarchy_levels": p.get("hierarchyLevels"),
        "renders_dark": cap.get("rendersDark"),
        "has_dark_theme": (dark.get("pageBg") != cap.get("pageBg")) if dark else None,
    }


def take_lines(n):
    """The numbers restated as sentences a planner can paste into a goal."""
    out = []
    if n.get("body_px"):
        s = f"type: {n['body_px']}px body"
        if n.get("scale_ratio"):
            s += f" on a {n['scale_ratio']} ({n.get('nearest_scale')}) scale"
        if n.get("dominant_weight"):
            s += f", weight {n['dominant_weight']} carries the page"
        out.append(s)
    if n.get("spacing_on_4px_pct") is not None:
        carry = ", ".join(str(v) for v in (n.get("spacing_carrying_90pct") or [])[:8])
        out.append(f"spacing: {n['spacing_on_4px_pct']}% on a 4px grid; {carry} carry 90%")
    if n.get("accent_colours") is not None:
        out.append(f"colour: {n.get('text_colours')} text colours, {n['accent_colours']} accents in {n.get('accent_hues')} hues"
                   + (f" ({', '.join(n['accents'][:3])})" if n.get("accents") else ""))
    if n.get("radius_px") is not None:
        out.append(f"surface: {n['radius_px']}px radius, {n.get('shadows')} shadow styles (max blur {n.get('max_shadow_blur_px')}px)")
    if n.get("motion_ms"):
        out.append(f"motion: {', '.join(n['motion_ms'])}; easing {', '.join(n.get('easings') or [])}")
    if n.get("words_first_screen") is not None:
        out.append(f"density: {n['words_first_screen']} words and {n.get('controls_first_screen')} controls on the first screen, "
                   f"{n.get('text_coverage_pct')}% of it text" + (f"; prose at {n['measure_cpl']} characters a line" if n.get("measure_cpl") else ""))
    if n.get("has_dark_theme") is not None:
        out.append("theme: " + ("follows the system dark mode" if n["has_dark_theme"] else ("dark only" if n.get("renders_dark") else "light only")))
    return out


def median(cards):
    def med(key):
        vals = [_num(c["numbers"].get(key)) for c in cards]
        vals = [v for v in vals if v is not None]
        return round(statistics.median(vals), 3) if vals else None
    keys = ["body_px", "scale_ratio", "spacing_on_4px_pct", "accent_colours", "accent_hues", "radius_px",
            "measure_cpl", "words_first_screen", "controls_first_screen", "text_colours"]
    out = {k: med(k) for k in keys}
    ms = [float(d[:-2]) for c in cards for d in (c["numbers"].get("motion_ms") or [])[:1] if re.match(r"[\d.]+ms$", d)]
    out["motion_ms"] = round(statistics.median(ms)) if ms else None
    out["sites"] = len(cards)
    return out


def build_cards(caps, for_text=""):
    by_url = {}
    for c in caps:
        by_url.setdefault(c.get("url"), []).append(c)
    cards = []
    for url, cs in by_url.items():
        light = next((c for c in cs if c.get("theme") == "light"), cs[0])
        dark = next((c for c in cs if c.get("theme") == "dark"), None)
        n = numbers(light, dark)
        cards.append({"url": url, "title": light.get("title"), "screenshot": light.get("screenshot"),
                      "dark_screenshot": (dark or {}).get("screenshot"), "overlays_hidden": light.get("overlaysHidden"),
                      "numbers": n, "take": take_lines(n)})
    return {"made_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "for": for_text,
            "sites": cards, "median": median(cards) if cards else {}}


def to_md(data):
    lines = [f"# Inspiration — {data.get('for') or 'references'}", "",
             f"Measured {data['made_at']}. Numbers only; what to take from each site is written below its card by whoever "
             "read the screenshots. Take a system (a scale, a density, a restraint), never an asset or a look.", "",
             "| site | body | scale | 4px grid | accents | radius | motion | words above fold |", "|---|---|---|---|---|---|---|---|"]
    for c in data["sites"]:
        n = c["numbers"]
        lines.append(f"| {c['url']} | {n.get('body_px')}px | {n.get('scale_ratio')} ({n.get('nearest_scale')}) | "
                     f"{n.get('spacing_on_4px_pct')}% | {n.get('accent_colours')} | {n.get('radius_px')}px | "
                     f"{', '.join(n.get('motion_ms') or []) or '—'} | {n.get('words_first_screen')} |")
    md = data.get("median") or {}
    lines += ["", f"**Median of {md.get('sites', 0)}:** {md.get('body_px')}px body · scale {md.get('scale_ratio')} · "
              f"{md.get('spacing_on_4px_pct')}% on 4px · {md.get('accent_colours')} accents · {md.get('radius_px')}px radius · "
              f"{md.get('motion_ms')}ms motion · {md.get('measure_cpl')} characters a line", ""]
    for c in data["sites"]:
        lines += [f"## {c.get('title') or c['url']}", "", f"{c['url']} · screenshot `{c.get('screenshot')}`", ""]
        lines += [f"- {t}" for t in c["take"]]
        lines += ["- take: _(written by the session that read the screenshot)_", ""]
    lines += ["How the build uses this: the planner reads the median next to the project's direction; where they differ, "
              "the direction wins, because the human chose it.", ""]
    return "\n".join(lines)


def run(a):
    out = Path(a.out)
    urls, notes = list(a.url or []), []
    if not urls:
        words = set(re.findall(r"[a-z]+", a.for_text.lower()))
        if a.no_galleries:
            picks = [{"url": u, "source": "vetted"} for u, _, tags in ui_sources.VETTED if words & tags]
        else:
            picks, _cats, gnotes = ui_sources.galleries(a.for_text, a.limit * 3)
            notes += gnotes
        vetted = [p["url"] for p in picks if p.get("source") == "vetted"]
        found = [p["url"] for p in picks if p.get("source") != "vetted"]
        urls = (vetted[:2] + found)[:a.limit] or [u for u, _, _ in ui_sources.VETTED[:a.limit]]
    caps, err = ui_check.measure(urls, out / "shots", ["1440x900"], ["light"] if a.no_dark else ["light", "dark"], stress=False)
    if err:
        notes.append(err)
    data = build_cards([c for c in caps if c], a.for_text)
    data["notes"] = notes
    out.mkdir(parents=True, exist_ok=True)
    (out / "inspiration.json").write_text(json.dumps(data, indent=2) + "\n")
    (out / "inspiration.md").write_text(to_md(data))
    md = data.get("median") or {}
    print(f"inspire: {len(data['sites'])} of {len(urls)} sites measured · median {md.get('body_px')}px body, "
          f"scale {md.get('scale_ratio')}, {md.get('spacing_on_4px_pct')}% on 4px, {md.get('accent_colours')} accents · "
          f"{out / 'inspiration.md'}")
    for n in notes:
        print(f"note: {n}")
    return 0 if data["sites"] else 3


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--for", dest="for_text", default="")
    r.add_argument("--url", action="append")
    r.add_argument("--limit", type=int, default=4)
    r.add_argument("--out", default=".pipeline/ui/inspire")
    r.add_argument("--no-galleries", action="store_true")
    r.add_argument("--no-dark", action="store_true")
    c = sub.add_parser("cards")
    c.add_argument("--captures", required=True)
    c.add_argument("--for", dest="for_text", default="")
    c.add_argument("--out", required=True)
    d = sub.add_parser("adopt")
    d.add_argument("site")
    d.add_argument("--inspire-dir", default=".pipeline/ui/inspire")
    d.add_argument("--out", required=True)
    d.add_argument("--css")
    d.add_argument("--designmd")
    d.add_argument("--name")
    d.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        if not a.for_text and not a.url:
            print("say what the product is (--for) or name sites (--url)", file=sys.stderr)
            return 1
        return run(a)
    if a.cmd == "cards":
        caps = [ui_check.load_json(p) for p in sorted(Path(a.captures).glob("*.json"))]
        data = build_cards([x for x in caps if x and "profile" in x], a.for_text)
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "inspiration.json").write_text(json.dumps(data, indent=2) + "\n")
        (Path(a.out) / "inspiration.md").write_text(to_md(data))
        print(f"cards: {len(data['sites'])} sites · {Path(a.out) / 'inspiration.md'}")
        return 0 if data["sites"] else 3
    cap_path = Path(a.site)
    if not cap_path.exists():
        slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"^https?://", "", a.site.lower())).strip("-")
        hits = sorted(p for p in (Path(a.inspire_dir) / "shots").glob("*-1440-light.json") if p.name.startswith(slug))
        if not hits:
            print(f"no measured capture for {a.site}; run `ui_inspire.py run --url {a.site}` first", file=sys.stderr)
            return 1
        cap_path = hits[0]
    argv2 = ["adopt", str(cap_path), "--out", a.out] + (["--css", a.css] if a.css else []) + \
            (["--designmd", a.designmd] if a.designmd else []) + (["--name", a.name] if a.name else []) + \
            (["--force"] if a.force else [])
    return ui_direction.main(argv2)


if __name__ == "__main__":
    sys.exit(main())
