"""npm funnel — packages from the public npm registry.

Why a second OSS funnel when GitHub exists: the GitHub search API is
unreachable from repo-scoped cloud sessions (every request returns 403), so
the index those sessions build has no open-source rows at all. The npm search
endpoint answers from anywhere, and most Claude Code tools, MCP servers and
CLIs are published there. This funnel gives a cloud session real OSS coverage
and, on a machine where the GitHub funnel also runs, the two dedupe through
`canon_key` (a package's repository link resolves to the same
`github.com/owner/repo` key the GitHub row carries).

What the endpoint gives us, measured on a live response (2026-09-17):
`package.{name, description, keywords, version, license, date, links}`,
`downloads.{monthly, weekly}`, `flags.insecure`, up to 250 objects per query.
No token, no documented rate limit; the funnel still pauses between queries.

Trust: download counts on npm are gameable (a fresh package with 300k
monthly downloads and no users is not rare), so downloads alone never lift a
package. Yellow needs a permissive license, a repository link, a publish in
the last year and a download floor together; everything else is red, and
nothing is ever green. The recipe stays `manual`: the exact `npm install`
line is recorded for the human, never executed by the installer.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator

from .. import config
from ..models import FunnelPage, RawRecord, ResourceDraft
from .base import HttpClient, HttpError

SEARCH = "https://registry.npmjs.org/-/v1/search"
PER_PAGE = 250          # the endpoint's maximum
PAUSE_S = 1.0           # undocumented limits: be polite anyway

FIELD_WHITELIST = frozenset({
    "name", "description", "keywords", "version", "license", "date",
    "links", "downloads", "flags",
})

# Curated keyword queries, mirroring the GitHub funnel's domains plus the
# Claude ecosystem. `not:deprecated not:insecure` removes the two failure
# modes at the source; `popularity=1.0` asks the registry to rank by use.
QUERY_KEYWORDS = [
    "mcp-server", "mcp", "claude-code", "claude", "agent",
    "pdf", "ocr", "markdown",
    "scraper", "browser-automation", "playwright", "crawler",
    "llm", "rag", "embeddings", "vector",
    "cli", "tui", "developer-tools",
    "testing", "linter", "codegen", "openapi",
    "sdk", "automation", "etl",
]

MIN_MONTHLY_DOWNLOADS = 5_000       # ingest floor: below this there is no signal
YELLOW_MONTHLY_DOWNLOADS = 20_000   # with license + repo + freshness
FRESH_MONTHS = 12

_GITHUB_RE = re.compile(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", re.IGNORECASE)


def _queries() -> list[str]:
    return [f"keywords:{kw} not:deprecated not:insecure" for kw in QUERY_KEYWORDS]


class NpmFunnel:
    name = "npm"
    supports_delta = False
    default_trust_tier = "red"
    field_whitelist = FIELD_WHITELIST

    def fetch(self, state: dict[str, Any], http: HttpClient,
              limit: int | None = None) -> Iterator[FunnelPage]:
        seen = 0
        ok_queries = 0
        last_error: HttpError | None = None

        for i, query in enumerate(_queries()):
            if i:
                time.sleep(PAUSE_S)
            url = f"{SEARCH}?text={_q(query)}&size={PER_PAGE}&popularity=1.0"
            try:
                resp = http.get(url)
            except HttpError as exc:
                # One bad query must not abandon the crawl; total failure
                # must not be reported as success (re-raise below).
                last_error = exc
                continue

            ok_queries += 1
            objects = (resp.json() or {}).get("objects") or []
            records = []
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                pkg = obj.get("package")
                if not isinstance(pkg, dict) or not pkg.get("name"):
                    continue
                # Flatten: the whitelist works on top-level keys, and the
                # popularity signal lives beside the package, not inside it.
                fields = {**pkg, "downloads": obj.get("downloads") or {},
                          "flags": obj.get("flags") or {}}
                records.append(RawRecord(
                    natural_key=str(pkg["name"]),
                    type="library",
                    fields=fields,
                    source_ref=f"{SEARCH}?text={query}",
                ))
            seen += len(records)
            yield FunnelPage(records=records)

            if limit is not None and seen >= limit:
                return

        if ok_queries == 0 and last_error is not None:
            raise last_error

    def to_draft(self, rec: RawRecord) -> ResourceDraft | None:
        from ..sanitize import apply_whitelist

        f, _ = apply_whitelist(rec.fields, self.field_whitelist)

        name = str(f.get("name") or "").strip()
        if not name or not _valid_package_name(name):
            return None

        description = str(f.get("description") or "").strip()
        if len(description) < config.MIN_DESCRIPTION_LEN:
            return None

        downloads = f.get("downloads") if isinstance(f.get("downloads"), dict) else {}
        monthly = _int(downloads.get("monthly"))
        if monthly < MIN_MONTHLY_DOWNLOADS:
            return None  # no usage signal: not worth a row

        flags = f.get("flags") if isinstance(f.get("flags"), dict) else {}
        insecure = bool(flags.get("insecure"))

        links = f.get("links") if isinstance(f.get("links"), dict) else {}
        repo_url = _clean_repo_url(links.get("repository"))
        npm_url = str(links.get("npm") or f"https://www.npmjs.com/package/{name}")

        version = str(f.get("version") or "").strip() or None
        license_id = f.get("license") if isinstance(f.get("license"), str) else None
        published = _rfc3339(f.get("date"))

        keywords = [k for k in (f.get("keywords") or []) if isinstance(k, str)]
        tags = keywords[:19] + ["npm"]

        canon_key = _canon_key(name, repo_url)
        install = f"npm install {name}" + (f"@{version}" if version else "")

        return ResourceDraft(
            id=f"library:{self.name}:{name}",
            type="library",
            name=name,
            slug=_slug(name),
            funnel=self.name,
            source_ref=rec.source_ref,
            summary=description,
            url=repo_url or npm_url,
            tags=tags[:20],
            canon_key=canon_key,
            recipe_kind="manual",
            recipe_json=json.dumps({
                "kind": "manual",
                "url": npm_url,
                "command": install,
                "note": "Recorded, not run: an npm install executes third-party "
                        "code. Read the package page first.",
            }),
            pinned_version=version,
            trust_tier=_tier(monthly, published, license_id, repo_url, insecure),
            status="active",
            stars=None,
            pushed_at=published,
            archived=False,
            install_count=monthly,
        )


def _tier(monthly: int, published: str | None, license_id: str | None,
          repo_url: str | None, insecure: bool) -> str:
    """Yellow needs every signal at once; downloads alone are gameable."""
    if insecure:
        return "red"
    osi = license_id not in (None, "", "UNLICENSED", "NOASSERTION", "SEE LICENSE IN LICENSE")
    if license_id and license_id.lower() in ("proprietary", "unlicensed"):
        osi = False
    if (monthly >= YELLOW_MONTHLY_DOWNLOADS and osi and repo_url
            and _is_fresh(published)):
        return "yellow"
    return "red"


def _is_fresh(published: str | None, *, months: int = FRESH_MONTHS) -> bool:
    from ..ingest import _months_since
    return _months_since(published) <= months


def _canon_key(name: str, repo_url: str | None) -> str:
    if repo_url:
        m = _GITHUB_RE.search(repo_url)
        if m:
            owner, repo = m.group(1), m.group(2)
            if repo.endswith(".git"):
                repo = repo[:-4]
            return f"github.com/{owner}/{repo}".lower()
    return f"npm/{name}".lower()


def _clean_repo_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    for prefix in ("git+", "git://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    if url.startswith("ssh://git@"):
        url = "https://" + url[len("ssh://git@"):]
    if url.endswith(".git"):
        url = url[:-4]
    if not url.startswith(("http://", "https://")):
        return None
    return url


def _slug(name: str) -> str:
    # "@scope/name" -> "scope-name"; the id keeps the real package name.
    return re.sub(r"[^a-z0-9._-]+", "-", name.lower().lstrip("@").replace("/", "-")).strip("-")


def _valid_package_name(name: str) -> bool:
    return bool(re.fullmatch(r"(@[a-z0-9._-]+/)?[a-z0-9._-]+", name)) and len(name) <= 214


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _rfc3339(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 19:
        return None
    return value[:19] + "Z"


def _q(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe=":")
