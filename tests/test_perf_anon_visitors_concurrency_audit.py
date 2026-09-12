"""Performance audit: _fetch_anon_visitors_data (the Anonymous Visitors agent's
data source) rebuilt a Google Sheets client from scratch for each of its two tab
reads, then read "People Enriched" and "Visitors By Company" one after another --
two full client-construction costs plus two serial network round-trips on every
cache-miss request (the cache TTL is 5 minutes, so this hits often under normal
traffic).

Fixed the same way as the Visitor Analytics dashboard's equivalent bug
(test_perf_visitor_analytics_concurrency_audit.py): the two range reads now run
concurrently, each with its own client instance (built via the module-level
_anon_sheets_service(), cheap now that static_discovery=True means no
discovery-document network call).
"""

import os
import sys
import threading
import time

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(appmod, "_ANON_CACHE", {"data": None, "ts": 0.0})


def test_the_two_tab_reads_overlap_instead_of_running_one_after_another(monkeypatch):
    barrier = threading.Barrier(2, timeout=5.0)
    broke = {"v": False}

    class FakeValues:
        def get(self, spreadsheetId=None, range=None):
            return self

        def execute(self):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                broke["v"] = True
            time.sleep(0.05)
            return {"values": []}

    class FakeSvc:
        def spreadsheets(self):
            return self

        def values(self):
            return FakeValues()

    monkeypatch.setattr(appmod, "_anon_sheets_service", lambda: FakeSvc())

    started = time.time()
    result = appmod._fetch_anon_visitors_data()
    elapsed = time.time() - started

    assert not broke["v"], "the two reads never actually overlapped"
    assert elapsed < 1.0, "reads took as long as if they ran one after another"
    assert result["people_table"] == []
    assert result["company_table"] == []


def test_each_concurrent_read_gets_its_own_service_instance(monkeypatch):
    """httplib2 (the client's transport) is not thread-safe -- sharing one
    service object across the two concurrent reads would be a race, not a
    speedup. Each call to _fetch_anon_tab must build its own."""
    calls = {"n": 0}

    class FakeValues:
        def get(self, spreadsheetId=None, range=None):
            return self

        def execute(self):
            return {"values": []}

    class FakeSvc:
        def spreadsheets(self):
            return self

        def values(self):
            return FakeValues()

    def make_service():
        calls["n"] += 1
        return FakeSvc()

    monkeypatch.setattr(appmod, "_anon_sheets_service", make_service)

    appmod._fetch_anon_visitors_data()

    assert calls["n"] == 2, "expected one service instance per concurrent read"


def test_a_cached_result_within_the_ttl_does_not_re_fetch(monkeypatch):
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise AssertionError("should not build a client on a cache hit")

    monkeypatch.setattr(appmod, "_anon_sheets_service", boom)
    monkeypatch.setattr(appmod, "_ANON_CACHE", {
        "data": {"people_table": [], "company_table": [], "total_people": 0,
                 "unique_companies": 0, "top_industries": []},
        "ts": time.time(),
    })

    appmod._fetch_anon_visitors_data()

    assert calls["n"] == 0


def test_a_missing_service_account_returns_empty_rows_without_raising(monkeypatch):
    monkeypatch.setattr(appmod, "_anon_sheets_service", lambda: None)

    result = appmod._fetch_anon_visitors_data()

    assert result["people_table"] == []
    assert result["company_table"] == []
