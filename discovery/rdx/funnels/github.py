"""GitHub funnel — open-source libraries and tools.

This is the funnel that delivers the original ask. The Phase 1 funnels only
index the Claude ecosystem (MCP servers, plugins, skills); this one indexes the
much larger world of OSS libraries, which is where most of the answers actually
live. It is also the first funnel that supplies a real popularity signal:
stars, push recency, archived state and license. Until it exists,
`quality_score` is near-constant across the index and curation has to do all
the ranking.

Rate limits (measured, not assumed): the search API allows 30 requests/minute
authenticated and 10 unauthenticated, regardless of tier, and caps any single
query at 1,000 results. Both shape the design:

  * A small curated query set, not an attempt to enumerate GitHub.
  * A deliberate pause between requests, sized from whether a token is present.
  * Date partitioning is available via `created:` windows for queries that
    would exceed 1,000 results, though the curated set is built to stay under.

Scoring note: `archived: true` repos are stored as deprecated rather than
dropped. That is deliberate — the motivating failure was GitHub's own search
ranking an ARCHIVED repo (atlanhq/camelot) first for "pdf table extraction"
while missing docling, MinerU and markitdown entirely. Keeping archived repos
visible in `rdx audit`, but unreachable by retrieval, makes that failure
explicit instead of silent.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator

from .. import config
from ..models import FunnelPage, RawRecord, ResourceDraft
from .base import HttpClient, HttpError

SEARCH = "https://api.github.com/search/repositories"
PER_PAGE = 50

FIELD_WHITELIST = frozenset({
    "name", "full_name", "description", "html_url", "stargazers_count",
    "pushed_at", "archived", "fork", "license", "topics", "language",
    "open_issues_count", "size",
})

# Curated queries, not an enumeration of GitHub.
#
# Each targets a domain where "is there a library for this?" is a question
# someone actually asks, with a quality floor baked in. `pushed:` is the single
# best staleness filter and `archived:false` removes the dead-project failure
# mode at the source.
QUERY_TOPICS = [
    "pdf", "ocr", "document-processing",
    "web-scraping", "browser-automation", "crawler",
    "data-engineering", "dataframe", "etl",
    "llm", "rag", "embeddings", "vector-database",
    "cli", "tui", "developer-tools",
    "testing", "static-analysis", "code-generation",
    "api-client", "sdk", "automation",
]

MIN_STARS = 300
FRESH_SINCE = "2025-09-01"   # pushed within roughly the last year


def _queries() -> list[str]:
    return [
        f"topic:{topic} stars:>{MIN_STARS} pushed:>{FRESH_SINCE} "
        f"archived:false is:public fork:false"
        for topic in QUERY_TOPICS
    ]


class GitHubFunnel:
    name = "github"
    supports_delta = False
    default_trust_tier = "red"
    field_whitelist = FIELD_WHITELIST

    def __init__(self, token: str | None = None) -> None:
        # A token raises the search limit from 10/min to 30/min. Optional:
        # the funnel works without one, just slower.
        self.token = token or os.environ.get("GITHUB_TOKEN") or None

    @property
    def _pause(self) -> float:
        """Seconds between search requests, from the documented limits."""
        return 2.2 if self.token else 6.5

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch(self, state: dict[str, Any], http: HttpClient,
              limit: int | None = None) -> Iterator[FunnelPage]:
        seen = 0
        ok_queries = 0
        last_error: HttpError | None = None

        for i, query in enumerate(_queries()):
            if i:
                time.sleep(self._pause)

            url = (f"{SEARCH}?q={_q(query)}&sort=stars&order=desc"
                   f"&per_page={PER_PAGE}")
            try:
                resp = http.get(url, headers=self._headers())
            except HttpError as exc:
                # One bad query (rate limit, transient 5xx) must not abandon
                # the whole crawl; the remaining topics are still worth having.
                # But TOTAL failure must not be reported as success -- see the
                # re-raise below.
                last_error = exc
                continue

            ok_queries += 1
            items = (resp.json() or {}).get("items") or []
            records = [
                RawRecord(
                    natural_key=str(item.get("full_name") or ""),
                    type="library",
                    fields=item,
                    source_ref=f"{SEARCH}?q={query}",
                )
                for item in items
                if isinstance(item, dict) and item.get("full_name")
            ]
            seen += len(records)
            yield FunnelPage(records=records)

            if limit is not None and seen >= limit:
                return

        # Every single query failed. Swallowing that reported `github ok
        # seen=0` -- indistinguishable from "GitHub genuinely had nothing" --
        # and the discovery eval then blamed ranking for cases that had no
        # rows to rank. A funnel that fetched nothing at all is an error.
        if ok_queries == 0 and last_error is not None:
            raise last_error

    def to_draft(self, rec: RawRecord) -> ResourceDraft | None:
        from ..sanitize import apply_whitelist

        f, _ = apply_whitelist(rec.fields, self.field_whitelist)

        full_name = str(f.get("full_name") or "")
        if not full_name or "/" not in full_name:
            return None
        if f.get("fork"):
            return None  # forks are pure noise in this ecosystem

        description = str(f.get("description") or "").strip()
        if len(description) < config.MIN_DESCRIPTION_LEN:
            return None

        owner, _, repo = full_name.partition("/")
        url = str(f.get("html_url") or f"https://github.com/{full_name}")
        stars = int(f.get("stargazers_count") or 0)
        archived = bool(f.get("archived"))
        pushed_at = _rfc3339(f.get("pushed_at"))

        license_id = None
        lic = f.get("license")
        if isinstance(lic, dict):
            license_id = lic.get("spdx_id")

        topics = [t for t in (f.get("topics") or []) if isinstance(t, str)][:19]
        language = f.get("language")
        if isinstance(language, str) and language:
            topics.append(language.lower())

        return ResourceDraft(
            id=f"library:{self.name}:{full_name}",
            type="library",
            name=repo,
            slug=repo,
            funnel=self.name,
            source_ref=rec.source_ref,
            summary=description,
            url=url,
            tags=topics[:20],
            canon_key=f"github.com/{full_name}".lower(),
            recipe_kind="manual",
            recipe_json=json.dumps({
                "kind": "manual",
                "url": url,
                "note": "Install command depends on the package registry; "
                        "check the project README.",
            }),
            pinned_version=None,
            trust_tier=_tier(stars, pushed_at, license_id, archived),
            # Archived repos are kept and marked, not dropped: the whole point
            # is to make the dead-project failure visible rather than silent.
            status="deprecated" if archived else "active",
            stars=stars,
            pushed_at=pushed_at,
            archived=archived,
        )


def _tier(stars: int, pushed_at: str | None, license_id: str | None,
          archived: bool) -> str:
    """Trust tier for an OSS library.

    Installing a package still executes third-party code, so nothing here ever
    reaches green. Yellow means "well-established and maintained, one keystroke
    to install"; everything else needs an explicit look.
    """
    if archived:
        return "red"
    osi = license_id not in (None, "", "NOASSERTION", "Unlicense")
    if stars >= 1000 and osi and _is_fresh(pushed_at):
        return "yellow"
    return "red"


def _is_fresh(pushed_at: str | None, *, months: int = 12) -> bool:
    from ..ingest import _months_since
    return _months_since(pushed_at) <= months


def _rfc3339(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 19:
        return None
    return value[:19] + "Z"


def _q(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe=":><=/*")
