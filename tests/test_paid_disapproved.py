"""Disapproved Ads Tracker: reading Google's answer, the cover and the weight, an MCC, the report and its cache (tracker/paid_disapproved.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def searcher(ads=None, cover=None, weekly=None, log=None, fail=(), fail_cover=False, fail_weekly=False, assets=None, asset_cover=None, asset_weekly=None, keywords=None, fail_assets=False,
             fail_keywords=False, fail_policy=False):
    """A stand-in for the Google Ads read: the flagged ads by account and, for the other reads, what each returns (the cover and the weeks of ad groups, the Performance Max assets and
    their cover and weeks, the disapproved keywords); the flags make a read refuse."""
    ads = ads if ads is not None else {ACCT["id"]: [api()]}

    def search(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        if "FROM asset_group_asset" in q:
            if fail_assets:
                raise gads.GadsError("api", "no assets")
            return ((asset_cover if "'APPROVED'" in q else assets) or {}).get(cid, [])
        if "FROM ad_group_criterion" in q:
            if fail_keywords or (fail_policy and "policy_summary" in q):
                raise gads.GadsError("api", "no keywords")
            return (keywords or {}).get(cid, [])
        if "segments.week" in q:
            if fail_weekly:
                raise gads.GadsError("api", "no weeks")
            return ((asset_weekly if "FROM asset_group " in q else weekly) or {}).get(cid, [])
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
    assert r == {"ads": [], "cover": {}, "weekly": {}, "warnings": [], "truncated": False} and len(log) == 3                  # the ads, the Performance Max assets and the keywords


def test_the_cover_and_the_weeks_are_read_only_for_the_groups_with_a_flagged_ad():
    log = []
    keys = [w[0].isoformat() for w in WEEKS]
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": [api(ad="1", group="10"), api(ad="2", group="10"), api(ad="3", group="11")]},
                                                     cover={"1234567890": [{"adGroup": {"id": "11"}}]}, weekly={"1234567890": [week_row("10", keys[0], 5, 1_000_000)]}, log=log))
    later = [q for _c, q in log if "'APPROVED'" in q or "segments.week" in q]
    assert len(later) == 2 and all("ad_group.id IN (10, 11)" in q for q in later) and len(log) == 5
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
    assert all("12" not in q.split("ad_group.id IN")[-1] for _c, q in log if "ad_group.id IN" in q)


def test_the_default_cap_is_three_hundred_ad_groups_and_the_rest_are_not_checked():
    log = []
    ads = [api(ad=str(i), group=str(1000 + i)) for i in range(301)]
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": ads}, log=log))
    assert sum(1 for v in r["cover"].values() if v is None) == 1 and r["cover"]["1300"] is None and r["cover"]["1299"] == 0 and "Only the first 300 of 301 ad groups" in r["warnings"][0]
    assert len([1 for _c, q in log if "'APPROVED'" in q]) == 3                      # 300 groups in batches of 100


def test_a_very_long_list_of_flagged_ads_is_cut_and_said(monkeypatch):
    monkeypatch.setattr(d, "MAX_ADS", 3)
    r = d.read_account("1234567890", TODAY, searcher({"1234567890": [api(ad=str(i), group="10") for i in range(5)]}))
    assert len(r["ads"]) == 3 and r["truncated"] is True and "More than 3 flagged items: the first 3 are shown." in r["warnings"]


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
    assert rep["summary"] == {"counts": {"stopped": 1, "disapproved": 0, "limited": 0, "clear": 0}, "accounts": 1, "needsLook": 1, "stoppedGroups": 1, "disapproved": 1, "keywords": 0, "limited": 0, "waiting": 0, "lostClicks": 100}
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


# ── Performance Max assets, keywords and Meta in the read ───────────────────

from tests import test_paid_disapproved_extra as X  # noqa: E402
from tracker import paid_disapproved_extra as ex  # noqa: E402


def test_the_assets_of_performance_max_are_read_with_their_cover_and_their_weeks_and_are_stopped_when_a_field_is_short():
    keys = [w[0].isoformat() for w in WEEKS]
    a = ACCT["id"]
    r = d.read_account(a, TODAY, searcher({a: []}, assets={a: [X.arow(aid="900"), X.arow(aid="901")]}, asset_cover={a: [X.arow()] * 1},
                                          asset_weekly={a: [{"assetGroup": {"id": "7"}, "segments": {"week": keys[i]}, "metrics": {"clicks": "40", "costMicros": "10000000"}} for i in range(4)]}))
    assert [i["id"] for i in r["ads"]] == ["asset:7:900:HEADLINE", "asset:7:901:HEADLINE"] and r["cover"] == {"pmax:7:HEADLINE": 1} and r["warnings"] == []
    assert r["weekly"]["pmax:7:HEADLINE"][0] == {"clicks": 40.0, "cost": 10.0}
    from tracker import paid_disapproved_math as m
    an = m.analyse(r["ads"], r["cover"], r["weekly"])
    assert an["status"] == "stopped" and an["deadGroups"][0]["name"] == "Summer (headlines)" and an["deadGroups"][0]["weight"]["lostClicks"] == 40


def test_keywords_are_read_once_without_the_policy_topics_google_refuses_and_a_failed_read_is_said():
    a = ACCT["id"]
    log = []
    r = d.read_account(a, TODAY, searcher({a: []}, keywords={a: [X.krow()]}, fail_policy=True, log=log))          # a fake that refuses the policy field: the query never asks for it
    assert [i["id"] for i in r["ads"]] == ["kw:3:11"] and r["warnings"] == [] and len([q for _c, q in log if "FROM ad_group_criterion" in q]) == 1
    assert not any("policy_summary" in q for _c, q in log if "FROM ad_group_criterion" in q)
    both = d.read_account(a, TODAY, searcher({a: []}, keywords={a: [X.krow()]}, fail_keywords=True))
    assert both["ads"] == [] and both["warnings"] == ["The disapproved keywords could not be read (no keywords)."]


def test_assets_that_cannot_be_read_are_said_and_the_ads_still_come():
    r = d.read_account(ACCT["id"], TODAY, searcher(fail_assets=True))
    assert len(r["ads"]) == 1 and r["warnings"] == ["The assets of Performance Max asset groups could not be read (no assets)."]


def test_flagged_assets_alone_are_enough_to_ask_for_the_cover_and_keywords_never_are():
    a = ACCT["id"]
    log = []
    d.read_account(a, TODAY, searcher({a: []}, keywords={a: [X.krow()]}, log=log))
    assert not [q for _c, q in log if "'APPROVED'" in q or "segments.week" in q]


def test_a_meta_ad_account_is_read_for_rejected_ads_and_the_active_ones_that_cover_them():
    asked = []

    def pages(path, params):
        asked.append((path, params["effective_status"]))
        return [{"id": "9", "adset_id": "55"}] if params["effective_status"] == '["ACTIVE"]' else [X.mad("1", adset="55"), X.mad("2", adset="56")]
    r = d.read_meta_account("act_123", pages)
    assert [i["id"] for i in r["ads"]] == ["meta:1", "meta:2"] and r["cover"] == {"meta:55": 1, "meta:56": 0} and r["weekly"] is None
    assert r["warnings"] == ["Meta's clicks per ad set are not read, so what a stopped ad set used to bring is not sized."] and asked == [("/act_123/ads", '["DISAPPROVED", "WITH_ISSUES"]'), ("/act_123/ads", '["ACTIVE"]')]
    none = d.read_meta_account("act_123", lambda p, q: [])
    assert none == {"ads": [], "cover": {}, "weekly": None, "warnings": [], "truncated": False}


def test_a_meta_cover_that_cannot_be_read_is_none_and_said():
    def pages(path, params):
        if params["effective_status"] == '["ACTIVE"]':
            raise RuntimeError("meta says no")
        return [X.mad()]
    r = d.read_meta_account("act_123", pages)
    assert r["cover"] is None and r["warnings"][0] == "Whether other active ads cover the rejected ones could not be read (meta says no)."


def test_the_report_for_a_meta_account_names_its_stopped_ad_sets(cache):
    def pages(path, params):
        return [] if params["effective_status"] == '["ACTIVE"]' else [X.mad("1", adset="55", feedback={"ADULT_CONTENT": "Too much skin"})]
    rep = d.report_for("act_1234567", search_fn=searcher(), meta_pages_fn=pages, meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"}, today=TODAY)
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and rep["account"]["name"] == "Beta Meta" and a["platform"] == "meta" and a["status"] == "stopped" and a["currency"] == "EUR" and a["counts"]["meta"] == 1
    assert a["deadGroups"][0]["name"] == "Ad set 55" and a["plan"]["steps"][0]["worth"] == "How many clicks these ad groups used to bring could not be read, so it is not sized here."
    assert "Adult content" in [t["label"] for t in a["topics"]]


# ── Age, patterns and how long fixes take ───────────────────────────────────

def issue(fp, status="open", first="2026-10-14T07:00:00+00:00", resolved=None, reopened=0):
    return {"agent": "disapproved", "fingerprint": fp, "status": status, "firstSeen": first, "lastSeen": resolved or "2026-10-20T07:00:00+00:00", "resolvedAt": resolved, "reopened": reopened}


def test_the_ages_come_from_the_open_issues_the_monitor_keeps_and_not_from_resolved_ones():
    got = d.ages_of([issue("group:1:10", reopened=2), issue("topic:1:X", status="resolved", resolved="2026-10-16T07:00:00+00:00")])
    assert got == {"group:1:10": {"firstSeen": "2026-10-14T07:00:00+00:00", "reopened": 2}}


def test_how_long_fixes_took_is_the_middle_and_the_longest_of_the_resolved_issues():
    done = [issue("a", "resolved", "2026-10-01T07:00:00+00:00", "2026-10-03T07:00:00+00:00"), issue("b", "resolved", "2026-10-01T07:00:00+00:00", "2026-10-08T07:00:00+00:00"),
            issue("c", "resolved", "2026-10-01T07:00:00+00:00", "2026-10-02T07:00:00+00:00"), issue("d")]
    assert d.fix_times(done) == {"fixed": 3, "median": 2, "longest": 7} and d.fix_times([issue("d")]) is None and d.fix_times([]) is None


def test_the_report_gives_a_stopped_group_its_age_from_the_monitors_issues(cache):
    from monitor_fakes import MemStore
    mon = MemStore()
    mon.issues_put([issue("group:1234567890:10", first="2026-10-14T07:00:00+00:00")])
    keys = [w[0].isoformat() for w in WEEKS]
    rep = d.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(weekly={"1234567890": [week_row("10", keys[i], 70, 35_000_000) for i in range(4)]}), today=TODAY, mon=mon)
    g = rep["accounts"][0]["deadGroups"][0]
    assert rep["agesKnown"] is True and g["ageDays"] is not None and g["ageDays"] >= 1 and rep["fixTimes"] is None
    plain = d.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY)
    assert plain["agesKnown"] is False and plain["fixTimes"] is None and plain["accounts"][0]["deadGroups"][0]["ageDays"] is None


def test_an_unreadable_history_is_said_and_the_report_stands(cache):
    class Broken:
        def issues_all(self, agent):
            raise RuntimeError("down")
    rep = d.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, mon=Broken())
    assert rep["warnings"][0] == "The history of what was flagged before could not be read (down), so no age is shown." and rep["agesKnown"] is False


def test_a_policy_that_flags_ads_in_several_accounts_is_a_pattern_the_most_accounts_first(cache):
    accounts = [ACCT, ACCT2, {"id": "3333333333", "name": "Gamma", "currency": "USD"}]
    ads = {ACCT["id"]: [api(topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED", []), ("DESTINATION_NOT_WORKING", "PROHIBITED", [])))], ACCT2["id"]: [api(ad="2", group="20", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED", []),))],
           "3333333333": [api(ad="3", group="30", topics=(("TRADEMARKS_IN_AD_TEXT", "LIMITED", []), ("ONLY_HERE", "LIMITED", [])))]}
    rep = d.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=searcher(ads), today=TODAY)
    [p] = rep["patterns"]
    assert p["topic"] == "TRADEMARKS_IN_AD_TEXT" and p["label"] == "Trademarks in ad text" and sorted(p["accounts"]) == ["Acme Dental", "Beta Clinic", "Gamma"] and p["ads"] == 3
    assert d.patterns([]) == []
