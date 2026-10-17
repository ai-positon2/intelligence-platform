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


def test_the_agent_is_on_the_hub_the_dashboard_the_command_palette_and_the_assistants_knowledge():
    import os
    hub = client_as(STAFF).get("/p2/paid-marketing").data.decode()
    assert "/p2/paid-marketing/account-audit" in hub and "Account Audit" in hub and "c-aud" in hub
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert "Account Audit" in open(os.path.join(root, "templates", "_paid_kpal.html"), encoding="utf-8").read()
    assert ".c-aud" in open(os.path.join(root, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    src = open(os.path.join(root, "app.py"), encoding="utf-8").read()
    assert "Account Audit (/p2/paid-marketing/account-audit)" in src and "a part the platform would not read is reported as could not check" in src


def test_there_is_no_em_or_en_dash_in_any_of_the_audit_code():
    import glob, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = glob.glob(os.path.join(root, "tracker", "paid_audit*.py")) + [os.path.join(root, "docs", "account-audit-plan.md")]
    for p in files:
        text = open(p, encoding="utf-8").read()
        assert "—" not in text and "–" not in text, p


# ── Saved audits ────────────────────────────────────────────────────────────

class FakeAuditStore:
    """The audit store's functions over a list, so the routes are tested without a database."""

    def __init__(self):
        self.rows = []

    def save(self, key, name, by, report, note=""):
        from tracker import paid_audit_history as h
        snap = h.snapshot(report)
        row = {"id": "s%d" % (len(self.rows) + 1), "accountKey": key, "accountName": name, "createdBy": by, "createdAt": "2026-10-17T10:00:00+00:00", "score": snap["score"],
               "grade": snap["grade"], "tally": snap["tally"], "critical": snap["critical"], "high": snap["high"], "note": note or "", "results": snap["results"]}
        self.rows.append(row)
        return {k: v for k, v in row.items() if k != "results"}

    def list_saved(self, key, limit=30):
        return [{k: v for k, v in r.items() if k != "results"} for r in reversed(self.rows) if r["accountKey"] == key]

    def get(self, sid, key=None):
        return next((r for r in self.rows if r["id"] == sid and (key is None or r["accountKey"] == key)), None)

    def latest(self, key):
        mine = [r for r in self.rows if r["accountKey"] == key]
        return mine[-1] if mine else None

    def delete(self, sid, by, is_admin=False):
        row = self.get(sid)
        if row and (is_admin or row["createdBy"] == by):
            self.rows.remove(row)
            return True
        return False


@pytest.fixture
def store(monkeypatch):
    from tracker import paid_audit_store
    fake = FakeAuditStore()
    for name in ("save", "list_saved", "get", "latest", "delete"):
        monkeypatch.setattr(paid_audit_store, name, getattr(fake, name))
    monkeypatch.setattr(audit, "report_for", lambda *x, **k: _report())
    return fake


def post(client, path, body):
    return client.post(API + path, json=body)


def test_an_audit_is_saved_with_a_note_and_listed_newest_first(store):
    c = client_as(STAFF)
    r = post(c, "/save", {"account": "1112223334", "note": "Onboarding"})
    assert r.status_code == 200 and r.get_json()["saved"]["note"] == "Onboarding" and r.get_json()["saved"]["createdBy"] == STAFF and r.get_json()["saved"]["accountName"] == "Acme Dental"
    assert r.get_json()["saved"]["accountKey"] == "1112223334" and "results" not in r.get_json()["saved"]
    post(c, "/save", {"account": "1112223334", "note": "Q1"})
    got = c.get(API + "/history?account=1112223334").get_json()["history"]
    assert [h["note"] for h in got] == ["Q1", "Onboarding"] and c.get(API + "/history?account=2223334445").get_json()["history"] == []


def test_a_combined_account_is_filed_under_both_ids(store):
    r = post(client_as(STAFF), "/save", {"account": "1112223334+act_1234567890"})
    assert r.get_json()["saved"]["accountKey"] == "1112223334+act_1234567890"


def test_save_and_history_say_what_is_wrong_with_the_request(store):
    c = client_as(STAFF)
    assert post(c, "/save", {"account": "nonsense"}).status_code == 400 and post(c, "/save", {}).status_code == 400
    assert c.get(API + "/history?account=nonsense").status_code == 400 and c.get(API + "/history").status_code == 400
    assert client_as(None).post(API + "/save", json={"account": "1112223334"}).status_code == 302
    assert client_as("someone@gmail.com").get(API + "/history?account=1112223334").status_code == 302


def test_the_audit_now_is_compared_with_the_latest_saved_or_a_chosen_one(store):
    c = client_as(STAFF)
    assert c.get(API + "/compare?account=1112223334").get_json() == {"compare": None}
    post(c, "/save", {"account": "1112223334", "note": "Onboarding"})
    out = c.get(API + "/compare?account=1112223334").get_json()
    assert out["id"] == "s1" and out["compare"]["note"] == "Onboarding" and out["compare"]["scoreChange"] == 0 and out["compare"]["since"] == "2026-10-17T10:00:00+00:00"
    assert c.get(API + "/compare?account=1112223334&id=s1").get_json()["id"] == "s1"
    r = c.get(API + "/compare?account=1112223334&id=nope")
    assert r.status_code == 400 and "not one for this account" in r.get_json()["error"]
    assert c.get(API + "/compare?account=2223334445&id=s1").status_code == 400                 # a saved audit of another account


def test_only_whoever_saved_an_audit_or_an_admin_removes_it(store):
    post(client_as(STAFF), "/save", {"account": "1112223334"})
    assert client_as("ben@position2.com").delete(API + "/history/s1", content_type="application/json").status_code == 403 and store.get("s1") is not None
    assert client_as(STAFF).delete(API + "/history/s1", content_type="application/json").get_json() == {"ok": True} and store.get("s1") is None
    assert client_as(STAFF).delete(API + "/history/s1", content_type="application/json").status_code == 404
    sid = post(client_as(STAFF), "/save", {"account": "1112223334"}).get_json()["saved"]["id"]
    admin = sorted(app_module.ADMIN_EMAILS)[0]
    assert admin != STAFF and client_as(admin).delete(API + "/history/" + sid, content_type="application/json").status_code == 200 and store.get(sid) is None


def test_a_store_that_is_down_is_a_503_in_words(monkeypatch):
    from tracker import paid_audit_store
    def down(*a, **k):
        raise paid_audit_store.StoreUnavailable("database is not configured")
    monkeypatch.setattr(paid_audit_store, "list_saved", down)
    monkeypatch.setattr(paid_audit_store, "save", down)
    monkeypatch.setattr(paid_audit_store, "get", down)
    monkeypatch.setattr(audit, "report_for", lambda *x, **k: _report())
    c = client_as(STAFF)
    for r in (c.get(API + "/history?account=1112223334"), post(c, "/save", {"account": "1112223334"}), c.delete(API + "/history/s1", content_type="application/json")):
        assert r.status_code == 503 and "store is unavailable" in r.get_json()["error"]


def test_saving_and_removing_refuse_a_cross_site_or_non_json_request(store):
    c = client_as(STAFF)
    assert c.post(API + "/save", json={"account": "1112223334"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post(API + "/save", data="account=1112223334", content_type="application/x-www-form-urlencoded").status_code == 415
    post(c, "/save", {"account": "1112223334"})
    assert c.delete(API + "/history/s1", headers={"Origin": "https://evil.example"}, content_type="application/json").status_code == 403
    assert c.delete(API + "/history/s1").status_code == 415 and store.get("s1") is not None
