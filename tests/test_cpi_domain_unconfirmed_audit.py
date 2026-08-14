"""search_companies had the identical bug this whole file is about, on the
Companies tab, unfixed: its own domain filter dropped any company Apollo
returned with no domain field at all, exactly the "Apollo didn't say" read as
"Apollo said no" defect the people-side fix below exists to describe. Found by
re-auditing this page end to end rather than by a live report -- the two
endpoints share the same shape (mixed_companies/search splits into
organizations/accounts the same way mixed_people/api_search splits into
people/contacts, and Apollo's per-row field coverage is the same plan/quota
gap in both), so the same fix applies: split domain_dropped (confirmed
mismatch, still dropped) from domain_unconfirmed (no domain at all, kept and
flagged). Named distinctly from the people-side company_dropped/
company_unconfirmed because a company row dropped for "working somewhere
else" is nonsense -- cpi_search folds the count into the same
company_unconfirmed response field but reports the drop under its own
"domain" label ("a different company at that domain").

The Beta Bionics search came back empty a second time, past the fix in
test_cpi_search_buckets_audit.py, and this time honestly: "Apollo returned 24
people, and on checking, none of them matched: 24 working somewhere else."

That sentence is only true if all 24 really do work somewhere else. A live probe
of the same free endpoint, same domain, showed 355 real matches with clean
`organization.domain` fields -- so the drop had to be coming from something in
this account's own results that the earlier fix never accounted for: a row
Apollo returns with NO employer domain at all. Apollo's free-tier field coverage
is plan- and quota-dependent (this file's neighbor, the last-name masking, is
the same phenomenon on a different field), so some rows in a company-scoped
search carry an organization name but no domain to check it against.

The domain filter could not tell "Apollo told us a different company" (a real
mismatch -- keep dropping it) apart from "Apollo told us nothing" (evidence of
nothing) -- and folded both into the same "working somewhere else" claim. On an
account whose plan doesn't return domains for every row, that claim is false
for every row it fires on, on exactly the search this whole page is for.

Fixed by splitting the two: a row with a domain that disagrees is still dropped
and still counted in company_dropped. A row with no domain at all is kept,
flagged `employer_unconfirmed`, and counted separately in
company_unconfirmed -- not a rejection, since nothing about it was ruled out.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tracker.apollo_client as ac  # noqa: E402

_FAKE_API_KEY = "test-key"
_SEARCH = "/p2/b2b-agents/company-people-intelligence/search"


def _mock_response(json_data: dict) -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_data
    return m


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


@pytest.fixture(autouse=True)
def apollo_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")


# ── search_people itself ─────────────────────────────────────────────────────

@patch("tracker.apollo_client.requests.post")
def test_a_row_with_no_employer_domain_is_kept_not_dropped(mock_post):
    mock_post.return_value = _mock_response({
        "people": [
            {"id": "p1", "first_name": "Sean", "last_name": "Saint",
             "title": "Chief Executive Officer",
             "organization": {"id": "o1", "name": "Beta Bionics"}},  # no domain
        ],
    })
    meta = {}
    result = ac.search_people({"company_domains": ["betabionics.com"]},
                              _FAKE_API_KEY, meta=meta)
    assert [p["full_name"] for p in result] == ["Sean Saint"], (
        "a row Apollo simply didn't give a domain for was treated as a mismatch")
    assert meta["company_dropped"] == 0
    assert meta["company_unconfirmed"] == 1


@patch("tracker.apollo_client.requests.post")
def test_the_kept_row_is_flagged_so_the_reader_is_not_told_more_than_apollo_said(mock_post):
    mock_post.return_value = _mock_response({
        "people": [{"id": "p1", "first_name": "Sean", "last_name": "Saint",
                    "organization": {"name": "Beta Bionics"}}],
    })
    result = ac.search_people({"company_domains": ["betabionics.com"]}, _FAKE_API_KEY)
    assert result[0]["employer_unconfirmed"] is True


@patch("tracker.apollo_client.requests.post")
def test_a_row_with_no_organization_object_at_all_is_also_kept(mock_post):
    """Same gap, worse case: not even a name came back. Still not proof the
    person works elsewhere -- Apollo just told us nothing about their employer."""
    mock_post.return_value = _mock_response({
        "people": [{"id": "p1", "first_name": "Jane", "last_name": "Doe"}],
    })
    meta = {}
    result = ac.search_people({"company_domains": ["betabionics.com"]},
                              _FAKE_API_KEY, meta=meta)
    assert len(result) == 1
    assert result[0]["employer_unconfirmed"] is True
    assert meta["company_unconfirmed"] == 1
    assert meta["company_dropped"] == 0


@patch("tracker.apollo_client.requests.post")
def test_a_row_with_a_different_confirmed_domain_is_still_dropped(mock_post):
    """The mirror the whole fix has to preserve: this is the actual defect
    company_dropped exists for, and it must keep firing on a REAL mismatch,
    or the fix that stops false negatives just re-opens the false positives
    the original bug report was never about."""
    mock_post.return_value = _mock_response({
        "people": [
            {"id": "p1", "first_name": "Ana", "last_name": "Real",
             "organization": {"name": "Beta Bionics", "domain": "betabionics.com"}},
            {"id": "p2", "first_name": "Bill", "last_name": "Unrelated",
             "organization": {"name": "Microsoft", "domain": "microsoft.com"}},
        ],
    })
    meta = {}
    result = ac.search_people({"company_domains": ["betabionics.com"]},
                              _FAKE_API_KEY, meta=meta)
    assert [p["full_name"] for p in result] == ["Ana Real"]
    assert meta["company_dropped"] == 1
    assert meta["company_unconfirmed"] == 0
    assert "employer_unconfirmed" not in result[0]


@patch("tracker.apollo_client.requests.post")
def test_a_confirmed_match_and_an_unconfirmed_row_both_survive_together(mock_post):
    mock_post.return_value = _mock_response({
        "people": [
            {"id": "p1", "first_name": "Ana", "last_name": "Real",
             "organization": {"name": "Beta Bionics", "domain": "betabionics.com"}},
            {"id": "p2", "first_name": "Sean", "last_name": "Saint",
             "organization": {"name": "Beta Bionics"}},
            {"id": "p3", "first_name": "Bill", "last_name": "Unrelated",
             "organization": {"name": "Microsoft", "domain": "microsoft.com"}},
        ],
    })
    meta = {}
    result = ac.search_people({"company_domains": ["betabionics.com"]},
                              _FAKE_API_KEY, meta=meta)
    assert sorted(p["full_name"] for p in result) == ["Ana Real", "Sean Saint"]
    assert meta["company_dropped"] == 1
    assert meta["company_unconfirmed"] == 1


@patch("tracker.apollo_client.requests.post")
def test_an_all_unconfirmed_page_does_not_null_out_apollos_total(mock_post):
    """total_entries is only invalidated when a row was actually removed for
    disagreeing -- an unconfirmed row is still IN the results, so it does not
    describe a looser match than what's on screen the way a real drop does.

    A FULL page is returned here (served == per_page) so the separate
    ignoring-an-inconsistent-total heuristic -- which fires on a SHORT page
    claiming more exist, a different, already-covered concern -- cannot also
    explain a None here and mask a regression in this one."""
    mock_post.return_value = _mock_response({
        "people": [{"id": "p%d" % i, "first_name": "Sean", "last_name": "Saint",
                    "organization": {"name": "Beta Bionics"}} for i in range(24)],
        "pagination": {"total_entries": 355, "total_pages": 15},
    })
    meta = {}
    result = ac.search_people({"company_domains": ["betabionics.com"]}, _FAKE_API_KEY,
                              per_page=24, meta=meta)
    assert len(result) == 24
    assert meta["company_unconfirmed"] == 24
    assert meta["total_entries"] == 355


# ── The route ─────────────────────────────────────────────────────────────

def test_the_route_reports_unconfirmed_separately_from_rejected(client, monkeypatch):
    """Not a rejection: it must not inflate rejected_total or trigger the
    "Remove a filter" empty-state copy when everything else is fine."""
    def fake_search_people(filters, api_key, page=1, per_page=25, meta=None, **kw):
        if meta is not None:
            meta["company_dropped"] = 0
            meta["company_unconfirmed"] = 1
            meta["returned"] = 1
        return [{"id": "p1", "full_name": "Sean Saint",
                "organization_name": "Beta Bionics", "employer_unconfirmed": True}]

    monkeypatch.setattr(ac, "search_people", fake_search_people)
    r = client.post(_SEARCH, json={"entity": "people",
                                   "filters": {"company_domains": ["betabionics.com"],
                                              "company_detail": False}})
    d = r.get_json()
    assert d["company_unconfirmed"] == 1
    assert "rejected" not in d, "an unconfirmed row must not read as a rejection"
    assert len(d["results"]) == 1
    assert d["results"][0]["employer_unconfirmed"] is True


def test_the_route_still_reports_real_mismatches_as_rejected(client, monkeypatch):
    def fake_search_people(filters, api_key, page=1, per_page=25, meta=None, **kw):
        if meta is not None:
            meta["company_dropped"] = 3
            meta["company_unconfirmed"] = 0
            meta["returned"] = 3
        return []

    monkeypatch.setattr(ac, "search_people", fake_search_people)
    r = client.post(_SEARCH, json={"entity": "people",
                                   "filters": {"company_domains": ["betabionics.com"],
                                              "company_detail": False}})
    d = r.get_json()
    assert d["rejected"]["company"] == 3
    assert "company_unconfirmed" not in d


# ── The JS badge ─────────────────────────────────────────────────────────────

_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "js", "company_people_intelligence.js")


def _js():
    return open(_JS, encoding="utf-8").read()


def _js_function(name):
    """One named `function name(){...}` block, by brace counting -- good enough
    for this file's style (no nested function literals inside these bodies) and
    precise enough that a check against one function cannot be satisfied by an
    unrelated occurrence of the same string somewhere else in a 2,900-line file."""
    body = _js()
    start = body.index("function %s(" % name)
    open_brace = body.index("{", start)
    depth = 0
    for i in range(open_brace, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                return body[open_brace:i + 1]
    raise AssertionError("unbalanced braces in function %s" % name)


def test_the_table_company_cell_flags_an_unconfirmed_employer():
    assert "employer_unconfirmed" in _js_function("coCell")


def test_the_card_company_row_flags_an_unconfirmed_employer():
    assert "employer_unconfirmed" in _js_function("personCard")


def test_the_header_reports_the_unconfirmed_count():
    assert "STATE.companyUnconfirmed" in _js_function("unconfirmedNote")
    assert "unconfirmedNote()" in _js_function("renderResults")


# ── search_companies: the same fix, on the Companies tab ────────────────────

@patch("tracker.apollo_client.requests.post")
def test_a_company_row_with_no_domain_is_kept_not_dropped(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [{"id": "o1", "name": "Beta Bionics"}],  # no domain
    })
    meta = {}
    result = ac.search_companies({"domains": ["betabionics.com"]},
                                 _FAKE_API_KEY, meta=meta)
    assert [o["name"] for o in result] == ["Beta Bionics"], (
        "a company Apollo simply didn't give a domain for was treated as a mismatch")
    assert meta["domain_dropped"] == 0
    assert meta["domain_unconfirmed"] == 1


def test_the_kept_company_row_is_flagged_domain_unconfirmed():
    with patch("tracker.apollo_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response({
            "organizations": [{"id": "o1", "name": "Beta Bionics"}],
        })
        result = ac.search_companies({"domains": ["betabionics.com"]}, _FAKE_API_KEY)
    assert result[0]["domain_unconfirmed"] is True


@patch("tracker.apollo_client.requests.post")
def test_a_company_row_with_a_different_confirmed_domain_is_still_dropped(mock_post):
    """The mirror this fix has to preserve: a REAL mismatch must keep being
    dropped, or fixing the false negative just reopens the false positive the
    domain filter exists to catch."""
    mock_post.return_value = _mock_response({
        "organizations": [
            {"id": "o1", "name": "Beta Bionics", "primary_domain": "betabionics.com"},
            {"id": "o2", "name": "Microsoft", "primary_domain": "microsoft.com"},
        ],
    })
    meta = {}
    result = ac.search_companies({"domains": ["betabionics.com"]},
                                 _FAKE_API_KEY, meta=meta)
    assert [o["name"] for o in result] == ["Beta Bionics"]
    assert meta["domain_dropped"] == 1
    assert meta["domain_unconfirmed"] == 0
    assert "domain_unconfirmed" not in result[0]


@patch("tracker.apollo_client.requests.post")
def test_a_confirmed_company_and_an_unconfirmed_one_both_survive_together(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [
            {"id": "o1", "name": "Beta Bionics", "primary_domain": "betabionics.com"},
            {"id": "o2", "name": "Beta Bionics Inc"},  # no domain
            {"id": "o3", "name": "Microsoft", "primary_domain": "microsoft.com"},
        ],
    })
    meta = {}
    result = ac.search_companies({"domains": ["betabionics.com"]},
                                 _FAKE_API_KEY, meta=meta)
    assert sorted(o["name"] for o in result) == ["Beta Bionics", "Beta Bionics Inc"]
    assert meta["domain_dropped"] == 1
    assert meta["domain_unconfirmed"] == 1


@patch("tracker.apollo_client.requests.post")
def test_an_all_unconfirmed_company_page_does_not_null_out_apollos_total(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [{"id": "o1", "name": "Beta Bionics"}],
        "pagination": {"total_entries": 1, "total_pages": 1},
    })
    meta = {}
    result = ac.search_companies({"domains": ["betabionics.com"]}, _FAKE_API_KEY, meta=meta)
    assert len(result) == 1
    assert meta["domain_unconfirmed"] == 1
    assert meta["total_entries"] == 1


@patch("tracker.apollo_client.requests.post")
def test_a_dropped_company_row_nulls_the_total(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [{"id": "o1", "name": "Microsoft", "primary_domain": "microsoft.com"}],
        "pagination": {"total_entries": 1, "total_pages": 1},
    })
    meta = {}
    ac.search_companies({"domains": ["betabionics.com"]}, _FAKE_API_KEY, meta=meta)
    assert meta["total_entries"] is None


# ── The route, companies entity ──────────────────────────────────────────────

def test_the_route_reports_a_company_unconfirmed_domain_not_as_a_rejection(client, monkeypatch):
    def fake_search_companies(filters, api_key, page=1, per_page=25, meta=None, **kw):
        if meta is not None:
            meta["domain_dropped"] = 0
            meta["domain_unconfirmed"] = 1
        return [{"id": "o1", "name": "Beta Bionics", "domain_unconfirmed": True}]

    monkeypatch.setattr(ac, "search_companies", fake_search_companies)
    r = client.post(_SEARCH, json={"entity": "companies",
                                   "filters": {"domains": ["betabionics.com"]}})
    d = r.get_json()
    assert d["company_unconfirmed"] == 1
    assert "rejected" not in d, "an unconfirmed company row must not read as a rejection"
    assert d["results"][0]["domain_unconfirmed"] is True


def test_the_route_reports_a_dropped_company_domain_under_its_own_label(client, monkeypatch):
    def fake_search_companies(filters, api_key, page=1, per_page=25, meta=None, **kw):
        if meta is not None:
            meta["domain_dropped"] = 2
            meta["domain_unconfirmed"] = 0
        return []

    monkeypatch.setattr(ac, "search_companies", fake_search_companies)
    r = client.post(_SEARCH, json={"entity": "companies",
                                   "filters": {"domains": ["betabionics.com"]}})
    d = r.get_json()
    assert d["rejected"]["domain"] == 2
    assert d["rejected_labels"]["domain"] == "a different company at that domain", (
        "a company row must not be told it was 'working somewhere else' -- "
        "that label describes a PERSON's employer"
    )
    assert "company" not in d.get("rejected", {})
    assert "company_unconfirmed" not in d


# ── The JS badge, company grid + card ────────────────────────────────────────

def test_the_table_company_cell_flags_an_unconfirmed_domain_too():
    body = _js_function("coCell")
    assert "domain_unconfirmed" in body
    assert "employer_unconfirmed" in body, "must not have regressed the person-side flag"


def test_the_company_card_flags_an_unconfirmed_domain():
    assert "domain_unconfirmed" in _js_function("companyCard")


# ── exclude_keywords: the same "no silent shrink" rule, one filter over ─────
# Every other post-filter on this page (domain, industry) reports how many
# rows it removed. exclude_keywords is a client-side post-filter too (Apollo
# has no native text-exclusion param) and removed rows with no count at all --
# the one filter on the Companies tab that could narrow a page silently.

@patch("tracker.apollo_client.requests.post")
def test_exclude_keywords_reports_how_many_it_removed(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [
            {"id": "o1", "name": "Beta Bionics", "primary_domain": "betabionics.com"},
            {"id": "o2", "name": "Acme Casino", "primary_domain": "acmecasino.com"},
        ],
    })
    meta = {}
    result = ac.search_companies({"exclude_keywords": ["casino"]}, _FAKE_API_KEY, meta=meta)
    assert [o["name"] for o in result] == ["Beta Bionics"]
    assert meta["exclude_keywords_dropped"] == 1


@patch("tracker.apollo_client.requests.post")
def test_exclude_keywords_reports_nothing_when_nothing_was_removed(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [{"id": "o1", "name": "Beta Bionics"}],
    })
    meta = {}
    ac.search_companies({"exclude_keywords": ["casino"]}, _FAKE_API_KEY, meta=meta)
    assert "exclude_keywords_dropped" not in meta


def test_the_route_reports_excluded_keyword_drops(client, monkeypatch):
    def fake_search_companies(filters, api_key, page=1, per_page=25, meta=None, **kw):
        if meta is not None:
            meta["exclude_keywords_dropped"] = 4
        return []

    monkeypatch.setattr(ac, "search_companies", fake_search_companies)
    r = client.post(_SEARCH, json={"entity": "companies",
                                   "filters": {"exclude_keywords": ["casino"]}})
    d = r.get_json()
    assert d["rejected"]["excluded_keyword"] == 4
    assert d["rejected_labels"]["excluded_keyword"] == "matching an excluded keyword"
