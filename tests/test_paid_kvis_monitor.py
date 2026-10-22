"""Keyword Visibility Tracker: the weekly run over every Google Ads account (tracker/paid_kvis_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_kvis as TK  # noqa: E402
from tests import test_paid_kvis_math as TM  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_kvis as kv  # noqa: E402
from tracker import paid_kvis_math as m  # noqa: E402
from tracker import paid_kvis_monitor as km  # noqa: E402
from tracker import paid_kvis_settings as st  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/keyword-visibility"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="EUR")]
DAY = dt.date(2026, 10, 28)
ACCOUNT = {"id": A["id"], "name": A["name"], "platform": "google"}


def d(n):
    return DAY + dt.timedelta(days=7 * n)                                   # a week on: the next whole week


def weeks_of(day):
    return [a.isoformat() for a, _b in m.week_list(day)]


class World:
    """What Google answers, regenerated for the day of each run: the top share of the one keyword of each account and a competitor from the last whole week."""

    def __init__(self):
        self.day = DAY
        self.top = {A["id"]: 0.6, B["id"]: 0.6}
        self.prior = {A["id"]: None, B["id"]: None}                          # the top share of the weeks before the last one, when it is not the same
        self.rival = {A["id"]: 0.0, B["id"]: 0.0}
        self.fail, self.quota, self.fail_kinds = set(), False, ()

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        wk = weeks_of(self.day)
        weeks = {c: [TK.week_row("9", "101", w, top=self.top[c] if w == wk[-1] or self.prior[c] is None else self.prior[c]) for w in wk] for c in (A["id"], B["id"])}
        auction = {c: [TK.auc_row(week=w, overlap=self.rival[c] if w == wk[-1] else 0.0) for w in wk] for c in (A["id"], B["id"])}
        return TK.searcher(top={A["id"]: [TK.top_row()], B["id"]: [TK.top_row()]}, weeks=weeks, auction=auction, fail=self.fail, fail_kinds=self.fail_kinds)(cid, q)


def run(store, world, deliver, day=DAY, accounts=(A, B), hour=7, workers=4):
    world.day = day
    now = lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc)                    # noqa: E731
    spec = km.make_spec(today=day, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, now_fn=now, workers=workers)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def analysed(keywords, auction=None, config=None):
    return m.analyse({"keywords": keywords, "auction": auction or [], "currency": "USD", "brandIds": []}, config=config or {}, today=TM.TODAY)


def test_a_keyword_that_lost_its_place_is_one_issue_with_its_own_fingerprint_level_and_words():
    ks = [TM.kw("9-101", "dentist quincy", rows_=TM.rows(recent={"top": 0.3}, prior={"top": 0.3}))]
    (i,) = km.issues_of(ACCOUNT, analysed(ks))
    assert (i["fingerprint"], i["level"], i["kind"], i["agent"], i["accountName"]) == ("kvis:1234567890:top_is_low:9-101", "medium", "top_is_low", "kvis", "Acme Dental")
    assert i["title"] == '"dentist quincy" (exact) is at the top of the page 30% of the time' and i["nextStep"].startswith("Start with: If the keyword earns its cost")
    assert i["data"]["keyword"] == "dentist quincy" and i["data"]["campaign"] == "Search Core" and i["data"]["evidence"][0].startswith("Top of the page: 30%")


def test_a_competitor_is_an_issue_by_its_domain_and_the_low_findings_are_counted_once():
    ks = [TM.kw("9-101", "a", qs=3, rows_=TM.rows(recent={"top": 0.45, "lost": 0.45}, prior={"top": 0.45})), TM.kw("9-102", "b", rows_=TM.rows(recent={"top": 0.55, "lost": 0.5}, prior={"top": 0.55}))]
    got = km.issues_of(ACCOUNT, analysed(ks, TM.history("rival.com", 0.3, 0.0)))
    assert [(x["fingerprint"], x["level"], x["kind"]) for x in got] == [("kvis:1234567890:comp_new:rival.com", "medium", "comp_new"), ("kvis:1234567890:qs_low:9-101", "medium", "qs_low"),
                                                                   ("kvis:1234567890:small", "low", "small")]
    assert got[0]["data"]["campaigns"] == ["Search Core"] and got[2]["title"] == "3 small findings to check" and got[2]["data"] == {"findings": 3}
    assert km.issues_of(ACCOUNT, analysed([TM.kw(rows_=TM.rows(recent={"top": 0.6}, prior={"top": 0.6}))])) == []


def test_only_the_worst_findings_are_followed_one_by_one(monkeypatch):
    monkeypatch.setattr(km, "MAX_PER_ACCOUNT", 2)
    ks = [TM.kw("9-%d" % i, "k%d" % i, cost=900.0 - i, rows_=TM.rows(recent={"top": 0.3}, prior={"top": 0.3})) for i in range(4)]
    got = km.issues_of(ACCOUNT, analysed(ks))
    assert [x["fingerprint"] for x in got] == ["kvis:1234567890:top_is_low:9-0", "kvis:1234567890:top_is_low:9-1", "kvis:1234567890:more"]
    assert got[2]["title"] == "2 more things to look at" and got[2]["level"] == "medium" and got[2]["data"] == {"findings": 2}
    one = km.issues_of(ACCOUNT, analysed(ks[:3]))
    assert one[2]["title"] == "1 more thing to look at"


def test_exactly_the_limit_of_findings_makes_no_more_issue(monkeypatch):
    monkeypatch.setattr(km, "MAX_PER_ACCOUNT", 2)
    ks = [TM.kw("9-%d" % i, "k%d" % i, rows_=TM.rows(recent={"top": 0.3}, prior={"top": 0.3})) for i in range(2)]
    assert [x["kind"] for x in km.issues_of(ACCOUNT, analysed(ks))] == ["top_is_low", "top_is_low"]


def test_the_worst_kind_comes_first_whatever_the_keyword_names():
    ks = [TM.kw("9-1", "a", rows_=TM.rows(recent={"top": 0.3}, prior={"top": 0.3})), TM.kw("9-2", "z", rows_=TM.rows(recent={"top": 0.2}, prior={"top": 0.7}))]
    got = km.issues_of(ACCOUNT, analysed(ks))
    assert [(x["level"], x["kind"]) for x in got] == [("high", "is_drop"), ("medium", "top_is_low"), ("medium", "top_is_low")] and got[0]["data"]["keyword"] == "z"


def test_a_high_finding_comes_before_a_medium_one_whatever_their_keys_say():
    k = TM.kw("9-1", "z", qs=4, qs_weeks={TM.OLDEST: 6, TM.RECENT: 4}, rows_=TM.rows(recent={"top": 0.6}, prior={"top": 0.6}))
    got = km.issues_of(ACCOUNT, analysed([k], TM.history("rival.com", 0.3, 0.0)))
    assert [(x["level"], x["kind"]) for x in got] == [("high", "qs_drop"), ("medium", "comp_new"), ("medium", "qs_low")]


def test_the_first_run_sets_the_baseline_sends_nothing_and_keeps_the_weeks_figures():
    s, dv, w = configured(), Deliver(), World()
    w.top[A["id"]] = 0.3
    r = run(s, w, dv, accounts=(A, B, M))
    assert r["status"] == "done" and r["baseline"] is True and r["alerted"] == 0 and not dv.emails and r["read"] == 2 and r["unreadable"] == []         # a Meta account has no keywords and is left alone
    assert s.snaps_get("kvis", A["id"], "2026-10-01") == [{"status": "fix", "keywords": 1, "judged": 1, "high": 0, "medium": 1, "low": 0, "findings": 1, "competitors": 0, "day": "2026-10-28"}]
    assert s.snaps_get("kvis", B["id"], "2026-10-01")[0]["status"] == "clean" and s.snaps_get("kvis", M["id"], "2026-10-01") == []
    assert [i["kind"] for i in s.issues_all("kvis")] == ["top_is_low"]


def test_a_keyword_that_loses_its_place_after_the_baseline_is_alerted_once_and_one_that_recovers_resolves():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.top[A["id"]], w.prior[A["id"]] = 0.3, 0.6
    r = run(s, w, dv, d(1))
    assert r["new"] == 2 and r["alerted"] == 2                                                           # the keyword is under the lowest wanted and has lost 30 points
    assert dv.emails[0]["subject"] == "HIGH: Keyword Visibility Tracker: 2 new (1 account)" and '"dentist quincy" (exact) lost 30 points of top-of-page share' in dv.emails[0]["text"]
    assert dv.emails[0]["text"].endswith("Open the page: " + LINK)
    n = len(dv.emails)
    run(s, w, dv, d(1))
    assert len(dv.emails) == n
    w.top[A["id"]], w.prior[A["id"]] = 0.6, None
    r = run(s, w, dv, d(2))
    assert r["resolved"] == 2 and sorted(i["status"] for i in s.issues_all("kvis")) == ["resolved", "resolved"]


def test_a_new_competitor_is_alerted_by_its_domain():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.rival[A["id"]] = 0.3
    r = run(s, w, dv, d(1))
    assert r["new"] == 1 and "rival.com is a new competitor" in dv.emails[0]["text"] and [i["fingerprint"] for i in s.issues_all("kvis")] == ["kvis:1234567890:comp_new:rival.com"]
    assert s.snaps_get("kvis", A["id"], "2026-10-01")[-1]["competitors"] == 1


def test_what_the_team_saved_is_used_so_a_competitor_ignored_is_not_an_alert():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    st.add_ignore(s, A["id"], "rival.com", "a@b.co")
    w.rival[A["id"]] = 0.3
    assert run(s, w, dv, d(1))["new"] == 0 and s.issues_all("kvis") == []


def test_an_account_that_cannot_be_read_is_named_and_never_resolves_what_was_open():
    s, dv, w = configured(), Deliver(), World()
    w.top[A["id"]] = 0.3
    run(s, w, dv)
    w.fail = {A["id"]}
    r = run(s, w, dv, d(1))
    assert r["unreadable"] == [{"accountId": A["id"], "name": "Acme Dental", "platform": "google", "error": "Account 1234567890 cannot be read."}] and r["resolved"] == 0
    assert [i["status"] for i in s.issues_all("kvis")] == ["open"]


def test_a_read_that_lost_a_part_is_not_checked_on_what_was_left_and_resolves_nothing():
    s, dv, w = configured(), Deliver(), World()
    w.rival[A["id"]] = 0.3
    run(s, w, dv)
    w.fail_kinds = ("auction",)
    r = run(s, w, dv, d(1))
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"], B["id"]] and r["unreadable"][0]["error"] == "Part of the read failed, so the account was not checked: The auction insights could not be read (no auction insights), so that check was skipped."
    assert r["resolved"] == 0 and r["read"] == 0


def test_auction_insights_refused_to_the_app_do_not_make_an_account_unreadable_and_the_keywords_are_still_followed():
    s, dv, w = configured(), Deliver(), World()
    w.fail_kinds = ("auction403",)
    w.top[A["id"]] = 0.3
    r = run(s, w, dv)
    assert r["unreadable"] == [] and r["read"] == 2 and [i["kind"] for i in s.issues_all("kvis")] == ["top_is_low"]


def test_a_spent_quota_stops_the_accounts_after_the_first_and_says_so():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    r = run(s, w, dv, workers=1)
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"], B["id"]] and r["unreadable"][1]["error"].startswith("The Google Ads daily read budget was used up") and r["read"] == 0


def test_one_account_failing_with_an_ordinary_error_does_not_stop_the_next():
    s, dv, w = configured(), Deliver(), World()
    w.fail = {A["id"]}
    r = run(s, w, dv, workers=1)
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"]] and r["read"] == 1


def test_a_team_list_that_cannot_be_read_does_not_stop_the_run():
    class Broken(MemStore):
        def state_get(self, key, default=None):
            if key.startswith("settings:"):
                raise RuntimeError("db down")
            return super().state_get(key, default)
    s, dv, w = Broken(), Deliver(), World()
    w.top[A["id"]] = 0.3
    r = run(s, w, dv)
    assert r["status"] == "done" and [i["kind"] for i in s.issues_all("kvis")] == ["top_is_low"]


def test_the_monitor_is_registered_to_run_on_tuesdays_with_its_page():
    from tracker import paid_monitors as pm
    assert pm.AGENTS["kvis"] == {"label": "Keyword Visibility Tracker", "page": "/p2/paid-marketing/keyword-visibility", "weekdays": (1,)} and pm.link_for("kvis", "https://p.example/") == LINK
    assert km.MAX_PER_ACCOUNT == 200 and kv.AGENT == "kvis"
