"""KPI Monitoring: the daily run over every account (tracker/paid_kpi_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_kpi as T  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_kpi as k  # noqa: E402
from tracker import paid_kpi_monitor as km  # noqa: E402
from tracker import paid_kpi_settings as st  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/kpi-monitoring"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="EUR")]
DAY = dt.date(2026, 10, 26)
TARGET = {"targetCpa": {"targetCpaMicros": "40000000"}}


def d(n):
    return DAY + dt.timedelta(days=n)


def rows(cid, day, cost, conv, **kw):
    """Fourteen days of rows counted back from `day` (the clock of the run), as T.day_rows counts them from T.TODAY."""
    out = T.day_rows(cid, cost=cost, conv=conv, **kw)
    shift = (day - T.TODAY).days
    for r in out:
        r["segments"]["date"] = (dt.date.fromisoformat(r["segments"]["date"]) + dt.timedelta(days=shift)).isoformat()
    return out


class World:
    """What Google answers: per account a campaign list and the cost and conversions a day, regenerated for the day of each run."""

    def __init__(self):
        self.day = DAY
        self.camps = {A["id"]: [T.camp_row(**TARGET)], B["id"]: [T.camp_row(**TARGET)]}
        self.cost = {A["id"]: 40.0, B["id"]: 40.0}
        self.conv = {A["id"]: 1.0, B["id"]: 1.0}
        self.fail, self.quota, self.meta_cost = set(), False, 40.0

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        days = {c: rows("5", self.day, self.cost[c], self.conv[c]) for c in (A["id"], B["id"])}
        return T.searcher(camps=self.camps, days=days, fail=self.fail)(cid, q)

    def pages(self, path, params):
        meta = [T.meta_rows(day=(self.day - dt.timedelta(days=i)).isoformat(), spend=str(self.meta_cost), leads="1") for i in range(1, 15)]
        return T.meta_pages(insights=meta)(path, params)


def run(store, world, deliver, day=DAY, accounts=(A, B), hour=7, workers=4):
    world.day = day
    now = lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc)                    # noqa: E731
    spec = km.make_spec(today=day, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, meta_pages_fn=world.pages, meta_account_fn=lambda act: {"name": "Meta Co", "currency": "GBP"},
                        now_fn=now, workers=workers)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def analysed(campaigns, config=None, account=A):
    from tracker import paid_kpi_math as m
    return m.analyse({"campaigns": campaigns, "currency": "USD"}, config=config or {}, today=T.TODAY)


def camps_of(rows_, **kw):
    out = []
    for cid, name, cost, conv in rows_:
        days = [{"date": (T.TODAY - dt.timedelta(days=i)).isoformat(), "impr": 2000, "clicks": 200, "cost": cost, "conv": conv, "value": 0.0} for i in range(1, 15)]
        out.append(dict({"id": cid, "name": name, "primaryStatus": "ELIGIBLE", "days": days}, **kw))
    return out


ACCOUNT = {"id": A["id"], "name": A["name"], "platform": "google"}


def test_an_off_target_campaign_is_one_issue_with_its_own_fingerprint_level_and_words():
    issues = km.issues_of(ACCOUNT, analysed(camps_of([("5", "Search Core", 100.0, 1.0)]), {"targets": {"cpa": 40}}))
    (i,) = issues
    assert (i["fingerprint"], i["level"], i["kind"], i["agent"], i["accountName"]) == ("kpi:1234567890:cpa:5", "high", "cpa", "kpi", "Acme Dental")
    assert i["title"] == "Search Core costs $100 a conversion against a target of $40.00"
    assert i["nextStep"].startswith("Start with: Look at what spent without converting")
    assert i["data"]["campaign"] == "Search Core" and i["data"]["campaignId"] == "5" and i["data"]["evidence"][0] == "Last 7 days: $100 (target $40.00, saved for the account)"


def test_a_learning_campaign_is_its_own_issue_even_when_low_and_the_other_lows_are_counted_once():
    campaigns = camps_of([("1", "Learner", 40.0, 2.0)], systemStatus="LEARNING_NEW") + camps_of([("2", "Slow", 40.0, 1.0)])
    for c in campaigns:
        for dd in c["days"]:
            dd["clicks"] = 100
    got = km.issues_of(ACCOUNT, analysed(campaigns, {"targets": {"cvr": 1.5}}))
    assert [(i["fingerprint"], i["level"], i["kind"]) for i in got] == [("kpi:1234567890:learning:1", "low", "learning"), ("kpi:1234567890:small", "low", "small")]
    assert got[0]["data"]["campaignId"] == "1" and got[1]["title"] == "1 small finding to check" and got[1]["data"] == {"findings": 1}
    assert km.issues_of(ACCOUNT, analysed(camps_of([("1", "Fine", 40.0, 1.0)]), {"targets": {"cpa": 50}})) == []


def test_only_the_worst_findings_are_followed_one_by_one(monkeypatch):
    monkeypatch.setattr(km, "MAX_PER_ACCOUNT", 2)
    campaigns = camps_of([(str(i), "C%d" % i, 100.0, 1.0) for i in range(1, 5)])
    got = km.issues_of(ACCOUNT, analysed(campaigns, {"targets": {"cpa": 40}}))
    assert [i["fingerprint"] for i in got] == ["kpi:1234567890:cpa:1", "kpi:1234567890:cpa:2", "kpi:1234567890:more"]
    assert got[2]["title"] == "2 more campaigns off target or held back" and got[2]["level"] == "medium" and got[2]["data"] == {"findings": 2}
    one = km.issues_of(ACCOUNT, analysed(camps_of([(str(i), "C%d" % i, 100.0, 1.0) for i in range(1, 4)]), {"targets": {"cpa": 40}}))
    assert one[2]["title"] == "1 more campaign off target or held back"


def test_exactly_the_limit_of_findings_makes_no_more_issue(monkeypatch):
    monkeypatch.setattr(km, "MAX_PER_ACCOUNT", 2)
    got = km.issues_of(ACCOUNT, analysed(camps_of([("1", "C1", 100.0, 1.0), ("2", "C2", 100.0, 1.0)]), {"targets": {"cpa": 40}}))
    assert [i["kind"] for i in got] == ["cpa", "cpa"]


def test_the_worst_kind_comes_first_whatever_the_campaign_names():
    campaigns = camps_of([("1", "A", 55.0, 1.0)]) + camps_of([("2", "Z", 100.0, 1.0)])
    got = km.issues_of(ACCOUNT, analysed(campaigns, {"targets": {"cpa": 40}}))
    assert [(i["level"], i["data"]["campaign"]) for i in got] == [("high", "Z"), ("medium", "A")]


def test_the_first_run_sets_the_baseline_sends_nothing_and_keeps_the_days_figures():
    s, dv, w = configured(), Deliver(), World()
    w.cost[A["id"]] = 100.0
    r = run(s, w, dv)
    assert r["status"] == "done" and r["baseline"] is True and r["alerted"] == 0 and not dv.emails and r["read"] == 2
    assert s.snaps_get("kpi", A["id"], "2026-10-01") == [{"status": "fix", "campaigns": 1, "high": 1, "medium": 0, "low": 0, "findings": 1, "offTarget": 1, "onTarget": 0, "day": "2026-10-26"}]
    assert s.snaps_get("kpi", B["id"], "2026-10-01")[0]["status"] == "clean" and s.snaps_get("kpi", B["id"], "2026-10-01")[0]["onTarget"] == 1
    assert [i["kind"] for i in s.issues_all("kpi")] == ["cpa"]


def test_a_campaign_off_target_after_the_baseline_is_alerted_once_and_one_back_on_target_resolves():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.cost[A["id"]] = 100.0
    r = run(s, w, dv, d(1))
    assert r["new"] == 1 and r["alerted"] == 1 and dv.emails[0]["subject"] == "HIGH: KPI Monitoring: 1 new (1 account)" and "Search Core costs $100 a conversion against a target of $40.00" in dv.emails[0]["text"]
    assert dv.emails[0]["text"].endswith("Open the page: " + LINK)
    n = len(dv.emails)
    run(s, w, dv, d(1))
    assert len(dv.emails) == n                                                          # the same finding again is not news
    w.cost[A["id"]] = 40.0
    r = run(s, w, dv, d(2))
    assert r["resolved"] == 1 and [i["status"] for i in s.issues_all("kpi")] == ["resolved"]


def test_a_campaign_learning_for_two_weeks_is_alerted_as_stuck_by_the_age_of_its_own_issue():
    s, dv, w = configured(), Deliver(), World()
    w.camps[A["id"]] = [T.camp_row(system="LEARNING_NEW", **TARGET)]
    run(s, w, dv)
    assert [(i["kind"], i["level"]) for i in s.issues_all("kpi")] == [("learning", "low")]
    r = run(s, w, dv, d(13))
    assert r["alerted"] == 0 and [i["level"] for i in s.issues_all("kpi")] == ["low"]                 # 13 days: still learning
    r = run(s, w, dv, d(14))
    assert r["alerted"] == 1 and [i["level"] for i in s.issues_all("kpi")] == ["medium"] and "Search Core is stuck in learning" in dv.emails[0]["text"]
    assert "It has been learning for 14 days." in dv.emails[0]["text"]


def test_what_the_team_saved_is_used_so_a_target_can_make_a_campaign_off_it_and_a_muted_one_is_left_out():
    s, dv, w = configured(), Deliver(), World()
    w.camps[A["id"]] = [T.camp_row()]                                                    # no strategy target
    run(s, w, dv)
    assert s.issues_all("kpi") == []
    st.save_account(s, A["id"], A["name"], {"targets": {"cpa": 20}}, "a@b.co")
    r = run(s, w, dv, d(1))
    assert r["new"] == 1 and [i["kind"] for i in s.issues_all("kpi")] == ["cpa"]
    st.mute(s, A["id"], "5", "Search Core", "being replaced", "a@b.co")
    r = run(s, w, dv, d(2))
    assert r["resolved"] == 1 and [i["status"] for i in s.issues_all("kpi")] == ["resolved"]


def test_an_account_that_cannot_be_read_is_named_and_never_resolves_what_was_open():
    s, dv, w = configured(), Deliver(), World()
    w.cost[A["id"]] = 100.0
    run(s, w, dv)
    w.fail = {A["id"]}
    r = run(s, w, dv, d(7))
    assert r["unreadable"] == [{"accountId": A["id"], "name": "Acme Dental", "platform": "google", "error": "Account 1234567890 cannot be read."}] and r["resolved"] == 0
    assert [i["status"] for i in s.issues_all("kpi")] == ["open"]


def test_a_spent_quota_stops_the_google_accounts_after_the_first_and_says_so_but_meta_is_still_read():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    w.meta_cost = 100.0
    r = run(s, w, dv, accounts=(A, B, M), workers=1)
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"], B["id"]] and r["unreadable"][1]["error"].startswith("The Google Ads daily read budget was used up") and r["read"] == 1
    assert s.issues_all("kpi") == []                                                    # the Meta campaign has no target saved: read and clean
    st.save_account(s, M["id"], M["name"], {"targets": {"cpa": 40}}, "a@b.co")
    run(s, w, dv, d(1), accounts=(A, B, M), workers=1)
    (i,) = s.issues_all("kpi")
    assert (i["accountId"], i["kind"], i["platform"], i["level"]) == ("act_1234567", "cpa", "meta", "high") and "Leads costs £100 a conversion" in i["detail"]


def test_a_team_list_that_cannot_be_read_does_not_stop_the_run():
    class Broken(MemStore):
        def state_get(self, key, default=None):
            if key.startswith("settings:"):
                raise RuntimeError("db down")
            return super().state_get(key, default)
    s, dv, w = Broken(), Deliver(), World()
    w.cost[A["id"]] = 100.0
    r = run(s, w, dv)
    assert r["status"] == "done" and [i["kind"] for i in s.issues_all("kpi")] == ["cpa"]


def test_the_history_that_cannot_be_read_leaves_learning_as_only_learning():
    class NoHistory(MemStore):
        def issues_all(self, agent):
            if agent == "kpi" and getattr(self, "armed", False):
                raise RuntimeError("db down")
            return super().issues_all(agent)
    s, dv, w = NoHistory(), Deliver(), World()
    w.camps[A["id"]] = [T.camp_row(system="LEARNING_NEW", **TARGET)]
    s.armed = False
    run(s, w, dv)
    s.armed = True
    w.day = d(20)
    got = km.make_spec(today=d(20), search_fn=w.search, list_fn=lambda: LISTED, mon_store=s, now_fn=lambda: dt.datetime.combine(d(20), dt.time(7), tzinfo=dt.timezone.utc))["collect"]([A], [])
    assert [(i["kind"], i["level"]) for i in got["issues"]] == [("learning", "low")]                  # twenty days on, but without the history it is only learning


def test_one_account_failing_with_an_ordinary_error_does_not_stop_the_next():
    s, dv, w = configured(), Deliver(), World()
    w.fail = {A["id"]}
    r = run(s, w, dv, workers=1)
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"]] and r["read"] == 1 and r["unreadable"][0]["error"] == "Account 1234567890 cannot be read."


def test_the_monitor_is_registered_for_the_daily_run_and_its_page():
    from tracker import paid_monitors as pm
    assert pm.AGENTS["kpi"] == {"label": "KPI Monitoring", "page": "/p2/paid-marketing/kpi-monitoring"} and pm.link_for("kpi", "https://p.example/") == LINK
    assert km.MAX_PER_ACCOUNT == 200                                                    # the page lists every finding; the alerts follow the worst 200 of an account


def test_a_target_in_the_master_agent_is_used_and_an_agents_own_target_wins():
    from tracker import paid_master
    s, dv, w = configured(), Deliver(), World()
    w.camps[A["id"]] = [T.camp_row()]
    run(s, w, dv)
    paid_master.save_client(s, "", {"name": "Acme", "accounts": [{"id": A["id"], "targets": {"cpa": 20}}]}, "a@b.co")
    r = run(s, w, dv, d(1))
    assert r["new"] == 1 and [i["kind"] for i in s.issues_all("kpi")] == ["cpa"]
    st.save_account(s, A["id"], A["name"], {"targets": {"cpa": 100000}}, "a@b.co")             # the team set this agent's own target on purpose: it wins
    r = run(s, w, dv, d(2))
    assert r["resolved"] == 1 and [i["status"] for i in s.issues_all("kpi")] == ["resolved"]
