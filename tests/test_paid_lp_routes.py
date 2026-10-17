"""Landing Page & Relevancy Check: the routes (app.py)."""
import pytest

import app as app_module
from tracker import paid_gads as base
from tracker import paid_gads_segments as gads
from tracker import paid_lp as lp

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/lp"


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


REPORT = {"account": {"id": "1112223334", "name": "Acme", "currency": "USD"}, "summary": {"score": 88, "grade": "B"}, "pages": [], "fixFirst": [], "sections": [], "catalog": {},
          "warnings": [], "unread": [], "notes": [], "readAt": "2026-10-17T12:00:00+00:00", "generatedAt": "2026-10-17T12:00:00+00:00"}


def test_the_report_route_returns_the_report_and_passes_refresh_on(monkeypatch):
    seen = []
    monkeypatch.setattr(lp, "report_for", lambda *x, **k: seen.append((x, k)) or REPORT)
    r = client_as(STAFF).get(API + "/report?account=1112223334&refresh=1")
    assert r.status_code == 200 and r.get_json()["summary"]["score"] == 88
    assert seen[0] == (("1112223334",), {"force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1112223334")
    assert seen[0] == (("1112223334",), {})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1112223334&refresh=0")
    assert seen[0] == (("1112223334",), {})


@pytest.mark.parametrize("err, status", [
    (gads.GadsError("not_configured", "not set"), 503), (gads.GadsError("quota", "slow down"), 429), (gads.GadsError("auth", "revoked"), 502),
    (gads.GadsError("api", "google broke"), 502), (lp.BadRequest("That account is not one of Position2's Google Ads accounts."), 400)])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(lp, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1112223334")
    assert r.status_code == status and str(err) in r.get_json()["error"]


def test_a_refresh_that_is_too_soon_says_when_to_try_again_and_the_api_is_staff_only(monkeypatch):
    def boom(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 45)
    monkeypatch.setattr(lp, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1112223334&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 45
    assert client_as(None).get(API + "/report?account=1").status_code == 302 and client_as("someone@gmail.com").get(API + "/report?account=1").status_code == 302


def test_a_bad_account_is_a_400_through_the_real_parser():
    r = client_as(STAFF).get(API + "/report?account=nonsense")
    assert r.status_code == 400 and "10-digit Google Ads customer id" in r.get_json()["error"]
    assert client_as(STAFF).get(API + "/report").status_code == 400
    assert client_as(STAFF).get(API + "/report?account=1112223334%2Bact_1234567890").status_code == 400            # a Meta account is not checked here


# ── Downloads ───────────────────────────────────────────────────────────────

def _report():
    from tests.test_paid_lp_export import make_report
    return make_report()


@pytest.mark.parametrize("kind, ctype, ext, magic", [("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx", b"PK"),
                                                        ("pdf", "application/pdf", ".pdf", b"%PDF-"), ("zip", "application/zip", ".zip", b"PK")])
def test_each_file_downloads_from_the_same_report_with_a_safe_name(monkeypatch, kind, ctype, ext, magic):
    seen = []
    rep = _report()
    monkeypatch.setattr(lp, "report_for", lambda *x, **k: seen.append((x, k)) or rep)
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=" + kind)
    assert r.status_code == 200 and r.headers["Content-Type"].startswith(ctype) and r.data[:len(magic)] == magic
    assert r.headers["Content-Disposition"] == 'attachment; filename="Acme-Dental-landing-page-check%s"' % ext and r.headers["Cache-Control"] == "private, no-store"
    assert seen == [(("1112223334",), {})]                                   # the saved read: a download never asks to refresh


def test_a_download_needs_a_known_file_and_staff_and_says_why_it_cannot_be_made(monkeypatch):
    assert client_as(STAFF).get(API + "/download?account=1112223334&file=doc").status_code == 400
    assert client_as(STAFF).get(API + "/download?account=1112223334").status_code == 400
    assert client_as(None).get(API + "/download?account=1112223334&file=pdf").status_code == 302
    assert client_as(STAFF).get(API + "/download?account=nonsense&file=pdf").status_code == 400

    def boom(*x, **k):
        raise gads.GadsError("quota", "slow down")
    monkeypatch.setattr(lp, "report_for", boom)
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=pdf")
    assert r.status_code == 429 and "slow down" in r.get_json()["error"]

    def tooSoon(*x, **k):
        raise base.RefreshTooSoon("moments ago", 12)
    monkeypatch.setattr(lp, "report_for", tooSoon)
    assert client_as(STAFF).get(API + "/download?account=1112223334&file=pdf").get_json()["retryIn"] == 12


def test_a_file_that_cannot_be_made_is_said_in_words(monkeypatch):
    from tracker import paid_lp_export as export
    rep = _report()
    monkeypatch.setattr(lp, "report_for", lambda *x, **k: rep)
    monkeypatch.setattr(export, "pdf", lambda r: (_ for _ in ()).throw(RuntimeError("x")))
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=pdf")
    assert r.status_code == 500 and "RuntimeError" in r.get_json()["error"]
