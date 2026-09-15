"""Ingest pipeline driver.

Every record from every funnel passes through sanitize_draft() here, exactly
once, centrally. Funnels shape data; they never sanitize. That way adding a
funnel cannot introduce a sanitizer bypass.

Commits are batched at 500 rows. A single 32k-row transaction would hold the
write lock long enough for the hook's log insert to time out, and a hook that
silently stops logging is a hook you cannot calibrate.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
import sqlite3
from typing import Any, Iterable

from . import db, sanitize
from .funnels import get_funnel
from .funnels.base import HttpClient, HttpError
from .models import FunnelReport, RawRecord, ResourceDraft

BATCH_SIZE = 500


def utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _months_since(timestamp: str | None, *, now: _dt.datetime | None = None) -> float:
    if not timestamp:
        return 999.0
    now = now or _dt.datetime.now(_dt.timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            then = _dt.datetime.strptime(timestamp, fmt).replace(
                tzinfo=_dt.timezone.utc)
            break
        except ValueError:
            continue
    else:
        return 999.0
    return max(0.0, (now - then).days / 30.44)


def quality_score(*, stars: int | None = None, pushed_at: str | None = None,
                  archived: bool = False, install_count: int | None = None,
                  trust_tier: str = "red",
                  now: _dt.datetime | None = None) -> float:
    """A 0..1 quality signal computed purely from structured fields.

    Deliberately not a popularity contest: freshness multiplies popularity, so
    a 3k-star repo last touched in 2021 scores below a 300-star repo pushed
    last week. That is the exact failure the baseline GitHub search showed us,
    where an archived project ranked first.
    """
    if archived:
        return 0.0

    popularity = 0.0
    if stars:
        popularity = max(popularity, min(1.0, math.log10(1 + stars) / 4.5))
    if install_count:
        popularity = max(popularity, min(1.0, math.log10(1 + install_count) / 4.0))
    if not stars and not install_count:
        popularity = 0.25  # unknown, not zero: most registry entries have no stars

    months = _months_since(pushed_at, now=now)
    if months >= 900:
        freshness = 0.6           # unknown push date: neutral, not punished
    elif months <= 3:
        freshness = 1.0
    elif months <= 12:
        freshness = 0.85
    elif months <= 18:
        freshness = 0.6
    elif months <= 36:
        freshness = 0.3
    else:
        freshness = 0.1

    # Trust is deliberately NOT folded in here: retrieval already scores it as
    # a separate term (cfg.w_trust). Including it in both double-counted, and
    # pushed first-party plugins that merely need a credential (github, linear,
    # context7) below anonymous registry entries.
    return round(min(1.0, popularity * freshness), 4)


def sanitize_and_store(conn: sqlite3.Connection, draft: ResourceDraft, *,
                       now: str, extra_text: Iterable[str] = ()) -> tuple[bool, bool]:
    """Sanitize one draft and upsert it.

    Returns (stored, quarantined). Quarantined rows ARE stored - with
    status='quarantined' - because deleting them would destroy the audit trail
    and make false-positive review impossible.
    """
    result = sanitize.sanitize_draft(
        draft.name, draft.summary, url=draft.url, tags=draft.tags,
        extra_text=list(extra_text),
    )

    src_sha = hashlib.sha256(
        (draft.summary or "").encode("utf-8", "replace")).hexdigest()

    draft.name = result.name
    draft.summary = result.summary
    draft.url = result.url
    draft.tags = result.tags
    draft.slug = sanitize.sanitize_slug(draft.slug)

    if result.quarantine:
        draft.status = "quarantined"

    # A resource that needs credentials is not dangerous, but it does need a
    # human to look at it before installing, so it can never be green/yellow.
    if result.needs_secrets and draft.trust_tier != "green":
        draft.trust_tier = "red"

    score = quality_score(
        stars=draft.stars, pushed_at=draft.pushed_at, archived=draft.archived,
        install_count=draft.install_count,
    )

    db.upsert_resource(conn, draft, now=now, flags=result.flags,
                       blocking_flags=result.blocking_flags,
                       summary_src_sha=src_sha, quality_score=score)
    return True, result.quarantine


def run_funnel(conn: sqlite3.Connection, name: str, *, full: bool = False,
               limit: int | None = None,
               http: HttpClient | None = None) -> FunnelReport:
    """Fetch, sanitize and store one funnel's records."""
    funnel = get_funnel(name)
    http = http or HttpClient()
    state = db.get_funnel_state(conn, name)
    run_ts = utcnow()
    report = FunnelReport(funnel=name)

    try:
        pending = 0
        for page in funnel.fetch(state, http, limit):
            if page.not_modified:
                report.not_modified = True
                db.set_funnel_state(conn, name, last_run_at=run_ts,
                                    last_status="ok", last_error=None)
                return report

            for rec in page.records:
                report.n_seen += 1
                draft = funnel.to_draft(rec)
                if draft is None:
                    report.n_dropped += 1
                    continue
                _, quarantined = sanitize_and_store(
                    conn, draft, now=run_ts,
                    extra_text=_extra_text(rec, funnel))
                report.n_upserted += 1
                report.n_quarantined += int(quarantined)
                pending += 1
                if pending >= BATCH_SIZE:
                    conn.commit()
                    pending = 0

            conn.commit()
            pending = 0
            if page.cursor is not None:
                db.set_funnel_state(conn, name, last_cursor=page.cursor)
            if page.etag:
                db.set_funnel_state(conn, name, last_etag=page.etag)

            if limit is not None and report.n_seen >= limit:
                break

        conn.commit()

        # Only a clean, complete run may deprecate rows: a crash mid-sync would
        # otherwise wipe out everything the funnel had not yet re-reported.
        if full and limit is None:
            report.n_deprecated = db.mark_stale_deprecated(conn, name, run_ts)
            conn.commit()

    except HttpError as exc:
        report.status = "error"
        report.error = str(exc)
        db.set_funnel_state(conn, name, last_run_at=run_ts, last_status="error",
                            last_error=str(exc))
        return report

    db.set_funnel_state(
        conn, name, last_run_at=run_ts, last_status="ok", last_error=None,
        n_seen=report.n_seen, n_upserted=report.n_upserted,
        n_quarantined=report.n_quarantined,
    )
    return report


def _extra_text(rec: RawRecord, funnel: Any) -> list[str]:
    """Untrusted strings that are screened but never rendered.

    Screening these costs almost nothing and closes the gap where an attacker
    hides a payload in a field we happen not to display.
    """
    out: list[str] = []
    for key in ("category", "author", "homepage", "title", "keywords"):
        val = rec.fields.get(key)
        if isinstance(val, str):
            out.append(val)
        elif isinstance(val, dict):
            out.extend(str(v) for v in val.values() if isinstance(v, str))
        elif isinstance(val, list):
            out.extend(str(v) for v in val if isinstance(v, str))
    return out


def sync_all(conn: sqlite3.Connection, *, full: bool = True,
             limit: int | None = None,
             only: list[str] | None = None) -> list[FunnelReport]:
    from .funnels import all_funnels

    names = only or list(all_funnels())
    reports = [run_funnel(conn, n, full=full, limit=limit) for n in names]
    from . import config
    db.recompute_eligibility(conn, config.load_config().max_eligible)
    conn.commit()
    return reports
