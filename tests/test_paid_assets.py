"""Paid Marketing Agents: Asset Coverage Audit, the rules, the reading, the report and the routes.

Pinned here: what counts as a live asset (enabled, not disapproved, paused or past its end date; under review counts; "limited" is
fine); how a count is judged against a minimum and a target (one sitelink is short, three are thin, four are enough); the reading of
every Google Ads row type (campaign, account, ad group and asset group links, asset sets, Display and Demand Gen ads) into compact
counts; that a part which cannot be read makes its slots "unknown" and never "missing"; the lowest level that holds a live asset
winning (campaign over account), Performance Max logo and business name counting from the campaign, the same shortfall in several
asset groups folded into one line; an MCC, the cache and Refresh, a spent quota, and the routes. Nothing here talks to Google.
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
from tracker import paid_assets as a  # noqa: E402
from tracker import paid_assets_rules as rules  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 16)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
STAFF = "someone@position2.com"
API = "/p2/paid-marketing/api/assets"


# ── Fake Google rows ────────────────────────────────────────────────────────

def camp_row(cid="5", name="Search_Brand", ctype="SEARCH", sub=None):
    c = {"id": cid, "name": name, "advertisingChannelType": ctype, "status": "ENABLED"}
    if sub:
        c["advertisingChannelSubType"] = sub
    return {"campaign": c}


def metric_row(cid="5", imp=1000):
    return {"campaign": {"id": cid}, "metrics": {"impressions": str(imp), "clicks": "40", "costMicros": "12500000", "conversions": 2.5}}


def link_row(cid, field, asset_id, *, status="ENABLED", primary="ELIGIBLE", reasons=None, asset=None, level="campaignAsset"):
    node = {"fieldType": field, "status": status, "primaryStatus": primary}
    if reasons:
        node["primaryStatusReasons"] = reasons
    row = {level: node, "asset": dict({"id": asset_id}, **(asset or {}))}
    if cid is not None:
        row["campaign"] = {"id": cid}
    return row


def sitelink(cid, n, **kw):
    return link_row(cid, "SITELINK", "sl%s" % n, asset={"sitelinkAsset": {"linkText": "Link %s" % n}}, **kw)


def many(cid, field, count, prefix=None, **kw):
    return [link_row(cid, field, "%s%d" % (prefix or field.lower(), i), **kw) for i in range(count)]


def searcher(campaigns=None, metrics=None, shown=None, campaign_assets=None, customer_assets=None, ad_group_assets=None, groups=None,
             group_assets=None, campaign_sets=None, customer_sets=None, ads=None, asset_by_id=None, log=None, fail=(), ad_groups=None):
    """A search function that answers by which query it is asked; `fail` names parts that raise a Google error."""
    ads = ads or {}

    def search(cid, q):
        if log is not None:
            log.append((cid, q))
        part = ("asset_by_id" if "FROM asset WHERE asset.id IN" in q else "campaign_asset_set" if "FROM campaign_asset_set" in q else "customer_asset_set" if "FROM customer_asset_set" in q else
                "shown" if "metrics.impressions > 0" in q else "campaign_asset" if "FROM campaign_asset " in q else
                "customer_asset" if "FROM customer_asset " in q else "ad_group_asset" if "FROM ad_group_asset" in q else
                "asset_group_asset" if "FROM asset_group_asset" in q else "asset_group" if "FROM asset_group " in q else "ad_group" if "FROM ad_group WHERE" in q else
                "metrics" if "metrics.impressions" in q and "FROM campaign " in q else "campaign" if "FROM campaign " in q else
                "ad_group_ad")
        if part in fail:
            raise gads.GadsError("api", "google broke on " + part)
        if part == "ad_group_ad":
            return ads.get(next(t for t in ("RESPONSIVE_DISPLAY_AD", "DEMAND_GEN_MULTI_ASSET_AD", "DEMAND_GEN_VIDEO_RESPONSIVE_AD") if t in q), [])
        return {"campaign": campaigns, "metrics": metrics, "shown": shown, "campaign_asset": campaign_assets, "customer_asset": customer_assets,
                "ad_group_asset": ad_group_assets, "asset_group": groups, "asset_group_asset": group_assets, "campaign_asset_set": campaign_sets,
                "customer_asset_set": customer_sets, "asset_by_id": asset_by_id, "ad_group": ad_groups}[part] or []
    return search


def read(**kw):
    return a.read_account({"id": "1234567890", "name": "Acme Dental"}, search_fn=searcher(**kw), today=TODAY)


def analyse(**kw):
    return a.analyse_account(read(**kw), TODAY)


def slot_of(item, key):
    return next(s for s in item["slots"] if s["key"] == key)


FULL_SEARCH = (many("5", "SITELINK", 4) + many("5", "CALLOUT", 4) + many("5", "STRUCTURED_SNIPPET", 1) + many("5", "CALL", 1) + many("5", "AD_IMAGE", 1) +
               many("5", "BUSINESS_NAME", 1) + many("5", "LOGO", 1))


# ── What counts as live ─────────────────────────────────────────────────────

@pytest.mark.parametrize("link,state", [
    ({"st": "ENABLED", "ps": "ELIGIBLE"}, "live"),
    ({"st": "ENABLED", "ps": "LIMITED", "rs": ["ASSET_APPROVED_LABELED"]}, "live"),            # approved with a label is not a problem
    ({"st": "ENABLED", "ps": "PENDING", "rs": ["ASSET_UNDER_REVIEW"]}, "pending"),
    ({"st": "ENABLED", "ps": "NOT_ELIGIBLE", "rs": ["ASSET_DISAPPROVED"]}, "disapproved"),
    ({"st": "ENABLED", "ps": "", "rs": ["ASSET_DISAPPROVED"]}, "disapproved"),
    ({"st": "ENABLED", "ps": "PAUSED", "rs": ["ASSET_LINK_PAUSED"]}, "paused"),
    ({"st": "PAUSED", "ps": "ELIGIBLE"}, "paused"),
    ({"st": "ENABLED", "ps": "PAUSED", "rs": ["ASSET_LINK_PAUSED", "ASSET_DISAPPROVED"]}, "paused"),   # off already: not also a disapproval
    ({"st": "ENABLED", "ps": "ELIGIBLE", "e": "2026-05-31"}, "expired"),
    ({"st": "ENABLED", "ps": "ELIGIBLE", "e": "2026-10-16"}, "live"),                         # the last day still counts
    ({"st": "ENABLED", "ps": "ELIGIBLE", "e": "2027-01-01"}, "live"),
    ({"st": "ENABLED"}, "live"),                                                               # no status given: not guessed to be a problem
])
def test_a_link_is_live_disapproved_paused_expired_or_under_review(link, state):
    assert rules.link_state(link, TODAY) == state


def test_under_review_counts_toward_coverage_and_the_rest_do_not():
    t = rules.tally([{"ps": "ELIGIBLE"}, {"ps": "PENDING"}, {"ps": "NOT_ELIGIBLE"}, {"ps": "PAUSED"}, {"st": "ENABLED", "ps": "ELIGIBLE", "e": "2020-01-01"}], TODAY)
    assert t == {"live": 1, "pending": 1, "disapproved": 1, "paused": 1, "expired": 1, "have": 2}


# ── Judging a count ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("have,status,severity,add", [
    (0, "missing", "high", 4), (1, "short", "high", 3), (2, "thin", "medium", 2), (3, "thin", "medium", 1), (4, "ok", None, 0), (9, "ok", None, 0)])
def test_sitelinks_need_two_to_show_and_four_to_be_enough(have, status, severity, add):
    assert rules.judge(rules.CAMPAIGN_ASSETS["SITELINK"], have) == (status, severity, add)


def test_a_performance_max_group_below_its_floor_is_short_and_an_optional_kind_is_never_a_gap():
    assert rules.judge(rules.PMAX_GROUP["HEADLINE"], 2) == ("short", "high", 6)
    assert rules.judge(rules.PMAX_GROUP["HEADLINE"], 3) == ("thin", "low", 5)
    assert rules.judge(rules.PMAX_GROUP["YOUTUBE_VIDEO"], 0)[:2] == ("missing", "medium")
    assert rules.judge(rules.CAMPAIGN_ASSETS["PROMOTION"], 0)[:2] == ("missing", "info")             # an opportunity, not a gap
    assert rules.judge(rules.LOCATION, 0)[:2] == ("missing", "info")


def test_the_words_say_what_is_short_what_to_add_and_what_is_wrong_with_the_ones_there():
    spec = rules.CAMPAIGN_ASSETS["SITELINK"]
    assert rules.slot("SITELINK", spec, 0, None, source="campaign")["message"] == "No live sitelinks. Add 4."
    s = rules.slot("SITELINK", spec, 1, {"disapproved": 2, "expired": 1, "pending": 1}, source="campaign")
    assert s["message"] == "1 live sitelink. At least 2 are needed. Add 3 more to reach 4. 2 disapproved. 1 past their end date. 1 under review."
    assert rules.slot("SITELINK", spec, 4, None, source="campaign")["message"] == ""
    assert rules.slot("SITELINK", spec, 3, None, source="campaign")["message"] == "3 live sitelinks. Add 1 more to reach 4."


def test_the_floors_and_targets_are_the_ones_the_page_says_they_are():
    c = rules.CAMPAIGN_ASSETS
    assert [(k, c[k]["min"], c[k]["target"]) for k in ("SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "CALL")] == [("SITELINK", 2, 4), ("CALLOUT", 2, 4), ("STRUCTURED_SNIPPET", 1, 1), ("CALL", 1, 1)]
    g = rules.PMAX_GROUP
    assert [(k, g[k]["min"]) for k in ("HEADLINE", "LONG_HEADLINE", "DESCRIPTION", "MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "LOGO", "BUSINESS_NAME")] == [
        ("HEADLINE", 3), ("LONG_HEADLINE", 1), ("DESCRIPTION", 2), ("MARKETING_IMAGE", 1), ("SQUARE_MARKETING_IMAGE", 1), ("LOGO", 1), ("BUSINESS_NAME", 1)]
    assert all(k in rules.CAMPAIGN_ASSETS for k in rules.CAMPAIGN_ASSET_ORDER) and len(rules.CAMPAIGN_ASSET_ORDER) == len(rules.CAMPAIGN_ASSETS)


# ── Reading Google's rows ───────────────────────────────────────────────────

def test_campaigns_and_their_30_day_numbers_are_read():
    c = a.read_campaigns([camp_row("5", "Brand"), camp_row("6", "PMax", "PERFORMANCE_MAX", "SMART_CAMPAIGN"), {"campaign": {}}])
    assert c["5"] == {"id": "5", "name": "Brand", "type": "SEARCH", "subType": ""} and c["6"]["type"] == "PERFORMANCE_MAX" and len(c) == 2
    m = a.read_metrics([metric_row("5", 1000), metric_row("5", 500)])
    assert m["5"] == {"impressions": 1500, "clicks": 80, "cost": 25.0, "conversions": 5.0}


def test_a_bucket_counts_states_names_the_problem_assets_and_finds_the_ones_never_shown():
    links = [{"f": "SITELINK", "st": "ENABLED", "ps": "ELIGIBLE", "rs": [], "a": "1", "l": "Shown", "e": ""},
             {"f": "SITELINK", "st": "ENABLED", "ps": "ELIGIBLE", "rs": [], "a": "2", "l": "Never shown", "e": ""},
             {"f": "SITELINK", "st": "ENABLED", "ps": "NOT_ELIGIBLE", "rs": ["ASSET_DISAPPROVED"], "a": "3", "l": "Bad", "e": ""},
             {"f": "SITELINK", "st": "PAUSED", "ps": "PAUSED", "rs": [], "a": "4", "l": "Off", "e": ""},
             {"f": "SITELINK", "st": "ENABLED", "ps": "ELIGIBLE", "rs": [], "a": "5", "l": "Old offer", "e": "2026-05-31"}]
    b = a.bucket(links, TODAY, shown={("9", "1")}, scope="9")
    assert (b["live"], b["disapproved"], b["paused"], b["expired"], b["notShown"]) == (2, 1, 1, 1, 1)
    assert b["bad"] == [{"label": "Bad", "state": "disapproved"}, {"label": "Old offer", "state": "expired"}]
    assert b["ids"] == ["3"] and b["expiredIds"] == ["5"]                              # a disapproved asset and one past its end date are named apart
    assert a.bucket(links, TODAY, shown=None)["notShown"] == 0                           # shown could not be read: nothing is called unshown
    assert a.bucket([dict(links[0], f="BUSINESS_NAME")], TODAY, shown=set(), scope="9")["notShown"] == 0     # not a kind Google reports shows for


def test_only_five_problem_names_are_kept_per_place_and_buckets_add_up():
    bad = [{"f": "AD_IMAGE", "st": "ENABLED", "ps": "NOT_ELIGIBLE", "rs": [], "a": str(i), "l": "img%d" % i, "e": ""} for i in range(20)]
    b = a.bucket(bad, TODAY)
    assert b["disapproved"] == 20 and len(b["bad"]) == a.BAD_EXAMPLES and len(b["ids"]) == 20
    m = a.merge(b, a.bucket(bad[:2], TODAY), None)
    assert m["disapproved"] == 22 and m["have"] == 0 and len(m["bad"]) == a.BAD_EXAMPLES and len(m["ids"]) == 20


def test_campaign_and_account_assets_are_read_with_their_labels_and_end_dates():
    rows = [sitelink("5", 1), link_row("5", "CALLOUT", "c1", asset={"calloutAsset": {"calloutText": "Open Saturdays"}}, status="ENABLED", primary="NOT_ELIGIBLE"),
            link_row("5", "PROMOTION", "p1", asset={"promotionAsset": {"promotionTarget": "Free whitening", "endDate": "2026-05-31"}})]
    ca = a.read_campaign_assets(rows, {("5", "sl1")}, TODAY)["5"]
    assert ca["SITELINK"]["live"] == 1 and ca["SITELINK"]["notShown"] == 0
    assert ca["CALLOUT"]["bad"] == [{"label": "Open Saturdays", "state": "disapproved"}]
    assert ca["PROMOTION"]["expired"] == 1 and ca["PROMOTION"]["bad"] == [{"label": "Free whitening", "state": "expired"}]
    cu = a.read_customer_assets([link_row(None, "CALLOUT", "c9", level="customerAsset")], TODAY)
    assert cu["CALLOUT"]["live"] == 1


def test_an_ad_group_that_holds_its_own_live_assets_is_noted_and_a_dead_one_is_not():
    rows = [{"campaign": {"id": "5"}, "adGroup": {"id": "g1"}, "adGroupAsset": {"fieldType": "SITELINK", "status": "ENABLED", "primaryStatus": "ELIGIBLE"}},
            {"campaign": {"id": "5"}, "adGroup": {"id": "g2"}, "adGroupAsset": {"fieldType": "SITELINK", "status": "ENABLED", "primaryStatus": "NOT_ELIGIBLE"}},
            {"campaign": {"id": "5"}, "adGroup": {"id": "g1"}, "adGroupAsset": {"fieldType": "SITELINK", "status": "ENABLED", "primaryStatus": "LIMITED"}}]
    assert a.read_ad_group_assets(rows) == {"5": {"SITELINK": ["g1"]}}


def test_location_sets_count_only_location_kinds():
    camp = [{"campaign": {"id": "5"}, "assetSet": {"type": "LOCATION_SYNC"}}, {"campaign": {"id": "6"}, "assetSet": {"type": "MERCHANT_CENTER_FEED"}}]
    cust = [{"assetSet": {"type": "BUSINESS_PROFILE_DYNAMIC_LOCATION_GROUP"}}, {"assetSet": {"type": "PAGE_FEED"}}]
    assert a.read_sets(camp, cust) == {"account": 1, "campaigns": {"5": 1}}


def test_an_asset_group_is_read_with_its_assets_per_kind_and_a_group_listed_twice_is_one():
    groups = [{"campaign": {"id": "6"}, "assetGroup": {"id": "700", "name": "Crowns"}}] * 2
    rows = [{"assetGroup": {"id": "700"}, "assetGroupAsset": {"fieldType": "HEADLINE", "status": "ENABLED", "primaryStatus": "ELIGIBLE"}, "asset": {"id": str(i)}} for i in range(3)]
    rows += [{"assetGroup": {"id": "700"}, "assetGroupAsset": {"fieldType": "YOUTUBE_VIDEO", "status": "ENABLED", "primaryStatus": "NOT_ELIGIBLE", "primaryStatusReasons": ["ASSET_DISAPPROVED"]},
              "asset": {"id": "v1", "youtubeVideoAsset": {"youtubeVideoTitle": "Tour"}}},
             {"assetGroup": {"id": "999"}, "assetGroupAsset": {"fieldType": "HEADLINE", "status": "ENABLED"}, "asset": {"id": "x"}}]      # a group that is not live
    g = a.read_groups(groups, rows, TODAY)
    assert len(g) == 1 and g[0]["campaignId"] == "6" and g[0]["name"] == "Crowns"
    assert g[0]["assets"]["HEADLINE"]["live"] == 3 and g[0]["assets"]["YOUTUBE_VIDEO"]["bad"] == [{"label": "Tour", "state": "disapproved"}]


def test_ads_are_read_as_counts_per_kind_whatever_the_format():
    img = lambda n: [{"asset": "customers/1/assets/%d" % i} for i in range(n)]      # noqa: E731
    display = {"campaign": {"id": "7"}, "adGroup": {"id": "g", "name": "Level 1"}, "adGroupAd": {"ad": {"id": "a1", "type": "RESPONSIVE_DISPLAY_AD", "responsiveDisplayAd": {
        "marketingImages": img(3), "squareMarketingImages": img(2), "squareLogoImages": img(1), "headlines": [{"text": "A"}, {"text": "B"}],
        "longHeadline": {"text": "A long one"}, "descriptions": [{"text": "D"}], "businessName": "Acme"}}}}
    d = a.read_ads("RESPONSIVE_DISPLAY_AD", [display])[0]
    assert d["counts"] == {"MARKETING_IMAGE": 3, "SQUARE_MARKETING_IMAGE": 2, "LOGO": 0, "SQUARE_LOGO": 1, "HEADLINE": 2, "LONG_HEADLINE": 1, "DESCRIPTION": 1,
                           "BUSINESS_NAME": 1, "YOUTUBE_VIDEO": 0}
    assert d["campaignId"] == "7" and d["adGroup"] == "Level 1"
    empty = {"campaign": {"id": "7"}, "adGroup": {"id": "g"}, "adGroupAd": {"ad": {"id": "a2", "demandGenMultiAssetAd": {"businessName": "  ", "headlines": [{"text": ""}]}}}}
    assert a.read_ads("DEMAND_GEN_MULTI_ASSET_AD", [empty])[0]["counts"]["BUSINESS_NAME"] == 0


# ── The queries ─────────────────────────────────────────────────────────────

def test_the_queries_leave_out_removed_links_and_only_ask_for_what_the_account_has():
    log = []
    read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, log=log)
    qs = {q.split(" FROM ")[1].split(" ")[0]: q for _c, q in log}
    assert "campaign_asset.status != REMOVED" in next(q for _c, q in log if "FROM campaign_asset " in q and "impressions > 0" not in q)
    assert "customer_asset.status != REMOVED" in qs["customer_asset"] and "ad_group_asset.status = ENABLED" in qs["ad_group_asset"]
    assert "metrics.impressions > 0" in next(q for _c, q in log if "campaign_asset " in q and "impressions > 0" in q)
    assert not any("FROM asset_group" in q or "responsive_display_ad" in q or "demand_gen" in q for _c, q in log)         # a Search-only account


def test_performance_max_display_and_demand_gen_accounts_also_read_their_groups_and_ads():
    log = []
    read(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX"), camp_row("7", "Disp", "DISPLAY"), camp_row("8", "DG", "DEMAND_GEN")], log=log)
    text = " ".join(q for _c, q in log)
    assert "FROM asset_group " in text and "FROM asset_group_asset" in text and "responsive_display_ad" in text
    assert "demand_gen_multi_asset_ad" in text and "demand_gen_video_responsive_ad" in text and "WHERE campaign.status = ENABLED AND asset_group.status = ENABLED" in text


# ── A part that cannot be read is unknown, never missing ────────────────────

def test_a_failing_read_is_a_warning_and_its_slots_say_unknown_not_missing():
    raw = read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, fail=("campaign_asset",))
    assert raw["ok"]["campaignAssets"] is False and any("campaign assets could not be read" in w for w in raw["warnings"])
    item = a.analyse_account(raw, TODAY)["items"][0]
    sl = slot_of(item, "SITELINK")
    assert sl["status"] == "unknown" and sl.get("severity") is None and sl["have"] is None
    assert not any(g["key"] == "SITELINK" for g in item["gaps"])                     # nothing is claimed about what could not be seen


def test_the_campaign_list_failing_is_an_error_but_the_numbers_failing_only_costs_the_ordering():
    def boom(cid, q):
        raise gads.GadsError("api", "no")
    with pytest.raises(gads.GadsError):
        a.read_account({"id": "1", "name": "x"}, search_fn=boom, today=TODAY)
    raw = read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, fail=("metrics",))
    assert raw["ok"]["metrics"] is False and a.analyse_account(raw, TODAY)["items"][0]["impressions"] == 0


def test_a_failing_location_read_is_unknown_and_failing_shown_does_not_invent_unshown_assets():
    raw = read(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, metrics=[metric_row("5")], fail=("shown", "campaign_asset_set"))
    item = a.analyse_account(raw, TODAY)["items"][0]
    assert slot_of(item, "LOCATION")["status"] == "unknown" and item["notShown"] == 0


# ── Holding a campaign against the rules ────────────────────────────────────

def test_a_fully_covered_search_campaign_has_no_gaps():
    item = analyse(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, metrics=[metric_row("5")])["items"][0]
    assert item["gaps"] == [] and item["severity"] is None and item["counts"] == {"high": 0, "medium": 0, "low": 0}
    assert slot_of(item, "SITELINK")["status"] == "ok" and slot_of(item, "SITELINK")["source"] == "campaign"


def test_one_sitelink_and_no_callouts_are_the_first_things_to_fix_and_are_said_exactly():
    rows = many("5", "SITELINK", 1) + many("5", "STRUCTURED_SNIPPET", 1)
    item = analyse(campaigns=[camp_row("5")], campaign_assets=rows, metrics=[metric_row("5")])["items"][0]
    top = item["gaps"][:2]
    assert [(g["label"], g["severity"], g["add"]) for g in top] == [("Sitelinks", "high", 3), ("Callouts", "high", 4)]
    assert top[0]["message"] == "1 live sitelink. At least 2 are needed. Add 3 more to reach 4." and item["severity"] == "high"
    assert [g["label"] for g in item["gaps"] if g["severity"] == "medium"] == ["Images"]


def test_a_campaign_with_none_of_its_own_uses_the_accounts_and_the_account_s_problems_are_not_blamed_on_it():
    cu = [link_row(None, "CALLOUT", "c%d" % i, level="customerAsset") for i in range(5)] + [link_row(None, "CALLOUT", "cx", primary="NOT_ELIGIBLE", level="customerAsset")]
    item = analyse(campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 4), customer_assets=cu, metrics=[metric_row("5")])["items"][0]
    co = slot_of(item, "CALLOUT")
    assert co["have"] == 5 and co["source"] == "account" and co["status"] == "ok" and "disapproved" not in co
    acct = analyse(campaigns=[camp_row("5")], customer_assets=cu)["accountAssets"]
    assert [(s["key"], s["have"], s["disapproved"]) for s in acct["slots"]] == [("CALLOUT", 5, 1)]


def test_the_campaigns_own_assets_win_even_when_there_are_fewer_than_the_accounts():
    cu = [link_row(None, "SITELINK", "a%d" % i, level="customerAsset") for i in range(8)]
    item = analyse(campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 1), customer_assets=cu)["items"][0]
    sl = slot_of(item, "SITELINK")
    assert (sl["have"], sl["source"], sl["status"]) == (1, "campaign", "short")


def test_when_every_campaign_link_is_dead_the_accounts_serve_and_the_dead_ones_are_still_named():
    rows = [link_row("5", "SITELINK", "x", primary="NOT_ELIGIBLE", asset={"sitelinkAsset": {"linkText": "Broken"}})]
    cu = [link_row(None, "SITELINK", "a%d" % i, level="customerAsset") for i in range(4)]
    sl = slot_of(analyse(campaigns=[camp_row("5")], campaign_assets=rows, customer_assets=cu)["items"][0], "SITELINK")
    assert (sl["have"], sl["source"], sl["disapproved"]) == (4, "account", 1) and sl["bad"][0]["label"] == "Broken"


def test_expired_sitelinks_do_not_count_and_say_so():
    rows = [link_row("5", "SITELINK", "old", asset={"sitelinkAsset": {"linkText": "Free Teeth Whitening", "endDate": "2026-05-31"}})]
    item = analyse(campaigns=[camp_row("5")], campaign_assets=rows)["items"][0]
    sl = slot_of(item, "SITELINK")
    assert sl["have"] == 0 and sl["expired"] == 1 and "1 past their end date" in sl["message"] and item["problems"]["expired"] == 1


def test_ad_groups_with_their_own_assets_are_mentioned_on_a_short_campaign():
    agl = {"campaign": {"id": "5"}, "adGroup": {"id": "g1"}, "adGroupAsset": {"fieldType": "SITELINK", "status": "ENABLED", "primaryStatus": "ELIGIBLE"}}
    sl = slot_of(analyse(campaigns=[camp_row("5")], ad_group_assets=[agl])["items"][0], "SITELINK")
    assert sl["adGroups"] == 1 and sl["message"].endswith("1 ad group holds its own.")
    two = [dict(agl, adGroup={"id": "g2"}), agl]
    assert slot_of(analyse(campaigns=[camp_row("5")], ad_group_assets=two)["items"][0], "SITELINK")["message"].endswith("2 ad groups hold their own.")
    covered = slot_of(analyse(campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 4), ad_group_assets=two)["items"][0], "SITELINK")
    assert covered["status"] == "ok" and not covered.get("message")


def test_a_campaign_whose_every_ad_group_holds_its_own_sitelinks_is_not_called_bare():
    """The lowest level that holds a live asset is what serves: three ad groups with their own sitelinks are covered although the campaign has none."""
    def agl(g):
        return {"campaign": {"id": "5"}, "adGroup": {"id": g}, "adGroupAsset": {"fieldType": "SITELINK", "status": "ENABLED", "primaryStatus": "ELIGIBLE"}}
    def groups(*ids):
        return [{"campaign": {"id": "5"}, "adGroup": {"id": g}} for g in ids]
    every = slot_of(analyse(campaigns=[camp_row("5")], ad_group_assets=[agl("g1"), agl("g2"), agl("g3")], ad_groups=groups("g1", "g2", "g3"))["items"][0], "SITELINK")
    assert every["status"] == "ok" and not every.get("severity") and not every.get("add") and every["coveredByAdGroups"] and not every.get("message")
    some = slot_of(analyse(campaigns=[camp_row("5")], ad_group_assets=[agl("g1"), agl("g2")], ad_groups=groups("g1", "g2", "g3"))["items"][0], "SITELINK")
    assert some["status"] == "missing" and some.get("severity") == "high" and "2 ad groups hold their own" in some["message"]     # the third ad group has none
    unread = slot_of(analyse(campaigns=[camp_row("5")], ad_group_assets=[agl("g1")], ad_groups=[])["items"][0], "SITELINK")
    assert unread["status"] == "missing"                                                                                       # no count of ad groups: never assumed covered


def test_links_never_shown_in_30_days_are_noted_only_on_a_campaign_that_was_shown():
    rows = many("5", "SITELINK", 4)
    shown = [{"campaign": {"id": "5"}, "asset": {"id": "sitelink0"}, "metrics": {"impressions": "10"}}, {"campaign": {"id": "5"}, "asset": {"id": "sitelink1"}, "metrics": {"impressions": "0"}}]
    item = analyse(campaigns=[camp_row("5"), camp_row("6")], campaign_assets=rows + many("6", "SITELINK", 4), shown=shown, metrics=[metric_row("5"), metric_row("6", 0)])["items"]
    by = {i["id"]: i for i in item}
    assert slot_of(by["5"], "SITELINK")["notShown"] == 3 and by["5"]["notShown"] == 3
    assert by["6"]["notShown"] == 0                                                   # it had no impressions at all, so no asset could have been shown


def test_a_search_campaign_has_images_name_and_logo_but_a_performance_max_one_does_not():
    raw = read(campaigns=[camp_row("5"), camp_row("6", "PMax", "PERFORMANCE_MAX")])
    by = {i["id"]: i for i in a.analyse_account(raw, TODAY)["items"]}
    assert {s["key"] for s in by["5"]["slots"]} >= {"AD_IMAGE", "BUSINESS_NAME", "LOGO"}
    assert not {s["key"] for s in by["6"]["slots"]} & {"AD_IMAGE", "BUSINESS_NAME", "LOGO"} and "SITELINK" in {s["key"] for s in by["6"]["slots"]}


def test_location_comes_from_the_campaigns_own_set_or_the_accounts_and_is_never_a_gap():
    cs = [{"campaign": {"id": "5"}, "assetSet": {"type": "LOCATION_SYNC"}}]
    cu = [{"assetSet": {"type": "STATIC_LOCATION_GROUP"}}]
    by = {i["id"]: i for i in analyse(campaigns=[camp_row("5"), camp_row("6")], campaign_sets=cs, customer_sets=cu)["items"]}
    assert (slot_of(by["5"], "LOCATION")["source"], slot_of(by["6"], "LOCATION")["source"]) == ("campaign", "account")
    none = analyse(campaigns=[camp_row("5")])["items"][0]
    assert slot_of(none, "LOCATION")["status"] == "missing" and not any(g["key"] == "LOCATION" for g in none["gaps"])


# ── Performance Max, Display, Demand Gen ────────────────────────────────────

def group(gid="700", name="Crowns", cid="6"):
    return {"campaign": {"id": cid}, "assetGroup": {"id": gid, "name": name}}


def gassets(gid, **counts):
    rows = []
    for field, n in counts.items():
        rows += [{"assetGroup": {"id": gid}, "assetGroupAsset": {"fieldType": field.upper(), "status": "ENABLED", "primaryStatus": "ELIGIBLE"}, "asset": {"id": "%s%s%d" % (gid, field, i)}} for i in range(n)]
    return rows


FULL_GROUP = dict(headline=8, long_headline=3, description=4, marketing_image=3, square_marketing_image=3, portrait_marketing_image=1, logo=1, business_name=1, youtube_video=1)


def test_a_complete_performance_max_group_has_no_gaps_and_a_thin_one_is_told_what_to_add():
    full = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[group()], group_assets=gassets("700", **FULL_GROUP))["items"][0]
    assert not [g for g in full["gaps"] if g["scope"].startswith("Asset group") or g["scope"].endswith("asset groups)")]
    thin = dict(FULL_GROUP, headline=2, marketing_image=0, youtube_video=0)
    item = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[group()], group_assets=gassets("700", **thin))["items"][0]
    gaps = {g["label"]: g for g in item["gaps"] if g["scope"].startswith("Asset group")}
    assert gaps["Headlines"]["severity"] == "high" and gaps["Headlines"]["add"] == 6 and gaps["Landscape images"]["severity"] == "high" and gaps["Videos"]["severity"] == "medium"


def test_the_logo_and_business_name_count_from_the_campaign_when_the_group_has_none():
    rows = dict(FULL_GROUP, logo=0, business_name=0)
    camp = many("6", "BUSINESS_NAME", 1) + many("6", "LOGO", 1)
    item = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[group()], group_assets=gassets("700", **rows), campaign_assets=camp)["items"][0]
    g = item["groups"][0]
    by = {s["key"]: s for s in g["slots"]}
    assert (by["LOGO"]["have"], by["LOGO"]["source"], by["BUSINESS_NAME"]["source"]) == (1, "campaign", "campaign") and g["severity"] is None
    none = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[group()], group_assets=gassets("700", **rows))["items"][0]
    assert {s["key"]: s for s in none["groups"][0]["slots"]}["LOGO"]["status"] == "missing"


def test_the_same_shortfall_in_several_asset_groups_is_one_line_naming_them():
    names = ["Crowns", "Dentures", "General", "Implants", "Veneers"]
    groups = [group(str(700 + i), n) for i, n in enumerate(names)] + [group("800", "Complete")]
    rows = []
    for i in range(5):
        rows += gassets(str(700 + i), **dict(FULL_GROUP, portrait_marketing_image=0, headline=4))
    rows += gassets("800", **FULL_GROUP)
    item = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=groups, group_assets=rows)["items"][0]
    folded = [g for g in item["gaps"] if g["label"] == "Portrait images"]
    assert len(folded) == 1 and folded[0]["scope"] == "5 asset groups (Crowns, Dentures, General and 2 more)"
    assert len([g for g in item["gaps"] if g["label"] == "Headlines"]) == 1 and len(item["groups"]) == 6          # the detail keeps every group


def test_a_display_ad_is_held_to_its_format_and_the_ads_are_folded():
    def ad(i, logos):
        img = lambda n: [{"asset": "x%d" % k} for k in range(n)]      # noqa: E731
        return {"campaign": {"id": "7"}, "adGroup": {"id": "g%d" % i, "name": "Level %d" % i}, "adGroupAd": {"ad": {"id": str(i), "type": "RESPONSIVE_DISPLAY_AD", "responsiveDisplayAd": {
            "marketingImages": img(3), "squareMarketingImages": img(3), "squareLogoImages": img(logos), "headlines": [{"text": "A"}] * 3, "longHeadline": {"text": "L"},
            "descriptions": [{"text": "D"}] * 3, "businessName": "Acme", "youtubeVideos": img(1)}}}}
    item = analyse(campaigns=[camp_row("7", "Disp", "DISPLAY")], ads={"RESPONSIVE_DISPLAY_AD": [ad(1, 0), ad(2, 0), ad(3, 1)]})["items"][0]
    assert item["slots"] == [] and len(item["ads"]) == 3 and item["ads"][2]["severity"] is None
    assert [(g["label"], g["severity"], g["scope"]) for g in item["gaps"]] == [("Logos", "medium", "2 ads (Level 1, Level 2)")]


def test_a_demand_gen_image_ad_with_no_image_of_any_shape_is_the_first_gap():
    ad = {"campaign": {"id": "8"}, "adGroup": {"id": "g", "name": "Leads"}, "adGroupAd": {"ad": {"id": "1", "type": "DEMAND_GEN_MULTI_ASSET_AD", "demandGenMultiAssetAd": {
        "logoImages": [{"asset": "l"}], "headlines": [{"text": "A"}] * 3, "descriptions": [{"text": "D"}] * 3, "businessName": "Acme"}}}}
    item = analyse(campaigns=[camp_row("8", "DG", "DEMAND_GEN")], ads={"DEMAND_GEN_MULTI_ASSET_AD": [ad]})["items"][0]
    assert item["gaps"][0]["label"] == "Images" and item["gaps"][0]["severity"] == "high" and "No live images" in item["gaps"][0]["message"]
    one = {"campaign": {"id": "8"}, "adGroup": {"id": "g", "name": "Leads"}, "adGroupAd": {"ad": {"id": "2", "type": "DEMAND_GEN_MULTI_ASSET_AD", "demandGenMultiAssetAd": {
        "squareMarketingImages": [{"asset": "s"}], "logoImages": [{"asset": "l"}], "headlines": [{"text": "A"}] * 3, "descriptions": [{"text": "D"}] * 3, "businessName": "Acme"}}}}
    ok = analyse(campaigns=[camp_row("8", "DG", "DEMAND_GEN")], ads={"DEMAND_GEN_MULTI_ASSET_AD": [one]})["items"][0]
    assert "Images" not in [g["label"] for g in ok["gaps"]] and {g["label"] for g in ok["gaps"]} == {"Landscape images", "Portrait images"}


def test_a_demand_gen_video_ad_needs_a_video_a_logo_and_a_name():
    ad = {"campaign": {"id": "8"}, "adGroup": {"id": "g", "name": "Video"}, "adGroupAd": {"ad": {"id": "1", "type": "DEMAND_GEN_VIDEO_RESPONSIVE_AD", "demandGenVideoResponsiveAd": {
        "headlines": [{"text": "A"}] * 3, "longHeadlines": [{"text": "L"}] * 2, "descriptions": [{"text": "D"}] * 3}}}}
    item = analyse(campaigns=[camp_row("8", "DG", "DEMAND_GEN")], ads={"DEMAND_GEN_VIDEO_RESPONSIVE_AD": [ad]})["items"][0]
    assert {g["label"] for g in item["gaps"]} == {"Videos", "Logo", "Business name"} and {g["severity"] for g in item["gaps"]} == {"high"}


# ── The report ──────────────────────────────────────────────────────────────

def test_other_campaign_types_are_listed_as_not_audited_not_judged():
    rep = a.assemble({"accounts": [read(campaigns=[camp_row("5"), camp_row("9", "Shop", "SHOPPING"), camp_row("10", "Smart", "SMART"), camp_row("11", "S2", "SMART")])], "warnings": []},
                     ACCT, TODAY)
    assert [i["id"] for i in rep["campaigns"]] == ["5"]
    assert [(n["label"], n["campaigns"]) for n in rep["notAudited"]] == [("Smart (Google manages the assets)", 2), ("Shopping (built from the product feed)", 1)]


def test_the_work_is_ordered_worst_gap_first_then_busiest():
    camps = [camp_row("1", "Full"), camp_row("2", "Medium busy"), camp_row("3", "High quiet"), camp_row("4", "High busy")]
    rows = FULL_SEARCH and (many("1", "SITELINK", 4) + many("1", "CALLOUT", 4) + many("1", "STRUCTURED_SNIPPET", 1) + many("1", "CALL", 1) + many("1", "AD_IMAGE", 1) +
                            many("1", "BUSINESS_NAME", 1) + many("1", "LOGO", 1))
    rows += many("2", "SITELINK", 4) + many("2", "CALLOUT", 4) + many("2", "CALL", 1) + many("2", "AD_IMAGE", 1) + many("2", "BUSINESS_NAME", 1) + many("2", "LOGO", 1)     # no snippet: medium
    metrics = [metric_row("1", 9000), metric_row("2", 5000), metric_row("3", 10), metric_row("4", 400)]
    rep = a.assemble({"accounts": [read(campaigns=camps, campaign_assets=rows, metrics=metrics)], "warnings": []}, ACCT, TODAY)
    assert [i["id"] for i in rep["campaigns"]] == ["4", "3", "2", "1"] and [i["severity"] for i in rep["campaigns"]] == ["high", "high", "medium", None]


def test_the_summary_counts_gaps_disapproved_assets_once_and_the_impressions_at_stake():
    camps = [camp_row("1"), camp_row("2")]
    shared = dict(asset={"sitelinkAsset": {"linkText": "Shared"}})
    rows = [link_row(c, "SITELINK", "same", primary="NOT_ELIGIBLE", **shared) for c in ("1", "2")] + many("1", "CALLOUT", 4) + many("2", "CALLOUT", 4)
    rep = a.assemble({"accounts": [read(campaigns=camps, campaign_assets=rows, metrics=[metric_row("1", 3000), metric_row("2", 1000)])], "warnings": []}, ACCT, TODAY)
    s = rep["summary"]
    assert s["campaigns"] == 2 and s["fixFirst"] == 2 and s["impressions"] == 4000 and s["impressionsFixFirst"] == 4000
    assert s["disapprovedAssets"] == 1                                               # the same asset linked to two campaigns is one asset
    assert s["gaps"]["high"] == 2 and rep["window"] == {"days": 30, "to": "2026-10-16", "from": "2026-09-17"}


def test_coverage_counts_each_kind_over_the_campaigns_it_applies_to():
    camps = [camp_row("1"), camp_row("2"), camp_row("3"), camp_row("4", "PMax", "PERFORMANCE_MAX")]
    rows = many("1", "SITELINK", 4) + many("2", "SITELINK", 2) + many("4", "SITELINK", 4)
    cov = {c["key"]: c for c in a.assemble({"accounts": [read(campaigns=camps, campaign_assets=rows)], "warnings": []}, ACCT, TODAY)["summary"]["coverage"]}
    assert (cov["SITELINK"]["campaigns"], cov["SITELINK"]["atTarget"], cov["SITELINK"]["below"], cov["SITELINK"]["none"]) == (4, 2, 1, 1)
    assert cov["AD_IMAGE"]["campaigns"] == 3 and cov["PROMOTION"]["optional"] is True and cov["SITELINK"]["optional"] is False


def test_a_slot_keeps_what_the_page_needs_and_drops_what_is_zero():
    item = analyse(campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 4))["items"][0]
    s = slot_of(item, "SITELINK")
    assert set(s) == {"key", "label", "have", "min", "target", "status", "source"}
    assert all(k in slot_of(item, "CALLOUT") for k in ("severity", "add", "message"))


def test_an_mcc_adds_its_accounts_says_which_failed_and_all_failing_is_an_error():
    members = [{"id": "1111111111", "name": "One"}, {"id": "2222222222", "name": "Two"}, {"id": "3333333333", "name": "Three"}]

    def search(cid, q):
        if cid == "2222222222":
            raise gads.GadsError("permission", "no access")
        return searcher(campaigns=[camp_row("5", "C" + cid[:1])], campaign_assets=FULL_SEARCH, fail=("metrics",) if cid == "3333333333" else ())(cid, q)
    raw = a.google_raw({"id": "mcc-1", "name": "Group", "members": members}, search_fn=search, today=TODAY)
    assert [x["name"] for x in raw["accounts"]] == ["One", "Three"] and raw["members"] == ["One", "Two", "Three"]
    assert raw["warnings"][0] == "Two could not be read: no access" and any("Three: the last 30 days" in w for w in raw["warnings"])
    rep = a.assemble(raw, {"id": "mcc-1", "name": "Group", "currency": "USD", "members": members}, TODAY)
    assert rep["account"]["isGroup"] and rep["account"]["accountCount"] == 3 and {i["account"] for i in rep["campaigns"]} == {"One", "Three"}
    assert len({i["key"] for i in rep["campaigns"]}) == 2                             # the same campaign id in two accounts is two campaigns

    def boom(cid, q):
        raise gads.GadsError("quota", "slow down")
    with pytest.raises(gads.GadsError):
        a.google_raw({"id": "mcc-1", "name": "G", "members": members[:1]}, search_fn=boom)


# ── The cache ───────────────────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    from tracker import paid_assets_detail
    from tracker import paid_geo
    paid_geo.clear_cache()
    paid_assets_detail.mem_clear()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def report(search, **kw):
    return a.report_for("1234567890", accounts_fn=lambda: [dict(ACCT, timeZone="America/New_York")], groups_fn=lambda: [], search_fn=search, today=TODAY, **kw)


def test_a_second_ask_does_not_read_google_again_and_refresh_is_limited(cache):
    calls = []
    search = searcher(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH, metrics=[metric_row("5")], log=calls)
    first = report(search)
    n = len(calls)
    assert first["campaigns"][0]["id"] == "5" and first["account"]["name"] == "Acme Dental"
    report(search)
    assert len(calls) == n
    report(search, force=True)
    assert len(calls) > n
    real = getattr(paid_store, "get_gads_entry", None)
    paid_store.get_gads_entry = lambda key, ttl: (cache.get(key), "2026-10-16T00:00:00+00:00") if key in cache else None
    try:
        with pytest.raises(base.RefreshTooSoon):
            report(search, force=True)
    finally:
        if real is None:
            del paid_store.get_gads_entry
        else:
            paid_store.get_gads_entry = real


def test_a_spent_quota_serves_the_last_saved_read_with_its_date_and_otherwise_raises(cache, monkeypatch):
    from tracker import paid_geo

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        report(spent)
    report(searcher(campaigns=[camp_row("5")], campaign_assets=FULL_SEARCH), now=0)
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: None)
    monkeypatch.setattr(paid_store, "get_gads_cache_any", lambda key: (cache.get(key), "2026-10-15T10:00:00+00:00") if key in cache else None, raising=False)
    paid_geo.clear_cache()
    base.forget("")
    old = report(spent, now=10 ** 9)
    assert old["stale"] == "2026-10-15T10:00:00+00:00" and old["campaigns"][0]["id"] == "5"


def test_an_account_that_is_not_position2s_is_refused(cache):
    with pytest.raises(a.BadRequest):
        a.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY)


# ── The routes ──────────────────────────────────────────────────────────────

def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def small():
    return a.assemble({"accounts": [read(campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 1))], "warnings": []}, ACCT, TODAY)


def test_the_page_renders_for_staff_and_not_for_anyone_else():
    path = "/p2/paid-marketing/asset-coverage"
    page = client_as(STAFF).get(path)
    assert page.status_code == 200 and b"asset_coverage.js" in page.data and b"asset_coverage.css" in page.data and b"Asset Coverage Audit" in page.data
    assert client_as(None).get(path).status_code == 302 and client_as("someone@gmail.com").get(path).status_code == 302


def test_the_report_route_returns_the_report_and_passes_refresh_on(monkeypatch, small):
    seen = []
    monkeypatch.setattr(a, "report_for", lambda *x, **k: seen.append((x, k)) or small)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json()["campaigns"][0]["gaps"][0]["label"] == "Sitelinks"
    assert seen[0] == (("1234567890",), {"force": True})
    seen.clear()
    client_as(STAFF).get(API + "/report?account=1234567890")
    assert seen[0] == (("1234567890",), {})


@pytest.mark.parametrize("err, status", [
    (gads.GadsError("not_configured", "not set"), 503), (gads.GadsError("quota", "slow down"), 429), (gads.GadsError("auth", "revoked"), 502),
    (gads.GadsError("api", "google broke"), 502), (a.BadRequest("That account is not one of Position2's Google Ads accounts."), 400)])
def test_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, err, status):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(a, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890")
    assert r.status_code == status and str(err) in r.get_json()["error"]


def test_a_refresh_that_is_too_soon_says_when_to_try_again_and_the_api_is_staff_only(monkeypatch):
    def boom(*x, **k):
        raise base.RefreshTooSoon("Refreshed a moment ago.", 45)
    monkeypatch.setattr(a, "report_for", boom)
    r = client_as(STAFF).get(API + "/report?account=1234567890&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 45
    assert client_as(None).get(API + "/report?account=1").status_code == 302 and client_as("someone@gmail.com").get(API + "/report?account=1").status_code == 302


def test_the_agent_is_on_the_hub_and_in_the_command_palette():
    hub = client_as(STAFF).get("/p2/paid-marketing").data.decode()
    assert "/p2/paid-marketing/asset-coverage" in hub and "Asset Coverage Audit" in hub and "c-assetcov" in hub
    assert "Asset Coverage Audit" in open(os.path.join(ROOT, "templates", "_paid_kpal.html"), encoding="utf-8").read()


def test_there_is_no_em_dash_in_any_of_it():
    for p in ("tracker/paid_assets.py", "tracker/paid_assets_rules.py", "tracker/paid_assets_detail.py", "static/js/asset_coverage.js", "static/js/asset_tiles.js",
              "static/css/asset_coverage.css", "templates/asset_coverage.html"):
        assert "—" not in open(os.path.join(ROOT, p), encoding="utf-8").read(), p


# ── Edges the first pass missed (found by breaking the code on purpose) ─────

def test_a_link_under_review_is_pending_by_its_reason_alone_too():
    assert rules.link_state({"st": "ENABLED", "ps": "", "rs": ["ASSET_UNDER_REVIEW"]}, TODAY) == "pending"


def test_a_group_or_ad_is_as_bad_as_its_worst_gap():
    thin = dict(FULL_GROUP, headline=2, portrait_marketing_image=0)           # a high (short on headlines) and a low (no portrait images)
    item = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[group()], group_assets=gassets("700", **thin))["items"][0]
    assert item["groups"][0]["severity"] == "high" and rules.worst([{"severity": "low"}, {"severity": "high"}, {"severity": "medium"}, {}]) == "high"
    assert rules.worst([{}]) is None


def test_a_campaign_with_nothing_anywhere_is_missing_from_the_campaign_not_the_account():
    sl = slot_of(analyse(campaigns=[camp_row("5")])["items"][0], "SITELINK")
    assert (sl["have"], sl["source"], sl["status"]) == (0, "campaign", "missing")


def test_a_group_with_its_own_logo_is_not_credited_with_the_campaigns():
    item = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[group()], group_assets=gassets("700", **FULL_GROUP), campaign_assets=many("6", "LOGO", 2))["items"][0]
    logo = {s["key"]: s for s in item["groups"][0]["slots"]}["LOGO"]
    assert (logo["have"], logo["source"]) == (1, "asset group")


def test_groups_short_by_different_amounts_are_not_folded_into_one_line():
    rows = gassets("700", **dict(FULL_GROUP, headline=2)) + gassets("701", **dict(FULL_GROUP, headline=4))
    item = analyse(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[group("700", "Crowns"), group("701", "Dentures")], group_assets=rows)["items"][0]
    heads = [g for g in item["gaps"] if g["label"] == "Headlines"]
    assert [(g["scope"], g["severity"]) for g in heads] == [("Asset group: Crowns", "high"), ("Asset group: Dentures", "low")]


def test_gaps_read_worst_first_whatever_kind_they_are():
    rows = many("5", "SITELINK", 3) + many("5", "STRUCTURED_SNIPPET", 1)            # sitelinks thin (medium), no callouts (high)
    item = analyse(campaigns=[camp_row("5")], campaign_assets=rows)["items"][0]
    assert [(g["label"], g["severity"]) for g in item["gaps"][:3]] == [("Callouts", "high"), ("Sitelinks", "medium"), ("Images", "medium")]
    assert all("_n" not in g for g in item["gaps"])


def test_the_same_asset_id_in_two_accounts_is_two_disapproved_assets_and_in_two_campaigns_of_one_it_is_one():
    bad = [link_row("5", "AD_IMAGE", "same", primary="NOT_ELIGIBLE")]
    one = a.read_account({"id": "1111111111", "name": "One"}, search_fn=searcher(campaigns=[camp_row("5")], campaign_assets=bad), today=TODAY)
    two = a.read_account({"id": "2222222222", "name": "Two"}, search_fn=searcher(campaigns=[camp_row("5")], campaign_assets=bad + [link_row("5", "AD_IMAGE", "same", primary="NOT_ELIGIBLE")]), today=TODAY)
    rep = a.assemble({"accounts": [one, two], "warnings": []}, ACCT, TODAY)
    assert rep["summary"]["disapprovedAssets"] == 2


@pytest.mark.parametrize("where", ["campaign", "account", "group"])
def test_a_disapproved_asset_is_counted_once_per_account_at_every_level(where):
    def acct(i):
        kw = {"campaigns": [camp_row("6", "PMax", "PERFORMANCE_MAX")]}
        if where == "campaign":
            kw["campaign_assets"] = [link_row("6", "AD_IMAGE", "same", primary="NOT_ELIGIBLE")]
        elif where == "account":
            kw["customer_assets"] = [link_row(None, "CALLOUT", "same", primary="NOT_ELIGIBLE", level="customerAsset")]
        else:
            kw["groups"] = [group()]
            kw["group_assets"] = [{"assetGroup": {"id": "700"}, "assetGroupAsset": {"fieldType": "HEADLINE", "status": "ENABLED", "primaryStatus": "NOT_ELIGIBLE"}, "asset": {"id": "same"}}]
        return a.read_account({"id": "%d" % (1111111111 * i), "name": "A%d" % i}, search_fn=searcher(**kw), today=TODAY)
    both = a.assemble({"accounts": [acct(1), acct(2)], "warnings": []}, ACCT, TODAY)
    assert both["summary"]["disapprovedAssets"] == 2
    assert a.assemble({"accounts": [acct(1)], "warnings": []}, ACCT, TODAY)["summary"]["disapprovedAssets"] == 1
