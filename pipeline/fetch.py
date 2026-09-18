#!/usr/bin/env python3
"""fetch.py — the validation stage's only way to touch the network.

    fetch.py get <url> [--extract SPEC] [--run-dir DIR] [--ledger PATH
                 --claim ID --measure NAME [--unit U] [--origin O] [--note N]
                 [--source-kind KIND] [--required]]
    fetch.py probe [--out reachable.json] [--hosts FILE]

A script fetches, never a model: a ledger row must carry something the
referee can re-run, and a re-run is `fetch.py get` with the same url and
the same --extract. `get` writes the body (text, tags stripped for HTML,
capped) to <run-dir>/bodies/<sha256>.txt so the judge can read what the
fetcher saw, and prints one JSON line:

    {"url", "status", "ok", "reason", "body_hash", "body_path",
     "extracted_by", "value", "unit", "date", "bytes"}

`reason` is null on success, else one of `egress` (the proxy or network
refused the host), `404`, `paywall` (401/402/403), `timeout`, `http`
(other 4xx/5xx), `extract` (fetched but the extractor found nothing).
Exit 0 on success, 2 when the extractor found nothing, 4 when the fetch
failed, 1 usage.

--extract SPEC selects the value:
    json:<path>      dotted path with [n] indexes into a JSON body, e.g.
                     json:downloads.monthly, json:objects[0].package.version
    regex:<pattern>  first group (or whole match) of a Python regex on the text
    count:<pattern>  number of non-overlapping regex matches
    text             no value; the body is the evidence (a tier-1 row)
Numeric strings become numbers.

--ledger appends the row the validation contract describes (claim, measure,
source, value, unit, date, origin, note, reason). --origin must be a URL
host or a handle (letters, digits, . _ - @), never free text: the referee
counts tier-1 rows only when their origins are distinct.

`probe` fetches a fixed host list (registries, GitHub, forums, news, a
search endpoint, data APIs) with a short timeout and writes
{"probed_at", "hosts": {host: {"reachable", "status", "reason", "ms"}}}.
The referee reads it to tell "blocked here" (infra) from "nobody has it"
(unobtainable). Measured 2026-09-18 from a cloud session: registries, raw
GitHub files and crates.io reach; api.github.com, Reddit, Hacker News,
Product Hunt, Stack Overflow, search engines and chain explorers do not.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "Mozilla/5.0 (compatible; jg-validate/1.0; +https://github.com/jacksongeiger/Claude_Upgrade)"
BODY_CAP = 200_000
TIMEOUT_S = 20
PROBE_TIMEOUT_S = 8

PROBE_HOSTS = [
    ("registry.npmjs.org", "https://registry.npmjs.org/-/v1/search?text=react&size=1"),
    ("pypi.org", "https://pypi.org/pypi/requests/json"),
    ("crates.io", "https://crates.io/api/v1/crates?q=serde&per_page=1"),
    ("api.github.com", "https://api.github.com/repos/pallets/flask"),
    ("raw.githubusercontent.com", "https://raw.githubusercontent.com/pallets/flask/main/README.md"),
    ("www.reddit.com", "https://www.reddit.com/r/programming/top.json?limit=1"),
    ("hn.algolia.com", "https://hn.algolia.com/api/v1/search?query=python&hitsPerPage=1"),
    ("news.ycombinator.com", "https://news.ycombinator.com/"),
    ("www.producthunt.com", "https://www.producthunt.com/"),
    ("api.stackexchange.com", "https://api.stackexchange.com/2.3/info?site=stackoverflow"),
    ("dev.to", "https://dev.to/api/articles?per_page=1"),
    ("lobste.rs", "https://lobste.rs/hottest.json"),
    ("html.duckduckgo.com", "https://html.duckduckgo.com/html/?q=python"),
    ("en.wikipedia.org", "https://en.wikipedia.org/w/api.php?action=query&format=json&titles=Python"),
    ("export.arxiv.org", "https://export.arxiv.org/api/query?search_query=all:python&max_results=1"),
    ("api.coingecko.com", "https://api.coingecko.com/api/v3/ping"),
    ("api.etherscan.io", "https://api.etherscan.io/api?module=stats&action=ethprice"),
]


def _ctx():
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or None
    try:
        return ssl.create_default_context(cafile=cafile)
    except (OSError, ssl.SSLError):
        return ssl.create_default_context()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _classify(exc):
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "404", exc.code
        if exc.code in (401, 402, 403):
            return "paywall", exc.code
        return "http", exc.code
    if isinstance(exc, urllib.error.URLError):
        r = str(exc.reason)
        if "Tunnel connection failed" in r or "Forbidden" in r or "refused" in r.lower() \
                or "Name or service not known" in r or "nodename" in r or "getaddrinfo" in r:
            return "egress", None
        if isinstance(exc.reason, socket.timeout) or "timed out" in r.lower():
            return "timeout", None
        return "egress", None
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout", None
    return "egress", None


def http_get(url, timeout=TIMEOUT_S):
    """Returns (status, bytes, content_type) or raises."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
        return resp.status, resp.read(BODY_CAP * 4), resp.headers.get("Content-Type", "")


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


def to_text(raw, content_type):
    text = raw.decode("utf-8", "replace")
    if "html" in (content_type or "").lower() or text.lstrip()[:1] == "<":
        text = _TAG_RE.sub(" ", text)
        text = _TAGS.sub(" ", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
    return text[:BODY_CAP]


def _json_path(obj, path):
    cur = obj
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
    return cur


def _num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = v.strip().replace(",", "")
        if re.fullmatch(r"-?\d+", t):
            return int(t)
        if re.fullmatch(r"-?\d+\.\d+", t):
            return float(t)
    return v


def extract(spec, raw, text):
    """Returns (value, ok). ok False when the extractor found nothing."""
    if not spec or spec == "text":
        return None, True
    kind, _, arg = spec.partition(":")
    if kind == "json":
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return None, False
        v = _json_path(obj, arg)
        return (_num(v), True) if v is not None else (None, False)
    if kind == "regex":
        m = re.search(arg, text, re.S)
        if not m:
            return None, False
        return _num(m.group(1) if m.groups() else m.group(0)), True
    if kind == "count":
        return len(re.findall(arg, text)), True
    return None, False


ORIGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,127}$")


def cmd_get(a):
    run_dir = a.run_dir or "."
    bodies = os.path.join(run_dir, "bodies")
    result = {"url": a.url, "status": None, "ok": False, "reason": None, "body_hash": None,
              "body_path": None, "extracted_by": a.extract or "text", "value": None,
              "unit": a.unit, "date": _now(), "bytes": 0}
    if a.ledger and (not a.claim or not a.measure):
        print(json.dumps({"ok": False, "error": "--ledger needs --claim and --measure"}))
        return 1
    if a.origin and not ORIGIN_RE.match(a.origin):
        print(json.dumps({"ok": False, "error": "--origin must be a host or a handle, not free text"}))
        return 1
    try:
        status, raw, ctype = http_get(a.url)
        result["status"] = status
    except Exception as exc:  # noqa: BLE001 - every failure becomes a reason
        reason, code = _classify(exc)
        result["reason"], result["status"] = reason, code
        _emit(result, a)
        return 4
    text = to_text(raw, ctype)
    digest = hashlib.sha256(raw[:BODY_CAP]).hexdigest()
    os.makedirs(bodies, exist_ok=True)
    path = os.path.join(bodies, f"{digest}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    result.update({"body_hash": digest, "body_path": path, "bytes": len(raw)})
    value, ok = extract(a.extract, raw, text)
    result["value"] = value
    if not ok:
        result["reason"] = "extract"
        _emit(result, a)
        return 2
    result["ok"] = True
    _emit(result, a)
    return 0


def _emit(result, a):
    if a.ledger:
        row = {"claim": a.claim, "measure": a.measure,
               "source": {"kind": a.source_kind, "url": a.url, "extracted_by": result["extracted_by"],
                          "body_hash": result["body_hash"], "body_path": result["body_path"]},
               "value": result["value"], "unit": a.unit, "date": result["date"],
               "origin": a.origin or urllib.parse.urlsplit(a.url).hostname,
               "required": bool(a.required), "note": a.note or "", "reason": result["reason"]}
        os.makedirs(os.path.dirname(os.path.abspath(a.ledger)), exist_ok=True)
        with open(a.ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    print(json.dumps(result))


def cmd_probe(a):
    hosts = PROBE_HOSTS
    if a.hosts:
        with open(a.hosts) as f:
            hosts = [(h.strip().split()[0], h.strip().split()[1]) for h in f if h.strip() and not h.startswith("#")]
    out = {"probed_at": _now(), "where": socket.gethostname(), "hosts": {}}
    for host, url in hosts:
        t0 = time.time()
        entry = {"reachable": False, "status": None, "reason": None, "ms": None}
        try:
            status, _raw, _ct = http_get(url, timeout=PROBE_TIMEOUT_S)
            entry.update({"reachable": True, "status": status})
        except Exception as exc:  # noqa: BLE001
            reason, code = _classify(exc)
            # reachable means evidence can come from here: a 2xx. A 403 is
            # recorded with its reason but is not reachable, because in a
            # cloud session the proxy answers 403 for api.github.com itself.
            entry.update({"reachable": False, "status": code, "reason": reason})
        entry["ms"] = int((time.time() - t0) * 1000)
        out["hosts"][host] = entry
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(out, f, indent=2)
    reachable = sorted(h for h, e in out["hosts"].items() if e["reachable"])
    blocked = sorted(h for h, e in out["hosts"].items() if not e["reachable"])
    print(json.dumps({"ok": True, "reachable": reachable, "blocked": blocked, "out": a.out}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fetch.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("url")
    g.add_argument("--extract", default="text")
    g.add_argument("--run-dir", default=None)
    g.add_argument("--ledger", default=None)
    g.add_argument("--claim", default=None)
    g.add_argument("--measure", default=None)
    g.add_argument("--unit", default=None)
    g.add_argument("--origin", default=None)
    g.add_argument("--note", default=None)
    g.add_argument("--source-kind", default="url")
    g.add_argument("--required", action="store_true")
    g.set_defaults(func=cmd_get)
    p = sub.add_parser("probe")
    p.add_argument("--out", default=None)
    p.add_argument("--hosts", default=None)
    p.set_defaults(func=cmd_probe)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
