"""Paid Marketing Agents: Ad Strength Analysis, the reading and what the ratings mean.

Pinned here: reading an RSA (rating, Google's tips, each headline with its own rating and pin, the 30 days' numbers); reading
a Performance Max asset group (counts per asset type, the text of the text assets); reading Google's recommendations (a new
ad, extra assets, a better version, a Performance Max group) without mistaking the ad's current text for the recommended
text; an account's keywords (once each, busiest first); an MCC (accounts added, one failing account said out loud, all failing
raised, keywords asked only for the ad groups that need work, a failing extra read a warning not an error); the report and its
cache; and the judgement: Google's tips sorted into text changes and extra assets, the gaps the rules see, the order of the
work (weak and busy first), and Performance Max held against what Google accepts. Nothing here talks to Google.
"""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_adstrength as a  # noqa: E402
from tracker import paid_adstrength_insights as ins  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 16)
ACCT = {"id": "1234567890", "name": "Acme Landscaping", "currency": "USD"}


def headline(text, label="PENDING", pin=None):
    h = {"text": text, "assetPerformanceLabel": label}
    if pin:
        h["pinnedField"] = pin
    return h


def rsa_row(ad_id="111", ad_group="900", strength="AVERAGE", heads=None, descs=None, tips=None, imp=1000, name="Brand"):
    heads = heads if heads is not None else [headline("Acme Landscaping"), headline("Patio Design Build")]
    descs = descs if descs is not None else [headline("Custom outdoor spaces designed and built for you")]
    return {"campaign": {"id": "5", "name": "Search_Brand"}, "adGroup": {"id": ad_group, "name": name},
            "adGroupAd": {"adStrength": strength, "actionItems": tips or [],
                          "ad": {"id": ad_id, "finalUrls": ["https://example.com/"],
                                 "responsiveSearchAd": {"headlines": heads, "descriptions": descs, "path1": "design"}}},
            "metrics": {"impressions": str(imp), "clicks": "40", "costMicros": "12500000", "conversions": 2.5}}


def group_row(gid="700", strength="AVERAGE", name="Spring", imp=500):
    return {"campaign": {"id": "6", "name": "PMax_Spring"}, "assetGroup": {"id": gid, "name": name, "adStrength": strength, "finalUrls": ["https://example.com/p"]},
            "metrics": {"impressions": str(imp), "clicks": "20", "costMicros": "5000000", "conversions": 1}}


def asset_row(gid, ftype, text=None):
    r = {"assetGroup": {"id": gid}, "assetGroupAsset": {"fieldType": ftype}, "asset": {"type": "TEXT" if text else "IMAGE"}}
    if text:
        r["asset"]["textAsset"] = {"text": text}
    return r


# ── Reading Google's rows ───────────────────────────────────────────────────

def test_an_rsa_is_read_with_its_rating_tips_texts_pins_and_numbers():
    row = rsa_row(heads=[headline("Acme Landscaping", "LOW", "HEADLINE_1"), headline("Patio Design Build", "GOOD")],
                  tips=["Try including more keywords in your headlines.", "Add 2 more sitelinks in your ad"])
    ad = a.read_rsas([row])[0]
    assert ad["key"] == "rsa:900:111" and ad["strength"] == "AVERAGE" and ad["adGroup"] == "Brand" and ad["campaign"] == "Search_Brand"
    assert ad["tips"] == ["Try including more keywords in your headlines.", "Add 2 more sitelinks in your ad"]
    assert ad["headlines"][0] == {"text": "Acme Landscaping", "label": "LOW", "pin": "HEADLINE_1"} and ad["headlines"][1]["pin"] is None
    assert (ad["impressions"], ad["clicks"], ad["cost"], ad["conversions"]) == (1000, 40, 12.5, 2.5)
    assert ad["finalUrl"] == "https://example.com/" and ad["path"] == "design"


def test_a_row_without_an_ad_id_is_skipped_and_a_missing_rating_is_unspecified():
    row = rsa_row()
    del row["adGroupAd"]["adStrength"]
    assert a.read_rsas([row])[0]["strength"] == "UNSPECIFIED"
    row["adGroupAd"]["ad"].pop("id")
    assert a.read_rsas([row]) == []


def test_an_asset_group_is_read_with_counts_per_type_and_the_text_of_its_text_assets():
    rows = [asset_row("700", "HEADLINE", "Patio Design"), asset_row("700", "HEADLINE", "Outdoor Living"), asset_row("700", "LONG_HEADLINE", "Design and build your outdoor space"),
            asset_row("700", "DESCRIPTION", "Custom outdoor spaces"), asset_row("700", "MARKETING_IMAGE"), asset_row("700", "MARKETING_IMAGE"),
            asset_row("999", "HEADLINE", "Belongs to a group that is not live")]
    g = a.read_groups([group_row()], rows)[0]
    assert g["key"] == "pmax:700" and g["strength"] == "AVERAGE" and g["counts"] == {"HEADLINE": 2, "LONG_HEADLINE": 1, "DESCRIPTION": 1, "MARKETING_IMAGE": 2}
    assert g["texts"]["HEADLINE"] == ["Patio Design", "Outdoor Living"] and g["texts"]["MARKETING_IMAGE" if False else "DESCRIPTION"] == ["Custom outdoor spaces"]
    assert g["impressions"] == 500 and g["cost"] == 5.0


def test_a_group_listed_twice_by_the_date_filter_is_one_group():
    assert len(a.read_groups([group_row(), group_row()], [])) == 1


def test_google_recommendations_are_read_without_the_ads_current_text():
    rows = [
        {"recommendation": {"type": "RESPONSIVE_SEARCH_AD", "adGroup": "customers/1/adGroups/900",
                            "responsiveSearchAdRecommendation": {"ad": {"responsiveSearchAd": {"headlines": [{"text": "Luxury Landscape Design"}],
                                                                                              "descriptions": [{"text": "Design build firm in Maryland"}]}}}}},
        {"recommendation": {"type": "RESPONSIVE_SEARCH_AD_ASSET", "adGroup": "customers/1/adGroups/901",
                            "responsiveSearchAdAssetRecommendation": {"currentAd": {"responsiveSearchAd": {"headlines": [{"text": "Already there"}]}},
                                                                      "recommendedAssets": {"headlines": [{"text": "Patio Builders"}]}}}},
        {"recommendation": {"type": "RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH", "adGroup": "customers/1/adGroups/902",
                            "responsiveSearchAdImproveAdStrengthRecommendation": {"currentAd": {"responsiveSearchAd": {"headlines": [{"text": "Old one"}]}},
                                                                                  "recommendedAd": {"responsiveSearchAd": {"headlines": [{"text": "Better one"}]}}}}},
        {"recommendation": {"type": "IMPROVE_PERFORMANCE_MAX_AD_STRENGTH", "campaign": "customers/1/campaigns/6",
                            "improvePerformanceMaxAdStrengthRecommendation": {"assetGroup": "customers/1/assetGroups/700", "adStrength": "GOOD"}}},
        {"recommendation": {"type": "SITELINK_ASSET", "adGroup": "customers/1/adGroups/900"}},
    ]
    r = a.read_recommendations(rows)
    assert r["adGroups"]["900"]["headlines"] == ["Luxury Landscape Design"] and r["adGroups"]["900"]["descriptions"] == ["Design build firm in Maryland"]
    assert r["adGroups"]["901"]["headlines"] == ["Patio Builders"]             # not "Already there"
    assert r["adGroups"]["902"]["headlines"] == ["Better one"] and r["adGroups"]["902"]["sources"] == ["RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH"]
    assert r["assetGroups"] == {"700": {"adStrength": "GOOD"}}


def test_keywords_are_listed_once_each_with_the_busiest_first():
    def kw(ag, text, mt, imp):
        return {"adGroup": {"id": ag}, "adGroupCriterion": {"keyword": {"text": text, "matchType": mt}}, "metrics": {"impressions": str(imp)}}
    rows = [kw("900", "patio design", "PHRASE", 10), kw("900", "Patio Design", "EXACT", 5), kw("900", "outdoor kitchen", "BROAD", 40), kw("901", "pool", "EXACT", 1)]
    assert a.read_keywords(rows) == {"900": ["outdoor kitchen", "patio design"], "901": ["pool"]}
    assert a.read_keywords(rows, per_group=1)["900"] == ["outdoor kitchen"]


# ── One account, an MCC ─────────────────────────────────────────────────────

def searcher(rsas=None, groups=None, assets=None, recs=None, keywords=None, log=None):
    def search(cid, q):
        if log is not None:
            log.append((cid, q))
        if "FROM ad_group_ad" in q:
            return rsas or []
        if "FROM asset_group_asset" in q:
            return assets or []
        if "FROM asset_group " in q:
            return groups or []
        if "FROM recommendation" in q:
            if isinstance(recs, Exception):
                raise recs
            return recs or []
        if "FROM keyword_view" in q:
            if isinstance(keywords, Exception):
                raise keywords
            return keywords or []
        raise AssertionError(q)
    return search


def test_keywords_are_asked_for_only_the_ad_groups_that_need_work():
    log = []
    rows = [rsa_row("1", "900", "POOR"), rsa_row("2", "901", "EXCELLENT"), rsa_row("3", "902", "UNSPECIFIED")]
    out = a.read_account({"id": "1234567890", "name": "Acme"}, search_fn=searcher(rsas=rows, log=log))
    q = next(q for _c, q in log if "keyword_view" in q)
    assert "900" in q and "902" in q and "901" not in q
    assert [x["account"] for x in out["ads"]] == ["Acme"] * 3


def test_a_failing_recommendations_or_keywords_read_is_a_warning_not_an_error():
    boom = gads.GadsError("api", "google broke")
    out = a.read_account({"id": "1234567890", "name": "Acme"}, search_fn=searcher(rsas=[rsa_row(strength="POOR")], recs=boom, keywords=boom))
    assert len(out["ads"]) == 1 and len(out["warnings"]) == 2
    assert "recommendations" in out["warnings"][0] and "keywords" in out["warnings"][1]


def test_an_mcc_adds_its_accounts_and_says_which_one_failed():
    members = [{"id": "1111111111", "name": "One"}, {"id": "2222222222", "name": "Two"}]

    def search(cid, q):
        if cid == "2222222222":
            raise gads.GadsError("permission", "no access")
        return searcher(rsas=[rsa_row(strength="GOOD")], groups=[group_row()])(cid, q)
    raw = a.google_raw({"id": "mcc-1", "name": "Group", "members": members}, search_fn=search)
    assert len(raw["ads"]) == 1 and raw["ads"][0]["account"] == "One" and raw["members"] == ["One", "Two"]
    assert raw["warnings"] == ["Two could not be read: no access"]


def test_every_account_failing_is_an_error():
    def search(cid, q):
        raise gads.GadsError("quota", "slow down")
    with pytest.raises(gads.GadsError):
        a.google_raw({"id": "mcc-1", "name": "G", "members": [{"id": "1111111111", "name": "One"}]}, search_fn=search)


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
    return a.report_for("1234567890", accounts_fn=lambda: [dict(ACCT, timeZone="America/New_York")], groups_fn=lambda: [], search_fn=search, today=TODAY, **kw)


ROWS = dict(rsas=[rsa_row("1", "900", "POOR", imp=5000), rsa_row("2", "901", "AVERAGE", imp=40000), rsa_row("3", "902", "GOOD", imp=100)],
            groups=[group_row("700", "AVERAGE"), group_row("701", "EXCELLENT", name="Fall")],
            assets=[asset_row("700", "HEADLINE", "Spring Patios")])


def test_the_report_lists_search_ads_and_asset_groups_weak_and_busy_first(cache):
    rep = report(searcher(**ROWS))
    assert rep["account"]["name"] == "Acme Landscaping" and rep["window"]["days"] == 30 and rep["window"]["to"] == "2026-10-16"
    keys = [i["key"] for i in rep["search"]["items"]]
    assert keys == ["rsa:901:2", "rsa:900:1", "rsa:902:3"]            # Average with 40,000 impressions before Poor with 5,000, Good last
    assert rep["search"]["counts"] == {"POOR": 1, "AVERAGE": 1, "GOOD": 1} and rep["search"]["belowGood"] == 2
    assert rep["search"]["impressionsBelowGood"] == 45000
    assert [i["key"] for i in rep["pmax"]["items"]] == ["pmax:700", "pmax:701"]
    assert rep["summary"]["reviewed"] == 5 and rep["summary"]["belowGood"] == 3 and rep["summary"]["good"] == 1 and rep["summary"]["excellent"] == 1


def test_a_second_ask_does_not_read_google_again_and_refresh_is_limited(cache):
    calls = []
    search = searcher(**ROWS, log=calls)
    report(search)
    n = len(calls)
    report(search)
    assert len(calls) == n
    report(search, force=True)
    assert len(calls) > n
    real = getattr(paid_store, "get_gads_entry", None)          # put back what was there: deleting it breaks later tests that patch it
    paid_store.get_gads_entry = lambda key, ttl: (cache.get(key), "2026-10-16T00:00:00+00:00") if key in cache else None
    try:
        with pytest.raises(base.RefreshTooSoon):
            report(search, force=True)
    finally:
        if real is None:
            del paid_store.get_gads_entry
        else:
            paid_store.get_gads_entry = real


def test_a_spent_quota_serves_the_last_saved_read_with_its_date(cache, monkeypatch):
    from tracker import paid_geo
    first = report(searcher(**ROWS), now=0)
    assert "stale" not in first

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: None)                              # nothing fresh
    monkeypatch.setattr(paid_store, "get_gads_cache_any", lambda key: (cache.get(key), "2026-10-15T10:00:00+00:00") if key in cache else None,
                        raising=False)
    paid_geo.clear_cache()
    base.forget("")
    old = report(spent, now=10 ** 9)
    assert old["stale"] == "2026-10-15T10:00:00+00:00" and old["search"]["total"] == 3


def test_with_no_saved_read_a_spent_quota_is_raised_to_the_caller(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        report(spent)


def test_find_item_returns_the_analysed_item_or_nothing(cache):
    rep = report(searcher(**ROWS))
    assert a.find_item(rep, "rsa:900:1")["strengthLabel"] == "Poor" and a.find_item(rep, "pmax:700")["kind"] == "pmax"
    assert a.find_item(rep, "rsa:0:0") is None


def test_an_account_that_is_not_position2s_is_refused(cache):
    with pytest.raises(a.BadRequest):
        a.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY)


# ── What the ratings mean ───────────────────────────────────────────────────

@pytest.mark.parametrize("tip,kind,text_fix", [
    ("Try including more keywords in your headlines.", "keywords_headlines", True),
    ("Try including more keywords in your descriptions.", "keywords_descriptions", True),
    ("Add 2 more sitelinks in your ad", "sitelinks", False),
    ("Add callouts to your ad", "assets", False),
    ("Make your headlines more unique", "headlines", True),
    ("Add more descriptions", "descriptions", True),
    ("Something Google has not said before", "other", False),
])
def test_googles_tips_are_sorted_into_text_changes_and_everything_else(tip, kind, text_fix):
    assert ins.classify_tip(tip) == (kind, text_fix)


def test_words_are_compared_without_plurals_or_filler():
    assert ins.tokens("The Patio Designs for You") == ins.tokens("patio design")
    assert ins.covers("patio design", ["Custom Patio Designs Near You"]) and not ins.covers("patio design", ["Patio Builders"])
    assert ins.near_duplicates(["Custom Patio Design Build", "Custom Patio Designs Built", "Outdoor Kitchens"]) == [("Custom Patio Design Build", "Custom Patio Designs Built")]


def test_an_rsa_with_few_texts_and_missing_keywords_is_told_exactly_that():
    ad = a.read_rsas([rsa_row(tips=["Try including more keywords in your headlines.", "Add 2 more sitelinks in your ad"])])[0]
    ad["keywords"] = ["patio builder", "outdoor kitchen", "pergola design", "fire pit"]
    item = ins.analyse_rsa(ad)
    kinds = [g["kind"] for g in item["gaps"]]
    assert "few_headlines" in kinds and "few_descriptions" in kinds and "keywords_headlines" in kinds and "assets" in kinds
    assert item["belowGood"] and item["textFixable"] and item["strengthLabel"] == "Average"
    assert "headlines and descriptions" in item["nextStep"]
    assert [t["textFix"] for t in item["tips"]] == [True, False]


def test_low_rated_text_duplicates_and_pins_are_named():
    heads = [headline("Custom Patio Design Build", "LOW"), headline("Custom Patio Designs Built"), headline("Outdoor Kitchens", pin="HEADLINE_1"),
             headline("Pergola Builders", pin="HEADLINE_2"), headline("Fire Pit Design", pin="HEADLINE_3")]
    item = ins.analyse_rsa(a.read_rsas([rsa_row(heads=heads)])[0])
    kinds = {g["kind"] for g in item["gaps"]}
    assert {"low_assets", "duplicates", "pins"} <= kinds
    assert item["stats"]["lowHeadlines"] == 1 and item["stats"]["pinned"] == 3


def test_a_rating_of_good_is_not_work_and_an_unrated_ad_says_wait():
    good = ins.analyse_rsa(a.read_rsas([rsa_row(strength="GOOD")])[0])
    assert not good["belowGood"] and good["priority"] == 0 and good["nextStep"].startswith("Nothing needed")
    new = ins.analyse_rsa(a.read_rsas([rsa_row(strength="PENDING")])[0])
    assert new["rank"] is None and not new["belowGood"] and new["strengthLabel"] == "Not rated yet" and new["nextStep"].startswith("Wait")


def test_when_the_only_tip_is_about_sitelinks_the_text_is_not_blamed():
    heads = [headline("Headline %s" % w) for w in "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar".split()]
    descs = [headline("Description %s about our service in detail" % w) for w in "one two three four".split()]
    ad = a.read_rsas([rsa_row(heads=heads, descs=descs, tips=["Add 2 more sitelinks in your ad"])])[0]
    item = ins.analyse_rsa(ad)
    assert not item["textFixable"] and "extra assets" in item["nextStep"]


def test_weak_and_busy_comes_first_and_poor_outweighs_average():
    poor, avg = ins.analyse_rsa(a.read_rsas([rsa_row("1", strength="POOR", imp=1000)])[0]), ins.analyse_rsa(a.read_rsas([rsa_row("2", strength="AVERAGE", imp=1000)])[0])
    assert poor["priority"] > avg["priority"] > 0
    quiet = ins.analyse_rsa(a.read_rsas([rsa_row("3", strength="POOR", imp=0)])[0])
    assert 0 < quiet["priority"] < avg["priority"]


def group(counts, strength="AVERAGE", texts=None, google=None):
    g = {"key": "pmax:1", "kind": "pmax", "assetGroupId": "1", "assetGroup": "Spring", "campaign": "PMax", "strength": strength, "finalUrl": "",
         "counts": counts, "texts": texts or {"HEADLINE": [], "LONG_HEADLINE": [], "DESCRIPTION": []}, "impressions": 100, "clicks": 1, "cost": 1, "conversions": 0}
    if google:
        g["google"] = google
    return ins.analyse_group(g)


def test_an_asset_group_below_what_google_accepts_is_told_what_is_missing():
    item = group({"HEADLINE": 2, "LONG_HEADLINE": 0, "DESCRIPTION": 2, "MARKETING_IMAGE": 1, "SQUARE_MARKETING_IMAGE": 5, "YOUTUBE_VIDEO": 0})
    kinds = {g["kind"] for g in item["gaps"]}
    assert {"missing_headline", "missing_long_headline", "few_marketing_image", "no_video"} <= kinds
    assert item["textFixable"] and item["assets"][0] == {"type": "HEADLINE", "label": "headlines", "count": 2, "min": 3, "max": 15}


def test_a_full_asset_group_below_good_points_at_the_images_not_the_text():
    full = {"HEADLINE": 15, "LONG_HEADLINE": 5, "DESCRIPTION": 5, "MARKETING_IMAGE": 11, "SQUARE_MARKETING_IMAGE": 8, "PORTRAIT_MARKETING_IMAGE": 1, "YOUTUBE_VIDEO": 7}
    item = group(full, texts={"HEADLINE": ["Headline %s" % i for i in "abcdefghijklmno"], "LONG_HEADLINE": [], "DESCRIPTION": []})
    assert [g["kind"] for g in item["gaps"]] == ["few_portrait_marketing_image"] and not item["textFixable"]
    assert "not in the API" in item["nextStep"] and "images" in item["nextStep"]


def test_near_duplicate_asset_group_headlines_and_googles_own_suggestion_are_noted():
    full = {"HEADLINE": 15, "LONG_HEADLINE": 5, "DESCRIPTION": 5, "MARKETING_IMAGE": 11, "SQUARE_MARKETING_IMAGE": 8, "PORTRAIT_MARKETING_IMAGE": 3, "YOUTUBE_VIDEO": 2}
    item = group(full, texts={"HEADLINE": ["Custom Patio Design Build", "Custom Patio Designs Built"], "LONG_HEADLINE": [], "DESCRIPTION": []},
                 google={"adStrength": "GOOD"})
    kinds = [g["kind"] for g in item["gaps"]]
    assert "duplicates" in kinds and "google_suggests" in kinds and item["textFixable"]


def test_no_text_the_page_shows_has_an_em_dash(cache):
    rep = report(searcher(**ROWS))
    texts = []
    for view in (rep["search"], rep["pmax"]):
        for it in view["items"]:
            texts += [g["text"] for g in it["gaps"]] + [it["nextStep"]]
    assert texts and all("—" not in t for t in texts)
