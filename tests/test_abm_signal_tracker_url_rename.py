"""ABM Signal Tracker's URLs moved: /p2/accounts -> /p2/abm-signal-tracker/accounts,
and /p2/signal-tracker/<account> -> /p2/abm-signal-tracker/<account>.

Three URL generations exist for the dashboard path alone (bare /signal-tracker/<id>,
then /p2/signal-tracker/<id> after the v16 /p2 relocation, now /p2/abm-signal-tracker/<id>),
plus the always-had-it /dashboard/<id> alias. Every old one must keep resolving --
bookmarks, the Slack weekly digest, and anything printed before this rename all point
at an old URL -- and none of them should chain through more than one 301, since each
extra hop this repo has ever added for one of these accumulates on every subsequent
rename otherwise.
"""

import os
import re
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app as appmod  # noqa: E402


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


# ── Canonical routes serve directly ──────────────────────────────────────────

def test_new_accounts_url_serves_the_picker_directly():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    resp = c.get("/p2/abm-signal-tracker/accounts")
    assert resp.status_code == 200


def test_new_dashboard_url_serves_a_known_account_directly(client):
    resp = client.get("/p2/abm-signal-tracker/healthcare")
    assert resp.status_code == 200


def test_new_dashboard_url_still_404s_an_unknown_account(client):
    resp = client.get("/p2/abm-signal-tracker/not-a-real-account")
    assert resp.status_code == 404


# ── Every old generation of the URL redirects, in exactly one hop ───────────

def test_old_p2_accounts_redirects_straight_to_canonical(client):
    resp = client.get("/p2/accounts", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/p2/abm-signal-tracker/accounts")


def test_bare_accounts_redirects_straight_to_canonical_not_through_old_p2_path():
    """Before this rename, bare /accounts bounced once through /p2/accounts. Now that
    /p2/accounts is itself a redirect, /accounts must skip straight to the final URL
    or a bookmark from years ago would chain two redirects deep."""
    c = appmod.app.test_client()
    resp = c.get("/accounts", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/p2/abm-signal-tracker/accounts")


def test_old_p2_signal_tracker_redirects_straight_to_canonical(client):
    resp = client.get("/p2/signal-tracker/healthcare", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/p2/abm-signal-tracker/healthcare")


def test_old_p2_signal_tracker_section_url_preserves_the_section(client):
    resp = client.get("/p2/signal-tracker/healthcare/pipeline", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/p2/abm-signal-tracker/healthcare/pipeline")


def test_bare_signal_tracker_redirects_straight_to_canonical_not_through_old_p2_path():
    c = appmod.app.test_client()
    resp = c.get("/signal-tracker/healthcare", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/p2/abm-signal-tracker/healthcare")


def test_dashboard_legacy_alias_redirects_to_canonical(client):
    resp = client.get("/dashboard/healthcare", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["Location"].endswith("/p2/abm-signal-tracker/healthcare")


# ── The dashboard's own client-side section routing must recognize the new path ──

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("report_file", ["dashboard.html", "dashboard_csg.html"])
def test_dashboard_curacct_regex_recognizes_the_new_canonical_path(report_file):
    """curAcct() reads the account id back out of location.pathname so switching
    sections (Overview/Pipeline/etc) can rewrite the URL bar via history.replaceState
    without a real navigation. Its regex only listed the old path segments
    (dashboard, signal-tracker) -- on the real, now-served /p2/abm-signal-tracker/<id>
    path it would silently return '' and every section switch would rewrite the URL
    to a path missing the account id entirely."""
    with open(os.path.join(_ROOT, "reports", report_file), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"function curAcct\(\)\{var m=location\.pathname\.match\((/[^;]+/)\);", text)
    assert m, "curAcct() not found or its shape changed"
    assert "abm-signal-tracker" in m.group(1), (
        f"curAcct()'s own path regex ({m.group(1)!r}) doesn't recognize the current "
        f"canonical path -- section switches will drop the account id from the URL bar")
