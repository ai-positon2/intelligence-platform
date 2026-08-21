"""/p2/b2b-agents/gentle-dental-slot-checker (page + /data).

Both routes are @position2_required: this is internal availability data for a
client's practice network, not something to serve to any signed-in user.

The route layer is thin on purpose -- tracker/slot_checker.py owns the parsing
and derivation, and tests/test_slot_checker.py covers that -- so what is pinned
here is the HTTP shape: who can reach it, what the payload contains, that gzip
negotiation works, and that a missing snapshot renders an empty dashboard rather
than a 500. Compression is asserted here but not implemented here: the app-wide
_compress_response hook gzips JSON over 800 bytes, which is why this view has no
gzip code of its own. The tests still cover the observable behaviour, since that
is what the browser depends on.
"""
import gzip
import json
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import slot_checker as sc  # noqa: E402

URL = "/p2/b2b-agents/gentle-dental-slot-checker"
DATA = URL + "/data"


def _client(email="reporting@position2.com"):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture(autouse=True)
def _clean_caches():
    """The module TTL cache persists for the life of the process, exactly as it
    would in a running server, so a test that swaps the snapshot out would
    otherwise be answered from another test's data."""
    sc.reset_cache()
    yield
    sc.reset_cache()


def _fake(generated_at="2026-08-21T00:00:00+00:00", slots=7):
    return {
        "generated_at": generated_at,
        "source": {"file": "x.xlsx"},
        "dates": ["2026-08-12", "2026-08-13"],
        "locations": [{
            "office": "007", "name": "Quincy", "account": "Gentle Dental",
            "brand": "Gentle Dental", "state": "MA", "city": "Quincy",
            "url": "https://example.test/ols", "system": "gentledental",
            "booking": "Calendar View", "checked_at": "2026-08-11T09:00:00",
            "services": [{"name": "Emergency Exam",
                          "observations": [{"at": "2026-08-11T09:00:00", "counts": [slots, 0]}]}],
        }],
    }


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    def write(snap):
        p = tmp_path / "slot_checker_snapshot.json"
        p.write_text(json.dumps(snap))
        monkeypatch.setattr(sc, "SNAPSHOT_PATH", p)
        sc.reset_cache()
        return p
    return write


# ── auth ────────────────────────────────────────────────────────────────────

def test_page_requires_login():
    resp = appmod.app.test_client().get(URL, follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_data_requires_login():
    resp = appmod.app.test_client().get(DATA, follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_a_signed_in_non_position2_user_is_bounced(snapshot):
    snapshot(_fake())
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "someone@gmail.com", "name": "T"}
    resp = c.get(DATA, follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_page_renders_for_any_position2_staff(snapshot):
    snapshot(_fake())
    resp = _client("someone@position2.com").get(URL)
    assert resp.status_code == 200
    assert b"Gentle Dental Slot Checker" in resp.data


def test_the_page_ships_the_stylesheet_and_the_data_url_it_calls(snapshot):
    """A 200 proves nothing about a page that renders entirely from JS, so pin
    the two things without which it renders blank."""
    snapshot(_fake())
    body = _client().get(URL).data.decode()
    assert "css/gentle_dental_slot_checker.css" in body
    assert DATA in body


# ── payload ─────────────────────────────────────────────────────────────────

def test_data_returns_the_derived_dashboard(snapshot):
    snapshot(_fake())
    body = _client().get(DATA).get_json()
    assert body["totals"]["slots"] == 7
    assert body["totals"]["practices"] == 1
    assert [p["name"] for p in body["practices"]] == ["Quincy"]
    assert [d["weekday"] for d in body["dates"]] == ["Wed", "Thu"]


def test_data_carries_every_panel_the_page_renders(snapshot):
    """The template reads each of these by name; a rename that drops one would
    otherwise only show up as an empty card in a browser."""
    snapshot(_fake())
    body = _client().get(DATA).get_json()
    for key in ("dates", "practices", "totals", "by_state", "by_service",
                "by_date", "by_weekday", "by_brand", "alerts", "freshness"):
        assert key in body, "payload lost " + key
    for key in ("no_data", "zero", "thin", "unbookable_services"):
        assert key in body["alerts"], "alerts lost " + key


def test_data_is_gzipped_when_the_client_accepts_it(snapshot):
    snapshot(_fake())
    resp = _client().get(DATA, headers={"Accept-Encoding": "gzip"})
    assert resp.headers["Content-Encoding"] == "gzip"
    # Flask appends Cookie to Vary on session responses, so assert containment
    # rather than equality: what matters is that a cache cannot serve the gzipped
    # body to a client that did not ask for it.
    assert "Accept-Encoding" in resp.headers["Vary"]
    assert json.loads(gzip.decompress(resp.data))["totals"]["slots"] == 7


def test_data_is_plain_json_when_the_client_does_not(snapshot):
    snapshot(_fake())
    resp = _client().get(DATA, headers={"Accept-Encoding": "identity"})
    assert "Content-Encoding" not in resp.headers
    assert resp.get_json()["totals"]["slots"] == 7


def test_a_fresh_snapshot_is_served_after_the_previous_one_is_replaced():
    """Guards the one caching layer this route does have. slot_checker.fetch()
    holds its result for 300s, so without the reset a redeploy would keep
    serving the old snapshot; ?fresh=1 is the bypass, and it has to work."""
    snap_a = _fake(generated_at="2026-08-21T00:00:00+00:00", slots=7)
    snap_b = _fake(generated_at="2026-08-22T00:00:00+00:00", slots=99)
    calls = {"n": 0}

    def fake_load(path=None):
        calls["n"] += 1
        return snap_a if calls["n"] == 1 else snap_b

    import unittest.mock as mock
    with mock.patch.object(sc, "load_snapshot", fake_load):
        c = _client()
        assert c.get(DATA).get_json()["totals"]["slots"] == 7
        # Still cached, so still the first snapshot.
        assert c.get(DATA).get_json()["totals"]["slots"] == 7
        assert c.get(DATA + "?fresh=1").get_json()["totals"]["slots"] == 99


# ── degradation ─────────────────────────────────────────────────────────────

def test_a_missing_snapshot_serves_an_empty_dashboard_not_a_500(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "SNAPSHOT_PATH", tmp_path / "absent.json")
    sc.reset_cache()
    resp = _client().get(DATA)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["practices"] == []
    assert body["totals"]["slots"] == 0


def test_a_corrupt_snapshot_serves_an_empty_dashboard_not_a_500(monkeypatch, tmp_path):
    p = tmp_path / "slot_checker_snapshot.json"
    p.write_text("{ this is not json")
    monkeypatch.setattr(sc, "SNAPSHOT_PATH", p)
    sc.reset_cache()
    resp = _client().get(DATA)
    assert resp.status_code == 200
    assert resp.get_json()["practices"] == []


def test_the_page_still_renders_when_there_is_no_snapshot(monkeypatch, tmp_path):
    """The shell must not depend on the data, or a bad snapshot takes the whole
    page down instead of showing its own empty state."""
    monkeypatch.setattr(sc, "SNAPSHOT_PATH", tmp_path / "absent.json")
    sc.reset_cache()
    assert _client().get(URL).status_code == 200


# ── the committed snapshot, through the real route ──────────────────────────

def test_the_committed_snapshot_serves_a_populated_dashboard():
    body = _client().get(DATA).get_json()
    if not body["practices"]:
        pytest.skip("no committed snapshot in this checkout")
    t = body["totals"]
    assert t["slots"] > 0 and t["practices"] > 1
    assert t["window_days"] == len(body["dates"])
    assert sum(r["slots"] for r in body["by_date"]) == t["slots"]
