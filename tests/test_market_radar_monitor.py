"""Market Radar, Phase 8: weekly updates. The schedule, the "what changed"
update, its delivery, and two consecutive weeks on a real Postgres."""
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_digest as D  # noqa: E402
from tracker import market_radar_deliver as DL  # noqa: E402
from tracker import market_radar_monitor as M  # noqa: E402

# Saturday 10 October 2026, 09:30 UTC
NOW = datetime(2026, 10, 10, 9, 30, tzinfo=timezone.utc)


# == the schedule ==========================================================================

@pytest.mark.parametrize("weekday,hour,expected", [
    (5, 7, datetime(2026, 10, 10, 7, tzinfo=timezone.utc)),     # today, earlier
    (5, 10, datetime(2026, 10, 3, 10, tzinfo=timezone.utc)),    # today, later: last week's
    (0, 7, datetime(2026, 10, 5, 7, tzinfo=timezone.utc)),      # Monday
    (6, 0, datetime(2026, 10, 4, 0, tzinfo=timezone.utc)),      # Sunday
])
def test_the_latest_slot_is_never_in_the_future(weekday, hour, expected):
    assert M.slot_for(NOW, weekday, hour) == expected
    assert M.next_slot(NOW, weekday, hour) == expected + timedelta(days=7)


def test_a_slot_is_due_once_and_only_when_switched_on():
    on = {"monitor": {"enabled": True, "weekday": 5, "hour": 7}}
    assert M.due(on, NOW) == datetime(2026, 10, 10, 7, tzinfo=timezone.utc)
    on["monitor"]["last_slot"] = "2026-10-10T07:00:00+00:00"
    assert M.due(on, NOW) is None
    assert M.due(on, NOW + timedelta(days=7)) is not None
    assert M.due({"monitor": {"enabled": "yes", "weekday": 5, "hour": 7}}, NOW) is None
    assert M.due({}, NOW) is None


class SchedStore:
    def __init__(self, clients, running=0, locked=True):
        self.clients, self.running, self.locked, self.states = clients, running, locked, []

    @contextmanager
    def advisory_lock(self, key):
        assert key == M.LOCK_KEY
        yield self.locked

    def running_collections(self):
        return self.running

    def monitored_clients(self):
        return self.clients

    def set_monitor_state(self, client_id, patch):
        self.states.append((client_id, dict(patch)))


def client(cid, last_slot=None):
    m = {"enabled": True, "weekday": 5, "hour": 7}
    if last_slot:
        m["last_slot"] = last_slot
    return {"client_id": cid, "owner_email": "o@x.example", "settings": {"monitor": m}}


def test_a_tick_starts_one_due_client_and_marks_its_slot_before_starting():
    started = []
    store = SchedStore([client(1, "2026-10-10T07:00:00+00:00"), client(2), client(3)])

    def start(cid, owner):
        assert store.states[-1] == (cid, {"last_slot": "2026-10-10T07:00:00+00:00",
                                          "last_started_at": NOW.isoformat()})
        started.append(cid)
        return 55
    out = M.tick(store=store, now=NOW, start=start)
    assert out == {"started": 2, "run_id": 55, "slot": "2026-10-10T07:00:00+00:00"} and started == [2]
    assert store.states[-1] == (2, {"last_run_id": 55, "last_error": None})


def test_a_tick_waits_for_the_lock_and_for_a_running_collection():
    assert "another worker" in M.tick(store=SchedStore([client(2)], locked=False), now=NOW,
                                       start=lambda *a: pytest.fail("started"))["skipped"]
    assert "running" in M.tick(store=SchedStore([client(2)], running=1), now=NOW,
                               start=lambda *a: pytest.fail("started"))["skipped"]
    assert M.tick(store=SchedStore([]), now=NOW, start=lambda *a: 1) == {"idle": True}


def test_a_start_that_fails_is_recorded_and_the_week_is_not_retried_in_a_loop():
    store = SchedStore([client(2)])

    def boom(*a):
        raise RuntimeError("database gone")
    assert M.tick(store=store, now=NOW, start=boom) == {"failed": 2}
    assert store.states[0][1]["last_slot"] == "2026-10-10T07:00:00+00:00"
    assert "database gone" in store.states[-1][1]["last_error"]


def test_the_scheduler_never_runs_under_tests_or_when_switched_off(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nowhere/x")
    assert M.enabled() is False and M.start_scheduler() is False
    monkeypatch.setenv("MR_MONITOR_DISABLED", "1")
    assert M.enabled() is False


# == the update's words ====================================================================

def pack_with(moves=(), nearby=(), hiring=(), themes=()):
    return {"client": {"name": "Acme Dental", "domain": "acme.example", "one_liner": "Dentist",
                       "archetype": "local_single", "industry": "Dentist", "city": "Austin",
                       "country": "US", "offerings": []},
            "competitors": [{"ref": "C1", "entity_id": 10, "name": "BigChain", "domain": "big.example",
                             "kind": "direct", "status": "confirmed"}],
            "moves": list(moves), "nearby": list(nearby), "entrants": [], "themes": list(themes),
            "rules": [], "hiring": list(hiring), "coverage": []}


MOVE = {"ref": "M1", "event_id": 1, "company": "BigChain", "company_ref": "C1", "type": "acquisition",
        "label": "Acquisition", "status": "completed", "date": "2026-10-08", "title": "BigChain buys Smile Co",
        "severity": "HIGH", "sources": [{"url": "https://news.example/a", "detector": "news",
                                         "publisher": "Pub", "headline": "BigChain buys Smile Co"}]}
HIRE = {"ref": "H1", "company": "BigChain", "company_ref": "C1", "open": 60, "before": 42, "change": 18,
        "places": 3, "functions": [["Clinical", 40]], "senior": [], "read": "2026-10-10"}


class LLM:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def call_json(self, system, user, schema, **kw):
        self.calls.append(dict(kw, user=user))
        a = self.answers[kw["stage"]]
        if isinstance(a, Exception):
            raise a
        return a, {}


class ModelError(Exception):
    kind = "refused"


def test_the_update_cites_real_items_and_the_check_removes_unsupported_lines():
    pack = pack_with(moves=[MOVE], hiring=[HIRE])
    llm = LLM({"digest_write": {"headline": {"text": "BigChain bought Smile Co — a big change.", "cites": ["M1"]},
                                "items": [{"text": "BigChain bought Smile Co.", "so_what": "s", "cites": ["m1"]},
                                          {"text": "BigChain is hiring 18 more people.", "so_what": "s", "cites": ["H1"]},
                                          {"text": "Made up.", "so_what": "s", "cites": ["Z9"]}]},
               "report_check": {"verdicts": [
                   {"id": "summary", "reasoning": "", "unsupported_facts": [], "supported": True},
                   {"id": "watch.0", "reasoning": "", "unsupported_facts": [], "supported": True},
                   {"id": "watch.1", "reasoning": "", "unsupported_facts": ["18 more people"], "supported": False}]}})
    words = D.write(pack, llm=llm)
    assert words["headline"]["text"] == "BigChain bought Smile Co, a big change." and len(words["items"]) == 2
    assert "H1 | BigChain hiring: 60 open roles (was 42 at the previous update)" in llm.calls[0]["user"]
    checked, removed, _ = D.check(words, pack, llm=llm)
    assert [i["text"] for i in checked["items"]] == ["BigChain bought Smile Co."]
    assert removed == [{"where": "item 2", "text": "BigChain is hiring 18 more people. s", "why": "18 more people"}]


def payload(words, status="ok", removed=()):
    return {"status": status, "since": "2026-10-03T07:00:00+00:00", "written_at": "2026-10-10T07:05:00+00:00",
            "client_name": "Acme <Dental>", "pack": pack_with(moves=[dict(MOVE, sources=[
                {"url": "javascript:alert(1)", "detector": "news"}])], hiring=[HIRE]),
            "words": words, "removed": list(removed)}


WORDS = {"headline": {"text": "<b>BigChain</b> bought Smile Co", "cites": ["M1"]},
         "items": [{"text": "BigChain bought <Smile Co>", "so_what": "Watch for ads & offers", "cites": ["M1", "H1"]}]}


def test_the_email_escapes_everything_and_links_only_web_addresses():
    h = D.render_html(payload(WORDS, removed=[{"x": 1}]), 6)
    assert "<b>BigChain</b>" not in h and "&lt;b&gt;BigChain&lt;/b&gt;" in h and "Acme &lt;Dental&gt;" in h
    assert "javascript:" not in h and "BigChain: 60 open roles, was 42" in h
    assert 'href="https://intelligence.position2.com/p2/admin/market-radar/report/6"' in h
    assert "1 were removed" in h
    t = D.render_text(payload(WORDS), 6)
    assert "1. BigChain bought <Smile Co>" in t and "So what: Watch for ads & offers" in t
    assert "Changes since 2026-10-03" in t and "(javascript" not in t


def test_the_slack_message_escapes_its_markup():
    text, blocks = D.render_slack(payload(WORDS), 6)
    body = json.dumps(blocks)
    assert "&lt;Smile Co&gt;" in body and "javascript:" not in body and "&amp; offers" in body
    assert blocks[0]["type"] == "header" and "report/6|Open the full report" in body
    assert text.startswith("Market Radar update for Acme <Dental>:")


# == delivery =============================================================================

class DStore:
    def __init__(self):
        self.delivery = None

    def set_digest_delivery(self, digest_id, delivery):
        self.delivery = (digest_id, delivery)


def test_each_destination_reports_sent_failed_or_not_used():
    st = DStore()
    sent = []
    out = D.deliver(9, 6, payload(WORDS), {"monitor": {"email": ["a@x.example"], "slack_channel": "C0123ABCD"}},
                    store=st, send_email=lambda *a: sent.append(a),
                    send_slack=lambda *a: (_ for _ in ()).throw(RuntimeError("Slack refused: not_in_channel")))
    assert out["email"] == {"status": "sent", "to": ["a@x.example"]}
    assert out["slack"]["status"] == "failed" and "not_in_channel" in out["slack"]["error"]
    assert sent[0][0] == "Market Radar: Acme <Dental>, week of 2026-10-10" and sent[0][3] == ["a@x.example"]
    assert st.delivery[0] == 9 and st.delivery[1]["slack"]["channel"] == "C0123ABCD"
    out = D.deliver(9, 6, payload(WORDS), {}, store=st)
    assert out["email"] == {"status": "not_set_up"} and out["slack"] == {"status": "not_set_up"}


def test_unset_senders_say_so_and_slack_errors_are_named(monkeypatch):
    for k in ("GMAIL_SENDER", "GOOGLE_SA_JSON", "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SLACK_BOT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert DL.ready() == {"email": False, "slack": False}
    assert DL.attempt(lambda: DL.send_email("s", "t", "<p>h</p>", ["a@x.example"]))["status"] == "not_configured"
    assert DL.attempt(lambda: DL.send_slack("C1", "t", []))["status"] == "not_configured"
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    class R:
        ok, status_code, headers = True, 200, {"content-type": "application/json; charset=utf-8"}

        def json(self):
            return {"ok": False, "error": "not_in_channel"}
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: R())
    got = DL.attempt(lambda: DL.send_slack("C1", "t", []))
    assert got["status"] == "failed" and "not_in_channel" in got["error"]
    msg = DL._message("s", "plain", "<p>html</p>", ["a@x.example", "b@x.example"], "me@x.example")
    assert msg["To"] == "a@x.example, b@x.example" and msg.is_multipart()
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]


# == the job ==============================================================================

def test_a_weekly_run_ends_with_the_update_and_a_failing_update_keeps_the_collection():
    from tracker import market_radar_run as mrun
    import tracker.market_radar_store as real
    saved, calls = {}, []
    orig = real.update_run
    real.update_run = lambda run_id, **kw: saved.update(kw)
    stubs = dict(collect=lambda *a, **k: {"companies": []}, radar=lambda *a, **k: {},
                 pulse=lambda *a, **k: {}, signals=lambda *a, **k: {}, report=lambda *a, **k: {})
    try:
        mrun.collect_job(1, 2, "o", monitor="preview",
                         digest=lambda *a, **k: calls.append(k) or {"digest_id": 3}, **stubs)
        assert calls == [{"run_id": 1, "send": False}] and saved["summary"]["digest"] == {"digest_id": 3}
        mrun.collect_job(1, 2, "o", digest=lambda *a, **k: calls.append("no"), **stubs)
        assert calls[-1] != "no"                       # a plain collection writes no update

        def boom(*a, **k):
            raise RuntimeError("writer down")
        mrun.collect_job(1, 2, "o", monitor="send", digest=boom, **stubs)
        assert saved["status"] == "complete" and "writer down" in saved["summary"]["digest"]["error"]
    finally:
        real.update_run = orig
    with pytest.raises(ValueError):
        mrun.start_collect(1, "o", monitor="sometimes", spawn=lambda *a: pytest.fail("spawned"))


def test_the_settings_are_validated():
    from tracker import market_radar_views as views
    ok = views.validate_monitor({"enabled": True, "weekday": "2", "hour": 7,
                                 "email": "A@x.example; b@x.example, a@x.example", "slack_channel": "#market-news"})
    assert ok == {"enabled": True, "weekday": 2, "hour": 7, "email": ["a@x.example", "b@x.example"],
                  "slack_channel": "#market-news"}
    with pytest.raises(views.EditError) as e:
        views.validate_monitor({"enabled": True, "weekday": 9, "hour": "noon", "email": "not-an-email",
                                "slack_channel": "general chat"})
    assert set(e.value.errors) == {"weekday", "hour", "email", "slack_channel"}
    with pytest.raises(views.EditError) as e:
        views.validate_monitor({"enabled": True, "weekday": 0, "hour": 0, "email": [], "slack_channel": ""})
    assert "add an email address or a Slack channel" in e.value.errors["email"]
    assert views.validate_monitor({"enabled": False})["enabled"] is False


# == two consecutive weeks on Postgres =====================================================

from test_market_radar_store import OWNER, OTHER, pg, world  # noqa: E402,F401


def _scored(store, client_id, eid, sev="HIGH", score=9.0):
    store.link_client_events(client_id, [(eid, score, sev, None)])


def test_two_consecutive_updates_each_say_only_what_is_new(pg, world, monkeypatch):
    from tracker import market_radar_views as views
    monkeypatch.setattr(views, "effective_profile", lambda p, s: dict(p or {}, name="Acme Dental"))
    store = pg
    c = world["client"]
    rival = store.upsert_entity("rival.example", name="Rival")
    store.propose_competitor(c, OWNER, rival, "direct", confidence=0.8)
    e1, _ = store.record_event(rival, "news:a", type="acquisition", title="Rival buys Smile Co",
                               source={"url": "https://news.example/a", "detector": "news"}, event_date="2026-10-01")
    _scored(store, c, e1)
    store.save_snapshot(rival, "jobs", {"open": 40, "places": {}}, item_count=40)
    week1 = datetime.now(timezone.utc)

    def writer(user):
        refs = [l.split(" | ")[0] for l in user.splitlines() if l[:1] in "MHN" and " | " in l]
        return {"headline": {"text": "Changes", "cites": refs[:1]},
                "items": [{"text": "Item %s" % r, "so_what": "s", "cites": [r]} for r in refs]}

    class W:
        def call_json(self, system, user, schema, **kw):
            if kw["stage"] == "digest_write":
                return writer(user), {}
            return {"verdicts": []}, {}

    d1, p1 = D.make_digest(c, OWNER, store=store, now=week1, llm=W())
    assert p1["first"] is True and [m["title"] for m in p1["pack"]["moves"]] == ["Rival buys Smile Co"]
    assert p1["pack"]["hiring"] == []                       # no read before "since"
    assert [i["text"] for i in p1["words"]["items"]] == ["Item M1"]

    # Week 2: one new move, one new nearby opening, and the jobs board grew by 20.
    e2, _ = store.record_event(rival, "news:b", type="new_location", title="Rival opens in Round Rock",
                               source={"url": "https://news.example/b", "detector": "news"}, event_date="2026-10-08")
    _scored(store, c, e2, "MEDIUM", 4.0)
    store.record_event(world["entity"], "radar_local:x", type="nearby_opening",
                       title="New nearby: Bright Smiles (dentist, 0.8 km away)",
                       source={"url": "https://bright.example/", "detector": "radar_local"},
                       location={"label": "Bright Smiles", "distance_km": 0.8, "lat": 30.2, "lon": -97.7},
                       summary="its website says now open")
    store.save_snapshot(rival, "jobs", {"open": 60, "places": {"Austin": 2}}, item_count=60)
    d2, p2 = D.make_digest(c, OWNER, store=store, now=datetime.now(timezone.utc), llm=W())
    assert p2["first"] is False and p2["since"][:19] == p1["created_at"][:19]
    assert [m["title"] for m in p2["pack"]["moves"]] == ["Rival opens in Round Rock"]
    assert [n["name"] for n in p2["pack"]["nearby"]] == ["Bright Smiles"]
    assert [(h["before"], h["open"]) for h in p2["pack"]["hiring"]] == [(40, 60)]
    assert sorted(i["text"] for i in p2["words"]["items"]) == ["Item H1", "Item M2", "Item N1"]

    # Week 3: nothing new is said plainly, without a model call.
    class Fail:
        def call_json(self, *a, **k):
            pytest.fail("a quiet week asks no model")
    d3, p3 = D.make_digest(c, OWNER, store=store, now=datetime.now(timezone.utc), llm=Fail())
    assert p3["status"] == "quiet" and "No new competitor moves" in p3["words"]["headline"]["text"]

    view = views.monitor_view(c, OWNER)
    assert [u["status"] for u in view["updates"]] == ["quiet", "ok", "ok"] and view["enabled"] is False
    assert view["email"] == [OWNER]
    assert views.monitor_view(c, OTHER) is None


def test_switching_updates_on_starts_from_the_next_slot(pg, world):
    from tracker import market_radar_views as views
    c = world["client"]
    v = views.save_monitor(c, OWNER, {"enabled": True, "weekday": 5, "hour": 7, "email": [OWNER]}, now=NOW)
    assert v["enabled"] and v["next"].startswith("2026-10-17T07:00")
    (row,) = pg.monitored_clients()
    assert row["client_id"] == c and M.due(row["settings"], NOW) is None
    assert M.due(row["settings"], NOW + timedelta(days=7)) is not None
    pg.set_monitor_state(c, {"last_slot": "2026-10-17T07:00:00+00:00", "last_run_id": 4})
    s = pg.get_client(c, OWNER)["settings"]["monitor"]
    assert s["last_run_id"] == 4 and s["email"] == [OWNER] and s["weekday"] == 5
    views.save_monitor(c, OWNER, {"enabled": False}, now=NOW)
    assert pg.monitored_clients() == []
    with pg.advisory_lock(M.LOCK_KEY) as a:
        with pg.advisory_lock(M.LOCK_KEY) as b:
            assert a is True and b is False
    with pg.advisory_lock(M.LOCK_KEY) as again:
        assert again is True                   # released after the block


def test_the_weekly_update_routes(pg, world, monkeypatch):
    import app as appmod
    from tracker import market_radar_run as mrun
    monkeypatch.setattr(appmod, "ADMIN_EMAILS", set(appmod.ADMIN_EMAILS) | {OWNER, OTHER})
    started = []
    monkeypatch.setattr(mrun, "collect_job", lambda *a, **k: started.append(k))
    real = mrun.start_collect
    monkeypatch.setattr(mrun, "start_collect", lambda cid, email, monitor=None:
                        real(cid, email, monitor=monitor, spawn=lambda f, a: f(*a)))
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": OWNER, "name": "T"}
    base = "/p2/admin/market-radar/api/clients/%d" % world["client"]
    r = c.get(base + "/monitor")
    assert r.status_code == 200 and r.get_json()["enabled"] is False
    r = c.post(base + "/monitor", json={"enabled": True, "weekday": 1, "hour": 8, "email": "bad"})
    assert r.status_code == 400 and "email" in r.get_json()["errors"]
    r = c.post(base + "/monitor", json={"enabled": True, "weekday": 1, "hour": 8, "email": OWNER})
    assert r.status_code == 200 and r.get_json()["next"]
    r = c.post(base + "/update-now", json={"send": False})
    assert r.status_code == 202 and r.get_json()["send"] is False and started == [{"monitor": "preview"}]
    o = appmod.app.test_client()
    with o.session_transaction() as sess:
        sess["google_user"] = {"email": OTHER, "name": "O"}
    assert o.get(base + "/monitor").status_code == 404
    assert o.post(base + "/update-now", json={}).status_code == 404


# == the page =============================================================================

JS = os.path.join(ROOT, "static", "js", "market_radar.js")
EVIL = '<img src=x onerror=alert(1)>"\'&'


def test_the_weekly_update_card_escapes_and_warns():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    m = {"enabled": True, "weekday": 5, "hour": 7, "email": [EVIL], "slack_channel": EVIL,
         "next": "2026-10-17T07:00:00+00:00", "last_error": EVIL, "scheduler": False,
         "senders": {"email": False, "slack": False},
         "updates": [{"id": 1, "created_at": "2026-10-10T07:05:00+00:00", "status": "ok", "headline": EVIL,
                      "items": [{"text": EVIL, "so_what": EVIL}], "removed": 2,
                      "delivery": {"email": {"status": "sent", "to": ["a@x.example"]},
                                   "slack": {"status": "failed", "error": EVIL}, "at": "x"}},
                     {"id": 2, "created_at": "2026-10-03T07:05:00+00:00", "status": "quiet", "items": [],
                      "delivery": {}}]}
    prog = ("globalThis.window = globalThis; require(%s); process.stdout.write(JSON.stringify("
            "globalThis.MR.renderMonitor(%s, null)));") % (json.dumps(JS), json.dumps(m))
    proc = subprocess.run([shutil.which("node"), "-e", prog], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    html = json.loads(proc.stdout)
    assert "<img" not in html and "onerror=alert(1)>" not in html.replace("&gt;", "")
    for frag in ("schedule is switched off on this server", "Email is not set up on the server",
                 "Slack is not set up on the server", "did not start", "Email: Sent", "Slack: Failed",
                 "2 lines were removed by the fact check", "Quiet week", "Preview, not sent",
                 'name="enabled" checked', "Send an update now"):
        assert frag in html, frag


@pytest.mark.parametrize("before,now,moved", [(40, 60, True), (300, 303, False), (300, 320, False), (300, 340, True),
                                              (10, 14, False), (0, 5, True), (60, 40, True)])
def test_only_a_real_change_in_open_roles_is_news(before, now, moved):
    assert D.hiring_moved(before, now) is moved


def test_a_gmail_refusal_falls_back_to_smtp_and_says_what_to_fix(monkeypatch):
    import types
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GMAIL_SENDER", "reports@x.example")
    monkeypatch.setenv("GOOGLE_SA_JSON", "{}")
    from google.oauth2 import service_account

    def refuse(*a, **k):
        raise RuntimeError("('unauthorized_client: Client is unauthorized to retrieve access tokens', {})")
    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", refuse)
    got = DL.attempt(lambda: DL.send_email("s", "t", "<p>h</p>", ["a@x.example"]))
    assert got["status"] == "failed" and "domain-wide delegation" in got["error"]
    assert "reports@x.example" in got["error"] and "SMTP is not set up" in got["error"]
    sent = []
    monkeypatch.setenv("SMTP_HOST", "smtp.x.example")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASS", "p")
    monkeypatch.setattr(DL, "_smtp", lambda *a: sent.append(a))
    assert DL.attempt(lambda: DL.send_email("s", "t", "<p>h</p>", ["a@x.example"])) == {"status": "sent"}
    assert sent[0][3] == ["a@x.example"]
