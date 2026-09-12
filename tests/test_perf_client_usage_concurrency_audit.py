"""Performance audit: the per-client Client Usage detail page's data path read
the two sign-in tabs ("A:U" and the Member Signins tab) for its name/picture
map, then later re-read the SAME two tabs again for its login-count pass, then
read Page Views, then read Agent Runs -- up to six sequential Sheets reads
(two of them pure duplicates of two others) for what is only four distinct
tabs, on every cache miss.

Fixed: each of the four distinct tabs (the two sign-in tabs, Page Views, Agent
Runs) is now read exactly once, all four concurrently, and the sign-in rows
are reused for both the name map and the login-count pass instead of being
fetched twice.
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
def reset_state(monkeypatch):
    monkeypatch.setattr(appmod, "_CU_CACHE", {})
    monkeypatch.setattr(appmod, "_CU_ALL_CACHE", {"data": None, "ts": 0.0})
    monkeypatch.setitem(appmod.CLIENTS, "acme", {"name": "Acme", "domains": ["acme.com"], "agents": []})


def test_cu_name_map_from_rows_is_pure_and_reads_no_sheet(monkeypatch):
    def boom(tab_range):
        raise AssertionError("should not read a sheet -- this function is pure")
    monkeypatch.setattr(appmod, "_cu_read_tab", boom)

    au_rows = [["h"] * 8, ["t", "", "", "", "", "a@x.com", "Ann", ""]]
    member_rows = [["h"] * 10, ["t", "", "", "", "", "b@x.com", "Bob", "", "http://pic"]]

    m = appmod._cu_name_map_from_rows([au_rows, member_rows])

    assert m["a@x.com"]["name"] == "Ann"
    assert m["b@x.com"]["name"] == "Bob"
    assert m["b@x.com"]["picture"] == "http://pic"


def test_each_of_the_four_distinct_tabs_is_read_exactly_once(monkeypatch):
    calls = []
    lock = threading.Lock()

    def read(tab_range):
        with lock:
            calls.append(tab_range)
        return []

    monkeypatch.setattr(appmod, "_cu_read_tab", read)

    appmod._fetch_client_usage("acme", force=True)

    assert sorted(calls) == sorted([
        "A:U", "%s!A:T" % appmod._MEMBER_TAB,
        "Page Views!A:N", "%s!A:F" % appmod._AR_TAB,
    ]), "expected exactly one read per distinct tab, no duplicates: %r" % calls


def test_the_four_tab_reads_overlap_instead_of_running_one_after_another(monkeypatch):
    barrier = threading.Barrier(4, timeout=5.0)
    broke = {"v": False}

    def read(tab_range):
        try:
            barrier.wait(timeout=5.0)
        except threading.BrokenBarrierError:
            broke["v"] = True
        time.sleep(0.05)
        return []

    monkeypatch.setattr(appmod, "_cu_read_tab", read)

    started = time.time()
    appmod._fetch_client_usage("acme", force=True)
    elapsed = time.time() - started

    assert not broke["v"], "the four tab reads never actually overlapped"
    assert elapsed < 1.0
