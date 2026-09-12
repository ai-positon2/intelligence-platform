"""Performance audit: _rows_from_live_sheet (42 North Dental Slot Checker) read
the "LPs" and "Locations" tabs one after another on the same shared service
object. The two reads are independent, so they now run concurrently, each
with its own service instance (httplib2 is not thread-safe to share).
static_discovery=True was also missing from _sheets_service(), same bug as
the rest of the platform's Sheets clients -- every call paid an extra
discovery-document network round-trip.
"""

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import slot_checker  # noqa: E402


@pytest.fixture(autouse=True)
def sa_configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_SA_JSON", json.dumps({"type": "service_account"}))

    class _FakeCreds:
        @staticmethod
        def from_service_account_info(*a, **k):
            return object()

    import google.oauth2.service_account as _sa
    monkeypatch.setattr(_sa, "Credentials", _FakeCreds)


def test_static_discovery_is_set():
    import inspect
    src = inspect.getsource(slot_checker._sheets_service)
    assert "static_discovery=True" in src


def test_the_two_tab_reads_overlap_instead_of_running_one_after_another(monkeypatch):
    barrier = threading.Barrier(2, timeout=5.0)
    broke = {"v": False}

    class SlowValues:
        def get(self, spreadsheetId=None, range=None):
            self._range = range
            return self

        def execute(self):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                broke["v"] = True
            time.sleep(0.05)
            return {"values": []}

    class SlowSvc:
        def spreadsheets(self):
            return self

        def values(self):
            return SlowValues()

    import googleapiclient.discovery as _disc
    monkeypatch.setattr(_disc, "build", lambda *a, **k: SlowSvc())

    started = time.time()
    slot_checker._rows_from_live_sheet()
    elapsed = time.time() - started

    assert not broke["v"], "the two tab reads never actually overlapped"
    assert elapsed < 1.0, "reads took as long as if they ran one after another"


def test_each_concurrent_read_gets_its_own_service_instance(monkeypatch):
    calls = {"n": 0}

    class Svc:
        def spreadsheets(self):
            return self

        def values(self):
            return self

        def get(self, spreadsheetId=None, range=None):
            return self

        def execute(self):
            return {"values": []}

    import googleapiclient.discovery as _disc

    def build(*a, **k):
        calls["n"] += 1
        return Svc()

    monkeypatch.setattr(_disc, "build", build)

    slot_checker._rows_from_live_sheet()

    assert calls["n"] == 2, "expected one service instance per concurrent read"
