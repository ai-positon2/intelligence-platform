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
    assert c["optimizationScore"] == 43
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
    assert out["examples"][1] == {"name": "Small", "type": "CRM_BASED", "size": 300}


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
    assert c["id"] == "10" and c["name"] == "Search | Implants" and c["type"] == "SEARCH" and c["bidding"] == "TARGET_CPA" and c["portfolio"] is True
    assert c["targetKind"] == "cpa" and c["target"] == 40.0 and c["budget"] == 75.5 and c["budgetShared"] is True
    assert c["googleSearch"] is True and c["searchPartners"] is True and c["displayNetwork"] is True and c["geoMode"] == "PRESENCE"
    assert c["primaryStatus"] == "LIMITED" and c["reasons"] == ["BUDGET_CONSTRAINED"] and c["serving"] == "SERVING"
    assert c["metrics"] == {"impressions": 22138, "clicks": 6109, "cost": 15082.63, "conversions": 198.74, "allConversions": 346.91,
                            "budgetLost": 18, "rankLost": 32, "share": 51}
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


def test_impression_share_is_a_whole_percent_and_zero_is_not_missing():
    c = r.parse_campaigns([A.campaign("10")], [A.campaign_metrics("10", budget_lost=0.0, rank_lost=0.004, share=1.0)])["campaigns"][0]
    assert c["metrics"]["budgetLost"] == 0 and c["metrics"]["rankLost"] == 0 and c["metrics"]["share"] == 100
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
