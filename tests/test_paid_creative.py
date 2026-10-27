"""Creative & Asset Health: reading Google and Meta, the report and its cache (tracker/paid_creative.py). No network."""
import datetime as dt
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import paid_creative as c  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_meta  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 22)                       # a Thursday: the whole weeks end on Sunday 18 October
AT = dt.datetime(2026, 10, 22, 15, 0, tzinfo=dt.timezone.utc)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}
STARTS = ["2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"]


def ad_row(aid="11", week="2026-10-12", impr=5000, clicks=250, cost=100.0, group="Crowns", kind="RESPONSIVE_SEARCH_AD", camp="Search Crowns", name=""):
    return {"campaign": {"id": "9", "name": camp}, "adGroup": {"id": "5", "name": group}, "adGroupAd": {"ad": {"id": aid, "type": kind, "name": name}}, "segments": {"week": week},
            "metrics": {"impressions": str(impr), "clicks": str(clicks), "costMicros": str(int(cost * 1e6))}}


def ad_weeks(aid="11", base_ctr=0.05, recent_ctr=0.05, impr=5000, **kw):
    return [ad_row(aid, w, impr, round(impr * (base_ctr if i < 3 else recent_ctr)), **kw) for i, w in enumerate(STARTS)]


def group_row(gid="31", week="2026-10-12", impr=4000, clicks=200, name="Crowns group"):
    return {"campaign": {"id": "8", "name": "PMax"}, "assetGroup": {"id": gid, "name": name}, "segments": {"week": week}, "metrics": {"impressions": str(impr), "clicks": str(clicks), "costMicros": "50000000"}}


def head_row(aid="11", text="Crowns in Boston"):
    return {"adGroupAd": {"ad": {"id": aid, "responsiveSearchAd": {"headlines": [{"text": text}, {"text": "Book today"}]}}}}


def weak_ad_row(aid="11", field="HEADLINE", text="Best crowns"):
    return {"campaign": {"id": "9", "name": "Search Crowns"}, "adGroup": {"name": "Crowns"}, "adGroupAd": {"ad": {"id": aid, "type": "RESPONSIVE_SEARCH_AD"}},
            "adGroupAdAssetView": {"fieldType": field, "performanceLabel": "LOW"}, "asset": {"type": "TEXT", "textAsset": {"text": text}}}


def weak_group_row(gid="31", field="MARKETING_IMAGE", kind="IMAGE", name="hero.png"):
    return {"campaign": {"id": "8", "name": "PMax"}, "assetGroup": {"id": gid, "name": "Crowns group"}, "assetGroupAsset": {"fieldType": field, "performanceLabel": "LOW"},
            "asset": {"type": kind, "name": name}}


# ── The weeks ───────────────────────────────────────────────────────────────

def test_the_weeks_are_the_last_whole_monday_to_sunday_weeks_and_never_the_one_in_progress():
    assert [d.isoformat() for d in c.week_starts(TODAY)] == STARTS
    assert c.week_starts(dt.date(2026, 10, 26))[-1].isoformat() == "2026-10-19"          # a Monday: the week that just ended is whole
    assert c.week_starts(dt.date(2026, 10, 25))[-1].isoformat() == "2026-10-12"          # a Sunday: this week is not over


def test_an_ads_rows_become_aligned_weeks_with_zeros_for_a_week_it_did_not_run():
    starts = c.week_starts(TODAY)
    rows = [ad_row(week="2026-10-12", impr=3000, clicks=90, cost=40.0), ad_row(week="2026-10-05", impr=2000, clicks=60, cost=25.5), ad_row(week="2026-08-03"), ad_row(aid="")]
    [a] = c.read_ads(rows, starts, {"11": "Crowns in Boston"})
    assert [w["impr"] for w in a["weeks"]] == [0, 0, 0, 2000, 3000] and [w["clicks"] for w in a["weeks"]] == [0, 0, 0, 60, 90] and a["weeks"][3]["cost"] == 25.5
    assert a["name"] == "Crowns in Boston (Crowns)" and a["kind"] == "Responsive search ad" and a["campaign"] == "Search Crowns" and a["group"] == "Crowns"


def test_two_ads_of_one_ad_group_read_differently_and_one_without_a_headline_says_its_kind():
    assert c.ad_name("1", "RESPONSIVE_SEARCH_AD", "Crowns", {"1": "Crowns in Boston", "2": "Crowns near you"}) != c.ad_name("2", "RESPONSIVE_SEARCH_AD", "Crowns", {"1": "Crowns in Boston", "2": "Crowns near you"})
    assert c.ad_name("3", "RESPONSIVE_DISPLAY_AD", "Crowns", {}) == "Responsive display ad in Crowns"
    assert c.ad_name("3", "RESPONSIVE_DISPLAY_AD", "Crowns", {}, "Spring banner") == "Spring banner (Crowns)"
    assert c.ad_name("3", "NEW_KIND", "", {}) == "New kind 3"


def test_headlines_are_the_first_one_of_each_responsive_ad():
    assert c.headlines_of([head_row("1", "Crowns in Boston"), {"adGroupAd": {"ad": {"id": "2"}}}]) == {"1": "Crowns in Boston"}


def test_asset_groups_are_creatives_of_their_own_with_a_prefixed_id():
    [g] = c.read_groups([group_row(week="2026-10-12"), group_row(week="2026-10-05", impr=1000, clicks=20)], c.week_starts(TODAY))
    assert g["id"] == "g31" and g["kind"] == "Performance Max asset group" and g["weeks"][4]["impr"] == 4000 and g["weeks"][3]["clicks"] == 20


def test_assets_google_rates_low_are_listed_for_ads_and_asset_groups_and_nothing_else():
    ok = weak_ad_row()
    fine = dict(weak_ad_row("12"), adGroupAdAssetView={"fieldType": "HEADLINE", "performanceLabel": "GOOD"})
    got = c.read_weak([ok, fine], [weak_group_row(), dict(weak_group_row("32"), assetGroupAsset={"fieldType": "X", "performanceLabel": "BEST"})], {"11": "Crowns in Boston"})
    assert [(w["creativeId"], w["field"], w["text"]) for w in got] == [("11", "HEADLINE", "Best crowns"), ("g31", "MARKETING_IMAGE", "an image")]
    assert got[0]["name"] == "Crowns in Boston (Crowns)" and got[1]["name"] == "Crowns group" and got[1]["kind"] == "Performance Max asset group"


def test_asset_coverages_gaps_are_flattened_with_their_campaign():
    report = {"campaigns": [{"id": "9", "name": "Search Crowns", "gaps": [{"key": "SITELINK", "scope": "Campaign", "label": "Sitelinks", "severity": "high", "have": 0, "target": 4, "add": 4, "message": "None."}]},
                            {"id": "8", "name": "PMax", "gaps": []}]}
    assert c.gaps_of_google(report) == [{"key": "SITELINK", "scope": "Campaign", "campaign": "Search Crowns", "campaignId": "9", "label": "Sitelinks", "severity": "high", "have": 0, "target": 4,
                                         "add": 4, "message": "None."}]


# ── One Google account ──────────────────────────────────────────────────────

def searcher(ads=None, groups=None, heads=None, weak_ads=None, weak_groups=None, fail=(), fail_accounts=()):
    ads = ads if ads is not None else ad_weeks()
    groups = groups if groups is not None else []
    heads = heads if heads is not None else [head_row()]
    weak_ads = weak_ads if weak_ads is not None else []
    weak_groups = weak_groups if weak_groups is not None else []

    def run(cid, q):
        if cid in fail_accounts:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        for kind, marker, rows in (("weak_ads", "FROM ad_group_ad_asset_view", weak_ads), ("weak_groups", "FROM asset_group_asset", weak_groups), ("groups", "FROM asset_group WHERE", groups),
                                   ("heads", "responsive_search_ad.headlines", heads), ("ads", "FROM ad_group_ad WHERE", ads)):
            if marker in q:
                if kind in fail:
                    raise gads.GadsError("api", "no %s read" % kind)
                return rows
        raise AssertionError("unexpected query: " + q[:90])
    return run


def coverage_report(gaps=()):
    return lambda member: {"campaigns": [{"id": "9", "name": "Search Crowns", "gaps": list(gaps)}]}


def read(search=None, report=None, member=ACCT):
    return c.read_account(member, search or searcher(), today=TODAY, report_fn=report or coverage_report())


def test_an_account_is_read_into_its_creatives_ratings_and_gaps():
    a = read(searcher(groups=[group_row()], weak_ads=[weak_ad_row()], weak_groups=[weak_group_row()]),
             coverage_report([{"key": "SITELINK", "scope": "Campaign", "label": "Sitelinks", "severity": "high", "have": 0, "target": 4, "add": 4, "message": "None."}]))
    assert [x["id"] for x in a["creatives"]] == ["11", "g31"] and len(a["weak"]) == 2 and len(a["gaps"]) == 1 and a["weeksRead"] is True and a["today"] == "2026-10-22"
    assert a["unread"] == [] and a["warnings"] == []


def test_the_queries_use_the_whole_weeks_window_and_only_live_things():
    seen = []

    def spy(cid, q):
        seen.append(q)
        return searcher()(cid, q)
    read(spy)
    ads_q = next(q for q in seen if "segments.week" in q and "FROM ad_group_ad" in q)
    assert "BETWEEN '2026-09-14' AND '2026-10-18'" in ads_q and "ad_group_ad.status = ENABLED" in ads_q and "campaign.status = ENABLED" in ads_q
    group_q = next(q for q in seen if "FROM asset_group WHERE" in q)
    assert "asset_group.status = ENABLED" in group_q and "BETWEEN '2026-09-14' AND '2026-10-18'" in group_q
    assert all("performance_label = LOW" in q for q in seen if "performance_label" in q and "WHERE" in q)


def test_a_failed_ad_read_is_raised_it_is_the_point():
    with pytest.raises(gads.GadsError):
        read(searcher(fail=("ads",)))


def test_a_part_that_fails_is_soft_says_so_and_is_named_unread():
    a = read(searcher(fail=("weak_ads",)))
    assert a["weak"] is None and a["unread"] == ["low_rating"] and "ratings of the ads' assets could not be read" in a["warnings"][0]
    b = read(report=lambda m: (_ for _ in ()).throw(gads.GadsError("quota", "The daily limit is used up.")))
    assert b["gaps"] is None and b["unread"] == ["missing_asset"] and "asset coverage could not be read" in b["warnings"][0]
    d = read(searcher(fail=("groups", "heads")))
    assert [x["id"] for x in d["creatives"]] == ["11"] and len(d["warnings"]) == 2 and d["unread"] == []


def test_the_ratings_are_unread_if_either_half_fails_so_nothing_is_half_judged():
    assert read(searcher(fail=("weak_groups",), weak_ads=[weak_ad_row()]))["weak"] is None
    assert read(searcher(fail=("weak_ads",), weak_groups=[weak_group_row()]))["weak"] is None


def test_the_headline_names_the_ad_and_a_missing_headline_read_still_gives_a_readable_name():
    assert read()["creatives"][0]["name"] == "Crowns in Boston (Crowns)"
    assert read(searcher(fail=("heads",)))["creatives"][0]["name"] == "Responsive search ad in Crowns"


# ── MCCs ────────────────────────────────────────────────────────────────────

def test_an_mcc_reads_every_account_and_leaves_out_one_that_cannot_be_read():
    group = {"id": "mcc-1", "name": "MCC", "currency": "USD", "members": [ACCT, ACCT2]}
    raw = c.google_raw(group, search_fn=searcher(fail_accounts=(ACCT2["id"],)), report_fn=coverage_report(), today=TODAY)
    assert [a["id"] for a in raw["accounts"]] == ["1234567890"] and raw["accounts"][0]["platform"] == "google" and raw["members"] == ["Acme Dental", "Beta Clinic"]
    assert raw["warnings"] == ["Beta Clinic could not be read: Account 2222222222 cannot be read."]
    with pytest.raises(gads.GadsError):
        c.google_raw(group, search_fn=searcher(fail_accounts=(ACCT["id"], ACCT2["id"])), report_fn=coverage_report(), today=TODAY)


def test_an_mcc_warning_names_the_account_it_came_from():
    group = {"id": "mcc-1", "name": "MCC", "currency": "USD", "members": [ACCT, ACCT2]}
    raw = c.google_raw(group, search_fn=searcher(fail=("heads",)), report_fn=coverage_report(), today=TODAY)
    assert raw["warnings"][0].startswith("Acme Dental: The ads' headlines could not be read")


# ── Meta ────────────────────────────────────────────────────────────────────

def test_the_meta_request_asks_for_five_seven_day_buckets_ending_yesterday():
    path, params = c.meta_weeks_request("act_1", TODAY)
    assert path == "/act_1/insights" and params["level"] == "ad" and params["time_increment"] == 7
    assert json.loads(params["time_range"]) == {"since": "2026-09-17", "until": "2026-10-21"}
    assert all(f in params["fields"] for f in ("ad_id", "impressions", "reach", "inline_link_clicks", "spend"))


def meta_row(aid="a1", start="2026-10-15", impr=5000, reach=2500, clicks=100, spend=50.0):
    return {"ad_id": aid, "date_start": start, "impressions": str(impr), "reach": str(reach), "inline_link_clicks": str(clicks), "spend": str(spend)}


def test_meta_rows_are_put_into_the_five_buckets_and_the_rest_dropped():
    rows = [meta_row(start="2026-09-17"), meta_row(start="2026-10-15", impr=3000, reach=1000, clicks=60, spend=12.5), meta_row(start="2026-09-10"), meta_row(start="2026-10-22"), meta_row(aid=""),
            meta_row(start="not a date")]
    got = c.read_meta_weeks(rows, TODAY)
    assert list(got) == ["a1"] and [w["impr"] for w in got["a1"]] == [5000, 0, 0, 0, 3000] and got["a1"][4]["reach"] == 1000 and got["a1"][4]["cost"] == 12.5 and got["a1"][4]["clicks"] == 60


def meta_raw_read(statuses=("ACTIVE", "ACTIVE", "DISAPPROVED"), insights_read=True, warnings=()):
    ads = [{"id": "a1", "name": "Hero video", "status": statuses[0], "adsetId": "s1", "adset": "Prospecting", "campaignId": "c1", "campaign": "Leads", "format": "video", "dynamic": False, "texts": 0},
           {"id": "a2", "name": "Static", "status": statuses[1], "adsetId": "s1", "adset": "Prospecting", "campaignId": "c1", "campaign": "Leads", "format": "image", "dynamic": False, "texts": 0},
           {"id": "a3", "name": "Stopped", "status": statuses[2], "adsetId": "s2", "adset": "Retargeting", "campaignId": "c1", "campaign": "Leads", "format": "image", "dynamic": False, "texts": 0}]
    return {"ads": ads, "insights": {"a1": {"rank": {"quality": "BELOW_AVERAGE_20", "engagement": "BELOW_AVERAGE_35", "conversion": None}}}, "warnings": list(warnings), "insightsRead": insights_read}


def test_only_active_ads_are_creatives_and_meta_rankings_ride_along():
    got = c.meta_creatives(meta_raw_read(), {"a1": c._empty_weeks()})
    assert [x["name"] for x in got] == ["Hero video (Prospecting)", "Static (Prospecting)"]                # ads share names across ad sets: the ad set tells them apart
    assert [x["id"] for x in got] == ["a1", "a2"] and got[0]["kind"] == "Video ad" and got[0]["rank"]["quality"] == "BELOW_AVERAGE_20" and got[1]["rank"] is None
    assert got[0]["group"] == "Prospecting" and got[0]["campaign"] == "Leads"


def test_ad_sets_with_one_ad_become_gaps_and_the_low_ones_do_not():
    cover = {"adsets": [{"id": "s1", "name": "Prospecting", "campaign": "Leads", "campaignId": "c1", "ads": 1, "flags": [{"code": "single_ad", "severity": "medium", "text": "Only one live ad."}]},
                        {"id": "s2", "name": "Retargeting", "campaign": "Leads", "campaignId": "c1", "ads": 2, "flags": [{"code": "few_ads", "severity": "low", "text": "2 live ads."}]}]}
    [g] = c.gaps_of_meta(cover)
    assert g["key"] == "SINGLE_AD" and g["scope"] == "Ad set: Prospecting" and g["have"] == 1 and g["target"] == 3 and g["add"] == 2 and g["severity"] == "medium"


def meta_pages(weeks=None, fail_weeks=False):
    def pages(path, params):
        if fail_weeks:
            raise paid_meta.AdsError("api", "Meta refused the read.")
        return weeks if weeks is not None else []
    return pages


def test_a_meta_account_reads_its_ads_weeks_and_gaps():
    rows = [meta_row("a1", s) for s in ("2026-09-17", "2026-09-24", "2026-10-01", "2026-10-08", "2026-10-15")]
    a = c.read_meta_account("act_1", meta_pages(rows), today=TODAY, raw_fn=lambda: meta_raw_read())
    assert [x["id"] for x in a["creatives"]] == ["a1", "a2"] and a["weeksRead"] is True and a["unread"] == [] and a["creatives"][0]["weeks"][4]["impr"] == 5000
    assert a["weak"] is None and a["gaps"] == []


def test_meta_weeks_that_cannot_be_read_say_so_and_name_the_part_unread():
    a = c.read_meta_account("act_1", meta_pages(fail_weeks=True), today=TODAY, raw_fn=lambda: meta_raw_read())
    assert a["weeksRead"] is False and a["unread"] == ["tired"] and "numbers by week could not be read" in a["warnings"][0]


def test_meta_rankings_that_were_not_read_are_named_unread():
    a = c.read_meta_account("act_1", meta_pages([]), today=TODAY, raw_fn=lambda: meta_raw_read(insights_read=False, warnings=["Meta's numbers and quality rankings could not be read."]))
    assert "low_rating" in a["unread"] and a["warnings"] == ["Meta's numbers and quality rankings could not be read."]


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
    return c.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, at=AT, report_fn=kw.pop("report_fn", coverage_report()), **kw)


def test_the_report_names_the_account_and_what_it_found(cache):
    rep = report(searcher(ads=ad_weeks(base_ctr=0.05, recent_ctr=0.02)))
    a = rep["accounts"][0]
    assert rep["account"] == {"id": "1234567890", "name": "Acme Dental", "currency": "USD", "platform": "google", "isGroup": False, "accountCount": 0}
    assert a["status"] == "fix" and [f["rule"] for f in a["findings"]] == ["tired"] and a["findings"][0]["where"]["campaign"] == "Search Crowns"
    assert rep["summary"]["tired"] == 1 and rep["summary"]["judged"] == 1 and rep["summary"]["counts"] == {"fix": 1, "check": 0, "clean": 0} and rep["readAt"]
    assert len(rep["notMeasured"]) >= 4 and [r["rule"] for r in rep["rules"]] == ["tired", "low_rating", "missing_asset"] and rep["thresholds"]["recentWeeks"] == 2


def test_the_read_is_saved_and_a_second_ask_reads_nothing(cache):
    calls = []

    def spy(cid, q):
        calls.append(q)
        return searcher()(cid, q)
    report(spy)
    n = len(calls)
    assert len(cache) == 1 and n > 0
    report(spy)
    assert len(calls) == n


def test_a_read_that_lost_a_part_is_shown_but_not_kept(cache):
    rep = report(searcher(fail=("weak_ads",)))
    assert rep["accounts"][0]["unread"] == ["low_rating"] and cache == {}


def test_an_mcc_report_puts_the_accounts_to_fix_first(cache):
    s1, s2 = searcher(ads=ad_weeks(base_ctr=0.05, recent_ctr=0.02)), searcher()

    def both(cid, q):
        return (s1 if cid == ACCT2["id"] else s2)(cid, q)
    grp = {"id": "mcc-9923720096-USD", "name": "All", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    rep = c.report_for(grp["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [grp], search_fn=both, today=TODAY, at=AT, report_fn=coverage_report())
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"] and rep["account"]["isGroup"] is True and rep["account"]["accountCount"] == 2


def meta_api(weeks=(), ads=None, rank_rows=None, fail_weeks=False):
    """What Meta answers: the live ads, the 30-day rows with the rankings, and the 7-day rows."""
    ads = ads if ads is not None else [{"id": "a1", "name": "Hero video", "effective_status": "ACTIVE", "adset_id": "s1", "adset": {"id": "s1", "name": "Prospecting"}, "campaign_id": "c1",
                                        "campaign": {"id": "c1", "name": "Leads", "objective": "OUTCOME_LEADS"}, "creative": {"object_type": "VIDEO", "video_id": "v1"}}]
    rank_rows = rank_rows if rank_rows is not None else [{"ad_id": "a1", "ad_name": "Hero video", "adset_id": "s1", "campaign_id": "c1", "objective": "OUTCOME_LEADS", "spend": "300", "impressions": "50000",
                                                          "reach": "20000", "frequency": "2.5", "inline_link_clicks": "800", "quality_ranking": "AVERAGE", "engagement_rate_ranking": "AVERAGE",
                                                          "conversion_rate_ranking": "AVERAGE"}]

    def pages(path, params):
        if path.endswith("/ads"):
            return ads
        if path.endswith("/insights") and params.get("time_increment") == 7:
            if fail_weeks:
                raise paid_meta.AdsError("Meta refused the read.")
            return list(weeks)
        if path.endswith("/insights"):
            return rank_rows
        raise AssertionError(path)
    return pages


def test_a_meta_report_judges_the_live_ads_and_reads_through_the_shared_creative_read(cache):
    rows = [meta_row("a1", s, impr=5000, clicks=250 if i < 3 else 60) for i, s in enumerate(("2026-09-17", "2026-09-24", "2026-10-01", "2026-10-08", "2026-10-15"))]
    rep = c.report_for("act_1234567", meta_pages_fn=meta_api(rows), meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"}, today=TODAY, at=AT)
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["platform"] == "meta" and a["currency"] == "EUR" and a["status"] == "fix"
    assert [f["key"] for f in a["findings"] if f["rule"] == "tired"] == ["tired:a1"] and a["findings"][0]["where"]["group"] == "Prospecting"
    assert len(cache) == 2                                                                  # this agent's read and the creative read Ad Strength shares


def test_a_meta_report_whose_weeks_failed_judges_no_ad_and_says_why(cache):
    rep = c.report_for("act_1234567", meta_pages_fn=meta_api(fail_weeks=True), meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "USD"}, today=TODAY, at=AT)
    a = rep["accounts"][0]
    assert a["unread"] == ["tired"] and a["checks"][0]["state"] == "unknown" and "could not be read" in a["checks"][0]["note"] and any("numbers by week" in w for w in rep["warnings"])


def test_a_spent_quota_with_nothing_saved_is_raised_and_an_account_that_is_not_position2s_is_refused(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        report(spent)
    with pytest.raises(c.BadRequest, match="not one of Position2"):
        c.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, at=AT)
