"""Anthropic plugin marketplace funnels.

Four files, all unauthenticated raw GitHub fetches, all verified live:
  claude-plugins-official    296 plugins
  claude-plugins-community  2282 plugins  (Anthropic-owned)
  claude-code                 13 plugins
  skills                       5 plugins  (anthropic-agent-skills)

No delta machinery here: these are small, slowly-changing files, so an ETag
conditional GET plus full replace is ~10 lines and strictly simpler than
cursors and watermarks. Real delta sync is reserved for the MCP registry,
where 32k records make it matter.

Metadata coverage in the community file is thin - measured: category 157/2282,
author 36/2282, tags 1/2282 - so nothing here may assume those fields exist.
Community plugins consequently rank low, which is correct: they should mostly
stay silent.
"""

from __future__ import annotations

from typing import Any, Iterator

from ..models import FunnelPage, RawRecord, ResourceDraft
from .base import HttpClient

RAW = "https://raw.githubusercontent.com/anthropics/{repo}/main/.claude-plugin/marketplace.json"

FIELD_WHITELIST = frozenset({
    "name", "description", "displayName", "category", "author", "homepage",
    "tags", "keywords", "source", "version", "license", "repository",
})

# Component keys whose presence becomes a searchable facet. This is what makes
# a pre-install capability preview possible: `claude plugin details` only works
# on already-installed plugins, but the marketplace JSON tells us up front that
# a plugin ships, say, a UserPromptSubmit hook.
COMPONENT_KEYS = ("skills", "commands", "agents", "hooks", "mcpServers", "lspServers")


class MarketplaceFunnel:
    supports_delta = False

    def __init__(self, name: str, repo: str, trust_tier: str) -> None:
        self.name = name
        self.repo = repo
        self.default_trust_tier = trust_tier
        self.field_whitelist = FIELD_WHITELIST

    @property
    def url(self) -> str:
        return RAW.format(repo=self.repo)

    def fetch(self, state: dict[str, Any], http: HttpClient,
              limit: int | None = None) -> Iterator[FunnelPage]:
        resp = http.get(self.url, etag=state.get("last_etag"))
        if resp.status == 304:
            yield FunnelPage(records=[], not_modified=True)
            return

        data = resp.json()
        plugins = data.get("plugins", []) or []
        if limit is not None:
            plugins = plugins[:limit]

        records = [
            RawRecord(
                natural_key=str(p.get("name") or ""),
                type="plugin",
                fields=p,
                source_ref=f"{self.url}#{p.get('name')}",
            )
            for p in plugins
            if isinstance(p, dict) and p.get("name")
        ]
        yield FunnelPage(records=records, etag=resp.etag)

    def _tier_for(self, source: Any) -> str:
        """SHA-pinned github/git-subdir sources from a first-party marketplace
        are yellow (one-keystroke install). Everything else is red."""
        if isinstance(source, dict):
            stype = source.get("source") or source.get("type")
            pinned = bool(source.get("sha") or source.get("ref"))
            if stype in ("github", "git-subdir") and pinned:
                return self.default_trust_tier
            if stype in ("url", "git-subdir") and pinned:
                return self.default_trust_tier
        return "red"

    def to_draft(self, rec: RawRecord) -> ResourceDraft | None:
        f, _ = _whitelist(rec.fields, self.field_whitelist)
        name = str(f.get("name") or "").strip()
        if not name:
            return None

        description = str(f.get("description") or "").strip()
        if len(description) < 10:
            return None  # nothing to match on, and nothing to show the user

        tags: list[str] = []
        category = f.get("category")
        if isinstance(category, str) and category:
            tags.append(category)
        for key in COMPONENT_KEYS:
            if rec.fields.get(key):
                tags.append(f"has:{key}")

        source = f.get("source")
        version = f.get("version")
        pinned = None
        if isinstance(source, dict):
            pinned = source.get("sha") or source.get("ref") or version
        elif isinstance(version, str):
            pinned = version

        homepage = f.get("homepage")
        repo = f.get("repository")
        url = homepage if isinstance(homepage, str) else None
        if not url and isinstance(repo, str):
            url = repo
        if not url and isinstance(source, dict) and isinstance(source.get("repo"), str):
            url = f"https://github.com/{source['repo']}"

        return ResourceDraft(
            id=f"plugin:{self.name}:{name}",
            type="plugin",
            name=str(f.get("displayName") or name),
            slug=name,
            funnel=self.name,
            source_ref=rec.source_ref,
            summary=description,
            url=url,
            tags=tags[:5],
            canon_key=_canon(url),
            recipe_kind="claude_plugin",
            recipe_json=_recipe(name, self.marketplace_id),
            pinned_version=str(pinned) if pinned else None,
            trust_tier=self._tier_for(source),
        )

    @property
    def marketplace_id(self) -> str:
        return {
            "mp_official": "claude-plugins-official",
            "mp_community": "claude-community",
            "mp_claude_code": "claude-code-plugins",
            "mp_skills": "anthropic-agent-skills",
        }.get(self.name, self.name)


def _recipe(plugin_name: str, marketplace: str) -> str:
    import json
    return json.dumps({
        "kind": "claude_plugin",
        "argv": ["claude", "plugin", "install",
                 f"{plugin_name}@{marketplace}", "--scope", "user"],
        "undo_argv": ["claude", "plugin", "uninstall", plugin_name],
    })


def _canon(url: str | None) -> str | None:
    """Normalize a repo URL so the same project arriving from two funnels
    dedupes instead of being suggested twice."""
    if not url:
        return None
    u = url.strip().lower().rstrip("/")
    for prefix in ("https://", "http://", "www."):
        if u.startswith(prefix):
            u = u[len(prefix):]
    if u.endswith(".git"):
        u = u[:-4]
    return u or None


def _whitelist(fields: dict, allowed: frozenset[str]) -> tuple[dict, int]:
    from ..sanitize import apply_whitelist
    return apply_whitelist(fields, allowed)


def instances() -> list[MarketplaceFunnel]:
    return [
        MarketplaceFunnel("mp_official", "claude-plugins-official", "yellow"),
        MarketplaceFunnel("mp_community", "claude-plugins-community", "yellow"),
        MarketplaceFunnel("mp_claude_code", "claude-code", "yellow"),
        MarketplaceFunnel("mp_skills", "skills", "yellow"),
    ]
