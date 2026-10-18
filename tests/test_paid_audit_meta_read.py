"""Account Audit: reading one Meta ad account (tracker/paid_audit_meta_read.py), on answers shaped like the Graph API's."""
import datetime as dt

import pytest

from tests import audit_meta_fakes as F
from tracker import paid_audit_meta_read as m
from tracker import paid_meta as meta


# ── Small helpers ───────────────────────────────────────────────────────────

def test_time_parsing_accepts_meta_formats_and_refuses_junk():
    utc = dt.timezone.utc
    assert m.parse_time("2026-10-16T12:30:00+0000") == dt.datetime(2026, 10, 16, 12, 30, tzinfo=utc)
    assert m.parse_time("2026-10-16T12:30:00+00:00") == dt.datetime(2026, 10, 16, 12, 30, tzinfo=utc)
    assert m.parse_time("2026-10-16T12:30:00Z") == dt.datetime(2026, 10, 16, 12, 30, tzinfo=utc)
    assert m.parse_time("2026-10-16T12:30:00-0500") == dt.datetime(2026, 10, 16, 17, 30, tzinfo=utc)
    assert m.parse_time("2026-10-16T12:30:00") == dt.datetime(2026, 10, 16, 12, 30, tzinfo=utc)
    assert m.parse_time("") is None and m.parse_time(None) is None and m.parse_time("yesterday") is None


def test_money_is_in_the_currencys_smallest_unit():
    assert m._major("250000", "USD") == 2500.0 and m._major("1500", "JPY") == 1500.0 and m._major(None, "USD") == 0.0 and m._major("x", "USD") == 0.0


def test_issue_summaries_are_short_and_capped():
    items = [{"error_summary": "Payment failed"}, {"error_message": "Something"}, {}, {"error_summary": "d"}, "junk"]
    assert m._issues(items) == ["Payment failed", "Something", "Meta reports an issue"]
    assert m._issues(None) == [] and m._issues([]) == []


# ── The account ─────────────────────────────────────────────────────────────

def test_account_status_spend_cap_and_terms():
    a = m.parse_account(F.account(), F.account_extra())
    assert a["name"] == "Acme Dental" and a["status"] == "ACTIVE" and a["statusCode"] == 1 and a["currency"] == "USD" and a["timeZone"] == "America/New_York"
    assert a["spendCap"] == 0.0 and a["amountSpent"] == 2500.0 and a["prepaid"] is False and a["pixelTerms"] is True and a["business"] == "Acme Holdings"
    capped = m.parse_account(F.account(spend_cap="1000000", amount_spent="920000", account_status=3, disable_reason=1))
    assert capped["spendCap"] == 10000.0 and capped["amountSpent"] == 9200.0 and capped["status"] == "UNSETTLED" and capped["disableReason"] == 1
    assert m.parse_account(F.account(account_status=2))["status"] == "DISABLED" and m.parse_account(F.account(account_status=999))["status"] == "UNKNOWN"


def test_account_extras_are_none_when_not_read_and_false_when_meta_says_false():
    a = m.parse_account(F.account(), None)
    assert a["prepaid"] is None and a["pixelTerms"] is None and a["business"] == ""
    b = m.parse_account(F.account(), F.account_extra(is_prepay_account=False, offsite_pixels_tos_accepted=False))
    assert b["prepaid"] is False and b["pixelTerms"] is False
    assert m.parse_account(None)["status"] == "UNKNOWN" and m.parse_account({"spend_cap": 0})["spendCap"] == 0.0


# ── Pixels ──────────────────────────────────────────────────────────────────

def test_pixels_are_listed_freshest_first_with_their_matching_settings():
    rows = [F.pixel("1", "Old", "2026-09-01T00:00:00+0000"), F.pixel("2", "New", "2026-10-17T11:00:00+0000"), F.pixel("3", "Never", "")]
    extra = [F.pixel_extra("2", True, "FIRST_PARTY_COOKIE_ENABLED"), F.pixel_extra("1", False, "FIRST_PARTY_COOKIE_DISABLED")]
    px = m.parse_pixels(rows, extra)
    assert [p["name"] for p in px] == ["New", "Old", "Never"]
    assert px[0]["autoMatching"] is True and px[0]["firstPartyCookie"] == "FIRST_PARTY_COOKIE_ENABLED" and px[1]["autoMatching"] is False
    assert px[2]["autoMatching"] is None and px[2]["lastFired"] == ""                         # no extra row for it
    assert m.parse_pixels(rows, None)[0]["autoMatching"] is None and m.parse_pixels([], []) == []
    assert m.parse_pixels([F.pixel(unavailable=True)], [])[0]["unavailable"] is True


def test_pixel_stats_are_added_up_by_value():
    rows = F.stats({"PageView": 5001, "Lead": 41})
    assert m.parse_stats(rows) == {"PageView": 5001, "Lead": 41}
    assert m.parse_stats([{"data": [{"value": "Lead", "count": "3"}, {"value": "", "count": 9}, {"count": 4}]}, {}]) == {"Lead": 3}
    assert m.parse_stats([]) == {}


# ── Campaigns, ad sets, ads ─────────────────────────────────────────────────

def test_campaigns_carry_their_objective_issues_and_the_last_30_days():
    rows = [F.campaign("1", "Leads A", issues=("Payment issue",)), F.campaign("2", "Sales B", "OUTCOME_SALES", special=("HOUSING",)), F.campaign("3", "Quiet")]
    ins = [F.insight("1", "Leads A", spend=500.0, leads=25, frequency=4.2, reach=1000, impressions=40000, clicks=800),
           F.insight("2", "Sales B", "OUTCOME_SALES", spend=900.0, leads=None)]
    out = m.parse_campaigns(rows, ins)
    assert [c["name"] for c in out] == ["Sales B", "Leads A", "Quiet"]                          # biggest spender first
    a = out[1]
    assert a["kind"] == "LEADS" and a["issues"] == ["Payment issue"] and a["spend"] == 500.0 and a["results"] == 25.0 and a["frequency"] == 4.2
    assert a["reach"] == 1000 and a["impressions"] == 40000 and a["clicks"] == 800 and a["bidStrategy"] == "LOWEST_COST_WITHOUT_CAP"
    assert out[0]["special"] == ["HOUSING"] and out[0]["kind"] == "SALES" and out[0]["results"] == 0.0
    sched = m.parse_campaigns([F.campaign("5", "Booked")], [dict(F.insight("5", "Booked", leads=None), actions=[{"action_type": "schedule", "value": "80"}])])[0]
    assert sched["results"] == 0.0 and sched["anyResults"] == 80.0                  # a lead campaign optimised for Schedule has results of its own
    assert out[1]["anyResults"] == 25.0 and out[2]["anyResults"] == 0.0
    assert out[2]["spend"] == 0.0 and out[2]["results"] == 0.0 and out[2]["frequency"] == 0.0           # read, nothing spent
    assert out[2]["impressions"] == 0 and out[2]["clicks"] == 0 and out[2]["reach"] == 0
    unnamed = m.parse_campaigns([{"id": "77"}], [])
    assert unnamed[0]["name"] == "77" and unnamed[0]["objective"] == "" and unnamed[0]["kind"] == "OTHER"          # a campaign with no name is its id


def test_campaign_numbers_are_none_when_the_insights_were_not_read():
    out = m.parse_campaigns([F.campaign()], None)
    assert out[0]["spend"] is None and out[0]["results"] is None and out[0]["frequency"] is None and out[0]["impressions"] is None and out[0]["anyResults"] is None


def test_ad_sets_carry_learning_and_issues():
    rows = [F.adset("10", "A", learning="FAIL", issues=("Audience too small",), conversions=12), F.adset("11", "B", learning="LEARNING"), {"id": "12"}]
    out = m.parse_adsets(rows)
    assert out[0] == {"id": "10", "name": "A", "campaignId": "1", "optimization": "LEAD_GENERATION", "billing": "IMPRESSIONS", "learning": "FAIL",
                      "learningConversions": 12, "issues": ["Audience too small"], "destination": "WEBSITE"}
    assert out[1]["learning"] == "LEARNING" and out[2]["learning"] == "" and out[2]["issues"] == [] and out[2]["campaignId"] == ""


def test_ads_are_counted_by_delivery_state_and_disapproved_ones_say_why():
    sets = m.parse_adsets([F.adset("10", "Set A"), F.adset("11", "Set B")])
    camps = m.parse_campaigns([F.campaign("1", "Camp")])
    rows = [F.ad("1", "A1", "10"), F.ad("2", "A2", "10"), F.ad("3", "B1", "11"), F.ad("4", "Bad", "10", status="DISAPPROVED", feedback={"Health claims": "x", "Before and after": "y"}),
            F.ad("5", "Flag", "11", status="WITH_ISSUES", issues=("Low quality",)), F.ad("6", "Wait", "11", status="PENDING_REVIEW")]
    a = m.parse_ads(rows, sets, camps)
    assert a["total"] == 6 and a["byStatus"] == {"ACTIVE": 3, "DISAPPROVED": 1, "WITH_ISSUES": 1, "PENDING_REVIEW": 1}
    assert a["_perAdSet"] == {"10": 2, "11": 3}                                  # active, flagged and in review all count; the disapproved one does not
    assert a["disapproved"] == {"count": 1, "examples": [{"ad": "Bad", "adSet": "Set A", "campaign": "Camp", "why": ["Before and after", "Health claims"]}]}
    assert a["withIssues"]["examples"][0] == {"ad": "Flag", "adSet": "Set B", "campaign": "Camp", "why": ["Low quality"]}
    assert "_perAdSet" not in m.compact(a) and m.compact(None) is None


def test_an_ad_without_a_name_is_its_id_and_a_reason_list_is_capped_at_three():
    a = m.parse_ads([{"id": "999", "effective_status": "DISAPPROVED", "adset_id": "10", "ad_review_feedback": {"global": {"d": 1, "a": 1, "c": 1, "b": 1}}}], [], [])
    assert a["disapproved"]["examples"][0]["ad"] == "999" and a["disapproved"]["examples"][0]["why"] == ["a", "b", "c"]


def test_a_disapproved_ad_without_review_feedback_falls_back_to_its_issues():
    a = m.parse_ads([F.ad("4", "Bad", status="DISAPPROVED", issues=("Policy",))], [], [])
    assert a["disapproved"]["examples"][0]["why"] == ["Policy"] and a["disapproved"]["examples"][0]["adSet"] == ""


# ── Reading the whole account ───────────────────────────────────────────────

def test_the_whole_account_is_read_and_joined():
    g = F.Graph()
    out = m.read_account(F.ACT, get=g.get, pages=g.pages, now=dt.datetime.fromisoformat(F.NOW))
    assert out["warnings"] == [] and all(out["ok"].values()) and out["readAt"] == F.NOW
    assert out["account"]["status"] == "ACTIVE" and out["pixels"][0]["name"] == "Acme pixel" and out["pixels"][0]["autoMatching"] is True
    assert out["stats"] == {"days": 7, "events": {"PageView": 5000, "Lead": 40}, "methods": {"pixel": 5000, "server": 30}}
    assert out["campaigns"][0]["results"] == 25.0 and out["adSets"][0]["learning"] == "SUCCESS" and out["ads"]["total"] == 2 and out["adsPerSet"] == {"10": 2}
    assert "_perAdSet" not in out["ads"]
    keys = [k for _p, k in g.asked]
    assert keys == ["account", "accountExtra", "accountBusiness", "pixels", "pixelExtra", "stats", "sources", "insights", "campaigns", "adSets", "ads"]


def test_requests_ask_for_live_things_in_the_last_30_days_and_stats_for_seven_days():
    seen = {}

    def get(path, params):
        seen[("get", path)] = params
        return F.account() if params["fields"] == m.ACCOUNT_CORE else F.account_extra()

    def pages(path, params):
        seen[(path.rsplit("/", 1)[-1], params.get("aggregation") or params.get("fields"))] = (path, params)
        return F.Graph().answers[F.Graph().key(path, params)]
    m.read_account(F.ACT, get=get, pages=pages, now=dt.datetime.fromisoformat(F.NOW))
    camp = seen[("campaigns", m.CAMPAIGN_FIELDS)][1]
    assert camp["effective_status"] == '["ACTIVE", "WITH_ISSUES"]' and camp["limit"] == 500
    assert seen[("adsets", m.ADSET_FIELDS)][1]["limit"] == 500 and seen[("adsets", m.ADSET_FIELDS)][1]["effective_status"] == '["ACTIVE", "WITH_ISSUES"]'
    assert seen[("adspixels", m.PIXEL_CORE)][1]["limit"] == 100 and seen[("adspixels", m.PIXEL_EXTRA)][1]["limit"] == 100
    ads = seen[("ads", m.AD_FIELDS)][1]
    assert ads["effective_status"] == '["ACTIVE", "DISAPPROVED", "WITH_ISSUES", "PENDING_REVIEW"]'
    ins = seen[("insights", m.INSIGHT_FIELDS)][1]
    assert ins["level"] == "campaign" and ins["date_preset"] == "last_30d" and ins["limit"] == 500 and ads["limit"] == 500
    path, st = seen[("stats", "event")]
    assert path == "111/stats" and st["start_time"] == int(dt.datetime.fromisoformat(F.NOW).timestamp()) - 7 * 86400
    assert seen[("stats", "event_detection_method")][1]["start_time"] == st["start_time"]
    assert st["limit"] == 500 and seen[("stats", "event_detection_method")][1]["limit"] == 500


def test_an_unreadable_account_is_an_error_but_every_other_part_is_a_warning():
    with pytest.raises(meta.AdsError):
        F.a_read(fail={"account"})
    for part, flag in (("accountExtra", "accountExtra"), ("pixels", "pixels"), ("campaigns", "campaigns"), ("insights", "insights"), ("adSets", "adSets"), ("ads", "ads")):
        out = F.a_read(fail={part})
        assert out["ok"][flag] is False and len(out["warnings"]) == 1 and "could not be read" in out["warnings"][0], part
        assert out["account"]["name"] == "Acme Dental", part
    assert F.a_read(fail={"pixels"})["warnings"] == ["The pixels could not be read (Meta answered 400: (#100) nonexisting field)."]


def test_what_each_failed_part_takes_down():
    assert F.a_read(fail={"accountExtra"})["account"]["pixelTerms"] is None
    out = F.a_read(fail={"pixels"})
    assert out["pixels"] is None and out["stats"] is None and "pixelExtra" not in out["ok"]
    out = F.a_read(fail={"pixelExtra"})
    assert out["pixels"][0]["autoMatching"] is None and out["stats"]["events"]["Lead"] == 40
    out = F.a_read(fail={"stats"})
    assert out["stats"]["events"] is None and out["stats"]["methods"]["server"] == 30 and out["ok"]["stats:111"] is False
    out = F.a_read(fail={"sources"})
    assert out["stats"]["methods"] is None and out["stats"]["events"]["Lead"] == 40 and out["ok"]["sources:111"] is False
    out = F.a_read(fail={"stats", "sources"})
    assert out["stats"] is None
    out = F.a_read(fail={"campaigns"})
    assert out["campaigns"] is None and out["ads"]["disapproved"]["count"] == 0
    assert F.a_read(fail={"insights"})["campaigns"][0]["spend"] is None
    assert F.a_read(fail={"adSets"})["adSets"] is None and F.a_read(fail={"ads"})["ads"] is None and F.a_read(fail={"ads"})["adsPerSet"] is None


def test_only_the_three_freshest_pixels_have_their_events_read():
    px = [F.pixel(str(i), "P%d" % i, "2026-10-%02dT10:00:00+0000" % (10 + i)) for i in range(5)]
    g = F.Graph({"pixels": px, "pixelExtra": []})
    m.read_account(F.ACT, get=g.get, pages=g.pages, now=dt.datetime.fromisoformat(F.NOW))
    stats_paths = [p for p, k in g.asked if k == "stats"]
    assert stats_paths == ["4/stats", "3/stats", "2/stats"]


def test_meta_asking_to_wait_stops_the_read():
    with pytest.raises(meta.OverQuota):
        F.a_read(quota={"campaigns"})
    with pytest.raises(meta.OverQuota):
        F.a_read(quota={"stats"})


def test_an_odd_answer_in_an_optional_part_is_a_warning_not_a_crash():
    out = F.a_read(answers={"campaigns": "not a list"})
    assert out["ok"]["campaigns"] is False and out["campaigns"] is None and "could not be read" in out["warnings"][0]
    bad = F.a_read(answers={"adSets": [None]})
    assert bad["ok"]["adSets"] is False and bad["adSets"] is None and "could not be read" in bad["warnings"][0]
    assert bad["account"]["name"] == "Acme Dental" and bad["ads"]["total"] == 2                 # the other parts are untouched


def test_an_account_with_nothing_live_reads_as_empty():
    out = F.a_read(answers={"pixels": [], "campaigns": [], "insights": [], "adSets": [], "ads": []})
    assert out["warnings"] == [] and out["pixels"] == [] and out["stats"] is None and out["campaigns"] == [] and out["adSets"] == [] and out["ads"]["total"] == 0


def test_a_token_without_business_management_still_gives_the_pixel_terms_and_the_audit_says_nothing_of_it():
    """Live: Meta answered "(#100) Requires business_management permission to access the field" to the business, and with it the account's pixel
    terms were lost, because both were asked in one request."""
    out = F.a_read(fail={"accountBusiness"})
    assert out["account"]["pixelTerms"] is True and out["account"]["prepaid"] is False and out["account"]["business"] == ""
    assert out["warnings"] == [] and all(out["ok"].values())


def test_the_business_name_is_read_when_the_token_may_see_it():
    assert F.a_read()["account"]["business"] == "Acme Holdings"
    assert F.a_read(fail={"accountExtra"})["account"]["business"] == ""            # no terms read: the account record carries no extras at all


def test_meta_asking_to_wait_while_the_business_is_read_stops_the_read():
    with pytest.raises(meta.OverQuota):
        F.a_read(quota={"accountBusiness"})
