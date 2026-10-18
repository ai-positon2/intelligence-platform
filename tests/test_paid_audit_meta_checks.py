"""Account Audit: the Meta Ads checklist (tracker/paid_audit_meta_checks.py)."""
import copy
import datetime as dt

from tests import audit_meta_fakes as F
from tracker import paid_audit_meta_checks as c
from tracker import paid_audit_model as model


def one(cid, read=None, **over):
    rd = copy.deepcopy(read or F.a_read())
    rd.update(over)
    out = c.run(rd, only={cid})
    assert len(out) == 1 and out[0]["id"] == cid
    assert "the check itself failed" not in out[0]["found"], out[0]["found"]
    return out[0]


def acct(**kw):
    return F.a_read(answers={"account": F.account(**kw)})


# ── The checklist as a whole ────────────────────────────────────────────────

def test_every_check_is_in_the_catalog_runs_once_and_a_healthy_account_has_no_failure():
    out = c.run(F.a_read())
    ids = [f["id"] for f in out]
    assert ids == [cid for cid, _fn in c.CHECKS] and len(set(ids)) == len(ids) and len(ids) == 17
    assert all(cid in model.CATALOG for cid in ids) and all(f["platform"] == "meta" for f in out)
    assert [(f["id"], f["found"]) for f in out if f["result"] in ("fail", "warn")] == []


def test_a_check_that_raises_is_reported_unknown_not_fatal():
    c.CHECKS.append(("M-ACC-01", lambda ctx: 1 / 0))
    try:
        out = c.run(F.a_read(), only={"M-ACC-01"})
    finally:
        c.CHECKS.pop()
    assert out[0]["result"] == "pass" and out[1]["result"] == "unknown" and "ZeroDivisionError" in out[1]["found"]
    assert [f["id"] for f in c.run(F.a_read(), only={"M-ACC-02", "M-ACC-01"})] == ["M-ACC-01", "M-ACC-02"]


# ── The ad account ──────────────────────────────────────────────────────────

def test_account_status():
    assert one("M-ACC-01")["result"] == "pass"
    f = one("M-ACC-01", acct(account_status=2, disable_reason=1))
    assert f["result"] == "fail" and f["found"] == "The ad account is disabled (an ads integrity policy)."
    assert one("M-ACC-01", acct(account_status=2, disable_reason=0))["found"] == "The ad account is disabled."
    assert one("M-ACC-01", acct(account_status=3))["found"] == "The ad account is unsettled." and one("M-ACC-01", acct(account_status=3))["result"] == "fail"
    assert one("M-ACC-01", acct(account_status=101))["found"] == "The ad account is closed."
    assert one("M-ACC-01", acct(account_status=100))["found"] == "The ad account is pending closure."
    for code, text in ((9, "in grace period"), (7, "pending review")):
        f = one("M-ACC-01", acct(account_status=code))
        assert f["result"] == "warn" and f["found"] == "The ad account is %s." % text
    assert one("M-ACC-01", acct(account_status=999))["result"] == "unknown"


def test_spend_cap():
    assert one("M-ACC-02")["result"] == "pass" and one("M-ACC-02")["found"] == "The ad account has no spend cap."
    cap = lambda spent: acct(spend_cap="1000000", amount_spent=str(spent * 100))
    f = one("M-ACC-02", cap(8000))
    assert f["result"] == "pass" and f["found"] == "80% of the USD 10,000 spend cap is used (USD 8,000 spent)."
    assert one("M-ACC-02", cap(8999))["result"] == "pass" and one("M-ACC-02", cap(9000))["result"] == "warn"
    assert one("M-ACC-02", cap(9999))["result"] == "warn" and one("M-ACC-02", cap(10000))["result"] == "fail" and one("M-ACC-02", cap(12000))["result"] == "fail"


def test_pixel_terms():
    assert one("M-ACC-03")["result"] == "pass"
    assert one("M-ACC-03", F.a_read(answers={"accountExtra": F.account_extra(offsite_pixels_tos_accepted=False)}))["result"] == "warn"
    assert one("M-ACC-03", F.a_read(fail={"accountExtra"}))["result"] == "unknown"


# ── Pixel and tracking ──────────────────────────────────────────────────────

def test_a_pixel_is_installed():
    f = one("M-TRK-01")
    assert f["result"] == "pass" and f["found"] == "1 pixel is assigned to this ad account."
    two = F.a_read(answers={"pixels": [F.pixel("1"), F.pixel("2")], "pixelExtra": []})
    assert one("M-TRK-01", two)["found"] == "2 pixels are assigned to this ad account."
    none = F.a_read(answers={"pixels": []})
    assert one("M-TRK-01", none)["result"] == "warn" and one("M-TRK-01", F.a_read(fail={"pixels"}))["result"] == "unknown"


def fired(last):
    return F.a_read(answers={"pixels": [F.pixel(last=last)]})


def test_the_pixel_has_fired_recently():
    now = dt.datetime.fromisoformat(F.NOW)
    at = lambda **kw: (now - dt.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    f = one("M-TRK-02", fired(at(hours=3)))
    assert f["result"] == "pass" and f["found"] == "The pixel last fired 3 hours ago (Acme pixel)."
    assert one("M-TRK-02", fired(at(hours=24)))["result"] == "pass" and one("M-TRK-02", fired(at(hours=25)))["result"] == "warn"
    assert one("M-TRK-02", fired(at(hours=47)))["found"] == "The freshest pixel last fired 47 hours ago (Acme pixel)."
    assert one("M-TRK-02", fired(at(hours=48)))["found"] == "The freshest pixel last fired 2 days ago (Acme pixel)."       # 48 hours is said as days
    f = one("M-TRK-02", fired(at(hours=60)))
    assert f["result"] == "warn" and f["found"] == "The freshest pixel last fired 2 days ago (Acme pixel)."
    assert one("M-TRK-02", fired(at(days=7)))["result"] == "warn" and one("M-TRK-02", fired(at(days=7, hours=1)))["result"] == "fail"
    f = one("M-TRK-02", fired(at(days=40)))
    assert f["result"] == "fail" and f["found"] == "The freshest pixel last fired 40 days ago (Acme pixel)."
    assert one("M-TRK-02", fired(""))["found"] == "No pixel has ever fired." and one("M-TRK-02", fired(""))["result"] == "fail"
    assert one("M-TRK-02", F.a_read(answers={"pixels": []}))["result"] == "na" and one("M-TRK-02", F.a_read(fail={"pixels"}))["result"] == "unknown"
    two = F.a_read(answers={"pixels": [F.pixel("1", "Old", at(days=30)), F.pixel("2", "Fresh", at(hours=2))], "pixelExtra": []})
    assert one("M-TRK-02", two)["found"] == "The pixel last fired 2 hours ago (Fresh)."          # judged on the freshest


def test_conversion_events_not_only_page_views():
    f = one("M-TRK-03")
    assert f["result"] == "pass" and f["found"] == "In the last 7 days the pixel received conversion events: Lead 40."
    custom = F.a_read(answers={"stats": F.stats({"PageView": 9000, "Lead": 100, "Schedule": 60, "MyCustomEvent": 20, "Purchase": 2})})
    assert one("M-TRK-03", custom)["found"] == "In the last 7 days the pixel received conversion events: Lead 100, Schedule 60, MyCustomEvent 20."
    f = one("M-TRK-03", F.a_read(answers={"stats": F.stats({"PageView": 9000, "ViewContent": 4000, "AddToCart": 200, "Search": 10})}))
    assert f["result"] == "fail" and f["found"] == "In the last 7 days the pixel received only browsing events (PageView 9,000, ViewContent 4,000, AddToCart 200)."
    f = one("M-TRK-03", F.a_read(answers={"stats": []}))
    assert f["result"] == "unknown" or f["result"] == "fail"
    empty = F.a_read(answers={"stats": [{"data": []}]})
    assert one("M-TRK-03", empty)["result"] == "fail" and one("M-TRK-03", empty)["found"] == "The pixel received no events in the last 7 days."
    assert one("M-TRK-03", F.a_read(fail={"stats"}))["result"] == "unknown" and one("M-TRK-03", F.a_read(fail={"pixels"}))["result"] == "unknown"


def test_advanced_matching():
    f = one("M-TRK-04")
    assert f["result"] == "pass" and f["found"] == "Automatic advanced matching is on for Acme pixel."
    off = F.a_read(answers={"pixelExtra": [F.pixel_extra(matching=False)]})
    assert one("M-TRK-04", off)["result"] == "warn" and "privacy" in one("M-TRK-04", off)["fix"]
    assert one("M-TRK-04", F.a_read(fail={"pixelExtra"}))["result"] == "unknown" and one("M-TRK-04", F.a_read(answers={"pixels": []}))["result"] == "na"
    assert one("M-TRK-04", F.a_read(fail={"pixels"}))["result"] == "unknown"


def test_the_conversions_api():
    f = one("M-TRK-05")
    assert f["result"] == "pass" and f["found"] == "Server events were received in the last 7 days (server 30)."
    only_pixel = F.a_read(answers={"sources": F.stats({"pixel": 5000})})
    f = one("M-TRK-05", only_pixel)
    assert f["result"] == "warn" and "only through pixel" in f["found"]
    for word in ("capi", "Conversions_API", "SERVER_SIDE"):
        assert one("M-TRK-05", F.a_read(answers={"sources": F.stats({"browser": 100, word: 20})}))["result"] == "pass", word
    none = F.a_read(answers={"sources": [{"data": []}]})
    assert one("M-TRK-05", none)["result"] == "warn" and "no source can be seen" in one("M-TRK-05", none)["found"]
    assert one("M-TRK-05", F.a_read(fail={"sources"}))["result"] == "unknown" and one("M-TRK-05", F.a_read(fail={"pixels"}))["result"] == "unknown"


# ── Campaigns and ad sets ───────────────────────────────────────────────────

def test_learning_limited_ad_sets():
    assert one("M-STR-01")["result"] == "pass" and "None of the 1 live ad set is" in one("M-STR-01")["found"]
    sets = [F.adset("10", "A", learning="FAIL"), F.adset("11", "B"), F.adset("12", "C"), F.adset("13", "D", learning="LEARNING")]
    f = one("M-STR-01", F.a_read(answers={"adSets": sets}))
    assert f["result"] == "warn" and f["found"] == "1 of 4 live ad sets are learning limited." and f["items"] == ["A"]
    half = F.a_read(answers={"adSets": sets[:1] + sets[1:2]})
    assert one("M-STR-01", half)["result"] == "fail"                                         # 1 of 2 is 50%
    assert one("M-STR-01", F.a_read(answers={"adSets": []}))["result"] == "na" and one("M-STR-01", F.a_read(fail={"adSets"}))["result"] == "unknown"


def test_campaigns_and_ad_sets_with_issues():
    assert one("M-STR-02")["result"] == "pass"
    rd = F.a_read(answers={"campaigns": [F.campaign("1", "Camp", issues=("Payment issue",))], "adSets": [F.adset("10", "Set", issues=("Audience too small",))]})
    f = one("M-STR-02", rd)
    assert f["result"] == "warn" and f["found"] == "Live campaigns and ad sets with an issue reported by Meta: 2." and f["items"] == ["Camp (Payment issue)", "Set (Audience too small)"]
    assert one("M-STR-02", F.a_read(fail={"campaigns"}))["result"] == "unknown" and one("M-STR-02", F.a_read(fail={"adSets"}))["result"] == "unknown"


def test_campaigns_that_spend_without_results():
    assert one("M-STR-03")["result"] == "pass" and "USD 100 or more" in one("M-STR-03")["found"]
    camps = [F.campaign("1", "Dead"), F.campaign("2", "Alive"), F.campaign("3", "Cheap"), F.campaign("4", "Traffic", "OUTCOME_TRAFFIC")]
    ins = [F.insight("1", "Dead", spend=300.0, leads=None), F.insight("2", "Alive", spend=900.0, leads=10), F.insight("3", "Cheap", spend=99.0, leads=None),
           F.insight("4", "Traffic", "OUTCOME_TRAFFIC", spend=500.0, leads=None)]
    f = one("M-STR-03", F.a_read(answers={"campaigns": camps, "insights": ins}))
    assert f["result"] == "warn" and f["found"] == "1 campaign spent USD 300 (17% of spend) and recorded no result in the last 30 days." and f["items"] == ["Dead (USD 300)"]
    assert one("M-STR-03", F.a_read(answers={"campaigns": camps[3:], "insights": ins[3:]}))["result"] == "na"
    edge = F.a_read(answers={"campaigns": camps[:2], "insights": [F.insight("1", "Dead", spend=100.0, leads=None), F.insight("2", "Alive", spend=9900.0, leads=10)]})
    assert one("M-STR-03", edge)["items"] == ["Dead (USD 100)"]                                  # exactly the minimum spend is judged
    under = F.a_read(answers={"campaigns": camps[:2], "insights": [F.insight("1", "Dead", spend=99.99, leads=None), F.insight("2", "Alive", spend=9900.0, leads=10)]})
    assert one("M-STR-03", under)["result"] == "pass"
    assert one("M-STR-03", F.a_read(fail={"campaigns"}))["result"] == "unknown" and one("M-STR-03", F.a_read(fail={"insights"}))["result"] == "unknown"


def test_a_big_share_of_spend_with_no_results_fails():
    camps = [F.campaign("1", "Dead"), F.campaign("2", "Alive")]
    f = one("M-STR-03", F.a_read(answers={"campaigns": camps, "insights": [F.insight("1", "Dead", spend=300.0, leads=None), F.insight("2", "Alive", spend=900.0, leads=10)]}))
    assert f["result"] == "fail" and "(25% of spend)" in f["found"]
    just_under = F.a_read(answers={"campaigns": camps, "insights": [F.insight("1", "Dead", spend=299.0, leads=None), F.insight("2", "Alive", spend=901.0, leads=10)]})
    f = one("M-STR-03", just_under)
    assert f["result"] == "warn" and "(25% of spend)" in f["found"]


def test_ad_sets_optimising_for_clicks_in_lead_and_sales_campaigns():
    assert one("M-STR-04")["result"] == "pass"
    camps = [F.campaign("1", "Leads", "OUTCOME_LEADS"), F.campaign("2", "Traffic", "OUTCOME_TRAFFIC"), F.campaign("3", "Sales", "OUTCOME_SALES")]
    sets = [F.adset("10", "Weak leads", "1", goal="LINK_CLICKS"), F.adset("11", "Good leads", "1", goal="LEAD_GENERATION"), F.adset("12", "Traffic clicks", "2", goal="LINK_CLICKS"),
            F.adset("13", "Weak sales", "3", goal="REACH")]
    f = one("M-STR-04", F.a_read(answers={"campaigns": camps, "adSets": sets, "insights": []}))
    assert f["result"] == "warn" and f["found"] == "2 ad sets in lead or sales campaigns optimise for clicks, page views or reach."
    assert f["items"] == ["Weak leads (link clicks, Leads)", "Weak sales (reach, Sales)"]
    for goal in ("LANDING_PAGE_VIEWS", "IMPRESSIONS", "POST_ENGAGEMENT"):
        assert one("M-STR-04", F.a_read(answers={"campaigns": camps[:1], "adSets": [F.adset("10", "X", "1", goal=goal)], "insights": []}))["result"] == "warn", goal
    assert one("M-STR-04", F.a_read(answers={"campaigns": camps[1:2], "adSets": sets[2:3], "insights": []}))["result"] == "na"
    assert one("M-STR-04", F.a_read(fail={"adSets"}))["result"] == "unknown" and one("M-STR-04", F.a_read(fail={"campaigns"}))["result"] == "unknown"


def test_high_frequency_on_big_campaigns():
    assert one("M-STR-05")["result"] == "pass" and "frequency of 4 or more" in one("M-STR-05")["found"]
    camps = [F.campaign("1", "Hot"), F.campaign("2", "Cool"), F.campaign("3", "Hot but tiny"), F.campaign("4", "Edge")]
    ins = [F.insight("1", "Hot", spend=500.0, frequency=4.6), F.insight("2", "Cool", spend=440.0, frequency=1.2), F.insight("3", "Hot but tiny", spend=10.0, frequency=9.0),
           F.insight("4", "Edge", spend=50.0, frequency=4.0)]
    f = one("M-STR-05", F.a_read(answers={"campaigns": camps, "insights": ins}))
    assert f["result"] == "warn" and f["found"] == "2 big campaigns have a frequency of 4 or more." and f["items"] == ["Hot (frequency 4.6)", "Edge (frequency 4)"]
    just = F.a_read(answers={"campaigns": camps[:1], "insights": [F.insight("1", "Hot", spend=500.0, frequency=3.99)]})
    assert one("M-STR-05", just)["result"] == "pass"
    assert "1 big campaign has a frequency" in one("M-STR-05", F.a_read(answers={"campaigns": camps[:1], "insights": ins[:1]}))["found"]
    assert one("M-STR-05", F.a_read(answers={"campaigns": [], "insights": []}))["result"] == "na" and one("M-STR-05", F.a_read(fail={"insights"}))["result"] == "unknown"


# ── Ads ─────────────────────────────────────────────────────────────────────

def test_disapproved_ads():
    assert one("M-ADS-01")["result"] == "pass"
    rows = [F.ad("1", "A", status="DISAPPROVED", feedback={"Health claims": "x"}), F.ad("2", "B", status="DISAPPROVED"), F.ad("3", "C", status="WITH_ISSUES", issues=("Low quality",)), F.ad("4", "D")]
    f = one("M-ADS-01", F.a_read(answers={"ads": rows}))
    assert f["result"] == "fail" and f["found"] == "2 ads are disapproved and 1 more ad with issues." and f["items"] == ["A (Health claims)", "B (no reason given)"] and f["count"] == 2
    f = one("M-ADS-01", F.a_read(answers={"ads": rows[2:]}))
    assert f["result"] == "warn" and f["found"] == "1 ad with issues." and f["items"] == ["C (Low quality)"]
    assert one("M-ADS-01", F.a_read(answers={"ads": rows[:1]}))["found"] == "1 ad is disapproved."
    assert one("M-ADS-01", F.a_read(fail={"ads"}))["result"] == "unknown"


def test_every_live_ad_set_needs_an_ad_that_can_deliver():
    assert one("M-ADS-02")["result"] == "pass" and one("M-ADS-02")["found"] == "Checked 1 live ad set: all have an ad that can deliver."
    sets = [F.adset("10", "Has ad"), F.adset("11", "Dark"), F.adset("12", "Only disapproved"), F.adset("13", "In review"), F.adset("14", "With issues")]
    ads = [F.ad("1", "A", "10"), F.ad("2", "Gone", "12", status="DISAPPROVED"), F.ad("3", "Wait", "13", status="PENDING_REVIEW"), F.ad("4", "Flag", "14", status="WITH_ISSUES", issues=("Low quality",))]
    f = one("M-ADS-02", F.a_read(answers={"adSets": sets, "ads": ads}))
    assert f["result"] == "fail" and f["severity"] == "critical" and f["found"] == "2 of 5 live ad sets have no ad that can deliver." and f["items"] == ["Dark", "Only disapproved"]
    assert one("M-ADS-02", F.a_read(answers={"adSets": []}))["result"] == "na"
    assert one("M-ADS-02", F.a_read(fail={"ads"}))["result"] == "unknown" and one("M-ADS-02", F.a_read(fail={"adSets"}))["result"] == "unknown"
    # an ad set whose only ad is flagged with issues is not "dark": the ads check and this one agree
    only_flag = F.a_read(answers={"adSets": [F.adset("14", "With issues")], "ads": [F.ad("4", "Flag", "14", status="WITH_ISSUES", issues=("Low quality",))]})
    assert one("M-ADS-02", only_flag)["result"] == "pass" and one("M-ADS-01", only_flag)["result"] == "warn"


def test_more_than_one_ad_per_ad_set():
    sets = [F.adset(str(10 + i), "S%d" % i) for i in range(4)]
    def read(n_single):
        ads = []
        for i in range(4):
            ads.append(F.ad("a%d" % i, "A%d" % i, str(10 + i)))
            if i >= n_single:
                ads.append(F.ad("b%d" % i, "B%d" % i, str(10 + i)))
        return F.a_read(answers={"adSets": sets, "ads": ads})
    assert one("M-ADS-03", read(0))["result"] == "pass" and one("M-ADS-03", read(0))["found"] == "0 of 4 live ad sets have a single ad."
    f = one("M-ADS-03", read(1))
    assert f["result"] == "warn" and f["found"] == "1 of 4 live ad sets (25%) have a single ad, so nothing is being tested." and f["items"] == ["S0"]
    assert one("M-ADS-03", read(2))["result"] == "warn" and one("M-ADS-03", read(3))["result"] == "fail"
    dark = F.a_read(answers={"adSets": sets[:2], "ads": [F.ad("a", "A", "10")]})
    f = one("M-ADS-03", dark)
    assert f["found"].startswith("1 of 1 live ad set (100%)")                                # an ad set with no ad is a different finding
    assert one("M-ADS-03", F.a_read(answers={"adSets": [], "ads": []}))["result"] == "na" and one("M-ADS-03", F.a_read(fail={"ads"}))["result"] == "unknown"


def test_an_ad_account_with_no_live_campaign_says_it_is_not_advertising():
    f = one("M-STR-06")
    assert f["result"] == "pass" and f["found"] == "1 live campaign."
    none = F.a_read(answers={"campaigns": [], "insights": [], "adSets": [], "ads": []})
    f = one("M-STR-06", none)
    assert f["result"] == "warn" and f["severity"] == "high" and f["found"].startswith("No campaign is live. The ad account is not advertising")
    assert one("M-STR-06", F.a_read(answers={"campaigns": [F.campaign("1"), F.campaign("2", "B")]}))["found"] == "2 live campaigns."
    assert one("M-STR-06", F.a_read(fail={"campaigns"}))["result"] == "unknown"


def test_a_lead_campaign_optimised_for_another_conversion_event_has_results():
    camps = [F.campaign("1", "Booked"), F.campaign("2", "Nothing")]
    ins = [dict(F.insight("1", "Booked", spend=400.0, leads=None), actions=[{"action_type": "schedule", "value": "80"}]), F.insight("2", "Nothing", spend=300.0, leads=None)]
    f = one("M-STR-03", F.a_read(answers={"campaigns": camps, "insights": ins}))
    assert f["items"] == ["Nothing (USD 300)"] and f["found"].startswith("1 campaign spent USD 300")                  # the Schedule campaign is not called dead
    contact = [dict(F.insight("1", "Booked", spend=400.0, leads=None), actions=[{"action_type": "contact", "value": "9"}])]
    assert one("M-STR-03", F.a_read(answers={"campaigns": camps[:1], "insights": contact}))["result"] == "pass"


def test_the_events_meta_detects_on_its_own_are_not_conversion_events():
    only_auto = F.a_read(answers={"stats": F.stats({"PageView": 9000, "Microdata": 900, "SubscribedButtonClick": 300})})
    f = one("M-TRK-03", only_auto)
    assert f["result"] == "fail" and "only browsing events" in f["found"]
    assert one("M-TRK-03", F.a_read(answers={"stats": F.stats({"PageView": 9000, "Microdata": 900, "Lead": 10})}))["result"] == "pass"


def test_a_campaign_that_spent_the_equivalent_of_a_few_dollars_is_not_called_dead_in_rupees():
    camps = [F.campaign("1", "Quiet"), F.campaign("2", "Alive")]
    ins = [F.insight("1", "Quiet", spend=3000.0, leads=None), F.insight("2", "Alive", spend=900000.0, leads=10)]       # INR 3,000 is about $35
    rd = F.a_read(answers={"campaigns": camps, "insights": ins})
    rd["account"] = dict(rd["account"], currency="INR")
    assert one("M-STR-03", rd)["result"] == "pass"
    ins[0] = F.insight("1", "Quiet", spend=9000.0, leads=None)                                                          # INR 9,000 is over $100
    rd = F.a_read(answers={"campaigns": camps, "insights": ins})
    rd["account"] = dict(rd["account"], currency="INR")
    f = one("M-STR-03", rd)
    assert f["result"] == "warn" and f["items"] == ["Quiet (INR 9,000)"]
