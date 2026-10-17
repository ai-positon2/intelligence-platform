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
