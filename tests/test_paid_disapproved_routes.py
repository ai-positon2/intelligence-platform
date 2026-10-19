"""Paid Marketing Agents: Disapproved Ads Agent, the page and its routes.

Pinned here: the page is staff-only; every state-changing route refuses a cross-site request; the daily scan is refused without the secret (and always when the
secret is not set) and, with it, needs no session; Scan now starts one scan and refuses a second while one runs; settings are validated and saved; a test alert
goes through the senders; dismissing, reopening and the sheet; and an unavailable store is a 503. Nothing here talks to Google, Meta, Slack or a mail server.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import app as appmod  # noqa: E402
from domains_fakes import Deliver  # noqa: E402
from test_paid_disapproved import G, H, DaStore, item  # noqa: E402
from tracker import paid_disapproved_scan as ds  # noqa: E402

STAFF = "someone@position2.com"
API = "/p2/paid-marketing/api/disapproved"
SAME = {"Origin": "http://localhost"}


def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def post(path, body=None, email=STAFF, headers=SAME):
    return client_as(email).post(API + path, json=body if body is not None else {}, headers=headers)


@pytest.fixture
def world(monkeypatch):
    store, deliver = DaStore(), Deliver()
    bad = {G["id"]: ["1", "2"], H["id"]: []}
    monkeypatch.setattr(appmod, "_da_store", lambda: store)
    monkeypatch.setattr(appmod, "_dom_deliver", lambda: deliver)
    monkeypatch.setattr(appmod, "_dom_inventory", lambda s: ([G, H], [], []))
    monkeypatch.setattr(appmod, "_da_spawn", lambda fn: fn())
    monkeypatch.setattr(ds, "read_account", lambda a, s: ([item(a, i) for i in bad[a["id"]]], {"ads": 0, "assetGroups": 0, "assets": 0}))
    monkeypatch.setenv("DOMAIN_ALERT_SECRET", "s3cret")
    store.state_set("da_settings", {"emails": ["ppc@example.com"], "slackChannel": "C0TEST"})
    return type("W", (), {"store": store, "deliver": deliver, "bad": bad})()


def test_the_page_is_for_staff_only():
    path = "/p2/paid-marketing/disapproved-ads"
    assert client_as(STAFF).get(path).status_code == 200
    assert client_as(None).get(path).status_code == 302
    assert client_as("someone@gmail.com").get(path).status_code == 302
    assert b"disapproved_ads.js" in client_as(STAFF).get(path).data


@pytest.mark.parametrize("path", ["/scan", "/settings", "/test-alert", "/findings/%s/dismiss" % ("a" * 24)])
def test_a_cross_site_request_is_refused(world, path):
    assert client_as(STAFF).post(API + path, json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client_as(None).post(API + path, json={}).status_code in (302, 401, 403)


def test_the_state_needs_a_staff_login(world):
    assert client_as(None).get(API + "/state").status_code == 302
    j = client_as(STAFF).get(API + "/state").get_json()
    assert {"findings", "counts", "settings", "senders", "runs", "progress"} <= set(j)


def test_the_daily_scan_needs_the_secret(world, monkeypatch):
    c = client_as(None)
    assert c.post(API + "/scheduled").status_code == 403
    assert c.post(API + "/scheduled", headers={"X-Domain-Secret": "wrong"}).status_code == 403
    monkeypatch.delenv("DOMAIN_ALERT_SECRET")
    assert c.post(API + "/scheduled", headers={"X-Domain-Secret": ""}).status_code == 403
    assert world.store.state_get("da_runs") is None


def test_the_daily_scan_runs_without_a_session_and_alerts(world):
    r = client_as(None).post(API + "/scheduled", headers={"X-Domain-Secret": "s3cret"})
    assert r.status_code == 202
    run = world.store.state_get("da_runs")[0]
    assert run["trigger"] == "schedule" and run["new"] == 2 and run["alerted"] == 2
    assert len(world.deliver.emails) == 1 and "p2/paid-marketing/disapproved-ads" in world.deliver.emails[0]["text"] and len(world.deliver.slacks) == 1


def test_a_scan_that_is_running_is_not_started_again(world):
    world.store.state_set("da_progress", {"running": True, "startedAt": datetime.now(timezone.utc).isoformat(), "done": 1, "total": 2})
    assert post("/scan").status_code == 409
    r = client_as(None).post(API + "/scheduled", headers={"X-Domain-Secret": "s3cret"})
    assert r.status_code == 200 and r.get_json()["started"] is False and world.store.state_get("da_runs") is None
    world.store.state_set("da_progress", {"running": True, "startedAt": "2020-01-01T00:00:00+00:00", "done": 1, "total": 2})     # a scan that died long ago
    assert post("/scan").status_code == 202 and world.store.state_get("da_runs")[0]["trigger"] == "page by " + STAFF


def test_settings_are_validated_and_saved(world):
    r = post("/settings", {"emails": "a@x.com, b@y.org", "slackChannel": "#sec"})
    assert r.status_code == 200 and world.store.state_get("da_settings") == {"emails": ["a@x.com", "b@y.org"], "slackChannel": "#sec"}
    assert post("/settings", {"emails": "nope"}).status_code == 400
    assert post("/settings", {"emails": "", "slackChannel": "two words"}).status_code == 400


def test_a_test_alert_goes_through_the_senders(world):
    r = post("/test-alert")
    assert r.status_code == 200 and r.get_json()["sent"] is True and world.deliver.emails[0]["subject"].startswith("TEST")


def test_dismissing_reopening_and_the_sheet(world):
    post("/scan")
    fp = next(iter(world.store.da))
    assert post("/findings/%s/dismiss" % fp).status_code == 200 and world.store.da[fp]["status"] == "dismissed"
    assert post("/findings/%s/reopen" % fp).status_code == 200 and world.store.da[fp]["status"] == "open"
    assert post("/findings/nothex/dismiss").status_code == 404
    assert post("/findings/%s/delete" % fp).status_code == 404
    assert post("/findings/%s/dismiss" % ("0" * 24)).status_code == 400
    r = client_as(STAFF).get(API + "/sheet.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.mimetype and r.data[:2] == b"PK" and "disapproved-ads.xlsx" in r.headers["Content-Disposition"]
    assert client_as(None).get(API + "/sheet.xlsx").status_code == 302


def test_an_unavailable_store_is_a_503(monkeypatch):
    class Down(DaStore):
        def state_get(self, key, default=None):
            raise self.StoreUnavailable("no database")
    monkeypatch.setattr(appmod, "_da_store", lambda: Down())
    r = client_as(STAFF).get(API + "/state")
    assert r.status_code == 503 and "unavailable" in r.get_json()["error"]
