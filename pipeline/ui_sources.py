#!/usr/bin/env python3
"""ui_sources.py — where /jg-ui's real things come from, stdlib only.

    ui_sources.py probe [--out reachable.json]              which UI sources this machine can reach
    ui_sources.py components search <words...> [--registry shadcn|kokonut|bklit|all] [--limit 12] [--refresh]
    ui_sources.py components show <registry>/<name> [--save DIR]
    ui_sources.py components vendor <registry>/<name> --dest <project> [--dry-run] [--force] [--no-deps]
    ui_sources.py libraries [<task words>]                  the curated library for a job (toasts, charts, drag …)
    ui_sources.py galleries --for "<what>" [--limit 12]     candidate reference sites from design galleries

Components are real: fetched live from the registries' own JSON (shadcn/ui
style new-york-v4, Kokonut UI, Bklit), the same files `npx shadcn add` writes.
`vendor` writes them itself so a headless build needs no npx: it honours each
file's target, rewrites registry import paths to the project's aliases, never
overwrites a file (or a page like app/page.tsx) without --force, lists the npm
packages to add — declared and imported — without installing them, and records
where every file came from in .pipeline/ui/components.lock.json. A registry
that cannot be reached is retried once and then named; nothing is written
from memory in its place.

Exit codes: 0 ok · 2 not found / refused · 3 a source unreachable · 1 usage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

KIT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) jg-ui/1.0"
CACHE = Path(os.environ.get("JG_UI_CACHE", Path.home() / ".cache" / "jg-ui"))
CACHE_TTL_S = 7 * 86400

REGISTRIES = {
    "shadcn": {"index": "https://ui.shadcn.com/r/styles/new-york-v4/registry.json",
               "item": "https://ui.shadcn.com/r/styles/new-york-v4/{name}.json", "style": "new-york-v4",
               "for": "app primitives: dialogs, menus, forms, tables, sidebars"},
    "kokonut": {"index": "https://kokonutui.com/r/registry.json", "item": "https://kokonutui.com/r/{name}.json",
                "for": "decorative, marketing and AI-chat pieces (most animate with motion; many hardcode colours)"},
    "bklit": {"index": "https://ui.bklit.com/r/registry.json", "item": "https://ui.bklit.com/r/{name}.json",
              "for": "charts only (visx, d3, motion); some examples overwrite app/page.tsx"},
}

# the probe: every host a /jg-ui stage may need, in fetch.py probe's format
PROBE_HOSTS = [
    ("ui.shadcn.com", "https://ui.shadcn.com/r/index.json"), ("kokonutui.com", "https://kokonutui.com/r/registry.json"),
    ("ui.bklit.com", "https://ui.bklit.com/r/registry.json"), ("motion.dev", "https://motion.dev/llms.txt"),
    ("recent.design", "https://recent.design/websites"), ("minimal.gallery", "https://minimal.gallery/"),
    ("onepagelove.com", "https://onepagelove.com/"), ("httpster.net", "https://httpster.net/"),
    ("landing.love", "https://www.landing.love/"), ("www.awwwards.com", "https://www.awwwards.com/websites/"),
    ("linear.app", "https://linear.app/"), ("stripe.com", "https://stripe.com/"), ("vercel.com", "https://vercel.com/"),
]

# Emil Kowalski's curated picks (github.com/emilkowalski/skills pick-ui-library, MIT, 85e8e23) plus the kit's
# registries: one library per job, so a builder never hand-rolls a toast or installs an abandoned package
LIBRARIES = [
    ("primitives", "shadcn/ui (new-york-v4) on Radix, or base-ui for unstyled primitives", "dialog dropdown menu popover select tabs tooltip form sidebar table accordion"),
    ("toasts", "Sonner (sonner.emilkowal.ski)", "toast notification snackbar"),
    ("command menu", "cmdk (cmdk.paco.me)", "command palette cmd+k search launcher"),
    ("drawers", "Vaul (vaul.emilkowal.ski)", "drawer sheet bottom mobile modal"),
    ("otp", "input-otp", "otp verification code pin"),
    ("motion", "Motion (motion.dev, import from motion/react); CSS transitions for hover and fades", "animation animate spring layout exit gesture transition"),
    ("numbers", "NumberFlow", "animated number counter price stat ticker"),
    ("charts", "Bklit (ui.bklit.com) or recharts; Liveline for streaming data", "chart charts graph line bar area pie analytics dashboard realtime streaming"),
    ("drag and drop", "dnd kit", "drag drop sortable reorder kanban"),
    ("virtualization", "Virtuoso", "long list large table virtual scroll infinite"),
    ("state", "zustand", "state store global"),
    ("class names", "clsx for conditionals; cva for components with real variants", "classname variants conditional"),
    ("theming", "next-themes (no flash on load) with the direction's tokens.css", "dark mode theme switch"),
    ("icons", "lucide-react (ships with shadcn); one set only", "icon icons"),
    ("marketing pieces", "Kokonut UI (kokonutui.com); theme it, most items hardcode colours", "hero landing marketing card showcase ai chat"),
    ("syntax highlighting", "shiki", "code block syntax highlight"),
    ("og images", "Satori", "og image social card"),
]

# galleries that return outbound site links to a plain request, measured from this Mac
# 2026-09-21; land-book, lapa.ninja and saaspo sit behind Cloudflare, Mobbin behind a login
GALLERIES = {
    "recent.design": {"url": "https://recent.design/websites?category={cat}", "cats": {"saas", "finance", "technology", "ai", "startup", "crypto", "portfolio", "agency", "ecommerce"}},
    "minimal.gallery": {"url": "https://minimal.gallery/tag/{cat}/", "cats": {"saas", "portfolio", "agency", "ecommerce", "technology", "finance"}},
    "onepagelove.com": {"url": "https://onepagelove.com/genre/{cat}", "cats": {"saas", "portfolio", "app", "agency", "startup"}},
    "httpster.net": {"url": "https://httpster.net/type/{cat}/", "cats": {"application-or-software", "finance", "product", "portfolio", "agency"}},
    "landing.love": {"url": "https://www.landing.love/categories/{cat}/", "cats": {"saas", "finance", "development", "ai", "app", "technology", "portfolio"}},
}
WORD_TO_CAT = {  # a product's words -> gallery categories, most specific first
    "crypto": ["crypto", "finance"], "defi": ["crypto", "finance"], "trading": ["finance", "crypto"], "finance": ["finance"],
    "fintech": ["finance"], "bank": ["finance"], "invoice": ["finance", "saas"], "invoices": ["finance", "saas"], "budget": ["finance", "app"],
    "dashboard": ["saas", "technology", "application-or-software"], "analytics": ["saas", "technology"], "admin": ["saas", "application-or-software"],
    "developer": ["development", "technology", "saas"], "api": ["development", "technology"], "devtool": ["development", "technology"],
    "ai": ["ai", "technology"], "llm": ["ai"], "agent": ["ai"], "notes": ["app", "product", "saas"], "todo": ["app", "product"],
    "shop": ["ecommerce"], "store": ["ecommerce"], "ecommerce": ["ecommerce"], "portfolio": ["portfolio"], "agency": ["agency"],
    "startup": ["startup", "saas"], "landing": ["saas", "startup"], "saas": ["saas"], "app": ["app", "application-or-software"],
}
VETTED = [  # read for their system, never copied
    ("https://linear.app", "dense tools and dashboards: restraint, 13–14px body, 0.1–0.16s ease-out", {"dashboard", "tool", "tools", "saas", "developer", "admin", "tracker", "issues"}),
    ("https://stripe.com", "marketing that stays serious: 16px body, ~1.36 display ratio, 8/16/32 spacing", {"landing", "finance", "fintech", "payments", "docs", "saas", "invoice", "invoices"}),
    ("https://vercel.com", "near-monochrome and precise: 14px body, borders over shadows", {"developer", "devtool", "status", "deploy", "api", "docs"}),
    ("https://www.raycast.com", "dark-first and compact, elevation by surface", {"launcher", "desktop", "command", "productivity", "tool"}),
    ("https://posthog.com", "dense analytics that stay legible", {"analytics", "dashboard", "charts", "data", "crypto", "scanner"}),
    ("https://resend.com", "a small surface with no clutter", {"email", "api", "simple", "crud", "notes"}),
    ("https://www.apple.com", "bold product pages: big type, generous space, restrained colour", {"launch", "product", "hardware", "portfolio", "campaign"}),
]
NOT_SITES = re.compile(r"(twitter\.com|//x\.com|facebook\.com|instagram\.com|linkedin\.com|youtube\.com|youtu\.be|tiktok\.com|pinterest\.|"
                       r"github\.com|dribbble\.com|behance\.net|medium\.com|google|gstatic|cloudflare|cloudfront|amazonaws|jsdelivr|unpkg|"
                       r"vercel-insights|vercel\.live|gravatar|imgix|//cdn\.|//fonts\.|plausible|stripe\.network|webflow\.(com|io)|framerusercontent|"
                       r"apps\.apple|play\.google|schema\.org|w3\.org|wordpress\.org|typekit|sentry|gmpg\.org|api\.w\.org|"
                       r"bsky\.app|threads\.net|mastodon|discord\.(gg|com)|t\.me|reddit\.com|producthunt\.com|figma\.com|"
                       r"gumroad|patreon|buymeacoffee|ko-fi|substack\.com|carbonads|buysellads|ethicalads|mobbin\.com|"
                       r"land-book|lapa\.ninja|saaspo|awwwards|godly\.website|refero\.design)", re.I)


# ---------------------------------------------------------------- fetching

def fetch(url, timeout=20, retries=1, accept="application/json, text/html;q=0.9, */*;q=0.1"):
    """GET a URL; one retry, then the error names the URL. Returns (status, body)."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return e.code, ""
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(getattr(e, "reason", e))
        if attempt < retries:
            time.sleep(1.5)
    raise ConnectionError(f"{url}: {last}")


def fetch_json(url, **kw):
    status, body = fetch(url, **kw)
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        raise ConnectionError(f"{url}: not JSON")


def registry_index(name, refresh=False):
    reg = REGISTRIES[name]
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"{name}.json"
    if not refresh and cached.exists() and time.time() - cached.stat().st_mtime < CACHE_TTL_S:
        try:
            return json.loads(cached.read_text())
        except ValueError:
            pass
    data = fetch_json(reg["index"])
    items = data.get("items", data) if isinstance(data, dict) else data
    items = [{k: it.get(k) for k in ("name", "type", "title", "description", "categories", "dependencies", "registryDependencies")}
             for it in (items or []) if isinstance(it, dict) and it.get("name")]
    cached.write_text(json.dumps(items))
    return items


def parse_ref(ref):
    """'kokonut/card-01', '@kokonutui/card-01', 'button' (shadcn), or a URL -> (registry or None, name or URL)."""
    if re.match(r"https?://", ref):
        return None, ref
    m = re.match(r"@?(shadcn|kokonut(?:ui)?|bklit)[/:](.+)$", ref)
    if m:
        return ("kokonut" if m.group(1).startswith("kokonut") else m.group(1)), m.group(2)
    return "shadcn", ref


def item_url(registry, name):
    return REGISTRIES[registry]["item"].format(name=name)


# ---------------------------------------------------------------- search / show

def search(words, registry="all", limit=12, refresh=False):
    names = list(REGISTRIES) if registry == "all" else [registry]
    terms = [w.lower() for w in words if w.strip()]
    hits, unreachable = [], []
    for reg in names:
        try:
            items = registry_index(reg, refresh)
        except ConnectionError as e:
            unreachable.append(str(e))
            continue
        for it in items:
            if str(it.get("type", "")).endswith(("example", "style", "theme", "font")):
                continue
            hay = {"name": str(it.get("name", "")).lower(), "title": str(it.get("title") or "").lower(),
                   "desc": str(it.get("description") or "").lower(), "cats": " ".join(it.get("categories") or []).lower()}
            score = sum(3 * (t in hay["name"]) + 2 * (t in hay["title"]) + (t in hay["desc"]) + (t in hay["cats"]) for t in terms)
            if score:
                hits.append({"ref": f"{reg}/{it['name']}", "score": score, "type": it.get("type"),
                             "title": it.get("title") or it["name"], "description": (it.get("description") or "")[:140],
                             "deps": it.get("dependencies") or []})
    hits.sort(key=lambda h: (-h["score"], h["ref"]))
    return hits[:limit], unreachable


def get_item(ref):
    registry, name = parse_ref(ref)
    url = name if registry is None else item_url(registry, name)
    return fetch_json(url), url


# ---------------------------------------------------------------- vendor

PAGE_TARGETS = re.compile(r"(^|/)(app|src/app)/(page|layout)\.[jt]sx?$|(^|/)pages/(_app|_document|index)\.[jt]sx?$")
BUILTIN = {"react", "react-dom", "react/jsx-runtime"}
PAID = {"@central-icons-react": "a paid icon licence"}
IMPORT_RX = re.compile(r"""(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|require\(\s*['"]([^'"]+)['"]\s*\)""")


def pkg_name(spec):
    """'motion@^12' -> 'motion'; '@radix-ui/react-dialog@1' -> '@radix-ui/react-dialog'."""
    if spec.startswith("@"):
        scope, _, rest = spec[1:].partition("/")
        return "@" + scope + "/" + rest.split("@")[0]
    return spec.split("@")[0]


def project_layout(dest):
    """Where aliases point in this project: components.json aliases, the
    tsconfig '@/*' path, or src/ when there is one."""
    dest = Path(dest)
    comp = {}
    try:
        comp = json.loads((dest / "components.json").read_text())
    except (OSError, ValueError):
        pass
    aliases = {"components": "@/components", "ui": "@/components/ui", "lib": "@/lib", "hooks": "@/hooks", "utils": "@/lib/utils"}
    aliases.update({k: v for k, v in (comp.get("aliases") or {}).items() if isinstance(v, str)})
    prefix = aliases["components"].split("/")[0] + "/"
    base = None
    for cfg in ("tsconfig.json", "jsconfig.json"):
        try:
            raw = re.sub(r"(?<!:)//[^\n]*|/\*.*?\*/", "", (dest / cfg).read_text(), flags=re.S)
            paths = (json.loads(raw).get("compilerOptions") or {}).get("paths") or {}
            target = (paths.get(prefix + "*") or [None])[0]
            if target:
                base = dest / target.rstrip("*")  # not resolve(): report paths stay relative to dest through a symlink
                break
        except (OSError, ValueError):
            continue
    if base is None:
        base = dest / "src" if (dest / "src").is_dir() and not (dest / "app").is_dir() else dest
    return {"aliases": aliases, "prefix": prefix, "base": Path(base), "has_components_json": bool(comp)}


def _alias_dir(layout, alias):
    rel = alias[len(layout["prefix"]):] if alias.startswith(layout["prefix"]) else alias.lstrip("@~/")
    return layout["base"] / rel


def _target(file, layout, dest):
    if file.get("target"):
        t = str(file["target"])
        # '~/' is the project root; any other target sits under src/ when the project has one (the CLI's rule)
        return Path(dest) / t[2:] if t.startswith("~/") else layout["base"] / t
    kind, path = str(file.get("type", "")), str(file.get("path", ""))
    folder = {"registry:ui": "ui", "registry:hook": "hooks", "registry:lib": "lib"}.get(kind, "components")
    sub = ""
    m = re.search(r"/(?:ui|components|blocks)/(.+)$", path)
    if m and folder == "components" and "/" in m.group(1):
        sub = str(Path(m.group(1)).parent)
    return _alias_dir(layout, layout["aliases"][folder]) / sub / Path(path).name


def rewrite_imports(text, layout, style="new-york-v4"):
    a = layout["aliases"]
    for pat, to in ((rf"@/registry/{style}/ui/", a["ui"] + "/"), (rf"@/registry/{style}/lib/utils", a["utils"]),
                    (rf"@/registry/{style}/lib/", a["lib"] + "/"), (rf"@/registry/{style}/hooks/", a["hooks"] + "/"),
                    (rf"@/registry/{style}/(?:blocks|components|examples)/", a["components"] + "/"),
                    (r"@/registry/[\w-]+/", a["components"] + "/")):
        text = re.sub(pat, to, text)
    if layout["prefix"] != "@/":
        text = re.sub(r"(['\"])@/", r"\1" + layout["prefix"], text)
    return text


def imported_packages(text):
    pkgs = set()
    for m in IMPORT_RX.finditer(text):
        spec = next(g for g in m.groups() if g)
        if spec.startswith((".", "/", "@/", "~/")) or spec in BUILTIN:
            continue
        parts = spec.split("/")
        pkgs.add("/".join(parts[:2]) if spec.startswith("@") else parts[0])
    return pkgs


def _package_json(dest):
    try:
        pj = json.loads((Path(dest) / "package.json").read_text())
    except (OSError, ValueError):
        return None
    return {**(pj.get("dependencies") or {}), **(pj.get("devDependencies") or {})}


def vendor(ref, dest, dry_run=False, force=False, deps=True, _seen=None, _depth=0):
    """Write one registry item (and, two levels deep, its registryDependencies)
    into the project. Returns a report; a refusal is reported, never raised."""
    _seen = _seen if _seen is not None else set()
    registry, name = parse_ref(ref)
    key = f"{registry}/{name}" if registry else name
    report = {"ref": key, "written": [], "skipped": [], "refused": [], "npm": [], "warnings": [], "css_vars": [], "children": []}
    if key in _seen:
        return report
    _seen.add(key)
    try:
        item, url = get_item(ref)
    except ConnectionError as e:
        report["warnings"].append(f"unreachable after one retry: {e}")
        report["unreachable"] = True
        return report
    if not item:
        report["warnings"].append(f"not found: {url}")
        report["missing"] = True
        return report
    layout = project_layout(dest)
    style = REGISTRIES.get(registry or "", {}).get("style", "new-york-v4")
    have = _package_json(dest)
    react_files = any(str(f.get("path", "")).endswith((".tsx", ".jsx")) for f in item.get("files") or [])
    if react_files and (have is None or "react" not in have) and _depth == 0:
        # a React component in a project without React: take it as a reference, don't vendor it
        report["warnings"].append("this project doesn't use React; take the item as a reference implementation "
                                  "(structure, states, variants) against tokens.css instead of vendoring it")
        report["refused"].append(f"{key} (React item, non-React project)")
        return report
    needed = {pkg_name(d) for d in (item.get("dependencies") or []) + (item.get("devDependencies") or [])}
    for f in item.get("files") or []:
        content = f.get("content")
        if content is None:
            report["warnings"].append(f"{f.get('path')}: no inline content")
            continue
        target = _target(f, layout, dest)
        rel = os.path.relpath(target, dest)
        if PAGE_TARGETS.search(rel) and not force:
            report["refused"].append(f"{rel} (a page of the app; an example may not replace it without --force)")
            continue
        if target.exists() and not force:
            report["skipped"].append(f"{rel} (exists)")
            continue
        text = rewrite_imports(content, layout, style)
        needed |= imported_packages(text)
        if "next/" in text and have is not None and "next" not in have:
            report["warnings"].append(f"{rel} imports next/*; this project doesn't use Next.js")
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        report["written"].append(rel)
    for pkg, why in PAID.items():
        if any(n == pkg or n.startswith(pkg + "/") for n in needed):
            report["warnings"].append(f"{pkg} needs {why}")
    report["npm"] = sorted(n for n in needed if have is None or n not in have)
    for scope, vars_ in (item.get("cssVars") or {}).items():
        for k in (vars_ or {}):
            report["css_vars"].append(f"{scope}: --{str(k).lstrip('-')}")
    if item.get("css"):
        report["warnings"].append("the item carries CSS rules; merge them into the project's stylesheet by hand")
    if deps and _depth < 2:
        wanted = list(item.get("registryDependencies") or [])
        # declared lists are incomplete (shadcn's dialog imports button without naming it):
        # any ui-alias import whose file is neither here nor being written is fetched too
        ui_alias = re.escape(layout["aliases"]["ui"])
        for f in item.get("files") or []:
            for n in re.findall(ui_alias + r"/([\w-]+)", rewrite_imports(f.get("content") or "", layout, style)):
                exists = any((_alias_dir(layout, layout["aliases"]["ui"]) / f"{n}{ext}").exists() for ext in (".tsx", ".ts", ".jsx"))
                if not exists and n not in wanted and f"{n}.tsx" not in " ".join(report["written"]):
                    wanted.append(n)
        for dep in wanted:
            # a bare name is shadcn's (the CLI's own rule); URLs and @namespaces name their registry
            child = dep if (dep.startswith(("http", "@")) or "/" in dep) else f"shadcn/{dep}"
            report["children"].append(vendor(child, dest, dry_run, force, deps, _seen, _depth + 1))
    if not dry_run and report["written"]:
        lock = Path(dest) / ".pipeline" / "ui" / "components.lock.json"
        data = {"entries": []}
        try:
            data = json.loads(lock.read_text())
        except (OSError, ValueError):
            pass
        data["entries"] = [e for e in data.get("entries", []) if e.get("ref") != key] + [{
            "ref": key, "url": url, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha256": hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest(), "files": report["written"]}]
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps(data, indent=2) + "\n")
    return report


def flatten(rep):
    out = [rep]
    for c in rep.get("children") or []:
        out += flatten(c)
    return out


# ---------------------------------------------------------------- libraries, galleries

def libraries(words):
    terms = {w.lower() for w in words}
    if not terms:
        return LIBRARIES
    return [lib for lib in LIBRARIES if terms & set(lib[2].split()) or any(t in lib[0] for t in terms)]


def categories_for(text):
    cats = []
    for w in re.findall(r"[a-z]+", text.lower()):
        for c in WORD_TO_CAT.get(w, []):
            if c not in cats:
                cats.append(c)
    return cats or ["saas", "technology"]


def _allowed(url):
    parts = urllib.parse.urlsplit(url)
    rp = urllib.robotparser.RobotFileParser()
    try:
        status, body = fetch(f"{parts.scheme}://{parts.netloc}/robots.txt", timeout=10, retries=0, accept="text/plain")
    except ConnectionError:
        return True
    if status != 200:
        return True
    rp.parse(body.splitlines())
    return rp.can_fetch(UA, url)


def outbound_sites(html, gallery_host):
    seen, out = set(), []
    html = html.replace("\\/", "/").replace("\\u002F", "/")  # URLs inside SSR JSON payloads
    for u in re.findall(r"https?://[^\s\"'<>()\\]+", html):
        u = u.rstrip(".,;")
        p = urllib.parse.urlsplit(u)
        host = p.netloc.lower().removeprefix("www.")
        if not host or "." not in host or gallery_host.lower().removeprefix("www.") in host or NOT_SITES.search(u):
            continue
        if re.search(r"\.(png|jpe?g|gif|svg|webp|avif|css|js|mjs|json|xml|ico|woff2?|mp4|webm|pdf)$", p.path, re.I):
            continue
        if host in seen or p.path.count("/") > 2:
            continue
        seen.add(host)
        out.append(f"{p.scheme}://{p.netloc}")
    return out


def galleries(for_text, limit=12):
    cats = categories_for(for_text)
    words = set(re.findall(r"[a-z]+", for_text.lower()))
    picks = [{"url": u, "why": why, "source": "vetted"} for u, why, tags in VETTED if words & tags]
    notes = []
    for gname, g in GALLERIES.items():
        cat = next((c for c in cats if c in g["cats"]), None)
        if not cat:
            continue
        url = g["url"].format(cat=cat)
        if not _allowed(url):
            notes.append(f"{gname}: robots.txt disallows {url}")
            continue
        try:
            status, body = fetch(url, accept="text/html")
        except ConnectionError as e:
            notes.append(f"{gname}: unreachable ({e})")
            continue
        if status != 200:
            notes.append(f"{gname}: HTTP {status}")
            continue
        for site in outbound_sites(body, urllib.parse.urlsplit(url).netloc)[:6]:
            if all(p["url"].rstrip("/") != site.rstrip("/") for p in picks):
                picks.append({"url": site, "why": f"{gname} · {cat}", "source": gname})
    return picks[:limit], cats, notes


# ---------------------------------------------------------------- probe

def probe(out):
    out = Path(out)
    hosts = out.with_suffix(".hosts.txt")
    hosts.parent.mkdir(parents=True, exist_ok=True)
    hosts.write_text("\n".join(f"{h} {u}" for h, u in PROBE_HOSTS) + "\n")
    proc = subprocess.run([sys.executable, str(KIT / "fetch.py"), "probe", "--out", str(out), "--hosts", str(hosts)],
                          capture_output=True, text=True, timeout=300)
    try:
        return json.loads(out.read_text()), None
    except (OSError, ValueError):
        return None, proc.stderr.strip()[-300:]


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("probe")
    pb.add_argument("--out", default=".pipeline/ui/reachable.json")
    cp = sub.add_parser("components")
    csub = cp.add_subparsers(dest="action", required=True)
    se = csub.add_parser("search")
    se.add_argument("words", nargs="+")
    se.add_argument("--registry", default="all", choices=["all", *REGISTRIES])
    se.add_argument("--limit", type=int, default=12)
    se.add_argument("--refresh", action="store_true")
    se.add_argument("--json", action="store_true")
    sh = csub.add_parser("show")
    sh.add_argument("ref")
    sh.add_argument("--save")
    ve = csub.add_parser("vendor")
    ve.add_argument("ref")
    ve.add_argument("--dest", default=".")
    ve.add_argument("--dry-run", action="store_true")
    ve.add_argument("--force", action="store_true")
    ve.add_argument("--no-deps", action="store_true")
    lb = sub.add_parser("libraries")
    lb.add_argument("words", nargs="*")
    gl = sub.add_parser("galleries")
    gl.add_argument("--for", dest="for_text", required=True)
    gl.add_argument("--limit", type=int, default=12)
    gl.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "probe":
        data, err = probe(a.out)
        if not data:
            print(f"probe failed: {err}", file=sys.stderr)
            return 3
        for h, e in (data.get("hosts") or {}).items():
            print(f"{h:22} {'reachable' if e.get('reachable') else 'BLOCKED ' + str(e.get('reason'))}")
        return 0 if all(e.get("reachable") for e in data["hosts"].values()) else 3
    if a.cmd == "libraries":
        for job, lib, _ in libraries(a.words):
            print(f"{job:20} {lib}")
        return 0
    if a.cmd == "galleries":
        picks, cats, notes = galleries(a.for_text, a.limit)
        if a.json:
            print(json.dumps({"categories": cats, "sites": picks, "notes": notes}, indent=2))
        else:
            print(f"categories: {', '.join(cats)}")
            for p in picks:
                print(f"{p['url']:40} {p['why']}")
            for n in notes:
                print(f"note: {n}")
        return 0 if picks else 3
    if a.action == "search":
        hits, unreachable = search(a.words, a.registry, a.limit, a.refresh)
        if a.json:
            print(json.dumps({"hits": hits, "unreachable": unreachable}, indent=2))
        else:
            for h in hits:
                print(f"{h['ref']:36} {str(h['type'] or ''):20} {h['title']} — {h['description'][:80]}")
            for u in unreachable:
                print(f"unreachable: {u}")
        return 0 if hits else (3 if unreachable else 2)
    if a.action == "show":
        try:
            item, url = get_item(a.ref)
        except ConnectionError as e:
            print(f"unreachable: {e}", file=sys.stderr)
            return 3
        if not item:
            print(f"not found: {url}", file=sys.stderr)
            return 2
        files = [f"{f.get('path')} → {f.get('target') or f.get('type')}" for f in item.get("files") or []]
        print(json.dumps({"ref": a.ref, "url": url, "type": item.get("type"), "title": item.get("title"),
                          "description": item.get("description"), "dependencies": item.get("dependencies"),
                          "registryDependencies": item.get("registryDependencies"), "files": files,
                          "cssVars": {k: sorted(v or {}) for k, v in (item.get("cssVars") or {}).items()}}, indent=2))
        if a.save:
            Path(a.save).mkdir(parents=True, exist_ok=True)
            reg, name = parse_ref(a.ref)
            (Path(a.save) / f"{reg or 'url'}-{Path(name).stem}.json").write_text(json.dumps(item, indent=2))
        return 0
    rep = vendor(a.ref, a.dest, a.dry_run, a.force, not a.no_deps)
    flat = flatten(rep)
    for r in flat:
        for w in r["written"]:
            print(f"{'would write' if a.dry_run else 'wrote'} {w}  ({r['ref']})")
        for s in r["skipped"] + r["refused"]:
            print(f"kept {s}  ({r['ref']})")
        for w in r["warnings"]:
            print(f"warning: {r['ref']}: {w}")
    npm = sorted({p for r in flat for p in r["npm"]})
    if npm:
        print(f"packages to add (not installed): npm install {' '.join(npm)}")
    css_vars = sorted({v for r in flat for v in r["css_vars"]})
    if css_vars:
        print(f"cssVars the item expects (the direction's tokens.css wins; add only what it lacks): {', '.join(css_vars[:12])}")
    if any(r.get("unreachable") for r in flat):
        return 3
    if any(r.get("missing") for r in flat) or not any(r["written"] or r["skipped"] for r in flat):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
