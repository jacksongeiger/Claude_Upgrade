"""Dataclasses shared across ingest, sanitize and retrieval.

`ResourceDraft` is pre-sanitize and may contain arbitrary upstream text.
`Resource` is post-sanitize and is the only shape allowed near an envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RESOURCE_TYPES = ("mcp", "plugin", "skill", "library")
TRUST_TIERS = ("green", "yellow", "red")
STATUSES = ("active", "deprecated", "quarantined")
RECIPE_KINDS = (
    "claude_plugin",
    "claude_mcp_stdio",
    "claude_mcp_http",
    "pip",
    "npm",
    "enable_only",
    "manual",
)


@dataclass
class RawRecord:
    """One upstream record, exactly as fetched. Untrusted."""

    natural_key: str
    type: str
    fields: dict[str, Any]
    source_ref: str


@dataclass
class FunnelPage:
    records: list[RawRecord]
    cursor: str | None = None
    etag: str | None = None
    not_modified: bool = False


@dataclass
class ResourceDraft:
    """Pre-sanitize. Every text field here is untrusted third-party content."""

    id: str
    type: str
    name: str
    slug: str
    funnel: str
    source_ref: str
    summary: str = ""
    url: str | None = None
    tags: list[str] = field(default_factory=list)
    canon_key: str | None = None
    recipe_kind: str | None = None
    recipe_json: str | None = None
    pinned_version: str | None = None
    trust_tier: str = "red"
    status: str = "active"
    stars: int | None = None
    pushed_at: str | None = None
    archived: bool = False
    install_count: int | None = None


@dataclass
class Resource:
    """Post-sanitize. Safe to rank and render."""

    id: str
    type: str
    name: str
    slug: str
    summary: str
    url: str | None
    tags: list[str]
    trust_tier: str
    status: str
    quality_score: float
    stars: int | None = None
    install_count: int | None = None
    funnel: str = ""
    flags: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    resource: Resource
    bm25_raw: float
    bm25_norm: float
    score: float
    # Fraction of query terms that actually appear in the matched document.
    # bm25_norm is min-max scaled over the candidate set, so the top hit always
    # normalizes to 1.0 no matter how poor the match - coverage is the only
    # signal here that carries ABSOLUTE match quality.
    coverage: float = 0.0
    # Raw count of content terms matched. The fraction alone punishes verbose
    # prompts: "is there a way to integrate claude with slack for
    # notifications" matched `slack` - the only term that mattered - and still
    # scored 1/3. The count is what the gate should test.
    matched: int = 0


@dataclass
class Decision:
    """Result of one gate evaluation. `reason` is None only when inject is True."""

    inject: bool
    context: str | None = None
    items: list[Candidate] = field(default_factory=list)
    reason: str | None = None
    top_score: float | None = None
    margin: float | None = None
    n_candidates: int = 0
    query_terms: str = ""
    latency_ms: int = 0
    injection_id: int | None = None


@dataclass
class FunnelReport:
    funnel: str
    n_seen: int = 0
    n_upserted: int = 0
    n_quarantined: int = 0
    n_dropped: int = 0
    n_deprecated: int = 0
    status: str = "ok"
    error: str | None = None
    not_modified: bool = False
