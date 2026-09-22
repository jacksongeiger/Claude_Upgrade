"""Tests for ui_sources.py — registry refs, import rewriting, project layout,
vendoring real registry items (fetched through a fake registry: no network),
gallery link mining, categories and the curated library list.

    discovery/venv/bin/python -m pytest pipeline/tests/test_ui_sources.py -q -p no:cacheprovider
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ui_sources", KIT / "ui_sources.py")
us = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(us)

SH = "https://ui.shadcn.com/r/styles/new-york-v4/{}.json"
KK = "https://kokonutui.com/r/{}.json"
ITEMS = {
    SH.format("button"): {
        "name": "button", "type": "registry:ui", "dependencies": ["@radix-ui/react-slot@^1", "class-variance-authority"],
        "files": [{"path": "registry/new-york-v4/ui/button.tsx", "type": "registry:ui",
                   "content": "import { Slot } from '@radix-ui/react-slot'\n"
                              "import { cn } from '@/registry/new-york-v4/lib/utils'\nexport function Button() { return null }\n"}]},
    # shadcn's dialog imports button without declaring it in registryDependencies
    SH.format("dialog"): {
        "name": "dialog", "type": "registry:ui", "dependencies": ["@radix-ui/react-dialog"],
        "cssVars": {"light": {"overlay": "rgb(0 0 0 / 0.5)"}},
        "files": [{"path": "registry/new-york-v4/ui/dialog.tsx", "type": "registry:ui",
                   "content": "import * as D from '@radix-ui/react-dialog'\n"
                              "import { Button } from '@/registry/new-york-v4/ui/button'\n"
                              "import { cn } from '@/registry/new-york-v4/lib/utils'\nimport { X } from 'lucide-react'\n"}]},
    SH.format("use-mobile"): {
        "name": "use-mobile", "type": "registry:hook",
        "files": [{"path": "registry/new-york-v4/hooks/use-mobile.ts", "type": "registry:hook", "content": "export const useMobile = () => false\n"}]},
    KK.format("card-01"): {
        "name": "card-01", "type": "registry:component", "dependencies": ["motion@^12", "lucide-react"],
        "registryDependencies": ["https://kokonutui.com/r/badge.json"], "css": {"@keyframes x": {}},
        "files": [{"path": "components/kokonutui/card-01.tsx", "type": "registry:component",
                   "target": "components/kokonutui/card-01.tsx",
                   "content": "import { motion } from 'motion/react'\nimport { Sparkles } from 'lucide-react'\n"
                              "import clsx from 'clsx'\nimport { Icon } from '@central-icons-react/round'\n"},
                  {"path": "app/page.tsx", "type": "registry:page", "target": "app/page.tsx",
                   "content": "import Card from '@/components/kokonutui/card-01'\nexport default function Page() { return null }\n"}]},
    KK.format("badge"): {
        "name": "badge", "type": "registry:component",
        "files": [{"path": "components/kokonutui/badge.tsx", "type": "registry:component",
                   "target": "~/components/kokonutui/badge.tsx", "content": "export const Badge = () => null\n"}]},
    KK.format("glow-css"): {
        "name": "glow-css", "type": "registry:style",
        "files": [{"path": "styles/glow.css", "type": "registry:style", "target": "styles/glow.css", "content": ".glow {}\n"}]},
}


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """A fake registry behind fetch_json; records every URL asked for."""
    asked = []
    items = copy.deepcopy(ITEMS)

    def fake_fetch_json(url, **kw):
        asked.append(url)
        if "unreachable" in url:
            raise ConnectionError(f"{url}: HTTP 503")
        return copy.deepcopy(items.get(url))

    monkeypatch.setattr(us, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(us, "CACHE", tmp_path / "cache")
    return asked


def react_project(root, deps=None, tsconfig=True, src=True):
    root.mkdir(parents=True, exist_ok=True)
    if src:
        (root / "src").mkdir()
    pj = {"dependencies": {"react": "^19.0.0", "react-dom": "^19.0.0", **(deps or {})}}
    (root / "package.json").write_text(json.dumps(pj))
    if tsconfig:
        (root / "tsconfig.json").write_text('{\n  // the Vite template ships comments\n  "compilerOptions": {\n'
                                            '    "baseUrl": ".",\n    "paths": { "@/*": ["./src/*"] }\n  }\n}\n')
    return root


def files_under(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------- refs and names

@pytest.mark.parametrize("ref, want", [
    ("kokonut/x", ("kokonut", "x")),
    ("@kokonutui/x", ("kokonut", "x")),
    ("kokonutui/card-01", ("kokonut", "card-01")),
    ("bklit/area-chart", ("bklit", "area-chart")),
    ("@shadcn/dialog", ("shadcn", "dialog")),
    ("shadcn:button", ("shadcn", "button")),
    ("button", ("shadcn", "button")),
    ("https://example.com/r/thing.json", (None, "https://example.com/r/thing.json")),
])
def test_parse_ref(ref, want):
    assert us.parse_ref(ref) == want


def test_item_url():
    assert us.item_url("shadcn", "button") == SH.format("button")
    assert us.item_url("kokonut", "card-01") == KK.format("card-01")
    assert us.item_url("bklit", "area-chart") == "https://ui.bklit.com/r/area-chart.json"


@pytest.mark.parametrize("spec, want", [
    ("@radix-ui/react-dialog@1", "@radix-ui/react-dialog"),
    ("@radix-ui/react-dialog", "@radix-ui/react-dialog"),
    ("motion@^12", "motion"),
    ("lodash@4.17.21", "lodash"),
    ("clsx", "clsx"),
])
def test_pkg_name(spec, want):
    assert us.pkg_name(spec) == want


# ---------------------------------------------------------------- layout and imports

SOURCE = ("import { Button } from '@/registry/new-york-v4/ui/button'\n"
          'import { cn } from "@/registry/new-york-v4/lib/utils"\n'
          "import { useMobile } from '@/registry/new-york-v4/hooks/use-mobile'\n"
          "import { thing } from '@/registry/new-york-v4/lib/thing'\n"
          "import Hero from '@/registry/new-york-v4/blocks/hero'\n"
          "import Glow from '@/registry/kokonutui/glow'\n"
          "import Card from '@/components/card'\n"
          "import { motion } from 'motion/react'\n")


def test_rewrite_imports_to_the_default_aliases(tmp_path):
    layout = us.project_layout(tmp_path)
    out = us.rewrite_imports(SOURCE, layout)
    assert "from '@/components/ui/button'" in out
    assert 'from "@/lib/utils"' in out
    assert "from '@/hooks/use-mobile'" in out
    assert "from '@/lib/thing'" in out
    assert "from '@/components/hero'" in out and "from '@/components/glow'" in out
    assert "@/registry" not in out
    assert "from 'motion/react'" in out  # packages are left alone


def test_rewrite_imports_to_a_tilde_alias_from_components_json(tmp_path):
    (tmp_path / "components.json").write_text(json.dumps({"aliases": {
        "components": "~/components", "ui": "~/components/ui", "lib": "~/lib", "utils": "~/lib/utils", "hooks": "~/hooks"}}))
    layout = us.project_layout(tmp_path)
    assert layout["prefix"] == "~/" and layout["has_components_json"] is True
    out = us.rewrite_imports(SOURCE, layout)
    assert "from '~/components/ui/button'" in out and 'from "~/lib/utils"' in out
    assert "from '~/hooks/use-mobile'" in out
    assert "from '~/components/card'" in out  # the item's own '@/' paths move to the project's prefix
    assert "'@/" not in out and '"@/' not in out


def test_project_layout_reads_tsconfig_paths(tmp_path):
    react_project(tmp_path)
    layout = us.project_layout(tmp_path)
    assert os.path.samefile(layout["base"], tmp_path / "src")
    assert layout["aliases"]["ui"] == "@/components/ui" and layout["prefix"] == "@/"


def test_project_layout_without_tsconfig(tmp_path):
    assert us.project_layout(tmp_path)["base"] == tmp_path
    (tmp_path / "src").mkdir()
    assert us.project_layout(tmp_path)["base"] == tmp_path / "src"
    (tmp_path / "app").mkdir()  # a Next.js app/ at the root: aliases point at the root
    assert us.project_layout(tmp_path)["base"] == tmp_path


def test_imported_packages():
    text = us.rewrite_imports(SOURCE, us.project_layout(Path("/nonexistent"))) + (
        "import * as D from '@radix-ui/react-dialog'\nimport React from 'react'\nimport './local.css'\n"
        "export { z } from '../z'\nconst c = require('clsx')\nconst S = await import('sonner/dist')\n")
    assert us.imported_packages(text) == {"motion", "@radix-ui/react-dialog", "clsx", "sonner"}


# ---------------------------------------------------------------- vendor

def test_vendor_writes_a_ui_item_where_the_project_keeps_them(tmp_path, registry):
    proj = react_project(tmp_path / "proj", deps={"@radix-ui/react-slot": "^1"})
    rep = us.vendor("button", proj)
    assert rep["ref"] == "shadcn/button"
    assert rep["written"] == ["src/components/ui/button.tsx"]
    text = (proj / "src/components/ui/button.tsx").read_text()
    assert "from '@/lib/utils'" in text and "registry" not in text
    assert rep["npm"] == ["class-variance-authority"]  # react-slot is already in package.json
    assert registry == [SH.format("button")]


def test_vendor_follows_hidden_ui_imports_and_records_provenance(tmp_path, registry):
    proj = react_project(tmp_path / "proj", deps={"@radix-ui/react-dialog": "^1"})
    rep = us.vendor("shadcn/dialog", proj)
    assert rep["written"] == ["src/components/ui/dialog.tsx"]
    assert [c["ref"] for c in rep["children"]] == ["shadcn/button"]
    assert rep["children"][0]["written"] == ["src/components/ui/button.tsx"]
    assert rep["npm"] == ["lucide-react"]  # imported, not declared, not installed
    assert rep["css_vars"] == ["light: --overlay"]
    lock = json.loads((proj / ".pipeline/ui/components.lock.json").read_text())
    entries = {e["ref"]: e for e in lock["entries"]}
    assert set(entries) == {"shadcn/dialog", "shadcn/button"}
    dialog = entries["shadcn/dialog"]
    assert dialog["url"] == SH.format("dialog") and dialog["files"] == ["src/components/ui/dialog.tsx"]
    assert dialog["sha256"] == hashlib.sha256(json.dumps(ITEMS[SH.format("dialog")], sort_keys=True).encode()).hexdigest()
    assert dialog["fetched_at"].endswith("Z")
    flat = us.flatten(rep)
    assert [r["ref"] for r in flat] == ["shadcn/dialog", "shadcn/button"]


def test_vendor_does_not_fetch_a_dependency_the_project_already_has(tmp_path, registry):
    proj = react_project(tmp_path / "proj")
    (proj / "src/components/ui").mkdir(parents=True)
    (proj / "src/components/ui/button.tsx").write_text("// the project's own button\n")
    rep = us.vendor("dialog", proj)
    assert rep["children"] == [] and registry == [SH.format("dialog")]


def test_vendor_honours_targets_and_refuses_pages(tmp_path, registry):
    proj = react_project(tmp_path / "proj", deps={"motion": "^12"})
    rep = us.vendor("kokonut/card-01", proj)
    assert rep["written"] == ["src/components/kokonutui/card-01.tsx"]  # a plain target sits under src/
    assert rep["refused"] == ["src/app/page.tsx (a page of the app; an example may not replace it without --force)"]
    assert not (proj / "src/app/page.tsx").exists()
    badge = rep["children"][0]
    assert badge["ref"] == "https://kokonutui.com/r/badge.json"
    assert badge["written"] == ["components/kokonutui/badge.tsx"]  # '~/' is the project root
    assert rep["npm"] == ["@central-icons-react/round", "clsx", "lucide-react"]
    assert any("@central-icons-react needs a paid icon licence" in w for w in rep["warnings"])
    assert any("carries CSS rules" in w for w in rep["warnings"])
    forced = us.vendor("kokonut/card-01", proj, force=True)
    assert "src/app/page.tsx" in forced["written"]
    assert (proj / "src/app/page.tsx").read_text().startswith("import Card from '@/components/kokonutui/card-01'")


def test_vendor_skips_existing_files_and_dry_run_writes_nothing(tmp_path, registry):
    proj = react_project(tmp_path / "proj")
    dry = us.vendor("button", proj, dry_run=True)
    assert dry["written"] == ["src/components/ui/button.tsx"]
    assert files_under(proj) == ["package.json", "tsconfig.json"]  # nothing written, no lock
    us.vendor("button", proj)
    (proj / "src/components/ui/button.tsx").write_text("// edited by hand\n")
    again = us.vendor("button", proj)
    assert again["written"] == [] and again["skipped"] == ["src/components/ui/button.tsx (exists)"]
    assert (proj / "src/components/ui/button.tsx").read_text() == "// edited by hand\n"


def test_vendor_refuses_a_react_item_in_a_non_react_project(tmp_path, registry):
    for proj, pj in ((tmp_path / "bare", None), (tmp_path / "vue", {"dependencies": {"vue": "^3"}})):
        proj.mkdir()
        if pj:
            (proj / "package.json").write_text(json.dumps(pj))
        rep = us.vendor("dialog", proj)
        assert rep["written"] == [] and rep["children"] == []
        assert rep["refused"] == ["shadcn/dialog (React item, non-React project)"]
        assert any("reference implementation" in w for w in rep["warnings"])
        assert not (proj / "src").exists() and not (proj / "components").exists() and not (proj / ".pipeline").exists()
    # an item with no React files isn't a React item
    css_only = us.vendor("kokonut/glow-css", tmp_path / "bare")
    assert css_only["written"] == ["styles/glow.css"]


def test_vendor_reports_unreachable_and_missing(tmp_path, registry):
    proj = react_project(tmp_path / "proj")
    missing = us.vendor("no-such-item", proj)
    assert missing["missing"] is True and missing["written"] == []
    gone = us.vendor("https://unreachable.example/r/x.json", proj)
    assert gone["unreachable"] is True and "unreachable after one retry" in gone["warnings"][0]


def test_vendor_paths_are_project_relative_through_a_symlink(tmp_path, registry):
    real = react_project(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    rep = us.vendor("button", link)
    assert rep["written"] == ["src/components/ui/button.tsx"]
    lock = json.loads((real / ".pipeline/ui/components.lock.json").read_text())
    assert lock["entries"][0]["files"] == ["src/components/ui/button.tsx"]


def test_vendor_cli_exit_codes(tmp_path, registry, capsys):
    proj = react_project(tmp_path / "proj")
    assert us.main(["components", "vendor", "button", "--dest", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "wrote src/components/ui/button.tsx  (shadcn/button)" in out
    assert "npm install @radix-ui/react-slot class-variance-authority" in out
    assert us.main(["components", "vendor", "no-such-item", "--dest", str(proj)]) == 2
    assert us.main(["components", "vendor", "https://unreachable.example/r/x.json", "--dest", str(proj)]) == 3
    (tmp_path / "plain").mkdir()
    assert us.main(["components", "vendor", "button", "--dest", str(tmp_path / "plain")]) == 2


# ---------------------------------------------------------------- search

def test_search_ranks_and_names_unreachable_registries(tmp_path, monkeypatch):
    index = {"items": [
        {"name": "button", "type": "registry:ui", "title": "Button", "description": "A button", "dependencies": ["x"]},
        {"name": "button-demo", "type": "registry:example", "title": "Button demo"},
        {"name": "dialog", "type": "registry:ui", "title": "Dialog", "description": "A modal window with a button"},
        {"name": "toggle", "type": "registry:ui", "title": "Toggle"},
    ]}

    def fake_fetch_json(url, **kw):
        if "kokonutui" in url:
            raise ConnectionError(f"{url}: HTTP 403")
        return index if "shadcn" in url else {"items": []}

    monkeypatch.setattr(us, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(us, "CACHE", tmp_path / "cache")
    hits, unreachable = us.search(["button"])
    assert [h["ref"] for h in hits] == ["shadcn/button", "shadcn/dialog"]  # name beats description; examples skipped
    assert hits[0]["deps"] == ["x"]
    assert len(unreachable) == 1 and "kokonutui.com" in unreachable[0]
    assert json.loads((tmp_path / "cache" / "shadcn.json").read_text())[0]["name"] == "button"
    monkeypatch.setattr(us, "fetch_json", lambda url, **kw: pytest.fail("the cached index should be used"))
    assert [h["ref"] for h in us.search(["dialog"], registry="shadcn")[0]] == ["shadcn/dialog"]


# ---------------------------------------------------------------- galleries

def test_outbound_sites():
    html = ('<a href="https://www.coolsite.io/">Cool</a> <a href="https://recent.design/websites/foo">self</a> '
            '<a href="https://www.recent.design/about">self again</a> '
            '<script>{"url":"https:\\/\\/another.app\\/","img":"https:\\/\\/another.app\\/og.png"}</script> '
            "https://twitter.com/foo https://x.com/bar https://github.com/a/b https://cdn.jsdelivr.net/npm/x.js "
            "https://fonts.googleapis.com/css2 https://wordpress.org/ https://api.w.org/ https://gmpg.org/xfn/11 "
            "https://img.site.com/a.png https://deep.site.com/a/b/c/d https://coolsite.io/again "
            "https://www.awwwards.com/sites/x https://example.org/page.")
    assert us.outbound_sites(html, "recent.design") == ["https://www.coolsite.io", "https://another.app", "https://example.org"]


def test_categories_for():
    assert us.categories_for("crypto dashboard")[0] == "crypto"
    assert us.categories_for("crypto dashboard")[:3] == ["crypto", "finance", "saas"]
    assert us.categories_for("An Invoices app for freelancers")[:2] == ["finance", "saas"]
    assert us.categories_for("nothing we know") == ["saas", "technology"]


def test_libraries():
    assert [lib[1].split(" ")[0] for lib in us.libraries(["toast"])] == ["Sonner"]
    assert us.libraries([]) == us.LIBRARIES
    assert {lib[0] for lib in us.libraries(["drawer", "charts"])} == {"drawers", "charts"}
    assert us.libraries(["nothing-like-this"]) == []


def test_galleries_offline(monkeypatch):
    pages = {"https://recent.design/websites?category=crypto": '<a href="https://www.chainboard.xyz/">x</a> https://twitter.com/y',
             "https://minimal.gallery/tag/finance/": '<a href="https://ledgerly.com/">y</a>'}

    def fake_fetch(url, timeout=20, retries=1, accept=None):
        if url.endswith("/robots.txt"):
            return (200, "User-agent: *\nDisallow: /private\n") if "recent.design" in url else (404, "")
        if url in pages:
            return 200, pages[url]
        if "onepagelove" in url:
            raise ConnectionError(f"{url}: timed out")
        return 404, ""

    monkeypatch.setattr(us, "fetch", fake_fetch)
    picks, cats, notes = us.galleries("crypto dashboard", limit=12)
    urls = [p["url"] for p in picks]
    assert cats[0] == "crypto"
    assert urls[:2] == ["https://linear.app", "https://posthog.com"]  # vetted sites that fit come first
    assert "https://www.chainboard.xyz" in urls and "https://ledgerly.com" in urls
    assert not any("twitter" in u for u in urls)
    assert any(n.startswith("onepagelove.com: unreachable") for n in notes)
