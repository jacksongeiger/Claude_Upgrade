"""GitHub funnel tests.

Runs against a frozen fixture captured from the live search API, which
deliberately includes `atlanhq/camelot` — the archived repo that GitHub's own
search ranks FIRST for "pdf table extraction" while missing docling, MinerU and
markitdown entirely. That failure is the reason this project exists, so it gets
a permanent test.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rdx import config, db, ingest, retrieve  # noqa: E402
from rdx.funnels import github  # noqa: E402
from rdx.funnels.base import HttpResponse  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "github_search_pdf.json"


class FakeHttp:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get(self, url, *, etag=None, headers=None):
        self.calls.append(url)
        return HttpResponse(status=200, body=json.dumps(self.payload).encode())


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def conn(payload):
    c = db.init_db(":memory:")
    ingest.run_funnel(c, "github", full=False, limit=len(payload["items"]),
                      http=FakeHttp(payload))
    db.recompute_eligibility(c, 2000)
    c.commit()
    yield c
    c.close()


def slugs(c):
    return {r["slug"] for r in c.execute("SELECT slug FROM resource")}


# --------------------------------------------------------------------------
# The motivating failure
# --------------------------------------------------------------------------

def test_archived_repo_is_stored_but_unreachable(conn):
    """camelot is archived. It must be visible in `rdx audit` for review, and
    provably unreachable by retrieval — GitHub ranking it #1 is the bug."""
    row = conn.execute(
        "SELECT status, archived, eligible, quality_score FROM resource "
        "WHERE slug = 'camelot'").fetchone()
    assert row is not None, "archived repo was dropped instead of recorded"
    assert row["archived"] == 1
    assert row["status"] == "deprecated"
    assert row["eligible"] == 0
    assert row["quality_score"] == 0.0

    for r, _ in db.candidates(conn, '"pdf" OR "table" OR "extraction"'):
        assert r.slug != "camelot", "archived repo reached retrieval"


def test_the_tools_github_search_missed_are_present(conn):
    """docling, MinerU and markitdown are all absent from GitHub's own top-10
    for this query. The whole point is that they are in the index."""
    assert {"docling", "mineru", "markitdown"} <= slugs(conn)


def test_motivating_query_surfaces_a_maintained_tool(conn):
    cfg = config.load_config(min_score=0.0, min_margin=0.0, shadow=False)
    prompt = "is there a library for extracting tables from pdfs"
    rows, _ = retrieve.search(conn, prompt)
    scored = retrieve.score_candidates(rows, cfg,
                                       terms=retrieve.query_terms(prompt))
    top = [c.resource.slug for c in scored[:5]]
    assert top, "no candidates at all"
    assert "camelot" not in top
    assert any(s in top for s in
               ("docling", "mineru", "markitdown", "stirling-pdf")), top


# --------------------------------------------------------------------------
# Quality signal — the thing this funnel exists to supply
# --------------------------------------------------------------------------

def test_stars_and_push_dates_are_captured(conn):
    row = conn.execute(
        "SELECT stars, pushed_at FROM resource WHERE slug = 'docling'").fetchone()
    assert row["stars"] > 1000
    assert row["pushed_at"] and row["pushed_at"].endswith("Z")


def test_quality_scores_are_spread_not_saturated(conn):
    """With the old divisor everything above ~30k stars pinned to 1.0, making
    the whole top of the index indistinguishable."""
    scores = [r["quality_score"] for r in conn.execute(
        "SELECT quality_score FROM resource WHERE archived = 0")]
    assert len(set(scores)) > 3, "quality_score is not discriminating"
    assert max(scores) < 1.0, "scores are saturating at the cap again"


def test_more_stars_scores_higher(conn):
    def q(slug):
        return conn.execute(
            "SELECT quality_score FROM resource WHERE slug = ?", (slug,)
        ).fetchone()["quality_score"]

    assert q("markitdown") > q("docling") > q("paperless-ngx")


# --------------------------------------------------------------------------
# Trust tiers
# --------------------------------------------------------------------------

def test_nothing_from_github_is_ever_green(conn):
    """Installing a package executes third-party code. Nothing auto-installs."""
    tiers = {r["trust_tier"] for r in conn.execute(
        "SELECT trust_tier FROM resource")}
    assert "green" not in tiers


def test_well_established_permissive_repos_are_yellow(conn):
    row = conn.execute(
        "SELECT trust_tier FROM resource WHERE slug = 'docling'").fetchone()
    assert row["trust_tier"] == "yellow"  # 66k stars, MIT, actively pushed


def test_unclear_license_is_red(conn):
    """NOASSERTION means the license could not be identified — that is a
    reason to look before installing."""
    row = conn.execute(
        "SELECT trust_tier FROM resource WHERE slug = 'mineru'").fetchone()
    assert row["trust_tier"] == "red"


def test_archived_is_red(conn):
    row = conn.execute(
        "SELECT trust_tier FROM resource WHERE slug = 'camelot'").fetchone()
    assert row["trust_tier"] == "red"


@pytest.mark.parametrize("stars,pushed,lic,archived,expected", [
    (5000, "2026-09-01T00:00:00Z", "MIT", False, "yellow"),
    (5000, "2026-09-01T00:00:00Z", "MIT", True, "red"),      # archived
    (100, "2026-09-01T00:00:00Z", "MIT", False, "red"),      # too few stars
    (5000, "2021-01-01T00:00:00Z", "MIT", False, "red"),     # stale
    (5000, "2026-09-01T00:00:00Z", None, False, "red"),      # no license
    (5000, "2026-09-01T00:00:00Z", "NOASSERTION", False, "red"),
])
def test_tier_rules(stars, pushed, lic, archived, expected):
    assert github._tier(stars, pushed, lic, archived) == expected


# --------------------------------------------------------------------------
# Ingest hygiene
# --------------------------------------------------------------------------

def test_forks_are_dropped():
    f = github.GitHubFunnel()
    from rdx.models import RawRecord
    rec = RawRecord(natural_key="a/b", type="library", source_ref="x", fields={
        "full_name": "someone/fork-of-docling", "fork": True,
        "description": "A fork of something with a long enough description.",
        "html_url": "https://github.com/someone/fork-of-docling"})
    assert f.to_draft(rec) is None


def test_short_descriptions_are_dropped():
    f = github.GitHubFunnel()
    from rdx.models import RawRecord
    rec = RawRecord(natural_key="a/b", type="library", source_ref="x", fields={
        "full_name": "a/b", "description": "tool",
        "html_url": "https://github.com/a/b"})
    assert f.to_draft(rec) is None


def test_canon_key_enables_cross_funnel_dedupe(conn):
    row = conn.execute(
        "SELECT canon_key FROM resource WHERE slug = 'docling'").fetchone()
    assert row["canon_key"] == "github.com/docling-project/docling"


def test_recipe_is_manual_not_a_guessed_install_command(conn):
    """A repo name is not reliably its package name. Guessing `pip install
    <repo>` would be a typosquatting vector, so the recipe points at the URL
    and says to check the README."""
    row = conn.execute(
        "SELECT recipe_kind, recipe_json FROM resource "
        "WHERE slug = 'docling'").fetchone()
    assert row["recipe_kind"] == "manual"
    recipe = json.loads(row["recipe_json"])
    assert recipe["url"].startswith("https://github.com/")
    assert "argv" not in recipe


def test_query_set_has_a_quality_floor():
    for q in github._queries():
        assert "archived:false" in q
        assert "fork:false" in q
        assert f"stars:>{github.MIN_STARS}" in q
        assert "pushed:>" in q


def test_one_failing_query_does_not_abandon_the_crawl(payload):
    """A rate-limited or 5xx topic must not cost us the other topics."""
    from rdx.funnels.base import HttpError

    class FlakyHttp(FakeHttp):
        def get(self, url, *, etag=None, headers=None):
            if len(self.calls) == 0:
                self.calls.append(url)
                raise HttpError("429 rate limited")
            return super().get(url, etag=etag, headers=headers)

    c = db.init_db(":memory:")
    report = ingest.run_funnel(c, "github", full=False, limit=20,
                               http=FlakyHttp(payload))
    assert report.status == "ok"
    assert report.n_upserted > 0
    c.close()
