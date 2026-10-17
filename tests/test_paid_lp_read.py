"""Landing Page & Relevancy Check: what Google Ads says about each landing page (tracker/paid_lp_read.py)."""
import pytest

from tracker import paid_gads_segments as gads
from tracker import paid_lp_read as r


def page_row(url, cost, device="MOBILE", clicks=10, conv=1.0, speed=None, impressions=100, value=0):
    m = {"clicks": str(clicks), "costMicros": str(int(cost * 1e6)), "conversions": conv, "impressions": str(impressions), "conversionsValue": value}
    if speed is not None:
        m["speedScore"] = str(speed)
    return {"landingPageView": {"unexpandedFinalUrl": url}, "segments": {"device": device}, "metrics": m}


def ad_row(gid, name, campaign, url, cost=10.0, headlines=("Emergency Dentist",), descriptions=("Open today",), ctype="SEARCH", mobile_url=None, ad_type="RESPONSIVE_SEARCH_AD"):
    ad = {"type": ad_type, "finalUrls": [url] if url else [], "responsiveSearchAd": {"headlines": [{"text": h} for h in headlines], "descriptions": [{"text": d} for d in descriptions]}}
    if mobile_url:
        ad["finalMobileUrls"] = [mobile_url]
    return {"campaign": {"id": "c1", "name": campaign, "advertisingChannelType": ctype}, "adGroup": {"id": gid, "name": name}, "adGroupAd": {"ad": ad},
            "metrics": {"clicks": "5", "costMicros": str(int(cost * 1e6)), "conversions": 1}}


def kw_row(gid, text, cost=5.0, match="PHRASE", qs=7, ctr="AVERAGE", ad="ABOVE_AVERAGE", lp="BELOW_AVERAGE", urls=None, clicks=3, impressions=50):
    q = {"qualityScore": qs, "searchPredictedCtr": ctr, "creativeQualityScore": ad, "postClickQualityScore": lp}
    return {"adGroup": {"id": gid}, "adGroupCriterion": {"keyword": {"text": text, "matchType": match}, "qualityInfo": q, "finalUrls": urls or []},
            "metrics": {"clicks": str(clicks), "costMicros": str(int(cost * 1e6)), "impressions": str(impressions), "conversions": 0}}


def term_row(gid, text, clicks=4, cost=2.0):
    return {"adGroup": {"id": gid}, "searchTermView": {"searchTerm": text}, "metrics": {"clicks": str(clicks), "costMicros": str(int(cost * 1e6)), "conversions": 0}}


# ── The pages ───────────────────────────────────────────────────────────────

def test_a_page_is_one_row_however_many_devices_and_parameter_variants_it_had():
    rows = [page_row("https://a.test/x?utm=1", 30, "MOBILE", 100, 3), page_row("https://a.test/x?utm=2", 10, "MOBILE", 20, 1), page_row("https://a.test/x", 60, "DESKTOP", 50, 4),
            page_row("https://a.test/y", 5)]
    got = r.parse_pages(rows)
    assert [p["id"] for p in got] == ["a.test/x", "a.test/y"]
    x = got[0]
    assert x["cost"] == 100.0 and x["clicks"] == 170 and x["conversions"] == 8.0 and x["url"] == "https://a.test/x"        # the variant that spent most
    assert x["devices"] == {"mobile": {"clicks": 120, "cost": 40.0, "conversions": 4.0}, "desktop": {"clicks": 50, "cost": 60.0, "conversions": 4.0}}


def test_value_and_impressions_add_up():
    got = r.parse_pages([page_row("https://a.test/x", 1, impressions=300, value=50.5), page_row("https://a.test/x", 1, "DESKTOP", impressions=200, value=10)])[0]
    assert got["impressions"] == 500 and got["value"] == 60.5


def test_the_speed_score_is_the_one_of_the_address_that_spent_most_and_must_be_one_to_ten():
    got = r.parse_pages([page_row("https://a.test/x?a=1", 5, speed=3), page_row("https://a.test/x?a=2", 50, speed=8), page_row("https://a.test/x?a=3", 99, speed=None)])[0]
    assert got["speed"] == 8
    assert r.parse_pages([page_row("https://a.test/x", 5, speed=0)])[0]["speed"] is None
    assert r.parse_pages([page_row("https://a.test/x", 5, speed=11)])[0]["speed"] is None
    assert r.parse_pages([page_row("https://a.test/x", 5)])[0]["speed"] is None


def test_other_devices_are_not_lost():
    got = r.parse_pages([page_row("https://a.test/x", 5, "CONNECTED_TV", 7), page_row("https://a.test/x", 5, "TABLET", 3)])[0]
    assert set(got["devices"]) == {"other", "tablet"}


def test_pages_are_biggest_spender_first_and_addresses_that_are_not_pages_are_dropped():
    rows = [page_row("https://a.test/small", 1), page_row("https://a.test/big", 9), page_row("ftp://a.test/f", 99), page_row("", 99), page_row("https:///nohost", 99),
            page_row("https://a.test/tpl?x={keyword}", 5)]
    assert [p["id"] for p in r.parse_pages(rows)] == ["a.test/big", "a.test/tpl", "a.test/small"]
    assert r.parse_pages([]) == []


def test_a_page_id_is_host_and_path_only():
    assert r.page_id("https://A.test/x/?a=1#top") == "a.test/x" and r.page_id("https://a.test") == "a.test/"


# ── Ads ─────────────────────────────────────────────────────────────────────

def test_an_ad_group_carries_its_ads_with_headlines_descriptions_and_addresses():
    g = r.parse_ads([ad_row("g1", "Emergency", "Boston", "https://a.test/em?x=1", 12.5, ("Emergency Dentist", "Emergency Dentist", "Open Late"), ("Call now",), mobile_url="https://m.a.test/em")])["g1"]
    assert (g["name"], g["campaign"], g["campaignType"], g["cost"]) == ("Emergency", "Boston", "SEARCH", 12.5)
    ad = g["ads"][0]
    assert ad["headlines"] == ["Emergency Dentist", "Open Late"] and ad["descriptions"] == ["Call now"] and ad["urls"] == ["https://a.test/em?x=1", "https://m.a.test/em"]


def test_an_ad_groups_ads_add_up_and_only_the_biggest_few_are_kept():
    rows = [ad_row("g1", "G", "C", "https://a.test/%d" % i, cost=float(i)) for i in range(1, 10)]
    g = r.parse_ads(rows)["g1"]
    assert g["cost"] == 45.0 and len(g["ads"]) == r.MAX_ADS and g["clicks"] == 45


def test_headlines_are_capped_and_blank_ones_dropped():
    rows = [ad_row("g1", "G", "C", "https://a.test/x", headlines=["h%d" % i for i in range(20)] + ["", "  "])]
    assert len(r.parse_ads(rows)["g1"]["ads"][0]["headlines"]) == r.MAX_HEADLINES


def test_an_ad_with_no_address_or_a_bad_one_has_none():
    assert r.parse_ads([ad_row("g1", "G", "C", None)])["g1"]["ads"][0]["urls"] == []
    assert r.parse_ads([ad_row("g1", "G", "C", "javascript:x")])["g1"]["ads"][0]["urls"] == []


def test_a_row_with_no_ad_group_is_skipped():
    assert r.parse_ads([{"adGroup": {}, "metrics": {}}]) == {}


# ── Keywords and search terms ───────────────────────────────────────────────

def test_keywords_carry_their_quality_score_parts_and_come_biggest_spender_first():
    got = r.parse_keywords([kw_row("g1", "dentist boston", 5.0), kw_row("g1", "emergency dentist", 50.0, qs=3, ctr="BELOW_AVERAGE", ad="BELOW_AVERAGE", lp="ABOVE_AVERAGE")])["g1"]
    assert [k["text"] for k in got] == ["emergency dentist", "dentist boston"]
    top = got[0]
    assert (top["qs"], top["expectedCtr"], top["adRelevance"], top["landingPage"], top["match"]) == (3, "below", "below", "above", "phrase")
    assert got[1]["landingPage"] == "below" and got[1]["adRelevance"] == "above" and got[1]["expectedCtr"] == "average"


def test_a_quality_score_or_rating_google_did_not_give_is_none():
    k = r.parse_keywords([kw_row("g1", "x", qs=0, ctr="UNKNOWN", ad="UNSPECIFIED", lp="")])["g1"][0]
    assert k["qs"] is None and k["expectedCtr"] is None and k["adRelevance"] is None and k["landingPage"] is None
    assert r.parse_keywords([kw_row("g1", "x", qs=11)])["g1"][0]["qs"] is None


def test_a_keyword_with_its_own_address_carries_it():
    assert r.parse_keywords([kw_row("g1", "x", urls=["https://a.test/own?q=1"])])["g1"][0]["url"] == "https://a.test/own?q=1"
    assert r.parse_keywords([kw_row("g1", "x")])["g1"][0]["url"] == ""


def test_keywords_with_no_text_or_ad_group_are_skipped():
    assert r.parse_keywords([kw_row("g1", "  "), {"adGroupCriterion": {"keyword": {"text": "x"}}}]) == {}


def test_search_terms_are_summed_across_match_types_and_most_clicks_first():
    got = r.parse_terms([term_row("g1", "Emergency Dentist", 5, 3.0), term_row("g1", "emergency dentist", 2, 1.0), term_row("g1", "root canal", 20, 9.0)])["g1"]
    assert [(t["text"], t["clicks"], t["cost"]) for t in got] == [("root canal", 20, 9.0), ("Emergency Dentist", 7, 4.0)]


# ── Joining ─────────────────────────────────────────────────────────────────

def joined(keep=40):
    pages_ = r.parse_pages([page_row("https://a.test/em", 100), page_row("https://a.test/own", 40), page_row("https://a.test/shop", 30), page_row("https://a.test/other", 10)])
    groups = r.parse_ads([ad_row("g1", "Emergency", "Boston", "https://a.test/em", 80.0), ad_row("g2", "Other", "NYC", "https://a.test/other", 5.0), ad_row("g9", "Gone", "X", "https://a.test/unseen", 1.0)])
    kws = r.parse_keywords([kw_row("g1", "emergency dentist", 40.0), kw_row("g1", "tooth pain", 10.0, urls=["https://a.test/own"]), kw_row("g2", "braces", 3.0)])
    terms = r.parse_terms([term_row("g1", "emergency dentist boston", 9), term_row("g2", "braces nyc", 3)])
    return r.join(pages_, groups, kws, terms, keep=keep)


def test_each_page_lists_the_ad_groups_that_send_people_to_it_biggest_first():
    pages_, groups = joined()
    by = {p["id"]: p for p in pages_}
    assert by["a.test/em"]["adGroups"] == ["g1"] and by["a.test/other"]["adGroups"] == ["g2"]
    assert by["a.test/shop"]["adGroups"] == []                  # Shopping or Performance Max: no ad group to compare with


def test_a_keyword_with_its_own_address_puts_its_ad_group_on_that_page_too():
    pages_, _groups = joined()
    assert {p["id"]: p["adGroups"] for p in pages_}["a.test/own"] == ["g1"]


def test_ad_groups_carry_their_keywords_and_terms_and_the_pages_they_reach():
    _pages, groups = joined()
    g1 = groups["g1"]
    assert [k["text"] for k in g1["keywords"]] == ["emergency dentist", "tooth pain"] and [t["text"] for t in g1["terms"]] == ["emergency dentist boston"]
    assert g1["pages"] == {"a.test/em": 80.0, "a.test/own": 0.0}


def test_only_the_busiest_pages_and_the_ad_groups_that_reach_them_are_kept():
    pages_, groups = joined(keep=2)
    assert [p["id"] for p in pages_] == ["a.test/em", "a.test/own"]
    assert set(groups) == {"g1"} and groups["g1"]["pages"] == {"a.test/em": 80.0, "a.test/own": 0.0}
    assert "g9" not in groups and "g2" not in groups


def test_an_ad_group_that_reaches_no_kept_page_is_dropped_and_the_page_ids_it_keeps_are_only_kept_ones():
    _pages, groups = joined(keep=1)
    assert set(groups) == {"g1"} and groups["g1"]["pages"] == {"a.test/em": 80.0}


def test_keywords_and_terms_are_capped_per_ad_group():
    pages_ = r.parse_pages([page_row("https://a.test/x", 10)])
    groups = r.parse_ads([ad_row("g1", "G", "C", "https://a.test/x")])
    kws = r.parse_keywords([kw_row("g1", "kw%d" % i, cost=float(100 - i)) for i in range(60)])
    terms = r.parse_terms([term_row("g1", "t%d" % i, clicks=100 - i) for i in range(60)])
    _p, g = r.join(pages_, groups, kws, terms)
    assert len(g["g1"]["keywords"]) == r.MAX_KEYWORDS and g["g1"]["keywords"][0]["text"] == "kw0"
    assert len(g["g1"]["terms"]) == r.MAX_TERMS and g["g1"]["terms"][0]["text"] == "t0"


# ── The whole read ──────────────────────────────────────────────────────────

def fake_search(answers=None, fail=None, calls=None):
    """Answers each of the four queries by what it reads from. `fail`: part -> exception."""
    answers = answers or {}
    parts = {"landing_page_view": "pages", "FROM ad_group_ad": "ads", "FROM keyword_view": "keywords", "FROM search_term_view": "terms"}

    def search(cid, gaql):
        if calls is not None:
            calls.append((cid, gaql))
        part = next(v for k, v in parts.items() if k in gaql)
        if fail and part in fail:
            raise fail[part]
        return answers.get(part, [])
    return search


def good():
    return {"pages": [page_row("https://a.test/em", 100, speed=7)], "ads": [ad_row("g1", "Emergency", "Boston", "https://a.test/em", 80.0)],
            "keywords": [kw_row("g1", "emergency dentist", 40.0)], "terms": [term_row("g1", "emergency dentist boston", 9)]}


def test_the_read_joins_the_four_parts():
    calls = []
    out = r.read_account("1234567890", search_fn=fake_search(good(), calls=calls))
    assert out["ok"] == {"pages": True, "ads": True, "keywords": True, "terms": True} and out["warnings"] == []
    assert [p["id"] for p in out["pages"]] == ["a.test/em"] and out["pages"][0]["adGroups"] == ["g1"] and out["pages"][0]["speed"] == 7
    assert out["pageCount"] == 1 and out["pageCost"] == 100.0
    assert out["adGroups"]["g1"]["keywords"][0]["text"] == "emergency dentist"
    assert len(calls) == 4 and all(c[0] == "1234567890" for c in calls)


def test_every_query_is_read_only_and_asks_for_the_last_30_days():
    for _key, _what, q in r.READS:
        assert q.startswith("SELECT ") and "LAST_30_DAYS" in q and "LIMIT" in q
        assert not any(w in q.upper() for w in (" MUTATE", " UPDATE ", " INSERT ", " DELETE "))


def test_a_part_that_cannot_be_read_is_a_warning_and_the_rest_still_comes():
    fail = {"keywords": gads.GadsError("api", "Google answered 500")}
    out = r.read_account("1234567890", search_fn=fake_search(good(), fail=fail))
    assert out["ok"]["keywords"] is False and out["ok"]["pages"] is True
    assert out["warnings"] == ["The keywords and their Quality Score could not be read (Google answered 500)."]
    assert out["adGroups"]["g1"]["keywords"] == [] and out["pages"][0]["adGroups"] == ["g1"]


def test_without_the_ads_every_page_has_no_ad_group():
    out = r.read_account("1234567890", search_fn=fake_search(good(), fail={"ads": gads.GadsError("api", "x")}))
    assert out["ok"]["ads"] is False and out["pages"][0]["adGroups"] == [] and out["adGroups"] == {}


def test_without_the_pages_there_is_nothing_to_report_so_it_is_raised():
    with pytest.raises(gads.GadsError) as e:
        r.read_account("1234567890", search_fn=fake_search(good(), fail={"pages": gads.GadsError("quota", "allowance spent")}))
    assert e.value.kind == "quota"


def test_an_account_with_no_spend_on_any_page_reads_as_empty_not_as_an_error():
    out = r.read_account("1234567890", search_fn=fake_search({}))
    assert out["pages"] == [] and out["pageCount"] == 0 and out["pageCost"] == 0.0 and out["adGroups"] == {}
