#!/usr/bin/env python3
"""gates.py — screenshot and perf gates (pass/fail), stdlib only.

Usage:
    gates.py run --check '<json>' [--workdir .]
                 [--baseline-dir .pipeline/gates/baseline]
                 [--last-dir .pipeline/gates/last]
        Runs one gate check. `<json>` is either:
          screenshot: {"name","kind":"screenshot","url","viewport":[w,h],
                       "theme":"light|dark","threshold":0.01,"wait_ms":500}
          perf:       {"name","kind":"perf","cmd","metric","max":N|"min":N}
        For a screenshot check: renders the url with js/snap.cjs into
        <last-dir>/<name>.png, compares pixel-wise against
        <baseline-dir>/<name>.png with a pure-Python PNG decoder (zlib +
        PNG filter types 0-4, 8-bit gray/gray+alpha/RGB/RGBA), and passes
        when the fraction of differing pixels (any channel differing by
        more than 8) is <= threshold (default 0.01).
        For a perf check: runs `cmd` via `bash -c` in --workdir, parses the
        LAST JSON-object line of its stdout, and compares
        parsed[metric] against max (value <= max passes) or min
        (value >= min passes).
        Prints one JSON line: {"name","kind","ok","detail","diff_fraction"}
        (diff_fraction is null for perf checks).
        Exit codes: 0 pass · 2 fail · 3 needs-baseline (screenshot only,
        no baseline file yet) · 4 infra (couldn't run the check).

    gates.py accept <name> [--baseline-dir .pipeline/gates/baseline]
                    [--last-dir .pipeline/gates/last]
        Copies <last-dir>/<name>.png over <baseline-dir>/<name>.png — the
        human's approval of the current render as the new baseline.
        Exit 0 on success, 4 if <last-dir>/<name>.png does not exist.

    gates.py run-all --index <acceptance-index.json> --milestone <id>
                     [--workdir .] [--baseline-dir ...] [--last-dir ...]
        Reads the index (`{"<feature-id>": {"milestone": "<id>",
        "checks": [<gate check dict>, ...]}}`) and runs every check of every
        feature belonging to --milestone, printing one result line per
        check (as `run` does). Exit code is the worst of the individual
        checks' exit codes (4 > 3 > 2 > 0).

Exit codes overall: 0 ok · 2 fail · 3 needs-human (needs-baseline) ·
4 infra · 1 usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_JS = os.path.join(HERE, "js", "snap.cjs")

DEFAULT_BASELINE_DIR = os.path.join(".pipeline", "gates", "baseline")
DEFAULT_LAST_DIR = os.path.join(".pipeline", "gates", "last")
DEFAULT_THRESHOLD = 0.01
DIFF_CHANNEL_TOLERANCE = 8
PERF_TIMEOUT_S = 600
SNAP_TIMEOUT_S = 120

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_NEEDS_BASELINE = 3
EXIT_INFRA = 4


# ---------------------------------------------------------------------------
# Pure-Python PNG decoder (8-bit, non-interlaced, filter types 0-4).
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_CHANNELS_BY_COLOR_TYPE = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class PngError(Exception):
    pass


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw, width, height, bpp):
    stride = width * bpp
    out = bytearray(height * stride)
    prev_row = bytearray(stride)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            raise PngError("truncated PNG scanline data")
        filter_type = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        if len(row) != stride:
            raise PngError("truncated PNG scanline data")
        pos += stride
        for i in range(stride):
            a = row[i - bpp] if i >= bpp else 0
            b = prev_row[i]
            c = prev_row[i - bpp] if i >= bpp else 0
            x = row[i]
            if filter_type == 0:
                pass
            elif filter_type == 1:
                row[i] = (x + a) & 0xFF
            elif filter_type == 2:
                row[i] = (x + b) & 0xFF
            elif filter_type == 3:
                row[i] = (x + (a + b) // 2) & 0xFF
            elif filter_type == 4:
                row[i] = (x + _paeth(a, b, c)) & 0xFF
            else:
                raise PngError(f"unsupported PNG filter type {filter_type}")
        out[y * stride:(y + 1) * stride] = row
        prev_row = row
    return bytes(out)


def decode_png(path):
    """Decode an 8-bit, non-interlaced PNG. Returns (width, height, channels, pixels)."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != _PNG_SIG:
        raise PngError(f"not a PNG file: {path}")
    pos = 8
    width = height = bitdepth = colortype = None
    idat = bytearray()
    n = len(data)
    while pos < n:
        if pos + 8 > n:
            raise PngError("truncated PNG chunk header")
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        pos += 8
        chunk = data[pos:pos + length]
        pos += length + 4  # skip CRC
        if ctype == b"IHDR":
            width, height, bitdepth, colortype, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", chunk[:13]
            )
            if interlace != 0:
                raise PngError("interlaced PNG not supported")
            if bitdepth != 8:
                raise PngError(f"only 8-bit PNG supported, got bitdepth {bitdepth}")
            if colortype not in _CHANNELS_BY_COLOR_TYPE:
                raise PngError(f"unsupported PNG color type {colortype}")
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
    if width is None:
        raise PngError(f"no IHDR chunk in {path}")
    channels = _CHANNELS_BY_COLOR_TYPE[colortype]
    raw = zlib.decompress(bytes(idat))
    pixels = _unfilter(raw, width, height, channels)
    return width, height, channels, pixels


def _to_rgb(channels, pixel):
    if channels == 1:
        v = pixel[0]
        return (v, v, v)
    if channels == 2:
        v = pixel[0]
        return (v, v, v)
    if channels == 3:
        return (pixel[0], pixel[1], pixel[2])
    return (pixel[0], pixel[1], pixel[2])  # 4 channels, drop alpha


def diff_fraction(path_a, path_b):
    """Fraction of pixels differing by more than DIFF_CHANNEL_TOLERANCE in any channel."""
    w1, h1, c1, px1 = decode_png(path_a)
    w2, h2, c2, px2 = decode_png(path_b)
    if w1 != w2 or h1 != h2:
        raise PngError(f"size mismatch: {w1}x{h1} vs {w2}x{h2}")
    total = w1 * h1
    if total == 0:
        return 0.0
    differing = 0
    for y in range(h1):
        row1 = y * w1 * c1
        row2 = y * w2 * c2
        for x in range(w1):
            i1 = row1 + x * c1
            i2 = row2 + x * c2
            r1 = _to_rgb(c1, px1[i1:i1 + c1])
            r2 = _to_rgb(c2, px2[i2:i2 + c2])
            if (abs(r1[0] - r2[0]) > DIFF_CHANNEL_TOLERANCE
                    or abs(r1[1] - r2[1]) > DIFF_CHANNEL_TOLERANCE
                    or abs(r1[2] - r2[2]) > DIFF_CHANNEL_TOLERANCE):
                differing += 1
    return differing / total


# ---------------------------------------------------------------------------
# Check runners
# ---------------------------------------------------------------------------

def _resolve_dir(path, workdir):
    if os.path.isabs(path):
        return path
    return os.path.join(workdir, path)


def result(name, kind, ok, detail, diff_frac=None):
    return {"name": name, "kind": kind, "ok": ok, "detail": detail, "diff_fraction": diff_frac}


def run_screenshot_check(check, workdir, baseline_dir, last_dir):
    name = check.get("name", "screenshot")
    url = check.get("url")
    if not url:
        return result(name, "screenshot", False, "missing url", None), EXIT_INFRA
    viewport = check.get("viewport") or [1280, 800]
    theme = check.get("theme", "light")
    threshold = check.get("threshold", DEFAULT_THRESHOLD)
    wait_ms = check.get("wait_ms", 500)

    os.makedirs(last_dir, exist_ok=True)
    out_path = os.path.join(last_dir, f"{name}.png")

    cmd = [
        "node", SNAP_JS,
        "--url", url,
        "--out", out_path,
        "--width", str(viewport[0]),
        "--height", str(viewport[1]),
        "--theme", theme,
        "--wait-ms", str(wait_ms),
    ]
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=SNAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return result(name, "screenshot", False, f"snap.cjs timed out after {SNAP_TIMEOUT_S}s", None), EXIT_INFRA
    except OSError as e:
        return result(name, "screenshot", False, f"could not run snap.cjs: {e}", None), EXIT_INFRA
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"snap.cjs exited {proc.returncode}").strip()
        return result(name, "screenshot", False, detail, None), EXIT_INFRA

    baseline_path = os.path.join(baseline_dir, f"{name}.png")
    if not os.path.exists(baseline_path):
        return (
            result(name, "screenshot", False, f"no baseline at {baseline_path}", None),
            EXIT_NEEDS_BASELINE,
        )

    try:
        frac = diff_fraction(baseline_path, out_path)
    except (PngError, OSError, zlib.error) as e:
        return result(name, "screenshot", False, f"could not diff PNGs: {e}", None), EXIT_INFRA

    ok = frac <= threshold
    detail = f"diff {frac:.4f} {'<=' if ok else '>'} threshold {threshold}"
    return result(name, "screenshot", ok, detail, frac), (EXIT_OK if ok else EXIT_FAIL)


def _last_json_line(text):
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            return None
        return obj
    return None


def run_perf_check(check, workdir, timeout=PERF_TIMEOUT_S):
    name = check.get("name", "perf")
    cmd = check.get("cmd")
    metric = check.get("metric")
    has_max = "max" in check
    has_min = "min" in check
    if not cmd or not metric or not (has_max or has_min):
        return result(name, "perf", False, "missing cmd, metric, or max/min", None), EXIT_INFRA

    try:
        proc = subprocess.run(["bash", "-c", cmd], cwd=workdir, capture_output=True,
                               text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return result(name, "perf", False, f"cmd timed out after {timeout}s", None), EXIT_INFRA
    except OSError as e:
        return result(name, "perf", False, f"could not run cmd: {e}", None), EXIT_INFRA

    obj = _last_json_line(proc.stdout)
    if not isinstance(obj, dict):
        detail = f"last line of stdout is not a JSON object (exit {proc.returncode})"
        return result(name, "perf", False, detail, None), EXIT_INFRA
    if metric not in obj:
        return result(name, "perf", False, f"metric {metric!r} not in output {obj!r}", None), EXIT_INFRA
    try:
        value = float(obj[metric])
    except (TypeError, ValueError):
        return result(name, "perf", False, f"metric {metric!r} is not a number: {obj[metric]!r}", None), EXIT_INFRA

    if has_max:
        bound = float(check["max"])
        ok = value <= bound
        detail = f"{metric}={value} {'<=' if ok else '>'} max {bound}"
    else:
        bound = float(check["min"])
        ok = value >= bound
        detail = f"{metric}={value} {'>=' if ok else '<'} min {bound}"
    return result(name, "perf", ok, detail, None), (EXIT_OK if ok else EXIT_FAIL)


def run_check(check, workdir, baseline_dir, last_dir):
    kind = check.get("kind")
    if kind == "screenshot":
        return run_screenshot_check(check, workdir, baseline_dir, last_dir)
    if kind == "perf":
        return run_perf_check(check, workdir)
    name = check.get("name", "?")
    return result(name, kind, False, f"unknown gate kind {kind!r}", None), EXIT_INFRA


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_run(args):
    try:
        check = json.loads(args.check)
    except ValueError as e:
        print(f"invalid --check JSON: {e}", file=sys.stderr)
        return 1
    workdir = args.workdir
    baseline_dir = _resolve_dir(args.baseline_dir, workdir)
    last_dir = _resolve_dir(args.last_dir, workdir)
    res, code = run_check(check, workdir, baseline_dir, last_dir)
    print(json.dumps(res))
    return code


def cmd_accept(args):
    baseline_dir = args.baseline_dir
    last_dir = args.last_dir
    last_path = os.path.join(last_dir, f"{args.name}.png")
    if not os.path.exists(last_path):
        print(f"no last render at {last_path}", file=sys.stderr)
        return EXIT_INFRA
    os.makedirs(baseline_dir, exist_ok=True)
    baseline_path = os.path.join(baseline_dir, f"{args.name}.png")
    shutil.copyfile(last_path, baseline_path)
    print(json.dumps({"name": args.name, "accepted": baseline_path}))
    return EXIT_OK


def cmd_run_all(args):
    try:
        with open(args.index) as f:
            index = json.load(f)
    except (OSError, ValueError) as e:
        print(f"could not read index {args.index}: {e}", file=sys.stderr)
        return EXIT_INFRA

    workdir = args.workdir
    baseline_dir = _resolve_dir(args.baseline_dir, workdir)
    last_dir = _resolve_dir(args.last_dir, workdir)

    worst = EXIT_OK
    ran_any = False
    for feature_id, entry in index.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("milestone") != args.milestone:
            continue
        for check in entry.get("checks", []):
            ran_any = True
            res, code = run_check(check, workdir, baseline_dir, last_dir)
            print(json.dumps(res))
            worst = max(worst, code)
    if not ran_any:
        return EXIT_OK
    return worst


def build_argparser():
    ap = argparse.ArgumentParser(
        prog="gates.py",
        description="Screenshot and perf gates (pass/fail), stdlib only.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one gate check")
    p_run.add_argument("--check", required=True, help="JSON-encoded gate check")
    p_run.add_argument("--workdir", default=".")
    p_run.add_argument("--baseline-dir", default=DEFAULT_BASELINE_DIR)
    p_run.add_argument("--last-dir", default=DEFAULT_LAST_DIR)
    p_run.set_defaults(func=cmd_run)

    p_accept = sub.add_parser("accept", help="approve the last render as the new baseline")
    p_accept.add_argument("name")
    p_accept.add_argument("--baseline-dir", default=DEFAULT_BASELINE_DIR)
    p_accept.add_argument("--last-dir", default=DEFAULT_LAST_DIR)
    p_accept.set_defaults(func=cmd_accept)

    p_all = sub.add_parser("run-all", help="run every gate check of a milestone")
    p_all.add_argument("--index", required=True)
    p_all.add_argument("--milestone", required=True)
    p_all.add_argument("--workdir", default=".")
    p_all.add_argument("--baseline-dir", default=DEFAULT_BASELINE_DIR)
    p_all.add_argument("--last-dir", default=DEFAULT_LAST_DIR)
    p_all.set_defaults(func=cmd_run_all)

    return ap


def main(argv=None):
    ap = build_argparser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
