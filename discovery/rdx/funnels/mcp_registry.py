"""Official MCP Registry funnel.

https://registry.modelcontextprotocol.io/v0/servers - verified live: 32,159
servers, no auth, no rate limit, cursor pagination.

Two parameters matter and both are easy to get wrong:

  version=latest    Without it you get every historical version of every
                    server, which multiplies the row count for no benefit.

  updated_since     The delta key. Passing it forces include_deleted=true, so
                    responses carry tombstones; those flip status to
                    'deprecated'. The new watermark is the run's START time,
                    not max(updated_at) - using the max would silently drop
                    anything modified while the crawl was in flight.

Ingest-time filtering is aggressive on purpose. Storing 32k rows to surface a
few dozen is waste, and every stored row is a row the sanitizer must have
gotten right.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from .. import config
from ..models import FunnelPage, RawRecord, ResourceDraft
from .base import HttpClient

BASE = "https://registry.modelcontextprotocol.io/v0/servers"
PAGE_SIZE = 100

FIELD_WHITELIST = frozenset({
    "name", "description", "title", "version", "repository", "websiteUrl",
    "remotes", "packages",
})


class McpRegistryFunnel:
    name = "mcp_registry"
    supports_delta = True
    default_trust_tier = "red"  # long tail: explicit review before install
    field_whitelist = FIELD_WHITELIST

    def fetch(self, state: dict[str, Any], http: HttpClient,
              limit: int | None = None) -> Iterator[FunnelPage]:
        cursor: str | None = None
        watermark = state.get("last_cursor")
        seen = 0

        while True:
            params = [f"limit={PAGE_SIZE}", "version=latest"]
            if watermark:
                params.append(f"updated_since={watermark}")
            if cursor:
                params.append(f"cursor={_q(cursor)}")
            url = f"{BASE}?{'&'.join(params)}"

            resp = http.get(url)
            data = resp.json()
            servers = data.get("servers", []) or []
            meta = data.get("metadata", {}) or {}

            records = []
            for entry in servers:
                srv = entry.get("server", entry) if isinstance(entry, dict) else None
                if not isinstance(srv, dict):
                    continue
                name = srv.get("name")
                if not name:
                    continue
                records.append(RawRecord(
                    natural_key=str(name),
                    type="mcp",
                    fields={**srv, "_meta": entry.get("_meta", {})},
                    source_ref=f"{BASE}#{name}",
                ))

            seen += len(records)
            cursor = meta.get("nextCursor")
            yield FunnelPage(records=records, cursor=cursor)

            if not cursor or not servers:
                break
            if limit is not None and seen >= limit:
                break

    def to_draft(self, rec: RawRecord) -> ResourceDraft | None:
        from ..sanitize import apply_whitelist

        f, _ = apply_whitelist(rec.fields, self.field_whitelist)
        name = str(f.get("name") or "").strip()
        if not name:
            return None

        description = str(f.get("description") or "").strip()
        # Ingest-time filter: no description, or one too short to carry signal.
        if len(description) < config.MIN_DESCRIPTION_LEN:
            return None

        repo = f.get("repository")
        repo_url = None
        if isinstance(repo, dict):
            repo_url = repo.get("url")
        elif isinstance(repo, str):
            repo_url = repo
        website = f.get("websiteUrl")
        url = repo_url or (website if isinstance(website, str) else None)
        if not url:
            return None  # nothing installable and nothing to audit against

        meta = rec.fields.get("_meta") or {}
        official = {}
        if isinstance(meta, dict):
            official = meta.get("io.modelcontextprotocol.registry/official", {}) or {}
        status_raw = str(official.get("status") or "active").lower()
        status = "deprecated" if status_raw in ("deleted", "deprecated") else "active"

        recipe_kind, recipe = _recipe(name, f)

        return ResourceDraft(
            id=f"mcp:{self.name}:{name}",
            type="mcp",
            name=str(f.get("title") or name),
            slug=_slug_from_name(name),
            funnel=self.name,
            source_ref=rec.source_ref,
            summary=description,
            url=url,
            tags=[],
            canon_key=_canon(url),
            recipe_kind=recipe_kind,
            recipe_json=json.dumps(recipe) if recipe else None,
            pinned_version=str(f.get("version")) if f.get("version") else None,
            trust_tier=self.default_trust_tier,
            status=status,
            pushed_at=_rfc3339(official.get("updatedAt") or official.get("publishedAt")),
        )


def _recipe(name: str, f: dict) -> tuple[str | None, dict | None]:
    """Build an install recipe. Remote HTTP servers and stdio packages need
    different commands; anything else is 'manual' and executes nothing.

    Note the scope: always `local`. Project scope writes a committed .mcp.json
    that loads WITHOUT a trust prompt in non-interactive sessions, so it is
    never chosen automatically.
    """
    slug = _slug_from_name(name)

    remotes = f.get("remotes")
    if isinstance(remotes, list) and remotes:
        remote = remotes[0]
        if isinstance(remote, dict) and isinstance(remote.get("url"), str):
            transport = "http" if "http" in str(remote.get("type", "")) else "sse"
            return "claude_mcp_http", {
                "kind": "claude_mcp_http",
                "argv": ["claude", "mcp", "add", "--transport", transport,
                         slug, remote["url"], "-s", "local"],
                "undo_argv": ["claude", "mcp", "remove", slug],
            }

    packages = f.get("packages")
    if isinstance(packages, list) and packages:
        pkg = packages[0]
        if isinstance(pkg, dict):
            registry = str(pkg.get("registryType") or pkg.get("registry_name") or "")
            ident = pkg.get("identifier") or pkg.get("name")
            version = pkg.get("version")
            if ident and registry.lower() in ("npm", "node"):
                spec = f"{ident}@{version}" if version else str(ident)
                return "claude_mcp_stdio", {
                    "kind": "claude_mcp_stdio",
                    "argv": ["claude", "mcp", "add", slug, "-s", "local",
                             "--", "npx", "-y", spec],
                    "undo_argv": ["claude", "mcp", "remove", slug],
                }
            if ident and registry.lower() in ("pypi", "pip"):
                spec = f"{ident}=={version}" if version else str(ident)
                return "claude_mcp_stdio", {
                    "kind": "claude_mcp_stdio",
                    "argv": ["claude", "mcp", "add", slug, "-s", "local",
                             "--", "uvx", spec],
                    "undo_argv": ["claude", "mcp", "remove", slug],
                }

    return "manual", None


def _slug_from_name(name: str) -> str:
    """Registry names look like 'io.github.owner/server' or 'ai.waystation/gmail'.

    Using only the last segment collides constantly - many owners publish a
    server called 'memory' or 'gmail'. The owner is the disambiguator, so the
    slug keeps it: 'io.github.punkpeye/fastmcp' -> 'punkpeye-fastmcp'.
    """
    namespace, _, server = name.rpartition("/")
    if not namespace:
        return name

    # Drop the reverse-DNS prefix ('io.github.', 'ai.', 'com.') and keep the
    # owner, which is the part a human recognises.
    owner = namespace.split(".")[-1] if "." in namespace else namespace
    if not owner or owner == server:
        return server or name
    return f"{owner}-{server}"


def _rfc3339(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:19] + "Z" if len(value) >= 19 else value


def _canon(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip().lower().rstrip("/")
    for prefix in ("https://", "http://", "www."):
        if u.startswith(prefix):
            u = u[len(prefix):]
    if u.endswith(".git"):
        u = u[:-4]
    return u or None


def _q(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")
