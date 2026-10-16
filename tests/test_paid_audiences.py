"""Paid Marketing Agents: Audience Segment Analysis.

Pinned here: what a criterion points at; whether an audience observes or targets (an ad group's own setting beats its campaign's, and
a campaign with none observes on Search and Shopping, targets elsewhere); which kind an audience is (in-market, affinity, remarketing,
customer match, similar, custom, other) from its type and its list or interest; one audience used in several places is one row with every
use under it; the account's lists (size, status, used or not, an excluded list counts as used) and its exclusions; an MCC (accounts
added, the same list id in two accounts kept apart, one failing account said out loud, all failing raised, a failing extra read a
warning not an error); the report and its cache; and the judgement: an audience against the account's average for its campaign
type, advice that depends on whether the campaign takes a bid adjustment, observing versus targeting, nothing judged on too little
data, which kind does most for the money, lists that cannot serve, lists nobody uses. Nothing here talks to Google.
"""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import app as appmod  # noqa: E402
from tracker import paid_audiences as a  # noqa: E402
from tracker import paid_audiences_insights as ins  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_geo  # noqa: E402
from tracker import paid_store  # noqa: E402

START, END = dt.date(2026, 6, 1), dt.date(2026, 6, 30)
ACCT = {"id": "1234567890", "name": "Acme Landscaping", "currency": "USD"}
MEMBER = {"id": "1234567890", "name": "Acme"}
STAFF = "someone@position2.com"
API = "/p2/paid-marketing/api/audiences"


def metrics(imp=1000, clk=50, cost=100.0, conv=0.0, val=0.0):
    return {"impressions": str(imp), "clicks": str(clk), "costMicros": str(int(cost * 1e6)), "conversions": conv, "conversionsValue": val}


def ag_row(ref_type, ref, display="", campaign="P2_NonBrand", channel="SEARCH", ag="900", agname="Group", bid=None, bidding="TARGET_CPA", cid="5", **m):
    crit = {"type": ref_type, "displayName": display, "criterionId": "1"}
    if bid is not None:
        crit["bidModifier"] = bid
    if ref_type == "USER_LIST":
        crit["userList"] = {"userList": "customers/1/userLists/" + ref}
    elif ref_type == "USER_INTEREST":
        crit["userInterest"] = {"userInterestCategory": "customers/1/userInterests/" + ref}
    elif ref_type == "CUSTOM_AUDIENCE":
        crit["customAudience"] = {"customAudience": "customers/1/customAudiences/" + ref}
    return {"campaign": {"id": cid, "name": campaign, "advertisingChannelType": channel, "biddingStrategyType": bidding},
            "adGroup": {"id": ag, "name": agname}, "adGroupCriterion": crit, "metrics": metrics(**m)}


def camp_row(ref_type, ref, display="", campaign="P2_NonBrand", channel="SEARCH", bid=None, bidding="TARGET_CPA", **m):
    crit = {"type": ref_type, "displayName": display}
    if bid is not None:
        crit["bidModifier"] = bid
    if ref_type == "USER_LIST":
        crit["userList"] = {"userList": "customers/1/userLists/" + ref}
    elif ref_type == "USER_INTEREST":
        crit["userInterest"] = {"userInterestCategory": "customers/1/userInterests/" + ref}
    return {"campaign": {"id": "5", "name": campaign, "advertisingChannelType": channel, "biddingStrategyType": bidding}, "campaignCriterion": crit,
            "metrics": metrics(**m)}


def list_row(lid, name, ltype="REMARKETING", status="OPEN", search=5000, display=5000, ro=False):
    return {"userList": {"id": lid, "name": name, "type": ltype, "membershipStatus": status, "sizeForSearch": str(search),
                         "sizeForDisplay": str(display), "readOnly": ro}}


def interest_row(iid, name, taxonomy):
    return {"userInterest": {"userInterestId": iid, "name": name, "taxonomyType": taxonomy}}


def total_row(name="P2_NonBrand", channel="SEARCH", **m):
    return {"campaign": {"name": name, "advertisingChannelType": channel}, "metrics": metrics(**m)}


def searcher(ag=None, camp=None, settings=None, lists=None, interests=None, excl_ag=None, excl_camp=None, totals=None, log=None, fail=None):
    def search(cid, q):
        if log is not None:
            log.append((cid, q))
        for needle, err in (fail or {}).items():
            if needle in q:
                raise err
        if "FROM ad_group_audience_view" in q:
            return ag or []
        if "FROM campaign_audience_view" in q:
            return camp or []
        if "FROM ad_group WHERE" in q:
            return settings or []
        if "FROM user_list" in q:
            return lists or []
        if "FROM user_interest" in q:
            return interests or []
        if "FROM ad_group_criterion" in q:
            return excl_ag or []
        if "FROM campaign_criterion" in q:
            return excl_camp or []
        if "FROM campaign WHERE segments.date" in q:
            return totals or []
        raise AssertionError(q)
    return search


# ── Reading ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("crit,expect", [
    ({"type": "USER_LIST", "userList": {"userList": "customers/1/userLists/77"}}, ("list", "77")),
    ({"type": "USER_INTEREST", "userInterest": {"userInterestCategory": "customers/1/userInterests/80483"}}, ("interest", "80483")),
    ({"type": "CUSTOM_AUDIENCE", "customAudience": {"customAudience": "customers/1/customAudiences/9"}}, ("custom_audience", "9")),
    ({"type": "CUSTOM_INTENT", "customIntent": {"customIntent": "customers/1/customInterests/8"}}, ("custom_intent", "8")),
    ({"type": "AUDIENCE", "audience": {"audience": "customers/1/audiences/3"}}, ("audience", "3")),
    ({}, ("", "")),
])
def test_what_a_criterion_points_at_comes_from_its_type(crit, expect):
    assert a.reference(crit) == expect


def test_an_ad_groups_own_audience_setting_beats_its_campaigns_and_a_campaign_with_none_follows_its_channel():
    camps = {"5": [{"targetingDimension": "AUDIENCE", "bidOnly": True}]}
    groups = {"900": [{"targetingDimension": "AUDIENCE", "bidOnly": False}], "901": [{"targetingDimension": "AGE_RANGE", "bidOnly": True}]}
    rows = [ag_row("USER_LIST", "1", ag="900"), ag_row("USER_LIST", "1", ag="901"),
            ag_row("USER_LIST", "1", ag="902", channel="DEMAND_GEN", campaign="Gen"), ag_row("USER_LIST", "1", ag="903", channel="SEARCH", campaign="NoSetting")]
    uses = a.read_uses(rows, "ad_group", camps, groups)
    assert [u["mode"] for u in uses] == ["targeting", "observation", "observation", "observation"]
    gen = a.read_uses([ag_row("USER_LIST", "1", ag="902", channel="DEMAND_GEN", campaign="Gen")], "ad_group", {}, {})
    assert gen[0]["mode"] == "targeting"


def test_targeting_settings_are_read_for_campaigns_and_ad_groups():
    rows = [{"campaign": {"id": "5", "targetingSetting": {"targetRestrictions": [{"targetingDimension": "AUDIENCE", "bidOnly": True}]}},
             "adGroup": {"id": "900", "targetingSetting": {"targetRestrictions": [{"targetingDimension": "AUDIENCE", "bidOnly": False}]}}}]
    camps, groups = a.read_settings(rows)
    assert a.mode_of(camps["5"], "SEARCH") == "observation" and a.mode_of(groups["900"], "SEARCH") == "targeting" and a.mode_of(None, "SEARCH") is None


@pytest.mark.parametrize("use,kind,detail", [
    ({"type": "USER_INTEREST", "ref": "1", "name": "x"}, "in_market", "In-market segment"),
    ({"type": "USER_INTEREST", "ref": "2", "name": "x"}, "affinity", "Affinity segment"),
    ({"type": "USER_INTEREST", "ref": "3", "name": "x"}, "other", "Interest segment"),
    ({"type": "USER_LIST", "ref": "10", "name": "x"}, "customer_match", "Customer match list"),
    ({"type": "USER_LIST", "ref": "11", "name": "x"}, "remarketing", "Website visitors (rule based)"),
    ({"type": "USER_LIST", "ref": "12", "name": "x"}, "similar", "Similar audience"),
    ({"type": "CUSTOM_AUDIENCE", "ref": "5", "name": "Local Landscapers"}, "custom", "Custom segment"),
    ({"type": "CUSTOM_INTENT", "ref": "6", "name": "Landscaping Services"}, "custom", "Custom segment (intent)"),
    ({"type": "AUDIENCE", "ref": "7", "name": "P2_Acme"}, "other", "Your data segment"),
])
def test_the_kind_of_an_audience_comes_from_its_type_and_its_list_or_interest(use, kind, detail):
    lists = {"10": {"name": "CRM", "type": "CRM_BASED"}, "11": {"name": "Visitors", "type": "RULE_BASED"}, "12": {"name": "Lookalike", "type": "SIMILAR"}}
    interests = {"1": {"name": "Home & Garden Services", "taxonomy": "IN_MARKET"}, "2": {"name": "Home & Garden", "taxonomy": "AFFINITY"},
                 "3": {"name": "Arts", "taxonomy": "VERTICAL_GEO"}}
    key, label, k, d = a.identify(use, lists, interests)
    assert (k, d) == (kind, detail) and label and key


def test_one_audience_in_several_places_is_one_row_with_every_use_under_it():
    rows = [ag_row("USER_INTEREST", "1", ag="900", agname="One", cost=50, conv=2, clk=10, imp=100),
            ag_row("USER_INTEREST", "1", ag="901", agname="Two", cost=30, conv=1, clk=6, imp=60),
            ag_row("USER_INTEREST", "1", ag="902", agname="Silent", cost=0, conv=0, clk=0, imp=0)]
    out = a.read_account(MEMBER, START, END, search_fn=searcher(ag=rows, interests=[interest_row("1", "Home & Garden Services", "IN_MARKET")],
                                                                totals=[total_row()]))
    assert len(out["audiences"]) == 1
    aud = next(iter(out["audiences"].values()))
    assert aud["label"] == "Home & Garden Services" and aud["kind"] == "in_market" and len(aud["uses"]) == 3
    assert aud["all"]["cost"] == 80 and aud["all"]["conv"] == 3 and aud["types"]["Non Brand"]["clk"] == 16     # the silent use adds nothing


def test_campaign_level_audiences_are_read_too_and_carry_their_bid_and_bidding():
    out = a.read_account(MEMBER, START, END, search_fn=searcher(camp=[camp_row("USER_LIST", "10", bid=0.25, bidding="MANUAL_CPC", cost=40, conv=2)],
                                                                lists=[list_row("10", "CRM list", "CRM_BASED")], totals=[total_row()]))
    aud = next(iter(out["audiences"].values()))
    use = aud["uses"][0]
    assert aud["kind"] == "customer_match" and use["level"] == "campaign" and use["bid"] == 0.25 and use["bidding"] == "MANUAL_CPC" and use["adGroup"] == ""


def test_lists_exclusions_and_use_are_read_and_an_excluded_list_counts_as_used():
    lists = [list_row("10", "Used list"), list_row("11", "Excluded list"), list_row("12", "Idle list"), list_row("13", "Google's own", ro=True)]
    excl = [{"campaign": {"name": "P2_NonBrand"}, "adGroup": {"name": "Group"},
             "adGroupCriterion": {"type": "USER_LIST", "displayName": "Excluded list", "userList": {"userList": "customers/1/userLists/11"}}}]
    out = a.read_account(MEMBER, START, END, search_fn=searcher(ag=[ag_row("USER_LIST", "10", cost=10, conv=1)], lists=lists, excl_ag=excl, totals=[total_row()]))
    used = {l["label"]: l["used"] for l in out["lists"]}
    assert used == {"Used list": True, "Excluded list": True, "Idle list": False, "Google's own": False}
    assert out["exclusions"] == [{"label": "Excluded list", "kind": "remarketing", "detail": "Remarketing list", "account": "Acme", "campaign": "P2_NonBrand",
                                  "adGroup": "Group", "level": "ad_group"}]


def test_a_failing_extra_read_is_a_warning_and_a_failing_view_is_an_error():
    boom = gads.GadsError("api", "google broke")
    out = a.read_account(MEMBER, START, END, search_fn=searcher(ag=[ag_row("USER_LIST", "10", cost=5, conv=1)], totals=[total_row()],
                                                                fail={"FROM user_list": boom, "FROM ad_group WHERE": boom, "negative = TRUE": boom}))
    assert len(out["audiences"]) == 1 and len(out["warnings"]) == 4
    with pytest.raises(gads.GadsError):
        a.read_account(MEMBER, START, END, search_fn=searcher(fail={"FROM ad_group_audience_view": boom}))


def test_an_mcc_keeps_the_same_list_in_two_accounts_apart_and_says_which_account_failed():
    members = [{"id": "1111111111", "name": "One"}, {"id": "2222222222", "name": "Two"}, {"id": "3333333333", "name": "Three"}]

    def search(cid, q):
        if cid == "3333333333":
            raise gads.GadsError("permission", "no access")
        return searcher(ag=[ag_row("USER_LIST", "10", cost=10, conv=1)], lists=[list_row("10", "All visitors")], totals=[total_row()])(cid, q)
    raw = a.google_raw({"id": "mcc-1", "name": "Group", "members": members}, START, END, search_fn=search)
    assert len(raw["audiences"]) == 2 and {x["account"] for x in raw["audiences"].values()} == {"One", "Two"}
    assert raw["warnings"] == ["Three could not be read: no access"] and raw["totals"]["Non Brand"]["cost"] == 200 and len(raw["lists"]) == 2


def test_every_account_failing_is_an_error():
    def search(cid, q):
        raise gads.GadsError("quota", "slow down")
    with pytest.raises(gads.GadsError):
        a.google_raw({"id": "mcc-1", "name": "G", "members": [{"id": "1111111111", "name": "One"}]}, START, END, search_fn=search)


# ── The report and its cache ────────────────────────────────────────────────

# Non Brand, the account's average: 1,000 spent, 50 conversions, 5,000 of value (ROAS 5, CPA 20)
TOTALS = [total_row(cost=1000, conv=50, val=5000, clk=800, imp=20000), total_row("Gen", "DEMAND_GEN", cost=500, conv=10, val=500, clk=300, imp=9000)]
SETTINGS = [{"campaign": {"id": "5", "targetingSetting": {"targetRestrictions": [{"targetingDimension": "AUDIENCE", "bidOnly": True}]}}, "adGroup": {"id": "900"}}]
AG = [ag_row("USER_LIST", "10", ag="900", bid=0.1, bidding="MANUAL_CPC", cost=200, conv=20, val=3000, clk=100),           # strong, can bid
      ag_row("USER_LIST", "11", ag="900", bid=0.0, bidding="MANUAL_CPC", cost=300, conv=10, val=500, clk=120),            # weak, can bid
      ag_row("USER_INTEREST", "1", ag="900", bidding="TARGET_CPA", cost=150, conv=0, clk=200),                              # leaks, Smart Bidding
      ag_row("USER_INTEREST", "2", ag="900", bidding="TARGET_CPA", cost=60, conv=2, val=100, clk=40),                       # too little data
      ag_row("CUSTOM_AUDIENCE", "5", display="Local Landscapers", ag="901", campaign="Gen", channel="DEMAND_GEN", bidding="MAXIMIZE_CONVERSIONS", cid="6",
             cost=300, conv=8, val=400, clk=200)]
LISTS = [list_row("10", "CRM list", "CRM_BASED"), list_row("11", "Site visitors", "REMARKETING"), list_row("20", "Tiny list", search=0, display=40),
         list_row("21", "Unused list"), list_row("22", "Google made", ro=True)]
INTERESTS = [interest_row("1", "Weddings", "IN_MARKET"), interest_row("2", "Home Decor", "AFFINITY")]


@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def report(search=None, **kw):
    search = search or searcher(ag=AG, settings=SETTINGS, lists=LISTS, interests=INTERESTS, totals=TOTALS)
    return a.report_for("1234567890", "2026-06-01", "2026-06-30", accounts_fn=lambda: [dict(ACCT, timeZone="America/New_York")], groups_fn=lambda: [],
                        search_fn=search, **kw)


def row(rep, label, view="All"):
    return next(r for r in rep["views"][view]["rows"] if r["label"] == label)


def test_the_report_has_a_table_per_campaign_type_judged_against_that_types_average(cache):
    rep = report()
    assert rep["types"] == ["Non Brand", "Demand Gen"] and set(rep["views"]) == {"All", "Non Brand", "Demand Gen"}
    assert rep["views"]["All"]["basis"] == "roas" and rep["range"]["days"] == 30
    assert [r["label"] for r in rep["views"]["Non Brand"]["rows"]] == ["Site visitors", "CRM list", "Weddings", "Home Decor"]       # by spend
    assert [r["label"] for r in rep["views"]["Demand Gen"]["rows"]] == ["Local Landscapers"]


def test_a_strong_audience_where_the_campaign_takes_a_bid_adjustment_is_told_a_number(cache):
    r = row(report(), "CRM list", "Non Brand")
    assert r["verdict"] == "star" and r["adjustment"] == 0.40 and r["adjustable"] and r["mode"] == "observation" and r["bid"] == 0.1
    assert r["action"]["kind"] == "raise" and "+40%" in r["action"]["text"] and "+10%" in r["action"]["text"]
    assert r["kind"] == "customer_match" and r["uses"][0]["campaign"] == "P2_NonBrand"


def test_a_weak_observed_audience_is_lowered_before_it_is_excluded(cache):
    r = row(report(), "Site visitors", "Non Brand")
    assert r["verdict"] == "weak" and r["adjustment"] == -0.5 and r["action"]["kind"] == "lower"
    assert "-50%" in r["action"]["text"] and "exclude" in r["action"]["text"].lower()


def test_an_audience_in_smart_bidding_is_not_given_a_bid_adjustment(cache):
    rep = report()
    leak = row(rep, "Weddings", "Non Brand")
    assert leak["verdict"] == "leak" and not leak["adjustable"] and leak["action"]["kind"] == "exclude" and "Exclude" in leak["action"]["text"]
    insight = next(i for i in rep["views"]["Non Brand"]["insights"] if i["kind"] == "smart")
    assert "Smart Bidding" in insight["evidence"] and "Weddings" in insight["evidence"]


def test_demand_gen_takes_no_adjustment_so_a_strong_one_is_told_to_get_its_own_budget(cache):
    r = row(report(), "Local Landscapers", "Demand Gen")
    assert not r["adjustable"] and r["mode"] == "targeting" and r["verdict"] == "star"
    assert r["action"]["kind"] == "raise" and "budget" in r["action"]["text"] and "%" not in r["action"]["text"]


def test_too_few_conversions_is_never_judged(cache):
    r = row(report(), "Home Decor", "Non Brand")
    assert r["verdict"] == "thin" and r["adjustment"] is None and r["action"] == {"kind": "none", "text": ""} and "too few" in r["why"]


def test_an_account_with_no_conversions_judges_nothing_and_says_so(cache):
    zero = searcher(ag=[ag_row("USER_LIST", "10", cost=100, conv=0)], lists=LISTS, totals=[total_row(cost=500, conv=0)])
    rep = report(zero)
    r = rep["views"]["All"]["rows"][0]
    assert r["verdict"] == "none" and "no conversions" in r["why"]
    assert rep["views"]["All"]["insights"][0]["kind"] == "nodata"


def test_the_insights_name_the_audience_and_carry_the_numbers(cache):
    rep = report()
    ins_all = rep["views"]["Non Brand"]["insights"]
    kinds = {i["kind"] for i in ins_all}
    assert {"push", "trim", "exclude"} <= kinds
    push = next(i for i in ins_all if i["kind"] == "push")
    assert "CRM list" in push["headline"] and "ROAS" in push["evidence"] and "index" in push["evidence"] and push["keys"]
    assert [i["severity"] for i in ins_all] == sorted([i["severity"] for i in ins_all], key=lambda s: {"action": 0, "watch": 1, "info": 2}[s])


def test_which_kind_does_the_most_for_the_money_is_compared(cache):
    rep = report()
    totals = {k["kind"]: k for k in rep["kindTotals"]}
    assert totals["customer_match"]["index"] > totals["remarketing"]["index"] and totals["customer_match"]["count"] == 1
    insight = next(i for i in rep["views"]["All"]["insights"] if i["kind"] == "kinds")
    assert "Customer match" in insight["headline"] and "Custom segments" in insight["evidence"]


def test_lists_that_cannot_serve_and_lists_nobody_uses_are_named(cache):
    rep = report(searcher(ag=AG + [ag_row("USER_LIST", "20", cost=3, conv=0, clk=1)], settings=SETTINGS, lists=LISTS, interests=INTERESTS, totals=TOTALS))
    by = {l["label"]: l for l in rep["lists"]}
    assert not by["Tiny list"]["serves"] and "no members" in by["Tiny list"]["problems"][0] and by["CRM list"]["serves"]
    assert by["Unused list"]["used"] is False
    ins_all = {i["kind"]: i for i in rep["views"]["All"]["insights"]}
    assert "Tiny list" in ins_all["lists"]["evidence"] and "Unused list" in ins_all["unused"]["evidence"] and "Google made" not in ins_all["unused"]["evidence"]


def test_a_closed_list_is_flagged_and_the_exclusions_and_the_pmax_note_are_in(cache):
    excl = [{"campaign": {"name": "P2_NonBrand"}, "adGroup": {"name": "Group"},
             "adGroupCriterion": {"type": "USER_LIST", "displayName": "CRM list", "userList": {"userList": "customers/1/userLists/10"}}}]
    rep = report(searcher(ag=AG, settings=SETTINGS, lists=[list_row("10", "CRM list", "CRM_BASED", status="CLOSED")], interests=INTERESTS, totals=TOTALS, excl_ag=excl))
    assert any("closed" in p for p in rep["lists"][0]["problems"]) and len(rep["exclusions"]) == 1
    kinds = {i["kind"] for i in rep["views"]["All"]["insights"]}
    assert {"exclusions", "pmax"} <= kinds


def test_no_text_the_page_shows_has_an_em_dash(cache):
    rep = report()
    texts = []
    for v in rep["views"].values():
        for i in v["insights"]:
            texts += [i["headline"], i["evidence"], i["action"]]
        for r in v["rows"]:
            texts += [r["why"], r["action"]["text"]]
    for l in rep["lists"]:
        texts += l["problems"]
    assert texts and all("—" not in t for t in texts)


def test_a_second_ask_does_not_read_google_again_and_refresh_is_limited(cache):
    calls = []
    search = searcher(ag=AG, settings=SETTINGS, lists=LISTS, interests=INTERESTS, totals=TOTALS, log=calls)
    report(search)
    n = len(calls)
    report(search)
    assert len(calls) == n
    report(search, force=True)
    assert len(calls) > n
    real = getattr(paid_store, "get_gads_entry", None)
    paid_store.get_gads_entry = lambda key, ttl: (cache.get(key), "2026-06-30T00:00:00+00:00") if key in cache else None
    try:
        with pytest.raises(base.RefreshTooSoon):
            report(search, force=True)
    finally:
        if real is None:
            del paid_store.get_gads_entry
        else:
            paid_store.get_gads_entry = real


def test_a_spent_quota_serves_the_last_saved_read_with_its_date(cache, monkeypatch):
    first = report(now=0)
    assert "stale" not in first

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: None)
    monkeypatch.setattr(paid_store, "get_gads_cache_any", lambda key: (cache.get(key), "2026-06-30T10:00:00+00:00") if key in cache else None, raising=False)
    paid_geo.clear_cache()
    base.forget("")
    old = report(spent, now=10 ** 9)
    assert old["stale"] == "2026-06-30T10:00:00+00:00" and old["views"]["All"]["rows"]
    with pytest.raises(gads.GadsError):
        monkeypatch.setattr(paid_store, "get_gads_cache_any", lambda key: None, raising=False)
        paid_geo.clear_cache()
        base.forget("")
        report(spent, now=10 ** 9)


def test_a_bad_range_or_an_account_that_is_not_position2s_is_refused(cache):
    with pytest.raises(a.BadRequest):
        a.report_for("1234567890", "2026-07-01", "2026-06-01", accounts_fn=lambda: [ACCT], groups_fn=lambda: [])
    with pytest.raises(a.BadRequest):
        a.report_for("9999999999", "2026-06-01", "2026-06-30", accounts_fn=lambda: [ACCT], groups_fn=lambda: [])


# ── The routes ──────────────────────────────────────────────────────────────

def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_the_page_renders_for_staff_and_not_for_anyone_else():
    path = "/p2/paid-marketing/audience-segments"
    assert client_as(STAFF).get(path).status_code == 200 and b"audience_segments.js" in client_as(STAFF).get(path).data
    assert client_as(None).get(path).status_code == 302 and client_as("someone@gmail.com").get(path).status_code == 302


def test_the_report_route_passes_its_query_on_and_returns_the_report(monkeypatch, cache):
    small = report()
    seen = []
    monkeypatch.setattr(a, "report_for", lambda *x, **k: seen.append((x, k)) or small)
    r = client_as(STAFF).get(API + "/report?account=1234567890&from=2026-06-01&to=2026-06-30&refresh=1")
    assert r.status_code == 200 and r.get_json()["account"]["name"] == "Acme Landscaping"
    assert seen[0] == (("1234567890", "2026-06-01", "2026-06-30"), {"force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1234567890&from=2026-06-01&to=2026-06-30")
    assert seen[0][1] == {}


@pytest.mark.parametrize("err, status", [
    (gads.GadsError("not_configured", "not set"), 503), (gads.GadsError("quota", "slow down"), 429), (gads.GadsError("auth", "revoked"), 502),
    (gads.GadsError("api", "google broke"), 502), (a.BadRequest("The start date is after the end date."), 400)])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(a, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890&from=2026-06-01&to=2026-06-30")
    assert r.status_code == status and str(err) in r.get_json()["error"]


def test_a_refresh_that_is_too_soon_says_when_to_try_again_and_the_api_is_staff_only(monkeypatch):
    def boom(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 45)
    monkeypatch.setattr(a, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890&from=2026-06-01&to=2026-06-30&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 45
    assert client_as(None).get(API + "/report?account=1").status_code == 302
