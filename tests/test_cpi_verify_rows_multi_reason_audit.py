"""_cpi_verify_rows checked industry, size, revenue, HQ, technology and title
as a fixed if/elif chain, attributing a dropped row to only the FIRST check
it failed. A row that was both the wrong industry and undersized was tallied
under "industry" alone, so `rejected["employees"]` undercounted how many
rows a headcount filter was genuinely responsible for excluding whenever
another check happened to run first for the same row.

Fixed by checking every condition independently and tallying a row under
EVERY reason it fails, not just the first. Row membership in `kept` stays
exactly one-to-one with the input (a row is dropped once, no matter how many
reasons it fails), but `dropped`'s values can now sum to more than the true
number of removed rows -- which is why cpi_search's `rejected_total` is
computed from an actual before/after row count (`verify_dropped_rows`) rather
than `sum(rejected.values())`, and why the JS no longer re-derives its
"Apollo returned N people" total by summing STATE.rejected itself; it reads
the server's own rejected_total instead (see test_cpi_dashboard_behaviour.py,
which exercises this end to end through the real bundle).

The route-level tests use entity="people" rather than "companies" on
purpose: search_companies enforces the industry filter itself before
_cpi_verify_rows ever sees a company row (filter_by_industry, applied inside
apollo_client.py), so an industry-violating company row never reaches this
function's own (redundant, defense-in-depth) industry check at all. People
search has no such upstream industry filter, so _cpi_verify_rows is the
only place industry gets enforced there, which is what makes a genuine
industry+employees overlap observable through the real route.
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
    """A warm _CPI_FIRMO_CACHE entry from an earlier test reusing the same org
    id would answer from memory and never reach this test's own mocked
    Apollo response -- keep every test's org ids cold."""
    monkeypatch.setattr(appmod, "_CPI_FIRMO_CACHE", {})
    monkeypatch.setattr(appmod, "_cpi_firmo_db_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_firmo_db_write", lambda facts: None)


def _mock_response(json_data):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_data
    return m


# ── _cpi_verify_rows itself, company-shaped rows (is_people=False) ─────────
# Field names per _cpi_org_view's company branch: industry / estimated_num_employees.

def test_a_row_failing_two_checks_is_tallied_under_both():
    rows = [{"industry": "Casino", "estimated_num_employees": 3}]
    kept, dropped, _unconf = appmod._cpi_verify_rows(
        rows, {"industries": ["healthcare"], "employee_min": 100}, False)
    assert kept == []
    assert dropped == {"industry": 1, "employees": 1}


def test_a_row_failing_one_check_is_only_tallied_once():
    rows = [{"industry": "Medical Devices", "estimated_num_employees": 3}]
    kept, dropped, _unconf = appmod._cpi_verify_rows(
        rows, {"industries": ["healthcare"], "employee_min": 100}, False)
    assert kept == []
    assert dropped == {"employees": 1}


def test_dropped_values_can_exceed_the_true_removed_row_count():
    """The invariant this fix is built on: len(rows) - len(kept) is still the
    true number of rows removed, even though summing `dropped` now overcounts
    it whenever a row fails more than one check."""
    rows = [{"industry": "Casino", "estimated_num_employees": 3},    # fails both
            {"industry": "Casino", "estimated_num_employees": 300}]  # fails only industry
    kept, dropped, _unconf = appmod._cpi_verify_rows(
        rows, {"industries": ["healthcare"], "employee_min": 100}, False)
    true_dropped = len(rows) - len(kept)
    assert true_dropped == 2
    assert sum(dropped.values()) == 3, "3 checks failed across 2 rows -- expected to exceed true_dropped"
    assert dropped == {"industry": 2, "employees": 1}


def test_a_row_passing_every_check_is_kept():
    rows = [{"industry": "Medical Devices", "estimated_num_employees": 300}]
    kept, dropped, _unconf = appmod._cpi_verify_rows(
        rows, {"industries": ["healthcare"], "employee_min": 100}, False)
    assert kept == rows
    assert dropped == {}


# ── The route (people entity): rejected_total reflects true rows removed ───
# search_companies enforces industry itself before a company row ever reaches
# _cpi_verify_rows (see module docstring), so this scenario is only provably
# observable end-to-end through the People tab, where _cpi_verify_rows is the
# only place industry gets enforced.

def _people_search_response(rows):
    return _mock_response({"people": rows})


def test_the_route_reports_the_true_dropped_count_not_the_inflated_sum(client):
    people = [
        # Both fail industry AND size -- would double-count under the old bug.
        {"id": "p1", "first_name": "A", "last_name": "One",
         "organization": {"id": "o1", "name": "Casino A", "domain": "casinoa.com"}},
        {"id": "p2", "first_name": "B", "last_name": "Two",
         "organization": {"id": "o2", "name": "Casino B", "domain": "casinob.com"}},
    ]
    orgs = [
        {"id": "o1", "name": "Casino A", "industry": "Casino", "estimated_num_employees": 3},
        {"id": "o2", "name": "Casino B", "industry": "Casino", "estimated_num_employees": 3},
    ]

    def fake_post(url, json=None, **kw):
        if "mixed_people" in url:
            return _people_search_response(people)
        return _mock_response({"organizations": orgs})

    with patch("tracker.apollo_client.requests.post", side_effect=fake_post):
        r = client.post(_SEARCH, json={"entity": "people",
                                       "filters": {"industries": ["healthcare"],
                                                  "employee_min": 100}})
    d = r.get_json()
    assert d["results"] == []
    assert d["rejected"] == {"industry": 2, "employees": 2}
    assert d["rejected_total"] == 2, (
        "2 rows were removed, not 4 -- summing rejected's now-overlapping "
        "values would have said 4"
    )


def test_the_route_still_adds_the_right_total_when_reasons_dont_overlap(client):
    people = [
        {"id": "p1", "first_name": "A", "last_name": "One",
         "organization": {"id": "o1", "name": "Casino Co", "domain": "casinoco.com"}},
        {"id": "p2", "first_name": "B", "last_name": "Two",
         "organization": {"id": "o2", "name": "Tiny Health", "domain": "tinyhealth.com"}},
    ]
    orgs = [
        {"id": "o1", "name": "Casino Co", "industry": "Casino", "estimated_num_employees": 300},
        {"id": "o2", "name": "Tiny Health", "industry": "Medical Devices", "estimated_num_employees": 3},
    ]

    def fake_post(url, json=None, **kw):
        if "mixed_people" in url:
            return _people_search_response(people)
        return _mock_response({"organizations": orgs})

    with patch("tracker.apollo_client.requests.post", side_effect=fake_post):
        r = client.post(_SEARCH, json={"entity": "people",
                                       "filters": {"industries": ["healthcare"],
                                                  "employee_min": 100}})
    d = r.get_json()
    assert d["rejected"] == {"industry": 1, "employees": 1}
    assert d["rejected_total"] == 2
