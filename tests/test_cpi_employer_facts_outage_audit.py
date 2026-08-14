"""_cpi_attach_employer_facts' paid lookup (mixed_companies/search, called with
up to 50 missing organization_ids at once) called search_companies without
strict=True. On a transport failure -- a timeout, a rate limit, a 5xx -- the
default swallows that and returns [], which this function could not tell
apart from "Apollo genuinely has nothing on these companies": both left every
row's employer fields empty.

_cpi_verify_rows then read those same empty fields as a fact about the
company, not about the request: a people search filtering by industry,
headcount, revenue, HQ or technology dropped every row whose employer lookup
happened to fail, and reported it under a specific, false reason --
"outside the industry", "outside the size range", "headquartered elsewhere",
"not using the technology" -- for a question that was never actually
answered. Exactly the same defect class as domain_unconfirmed one level up
the call stack (see test_cpi_domain_unconfirmed_audit.py), just at the
employer-facts-attach step instead of the domain-match step.

Fixed the same way: the fetch is strict now, so a transport failure raises
instead of silently returning []; _cpi_attach_employer_facts distinguishes
that ("the fetch itself never came back", stats["fetch_failed_ids"]) from a
bug elsewhere in its own processing loop (orgs came back, something after it
broke -- not a fetch failure). cpi_search flags the affected rows
`employer_lookup_failed`, and _cpi_verify_rows skips every check that needs
employer data for a flagged row, counting it separately instead of dropping
it under a reason nothing checked. The title check does not depend on
employer facts and still runs for these rows.
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

_SEARCH = "/p2/b2b-agents/company-people-intelligence/search"


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


@pytest.fixture(autouse=True)
def apollo_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def no_firmo_cache(monkeypatch):
    """A warm cache would answer from memory/DB and never reach the mocked
    Apollo call this file is testing -- keep every test's org id cold."""
    monkeypatch.setattr(appmod, "_CPI_FIRMO_CACHE", {})
    monkeypatch.setattr(appmod, "_cpi_firmo_db_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_firmo_db_write", lambda facts: None)


# ── _cpi_attach_employer_facts itself ────────────────────────────────────────

def test_a_transport_failure_is_reported_as_fetch_failed_not_silently_swallowed(monkeypatch):
    def boom(*a, **kw):
        raise ac.requests.HTTPError("Apollo did not answer")
    monkeypatch.setattr(ac, "search_companies", boom)

    rows = [{"organization_id": "org1", "full_name": "Sean Saint"}]
    spend = {"credits": 0}
    stats = appmod._cpi_attach_employer_facts(rows, "key", spend)

    assert stats["fetch_failed_ids"] == ["org1"]
    assert stats["fetched"] == 0
    assert spend["credits"] == 0, "an outage must never be billed"


def test_a_bug_in_the_processing_loop_is_not_mistaken_for_a_fetch_failure(monkeypatch):
    """orgs DID come back -- Apollo answered. Something breaking afterward is a
    different problem and must not tell a row "the lookup failed" when it
    didn't; that would spuriously mark real answers as unconfirmed."""
    monkeypatch.setattr(ac, "search_companies",
                        lambda *a, **kw: [{"id": "org1", "name": "Beta Bionics"}])
    monkeypatch.setattr(appmod, "_cpi_employer_facts",
                        lambda o: (_ for _ in ()).throw(ValueError("boom")))

    rows = [{"organization_id": "org1", "full_name": "Sean Saint"}]
    stats = appmod._cpi_attach_employer_facts(rows, "key", {"credits": 0})

    assert "fetch_failed_ids" not in stats


def test_a_clean_fetch_still_works_normally(monkeypatch):
    monkeypatch.setattr(ac, "search_companies", lambda *a, **kw: [
        {"id": "org1", "name": "Beta Bionics", "industry": "Medical Devices",
         "estimated_num_employees": 300},
    ])
    monkeypatch.setattr(appmod, "_cpi_record_industries", lambda orgs: None)
    monkeypatch.setattr(appmod, "_cpi_record_vocab", lambda orgs: None)

    rows = [{"organization_id": "org1", "full_name": "Sean Saint"}]
    spend = {"credits": 0}
    stats = appmod._cpi_attach_employer_facts(rows, "key", spend)

    assert "fetch_failed_ids" not in stats
    assert stats["fetched"] == 1
    assert rows[0]["organization_industry"] == "Medical Devices"
    assert spend["credits"] == 1


# ── _cpi_verify_rows: the row must be kept, not rejected under a false reason ─

def test_a_row_flagged_employer_lookup_failed_is_not_dropped_for_industry():
    rows = [{"organization_id": "org1", "employer_lookup_failed": True,
            "organization_industry": None}]
    kept, dropped, unconfirmed = appmod._cpi_verify_rows(
        rows, {"industries": ["healthcare"]}, True)
    assert kept == rows
    assert dropped == {}
    assert unconfirmed == 1


def test_a_row_flagged_employer_lookup_failed_is_not_dropped_for_size_or_hq_or_tech():
    rows = [{"organization_id": "org1", "employer_lookup_failed": True}]
    kept, dropped, unconfirmed = appmod._cpi_verify_rows(
        rows, {"employee_min": 100, "company_locations": ["Boston, MA"],
              "technologies": ["salesforce"]}, True)
    assert kept == rows
    assert dropped == {}
    assert unconfirmed == 1


def test_the_title_check_still_runs_for_an_employer_lookup_failed_row():
    """Title comes off the person's own record, not the failed employer
    lookup -- a genuinely wrong title must still be caught."""
    rows = [{"organization_id": "org1", "employer_lookup_failed": True,
            "title": "Barista"}]
    kept, dropped, unconfirmed = appmod._cpi_verify_rows(
        rows, {"titles": ["Chief Executive Officer"],
              "include_similar_titles": False}, True)
    assert kept == []
    assert dropped == {"title": 1}


def test_a_flagged_row_is_not_counted_unconfirmed_when_no_employer_filter_was_active():
    """The flag only matters when something depends on the missing data --
    a plain browse with no industry/size/hq/tech filter has nothing to skip."""
    rows = [{"organization_id": "org1", "employer_lookup_failed": True}]
    kept, dropped, unconfirmed = appmod._cpi_verify_rows(rows, {}, True)
    assert kept == rows
    assert unconfirmed == 0


def test_a_confirmed_row_and_a_lookup_failed_row_are_told_apart():
    rows = [
        {"organization_id": "org1", "employer_lookup_failed": True},
        {"organization_id": "org2", "organization_employees": 5},  # genuinely too small
    ]
    kept, dropped, unconfirmed = appmod._cpi_verify_rows(rows, {"employee_min": 100}, True)
    assert kept == [rows[0]]
    assert dropped == {"employees": 1}
    assert unconfirmed == 1


def test_company_entity_rows_never_set_the_flag_so_unconfirmed_is_always_zero():
    rows = [{"industry": "Medical Devices", "employees": 300}]
    kept, dropped, unconfirmed = appmod._cpi_verify_rows(
        rows, {"industries": ["healthcare"]}, False)
    assert unconfirmed == 0


# ── The route ─────────────────────────────────────────────────────────────

def _mock_response(json_data):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_data
    return m


def test_the_route_keeps_the_row_and_reports_it_unconfirmed_not_rejected(client, monkeypatch):
    @patch("tracker.apollo_client.requests.post")
    def run(mock_post):
        # search_people succeeds with one row whose employer lookup will then fail.
        def fake_post(url, json=None, **kw):
            if "mixed_people" in url:
                return _mock_response({"people": [
                    {"id": "p1", "first_name": "Sean", "last_name": "Saint",
                     "organization": {"id": "org1", "name": "Beta Bionics"}},
                ]})
            raise ac.requests.HTTPError("Apollo did not answer")
        mock_post.side_effect = fake_post
        return client.post(_SEARCH, json={"entity": "people",
                                          "filters": {"employee_min": 100}})
    r = run()
    d = r.get_json()
    assert len(d["results"]) == 1, "the row must be kept, not dropped"
    assert d["results"][0]["employer_lookup_failed"] is True
    assert "rejected" not in d, "an outage must not read as a rejection"
    assert d["company_unconfirmed"] == 1


def test_the_route_still_rejects_a_genuinely_undersized_company(client, monkeypatch):
    @patch("tracker.apollo_client.requests.post")
    def run(mock_post):
        def fake_post(url, json=None, **kw):
            if "mixed_people" in url:
                return _mock_response({"people": [
                    {"id": "p1", "first_name": "Small", "last_name": "Co",
                     "organization": {"id": "org1", "name": "Tiny LLC"}},
                ]})
            return _mock_response({"organizations": [
                {"id": "org1", "name": "Tiny LLC", "estimated_num_employees": 3},
            ]})
        mock_post.side_effect = fake_post
        return client.post(_SEARCH, json={"entity": "people",
                                          "filters": {"employee_min": 100}})
    r = run()
    d = r.get_json()
    assert d["results"] == []
    assert d["rejected"]["employees"] == 1
    assert "company_unconfirmed" not in d


# ── The JS badge ─────────────────────────────────────────────────────────────

_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "js", "company_people_intelligence.js")


def _js_function(name):
    body = open(_JS, encoding="utf-8").read()
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


def test_the_table_company_cell_flags_a_failed_employer_lookup():
    body = _js_function("coCell")
    assert "employer_lookup_failed" in body
    assert "domain_unconfirmed" in body, "must not have regressed the earlier flags"
    assert "employer_unconfirmed" in body


def test_the_card_person_row_flags_a_failed_employer_lookup():
    assert "employer_lookup_failed" in _js_function("personCard")
