"""Brand Spend Alert: the daily run over every account (tracker/paid_brandspend_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_brandspend as T  # noqa: E402
from tracker import paid_brandspend_monitor as bm  # noqa: E402
from tracker import paid_brandspend_settings as st  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/brand-spend"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
LISTED = [dict(A, currency="USD"), dict(B, currency="USD")]
USUAL = T.USUAL


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    from tracker import paid_geo, paid_store
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield
    paid_geo.clear_cache()
    base.forget("")


class World:
    def __init__(self):
        self.rows = {A["id"]: T.weeks_of(weekly=USUAL + [203]), B["id"]: T.weeks_of(cid="22", weekly=USUAL + [203])}
        self.terms, self.fail, self.quota, self.today = {}, set(), False, T.TODAY

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        got = T.searcher(self.rows, terms=self.terms, fail=self.fail)(cid, q)
        if "segments.date" in q and "FROM campaign WHERE" in q and got:                          # the days of this week that have happened, at the same pace as before
            monday = self.today - dt.timedelta(days=self.today.weekday())
            camp = got[0]["campaign"]
            got = got + [T.api(camp["id"], camp["name"], (monday + dt.timedelta(days=d)).isoformat(), "29000000", "10") for d in range(self.today.weekday())]
        return got


def run(store, world, deliver, today, accounts=(A, B), settings=None, hour=7, listed=None, workers=4):
    world.today = today
    spec = bm.make_spec(today=today, search_fn=world.search, list_fn=lambda: listed or LISTED, settings=settings, mon_store=store, workers=workers)
    at = dt.datetime.combine(today, dt.time(hour), tzinfo=dt.timezone.utc)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=lambda: at)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def test_the_first_run_sets_the_baseline_and_keeps_the_figures_of_the_day():
    s, dv, w = configured(), Deliver(), World()
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [301])
    r = run(s, w, dv, T.TODAY)
    assert r["status"] == "done" and r["baseline"] is True and r["alerted"] == 0 and not dv.emails and r["read"] == 2 and r["issues"] >= 1
    snaps = s.snaps_get("brandspend", A["id"], "2026-10-01")
    assert snaps == [{"status": "over", "cost": 301.0, "ratio": 1.4828, "level": "medium", "share": 0.9, "usual": 203.0, "day": "2026-10-20"}]
    assert s.snaps_get("brandspend", B["id"], "2026-10-01")[0]["status"] == "ok"


def test_a_problem_that_turns_up_after_the_baseline_is_alerted_once_and_names_the_account_and_the_figures():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, T.TODAY)
    w.rows[B["id"]] = T.weeks_of(cid="22", weekly=USUAL + [301])
    r = run(s, w, dv, T.TODAY + dt.timedelta(days=1))
    assert r["new"] == 1 and r["alerted"] == 1 and len(dv.emails) == 1 and dv.emails[0]["subject"] == "Brand Spend Alert: 1 new (1 account)"
    text = dv.emails[0]["text"]
    assert "[NEW] Beta Clinic (Google Ads)" in text and "Brand spend is $301, above the usual $203" in text and "48% above a usual week of $203" in text and "Start with: " in text and "Acme" not in text
    assert run(s, w, dv, T.TODAY + dt.timedelta(days=2))["alerted"] == 0 and len(dv.emails) == 1


def test_the_same_problem_getting_worse_is_alerted_again_and_a_fixed_one_is_said_fixed():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, T.TODAY)
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [301])
    run(s, w, dv, T.TODAY + dt.timedelta(days=1))
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [329])
    r = run(s, w, dv, T.TODAY + dt.timedelta(days=2))
    assert r["worse"] == 1 and "[WORSE THAN BEFORE] Acme Dental" in dv.emails[-1]["text"] and dv.emails[-1]["subject"].startswith("HIGH: ")
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [203])
    r2 = run(s, w, dv, T.TODAY + dt.timedelta(days=3))
    assert r2["resolved"] >= 1 and r2["alerted"] == 0 and {x["status"] for x in s.issues_all("brandspend")} == {"resolved"}


def test_the_history_grows_a_day_at_a_time_and_the_streak_reaches_the_page_and_the_alert():
    s, dv, w = configured(), Deliver(), World()
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [301])
    for n in range(3):
        run(s, w, dv, T.TODAY + dt.timedelta(days=n))
    assert [x["day"] for x in s.snaps_get("brandspend", A["id"], "2026-10-01")] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    detail = [i for i in s.issues_all("brandspend") if i["kind"] == "over"][0]["detail"]
    assert "outside its band for 2 days in a row (since 2026-10-20)" in detail                  # the third run had two days behind it


def test_the_saved_marks_limits_and_brand_words_are_used_by_the_run():
    s, dv, w = configured(), Deliver(), World()
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [301])
    settings = {"marks": {"11": False}, "accounts": {A["id"]: {"weeklyMax": 250}}, "limit": None}
    run(s, w, dv, T.TODAY, settings=settings)
    assert not [i for i in s.issues_all("brandspend") if i["accountId"] == A["id"] and i["kind"] == "over"]       # campaign 11 is not counted: no brand campaign
    w2, s2 = World(), configured()
    w2.rows[A["id"]] = T.weeks_of(weekly=USUAL + [301])
    run(s2, w2, dv, T.TODAY, settings={"accounts": {A["id"]: {"weeklyMax": 250}}})
    assert "limit-over" in [i["kind"] for i in s2.issues_all("brandspend")]
    w3, s3 = World(), configured()
    w3.terms = {A["id"]: [T.term_row("12", "NonBrand", "acme dental near me", "300000000", "60", "2")]}
    run(s3, w3, dv, T.TODAY, settings={"accounts": {A["id"]: {"brandTerms": ["acme"]}}})
    assert "leak" in [i["kind"] for i in s3.issues_all("brandspend")]


def test_an_account_google_would_not_answer_is_named_and_never_called_clean_and_quota_stops_the_rest():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, T.TODAY)
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [301])
    run(s, w, dv, T.TODAY + dt.timedelta(days=1))
    w.fail = {A["id"]}
    r = run(s, w, dv, T.TODAY + dt.timedelta(days=2))
    assert r["unreadableCount"] == 1 and r["unreadable"][0]["accountId"] == A["id"] and r["resolved"] == 0 and "cannot be read" in r["unreadable"][0]["error"]
    w.fail, w.quota = set(), True
    q = run(s, w, dv, T.TODAY + dt.timedelta(days=3), accounts=[A])
    assert q["unreadableCount"] == 1 and q["read"] == 0 and "daily limit" in q["unreadable"][0]["error"]


def test_once_the_daily_budget_of_reads_is_spent_the_rest_of_the_run_does_not_ask_google_again():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    r = run(s, w, dv, T.TODAY, workers=1)
    assert r["unreadableCount"] == 2 and r["read"] == 0
    errs = [u["error"] for u in r["unreadable"]]
    assert "The daily limit is used up." in errs[0] and errs[1] == "The Google Ads daily read budget was used up earlier in this run."


def test_the_teams_limit_and_the_accounts_currency_are_used():
    s, dv, w = configured(), Deliver(), World()
    w.rows[A["id"]] = T.weeks_of(weekly=USUAL + [301])
    run(s, w, dv, T.TODAY, settings={"limit": 0.6})
    assert not [i for i in s.issues_all("brandspend") if i["kind"] == "over"]
    s2 = configured()
    run(s2, w, dv, T.TODAY, listed=[dict(A, currency="EUR"), dict(B, currency="EUR")])
    assert [i["title"] for i in s2.issues_all("brandspend") if i["kind"] == "over"] == ["Brand spend is \u20ac301, above the usual \u20ac203"]


def test_only_google_accounts_are_read_here():
    s, dv, w = configured(), Deliver(), World()
    meta = {"id": "act_1", "name": "Meta thing", "platform": "meta", "group": ""}
    r = run(s, w, dv, T.TODAY, accounts=[A, meta])
    assert r["accounts"] == 2 and r["read"] == 1 and r["unreadableCount"] == 0 and s.snaps_get("brandspend", "act_1", "2000-01-01") == []


def test_a_finding_becomes_an_issue_with_a_fingerprint_that_is_the_same_every_day_and_information_is_left_out():
    from tests.test_paid_brandspend_notes import rows, go
    a = go(rows([200] * 8, overrides={"2026-10-07": (150.0, 40.0, 0.0)}), extra={"campaigns": [{"id": "11", "name": "P2_Brand", "status": "ENABLED"}, {"id": "50", "name": "Brand Exact", "status": "ENABLED"}]})
    issues = bm.issues_of({"id": "111", "name": "Acme", "platform": "google"}, a)
    assert sorted(i["fingerprint"] for i in issues) == ["111:silent:50", "111:spike:2026-10-07"] and {i["level"] for i in issues} == {"medium"}
    assert all(i["agent"] == "brandspend" and i["accountName"] == "Acme" and i["data"]["weekFrom"] == "2026-10-12" for i in issues)
    none = go(rows([200] * 8, name="Core"))
    assert none["status"] == "none" and bm.issues_of({"id": "1", "name": "x", "platform": "google"}, none) == []
    assert bm.fingerprint("9", {"kind": "over"}) == "9:over" and bm.fingerprint("9", {"kind": "spike", "key": "2026-10-14"}) == "9:spike:2026-10-14"
