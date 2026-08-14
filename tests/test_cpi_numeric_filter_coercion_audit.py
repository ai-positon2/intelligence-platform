"""cpi_search's numeric range filters (employee_min/max, revenue_min/max,
founded_min/max, ...) reached _cpi_num_in_range and _employee_ranges_for
without ever being cast. The shipped filter panel's numVal() always sends a
JS number, so this never fired from the UI -- but the route has no schema
validation of its own, and a string value (e.g. "500" instead of 500) crashed
deep in the comparison chain with a bare TypeError:

    _cpi_num_in_range(300, '201', 500)
      -> TypeError: '<' not supported between instances of 'int' and 'str'

cpi_search's own except-Exception-as-e caught that and reported it exactly
like an Apollo outage: "Apollo did not answer this search, so nothing was
found and nothing was ruled out. Try again in a moment." -- advice that
cannot help, since the same malformed request fails identically every retry.
A validation bug wearing the costume of a transient failure is the same
defect class this whole page keeps needing fixed, just triggered by the
caller's own input instead of Apollo's.

Fixed by casting every numeric filter key through _cpi_int_or_none (already
used for the same purpose on the chat's intent-parser output) before it
reaches either _cpi_verify_rows or apollo_client.
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


def _mock_response(json_data):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = json_data
    return m


def test_a_string_employee_bound_no_longer_crashes_the_search(client):
    with patch("tracker.apollo_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response({"organizations": [
            {"id": "o1", "name": "Beta Bionics", "estimated_num_employees": 300},
        ]})
        r = client.post(_SEARCH, json={"entity": "companies",
                                       "filters": {"employee_min": 1, "employee_max": "500"}})
    d = r.get_json()
    assert d.get("search_failed") is not True, d
    assert [c["name"] for c in d["results"]] == ["Beta Bionics"]


def test_a_string_bound_that_would_exclude_the_row_still_excludes_it(client):
    """Coercion must not just avoid the crash -- the numeric value has to keep
    meaning what it meant, or a real filter silently stops filtering."""
    with patch("tracker.apollo_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response({"organizations": [
            {"id": "o1", "name": "Tiny LLC", "estimated_num_employees": 3},
        ]})
        r = client.post(_SEARCH, json={"entity": "companies",
                                       "filters": {"employee_min": "100"}})
    d = r.get_json()
    assert d["results"] == []
    assert d["rejected"]["employees"] == 1


def test_a_garbage_numeric_value_is_dropped_rather_than_crashing(client):
    with patch("tracker.apollo_client.requests.post") as mock_post:
        mock_post.return_value = _mock_response({"organizations": [
            {"id": "o1", "name": "Beta Bionics", "estimated_num_employees": 300},
        ]})
        r = client.post(_SEARCH, json={"entity": "companies",
                                       "filters": {"employee_min": "not-a-number"}})
    d = r.get_json()
    assert d.get("search_failed") is not True, d


def test_cpi_int_or_none_still_used_directly_covers_the_comma_and_float_cases():
    """Not a route test -- confirms the shared helper this fix reuses still
    handles the shapes an LLM or a hand-typed API call could plausibly send."""
    assert appmod._cpi_int_or_none("1,000") == 1000
    assert appmod._cpi_int_or_none(200.0) == 200
    assert appmod._cpi_int_or_none("garbage") is None
    assert appmod._cpi_int_or_none(None) is None
