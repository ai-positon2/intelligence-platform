"""Performance audit: _fetch_visitor_analytics_uncached (the Anonymous Traffic
dashboard's cache-miss path) issued its five Sheets reads -- Visitor Analytics,
Member Signins, the internal login log, Visitor Identities, Page Views -- one
after another, and called _read_access_requests() TWICE (once directly for
`conversions`, again inside _va_identity_map()) even though both wanted the
exact same data. That's six-to-seven serial network round-trips on every
cache-miss request, for a dashboard whose sibling (_fetch_member_analytics_uncached)
had already been fixed to issue its equivalent reads concurrently.

Fixed the same way: every read now runs in one ThreadPoolExecutor, and
_read_access_requests() is called exactly once, with the result threaded into
both _va_identity_map() and the `conversions` count instead of each fetching
its own copy.
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
def sheet_configured(monkeypatch):
    monkeypatch.setattr(appmod, "LOGIN_LOG_SHEET_ID", "fake-sheet-id")
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: object())  # just the truthy probe
    monkeypatch.setattr(appmod, "_resolve_ips_bulk", lambda pool: None)
    monkeypatch.setattr(appmod, "_VI_OK", False)


READS = 6          # 5 sheet ranges + access requests
BARRIER_WAIT = 5.0  # generous; a serial implementation fails here, not hangs


class _ConcurrencyTracker:
    """Counts overlap, and FORCES it to be observable.

    The earlier version only counted, and asserted the peak reached 4. That
    is not a property of the code under test. ThreadPoolExecutor grows its
    pool lazily: `_adjust_thread_count` skips spawning whenever a worker is
    already idle, so on a loaded machine several 0.05s reads finish before
    later threads exist and the peak lands at 3 with the reads still issued
    concurrently. The assertion measured how fast threads spun up.

    The barrier removes the timing question. No read can finish until all six
    have arrived, so no worker goes idle, every submit spawns its thread, and
    the peak is six or the reads were not concurrent. A serial implementation
    blocks the first read until the barrier times out and can never reach six.
    """

    def __init__(self, parties=READS):
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(parties)
        self.in_flight = 0
        self.max_in_flight = 0
        self.broke = False

    def enter(self):
        with self.lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.barrier.wait(timeout=BARRIER_WAIT)
        except threading.BrokenBarrierError:
            # Everything that reaches here after the first timeout gets the
            # broken barrier immediately, which is what keeps a serial run
            # from taking parties x BARRIER_WAIT seconds to fail.
            self.broke = True

    def exit(self):
        with self.lock:
            self.in_flight -= 1


def test_the_five_sheet_reads_overlap_instead_of_running_one_after_another(monkeypatch):
    tracker = _ConcurrencyTracker()

    class SlowFakeSvc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId=None, range=None):
            return self

        def execute(self):
            tracker.enter()
            time.sleep(0.05)
            tracker.exit()
            return {"values": []}

    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: SlowFakeSvc())

    def slow_access_requests(limit=300):
        tracker.enter()
        time.sleep(0.05)
        tracker.exit()
        return []

    monkeypatch.setattr(appmod, "_read_access_requests", slow_access_requests)

    started = time.time()
    appmod._fetch_visitor_analytics_uncached()
    elapsed = time.time() - started

    # The barrier is the real assertion: all six reads were in flight at the
    # same instant, or they were not concurrent. Checked on the tracker rather
    # than left to an exception, because the read wrapper catches its own
    # failures and a swallowed BrokenBarrierError would read as a clean pass.
    assert not tracker.broke, "reads never actually overlapped"
    assert tracker.max_in_flight == READS, (
        "expected all %d reads in flight together, saw %d"
        % (READS, tracker.max_in_flight))
    # Backstop for the shape the barrier cannot see: six reads that overlap
    # and are still individually slow. Loose, because overlap is now proven
    # above rather than inferred from the clock.
    assert elapsed < 1.0, "reads took as long as if they ran one after another"


def test_read_access_requests_is_called_exactly_once_per_fetch(monkeypatch):
    calls = {"n": 0}

    class EmptyFakeSvc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId=None, range=None):
            return self

        def execute(self):
            return {"values": []}

    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: EmptyFakeSvc())

    def counted_access_requests(limit=300):
        calls["n"] += 1
        return []

    monkeypatch.setattr(appmod, "_read_access_requests", counted_access_requests)

    appmod._fetch_visitor_analytics_uncached()

    assert calls["n"] == 1, "access requests were fetched more than once for a single dashboard load"


def test_conversions_and_identity_map_both_reflect_the_one_shared_fetch(monkeypatch):
    """Not just 'called once' -- the single fetched list must actually reach
    both consumers with the right semantics: idmap sees the full (up to 2000)
    list, conversions keeps _read_access_requests()'s own default-300 cap.

    305 fake requests, one visitor whose access-request row (#302) is past the
    300 cutoff: if idmap had been capped to the same 300 as `conversions`,
    that visitor's company would never show up in top_companies."""
    requests = [{"vid": "v%d" % i, "name": "N%d" % i, "email": "e%d@x.com" % i,
                 "company": "Acme"} for i in range(305)]

    va_row = [""] * len(appmod._VA_HEADER)
    va_row[appmod._VA_HEADER.index("Visitor ID")] = "v302"
    va_rows = [appmod._VA_HEADER, va_row]

    class FakeSvc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId=None, range=None):
            self._range = range
            return self

        def execute(self):
            if self._range == "Visitor Analytics!A:AM":
                return {"values": va_rows}
            return {"values": []}

    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: FakeSvc())
    monkeypatch.setattr(appmod, "_read_access_requests", lambda limit=300: requests[:limit])

    data = appmod._fetch_visitor_analytics_uncached()

    assert data["kpis"]["conversions"] == 300, "conversions must keep the original default-300 cap"
    assert dict(data["top_companies"]).get("Acme") == 1, \
        "idmap must have received the full (up to 2000) list, not the capped 300"


def test_va_identity_map_uses_the_passed_in_access_requests_without_refetching(monkeypatch):
    calls = {"n": 0}

    def boom(limit=2000):
        calls["n"] += 1
        return []

    monkeypatch.setattr(appmod, "_read_access_requests", boom)

    idmap = appmod._va_identity_map(vi_rows=[], access_requests=[
        {"vid": "v1", "name": "Ann", "email": "ann@x.com", "company": "Acme"},
    ])

    assert idmap["v1"]["company"] == "Acme"
    assert calls["n"] == 0, "passing access_requests explicitly must skip the internal fetch"


def test_login_events_by_vid_uses_passed_in_rows_without_refetching(monkeypatch):
    def boom():
        raise AssertionError("_va_sheets_service should not be called when rows are passed in")

    monkeypatch.setattr(appmod, "_va_sheets_service", boom)

    ms_rows = [["h"] * 20]  # header only, no data rows
    login_rows = [["h"] * 21]

    out = appmod._login_events_by_vid(ms_rows=ms_rows, login_rows=login_rows)

    assert out == {}
