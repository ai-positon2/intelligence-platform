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
