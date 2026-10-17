"""Account Audit: reading one Google Ads account (tracker/paid_audit_google_read.py), on rows shaped like Google's real answers."""
from tests import audit_fakes as A
from tracker import paid_audit_google_read as r


# ── Small helpers ───────────────────────────────────────────────────────────

def test_number_helpers_never_raise():
    assert r._i("12") == 12 and r._i(3.9) == 3 and r._i(None) == 0 and r._i("x", 7) == 7
    assert r._f("1.5") == 1.5 and r._f(None) == 0.0 and r._f("n/a", 2.0) == 2.0
    assert r._units("1234567") == 1.23 and r._units(None) == 0.0
    assert r._id_of("customers/1/conversionActions/22") == "22" and r._id_of(None) == "" and r._id_of("9") == "9"
    assert r._get({"a": {"b": 1}}, "a", "b") == 1 and r._get({"a": 1}, "a", "b", default="d") == "d" and r._get({"a": None}, "a", default=5) == 5


# ── The account's own settings ──────────────────────────────────────────────

def test_customer_settings_are_read_and_the_tag_is_reduced_to_its_id():
    c = r.parse_customer(A.customer())
    assert c["id"] == "1112223334" and c["name"] == "Acme Dental" and c["currency"] == "USD" and c["timeZone"] == "America/New_York"
    assert c["status"] == "ENABLED" and c["test"] is False and c["manager"] is False and c["autoTagging"] is True
    assert c["trackingTemplate"].startswith("{lpurl}") and c["finalUrlSuffix"] == ""
    assert c["trackingStatus"] == "CONVERSION_TRACKING_MANAGED_BY_SELF" and c["enhancedLeads"] is True and c["dataTerms"] is True
    assert c["callReporting"] is True and c["callConversions"] is True
    assert c["optimizationScore"] == 43.3
    assert c["googleTagId"] == "AW-16599110526"
    assert "script" not in str(c)                       # the snippet is never kept


def test_customer_with_nothing_set_reads_as_off_not_as_an_error():
    c = r.parse_customer(A.customer(autoTaggingEnabled=None, conversionTrackingSetting={}, callReportingSetting={}, remarketingSetting={},
                                    optimizationScore=None, testAccount=True, status="SUSPENDED", trackingUrlTemplate=None))
    assert c["autoTagging"] is False and c["trackingStatus"] == "UNKNOWN" and c["enhancedLeads"] is False and c["dataTerms"] is False
    assert c["callReporting"] is False and c["googleTagId"] == "" and c["optimizationScore"] is None
    assert c["test"] is True and c["status"] == "SUSPENDED" and c["trackingTemplate"] == ""
    empty = r.parse_customer([])
    assert empty["id"] == "" and empty["status"] == "UNKNOWN" and empty["autoTagging"] is False


def test_the_optimization_score_is_a_whole_percent_and_zero_is_not_missing():
    assert r.parse_customer(A.customer(optimizationScore=0))["optimizationScore"] == 0
    assert r.parse_customer(A.customer(optimizationScore=1))["optimizationScore"] == 100


def test_a_tag_id_needs_the_aw_prefix_and_enough_digits():
    for text, want in (("gtag('config', 'AW-123456')", "AW-123456"), ("AW-12345", ""), ("G-ABCDEFGH12", ""), ("xAW-99999999", "")):
        assert r.parse_customer(A.customer(remarketingSetting={"googleGlobalSiteTag": text}))["googleTagId"] == want, text


# ── Conversion actions ──────────────────────────────────────────────────────

def test_conversion_actions_carry_their_settings_and_30_day_volume():
    rows = [A.action("1", "Submit lead form"), A.action("2", "Lead form - Submit", type="LEAD_FORM_SUBMIT", primary=False, included=False,
                                                       attribution="GOOGLE_ADS_LAST_CLICK", click="1", view="30", origin="GOOGLE_HOSTED", always=True)]
    vol = [A.volume("1", 332.914532, value=298.93), A.volume("1", 10.0, value=1.0), A.volume("2", 0.0, all_conversions=234.0)]
    acts = r.parse_conversions(rows, vol, own_id="1112223334")
    a, b = acts
    assert a["id"] == "1" and a["name"] == "Submit lead form" and a["status"] == "ENABLED" and a["type"] == "WEBPAGE"
    assert a["category"] == "SUBMIT_LEAD_FORM" and a["primary"] is True and a["counting"] == "ONE_PER_CLICK"
    assert a["attribution"] == "GOOGLE_SEARCH_ATTRIBUTION_DATA_DRIVEN" and a["defaultValue"] == 1.0 and a["alwaysDefault"] is False
    assert a["clickWindow"] == 90 and a["viewWindow"] == 1 and a["origin"] == "WEBSITE" and a["included"] is True and a["mine"] is True
    assert a["conversions"] == 342.91 and a["allConversions"] == 342.91 and a["value"] == 299.93      # two rows of one action add up
    assert b["primary"] is False and b["included"] is False and b["alwaysDefault"] is True and b["origin"] == "GOOGLE_HOSTED"
    assert b["conversions"] == 0.0 and b["allConversions"] == 234.0                                  # secondary: counted in all, not in conversions


def test_an_action_with_no_volume_row_recorded_zero_not_unknown():
    acts = r.parse_conversions([A.action("1")], [A.volume("9", 5.0)])
    assert acts[0]["conversions"] == 0.0 and acts[0]["allConversions"] == 0.0 and acts[0]["value"] == 0.0


def test_when_the_volumes_could_not_be_read_the_counts_are_none_never_zero():
    acts = r.parse_conversions([A.action("1")], None)
    assert acts[0]["conversions"] is None and acts[0]["allConversions"] is None and acts[0]["value"] is None


def test_an_action_owned_by_another_account_is_marked_not_mine():
    rows = [A.action("1", owner="customers/1112223334"), A.action("2", owner="customers/5556667778")]
    mine = {a["id"]: a["mine"] for a in r.parse_conversions(rows, [], own_id="1112223334")}
    assert mine == {"1": True, "2": False}
    assert all(a["mine"] for a in r.parse_conversions(rows, []))                   # with no account id to compare, nothing is called foreign


def test_conversion_actions_sort_enabled_first_then_primary_then_name():
    rows = [A.action("1", "b", status="PAUSED"), A.action("2", "z", primary=False), A.action("3", "a", primary=True), A.action("4", "m", primary=True)]
    assert [a["id"] for a in r.parse_conversions(rows, [])] == ["3", "4", "2", "1"]


def test_missing_settings_in_a_conversion_row_do_not_raise():
    acts = r.parse_conversions([{"conversionAction": {"resourceName": "customers/1/conversionActions/77"}}, {}], [])
    assert acts[0]["id"] == "77" and acts[0]["status"] == "UNKNOWN" and acts[0]["clickWindow"] == 0 and acts[0]["primary"] is False
    assert acts[1]["id"] == "" and acts[1]["name"] == ""


# ── Links and audience lists ────────────────────────────────────────────────

def test_links_are_counted_by_type_and_merchant_ids_listed():
    out = r.parse_links([A.link("MERCHANT_CENTER", "1"), A.link("MERCHANT_CENTER", "2"), A.link("DATA_PARTNER", "3")],
                        [A.merchant("9282495"), A.merchant("9282495"), A.merchant("77")])
    assert out == {"counts": {"MERCHANT_CENTER": 2, "DATA_PARTNER": 1}, "merchantCenter": ["77", "9282495"]}


def test_no_links_is_an_empty_answer_and_unreadable_merchant_ids_are_none():
    assert r.parse_links([], []) == {"counts": {}, "merchantCenter": []}
    assert r.parse_links([A.link()], None)["merchantCenter"] is None
    assert r.parse_links([{}], [{}]) == {"counts": {"UNKNOWN": 1}, "merchantCenter": []}


def test_a_list_with_members_an_empty_one_and_a_small_one():
    rows = [A.user_list("Big", "REMARKETING", search=5000, display=9000, lid="1"), A.user_list("Empty", "RULE_BASED", lid="2"),
            A.user_list("Small", "CRM_BASED", search=300, display=300, lid="3"), A.user_list("Display only", "REMARKETING", search=0, display=500, lid="4"),
            A.user_list("Closed", "REMARKETING", status="CLOSED", lid="5")]
    out = r.parse_lists(rows)
    assert out["total"] == 5 and out["open"] == 4 and out["withMembers"] == 3
    assert out["byType"] == {"REMARKETING": 3, "RULE_BASED": 1, "CRM_BASED": 1}
    assert out["empty"] == 1 and out["tooSmallForSearch"] == 2                         # Small and Display only; the closed list is on purpose
    assert [e["name"] for e in out["examples"]] == ["Empty", "Small", "Display only"]
    assert out["examples"][1] == {"name": "Small", "type": "CRM_BASED", "size": 300, "kind": "small"} and [e["kind"] for e in out["examples"]] == ["empty", "small", "small"]


def test_the_search_minimum_is_inclusive_and_examples_are_capped():
    assert r.parse_lists([A.user_list(search=1000)])["tooSmallForSearch"] == 0
    assert r.parse_lists([A.user_list(search=999)])["tooSmallForSearch"] == 1
    many = r.parse_lists([A.user_list("L%d" % i, lid=str(i)) for i in range(30)])
    assert many["empty"] == 30 and len(many["examples"]) == r.EXAMPLES


def test_no_lists_and_odd_rows_do_not_raise():
    assert r.parse_lists([]) == {"total": 0, "open": 0, "withMembers": 0, "byType": {}, "empty": 0, "tooSmallForSearch": 0, "examples": []}
    assert r.parse_lists([{}])["total"] == 1


# ── Campaigns ───────────────────────────────────────────────────────────────

def test_an_enabled_campaign_carries_its_settings_numbers_targeting_and_negatives():
    rows = [A.campaign("10", "Search | Implants", partners=True, display=True, geo="PRESENCE", budget="75500000", shared=True, target_cpa="40000000",
                       bidding="TARGET_CPA", reasons=("BUDGET_CONSTRAINED",), primary="LIMITED", portfolio="customers/1/biddingStrategies/5")]
    out = r.parse_campaigns(rows, [A.campaign_metrics("10", 22138, 6109, 15082.63, 198.74, 346.91, 0.1756, 0.318, 0.5064)],
                            [A.criterion("10", "LOCATION"), A.criterion("10", "LOCATION"), A.criterion("10", "LOCATION", True), A.criterion("10", "PROXIMITY"),
                             A.criterion("10", "LANGUAGE")],
                            [A.negative("10"), A.negative("10", "EXACT")], [A.attached_list("10", "900"), A.attached_list("10", "901", "Second", "12")])
    c = out["campaigns"][0]
    assert c["id"] == "10" and c["name"] == "Search | Implants" and c["type"] == "SEARCH" and c["bidding"] == "TARGET_CPA" and c["portfolio"] == "customers/1/biddingStrategies/5"
    assert c["targetKind"] == "cpa" and c["target"] == 40.0 and c["budget"] == 75.5 and c["budgetShared"] is True
    assert c["googleSearch"] is True and c["searchPartners"] is True and c["displayNetwork"] is True and c["geoMode"] == "PRESENCE"
    assert c["primaryStatus"] == "LIMITED" and c["reasons"] == ["BUDGET_CONSTRAINED"] and c["serving"] == "SERVING"
    assert c["metrics"] == {"impressions": 22138, "clicks": 6109, "cost": 15082.63, "conversions": 198.74, "allConversions": 346.91,
                            "budgetLost": 17.6, "rankLost": 31.8, "share": 50.6}
    assert c["targeting"] == {"locations": 2, "excludedLocations": 1, "proximities": 1, "languages": 1}
    assert c["negatives"] == 2 and c["lists"] == ["900", "901"]
    assert out["lists"] == {"900": {"name": "Master negatives", "members": 4000}, "901": {"name": "Second", "members": 12}}
    assert out["counts"] == {"enabled": 1, "paused": 0, "byType": {"SEARCH": 1}}
    assert out["totals"] == {"impressions": 22138, "clicks": 6109, "cost": 15082.63, "conversions": 198.74}


def test_paused_campaigns_are_counted_not_listed_and_removed_ones_never_come():
    rows = [A.campaign("1", status="PAUSED"), A.campaign("2", status="PAUSED", ctype="PERFORMANCE_MAX"), A.campaign("3", ctype="PERFORMANCE_MAX"),
            A.campaign("4", ctype="SEARCH"), A.campaign("5", ctype="SEARCH"), A.campaign("6", status="UNKNOWN")]
    out = r.parse_campaigns(rows, [], [], [], [])
    assert out["counts"] == {"enabled": 3, "paused": 2, "byType": {"PERFORMANCE_MAX": 1, "SEARCH": 2}}
    assert sorted(c["id"] for c in out["campaigns"]) == ["3", "4", "5"]


def test_a_campaign_with_no_rows_in_a_part_that_was_read_has_zeros_and_a_part_not_read_is_none():
    read = r.parse_campaigns([A.campaign("10")], [], [], [], [])["campaigns"][0]
    assert read["metrics"]["cost"] == 0 and read["metrics"]["impressions"] == 0 and read["metrics"]["budgetLost"] is None
    assert read["targeting"] == {"locations": 0, "excludedLocations": 0, "proximities": 0, "languages": 0}
    assert read["negatives"] == 0 and read["lists"] == []
    unread = r.parse_campaigns([A.campaign("10")])
    c = unread["campaigns"][0]
    assert c["metrics"] is None and c["targeting"] is None and c["negatives"] is None and c["lists"] is None and unread["totals"] is None


def test_impression_share_keeps_a_tenth_of_a_percent_and_zero_is_not_missing():
    c = r.parse_campaigns([A.campaign("10")], [A.campaign_metrics("10", budget_lost=0.0, rank_lost=0.004, share=1.0)])["campaigns"][0]
    assert c["metrics"]["budgetLost"] == 0.0 and c["metrics"]["rankLost"] == 0.4 and c["metrics"]["share"] == 100.0       # a rounded 19.6 must not read as 20
    assert r.parse_campaigns([A.campaign("10")], [A.campaign_metrics("10", budget_lost=0.196, rank_lost=0.396)])["campaigns"][0]["metrics"]["budgetLost"] == 19.6
    assert r.parse_campaigns([A.campaign("10")], [A.campaign_metrics("10")])["campaigns"][0]["metrics"]["share"] is None     # Display has none


def test_numbers_of_a_campaign_split_over_several_rows_add_up():
    c = r.parse_campaigns([A.campaign("10")], [A.campaign_metrics("10", 100, 10, 50.0, 2.0), A.campaign_metrics("10", 200, 20, 25.5, 1.5)])["campaigns"][0]
    assert c["metrics"]["impressions"] == 300 and c["metrics"]["clicks"] == 30 and c["metrics"]["cost"] == 75.5 and c["metrics"]["conversions"] == 3.5


def test_the_bid_target_comes_from_whichever_strategy_field_is_set():
    t = lambda **kw: r.parse_campaigns([A.campaign("1", **kw)])["campaigns"][0]
    assert (t(target_cpa="12500000")["targetKind"], t(target_cpa="12500000")["target"]) == ("cpa", 12.5)
    assert (t(target_roas=3.456)["targetKind"], t(target_roas=3.456)["target"]) == ("roas", 3.46)
    assert (t()["targetKind"], t()["target"]) == ("", 0)
    mc = A.campaign("2", bidding="MAXIMIZE_CONVERSIONS")
    mc["campaign"]["maximizeConversions"] = {"targetCpaMicros": "30000000"}
    assert r.parse_campaigns([mc])["campaigns"][0]["target"] == 30.0
    mv = A.campaign("3", bidding="MAXIMIZE_CONVERSION_VALUE")
    mv["campaign"]["maximizeConversionValue"] = {"targetRoas": 4.0}
    assert (r.parse_campaigns([mv])["campaigns"][0]["targetKind"], r.parse_campaigns([mv])["campaigns"][0]["target"]) == ("roas", 4.0)


def test_a_shopping_campaign_names_its_merchant_center():
    assert r.parse_campaigns([A.campaign("1", ctype="SHOPPING", merchant="9282495")])["campaigns"][0]["merchantId"] == "9282495"
    assert r.parse_campaigns([A.campaign("1")])["campaigns"][0]["merchantId"] == ""


def test_campaigns_are_listed_biggest_spender_first():
    rows = [A.campaign("1", "small"), A.campaign("2", "big"), A.campaign("3", "idle")]
    out = r.parse_campaigns(rows, [A.campaign_metrics("1", cost=10.0), A.campaign_metrics("2", cost=900.0)])
    assert [c["name"] for c in out["campaigns"]] == ["big", "small", "idle"]


def test_a_repeated_shared_list_is_remembered_once_and_attached_to_each_campaign():
    out = r.parse_campaigns([A.campaign("1"), A.campaign("2")], [], [], [], [A.attached_list("1", "900"), A.attached_list("2", "900")])
    assert out["lists"] == {"900": {"name": "Master negatives", "members": 4000}} and [c["lists"] for c in out["campaigns"]] == [["900"], ["900"]]


def test_network_spend_is_split_by_where_the_ad_ran():
    out = r.parse_network([A.network_row("1", "SEARCH", 80.0, 4.0), A.network_row("2", "SEARCH", 10.0, 1.0), A.network_row("1", "SEARCH_PARTNERS", 8.0, 0.0),
                           A.network_row("1", "CONTENT", 2.0, 0.0)])
    assert out == {"cost": {"SEARCH": 90.0, "SEARCH_PARTNERS": 8.0, "CONTENT": 2.0}, "total": 100.0,
                   "conversions": {"SEARCH": 5.0, "SEARCH_PARTNERS": 0.0, "CONTENT": 0.0}}
    assert r.parse_network([]) == {"cost": {}, "total": 0.0, "conversions": {}}


# ── Ad groups, keywords, ads ────────────────────────────────────────────────

NAMES = {"10": "Search | Implants", "11": "Search | Whitening"}
GROUPS = r.parse_ad_groups([A.ad_group("100", "10", "Implants"), A.ad_group("101", "10", "Dentures"), A.ad_group("200", "11", "Whitening"),
                            A.ad_group("300", "11", "Display group", "DISPLAY_STANDARD")])["groups"]


def test_ad_groups_are_mapped_by_id_with_their_campaign_and_type():
    out = r.parse_ad_groups([A.ad_group("100", "10", "Implants"), A.ad_group("300", "11", "D", "DISPLAY_STANDARD"), {}])
    assert out["total"] == 2 and out["groups"]["100"] == {"campaign": "10", "name": "Implants", "type": "SEARCH_STANDARD"}
    assert out["groups"]["300"]["type"] == "DISPLAY_STANDARD"


def test_keywords_are_counted_by_match_and_status_and_only_enabled_ones_are_judged():
    rows = [A.keyword(crit="1", match="BROAD"), A.keyword(crit="2", match="PHRASE"), A.keyword(crit="3", match="EXACT"), A.keyword(crit="4", match="EXACT"),
            A.keyword(crit="5", status="PAUSED", match="BROAD")]
    k = r.parse_keywords(rows, [], GROUPS, NAMES)
    assert k["total"] == 5 and k["enabled"] == 4 and k["paused"] == 1
    assert k["byMatch"] == {"BROAD": 1, "PHRASE": 1, "EXACT": 2}
    assert k["perCampaign"] == {"10": {"keywords": 4, "broad": 1}}
    assert k["_perGroup"] == {"100": 4}


def test_quality_score_is_weighted_by_impressions_and_split_in_three_bands():
    rows = [A.keyword(crit="1", qs=3, ctr="BELOW_AVERAGE", creative="AVERAGE", post_click="BELOW_AVERAGE"),
            A.keyword(crit="2", qs=4), A.keyword(crit="3", qs=5), A.keyword(crit="4", qs=6), A.keyword(crit="5", qs=7, ctr="ABOVE_AVERAGE"),
            A.keyword(crit="6", qs=10), A.keyword(crit="7")]                      # no score yet
    m = [A.keyword_metrics(crit="1", impressions=400), A.keyword_metrics(crit="2", impressions=100), A.keyword_metrics(crit="3", impressions=50),
         A.keyword_metrics(crit="4", impressions=50), A.keyword_metrics(crit="5", impressions=200), A.keyword_metrics(crit="6", impressions=200),
         A.keyword_metrics(crit="7", impressions=77)]
    q = r.parse_keywords(rows, m, GROUPS, NAMES)["qs"]
    assert q["scored"] == 6 and q["keywords"] == {"low": 2, "mid": 2, "high": 2}
    assert q["impressions"] == {"low": 500, "mid": 100, "high": 400, "none": 77}
    assert q["below"] == {"expectedCtr": 40, "adRelevance": 0, "landingPage": 40}      # of the 1,000 scored impressions: 400 below on CTR and on the page


def test_the_worst_quality_keywords_are_named_busiest_first_and_need_impressions():
    rows = [A.keyword(crit="1", text="a", qs=2), A.keyword(crit="2", text="b", qs=3), A.keyword(crit="3", text="c", qs=1)]
    m = [A.keyword_metrics(crit="1", impressions=10), A.keyword_metrics(crit="2", impressions=500)]
    ex = r.parse_keywords(rows, m, GROUPS, NAMES)["lowQualityExamples"]
    assert [e["keyword"] for e in ex] == ["b", "a"]                       # "c" had no impressions
    assert ex[0] == {"keyword": "b", "match": "PHRASE", "campaign": "Search | Implants", "adGroup": "Implants", "qs": 3, "impressions": 500}


def test_keywords_that_spent_without_converting_need_enough_clicks():
    rows = [A.keyword(crit="1", text="waste"), A.keyword(crit="2", text="edge"), A.keyword(crit="3", text="few"), A.keyword(crit="4", text="wins")]
    m = [A.keyword_metrics(crit="1", clicks=40, cost=120.5, conversions=0), A.keyword_metrics(crit="2", clicks=15, cost=30.0, conversions=0),
         A.keyword_metrics(crit="3", clicks=14, cost=99.0, conversions=0), A.keyword_metrics(crit="4", clicks=90, cost=500.0, conversions=2.5)]
    w = r.parse_keywords(rows, m, GROUPS, NAMES)["wasted"]
    assert w["cost"] == 150.5 and w["keywords"] == 2 and w["minClicks"] == 15
    assert [e["keyword"] for e in w["examples"]] == ["waste", "edge"] and w["examples"][0]["clicks"] == 40


def test_when_the_keyword_numbers_were_not_read_the_facts_that_need_them_are_none():
    k = r.parse_keywords([A.keyword(qs=3)], None, GROUPS, NAMES)
    assert k["wasted"] is None and k["qs"]["impressions"] is None and k["qs"]["below"] is None
    assert k["qs"]["scored"] == 1 and k["qs"]["keywords"]["low"] == 1 and k["lowQualityExamples"] == []


def test_the_same_keyword_in_two_ad_groups_of_one_campaign_is_a_duplicate_but_not_across_campaigns_or_match_types():
    rows = [A.keyword(cid="10", gid="100", crit="1", text="Dental  Implants", match="PHRASE"), A.keyword(cid="10", gid="101", crit="2", text="dental implants", match="PHRASE"),
            A.keyword(cid="10", gid="101", crit="3", text="dental implants", match="EXACT"),
            A.keyword(cid="11", gid="200", crit="4", text="dental implants", match="PHRASE"),
            A.keyword(cid="10", gid="100", crit="5", text="same group", match="PHRASE"), A.keyword(cid="10", gid="100", crit="6", text="same group", match="PHRASE")]
    d = r.parse_keywords(rows, [], GROUPS, NAMES)["duplicates"]
    assert d["keywords"] == 1 and d["copies"] == 1
    assert d["examples"] == [{"keyword": "dental implants", "match": "PHRASE", "campaign": "Search | Implants", "adGroups": 2}]


def test_disapproved_and_rarely_served_keywords_are_counted():
    rows = [A.keyword(crit="1", approval="DISAPPROVED", text="bad"), A.keyword(crit="2", serving="RARELY_SERVED"), A.keyword(crit="3", approval="APPROVED_LIMITED")]
    k = r.parse_keywords(rows, [], GROUPS, NAMES)
    assert k["disapproved"]["count"] == 1 and k["disapproved"]["examples"][0]["keyword"] == "bad" and k["rarelyServed"] == 1


def test_a_keyword_row_missing_pieces_does_not_raise():
    k = r.parse_keywords([{}, {"adGroupCriterion": {"status": "ENABLED"}}], [{}], {}, {})
    assert k["total"] == 2 and k["byMatch"] == {"UNKNOWN": 1}


def test_ads_are_counted_by_type_and_responsive_search_ads_by_strength():
    rows = [A.ad(aid="1", strength="POOR"), A.ad(aid="2", strength="GOOD"), A.ad(aid="3", strength="GOOD"), A.ad(aid="4", atype="EXPANDED_TEXT_AD"),
            A.ad(aid="5", status="PAUSED"), A.ad(aid="6", atype="RESPONSIVE_DISPLAY_AD", gid="300")]
    a = r.parse_ads(rows, GROUPS, NAMES)
    assert a["total"] == 5 and a["byType"] == {"RESPONSIVE_SEARCH_AD": 3, "EXPANDED_TEXT_AD": 1, "RESPONSIVE_DISPLAY_AD": 1} and a["rsa"] == 3
    assert a["strength"] == {"POOR": 1, "GOOD": 2} and a["legacy"] == 1


def test_disapproved_ads_name_the_policy_topic_and_limited_ones_are_counted():
    rows = [A.ad(aid="1", approval="DISAPPROVED", topics=("HEALTHCARE", "TRADEMARKS", "A", "B")), A.ad(aid="2", approval="APPROVED_LIMITED"),
            A.ad(aid="3", approval="AREA_OF_INTEREST_ONLY"), A.ad(aid="4")]
    a = r.parse_ads(rows, GROUPS, NAMES)
    assert a["disapproved"] == {"count": 1, "examples": [{"campaign": "Search | Implants", "adGroup": "Implants", "type": "RESPONSIVE_SEARCH_AD",
                                                          "topics": ["HEALTHCARE", "TRADEMARKS", "A"]}]}
    assert a["limited"] == 2


def test_pinned_headlines_are_counted_per_ad():
    rows = [A.ad(aid="1", pins=0), A.ad(aid="2", pins=1), A.ad(aid="3", pins=2), A.ad(aid="4", pins=3), A.ad(aid="5", atype="EXPANDED_TEXT_AD")]
    assert r.parse_ads(rows, GROUPS, NAMES)["pinned"] == {"any": 3, "heavy": 1}


def test_landing_pages_are_listed_once_with_how_many_ads_use_each_and_capped():
    rows = [A.ad(aid="1", urls=("https://a.test/x",)), A.ad(aid="2", urls=("https://a.test/x",)), A.ad(aid="3", urls=("https://a.test/y", "https://a.test/z"))]
    u = r.parse_ads(rows, GROUPS, NAMES)["urls"]
    assert u["distinct"] == 3 and u["list"][0] == {"url": "https://a.test/x", "ads": 2}
    many = r.parse_ads([A.ad(aid=str(i), urls=("https://a.test/p%d" % i,)) for i in range(60)], GROUPS, NAMES)["urls"]
    assert many["distinct"] == 60 and len(many["list"]) == r.MAX_URLS


def test_ad_groups_are_judged_on_ads_and_keywords_together():
    kw = r.parse_keywords([A.keyword(gid="100", crit="1"), A.keyword(gid="200", cid="11", crit="2")] +
                          [A.keyword(gid="101", crit=str(10 + i), text="k%d" % i) for i in range(41)], [], GROUPS, NAMES)
    ads = r.parse_ads([A.ad(gid="100", aid="1"), A.ad(gid="100", aid="2"), A.ad(gid="101", aid="3", atype="EXPANDED_TEXT_AD"),
                       A.ad(gid="200", cid="11", aid="4"), A.ad(gid="200", cid="11", aid="5"), A.ad(gid="200", cid="11", aid="6"),
                       A.ad(gid="200", cid="11", aid="7"), A.ad(gid="200", cid="11", aid="8")], GROUPS, NAMES)
    s = r.join_structure(GROUPS, kw, ads, NAMES)
    assert s["adGroups"] == 4 and s["searchAdGroups"] == 3 and s["byType"] == {"SEARCH_STANDARD": 3, "DISPLAY_STANDARD": 1}
    assert s["noLiveAd"]["count"] == 0                                    # the Display group is not judged
    assert [e["adGroup"] for e in s["noResponsiveAd"]["examples"]] == ["Dentures"]
    assert [e["adGroup"] for e in s["oneAd"]["examples"]] == ["Dentures"]
    assert [e["adGroup"] for e in s["manyAds"]["examples"]] == ["Whitening"]
    assert s["noKeywords"]["count"] == 0 and [e["adGroup"] for e in s["manyKeywords"]["examples"]] == ["Dentures"]


def test_an_ad_group_with_nothing_live_is_named_with_its_campaign():
    kw = r.parse_keywords([], [], GROUPS, NAMES)
    ads = r.parse_ads([], GROUPS, NAMES)
    s = r.join_structure(GROUPS, kw, ads, NAMES)
    assert s["noLiveAd"]["count"] == 3 and s["noKeywords"]["count"] == 3
    assert s["noLiveAd"]["examples"][0] == {"adGroup": "Dentures", "campaign": "Search | Implants"}


def test_structure_facts_that_need_an_unread_part_are_absent_not_zero():
    s = r.join_structure(GROUPS, None, None, NAMES)
    assert "noLiveAd" not in s and "noKeywords" not in s and s["searchAdGroups"] == 3
    kw_only = r.join_structure(GROUPS, r.parse_keywords([], [], GROUPS, NAMES), None, NAMES)
    assert "noKeywords" in kw_only and "noLiveAd" not in kw_only


def test_compact_drops_the_join_helpers_only():
    kw = r.parse_keywords([A.keyword()], [], GROUPS, NAMES)
    c = r.compact(kw)
    assert "_perGroup" not in c and c["enabled"] == 1 and "_perGroup" in kw
    assert r.compact(None) is None


# ── Search terms ────────────────────────────────────────────────────────────

def test_search_terms_that_never_converted_are_the_waste():
    rows = [A.term("good", 100.0, 20, 4.0), A.term("free implants", 60.0, 12, 0), A.term("implants jobs", 40.0, 9, 0.0), A.term("tiny", 0.5, 1, 0)]
    t = r.parse_terms(rows, NAMES)
    assert t["rows"] == 4 and t["capped"] is False and t["cost"] == 200.5 and t["wastedCost"] == 100.5 and t["wastedTerms"] == 3
    assert [e["term"] for e in t["examples"]] == ["free implants", "implants jobs", "tiny"]
    assert t["examples"][0] == {"term": "free implants", "cost": 60.0, "clicks": 12, "campaign": "Search | Implants"}


def test_a_full_page_of_terms_says_the_cheaper_ones_are_not_in_it():
    rows = [A.term("t%d" % i, 1.0) for i in range(r.TERMS_LIMIT)]
    assert r.parse_terms(rows, NAMES)["capped"] is True
    assert r.parse_terms(rows[:-1], NAMES)["capped"] is False
    assert r.parse_terms([], NAMES) == {"rows": 0, "capped": False, "cost": 0.0, "wastedCost": 0.0, "wastedTerms": 0, "examples": []}


# ── Cases the first round of deliberate faults showed were not pinned ───────

def test_each_customer_flag_is_read_from_its_own_field():
    c = r.parse_customer(A.customer(conversionTrackingSetting={"acceptedCustomerDataTerms": False, "enhancedConversionsForLeadsEnabled": True},
                                    callReportingSetting={"callReportingEnabled": False, "callConversionReportingEnabled": True}))
    assert c["enhancedLeads"] is True and c["dataTerms"] is False and c["callConversions"] is True and c["callReporting"] is False
    c = r.parse_customer(A.customer(conversionTrackingSetting={"acceptedCustomerDataTerms": True}, callReportingSetting={"callReportingEnabled": True}))
    assert c["enhancedLeads"] is False and c["dataTerms"] is True and c["callConversions"] is False and c["callReporting"] is True


def test_the_optimization_score_keeps_a_tenth_of_a_percent():
    assert r.parse_customer(A.customer(optimizationScore=0.4366))["optimizationScore"] == 43.7 and r.parse_customer(A.customer(optimizationScore=0.496))["optimizationScore"] == 49.6


def test_the_conversion_value_falls_back_to_the_primary_value_field():
    row = {"metrics": {"conversionsValue": 12.5, "conversions": 1.0, "allConversions": 1.0}, "segments": {"conversionAction": "customers/1/conversionActions/1"}}
    assert r.parse_conversions([A.action("1")], [row])[0]["value"] == 12.5


def test_a_keyword_with_some_conversion_is_not_waste_and_a_missing_serving_status_is_not_rarely_served():
    rows = [A.keyword(crit="1", text="half"), A.keyword(crit="2", text="one"), A.keyword(crit="3", text="none"), A.keyword(crit="4", text="unset", serving=None)]
    m = [A.keyword_metrics(crit="1", clicks=50, cost=10.0, conversions=0.5), A.keyword_metrics(crit="2", clicks=50, cost=10.0, conversions=1.0),
         A.keyword_metrics(crit="3", clicks=50, cost=10.0, conversions=0.0)]
    k = r.parse_keywords(rows, m, GROUPS, NAMES)
    assert [e["keyword"] for e in k["wasted"]["examples"]] == ["none"] and k["rarelyServed"] == 0


def test_every_legacy_ad_format_is_counted_and_the_current_ones_are_not():
    legacy = ["EXPANDED_TEXT_AD", "TEXT_AD", "CALL_ONLY_AD", "EXPANDED_DYNAMIC_SEARCH_AD", "LEGACY_RESPONSIVE_DISPLAY_AD", "LEGACY_APP_INSTALL_AD"]
    current = ["RESPONSIVE_SEARCH_AD", "CALL_AD", "RESPONSIVE_DISPLAY_AD", "SHOPPING_PRODUCT_AD"]
    a = r.parse_ads([A.ad(aid=str(i), atype=t) for i, t in enumerate(legacy + current)], GROUPS, NAMES)
    assert a["legacy"] == 6 and a["total"] == 10


def test_a_responsive_search_ad_without_a_strength_is_unspecified_not_good():
    assert r.parse_ads([A.ad(strength=None)], GROUPS, NAMES)["strength"] == {"UNSPECIFIED": 1}


def test_the_thresholds_for_one_ad_many_ads_and_many_keywords_are_exact():
    groups = r.parse_ad_groups([A.ad_group("1", "10", "none"), A.ad_group("2", "10", "one"), A.ad_group("3", "10", "three"), A.ad_group("4", "10", "four"),
                                A.ad_group("5", "10", "forty"), A.ad_group("6", "10", "forty one")])["groups"]
    ads = r.parse_ads([A.ad(gid="2", aid="a")] + [A.ad(gid="3", aid="b%d" % i) for i in range(3)] + [A.ad(gid="4", aid="c%d" % i) for i in range(4)], groups, {})
    kw = r.parse_keywords([A.keyword(gid="5", crit="k%d" % i, text="t%d" % i) for i in range(40)] +
                          [A.keyword(gid="6", crit="m%d" % i, text="u%d" % i) for i in range(41)], [], groups, {})
    s = r.join_structure(groups, kw, ads, {})
    assert [e["adGroup"] for e in s["oneAd"]["examples"]] == ["one"]                    # no ad at all is a different problem, not "one ad"
    assert sorted(e["adGroup"] for e in s["noLiveAd"]["examples"]) == ["forty", "forty one", "none"]
    assert [e["adGroup"] for e in s["manyAds"]["examples"]] == ["four"]                 # three is fine, four is too many
    assert [e["adGroup"] for e in s["manyKeywords"]["examples"]] == ["forty one"]       # forty is fine


def test_a_term_with_part_of_a_conversion_is_not_waste():
    t = r.parse_terms([A.term("half", 10.0, 5, 0.5), A.term("none", 7.0, 5, 0.0)], NAMES)
    assert t["wastedTerms"] == 1 and t["wastedCost"] == 7.0


def test_the_conversion_value_counts_every_conversion_when_both_values_are_given():
    row = {"metrics": {"conversionsValue": 12.5, "allConversionsValue": 20.0, "conversions": 1.0, "allConversions": 2.0},
           "segments": {"conversionAction": "customers/1/conversionActions/1"}}
    assert r.parse_conversions([A.action("1")], [row])[0]["value"] == 20.0


# ── Reading the whole account ───────────────────────────────────────────────

def a_full_answer():
    return {r.CUSTOMER_GAQL: A.customer(), r.CONVERSIONS_GAQL: [A.action("1")], r.CONVERSION_VOLUME_GAQL: [A.volume("1", 20.0)],
            r.LINKS_GAQL: [A.link()], r.MERCHANT_GAQL: [A.merchant("77")], r.LISTS_GAQL: [A.user_list(search=2000, display=2000)],
            r.CAMPAIGNS_GAQL: [A.campaign("10", "Search | Implants")], r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10")],
            r.CAMPAIGN_CRITERIA_GAQL: [A.criterion("10")], r.CAMPAIGN_NEGATIVES_GAQL: [A.negative("10")], r.CAMPAIGN_LISTS_GAQL: [A.attached_list("10")],
            r.NETWORK_GAQL: [A.network_row("10")], r.AD_GROUPS_GAQL: [A.ad_group("100", "10", "Implants")],
            r.KEYWORDS_GAQL: [A.keyword(qs=7)], r.KEYWORD_METRICS_GAQL: [A.keyword_metrics(impressions=50)], r.ADS_GAQL: [A.ad(aid="1"), A.ad(aid="2")],
            r.TERMS_GAQL: [A.term("implants near me", 12.0, 4, 1.0)]}


def test_the_whole_account_is_read_with_every_query_once_and_joined():
    search = A.fake_search(a_full_answer())
    out = r.read_account("1112223334", search_fn=search)
    assert sorted(search.asked) == sorted(q for _k, _w, q in r.READS) and len(search.asked) == len(r.READS) == 17
    assert out["warnings"] == [] and all(out["ok"].values()) and set(out["ok"]) == {k for k, _w, _q in r.READS}
    assert out["customer"]["name"] == "Acme Dental" and out["conversions"][0]["conversions"] == 20.0 and out["conversions"][0]["mine"] is True
    assert out["links"] == {"counts": {"MERCHANT_CENTER": 1}, "merchantCenter": ["77"]} and out["lists"]["withMembers"] == 1
    c = out["campaigns"]["campaigns"][0]
    assert c["name"] == "Search | Implants" and c["negatives"] == 1 and c["targeting"]["locations"] == 1
    assert out["network"]["total"] == 100.0
    assert out["structure"]["searchAdGroups"] == 1 and out["structure"]["oneAd"]["count"] == 0 and out["structure"]["noKeywords"]["count"] == 0
    assert out["keywords"]["enabled"] == 1 and "_perGroup" not in out["keywords"] and out["keywords"]["lowQualityExamples"] == []
    assert out["ads"]["rsa"] == 2 and "_perGroup" not in out["ads"]
    assert out["terms"]["cost"] == 12.0
    assert out["keywords"]["lowQualityExamples"] == [] and out["keywords"]["qs"]["scored"] == 1


def test_the_names_of_campaigns_and_ad_groups_reach_the_examples():
    ans = a_full_answer()
    ans[r.KEYWORDS_GAQL] = [A.keyword(qs=2, text="low one")]
    ans[r.KEYWORD_METRICS_GAQL] = [A.keyword_metrics(impressions=900)]
    ans[r.TERMS_GAQL] = [A.term("free implants", 30.0, 9, 0)]
    out = r.read_account("1112223334", search_fn=A.fake_search(ans))
    assert out["keywords"]["lowQualityExamples"][0]["campaign"] == "Search | Implants" and out["keywords"]["lowQualityExamples"][0]["adGroup"] == "Implants"
    assert out["terms"]["examples"][0]["campaign"] == "Search | Implants"


def test_an_unreadable_account_record_is_an_error_not_a_partial_read():
    import pytest
    from tracker import paid_gads_segments as gads
    with pytest.raises(gads.GadsError):
        r.read_account("1112223334", search_fn=A.fake_search(a_full_answer(), fail={r.CUSTOMER_GAQL}))


def test_every_other_part_failing_alone_is_a_warning_and_leaves_what_depends_on_it_unknown():
    for key, what, gaql in r.READS[1:]:
        out = r.read_account("1112223334", search_fn=A.fake_search(a_full_answer(), fail={gaql}))
        assert out["ok"][key] is False and sum(1 for v in out["ok"].values() if not v) == 1, key
        assert len(out["warnings"]) == 1 and out["warnings"][0].startswith(what[0].upper() + what[1:]) and "could not be read" in out["warnings"][0], key
        assert out["customer"]["name"] == "Acme Dental"


def test_what_each_failed_part_takes_down():
    def read(key):
        gaql = next(q for k, _w, q in r.READS if k == key)
        return r.read_account("1112223334", search_fn=A.fake_search(a_full_answer(), fail={gaql}))
    assert read("conversionActions")["conversions"] is None
    assert read("conversionVolume")["conversions"][0]["conversions"] is None            # the actions are still there, their volume is not
    assert read("links")["links"] is None and read("merchant")["links"]["merchantCenter"] is None
    assert read("lists")["lists"] is None
    c = read("campaigns")
    assert c["campaigns"] is None and c["structure"]["searchAdGroups"] == 1             # ad groups still read, without campaign names
    assert read("campaignMetrics")["campaigns"]["campaigns"][0]["metrics"] is None and read("campaignMetrics")["campaigns"]["totals"] is None
    assert read("criteria")["campaigns"]["campaigns"][0]["targeting"] is None
    assert read("campaignNegatives")["campaigns"]["campaigns"][0]["negatives"] is None
    assert read("campaignLists")["campaigns"]["campaigns"][0]["lists"] is None
    assert read("network")["network"] is None
    ag = read("adGroups")
    assert ag["structure"] is None and ag["keywords"]["enabled"] == 1 and ag["ads"]["rsa"] == 2
    assert read("keywords")["keywords"] is None and "noKeywords" not in read("keywords")["structure"]
    assert read("keywordMetrics")["keywords"]["wasted"] is None
    assert read("ads")["ads"] is None and "noLiveAd" not in read("ads")["structure"]
    assert read("terms")["terms"] is None


def test_an_account_with_nothing_in_it_reads_as_empty_not_as_an_error():
    out = r.read_account("1112223334", search_fn=A.fake_search({r.CUSTOMER_GAQL: A.customer()}))
    assert out["warnings"] == [] and out["conversions"] == [] and out["campaigns"]["counts"]["enabled"] == 0
    assert out["keywords"]["total"] == 0 and out["ads"]["total"] == 0 and out["terms"]["rows"] == 0 and out["structure"]["adGroups"] == 0


def test_two_workers_or_one_give_the_same_read():
    a = r.read_account("1112223334", search_fn=A.fake_search(a_full_answer()), workers=1)
    b = r.read_account("1112223334", search_fn=A.fake_search(a_full_answer()), workers=8)
    assert a == b
