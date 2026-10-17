"""Account Audit: the routes (app.py)."""
import pytest

import app as app_module
from tracker import paid_audit as audit
from tracker import paid_gads as base
from tracker import paid_gads_segments as gads

STAFF = "ana@position2.com"
API = "/p2/paid-marketing/api/audit"


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


REPORT = {"accounts": [{"platform": "google", "id": "1112223334", "name": "Acme", "currency": "USD"}], "summary": {"score": 88, "grade": "B"}, "sections": [], "fixFirst": [],
          "warnings": [], "unread": [], "notes": [], "readAt": "2026-10-17T12:00:00+00:00", "generatedAt": "2026-10-17T12:00:00+00:00"}


def test_the_report_route_returns_the_report_and_passes_refresh_on(monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "report_for", lambda *x, **k: seen.append((x, k)) or REPORT)
    r = client_as(STAFF).get(API + "/report?account=1112223334&refresh=1")
    assert r.status_code == 200 and r.get_json()["summary"]["score"] == 88
    assert seen[0] == (("1112223334",), {"force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1112223334%2Bact_1234567890")
    assert seen[0] == (("1112223334+act_1234567890",), {})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1112223334&refresh=0")
    assert seen[0] == (("1112223334",), {})


@pytest.mark.parametrize("err, status", [
    (gads.GadsError("not_configured", "not set"), 503), (gads.GadsError("quota", "slow down"), 429), (gads.GadsError("auth", "revoked"), 502),
    (gads.GadsError("api", "google broke"), 502), (audit.BadRequest("That account is not one of Position2's Google Ads accounts."), 400)])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(audit, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1112223334")
    assert r.status_code == status and str(err) in r.get_json()["error"]


def test_a_refresh_that_is_too_soon_says_when_to_try_again_and_the_api_is_staff_only(monkeypatch):
    def boom(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 45)
    monkeypatch.setattr(audit, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1112223334&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 45
    assert client_as(None).get(API + "/report?account=1").status_code == 302 and client_as("someone@gmail.com").get(API + "/report?account=1").status_code == 302


def test_a_bad_account_is_a_400_through_the_real_parser():
    r = client_as(STAFF).get(API + "/report?account=nonsense")
    assert r.status_code == 400 and "10-digit Google Ads customer id" in r.get_json()["error"]
    assert client_as(STAFF).get(API + "/report").status_code == 400


# ── Downloads ───────────────────────────────────────────────────────────────

def _report():
    from tests import audit_fakes as A
    from tracker import paid_audit_google_read as gread
    google = A.a_read(answers={gread.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 5000, 400, 1000.0, 0.0)]})
    google["pages"] = None
    google["readAt"] = "2026-10-17T12:00:00+00:00"
    return audit.assemble(google, None, [{"platform": "google", "id": "1112223334", "name": "Acme Dental", "currency": "USD"}])


@pytest.mark.parametrize("kind, ctype, ext, magic", [("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx", b"PK"),
                                                        ("pdf", "application/pdf", ".pdf", b"%PDF-"), ("zip", "application/zip", ".zip", b"PK")])
def test_each_file_downloads_from_the_same_report_with_a_safe_name(monkeypatch, kind, ctype, ext, magic):
    seen = []
    monkeypatch.setattr(audit, "report_for", lambda *x, **k: seen.append((x, k)) or _report())
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=" + kind)
    assert r.status_code == 200 and r.headers["Content-Type"].startswith(ctype) and r.data[:len(magic)] == magic
    assert r.headers["Content-Disposition"] == 'attachment; filename="Acme-Dental-account-audit%s"' % ext and r.headers["Cache-Control"] == "private, no-store"
    assert seen == [(("1112223334",), {})]                                   # the saved read: a download never asks to refresh


def test_a_download_needs_a_known_file_and_staff_and_says_why_it_cannot_be_made(monkeypatch):
    assert client_as(STAFF).get(API + "/download?account=1112223334&file=doc").status_code == 400
    assert client_as(STAFF).get(API + "/download?account=1112223334").status_code == 400
    assert client_as(None).get(API + "/download?account=1112223334&file=pdf").status_code == 302
    assert client_as("someone@gmail.com").get(API + "/download?account=1112223334&file=pdf").status_code == 302
    for err, status in ((gads.GadsError("quota", "slow down"), 429), (audit.BadRequest("That account is not one of Position2's Google Ads accounts."), 400),
                        (gads.GadsError("not_configured", "not set"), 503), (base.RefreshTooSoon("moment ago", 30), 429)):
        def boom(*x, **k):
            raise err
        monkeypatch.setattr(audit, "report_for", boom)
        r = client_as(STAFF).get(API + "/download?account=1112223334&file=xlsx")
        assert r.status_code == status and str(err) in r.get_json()["error"]


def test_a_file_that_cannot_be_made_is_a_500_in_words(monkeypatch):
    from tracker import paid_audit_export as export
    monkeypatch.setattr(audit, "report_for", lambda *x, **k: _report())
    def broken(report):
        raise RuntimeError("boom")
    monkeypatch.setattr(export, "pdf", broken)
    r = client_as(STAFF).get(API + "/download?account=1112223334&file=pdf")
    assert r.status_code == 500 and r.get_json()["error"] == "The file could not be made (RuntimeError)."


# ── The page ────────────────────────────────────────────────────────────────

def test_the_page_renders_for_staff_and_not_for_anyone_else():
    path = "/p2/paid-marketing/account-audit"
    page = client_as(STAFF).get(path)
    assert page.status_code == 200 and b"account_audit.js" in page.data and b"account_audit.css" in page.data and b"au_data.js" in page.data and b"Account Audit" in page.data
    assert client_as(None).get(path).status_code == 302 and client_as("someone@gmail.com").get(path).status_code == 302
