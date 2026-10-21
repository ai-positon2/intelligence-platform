"""Disapproved Ads Tracker: reading Google's answer, the cover and the weight, an MCC, the report and its cache (tracker/paid_disapproved.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_disapproved as d  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 21)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}
WEEKS = d.weeks(TODAY)


def api(ad="1", group="10", camp="Core Search", approval="DISAPPROVED", review="REVIEWED", topics=(("DESTINATION_NOT_WORKING", "PROHIBITED", ["Click here!!!"]),), heads=("Dentist near you", "Book today"), url="https://example.com/a", atype="RESPONSIVE_SEARCH_AD"):
    entries = [{"topic": t, "type": ty, "evidences": [{"textList": {"texts": ev}}]} for t, ty, ev in topics]
    return {"campaign": {"id": "5", "name": camp}, "adGroup": {"id": group, "name": "Group " + group},
            "adGroupAd": {"ad": {"id": ad, "type": atype, "finalUrls": [url], "responsiveSearchAd": {"headlines": [{"text": h} for h in heads]}},
                          "policySummary": {"approvalStatus": approval, "reviewStatus": review, "policyTopicEntries": entries}}}


def week_row(group, week, clicks, cost_micros):
    return {"adGroup": {"id": group}, "segments": {"week": week}, "metrics": {"clicks": str(clicks), "costMicros": str(cost_micros)}}


def searcher(ads=None, cover=None, weekly=None, log=None, fail=(), fail_cover=False, fail_weekly=False):
    ads = ads if ads is not None else {ACCT["id"]: [api()]}

    def search(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        if "segments.week" in q:
            if fail_weekly:
                raise gads.GadsError("api", "no weeks")
            return (weekly or {}).get(cid, [])
        if "'APPROVED'" in q:
            if fail_cover:
                raise gads.GadsError("api", "no cover")
            return (cover or {}).get(cid, [])
        return ads.get(cid, [])
    return search


# ── Reading the answer ──────────────────────────────────────────────────────

def test_a_flagged_ad_is_read_into_the_shape_the_arithmetic_wants():
    a = d.read_ads([api()])[0]
    assert a == {"id": "1", "campaignId": "5", "campaign": "Core Search", "adGroupId": "10", "adGroup": "Group 10", "type": "RESPONSIVE_SEARCH_AD", "approval": "DISAPPROVED", "review": "REVIEWED",
                 "topics": [{"t": "DESTINATION_NOT_WORKING", "ty": "PROHIBITED", "ev": ["Click here!!!"]}], "headlines": ["Dentist near you", "Book today"], "url": "https://example.com/a"}


def test_topics_are_not_repeated_evidence_is_three_at_most_and_a_missing_piece_is_empty():
    row = api(topics=(("A_TOPIC", "limited", ["x", "x", "y", "z", "w"]), ("A_TOPIC", "LIMITED", ["q"]), ("B_TOPIC", "", [])), heads=("a", "b", "c", "d", ""))
    a = d.read_ads([row])[0]
    assert a["topics"] == [{"t": "A_TOPIC", "ty": "LIMITED", "ev": ["x", "y", "z"]}, {"t": "B_TOPIC", "ty": "", "ev": []}] and a["headlines"] == ["a", "b", "c"]
    bare = {"adGroup": {"id": "10", "name": "G"}, "campaign": {"id": "5"}, "adGroupAd": {"ad": {"id": "1"}, "policySummary": {"approvalStatus": "APPROVED_LIMITED"}}}
    b = d.read_ads([bare])[0]
    assert b["topics"] == [] and b["headlines"] == [] and b["url"] == "" and b["campaign"] == "" and b["review"] == "" and b["type"] == ""


def test_a_row_without_an_ad_or_an_ad_group_is_dropped():
    rows = [api(), {"adGroup": {"id": "10"}, "adGroupAd": {"ad": {}}}, {"adGroupAd": {"ad": {"id": "9"}}}, {}]
    assert [a["id"] for a in d.read_ads(rows)] == ["1"]


def test_the_ads_query_asks_for_enabled_ads_that_are_disapproved_or_limited_with_their_policy_and_text():
    q = d.GAQL_ADS
    assert "FROM ad_group_ad" in q and "campaign.status = ENABLED" in q and "ad_group.status = ENABLED" in q and "ad_group_ad.status = ENABLED" in q
    assert "IN ('DISAPPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY')" in q
    for f in ("policy_summary.approval_status", "policy_summary.review_status", "policy_summary.policy_topic_entries", "ad_group_ad.ad.final_urls", "responsive_search_ad.headlines", "ad_group.name", "campaign.name"):
        assert f in q


def test_the_cover_query_counts_approved_enabled_ads_of_the_named_groups_and_the_weekly_query_is_by_week():
    q = d.gaql_cover(["10", "11"])
    assert "ad_group.id IN (10, 11)" in q and "IN ('APPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY')" in q and "ad_group_ad.status = ENABLED" in q
    w = d.gaql_weekly(["10"], WEEKS[0][0], WEEKS[-1][1])
    assert "FROM ad_group" in w and "segments.week" in w and "BETWEEN '2026-09-14' AND '2026-10-18'" in w and "metrics.clicks" in w and "metrics.cost_micros" in w and "ad_group.id IN (10)" in w


def test_the_cover_counts_ads_per_group_and_the_weeks_fill_the_gaps_with_nothing():
    assert d.read_cover([{"adGroup": {"id": "10"}}, {"adGroup": {"id": "10"}}, {"adGroup": {"id": "11"}}, {}]) == {"10": 2, "11": 1}
    keys = [w[0].isoformat() for w in WEEKS]
    got = d.read_weekly([week_row("10", keys[0], 100, 50_000_000), week_row("10", keys[0], 10, 5_000_000), week_row("10", keys[3], 7, 1_000_000), week_row("10", "2020-01-06", 999, 1), week_row("", keys[1], 1, 1)], WEEKS)
    assert got == {"10": [{"clicks": 110.0, "cost": 55.0}, {"clicks": 0.0, "cost": 0.0}, {"clicks": 0.0, "cost": 0.0}, {"clicks": 7.0, "cost": 1.0}, {"clicks": 0.0, "cost": 0.0}]}


# ── An account and an MCC ───────────────────────────────────────────────────

def test_an_account_with_no_flagged_ad_is_read_once_and_asks_nothing_more():
    log = []
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": []}, log=log))
    assert r == {"ads": [], "cover": {}, "weekly": {}, "warnings": [], "truncated": False} and len(log) == 1


def test_the_cover_and_the_weeks_are_read_only_for_the_groups_with_a_flagged_ad():
    log = []
    keys = [w[0].isoformat() for w in WEEKS]
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": [api(ad="1", group="10"), api(ad="2", group="10"), api(ad="3", group="11")]},
                                                     cover={"1234567890": [{"adGroup": {"id": "11"}}]}, weekly={"1234567890": [week_row("10", keys[0], 5, 1_000_000)]}, log=log))
    assert [n for n in ("ad_group.id IN (10, 11)",) if all(n in q for _c, q in log[1:])] == ["ad_group.id IN (10, 11)"] and len(log) == 3
    assert r["cover"] == {"10": 0, "11": 1} and r["weekly"]["10"][0] == {"clicks": 5.0, "cost": 1.0} and r["warnings"] == [] and len(r["ads"]) == 3


def test_a_cover_that_cannot_be_read_is_none_with_a_warning_and_the_weeks_still_come():
    r = d.read_account("1234567890", TODAY, searcher(fail_cover=True))
    assert r["cover"] is None and r["weekly"] == {} and r["warnings"] == ["Whether other ads cover the flagged ones could not be read (no cover)."]


def test_weeks_that_cannot_be_read_are_none_with_a_warning_and_the_cover_still_comes():
    r = d.read_account("1234567890", TODAY, searcher(fail_weekly=True))
    assert r["weekly"] is None and r["cover"] == {"10": 0} and r["warnings"] == ["What the ad groups used to bring could not be read (no weeks)."]


def test_ad_groups_beyond_the_cap_are_not_checked_and_the_account_says_so(monkeypatch):
    monkeypatch.setattr(d, "MAX_GROUPS", 2)
    monkeypatch.setattr(d, "BATCH", 2)
    log = []
    ads = [api(ad=str(i), group=str(10 + i)) for i in range(5)]
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": ads}, log=log))
    assert r["cover"] == {"10": 0, "11": 0, "12": None, "13": None, "14": None} and "Only the first 2 of 5 ad groups" in r["warnings"][0]
    assert all("12" not in q.split("ad_group.id IN")[-1] for _c, q in log[1:])


def test_the_default_cap_is_three_hundred_ad_groups_and_the_rest_are_not_checked():
    log = []
    ads = [api(ad=str(i), group=str(1000 + i)) for i in range(301)]
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": ads}, log=log))
    assert sum(1 for v in r["cover"].values() if v is None) == 1 and r["cover"]["1300"] is None and r["cover"]["1299"] == 0 and "Only the first 300 of 301 ad groups" in r["warnings"][0]
    assert len([1 for _c, q in log if "'APPROVED'" in q]) == 3                      # 300 groups in batches of 100


def test_a_very_long_list_of_flagged_ads_is_cut_and_said(monkeypatch):
    monkeypatch.setattr(d, "MAX_ADS", 3)
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": [api(ad=str(i), group="10") for i in range(5)]}))
    assert len(r["ads"]) == 3 and r["truncated"] is True and "More than 3 flagged ads: the first 3 are shown." in r["warnings"]


def test_an_mcc_reads_every_account_and_says_the_one_it_could_not_read():
    mcc = {"id": "mcc-1", "name": "Group", "currency": "USD", "members": [ACCT, ACCT2]}
    raw = d.google_raw(mcc, TODAY, search_fn=searcher({ACCT["id"]: [api()], ACCT2["id"]: [api()]}, fail=(ACCT2["id"],)))
    assert [a["name"] for a in raw["accounts"]] == ["Acme Dental"] and raw["members"] == ["Acme Dental", "Beta Clinic"] and "Beta Clinic could not be read" in raw["warnings"][0]


def test_every_account_failing_is_raised():
    mcc = {"id": "mcc-1", "name": "Group", "members": [ACCT, ACCT2]}
    with pytest.raises(gads.GadsError, match="cannot be read"):
        d.google_raw(mcc, TODAY, search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])))


# ── The report and its cache ────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def report(search, **kw):
    return d.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, **kw)


def test_the_report_says_which_weeks_and_what_it_found(cache):
    keys = [w[0].isoformat() for w in WEEKS]
    rep = report(searcher(weekly={"1234567890": [week_row("10", keys[i], 100, 50_000_000) for i in range(4)]}))
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and rep["window"] == {"from": "2026-09-14", "to": "2026-10-18", "weeks": 5}
    a = rep["accounts"][0]
    assert a["status"] == "stopped" and a["deadGroups"][0]["weight"]["lostClicks"] == 100 and a["findings"][0]["level"] == "high" and a["id"] == "1234567890" and a["currency"] == "USD"
    assert rep["summary"] == {"counts": {"stopped": 1, "disapproved": 0, "limited": 0, "clear": 0}, "accounts": 1, "needsLook": 1, "stoppedGroups": 1, "disapproved": 1, "limited": 0, "waiting": 0, "lostClicks": 100}
    assert rep["readAt"] and rep["warnings"] == []


def test_a_second_ask_never_reads_google_again_and_refresh_does(cache):
    calls = []
    search = searcher(log=calls)
    report(search)
    n = len(calls)
    assert n >= 3
    report(search)
    assert len(calls) == n
    report(search, force=True)
    assert len(calls) > n


def test_an_mcc_report_puts_the_stopped_accounts_first_and_counts_each_state_and_names_the_account_in_a_warning(cache):
    accounts = [ACCT, ACCT2, {"id": "3333333333", "name": "Gamma", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"}]
    ads = {ACCT["id"]: [api(approval="APPROVED_LIMITED")],                                   # limited
           ACCT2["id"]: [api(ad="2", group="20")],                                            # stopped
           "3333333333": [],                                                                  # clear
           "4444444444": [api(ad="3", group="30"), api(ad="4", group="31")]}                  # two stopped groups
    cover = {"4444444444": [], ACCT2["id"]: []}
    rep = d.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=searcher(ads, cover=cover, fail_weekly=True), today=TODAY)
    assert [(a["name"], a["status"]) for a in rep["accounts"]] == [("Delta", "stopped"), ("Beta Clinic", "stopped"), ("Acme Dental", "limited"), ("Gamma", "clear")]
    assert rep["summary"]["counts"] == {"stopped": 2, "disapproved": 0, "limited": 1, "clear": 1} and rep["summary"]["stoppedGroups"] == 3 and rep["summary"]["needsLook"] == 2
    assert "Delta: What the ad groups used to bring could not be read (no weeks)." in rep["warnings"] and rep["account"]["isGroup"] and rep["account"]["accountCount"] == 4


def test_the_summary_counts_the_accounts_to_look_at_and_the_ads_waiting_on_google(cache):
    accounts = [ACCT, ACCT2]
    ads = {ACCT["id"]: [api(ad="1", group="10", review="UNDER_APPEAL"), api(ad="2", group="11", review="REVIEW_IN_PROGRESS")], ACCT2["id"]: [api(ad="3", group="20")]}
    cover = {ACCT["id"]: [{"adGroup": {"id": "10"}}, {"adGroup": {"id": "11"}}], ACCT2["id"]: [{"adGroup": {"id": "20"}}]}
    rep = d.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=searcher(ads, cover=cover), today=TODAY)
    s = rep["summary"]
    assert [a["status"] for a in rep["accounts"]] == ["disapproved", "disapproved"] and s["needsLook"] == 2 and s["waiting"] == 2 and s["disapproved"] == 3 and s["stoppedGroups"] == 0 and s["lostClicks"] == 0


def test_a_read_that_lost_an_account_is_shown_with_a_warning_and_is_not_saved_for_hours(cache):
    accounts = [ACCT, ACCT2]
    kw = dict(accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
              search_fn=searcher({ACCT["id"]: [api()], ACCT2["id"]: [api()]}, fail=(ACCT2["id"],)), today=TODAY)
    rep = d.report_for("mcc-9923720096-USD", **kw)
    assert len(rep["accounts"]) == 1 and "Beta Clinic could not be read" in rep["warnings"][0]
    saved = lambda: [k for k in cache if not k.startswith("rl:")]
    assert saved() == []
    whole = d.report_for("mcc-9923720096-USD", **dict(kw, search_fn=searcher({ACCT["id"]: [api()], ACCT2["id"]: [api()]}), force=True))
    assert not [w for w in whole["warnings"] if "could not be read:" in w] and len(saved()) == 1


def test_a_spent_quota_serves_the_last_saved_read_with_its_date(cache, monkeypatch):
    from tracker import paid_geo
    assert "stale" not in report(searcher(), now=0)

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: None)
    monkeypatch.setattr(paid_store, "get_gads_cache_any", lambda key: (cache.get(key), "2026-10-20T10:00:00+00:00") if key in cache else None, raising=False)
    paid_geo.clear_cache()
    base.forget("")
    old = report(spent, now=10 ** 9)
    assert old["stale"] == "2026-10-20T10:00:00+00:00" and old["accounts"][0]["status"] == "stopped"


def test_with_no_saved_read_a_spent_quota_is_raised(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        report(spent)


def test_an_account_that_is_not_position2s_is_refused(cache):
    with pytest.raises(d.BadRequest):
        d.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY)
