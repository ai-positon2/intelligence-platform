"""Pacing Desk: the refresh and allocation routes (app.py), behind the Position2 login."""
import datetime as dt

import pytest

from tests.test_paid_marketing import SAME, STAFF, client_as
from tracker import paid_pacing_budgets as pb
from tracker import paid_pacing_budget_store as bstore
from tracker import paid_refresh as rf
from tracker import paid_store as store

REFRESH = "/p2/paid-marketing/api/pacing/refresh"
BUDGETS = "/p2/paid-marketing/api/pacing/budgets"
PARSE = BUDGETS + "/parse"


class RunState:
    """paid_store's refresh state, in memory."""

    def __init__(self, built=None):
        self.state, self.built, self.started, self.read_at = {}, built, [], None

    def claim_refresh(self, by, stale_s):
        if self.state.get("state") == "running":
            return False
        self.state = {"state": "running", "by": by, "startedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.started.append(by)
        return True

    def update_refresh(self, **f):
        self.state.update(f)

    def get_refresh(self):
        return dict(self.state)

    def latest_built(self):
        return self.built


@pytest.fixture
def runs(monkeypatch):
    s = RunState(built=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat())
    # the store's cache of Google Ads reads: nothing saved until a test says so
    monkeypatch.setattr(store, "gads_saved_at", lambda key: s.read_at)
    monkeypatch.setattr(store, "get_gads_entry", lambda key, ttl: None)
    for name in ("claim_refresh", "update_refresh", "get_refresh", "latest_built"):
        monkeypatch.setattr(store, name, getattr(s, name))
    monkeypatch.setattr(rf, "config_problem", lambda: None)
    monkeypatch.setattr(rf.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda self: None})())      # the worker never finishes
    return s


# ── Refresh ─────────────────────────────────────────────────────────────────

def test_refresh_needs_the_login_and_the_same_origin(runs):
    assert client_as(None).get(REFRESH).status_code == 302
    assert client_as(None).post(REFRESH, json={}, headers=SAME).status_code == 302
    assert client_as(STAFF).post(REFRESH, json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert runs.started == []


def test_refresh_says_what_google_ads_setup_is_missing(runs, monkeypatch):
    monkeypatch.setattr(rf, "config_problem", lambda: "GOOGLE_ADS_DEVELOPER_TOKEN is not set.")
    r = client_as(STAFF).post(REFRESH, json={}, headers=SAME)
    assert r.status_code == 409 and "GOOGLE_ADS_DEVELOPER_TOKEN" in r.get_json()["error"] and runs.started == []
    assert "serviceEmail" not in r.get_json() and r.get_json()["configProblem"]


def test_a_refresh_starts_then_a_second_is_refused_while_it_runs(runs):
    c = client_as(STAFF)
    r = c.post(REFRESH, json={}, headers=SAME)
    assert r.status_code == 202 and r.get_json()["state"] == "running" and runs.started == [STAFF]
    r = c.post(REFRESH, json={}, headers=SAME)
    assert r.status_code == 409 and "already running" in r.get_json()["error"] and runs.started == [STAFF]
    assert c.get(REFRESH).get_json()["state"] == "running"


def test_a_refresh_right_after_a_read_of_google_is_held_back_with_how_long(runs):
    runs.read_at = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)).isoformat()
    r = client_as(STAFF).post(REFRESH, json={}, headers=SAME)
    j = r.get_json()
    assert r.status_code == 429 and "Try again in 8 minutes" in j["error"] and 470 <= j["retryIn"] <= 480 and runs.started == []
    assert j["cooldownLeft"] == j["retryIn"] or abs(j["cooldownLeft"] - j["retryIn"]) <= 1


def test_changed_allocations_are_not_held_back_by_that_wait(runs, monkeypatch):
    runs.read_at = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)).isoformat()
    c = client_as(STAFF)
    assert c.post(REFRESH, json={"recompute": True}, headers=SAME).status_code == 429          # nothing saved to run again on
    monkeypatch.setattr(rf, "_usable_read", lambda store, now=None: {"readAt": runs.read_at})
    r = c.post(REFRESH, json={"recompute": True}, headers=SAME)
    assert r.status_code == 202 and runs.started == [STAFF]


def test_the_page_starting_a_run_by_itself_is_refused_unless_one_is_due(runs):
    runs.built = dt.datetime.now(dt.timezone.utc).isoformat()
    r = client_as(STAFF).post(REFRESH, json={"auto": True}, headers=SAME)
    assert r.status_code == 429 and runs.started == []
    runs.built = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
    r = client_as(STAFF).post(REFRESH, json={"auto": True}, headers=SAME)
    assert r.status_code == 202 and runs.started == [STAFF]


def test_only_an_explicit_true_counts_as_the_page_asking(runs):
    """Anything else is a person pressing the button, who is never turned away for a run already built today."""
    runs.built = dt.datetime.now(dt.timezone.utc).isoformat()
    for body in ({"auto": "yes"}, {"auto": 1}, {"auto": False}, {}, [], "x"):
        runs.state = {}
        assert client_as(STAFF).post(REFRESH, json=body, headers=SAME).status_code == 202, body
    runs.state = {}
    assert client_as(STAFF).post(REFRESH, json={"auto": True}, headers=SAME).status_code == 429


def test_a_recompute_is_asked_for_only_by_an_explicit_true(runs, monkeypatch):
    seen = []
    monkeypatch.setattr(rf, "start", lambda *a, **k: seen.append((k["auto"], k["recompute"])))
    for body, want in (({"recompute": True}, (False, True)), ({"recompute": "yes"}, (False, False)), ({}, (False, False)),
                       ({"auto": True, "recompute": True}, (True, True)), ([], (False, False))):
        client_as(STAFF).post(REFRESH, json=body, headers=SAME)
        assert seen[-1] == want, body


def test_the_status_says_whether_a_run_is_due(runs):
    j = client_as(STAFF).get(REFRESH).get_json()
    assert j["due"] is True and j["configProblem"] is None and j["cooldownLeft"] == 0
    runs.built = dt.datetime.now(dt.timezone.utc).isoformat()
    assert client_as(STAFF).get(REFRESH).get_json()["due"] is False


# ── Allocations ─────────────────────────────────────────────────────────────

class Rows:
    """pm_pacing_budgets, in memory."""

    def __init__(self):
        self.by = {}
        self.clock = 0

    def list_month(self, m):
        return [dict(r) for r in self.by.get(m, [])]

    def months(self):
        return sorted(m for m, v in self.by.items() if v)

    def replace_month(self, m, rows, by):
        self.clock += 1
        self.by[m] = [dict(r, updatedBy=by, updatedAt="2026-10-12T10:00:%02d+00:00" % self.clock) for r in rows]
        return self.list_month(m)


@pytest.fixture
def book(monkeypatch):
    r = Rows()
    for name in ("list_month", "months", "replace_month"):
        monkeypatch.setattr(bstore, name, getattr(r, name))
    monkeypatch.setattr(store, "latest_date", lambda: None)
    monkeypatch.setattr(store, "get_snapshot", lambda d: None)
    monkeypatch.setattr("app._pacing_google_names", lambda: ["Acme Dental", "Beta Imaging"])
    return r


ROW = {"client": "Acme", "account": "Acme Dental", "allocated": 3000, "targetCpa": 45, "daysRunning": "Monday - Friday", "status": "Active", "am": "Sam"}


def test_the_allocation_routes_need_the_login_and_the_same_origin(book):
    assert client_as(None).get(BUDGETS + "?month=2026-10").status_code == 302
    assert client_as(None).put(BUDGETS + "?month=2026-10", json={"rows": [ROW]}, headers=SAME).status_code == 302
    assert client_as(None).post(PARSE, json={"month": "2026-10", "text": "x"}, headers=SAME).status_code == 302
    assert client_as(STAFF).put(BUDGETS + "?month=2026-10", json={"rows": [ROW]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client_as(STAFF).post(PARSE, json={"month": "2026-10", "text": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert book.by == {}


def test_an_empty_month_is_an_empty_book_with_the_account_names_as_hints(book):
    j = client_as(STAFF).get(BUDGETS + "?month=2026-10").get_json()
    assert j["month"] == "2026-10" and j["rows"] == [] and j["source"] == {"kind": "none"} and j["version"] == "" and j["lastSaved"] is None
    assert j["accounts"] == ["Acme Dental", "Beta Imaging"] and j["months"] == ["2026-10"]


def test_a_save_stores_the_month_and_says_who_and_when(book):
    c = client_as(STAFF)
    r = c.put(BUDGETS + "?month=2026-10", json={"rows": [ROW], "version": ""}, headers=SAME)
    j = r.get_json()
    assert r.status_code == 200 and j["source"] == {"kind": "saved"} and j["rows"][0]["allocated"] == 3000 and j["rows"][0]["updatedBy"] == STAFF
    assert j["lastSaved"]["by"] == STAFF and j["version"] == j["lastSaved"]["at"] and j["months"] == ["2026-10"]
    again = c.get(BUDGETS + "?month=2026-10").get_json()
    assert again["rows"] == j["rows"] and again["version"] == j["version"]


def test_a_new_month_shows_last_months_rows_as_carried_until_someone_saves(book):
    client_as(STAFF).put(BUDGETS + "?month=2026-09", json={"rows": [ROW], "version": ""}, headers=SAME)
    j = client_as(STAFF).get(BUDGETS + "?month=2026-10").get_json()
    assert j["source"] == {"kind": "carried", "from": "2026-09"} and j["rows"][0]["account"] == "Acme Dental" and j["version"] == "" and j["lastSaved"] is None
    assert "updatedBy" not in j["rows"][0]
    assert j["months"] == ["2026-09", "2026-10"]
    assert book.by.get("2026-10") is None                         # looking never saves


def test_a_save_that_started_from_an_older_version_is_refused_and_names_who_saved_since(book):
    first = client_as(STAFF).put(BUDGETS + "?month=2026-10", json={"rows": [ROW], "version": ""}, headers=SAME).get_json()
    other = client_as("ana@position2.com")
    r = other.put(BUDGETS + "?month=2026-10", json={"rows": [dict(ROW, allocated=1)], "version": ""}, headers=SAME)
    assert r.status_code == 409 and r.get_json()["kind"] == "conflict" and STAFF in r.get_json()["error"]
    assert book.list_month("2026-10")[0]["allocated"] == 3000
    ok = other.put(BUDGETS + "?month=2026-10", json={"rows": [dict(ROW, allocated=1)], "version": first["version"]}, headers=SAME)
    assert ok.status_code == 200 and book.list_month("2026-10")[0]["allocated"] == 1


@pytest.mark.parametrize("body,why", [
    ({"rows": []}, "at least one account"), ({"rows": "x"}, "should be a list"), ({}, "should be a list"), ([], "JSON object"),
    ({"rows": [dict(ROW, allocated="lots")]}, "Acme Dental: allocated budget should be a number"),
    ({"rows": [ROW, dict(ROW, account="acme dental")]}, "listed twice"),
])
def test_a_bad_save_says_what_to_fix_and_stores_nothing(book, body, why):
    r = client_as(STAFF).put(BUDGETS + "?month=2026-10", json=dict(body, version="") if isinstance(body, dict) else body, headers=SAME)
    assert r.status_code == 400 and why in r.get_json()["error"] and book.by == {}


def test_a_bad_month_is_refused(book):
    for m in ("October", "2026-13", "2026-1"):
        assert client_as(STAFF).get(BUDGETS + "?month=" + m).status_code == 400


def test_the_default_month_is_the_current_pacific_month(book):
    j = client_as(STAFF).get(BUDGETS).get_json()
    assert j["month"] == pb.month_of(rf.pacific_day())


def test_pasted_cells_are_read_for_review_and_nothing_is_saved(book):
    text = "Client\tAccount\tAllocated Budget\tDate\tChannels\nAcme\tAcme Dental\t$3,000\t10/1/2026\tGoogle\nAcme\tAcme Dental\t900\t10/1/2026\tBing"
    r = client_as(STAFF).post(PARSE, json={"month": "2026-10", "text": text}, headers=SAME)
    j = r.get_json()
    assert r.status_code == 200 and j["rows"][0]["allocated"] == 3000 and j["skipped"][0]["reason"] == "Bing budget, not Google Ads or Meta Ads" and book.by == {}


@pytest.mark.parametrize("body,why", [
    ({"month": "2026-10", "text": ""}, "nothing was pasted"), ({"month": "2026-10", "text": 5}, "pasted cells"), ({"month": "x", "text": "a"}, "month should look like"),
    ([], "pasted cells"), ({"text": "Client\tAccount\tAllocated Budget\nA\tB\t1"}, "month should look like"),
])
def test_a_bad_paste_says_why(book, body, why):
    r = client_as(STAFF).post(PARSE, json=body, headers=SAME)
    assert r.status_code == 400 and why in r.get_json()["error"]


def test_the_store_being_down_is_a_clean_error_not_a_crash(book, monkeypatch):
    def down(*a, **k):
        raise store.StoreUnavailable("DATABASE_URL is not set")
    monkeypatch.setattr(bstore, "list_month", down)
    r = client_as(STAFF).get(BUDGETS + "?month=2026-10")
    assert r.status_code == 503 and "unavailable" in r.get_json()["error"]
