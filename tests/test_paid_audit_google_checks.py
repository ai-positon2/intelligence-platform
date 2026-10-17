"""Account Audit: the Google Ads checklist (tracker/paid_audit_google_checks.py)."""
import copy

import pytest

from tests import audit_fakes as A
from tracker import paid_audit_google_checks as c
from tracker import paid_audit_google_read as r
from tracker import paid_audit_model as model


def one(cid, read=None, **over):
    """The finding of one check on a healthy read with parts replaced."""
    rd = copy.deepcopy(read or A.a_read())
    rd.update(over)
    out = c.run(rd, only={cid})
    assert len(out) == 1 and out[0]["id"] == cid
    return out[0]


def customer_with(**kw):
    rd = A.a_read()
    rd["customer"] = dict(rd["customer"], **kw)
    return rd


def actions_with(rows, volumes=None):
    ans = {r.CUSTOMER_GAQL: A.customer(), r.CONVERSIONS_GAQL: rows, r.CONVERSION_VOLUME_GAQL: volumes or []}
    return r.read_account("1112223334", search_fn=A.fake_search(ans))["conversions"]


# ── The checklist as a whole ────────────────────────────────────────────────

def test_every_check_is_in_the_catalog_runs_once_and_a_healthy_account_has_no_failure():
    out = c.run(A.a_read())
    ids = [f["id"] for f in out]
    assert ids == [cid for cid, _fn in c.CHECKS] and len(set(ids)) == len(ids)
    assert all(cid in model.CATALOG for cid in ids)
    bad = [(f["id"], f["found"]) for f in out if f["result"] == "fail"]
    assert bad == [], bad


def test_a_check_that_raises_is_reported_unknown_not_fatal():
    boom = lambda ctx: 1 / 0
    c.CHECKS.append(("G-SET-01", boom))
    try:
        out = c.run(A.a_read(), only={"G-SET-01"})
    finally:
        c.CHECKS.pop()
    assert len(out) == 2 and out[0]["result"] == "pass" and out[1]["result"] == "unknown" and "ZeroDivisionError" in out[1]["found"]


def test_only_limits_the_run():
    assert [f["id"] for f in c.run(A.a_read(), only={"G-SET-02", "G-SET-01"})] == ["G-SET-01", "G-SET-02"]
    assert c.run(A.a_read(), only=set()) == []


# ── Account and settings ────────────────────────────────────────────────────

def test_active_account():
    assert one("G-SET-01")["result"] == "pass"
    for status in ("SUSPENDED", "CANCELED", "CLOSED"):
        f = one("G-SET-01", customer_with(status=status))
        assert f["result"] == "fail" and status.lower() in f["found"]
    assert one("G-SET-01", customer_with(status="UNKNOWN"))["result"] == "unknown"
    assert one("G-SET-01", customer_with(test=True))["result"] == "fail"
    t = one("G-SET-01", customer_with(test=True, status="ENABLED"))
    assert "test account" in t["found"]


def test_auto_tagging():
    assert one("G-SET-02")["result"] == "pass" and one("G-SET-02", customer_with(autoTagging=False))["result"] == "fail"


def test_conversion_tracking_owner():
    for status, who in (("CONVERSION_TRACKING_MANAGED_BY_SELF", "this account"), ("CONVERSION_TRACKING_MANAGED_BY_THIS_MANAGER", "the manager account above it"),
                        ("CONVERSION_TRACKING_MANAGED_BY_ANOTHER_MANAGER", "another manager account")):
        f = one("G-SET-03", customer_with(trackingStatus=status))
        assert f["result"] == "pass" and who in f["found"]
    assert one("G-SET-03", customer_with(trackingStatus="NOT_CONVERSION_TRACKED"))["result"] == "fail"
    assert one("G-SET-03", customer_with(trackingStatus="UNKNOWN"))["result"] == "unknown"


def test_google_tag():
    f = one("G-SET-04")
    assert f["result"] == "pass" and "AW-16599110526" in f["found"]
    assert one("G-SET-04", customer_with(googleTagId=""))["result"] == "warn"
    assert one("G-SET-04", customer_with(googleTagId="", trackingStatus="CONVERSION_TRACKING_MANAGED_BY_ANOTHER_MANAGER"))["result"] == "na"


def test_enhanced_conversions_need_the_data_terms_too():
    assert one("G-SET-05")["result"] == "pass"
    f = one("G-SET-05", customer_with(dataTerms=False))
    assert f["result"] == "warn" and "cannot work" in f["found"]
    f = one("G-SET-05", customer_with(enhancedLeads=False))
    assert f["result"] == "warn" and "off" in f["found"]
    assert "privacy" in f["fix"]                                          # regulated clients may not be able to turn it on


def test_call_reporting_is_only_needed_when_calls_are_tracked():
    assert one("G-SET-06")["result"] == "na"
    calls = actions_with([A.action("1", "Calls", type="AD_CALL", category="PHONE_CALL_LEAD"), A.action("2", "Form")])
    assert one("G-SET-06", conversions=calls)["result"] == "pass"
    f = one("G-SET-06", customer_with(callReporting=False), conversions=calls)
    assert f["result"] == "fail" and "1 call conversion action is enabled but call reporting is off" in f["found"]
    f = one("G-SET-06", customer_with(callConversions=False), conversions=calls)
    assert f["result"] == "fail" and "without call conversion reporting" in f["found"]
    paused = actions_with([A.action("1", "Calls", type="AD_CALL", category="PHONE_CALL_LEAD", status="PAUSED")])
    assert one("G-SET-06", customer_with(callReporting=False), conversions=paused)["result"] == "na"
    by_category = actions_with([A.action("1", "Lead call", type="WEBPAGE", category="PHONE_CALL_LEAD")])
    assert one("G-SET-06", customer_with(callReporting=False), conversions=by_category)["result"] == "fail"
    assert one("G-SET-06", conversions=None)["result"] == "unknown"


def test_optimization_score():
    assert one("G-SET-07")["result"] == "pass" and "43%" in one("G-SET-07", customer_with(optimizationScore=43))["found"]
    assert one("G-SET-07", customer_with(optimizationScore=43))["result"] == "warn"
    assert one("G-SET-07", customer_with(optimizationScore=50))["result"] == "pass"
    assert one("G-SET-07", customer_with(optimizationScore=49))["result"] == "warn"
    assert one("G-SET-07", customer_with(optimizationScore=None))["result"] == "unknown"


# ── Conversion tracking ─────────────────────────────────────────────────────

def conv_read(rows, volumes=None, **answers):
    ans = {r.CONVERSIONS_GAQL: rows, r.CONVERSION_VOLUME_GAQL: volumes if volumes is not None else [A.volume(a["conversionAction"]["id"], 5.0, value=50.0) for a in rows]}
    ans.update(answers)
    return A.a_read(answers=ans)


def test_a_primary_action_is_needed():
    f = one("G-CNV-01")
    assert f["result"] == "pass" and "1 enabled primary conversion action: Submit lead form" in f["found"]
    rows = [A.action(str(i), "Action %d" % i) for i in range(5)]
    assert "5 enabled primary conversion actions: Action 0, Action 1, Action 2 and 2 more" in one("G-CNV-01", conv_read(rows))["found"]
    none = conv_read([A.action("1", primary=False)])
    f = one("G-CNV-01", none)
    assert f["result"] == "fail" and "1 action in the account" in f["found"]
    google_default = conv_read([A.action("1", "Installs", type="ANDROID_INSTALLS_ALL_OTHER_APPS", category="DOWNLOAD")])
    assert one("G-CNV-01", google_default)["result"] == "fail"                                # Google's own default action is not the client's tracking
    assert one("G-CNV-01", conv_read([A.action("1", status="PAUSED")]))["result"] == "fail"
    assert one("G-CNV-01", conv_read([A.action("1", type="AD_CALL", category="PHONE_CALL_LEAD")]))["result"] == "pass"
    assert one("G-CNV-01", conv_read([A.action("1", type="LEAD_FORM_SUBMIT", origin="GOOGLE_HOSTED")]))["result"] == "pass"
    assert one("G-CNV-01", conversions=None)["result"] == "unknown"


def test_conversions_in_the_last_30_days():
    f = one("G-CNV-02")
    assert f["result"] == "pass" and "40 conversions" in f["found"] and "USD 1,000" in f["found"]
    zero = A.a_read(answers={r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 5000, 400, 1000.0, 0.0)]})
    f = one("G-CNV-02", zero)
    assert f["result"] == "fail" and "USD 1,000 was spent" in f["found"]
    tiny = A.a_read(answers={r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 50, 4, 99.0, 0.0)]})
    assert one("G-CNV-02", tiny)["result"] == "na"
    edge = A.a_read(answers={r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 50, 4, 100.0, 0.0)]})
    assert one("G-CNV-02", edge)["result"] == "fail"
    assert one("G-CNV-02", campaigns=None)["result"] == "unknown"
    no_metrics = A.a_read(answers={r.CAMPAIGN_METRICS_GAQL: []})
    assert one("G-CNV-02", no_metrics)["result"] == "na"                                       # nothing spent, nothing to expect
    unread = A.a_read()
    unread["campaigns"] = dict(unread["campaigns"], totals=None)
    assert one("G-CNV-02", unread)["result"] == "unknown"


def test_silent_primary_actions():
    rows = [A.action("1", "Form"), A.action("2", "Chat"), A.action("3", "Calls", type="AD_CALL", category="PHONE_CALL_LEAD")]
    vol = [A.volume("1", 9.0), A.volume("2", 0.0, all_conversions=0.0), A.volume("3", 0.0, all_conversions=0.0)]
    f = one("G-CNV-03", conv_read(rows, vol))
    assert f["result"] == "warn" and f["items"] == ["Chat"] and "1 of 2 primary tag and import actions recorded nothing" in f["found"]             # a call action with no calls is not a broken tag
    assert one("G-CNV-03", conv_read(rows, [A.volume("1", 9.0), A.volume("2", 3.0)]))["result"] == "pass"
    f = one("G-CNV-03", conv_read(rows[:2], [A.volume("1", 0.0, all_conversions=0.0)]))
    assert f["result"] == "fail" and f["items"] == ["Chat", "Form"]          # the read lists actions by name
    secondary_only = conv_read([A.action("1", primary=False)])
    assert one("G-CNV-03", secondary_only)["result"] == "na"
    assert one("G-CNV-03", conv_read(rows[:1], None, **{r.CONVERSION_VOLUME_GAQL: []}))["result"] == "fail"
    unread = A.a_read()
    unread["conversions"] = [dict(a, allConversions=None) for a in unread["conversions"]]
    assert one("G-CNV-03", unread)["result"] == "unknown" and one("G-CNV-03", conversions=None)["result"] == "unknown"
    assert one("G-CNV-03", conv_read([A.action("1", status="PAUSED")], [A.volume("1", 0.0)]))["result"] == "na"


def test_lead_forms_counted_every_time():
    rows = [A.action("1", "Form A", counting="MANY_PER_CLICK"), A.action("2", "Form B"), A.action("3", "CRM meetings", type="UPLOAD_CLICKS", category="BOOK_APPOINTMENT", counting="MANY_PER_CLICK")]
    f = one("G-CNV-04", conv_read(rows))
    assert f["result"] == "warn" and f["items"] == ["Form A"] and "1 of 2 primary lead actions counts every submission" in f["found"]          # an import of separate meetings may count each
    assert one("G-CNV-04", conv_read(rows[1:2]))["result"] == "pass"
    assert one("G-CNV-04", conv_read([A.action("1", category="PURCHASE")]))["result"] == "na"
    ga4 = A.action("1", "GA4 lead", type="GOOGLE_ANALYTICS_4_CUSTOM", counting="MANY_PER_CLICK")
    assert one("G-CNV-04", conv_read([ga4]))["result"] == "warn"
    assert one("G-CNV-04", conv_read([A.action("1", counting="MANY_PER_CLICK", primary=False)]))["result"] == "na"
    assert one("G-CNV-04", conversions=None)["result"] == "unknown"


def test_purchases_counted_once():
    rows = [A.action("1", "Purchase", category="PURCHASE", counting="ONE_PER_CLICK"), A.action("2", "Purchase 2", category="PURCHASE", counting="MANY_PER_CLICK")]
    f = one("G-CNV-05", conv_read(rows))
    assert f["result"] == "warn" and f["items"] == ["Purchase"] and "1 of 2 primary purchase actions counts one per click" in f["found"]
    assert one("G-CNV-05", conv_read(rows[1:]))["result"] == "pass"
    assert one("G-CNV-05")["result"] == "na" and one("G-CNV-05", conversions=None)["result"] == "unknown"


def test_a_tag_and_a_ga4_import_of_the_same_kind_of_event_is_double_counting():
    tag = A.action("1", "Tag form")
    ga4 = A.action("2", "GA4 form", type="GOOGLE_ANALYTICS_4_CUSTOM")
    f = one("G-CNV-06", conv_read([tag, ga4]))
    assert f["result"] == "warn" and sorted(f["items"]) == ["GA4 form", "Tag form"] and "Counted by both a tag and a GA4 import: submit lead form" in f["found"]
    other = A.action("3", "GA4 purchase", type="GOOGLE_ANALYTICS_4_PURCHASE", category="PURCHASE")
    assert one("G-CNV-06", conv_read([tag, other]))["result"] == "pass"
    assert one("G-CNV-06", conv_read([tag, A.action("2", "GA4 form", type="GOOGLE_ANALYTICS_4_CUSTOM", primary=False)]))["result"] == "pass"
    assert one("G-CNV-06", conv_read([tag, A.action("2", "GA4 form", type="GOOGLE_ANALYTICS_4_CUSTOM", included=False)]))["result"] == "pass"
    assert one("G-CNV-06", conv_read([A.action("1", "Calls", type="AD_CALL", category="PHONE_CALL_LEAD")]))["result"] == "na"
    assert one("G-CNV-06", conversions=None)["result"] == "unknown"


def test_value_bidding_needs_values():
    value_camp = A.campaign("10", "Search | Implants", bidding="MAXIMIZE_CONVERSION_VALUE")
    roas_camp = A.campaign("11", "PMax", ctype="PERFORMANCE_MAX", bidding="TARGET_ROAS", target_roas=3.0)
    rows = [A.action("1", "Sale", category="PURCHASE")]
    with_value = conv_read(rows, [A.volume("1", 5.0, value=250.0)], **{r.CAMPAIGNS_GAQL: [value_camp, roas_camp]})
    f = one("G-CNV-07", with_value)
    assert f["result"] == "pass" and "Campaigns bidding on conversion value: 2" in f["found"] and "250" in f["found"]
    no_value = conv_read(rows, [A.volume("1", 5.0, value=0.0)], **{r.CAMPAIGNS_GAQL: [value_camp]})
    f = one("G-CNV-07", no_value)
    assert f["result"] == "fail" and f["items"] == ["Search | Implants"]
    assert one("G-CNV-07")["result"] == "na"
    assert one("G-CNV-07", campaigns=None)["result"] == "unknown" and one("G-CNV-07", with_value, conversions=None)["result"] == "unknown"
    unread = dict(with_value)
    unread["conversions"] = [dict(a, value=None) for a in with_value["conversions"]]
    assert one("G-CNV-07", unread)["result"] == "unknown"
    secondary_value = conv_read([A.action("1", "Sale", category="PURCHASE"), A.action("2", "Other", primary=False)], [A.volume("1", 5.0, value=0.0), A.volume("2", 5.0, value=999.0)],
                                **{r.CAMPAIGNS_GAQL: [value_camp]})
    assert one("G-CNV-07", secondary_value)["result"] == "fail"                                      # only primary actions steer bidding


def test_last_click_attribution():
    rows = [A.action("1", "A"), A.action("2", "B", attribution="GOOGLE_ADS_LAST_CLICK"), A.action("3", "C", attribution="EXTERNAL"), A.action("4", "D", attribution="")]
    f = one("G-CNV-08", conv_read(rows))
    assert f["result"] == "warn" and f["items"] == ["B", "C"] and "2 of 3 primary actions use last-click attribution" in f["found"]
    assert one("G-CNV-08")["result"] == "pass"
    assert one("G-CNV-08", conv_read([A.action("1", attribution="")]))["result"] == "na"
    assert one("G-CNV-08", conversions=None)["result"] == "unknown"


def test_click_windows():
    rows = [A.action("1", "Short", click="7"), A.action("2", "Edge", click="30"), A.action("3", "Long", click="90"), A.action("4", "Unset", click="0"),
            A.action("5", "Calls", type="AD_CALL", category="PHONE_CALL_LEAD", click="1")]
    f = one("G-CNV-09", conv_read(rows))
    assert f["result"] == "warn" and f["items"] == ["Short (7 days)"]
    assert one("G-CNV-09", conv_read(rows[1:4]))["result"] == "pass"
    assert one("G-CNV-09", conv_read(rows[4:]))["result"] == "na" and one("G-CNV-09", conversions=None)["result"] == "unknown"


def test_too_many_primary_actions():
    many = [A.action(str(i), "Action %d" % i) for i in range(7)]
    f = one("G-CNV-10", conv_read(many))
    assert f["result"] == "warn" and "7 primary" in f["found"] and len(f["items"]) == 7
    assert one("G-CNV-10", conv_read(many[:6]))["result"] == "pass"
    calls = [A.action(str(i), "Call %d" % i, type="AD_CALL", category="PHONE_CALL_LEAD") for i in range(40)]
    assert one("G-CNV-10", conv_read(calls))["result"] == "pass"                                     # one call action per location is normal
    assert one("G-CNV-10", conversions=None)["result"] == "unknown"


def test_ga4_is_seen_only_through_its_imports():
    ga4 = A.action("2", "GA4 form", type="GOOGLE_ANALYTICS_4_CUSTOM", primary=False)
    f = one("G-CNV-11", conv_read([A.action("1"), ga4]))
    assert f["result"] == "pass" and "imported from GA4: 1," in f["found"]
    f = one("G-CNV-11")
    assert f["result"] == "unknown" and "does not show the GA4 link" in f["found"]
    assert one("G-CNV-11", conv_read([A.action("1"), A.action("2", "GA4 form", type="GOOGLE_ANALYTICS_4_PURCHASE", status="PAUSED")]))["result"] == "unknown"
    assert one("G-CNV-11", conversions=None)["result"] == "unknown"


def test_wording_agrees_with_the_count():
    assert c.plural(1, "action") == "1 action" and c.plural(0, "action") == "0 actions" and c.plural(3, "action") == "3 actions"
    assert c.verb(1, "count") == "counts" and c.verb(2, "count") == "count" and c.verb(0, "use") == "use"
    only = conv_read([A.action("1", "Only form")])
    assert one("G-CNV-04", only)["found"] == "Checked 1 primary lead action: none counts every submission."
    assert one("G-CNV-09", only)["found"] == "Checked 1 primary tag and import action: all look back 30 days or more."
    two = conv_read([A.action("1", "A", counting="MANY_PER_CLICK"), A.action("2", "B", counting="MANY_PER_CLICK")])
    assert one("G-CNV-04", two)["found"] == "2 of 2 primary lead actions count every submission."
    three = actions_with([A.action("1", "Calls", type="AD_CALL", category="PHONE_CALL_LEAD"), A.action("2", "More calls", type="AD_CALL", category="PHONE_CALL_LEAD")])
    assert "(2 call conversion actions)" in one("G-SET-06", conversions=three)["found"]


# ── Links and audiences ─────────────────────────────────────────────────────

def camps(*rows, metrics=(), criteria=None):
    ans = {r.CAMPAIGNS_GAQL: list(rows), r.CAMPAIGN_METRICS_GAQL: list(metrics)}
    if criteria is not None:
        ans[r.CAMPAIGN_CRITERIA_GAQL] = criteria
    return A.a_read(answers=ans)


def test_shopping_campaigns_need_their_merchant_center():
    assert one("G-LNK-01")["result"] == "na"
    shop = A.campaign("20", "Shopping | All", ctype="SHOPPING", merchant="9282495")
    pmax_retail = A.campaign("21", "PMax | Retail", ctype="PERFORMANCE_MAX", merchant="9282495")
    pmax_leads = A.campaign("22", "PMax | Leads", ctype="PERFORMANCE_MAX")
    linked = camps(shop, pmax_retail, pmax_leads)
    linked_links = {"counts": {"MERCHANT_CENTER": 1}, "merchantCenter": ["9282495"]}
    f = one("G-LNK-01", linked, links=linked_links)
    assert f["result"] == "pass" and "2 campaigns serve products" in f["found"]                    # the lead-gen PMax campaign is not retail
    f = one("G-LNK-01", linked, links={"counts": {}, "merchantCenter": []})
    assert f["result"] == "fail" and f["items"] == ["PMax | Retail", "Shopping | All"] and "no Merchant Center account is linked" in f["found"]
    f = one("G-LNK-01", linked, links={"counts": {"MERCHANT_CENTER": 1}, "merchantCenter": ["77"]})
    assert f["result"] == "fail" and "not linked (linked: 77)" in f["found"] and f["items"][0] == "PMax | Retail (Merchant Center 9282495)"
    assert one("G-LNK-01", linked, links={"counts": {"MERCHANT_CENTER": 1}, "merchantCenter": None})["result"] == "pass"     # ids unreadable: the link is enough
    assert one("G-LNK-01", linked, links=None)["result"] == "unknown" and one("G-LNK-01", campaigns=None)["result"] == "unknown"
    assert one("G-LNK-01", camps(A.campaign("20", "Shopping", ctype="SHOPPING")), links=linked_links)["result"] == "pass"       # no merchant named: just the link


def lists_read(*rows):
    return A.a_read(answers={r.LISTS_GAQL: list(rows)})


def test_audience_lists_need_members():
    assert one("G-LNK-02")["result"] == "pass"
    assert one("G-LNK-02", lists_read())["result"] == "warn" and "no audience lists" in one("G-LNK-02", lists_read())["found"]
    closed = lists_read(A.user_list(status="CLOSED"))
    assert "all closed" in one("G-LNK-02", closed)["found"] and "1 list," in one("G-LNK-02", closed)["found"]
    empty = lists_read(A.user_list("Empty A", lid="1"), A.user_list("Empty B", lid="2"))
    f = one("G-LNK-02", empty)
    assert f["result"] == "warn" and "None of the 2 open" in f["found"] and f["items"] == ["Empty A", "Empty B"]
    some = lists_read(A.user_list("Empty A", lid="1"), A.user_list("Full", search=5000, display=5000, lid="2"))
    assert one("G-LNK-02", some)["result"] == "pass" and "1 of 2 open" in one("G-LNK-02", some)["found"]
    assert one("G-LNK-02", lists=None)["result"] == "unknown"


def test_lists_under_the_search_minimum():
    assert one("G-LNK-03")["result"] == "pass" and "1,000-member" in one("G-LNK-03")["found"]
    small = lists_read(A.user_list("Small", search=300, display=300, lid="1"), A.user_list("Full", search=5000, display=5000, lid="2"))
    f = one("G-LNK-03", small)
    assert f["result"] == "warn" and "1 open list is under the Search minimum of 1,000" in f["found"] and f["items"] == ["Small (300)"]
    two = lists_read(A.user_list("A", search=1, display=1, lid="1"), A.user_list("B", search=2, display=2, lid="2"), A.user_list("Full", search=5000, display=5000, lid="3"))
    assert "2 open lists are under" in one("G-LNK-03", two)["found"]
    assert one("G-LNK-03", lists_read(A.user_list("Empty")))["result"] == "na" and one("G-LNK-03", lists=None)["result"] == "unknown"


# ── Structure and bidding ───────────────────────────────────────────────────

def test_every_campaign_needs_a_location():
    f = one("G-STR-01")
    assert f["result"] == "pass" and "All 1 enabled campaign target" in f["found"]
    bare = camps(A.campaign("10", "Has location"), A.campaign("11", "No location"), A.campaign("12", "Radius only"),
                 criteria=[A.criterion("10", "LOCATION"), A.criterion("12", "PROXIMITY"), A.criterion("11", "LANGUAGE"), A.criterion("11", "LOCATION", True)])
    f = one("G-STR-01", bare)
    assert f["result"] == "fail" and f["items"] == ["No location"] and "Campaigns with no location target: 1 of 3 enabled campaigns." in f["found"]
    local = camps(A.campaign("10", "Local", ctype="LOCAL_SERVICES"), A.campaign("11", "Demand Gen", ctype="DEMAND_GEN"), criteria=[])
    assert one("G-STR-01", local)["result"] == "na"                                                  # their locations are not on the campaign
    mixed = camps(A.campaign("10", "Search"), A.campaign("11", "Demand Gen", ctype="DEMAND_GEN"), criteria=[])
    f = one("G-STR-01", mixed)
    assert f["result"] == "fail" and f["items"] == ["Search"] and "Campaigns with no location target: 1 of 1 enabled campaign." in f["found"]
    assert one("G-STR-01", campaigns=None)["result"] == "unknown"
    unread = A.a_read()
    unread["campaigns"]["campaigns"][0]["targeting"] = None
    assert one("G-STR-01", unread)["result"] == "unknown"


def test_search_campaigns_on_the_display_network():
    assert one("G-STR-02")["result"] == "pass"
    on = camps(A.campaign("10", "Search | A", display=True), A.campaign("11", "Search | B"), A.campaign("12", "PMax", ctype="PERFORMANCE_MAX", display=True))
    f = one("G-STR-02", on)
    assert f["result"] == "fail" and f["items"] == ["Search | A"] and "1 of 2 Search campaigns opt into the Display Network." in f["found"]
    net = {"cost": {"SEARCH": 90.0, "CONTENT": 10.0}, "total": 100.0, "conversions": {}}
    assert "10% of Search spend in the last 30 days ran on the Display Network." in one("G-STR-02", on, network=net)["found"]
    assert one("G-STR-02", camps(A.campaign("12", "PMax", ctype="PERFORMANCE_MAX", display=True)))["result"] == "na"
    assert one("G-STR-02", campaigns=None)["result"] == "unknown"


def test_search_partners_are_judged_on_cost_per_conversion():
    def net(cp, convp, cs=900.0, convs=30.0):
        return {"cost": {"SEARCH": cs, "SEARCH_PARTNERS": cp}, "total": cs + cp, "conversions": {"SEARCH": convs, "SEARCH_PARTNERS": convp}}
    assert one("G-STR-03", network=net(10.0, 0.0))["result"] == "pass" and "1%" in one("G-STR-03", network=net(10.0, 0.0))["found"]       # a small share
    f = one("G-STR-03", network=net(100.0, 0.0))
    assert f["result"] == "warn" and "10%" in f["found"] and "no conversions" in f["found"]
    f = one("G-STR-03", network=net(100.0, 1.0))
    assert f["result"] == "warn" and "USD 100 per conversion against USD 30" in f["found"]
    assert one("G-STR-03", network=net(100.0, 3.0))["result"] == "pass"                           # 33 against 30 is in line
    assert one("G-STR-03", network=net(100.0, 2.0))["result"] == "warn"                           # 50 against 30 is 1.67 times
    assert one("G-STR-03", network={"cost": {}, "total": 0.0, "conversions": {}})["result"] == "na"
    assert one("G-STR-03", network=None)["result"] == "unknown"
    # exactly 5% of 950 is 47.5: at the line it counts, a hair under it does not
    assert one("G-STR-03", network={"cost": {"SEARCH": 902.5, "SEARCH_PARTNERS": 47.5}, "total": 950.0, "conversions": {"SEARCH": 30.0, "SEARCH_PARTNERS": 0.0}})["result"] == "warn"
    assert one("G-STR-03", network={"cost": {"SEARCH": 903.0, "SEARCH_PARTNERS": 47.0}, "total": 950.0, "conversions": {"SEARCH": 30.0, "SEARCH_PARTNERS": 0.0}})["result"] == "pass"


def test_budget_limited_campaigns_that_convert():
    assert one("G-STR-04")["result"] == "pass"
    rows = [A.campaign("10", "Limited", primary="LIMITED", reasons=("BUDGET_CONSTRAINED",)), A.campaign("11", "Lost share"), A.campaign("12", "Limited no conv", reasons=("BUDGET_CONSTRAINED",)),
            A.campaign("13", "Fine")]
    m = [A.campaign_metrics("10", cost=500.0, conversions=5.0), A.campaign_metrics("11", cost=900.0, conversions=9.0, budget_lost=0.2),
         A.campaign_metrics("12", cost=100.0, conversions=0.0), A.campaign_metrics("13", cost=10.0, conversions=2.0, budget_lost=0.19)]
    f = one("G-STR-04", camps(*rows, metrics=m))
    assert f["result"] == "warn" and "2 campaigns are converting and held back by budget" in f["found"]
    assert f["items"] == ["Lost share (20% of impressions lost to budget)", "Limited (budget limited)"]                    # biggest spend first
    assert one("G-STR-04", camps(A.campaign("10", ctype="VIDEO", reasons=("BUDGET_CONSTRAINED",)), metrics=[A.campaign_metrics("10", conversions=5.0)]))["result"] == "na"
    assert one("G-STR-04", campaigns=None)["result"] == "unknown"


def test_campaign_status_problems():
    assert one("G-STR-05")["result"] == "pass"
    rows = [A.campaign("10", "Broken", primary="MISCONFIGURED"), A.campaign("11", "Policy", primary="LIMITED", reasons=("HAS_ADS_LIMITED_BY_POLICY", "BUDGET_CONSTRAINED")),
            A.campaign("12", "Budget only", primary="LIMITED", reasons=("BUDGET_CONSTRAINED",))]
    f = one("G-STR-05", camps(*rows))
    assert f["result"] == "fail" and f["severity"] == "high" and "1 campaigns are misconfigured" in f["found"] and f["items"] == ["Broken (misconfigured)", "Policy (has ads limited by policy)"]
    f = one("G-STR-05", camps(*rows[1:]))
    assert f["result"] == "warn" and f["severity"] == "medium" and "1 campaign is limited by policy" in f["found"] and f["items"] == ["Policy (has ads limited by policy)"]
    both = camps(A.campaign("10", "A", primary="NOT_ELIGIBLE", reasons=("NO_AD_GROUP_ADS",)), A.campaign("11", "B", primary="MISCONFIGURED"))
    assert "misconfigured or not eligible" in one("G-STR-05", both)["found"] and one("G-STR-05", both)["items"] == ["A (no ad group ads)", "B (misconfigured)"]
    assert one("G-STR-05", campaigns=None)["result"] == "unknown"


def test_campaigns_still_learning():
    assert one("G-STR-06")["result"] == "pass"
    rows = [A.campaign("10", "Learning", primary="LEARNING"), A.campaign("11", "Bidding", primary="ELIGIBLE", reasons=("BIDDING_STRATEGY_LEARNING",)), A.campaign("12", "Fine")]
    f = one("G-STR-06", camps(*rows))
    assert f["result"] == "warn" and "2 campaigns are in a learning period" in f["found"] and f["items"] == ["Bidding", "Learning"]
    assert "1 campaign is in" in one("G-STR-06", camps(rows[0]))["found"] and one("G-STR-06", campaigns=None)["result"] == "unknown"


def test_smart_bidding_needs_conversions():
    rows = [A.campaign("10", "CPA thin", bidding="TARGET_CPA", target_cpa="40000000"), A.campaign("11", "CPA ok", bidding="TARGET_CPA", target_cpa="40000000"),
            A.campaign("12", "ROAS thin", bidding="TARGET_ROAS", target_roas=3.0), A.campaign("13", "MaxConv thin"), A.campaign("14", "MaxConv ok"),
            A.campaign("15", "Manual", bidding="MANUAL_CPC"), A.campaign("16", "Maxconv with target", bidding="MAXIMIZE_CONVERSIONS", target_cpa="20000000")]
    m = [A.campaign_metrics("10", cost=300.0, conversions=29.0), A.campaign_metrics("11", cost=300.0, conversions=30.0), A.campaign_metrics("12", cost=200.0, conversions=49.0),
         A.campaign_metrics("13", cost=100.0, conversions=14.0), A.campaign_metrics("14", cost=100.0, conversions=15.0), A.campaign_metrics("16", cost=50.0, conversions=29.0)]
    f = one("G-STR-07", camps(*rows, metrics=m))
    assert f["result"] == "warn" and "4 of 6 Smart Bidding campaigns" in f["found"]
    assert f["items"] == ["CPA thin (29 conversions, about 30 suggested)", "ROAS thin (49 conversions, about 50 suggested)", "MaxConv thin (14 conversions, about 15 suggested)",
                          "Maxconv with target (29 conversions, about 30 suggested)"]
    assert one("G-STR-07", camps(rows[1], rows[4], metrics=[m[1], m[4]]))["result"] == "pass"
    assert one("G-STR-07", camps(rows[5], metrics=[]))["result"] == "na"
    idle = camps(A.campaign("10", "Idle"), metrics=[])
    assert one("G-STR-07", idle)["result"] == "pass"                                              # nothing spent, so nothing to learn from yet
    video = camps(A.campaign("10", "Video", ctype="VIDEO"), metrics=[A.campaign_metrics("10", cost=50.0, conversions=0.0)])
    assert one("G-STR-07", video)["result"] == "na"
    unread = A.a_read()
    unread["campaigns"]["campaigns"][0]["metrics"] = None
    assert one("G-STR-07", unread)["result"] == "unknown" and one("G-STR-07", campaigns=None)["result"] == "unknown"


def test_click_based_bidding_with_conversions_on_hand():
    rows = [A.campaign("10", "Manual ready", bidding="MANUAL_CPC"), A.campaign("11", "Clicks few", bidding="TARGET_SPEND"), A.campaign("12", "Smart", bidding="TARGET_CPA"),
            A.campaign("13", "Display manual", ctype="DISPLAY", bidding="MANUAL_CPC")]
    m = [A.campaign_metrics("10", cost=400.0, conversions=15.0), A.campaign_metrics("11", cost=100.0, conversions=14.0), A.campaign_metrics("12", conversions=99.0),
         A.campaign_metrics("13", conversions=50.0)]
    f = one("G-STR-08", camps(*rows, metrics=m))
    assert f["result"] == "warn" and "1 of 2 Search campaigns on manual or click-based bidding already record 15 or more" in f["found"]
    assert f["items"] == ["Manual ready (manual cpc, 15 conversions)"]
    assert one("G-STR-08", camps(rows[1], metrics=[m[1]]))["result"] == "pass" and "none with enough" in one("G-STR-08", camps(rows[1], metrics=[m[1]]))["found"]
    assert one("G-STR-08")["result"] == "pass" and "No Search campaign uses" in one("G-STR-08")["found"]
    unread = camps(rows[0])
    unread["campaigns"]["campaigns"][0]["metrics"] = None
    assert one("G-STR-08", unread)["result"] == "unknown" and one("G-STR-08", campaigns=None)["result"] == "unknown"


def test_campaigns_that_spend_and_never_convert():
    assert one("G-STR-09")["result"] == "pass"
    rows = [A.campaign("10", "Dead big"), A.campaign("11", "Dead small"), A.campaign("12", "Few clicks"), A.campaign("13", "Converting"), A.campaign("14", "Video", ctype="VIDEO")]
    m = [A.campaign_metrics("10", clicks=300, cost=600.0, conversions=0.0), A.campaign_metrics("11", clicks=50, cost=100.0, conversions=0.0), A.campaign_metrics("12", clicks=49, cost=90.0, conversions=0.0),
         A.campaign_metrics("13", clicks=500, cost=1200.0, conversions=40.0), A.campaign_metrics("14", clicks=900, cost=300.0, conversions=0.0)]
    f = one("G-STR-09", camps(*rows, metrics=m))
    assert f["result"] == "fail" and "2 campaigns have 50 or more clicks and no conversion in 30 days, USD 700 (31% of spend)." in f["found"]
    assert f["items"] == ["Dead big (USD 600, 300 clicks)", "Dead small (USD 100, 50 clicks)"]
    big = [A.campaign_metrics("10", clicks=300, cost=600.0, conversions=0.0), A.campaign_metrics("13", clicks=500, cost=1800.0, conversions=40.0)]
    assert one("G-STR-09", camps(rows[0], rows[3], metrics=big))["result"] == "fail"              # 25% exactly of 2,400 is 600
    small = [A.campaign_metrics("10", clicks=300, cost=598.0, conversions=0.0), A.campaign_metrics("13", clicks=500, cost=1802.0, conversions=40.0)]
    f = one("G-STR-09", camps(rows[0], rows[3], metrics=small))
    assert f["result"] == "warn" and "(25% of spend)" in f["found"]                              # shown as 25%, but 24.9% is not 25%
    assert one("G-STR-09", campaigns=None)["result"] == "unknown"


def test_a_big_share_of_spend_with_no_conversions_fails():
    rows = [A.campaign("10", "Dead"), A.campaign("13", "Converting")]
    m = [A.campaign_metrics("10", clicks=300, cost=900.0, conversions=0.0), A.campaign_metrics("13", clicks=500, cost=2700.0, conversions=40.0)]
    f = one("G-STR-09", camps(*rows, metrics=m))
    assert f["result"] == "fail" and "1 campaign has 50 or more clicks" in f["found"] and "(25% of spend)" in f["found"]


def test_search_campaigns_losing_reach_to_rank():
    assert one("G-STR-10")["result"] == "pass"
    rows = [A.campaign("10", "Big lost"), A.campaign("11", "Small lost"), A.campaign("12", "Big fine"), A.campaign("13", "Edge")]
    m = [A.campaign_metrics("10", cost=600.0, rank_lost=0.55), A.campaign_metrics("11", cost=20.0, rank_lost=0.9), A.campaign_metrics("12", cost=300.0, rank_lost=0.39),
         A.campaign_metrics("13", cost=80.0, rank_lost=0.4)]
    f = one("G-STR-10", camps(*rows, metrics=m))
    assert f["result"] == "warn" and "2 big Search campaigns are losing 40% or more" in f["found"]
    assert f["items"] == ["Big lost (55% lost to rank)", "Edge (40% lost to rank)"]                  # 20 of 1,000 is under 5% of spend, so it does not count
    assert one("G-STR-10", camps(A.campaign("10", ctype="PERFORMANCE_MAX"), metrics=[]))["result"] == "na"
    unread = A.a_read()
    unread["campaigns"]["campaigns"][0]["metrics"] = None
    assert one("G-STR-10", unread)["result"] == "unknown" and one("G-STR-10", campaigns=None)["result"] == "unknown"


def test_paused_campaign_clutter():
    def read(enabled, paused):
        rows = [A.campaign(str(i), "On %d" % i) for i in range(enabled)] + [A.campaign(str(1000 + i), "Off %d" % i, status="PAUSED") for i in range(paused)]
        return camps(*rows)
    assert one("G-STR-11", read(2, 49))["result"] == "pass"
    f = one("G-STR-11", read(2, 50))
    assert f["result"] == "warn" and f["found"] == "50 paused campaigns against 2 enabled."
    assert one("G-STR-11", read(30, 59))["result"] == "pass" and one("G-STR-11", read(30, 60))["result"] == "warn"
    assert one("G-STR-11", read(1, 1))["found"] == "1 paused campaign and 1 enabled."
    assert one("G-STR-11", campaigns=None)["result"] == "unknown"
