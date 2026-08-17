"""_fetch_job_change_tracked_data() + /p2/b2b-agents/job-change-alert/tracked.

Header-name-driven column mapping (not fixed column letters) because the
"Contact List (Being Monitored)" / "Tracked Companies" Google Sheet tabs get
columns reordered/added by teammates routinely -- fixtures below deliberately
scramble column order from what the real sheet currently uses to pin that
robustness, matching the existing _chatbot_get_anonymous_visitors convention.
"""

import gzip as gzip_mod
import json
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

CONTACT_RANGE = "'Contact List (Being Monitored)'!A5:BZ2000"
COMPANY_RANGE = "'Tracked Companies'!A1:BZ2000"


class _Exec:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return {"values": self._rows}


class FakeSheetsService:
    """Mimics the googleapiclient chained call, keyed by exact range string."""

    def __init__(self, range_to_rows):
        self._range_to_rows = range_to_rows
        self.calls = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId, range):  # noqa: A002 - matches google API's own kwarg name
        self.calls.append(range)
        return _Exec(self._range_to_rows.get(range, []))


# Column order deliberately scrambled from the real sheet to prove the fetch
# is header-driven, not position-driven.
CONTACT_ROWS = [
    ["Company Name", "First Name", "Last Name", "Title", "Seniority", "Departments",
     "Industry", "# Employees", "City", "State", "Person Linkedin Url",
     "Company Linkedin Url", "Website"],
    ["Spring Care Inc", "Dillon", "Mullaney", "Head of Payer Partnerships", "Head",
     "Partnerships", "mental health care", "2600", "New York", "New York",
     "http://www.linkedin.com/in/dmullaney", "http://www.linkedin.com/company/springcare",
     "https://springcare.com"],
    # A row with no name at all must be skipped (matches the "no name -> drop" convention).
    ["Ghost Co", "", "", "Nobody", "", "", "", "", "", "", "", "", ""],
]

COMPANY_ROWS = [
    ["Industry", "Company Name", "# Employees", "Website", "Company Linkedin Url",
     "City", "State", "Country", "Total Funding", "Annual Revenue", "Latest Funding"],
    ["mental health care", "Spring Care Inc", "2600", "https://springcare.com",
     "http://www.linkedin.com/company/springcare", "New York", "New York",
     "United States", "59000000", "106000000", "Series B"],
    # A row with no company name must be skipped.
    ["fintech", "", "10", "", "", "", "", "", "", "", ""],
]


def _reset_all_job_change_tracked_state():
    appmod._JOB_CHANGE_TRACKED_CACHE["data"] = None
    appmod._JOB_CHANGE_TRACKED_CACHE["ts"] = 0.0
    appmod._JOB_CHANGE_TRACKED_GZ["ts"] = None
    appmod._JOB_CHANGE_TRACKED_GZ["raw"] = b""
    appmod._JOB_CHANGE_TRACKED_GZ["gz"] = b""


@pytest.fixture(autouse=True)
def _reset_cache():
    _reset_all_job_change_tracked_state()
    yield
    _reset_all_job_change_tracked_state()


def _client(email="reporting@position2.com"):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_maps_reordered_columns_by_header_name(monkeypatch):
    fake = FakeSheetsService({CONTACT_RANGE: CONTACT_ROWS, COMPANY_RANGE: COMPANY_ROWS})
    monkeypatch.setattr(appmod, "_sheets_service", lambda: fake)

    data = appmod._fetch_job_change_tracked_data(force=True)

    assert data["totals"] == {"contacts": 1, "companies": 1}
    contact = data["contacts"][0]
    assert contact["name"] == "Dillon Mullaney"
    assert contact["title"] == "Head of Payer Partnerships"
    assert contact["company"] == "Spring Care Inc"
    assert contact["seniority"] == "Head"
    assert contact["city"] == "New York"
    assert contact["linkedin_url"] == "http://www.linkedin.com/in/dmullaney"

    company = data["companies"][0]
    assert company["name"] == "Spring Care Inc"
    assert company["industry"] == "mental health care"
    assert company["total_funding"] == "59000000"


def test_rows_with_no_name_are_dropped(monkeypatch):
    fake = FakeSheetsService({CONTACT_RANGE: CONTACT_ROWS, COMPANY_RANGE: COMPANY_ROWS})
    monkeypatch.setattr(appmod, "_sheets_service", lambda: fake)

    data = appmod._fetch_job_change_tracked_data(force=True)

    assert len(data["contacts"]) == 1
    assert len(data["companies"]) == 1


def test_falls_back_to_committed_snapshot_when_sheets_service_fails(monkeypatch):
    """The live sheet is currently unreachable (blocked by a Workspace
    external-sharing policy -- see the project_job_change_alert memory), so
    this is the realistic production path, not just a theoretical one."""
    def boom():
        raise RuntimeError("GOOGLE_SA_JSON env var not set")
    monkeypatch.setattr(appmod, "_sheets_service", boom)

    data = appmod._fetch_job_change_tracked_data(force=True)

    assert data["totals"] == {"contacts": 673, "companies": 274}
    assert data["fetched_at"] is not None
    assert any(c["name"] == "Aaron Roose" for c in data["contacts"])
    assert any(c["name"] == "314e Corporation" for c in data["companies"])


def test_degrades_to_empty_lists_when_sheets_and_snapshot_both_fail(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("GOOGLE_SA_JSON env var not set")
    monkeypatch.setattr(appmod, "_sheets_service", boom)
    monkeypatch.setattr(appmod, "JOB_CHANGE_TRACKED_SNAPSHOT_PATH", tmp_path / "does-not-exist.json")

    data = appmod._fetch_job_change_tracked_data(force=True)

    assert data == {
        "contacts": [], "companies": [],
        "totals": {"contacts": 0, "companies": 0}, "fetched_at": None,
    }


def test_snapshot_fallback_is_independent_per_list(monkeypatch):
    """If contacts come back live but companies don't (or vice versa), only
    the empty side should fall back -- the two lists shouldn't be coupled."""
    fake = FakeSheetsService({CONTACT_RANGE: CONTACT_ROWS, COMPANY_RANGE: []})
    monkeypatch.setattr(appmod, "_sheets_service", lambda: fake)

    data = appmod._fetch_job_change_tracked_data(force=True)

    assert data["totals"]["contacts"] == 1  # from the live (mocked) sheet, not the snapshot
    assert data["contacts"][0]["name"] == "Dillon Mullaney"
    assert data["totals"]["companies"] == 274  # fell back to the snapshot


def test_second_call_within_ttl_is_served_from_cache(monkeypatch):
    fake = FakeSheetsService({CONTACT_RANGE: CONTACT_ROWS, COMPANY_RANGE: COMPANY_ROWS})
    monkeypatch.setattr(appmod, "_sheets_service", lambda: fake)

    appmod._fetch_job_change_tracked_data(force=True)
    calls_after_first = len(fake.calls)
    appmod._fetch_job_change_tracked_data(force=False)

    assert len(fake.calls) == calls_after_first, "cached call should not re-hit the Sheets API"


def test_tracked_route_requires_position2_auth():
    c = appmod.app.test_client()
    resp = c.get("/p2/b2b-agents/job-change-alert/tracked")
    assert resp.status_code in (302, 401, 403)


def test_tracked_route_returns_fetched_data_as_json(monkeypatch):
    canned = {
        "contacts": [{"name": "Dillon Mullaney"}],
        "companies": [{"name": "Spring Care Inc"}],
        "totals": {"contacts": 1, "companies": 1},
        "fetched_at": "2026-08-17T00:00:00+00:00",
    }
    monkeypatch.setattr(appmod, "_fetch_job_change_tracked_data", lambda force=False: canned)

    resp = _client().get("/p2/b2b-agents/job-change-alert/tracked")

    assert resp.status_code == 200
    assert resp.get_json() == canned


def test_tracked_route_gzips_when_client_accepts_it(monkeypatch):
    canned = {"contacts": [], "companies": [], "totals": {"contacts": 0, "companies": 0}, "fetched_at": None}
    monkeypatch.setattr(appmod, "_fetch_job_change_tracked_data", lambda force=False: canned)

    resp = _client().get("/p2/b2b-agents/job-change-alert/tracked",
                          headers={"Accept-Encoding": "gzip"})

    assert resp.headers.get("Content-Encoding") == "gzip"
    assert json.loads(gzip_mod.decompress(resp.data)) == canned
