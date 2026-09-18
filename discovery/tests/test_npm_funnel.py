"""npm funnel tests.

The fixture is a frozen live response (2026-09-17) for `keywords:mcp-server`,
25 objects. It contains the cases the tier rules exist for: a proprietary
license with high downloads (@taazkareem/clickup-mcp-server), scoped names,
and packages whose repository resolves to GitHub so they dedupe with the
GitHub funnel's rows.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rdx import db, ingest  # noqa: E402
from rdx.funnels import npm  # noqa: E402
from rdx.funnels.base import HttpError, HttpResponse  # noqa: E402
from rdx.models import RawRecord  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "npm_search_mcp_server.json"


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
def conn(payload, monkeypatch):
    monkeypatch.setattr(npm.time, "sleep", lambda s: None)
    c = db.init_db(":memory:")
    ingest.run_funnel(c, "npm", full=False, limit=len(payload["objects"]),
                      http=FakeHttp(payload))
    db.recompute_eligibility(c, 2000)
    c.commit()
    yield c
    c.close()


def rows(c):
    return {r["name"]: dict(r) for r in c.execute("SELECT * FROM resource")}


def draft_for(name, **over):
    base = {"name": name, "description": "A long enough description of a package for the floor",
            "keywords": ["mcp"], "version": "1.2.3", "license": "MIT", "date": "2026-09-01T00:00:00.000Z",
            "links": {"repository": "git+https://github.com/acme/thing.git",
                      "npm": f"https://www.npmjs.com/package/{name}"},
            "downloads": {"monthly": 50_000, "weekly": 12_000}, "flags": {"insecure": 0}}
    base.update(over)
    return npm.NpmFunnel().to_draft(RawRecord(natural_key=name, type="library", fields=base, source_ref="t"))


# --------------------------------------------------------------------------
# The fixture through the real pipeline
# --------------------------------------------------------------------------

def test_fixture_rows_land_with_usage_signal(conn):
    r = rows(conn)
    assert "@modelcontextprotocol/sdk" not in r  # not in this query's page
    assert "@postman/postman-mcp-server" in r
    row = r["@postman/postman-mcp-server"]
    assert row["funnel"] == "npm" and row["type"] == "library"
    assert row["install_count"] and row["install_count"] > 10_000
    assert row["slug"] == "postman-postman-mcp-server"
    assert row["canon_key"] == "github.com/postmanlabs/postman-mcp-server"
    assert row["url"] == "https://github.com/postmanlabs/postman-mcp-server"


def test_proprietary_license_is_red_despite_downloads(conn):
    row = rows(conn)["@taazkareem/clickup-mcp-server"]
    assert row["install_count"] > 50_000
    assert row["trust_tier"] == "red"


def test_permissive_fresh_popular_with_repo_is_yellow(conn):
    row = rows(conn)["@postman/postman-mcp-server"]
    assert row["trust_tier"] == "yellow"


def test_nothing_from_npm_is_ever_green(conn):
    tiers = {r["trust_tier"] for r in rows(conn).values()}
    assert "green" not in tiers and tiers <= {"yellow", "red"}


def test_recipe_is_manual_with_the_exact_install_line(conn):
    row = rows(conn)["@postman/postman-mcp-server"]
    assert row["recipe_kind"] == "manual"
    recipe = json.loads(row["recipe_json"])
    assert recipe["command"].startswith("npm install @postman/postman-mcp-server@")
    assert row["pinned_version"]


def test_query_is_built_for_use_not_text_match(conn, payload):
    http = FakeHttp(payload)
    list(npm.NpmFunnel().fetch({}, http, limit=1))
    assert "keywords%3Amcp-server" in http.calls[0] or "keywords:mcp-server" in http.calls[0]
    assert "not%3Adeprecated" in http.calls[0] or "not:deprecated" in http.calls[0]
    assert "size=250" in http.calls[0] and "popularity=1.0" in http.calls[0]


# --------------------------------------------------------------------------
# to_draft rules
# --------------------------------------------------------------------------

def test_download_floor_drops_the_row():
    assert draft_for("tiny", downloads={"monthly": 120}) is None


def test_short_description_drops_the_row():
    assert draft_for("x-pkg", description="too short") is None


def test_invalid_package_name_drops_the_row():
    assert draft_for("Not A Package!") is None


@pytest.mark.parametrize("over,expected", [
    ({}, "yellow"),
    ({"downloads": {"monthly": 8_000}}, "red"),                       # popular enough to index, not to trust
    ({"license": "Proprietary"}, "red"),
    ({"license": "UNLICENSED"}, "red"),
    ({"license": None}, "red"),
    ({"date": "2024-01-01T00:00:00.000Z"}, "red"),                    # stale
    ({"links": {"npm": "https://www.npmjs.com/package/x"}}, "red"),   # no repository
    ({"flags": {"insecure": 1}}, "red"),
])
def test_tier_rules(over, expected):
    d = draft_for("x-pkg", **over)
    assert d is not None and d.trust_tier == expected


def test_canon_key_matches_the_github_funnels_key():
    d = draft_for("x-pkg", links={"repository": "git+ssh://git@github.com/Acme/Thing.git"})
    assert d.canon_key == "github.com/acme/thing"
    d2 = draft_for("y-pkg", links={"repository": "https://gitlab.com/acme/thing"})
    assert d2.canon_key == "npm/y-pkg"


def test_scoped_name_slug_and_id():
    d = draft_for("@scope/my.pkg")
    assert d.slug == "scope-my.pkg"
    assert d.id == "library:npm:@scope/my.pkg"
    assert "npm" in d.tags


def test_dedupes_with_a_github_row_through_canon_key(conn):
    """A GitHub row for the same repo lands on the same canon key, so
    retrieval treats them as one thing rather than two candidates."""
    from rdx.funnels import github
    item = {"name": "postman-mcp-server", "full_name": "postmanlabs/postman-mcp-server",
            "description": "Postman MCP server for collections and environments in Claude",
            "html_url": "https://github.com/postmanlabs/postman-mcp-server", "stargazers_count": 1500,
            "pushed_at": "2026-09-10T00:00:00Z", "archived": False, "fork": False,
            "license": {"spdx_id": "Apache-2.0"}, "topics": ["mcp"], "language": "TypeScript"}
    gh = github.GitHubFunnel().to_draft(RawRecord(natural_key=item["full_name"], type="library",
                                                  fields=item, source_ref="t"))
    npm_key = rows(conn)["@postman/postman-mcp-server"]["canon_key"]
    assert gh.canon_key == npm_key


# --------------------------------------------------------------------------
# failure paths
# --------------------------------------------------------------------------

def test_one_failing_query_does_not_abandon_the_crawl(payload, monkeypatch):
    monkeypatch.setattr(npm.time, "sleep", lambda s: None)

    class Flaky:
        def __init__(self):
            self.n = 0

        def get(self, url, *, etag=None, headers=None):
            self.n += 1
            if self.n == 1:
                raise HttpError("429 rate limited")
            return HttpResponse(status=200, body=json.dumps(payload).encode())

    pages = list(npm.NpmFunnel().fetch({}, Flaky(), limit=1))
    assert pages and pages[0].records


def test_total_failure_is_an_error_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(npm.time, "sleep", lambda s: None)

    class Dead:
        def get(self, url, *, etag=None, headers=None):
            raise HttpError("boom")

    with pytest.raises(HttpError):
        list(npm.NpmFunnel().fetch({}, Dead()))
