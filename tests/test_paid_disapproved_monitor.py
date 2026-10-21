"""Disapproved Ads Tracker: the daily run over every account (tracker/paid_disapproved_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_disapproved as T  # noqa: E402
from tests import test_paid_disapproved_extra as X  # noqa: E402
from tests import test_paid_disapproved_math as MT  # noqa: E402
from tracker import paid_disapproved_math as dm  # noqa: E402
from tracker import paid_disapproved_monitor as dmon  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/disapproved-ads"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="EUR")]
KEYS = [w[0].isoformat() for w in T.WEEKS]


class World:
    def __init__(self):
        self.ads, self.cover, self.weekly, self.keywords, self.fail, self.quota, self.meta = {A["id"]: [], B["id"]: []}, {}, {}, {}, set(), False, []
        self.meta_active = []

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        return T.searcher(self.ads, cover=self.cover, weekly=self.weekly, keywords=self.keywords, fail=self.fail)(cid, q)

    def pages(self, path, params):
        return self.meta_active if params["effective_status"] == '["ACTIVE"]' else self.meta


def run(store, world, deliver, day, accounts=(A, B), hour=7, workers=4):
    spec = dmon.make_spec(today=day, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, meta_pages_fn=world.pages, meta_account_fn=lambda act: {"name": "Meta Co", "currency": "GBP"},
                          now_fn=lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc), workers=workers)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc))


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


DAY = T.TODAY


def d(n):
    return DAY + dt.timedelta(days=n)


def test_the_first_run_sets_the_baseline_sends_nothing_and_keeps_the_days_figures():
    s, dv, w = configured(), Deliver(), World()
    w.ads[A["id"]] = [T.api()]
    r = run(s, w, dv, DAY)
    assert r["status"] == "done" and r["baseline"] is True and r["alerted"] == 0 and not dv.emails and r["read"] == 2
    assert s.snaps_get("disapproved", A["id"], "2026-10-01") == [{"status": "stopped", "stoppedGroups": 1, "disapproved": 1, "keywords": 0, "limited": 0, "day": "2026-10-21"}]
    assert s.snaps_get("disapproved", B["id"], "2026-10-01")[0]["status"] == "clear"


def test_a_group_that_stops_after_the_baseline_is_alerted_once_with_its_figures_and_then_its_age_grows():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, DAY)
    w.ads[A["id"]] = [T.api(ad="1", group="10"), T.api(ad="2", group="10")]
    w.weekly = {A["id"]: [T.week_row("10", KEYS[i], 70, 35_000_000) for i in range(4)]}
    r = run(s, w, dv, d(1))
    assert r["new"] == 1 and r["alerted"] == 1 and dv.emails[0]["subject"] == "HIGH: Disapproved Ads Tracker: 1 new (1 account)"
    text = dv.emails[0]["text"]
    assert "[NEW] Acme Dental (Google Ads)\n    Group 10 cannot show any ad" in text and "made about 70 clicks a week before; last week 0." in text and "Start with: Get the ad groups that cannot show an ad running again." in text
    run(s, w, dv, d(4))
    [row] = [i for i in s.issues_all("disapproved") if i["kind"] == "stopped"]
    assert "Stopped for 3 days." in row["detail"] and row["fingerprint"] == dm.group_fp(A["id"], "10") and row["level"] == "high" and row["data"] == {"group": "10", "campaign": "Core Search", "ads": 2}
    assert len(dv.emails) == 1


def test_a_policy_that_flags_ads_another_ad_covers_is_one_medium_issue_however_many_ads_it_flags():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, DAY)
    w.ads[A["id"]] = [T.api(ad=str(i), group="1%d" % i) for i in range(1, 4)]
    w.cover = {A["id"]: [{"adGroup": {"id": "1%d" % i}} for i in range(1, 4)]}
    r = run(s, w, dv, d(1))
    assert r["new"] == 1 and "[NEW] Acme Dental (Google Ads)\n    3 ads are disapproved for destination not working" in dv.emails[0]["text"] and "Each has another approved one beside it" in dv.emails[0]["text"]
    assert dv.emails[0]["subject"] == "Disapproved Ads Tracker: 1 new (1 account)"                      # medium, so not HIGH
    [i] = s.issues_all("disapproved")
    assert i["fingerprint"] == dm.topic_fp(A["id"], "DESTINATION_NOT_WORKING", True) and i["kind"] == "topic" and i["level"] == "medium"


def test_limited_ads_are_kept_as_low_issues_and_never_alerted_and_keywords_are_one_medium_issue():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, DAY)
    w.ads[A["id"]] = [T.api(approval="APPROVED_LIMITED")]
    w.cover = {A["id"]: [{"adGroup": {"id": "10"}}]}
    w.keywords = {B["id"]: [X.krow(), X.krow("12", text="cheap crowns")]}
    r = run(s, w, dv, d(1))
    rows = {i["kind"]: i for i in s.issues_all("disapproved")}
    assert rows["limited"]["level"] == "low" and rows["limited"]["fingerprint"] == "limited:1234567890:DESTINATION_NOT_WORKING" and rows["limited"]["alertedAt"] is None
    assert rows["keywords"]["title"] == "2 keywords are disapproved" and rows["keywords"]["fingerprint"] == "keywords:2222222222" and r["new"] == 1 and "Beta Clinic" in dv.emails[0]["text"] and "Acme" not in dv.emails[0]["text"]


def test_a_fixed_group_is_resolved_with_how_long_it_was_open_and_a_return_counts_as_coming_back():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, DAY)
    w.ads[A["id"]] = [T.api()]
    run(s, w, dv, d(1))
    w.ads[A["id"]] = []
    r = run(s, w, dv, d(4))
    assert r["resolved"] >= 1 and {i["status"] for i in s.issues_all("disapproved")} == {"resolved"}
    w.ads[A["id"]] = [T.api()]
    r2 = run(s, w, dv, d(6))
    assert r2["new"] == 1 and "has come back 1 time" in dv.emails[-1]["text"]
    [row] = [i for i in s.issues_all("disapproved") if i["kind"] == "stopped"]
    assert row["reopened"] == 1


def test_a_policy_that_came_back_is_named_in_the_next_report_from_the_stored_issues():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, DAY)
    w.ads[A["id"]] = [T.api()]
    run(s, w, dv, d(1))
    w.ads[A["id"]] = []
    run(s, w, dv, d(2))
    w.ads[A["id"]] = [T.api()]
    run(s, w, dv, d(3))
    from tracker import paid_disapproved as dis
    ages = dis.ages_of(s.issues_all("disapproved"))
    assert ages[dm.group_fp(A["id"], "10")]["reopened"] == 1 and list(ages) == [dm.group_fp(A["id"], "10")]          # no policy issue: nothing else covers the group


def test_an_account_google_would_not_answer_is_named_never_called_clean_and_quota_stops_the_rest():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, DAY)
    w.ads[A["id"]] = [T.api()]
    run(s, w, dv, d(1))
    w.fail = {A["id"]}
    r = run(s, w, dv, d(2))
    assert r["unreadableCount"] == 1 and r["unreadable"][0]["accountId"] == A["id"] and r["resolved"] == 0
    w.fail, w.quota = set(), True
    q = run(s, w, dv, d(3), workers=1)
    errs = [u["error"] for u in q["unreadable"]]
    assert q["read"] == 0 and "daily limit" in errs[0] and errs[1] == "The Google Ads daily read budget was used up earlier in this run."


def test_a_spent_google_budget_does_not_stop_the_read_of_a_meta_account():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    w.meta = [X.mad("1", adset="55")]
    r = run(s, w, dv, DAY, accounts=[A, M], workers=1)
    assert r["read"] == 1 and [u["accountId"] for u in r["unreadable"]] == [A["id"]] and s.snaps_get("disapproved", M["id"], "2026-10-01")[0]["status"] == "stopped"


def test_a_meta_ad_account_is_read_with_its_own_pages_and_a_failure_there_costs_only_that_account():
    s, dv, w = configured(), Deliver(), World()
    w.meta = [X.mad("1", adset="55")]
    r = run(s, w, dv, DAY, accounts=[A, M])
    assert r["read"] == 2 and r["baseline"] is True
    snap = s.snaps_get("disapproved", M["id"], "2026-10-01")[0]
    assert snap["status"] == "stopped" and snap["stoppedGroups"] == 1
    w.meta = [X.mad("1", adset="55"), X.mad("2", adset="56")]
    r2 = run(s, w, dv, d(1), accounts=[A, M])
    assert r2["new"] == 1 and "Meta Co (Meta Ads)" in dv.emails[0]["text"] and "Ad set 56 cannot show any ad" in dv.emails[0]["text"]

    def boom(path, params):
        raise RuntimeError("Meta says no")
    s2, w2 = configured(), World()
    spec = dmon.make_spec(today=DAY, search_fn=w2.search, list_fn=lambda: LISTED, mon_store=s2, meta_pages_fn=boom, meta_account_fn=lambda act: {})
    got = spec["collect"]([A, M], [])
    assert got["okIds"] == {A["id"]} and got["unreadable"] == [{"accountId": M["id"], "name": "Meta Co", "platform": "meta", "error": "RuntimeError: Meta says no"}]


def test_a_history_that_cannot_be_read_does_not_stop_the_run():
    class Broken(MemStore):
        def issues_all(self, agent):
            raise RuntimeError("down")
    s = Broken()
    s.state_set("alerts", {"emails": ["a@b.co"], "slackChannel": ""})
    got = dmon.make_spec(today=DAY, search_fn=World().search, list_fn=lambda: LISTED, mon_store=s)["collect"]([A], [])
    assert got["okIds"] == {A["id"]}


def test_the_account_currency_reaches_the_text_and_a_pmax_field_becomes_a_stopped_issue():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, DAY)
    w.ads[B["id"]] = []
    spec_w = World()
    spec_w.ads[B["id"]] = []
    got_assets = {B["id"]: [X.arow(aid="900"), X.arow(aid="901")]}
    spec_w.search = lambda cid, q: T.searcher(spec_w.ads, assets=got_assets, asset_cover={B["id"]: [X.arow()]})(cid, q)
    r = run(s, spec_w, dv, d(1))
    [i] = [x for x in s.issues_all("disapproved") if x["kind"] == "stopped"]
    assert i["title"] == "Summer (headlines) cannot show any ad" and i["fingerprint"] == "group:2222222222:pmax:7:HEADLINE" and r["new"] == 1
    assert i["detail"].startswith("Every enabled item in Summer (headlines) (PMax All) is disapproved, so nothing in it can show: Punctuation and symbols.")


def test_issues_of_names_what_is_stopped_what_is_covered_and_what_is_limited():
    ads = [MT.ad(1, "G1"), MT.ad(2, "G2", "Brand", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED"),)), MT.ad(3, "G3", approval="APPROVED_LIMITED", topics=(("PUNCTUATION_AND_SYMBOLS", "LIMITED"),))]
    a = dm.analyse(ads, {"G1": 0, "G2": 1, "G3": 1}, None, account_id="111")
    got = dmon.issues_of({"id": "111", "name": "Acme", "platform": "google"}, a)
    assert sorted((i["fingerprint"], i["level"], i["kind"]) for i in got) == [("group:111:G1", "high", "stopped"), ("limited:111:PUNCTUATION_AND_SYMBOLS", "low", "limited"), ("topic:111:TRADEMARKS_IN_AD_TEXT", "medium", "topic")]
    topic = [i for i in got if i["kind"] == "topic"][0]
    assert topic["title"] == "1 ad is disapproved for trademarks in ad text" and topic["detail"] == "1 flagged for trademarks in ad text in Brand. Each has another approved one beside it, so its group still shows."
    assert [i for i in dmon.issues_of({"id": "1", "name": "x", "platform": "google"}, dm.analyse([], {}, None))] == []
    unchecked = dmon.issues_of({"id": "1", "name": "x", "platform": "google"}, dm.analyse([MT.ad(1)], None, None))
    assert unchecked[0]["detail"] == "1 flagged for destination not working in Core Search."
    assert dmon.snapshot_of(a) == {"status": "stopped", "stoppedGroups": 1, "disapproved": 2, "keywords": 0, "limited": 1}
