"""Funnel protocol and the shared HTTP client.

A funnel knows how to fetch from one upstream source and how to shape a record
into a ResourceDraft. It does NOT sanitize - that happens once, centrally, in
ingest.py, so no funnel can accidentally skip it.

Future social funnels (HN, Bluesky, npm, PyPI, GitHub) implement this same
protocol. The one rule that keeps the interface stable: a social funnel emits
*mentions*, and its to_draft() must resolve a mention to a concrete repo or
package URL, or return None. If a link cannot be turned into an installable
thing, it is not a resource - there is no mention table and no resolver
subsystem.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator, Protocol

from ..models import FunnelPage, RawRecord, ResourceDraft

USER_AGENT = "rdx/0.1 (+https://github.com/jacksongeiger/Claude_Upgrade)"
DEFAULT_TIMEOUT = 30


class HttpError(RuntimeError):
    pass


@dataclass
class HttpResponse:
    status: int
    body: bytes
    etag: str | None = None

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class HttpClient:
    """Minimal stdlib HTTP client with conditional-GET support.

    Deliberately not `requests`: the hook path must stay dependency-free, and
    six GETs do not justify a supply-chain surface of their own.
    """

    def __init__(self, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def get(self, url: str, *, etag: str | None = None,
            headers: dict[str, str] | None = None) -> HttpResponse:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        if etag:
            req.add_header("If-None-Match", etag)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return HttpResponse(
                    status=resp.status,
                    body=resp.read(),
                    etag=resp.headers.get("ETag"),
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return HttpResponse(status=304, body=b"", etag=etag)
            raise HttpError(f"{url} -> HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise HttpError(f"{url} -> {exc.reason}") from exc


class Funnel(Protocol):
    """One upstream source."""

    name: str
    supports_delta: bool
    default_trust_tier: str
    field_whitelist: frozenset[str]

    def fetch(self, state: dict[str, Any], http: HttpClient,
              limit: int | None = None) -> Iterator[FunnelPage]:
        """Yield pages of raw records. May set `not_modified` on a 304."""
        ...

    def to_draft(self, rec: RawRecord) -> ResourceDraft | None:
        """Shape one raw record. Return None to drop it at ingest time."""
