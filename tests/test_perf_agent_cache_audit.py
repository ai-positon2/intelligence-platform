"""Performance audit: _agent_run_counts and _agent_access_requests_raw read the
whole 'Agent Runs' / 'Agent Access Requests' tabs fresh, with no caching, on
literally every /app, agent-detail, and client-portal page render -- for every
signed-in user, on every navigation. Both are now backed by a 30s TTL cache
(_agent_run_rows / _agent_access_request_rows) shared across all callers, so a
page load hits a Sheets API round-trip at most once every 30 seconds instead
of once per request.

A run/request logged in this process is also appended straight into the cache
(not just written to the sheet) so the request that just logged it sees the
updated count immediately, without waiting out the TTL.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


class FakeValues:
    """Counts only reads of `bulk_range` (the whole-tab read _agent_run_rows /
    _agent_access_request_rows does) -- the log-write functions also do a
    cheap single-cell header-existence check on every call, before and after
    this fix, which isn't what these tests are measuring."""

    def __init__(self, rows, counter, bulk_range):
        self._rows = rows
        self._counter = counter
        self._bulk_range = bulk_range

    def get(self, spreadsheetId=None, range=None):
        if range == self._bulk_range:
            self._counter["reads"] += 1
        return self

    def append(self, **kw):
        return self

    def update(self, **kw):
        return self

    def execute(self):
        return {"values": list(self._rows)}


class FakeSvc:
    def __init__(self, rows, counter, bulk_range):
        self._values = FakeValues(rows, counter, bulk_range)

    def spreadsheets(self):
        return self

    def values(self):
        return self._values

    def batchUpdate(self, **kw):
        return self._values


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    """Every test starts cold -- a warm cache from an earlier test would answer
    from memory and never reach the fake service being set up here."""
    monkeypatch.setattr(appmod, "_AGENT_RUN_ROWS_CACHE", {"rows": None, "ts": 0.0})
    monkeypatch.setattr(appmod, "_AGENT_ACCESS_REQUEST_ROWS_CACHE", {"rows": None, "ts": 0.0})
    monkeypatch.setattr(appmod, "LOGIN_LOG_SHEET_ID", "fake-sheet-id")


# ── _agent_run_counts / _agent_run_rows ──────────────────────────────────────

_AR_RANGE = "%s!A:F" % appmod._AR_TAB


def test_a_second_call_within_the_ttl_does_not_re_read_the_sheet(monkeypatch):
    counter = {"reads": 0}
    rows = [appmod._AR_HEADER, ["t", "d", "a@x.com", "A", "seo", "SEO Agent"]]
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: FakeSvc(rows, counter, _AR_RANGE))

    first = appmod._agent_run_counts("a@x.com")
    second = appmod._agent_run_counts("a@x.com")

    assert first == {"seo": 1}
    assert second == {"seo": 1}
    assert counter["reads"] == 1, "second call within the TTL must be served from cache"


def test_a_different_users_count_is_still_correct_from_the_same_cached_read(monkeypatch):
    """One shared cache of raw rows, filtered per email in Python -- not a
    per-user cache -- so two different users hitting /app back to back still
    only cost one Sheets round-trip between them, not one each."""
    counter = {"reads": 0}
    rows = [appmod._AR_HEADER,
            ["t", "d", "a@x.com", "A", "seo", "SEO Agent"],
            ["t", "d", "b@x.com", "B", "seo", "SEO Agent"],
            ["t", "d", "b@x.com", "B", "seo", "SEO Agent"]]
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: FakeSvc(rows, counter, _AR_RANGE))

    assert appmod._agent_run_counts("a@x.com") == {"seo": 1}
    assert appmod._agent_run_counts("b@x.com") == {"seo": 2}
    assert counter["reads"] == 1


def test_after_the_ttl_expires_the_sheet_is_read_again(monkeypatch):
    counter = {"reads": 0}
    rows = [appmod._AR_HEADER, ["t", "d", "a@x.com", "A", "seo", "SEO Agent"]]
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: FakeSvc(rows, counter, _AR_RANGE))

    appmod._agent_run_counts("a@x.com")
    appmod._AGENT_RUN_ROWS_CACHE["ts"] -= (appmod._AGENT_RUN_ROWS_CACHE_TTL + 1)
    appmod._agent_run_counts("a@x.com")

    assert counter["reads"] == 2


def test_logging_a_run_updates_the_cache_without_waiting_for_the_ttl(monkeypatch):
    """The user who just ran an agent must see it reflected on the very page
    this request renders next -- not stale for up to 30s."""
    counter = {"reads": 0}
    rows = [appmod._AR_HEADER]
    svc = FakeSvc(rows, counter, _AR_RANGE)
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: svc)
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: svc)

    assert appmod._agent_run_counts("a@x.com") == {}
    assert counter["reads"] == 1

    appmod._log_agent_run({"email": "a@x.com", "name": "A"}, {"slug": "seo", "name": "SEO Agent"})
    counts = appmod._agent_run_counts("a@x.com")

    assert counts == {"seo": 1}
    assert counter["reads"] == 1, "the logged run must come from the in-memory cache, not a re-read"


# ── _agent_access_requests_raw / _agent_access_request_rows ─────────────────

_AAR_RANGE = "%s!A:G" % appmod._AAR_TAB


def test_access_requests_are_cached_the_same_way(monkeypatch):
    counter = {"reads": 0}
    rows = [appmod._AAR_HEADER,
            ["t", "d", "a@x.com", "A", "seo", "SEO Agent", "please"]]
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: FakeSvc(rows, counter, _AAR_RANGE))

    first = appmod._agent_access_requested_slugs("a@x.com")
    second = appmod._agent_access_requested_slugs("a@x.com")

    assert first == {"seo"}
    assert second == {"seo"}
    assert counter["reads"] == 1


def test_logging_an_access_request_updates_the_cache_immediately(monkeypatch):
    counter = {"reads": 0}
    rows = [appmod._AAR_HEADER]
    svc = FakeSvc(rows, counter, _AAR_RANGE)
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: svc)
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: svc)

    assert appmod._agent_access_requested_slugs("a@x.com") == set()
    ok = appmod._log_agent_access_request({"email": "a@x.com", "name": "A"},
                                           {"slug": "seo", "name": "SEO Agent"})
    assert ok is True

    requested = appmod._agent_access_requested_slugs("a@x.com")
    assert requested == {"seo"}, "'Request sent' must show right away, not after the TTL"
    assert counter["reads"] == 1
