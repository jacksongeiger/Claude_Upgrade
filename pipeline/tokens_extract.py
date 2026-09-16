#!/usr/bin/env python3
"""tokens_extract.py — design-token extractor (pipeline Stage 4 — /jg-ux, "Tokens").

Usage:
    tokens_extract.py --url <u> --out <path> [--name <site>] [--viewport 1280x800]
    tokens_extract.py --from-json <tokens.cjs output.json> --out <path> [--name <site>]

Runs `js/tokens.cjs` (Playwright) against --url, or, with --from-json, reads
a previously captured tokens.cjs JSON payload instead of touching a browser
(used by tests). Either way, converts the raw sample into a design-token
proposal and writes it to --out:

    {"source": {"url": ..., "sampled_at": ...},
     "fonts": {"body": "...", "heading": "...", "mono": "..."},
     "type_scale": {"xs": 12, "sm": 14, "base": 16, ...},
     "spacing": {"1": 4, "2": 8, "3": 12, "4": 16, ...},
     "colors": {"c1": {"hex": "#...", "seen_as": ["text", "background"], "count": n}, ...},
     "radius": {"sm": "...", "md": "...", "lg": "..."},
     "shadow": {"sm": "...", "md": "..."}}

Conversion rules:
    - rgb()/rgba() colors are converted to hex; alpha is kept as a separate
      field only when < 1; fully transparent colors are dropped.
    - type-scale sizes are named by nearest match against the conventional
      ladder (12 xs, 14 sm, 16 base, 18 lg, 20 xl, 24 2xl, 30 3xl, 36 4xl,
      48 5xl, 60 6xl); sizes seen fewer than 2 times are skipped.
    - spacing values are snapped to a 4px grid and keyed by px / 4.
    - colors are capped at the top 12 by count.
    - radii are assigned ascending to sm/md/lg; shadows to sm/md (by count).

Exit codes: 0 ok; 4 when the browser step (tokens.cjs) fails or its output
cannot be parsed.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOKENS_CJS = os.path.join(THIS_DIR, "js", "tokens.cjs")

TYPE_LADDER = [
    (12, "xs"),
    (14, "sm"),
    (16, "base"),
    (18, "lg"),
    (20, "xl"),
    (24, "2xl"),
    (30, "3xl"),
    (36, "4xl"),
    (48, "5xl"),
    (60, "6xl"),
]

RADIUS_NAMES = ["sm", "md", "lg"]
SHADOW_NAMES = ["sm", "md"]
MAX_COLORS = 12

_RGB_RE = None  # compiled lazily to keep imports at top minimal


def _rgb_to_hex(value):
    """Parse 'rgb(r,g,b)' / 'rgba(r,g,b,a)' -> (hex_str, alpha) or None."""
    import re

    global _RGB_RE
    if _RGB_RE is None:
        _RGB_RE = re.compile(
            r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)"
            r"(?:[,\s/]+([\d.]+))?\s*\)"
        )
    m = _RGB_RE.match(value.strip()) if value else None
    if not m:
        return None
    r, g, b = (int(round(float(m.group(i)))) for i in (1, 2, 3))
    alpha = float(m.group(4)) if m.group(4) is not None else 1.0
    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return "#{:02x}{:02x}{:02x}".format(r, g, b), alpha


def _num_or_int(px):
    return int(px) if float(px) == int(px) else px


def build_fonts(fonts):
    """Pick the highest-count family per role (body/heading/mono)."""
    best = {}
    for f in fonts or []:
        family = f.get("family")
        count = f.get("count", 0)
        if not family:
            continue
        for role in f.get("roles") or []:
            cur = best.get(role)
            if cur is None or count > cur[1]:
                best[role] = (family, count)
    return {role: best[role][0] for role in ("body", "heading", "mono") if role in best}


def build_type_scale(type_scale):
    """Name distinct sizes (count >= 2) by nearest ladder entry."""
    sizes = {}
    for e in type_scale or []:
        px = e.get("px")
        count = e.get("count", 0)
        if px is None:
            continue
        sizes[px] = sizes.get(px, 0) + count

    used = set()
    result = {}
    for px in sorted(sizes):
        if sizes[px] < 2:
            continue
        name = min(TYPE_LADDER, key=lambda t: abs(t[0] - px))[1]
        if name in used:
            continue
        used.add(name)
        result[name] = _num_or_int(px)
    return result


def build_spacing(spacing_scale):
    """Snap distinct spacing values to a 4px grid, key by px // 4."""
    buckets = {}
    for e in spacing_scale or []:
        px = e.get("px")
        count = e.get("count", 0)
        if px is None or px <= 0:
            continue
        snapped = int(round(px / 4.0)) * 4
        if snapped <= 0:
            continue
        buckets[snapped] = buckets.get(snapped, 0) + count

    return {str(snapped // 4): snapped for snapped in sorted(buckets)}


def build_colors(colors):
    """Convert to hex, drop transparent, cap at MAX_COLORS by count."""
    ordered = sorted(colors or [], key=lambda c: -c.get("count", 0))
    result = {}
    idx = 0
    for c in ordered:
        if idx >= MAX_COLORS:
            break
        parsed = _rgb_to_hex(c.get("value", ""))
        if parsed is None:
            continue
        hex_str, alpha = parsed
        if alpha <= 0:
            continue
        idx += 1
        entry = {
            "hex": hex_str,
            "seen_as": c.get("roles") or [],
            "count": c.get("count", 0),
        }
        if alpha < 1:
            entry["alpha"] = alpha
        result["c{}".format(idx)] = entry
    return result


def build_radius(radii):
    """Assign ascending simple 'Npx' radii to sm/md/lg."""
    import re

    agg = {}
    for r in radii or []:
        m = re.match(r"^(\d+(?:\.\d+)?)px$", (r.get("value") or "").strip())
        if not m:
            continue
        px = float(m.group(1))
        agg[px] = agg.get(px, 0) + r.get("count", 0)

    result = {}
    for name, px in zip(RADIUS_NAMES, sorted(agg)):
        result[name] = "{}px".format(_num_or_int(px))
    return result


def build_shadow(shadows):
    """Assign the top shadows (by count) to sm/md."""
    ordered = sorted(shadows or [], key=lambda s: -s.get("count", 0))
    result = {}
    for name, s in zip(SHADOW_NAMES, ordered):
        result[name] = s.get("value")
    return result


def build_proposal(data, url=None, name=None):
    proposal = {
        "source": {
            "url": url,
            "sampled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "fonts": build_fonts(data.get("fonts")),
        "type_scale": build_type_scale(data.get("type_scale")),
        "spacing": build_spacing(data.get("spacing_scale")),
        "colors": build_colors(data.get("colors")),
        "radius": build_radius(data.get("radii")),
        "shadow": build_shadow(data.get("shadows")),
    }
    if name:
        proposal["source"]["name"] = name
    return proposal


def run_tokens_cjs(url, viewport):
    cmd = ["node", TOKENS_CJS, "--url", url, "--viewport", viewport]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "tokens.cjs failed (exit {}): {}".format(
                proc.returncode, proc.stderr.strip()
            )
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("tokens.cjs produced invalid JSON: {}".format(exc))


class _ArgParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("{}: error: {}\n".format(self.prog, message))
        sys.exit(1)


def parse_args(argv):
    parser = _ArgParser(description="Extract design tokens from a live page.")
    parser.add_argument("--url", help="page URL to sample")
    parser.add_argument("--out", required=True, help="path to write the proposal JSON")
    parser.add_argument("--name", help="site name, recorded in source")
    parser.add_argument("--viewport", default="1280x800", help="WIDTHxHEIGHT, default 1280x800")
    parser.add_argument(
        "--from-json",
        dest="from_json",
        help="convert a previously captured tokens.cjs JSON payload instead of running the browser",
    )
    args = parser.parse_args(argv)
    if not args.from_json and not args.url:
        parser.error("--url is required unless --from-json is given")
    return args


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if args.from_json:
            with open(args.from_json) as f:
                data = json.load(f)
        else:
            data = run_tokens_cjs(args.url, args.viewport)
    except Exception as exc:  # noqa: BLE001 - reported to stderr, exit 4
        print(str(exc), file=sys.stderr)
        return 4

    proposal = build_proposal(data, url=args.url, name=args.name)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(proposal, f, indent=2)
        f.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
