"""Performance audit: _fetch_usage_data (backs both the Internal Usage and
External Usage admin dashboards) read its three independent tabs -- the
login/member-signin tab, Page Views, and Visitor Analytics -- one after
another, each rebuilding its own Sheets client from scratch. Three serial
network round-trips plus three client-construction costs on every request that
isn't served from... actually there is no cache here at all (unlike the sibling
fixes), so this paid the full cost on EVERY page load of either dashboard.

Fixed the same way as _fetch_anon_visitors_data / _fetch_job_change_tracked_data:
the three reads now run concurrently via ThreadPoolExecutor.
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
    monkeypatch.setenv("GOOGLE_SA_JSON", '{"type": "service_account"}')


def _patch_build(monkeypatch, make_exec):
    class FakeSvc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId=None, range=None):
            return make_exec(range)

    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", lambda *a, **k: FakeSvc())

    class _FakeCreds:
        @staticmethod
        def from_service_account_info(*a, **k):
            return object()

    import google.oauth2.service_account as _sa
    monkeypatch.setattr(_sa, "Credentials", _FakeCreds)


def test_the_three_tab_reads_overlap_instead_of_running_one_after_another(monkeypatch):
    barrier = threading.Barrier(3, timeout=5.0)
    broke = {"v": False}

    class SlowExec:
        def execute(self):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                broke["v"] = True
            time.sleep(0.05)
            return {"values": []}

    _patch_build(monkeypatch, lambda r: SlowExec())

    started = time.time()
    appmod._fetch_usage_data(internal=True)
    elapsed = time.time() - started

    assert not broke["v"], "the three tab reads never actually overlapped"
    assert elapsed < 1.0, "reads took as long as if they ran one after another"


def test_internal_and_external_read_the_right_login_range(monkeypatch):
    seen_ranges = []
    lock = threading.Lock()

    class RecordingExec:
        def __init__(self, r):
            self._r = r

        def execute(self):
            with lock:
                seen_ranges.append(self._r)
            return {"values": []}

    _patch_build(monkeypatch, lambda r: RecordingExec(r))

    appmod._fetch_usage_data(internal=True)
    assert "A:U" in seen_ranges

    seen_ranges.clear()
    appmod._fetch_usage_data(internal=False)
    assert any(r.endswith("!A:T") for r in seen_ranges)
