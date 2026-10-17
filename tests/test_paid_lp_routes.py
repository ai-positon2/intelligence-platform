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


# ── The page, the card and the registry ─────────────────────────────────────

PAGE = "/p2/paid-marketing/landing-page-check"


def test_the_page_opens_for_staff_and_is_closed_to_everyone_else():
    r = client_as(STAFF).get(PAGE)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Landing Page &amp; Relevancy Check" in html and 'id="acctPick"' in html and "js/lp_data.js" in html and "js/landing_page_check.js" in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(PAGE).status_code == 302


def test_the_card_is_on_the_paid_marketing_page_the_hub_counts_it_and_the_command_palette_lists_it():
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/landing-page-check"' in paid and "c-lp" in paid
    from tracker import paid_lp_docs
    assert paid_lp_docs.markdown()                                           # the checklist document builds
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("templates/_paid_kpal.html", "templates/paid_marketing.html"):
        assert "/p2/paid-marketing/landing-page-check" in open(os.path.join(root, name), encoding="utf-8").read(), name
    # the hub's counts are held to the cards by tests/test_hub_card_counts.py and tests/test_paid_exp_routes.py


def test_the_assistant_knows_the_agent():
    src = open(app_module.__file__, encoding="utf-8").read()
    assert "Landing Page & Relevancy Check (/p2/paid-marketing/landing-page-check)" in src and "35 checks in six sections" in src and "A page that does not open scores 0" in src


def test_no_em_or_en_dash_in_any_landing_page_check_code_or_the_plan():
    import glob
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = glob.glob(os.path.join(root, "tracker", "paid_lp*.py")) + [os.path.join(root, "docs", "landing-page-check-plan.md"), os.path.join(root, "docs", "landing-page-check-checks.md")]
    assert files
    for p in files:
        text = open(p, encoding="utf-8").read()
        assert "—" not in text and "–" not in text, p


# ── The lab speed test ──────────────────────────────────────────────────────

def _lab_client(monkeypatch, result=None, error=None, key="K"):
    from tracker import paid_lp_speed as sp
    if key:
        monkeypatch.setenv("PAGESPEED_API_KEY", key)
    else:
        monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    calls = []

    def fake(account, page, **kw):
        calls.append((account, page))
        if error:
            raise error
        return result or {"score": 55, "lcpMs": 3000, "ranAt": "2026-10-17T12:00:00+00:00"}
    monkeypatch.setattr(lp, "run_lab", fake)
    app_module._LP_LAB_USES.clear()
    return calls, sp


def test_the_lab_test_runs_for_the_page_named_and_returns_its_result(monkeypatch):
    calls, _sp = _lab_client(monkeypatch)
    r = client_as(STAFF).post(API + "/lab", json={"account": "1112223334", "page": "acme-dental.com/lp"})
    assert r.status_code == 200 and r.get_json()["lab"]["score"] == 55 and calls == [("1112223334", "acme-dental.com/lp")]


def test_the_lab_test_is_closed_without_a_key_and_to_everyone_but_staff(monkeypatch):
    calls, _sp = _lab_client(monkeypatch, key=None)
    r = client_as(STAFF).post(API + "/lab", json={"account": "1112223334", "page": "x"})
    assert r.status_code == 503 and r.get_json()["kind"] == "not_configured" and "PAGESPEED_API_KEY" in r.get_json()["error"] and calls == []
    assert client_as(None).post(API + "/lab", json={}).status_code == 302 and client_as("someone@gmail.com").post(API + "/lab", json={}).status_code == 302


@pytest.mark.parametrize("kind, status", [("quota", 429), ("page", 502), ("api", 502), ("not_configured", 503)])
def test_what_goes_wrong_in_the_test_reaches_the_page_with_a_status_and_a_reason(monkeypatch, kind, status):
    from tracker import paid_lp_speed as sp
    _calls, _s = _lab_client(monkeypatch, error=sp.LabError(kind, "the reason"))
    r = client_as(STAFF).post(API + "/lab", json={"account": "1112223334", "page": "x"})
    assert r.status_code == status and r.get_json() == {"error": "the reason", "kind": kind}


def test_a_page_or_account_that_is_not_one_is_a_400_and_a_google_failure_is_mapped(monkeypatch):
    _lab_client(monkeypatch, error=lp.BadRequest("That page is not one of this account's landing pages."))
    r = client_as(STAFF).post(API + "/lab", json={"account": "1112223334", "page": "evil"})
    assert r.status_code == 400 and "not one of this account's landing pages" in r.get_json()["error"]
    _lab_client(monkeypatch, error=gads.GadsError("quota", "slow down"))
    assert client_as(STAFF).post(API + "/lab", json={"account": "1112223334", "page": "x"}).status_code == 429
    calls, _sp = _lab_client(monkeypatch)
    assert client_as(STAFF).post(API + "/lab", json=["not", "an", "object"]).status_code == 200 and calls == [(None, None)]          # a body that is not an object names no account or page: the runner says so


def test_a_person_can_run_only_so_many_tests_a_day(monkeypatch):
    calls, _sp = _lab_client(monkeypatch)
    c = client_as(STAFF)
    for _ in range(app_module.LP_LAB_DAILY_CAP):
        assert c.post(API + "/lab", json={"account": "1112223334", "page": "x"}).status_code == 200
    r = c.post(API + "/lab", json={"account": "1112223334", "page": "x"})
    assert r.status_code == 429 and r.get_json()["kind"] == "cap" and len(calls) == app_module.LP_LAB_DAILY_CAP
    other = client_as("ben@position2.com").post(API + "/lab", json={"account": "1112223334", "page": "x"})
    assert other.status_code == 200


def test_a_test_that_could_not_run_does_not_use_up_the_days_allowance_when_it_never_reached_google(monkeypatch):
    from tracker import paid_lp_speed as sp
    _calls, _s = _lab_client(monkeypatch, error=sp.LabError("quota", "x"))
    c = client_as(STAFF)
    for _ in range(app_module.LP_LAB_DAILY_CAP + 5):
        assert c.post(API + "/lab", json={"account": "1112223334", "page": "x"}).status_code == 429
    assert app_module._LP_LAB_USES.get(STAFF, (None, 0))[1] == 0


# ── Read it like a person ───────────────────────────────────────────────────

def _read_client(monkeypatch, result=None, error=None):
    calls = []

    def fake(account, page, **kw):
        calls.append((account, page))
        if error:
            raise error
        return result or {"verdict": "partly", "promiseKept": "Some of it.", "gaps": [], "pageEdits": [], "headlineIdeas": [], "adEdits": [], "source": "model"}
    monkeypatch.setattr(lp, "explain", fake)
    app_module._LP_READ_USES.clear()
    return calls


def test_a_page_is_read_for_the_account_and_page_named(monkeypatch):
    calls = _read_client(monkeypatch)
    r = client_as(STAFF).post(API + "/read", json={"account": "1112223334", "page": "acme-dental.com/lp"})
    assert r.status_code == 200 and r.get_json()["read"]["verdict"] == "partly" and calls == [("1112223334", "acme-dental.com/lp")]
    assert client_as(None).post(API + "/read", json={}).status_code == 302 and client_as("someone@gmail.com").post(API + "/read", json={}).status_code == 302


@pytest.mark.parametrize("err, status", [(lp.BadRequest("That page is not one of this account's landing pages."), 400), (gads.GadsError("quota", "slow down"), 429),
                                         (gads.GadsError("api", "broke"), 502), (base.RefreshTooSoon("moments ago", 9), 429)])
def test_what_goes_wrong_reading_a_page_reaches_the_page(monkeypatch, err, status):
    _read_client(monkeypatch, error=err)
    r = client_as(STAFF).post(API + "/read", json={"account": "1112223334", "page": "x"})
    assert r.status_code == status and str(err) in r.get_json()["error"]


def test_a_person_can_have_only_so_many_pages_read_a_day(monkeypatch):
    calls = _read_client(monkeypatch)
    c = client_as(STAFF)
    for _ in range(app_module.LP_READ_DAILY_CAP):
        assert c.post(API + "/read", json={"account": "1112223334", "page": "x"}).status_code == 200
    r = c.post(API + "/read", json={"account": "1112223334", "page": "x"})
    assert r.status_code == 429 and r.get_json()["kind"] == "cap" and len(calls) == app_module.LP_READ_DAILY_CAP
    assert client_as("ben@position2.com").post(API + "/read", json={"account": "1112223334", "page": "x"}).status_code == 200


def test_a_read_that_failed_does_not_use_up_the_allowance(monkeypatch):
    _read_client(monkeypatch, error=lp.BadRequest("no"))
    c = client_as(STAFF)
    for _ in range(app_module.LP_READ_DAILY_CAP + 3):
        assert c.post(API + "/read", json={"account": "1112223334", "page": "x"}).status_code == 400
    assert app_module._LP_READ_USES.get(STAFF, (None, 0))[1] == 0
