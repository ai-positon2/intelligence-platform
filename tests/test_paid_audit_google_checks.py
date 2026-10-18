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
    assert "the check itself failed" not in out[0]["found"], out[0]["found"]       # a check handles every read itself; the runner's net is for bugs
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
    f = one("G-SET-01", customer_with(status="UNKNOWN"))
    assert f["result"] == "unknown" and "(it said UNKNOWN)" in f["found"]
    assert "(it said nothing)" in one("G-SET-01", customer_with(status=""))["found"]
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
    both_off = one("G-SET-05", customer_with(enhancedLeads=False, dataTerms=False))
    assert both_off["result"] == "warn" and both_off["found"] == "Enhanced conversions for leads are off."
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
    by_category = actions_with([A.action("1", "Click on the number", type="WEBPAGE", category="PHONE_CALL_LEAD")])
    assert one("G-SET-06", customer_with(callReporting=False), conversions=by_category)["result"] == "na"          # a tag on a click-to-call number needs no call reporting
    for kind in ("AD_CALL", "CLICK_TO_CALL", "WEBSITE_CALL"):
        assert one("G-SET-06", customer_with(callReporting=False), conversions=actions_with([A.action("1", "Calls", type=kind, category="PHONE_CALL_LEAD")]))["result"] == "fail", kind
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
    assert one("G-CNV-01", conv_read(rows[:3]))["found"] == "3 enabled primary conversion actions: Action 0, Action 1, Action 2."
    none = conv_read([A.action("1", primary=False)])
    f = one("G-CNV-01", none)
    assert f["result"] == "fail" and "1 action in the account" in f["found"]
    google_default = conv_read([A.action("1", "Installs", type="ANDROID_INSTALLS_ALL_OTHER_APPS", category="DOWNLOAD")])
    assert one("G-CNV-01", google_default)["result"] == "fail"                                # Google's own default action is not the client's tracking
    for kind in ("GOOGLE_HOSTED", "UNKNOWN", "SMART_CAMPAIGN_TRACKED_CALLS", "SMART_CAMPAIGN_AD_CLICKS_TO_CALL"):
        assert one("G-CNV-01", conv_read([A.action("1", "Default", type=kind)]))["result"] == "fail", kind
    for kind in ("GOOGLE_PLAY_DOWNLOAD", "FIREBASE_ANDROID_FIRST_OPEN", "FIREBASE_IOS_CUSTOM", "THIRD_PARTY_APP_ANALYTICS_IOS_PURCHASE", "STORE_SALES_DIRECT_UPLOAD", "STORE_VISITS",
                 "WEBPAGE_CODELESS", "SALESFORCE", "GOOGLE_ANALYTICS_4_PURCHASE"):
        assert one("G-CNV-01", conv_read([A.action("1", "Own", type=kind)]))["result"] == "pass", kind          # an app or store account has its own measurement
    assert one("G-CNV-01", conv_read([A.action("1", status="PAUSED")]))["result"] == "fail"
    assert one("G-CNV-01", conv_read([A.action("1", type="AD_CALL", category="PHONE_CALL_LEAD")]))["result"] == "pass"
    assert one("G-CNV-01", conv_read([A.action("1", type="LEAD_FORM_SUBMIT", origin="GOOGLE_HOSTED")]))["result"] == "pass"
    assert one("G-CNV-01", conversions=None)["result"] == "unknown"


def test_conversions_in_the_last_30_days():
    f = one("G-CNV-02")
    assert f["result"] == "pass" and "40 conversions" in f["found"] and "USD 1,000" in f["found"]
    part = A.a_read(answers={r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 5000, 400, 1000.0, 0.5)]})
    assert one("G-CNV-02", part)["result"] == "pass"                                        # half a conversion is still a conversion
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
    upload = A.action("4", "CRM upload", type="UPLOAD_CLICKS")
    f = one("G-CNV-06", conv_read([tag, ga4, upload]))
    assert sorted(f["items"]) == ["GA4 form", "Tag form"]                                      # an upload of the same category is not one of the two sources
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
    half = conv_read(rows, [A.volume("1", 5.0, value=0.5)], **{r.CAMPAIGNS_GAQL: [value_camp]})
    assert one("G-CNV-07", half)["result"] == "pass"                                           # any value is something to optimise
    assert one("G-CNV-07", campaigns=None)["result"] == "unknown" and one("G-CNV-07", with_value, conversions=None)["result"] == "unknown"
    unread = dict(with_value)
    unread["conversions"] = [dict(a, value=None) for a in with_value["conversions"]]
    assert one("G-CNV-07", unread)["result"] == "unknown"
    secondary_value = conv_read([A.action("1", "Sale", category="PURCHASE"), A.action("2", "Other", primary=False)], [A.volume("1", 5.0, value=0.0), A.volume("2", 5.0, value=999.0)],
                                **{r.CAMPAIGNS_GAQL: [value_camp]})
    assert one("G-CNV-07", secondary_value)["result"] == "fail"                                      # only primary actions steer bidding


def test_last_click_attribution():
    rows = [A.action("1", "A"), A.action("2", "B", attribution="GOOGLE_ADS_LAST_CLICK"), A.action("3", "C", attribution="LAST_CLICK"), A.action("4", "D", attribution=""),
            A.action("5", "GA4", attribution="EXTERNAL", type="GOOGLE_ANALYTICS_4_CUSTOM")]
    f = one("G-CNV-08", conv_read(rows))
    assert f["result"] == "warn" and f["items"] == ["B", "C"] and "2 of 3 primary actions use last-click attribution" in f["found"]          # an import's model is set in GA4, not here
    ga4_only = conv_read([A.action("5", "GA4", attribution="EXTERNAL", type="GOOGLE_ANALYTICS_4_CUSTOM")])
    assert one("G-CNV-08", ga4_only)["result"] == "na" and "set in Google Ads" in one("G-CNV-08", ga4_only)["found"]
    assert one("G-CNV-08")["result"] == "pass"
    assert one("G-CNV-08", conv_read([A.action("1", attribution="")]))["result"] == "na"
    assert one("G-CNV-08", conversions=None)["result"] == "unknown"


def test_click_windows():
    one_day = conv_read([A.action("1", "One day", click="1")])
    assert one("G-CNV-09", one_day)["result"] == "warn" and one("G-CNV-09", one_day)["items"] == ["One day (1 days)"]
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
    assert one("G-LNK-02", closed)["result"] == "warn" and "all closed" in one("G-LNK-02", closed)["found"] and "1 list," in one("G-LNK-02", closed)["found"]
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
    assert f["result"] == "warn" and "1 open list is under the Search minimum of 1,000" in f["found"] and f["items"] == ["Small (300 for Search)"]
    two = lists_read(A.user_list("A", search=1, display=1, lid="1"), A.user_list("B", search=2, display=2, lid="2"), A.user_list("Full", search=5000, display=5000, lid="3"))
    assert "2 open lists are under" in one("G-LNK-03", two)["found"]
    assert one("G-LNK-03", lists_read(A.user_list("Empty")))["result"] == "na" and one("G-LNK-03", lists=None)["result"] == "unknown"


# ── Structure and bidding ───────────────────────────────────────────────────

def test_every_campaign_needs_a_location():
    f = one("G-STR-01")
    assert f["result"] == "pass" and "Checked 1 enabled campaign: all target at least one location or radius." == f["found"]
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
    none_spent = {"cost": {}, "total": 0.0, "conversions": {}}
    assert "Search spend" not in one("G-STR-02", on, network=none_spent)["found"]                       # no spend, no share to quote
    no_content = {"cost": {"SEARCH": 100.0}, "total": 100.0, "conversions": {}}
    assert "0% of Search spend in the last 30 days ran on the Display Network." in one("G-STR-02", on, network=no_content)["found"]
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
    assert one("G-STR-03", network=net(90.0, 2.0))["result"] == "warn"                            # 45 against 30 is exactly 1.5 times: at the line it counts
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
    unread = A.a_read(fail={r.CAMPAIGN_METRICS_GAQL})
    f = one("G-STR-04", unread)
    assert f["result"] == "unknown" and "numbers could not be read" in f["found"]               # Google's own budget reason does not stand in for numbers that were not read
    flagged = A.a_read(answers={r.CAMPAIGNS_GAQL: [A.campaign("10", reasons=("BUDGET_CONSTRAINED",), primary="LIMITED")]}, fail={r.CAMPAIGN_METRICS_GAQL})
    assert one("G-STR-04", flagged)["result"] == "unknown"


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
    bare_roas = A.campaign("20", "Bare ROAS", bidding="TARGET_ROAS")                                       # no target set: still needs 50
    value_target = A.campaign("21", "Value with target", bidding="MAXIMIZE_CONVERSION_VALUE", target_roas=2.0)
    g = one("G-STR-07", camps(bare_roas, value_target, metrics=[A.campaign_metrics("20", cost=100.0, conversions=49.0), A.campaign_metrics("21", cost=100.0, conversions=49.0)]))
    assert g["items"] == ["Bare ROAS (49 conversions, about 50 suggested)", "Value with target (49 conversions, about 50 suggested)"]
    one_conv = one("G-STR-07", camps(A.campaign("23", "One"), metrics=[A.campaign_metrics("23", cost=50.0, conversions=1.0)]))
    assert one_conv["items"] == ["One (1 conversion, about 15 suggested)"]
    g = one("G-STR-07", camps(A.campaign("22", "Cents"), metrics=[A.campaign_metrics("22", cost=0.5, conversions=0.0)]))
    assert g["result"] == "warn" and g["items"] == ["Cents (0 conversions, about 15 suggested)"]            # any spend counts, even under 1
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
    rows = [A.campaign("10", "Big lost"), A.campaign("11", "Small lost"), A.campaign("12", "Big fine"), A.campaign("13", "Edge"), A.campaign("14", "Five percent"),
            A.campaign("15", "Just under")]
    m = [A.campaign_metrics("10", cost=600.0, rank_lost=0.55), A.campaign_metrics("11", cost=20.0, rank_lost=0.9), A.campaign_metrics("12", cost=200.0, rank_lost=0.39),
         A.campaign_metrics("13", cost=80.0, rank_lost=0.4), A.campaign_metrics("14", cost=50.0, rank_lost=0.6), A.campaign_metrics("15", cost=49.9, rank_lost=0.6)]
    m[0] = A.campaign_metrics("10", cost=600.1, rank_lost=0.55)                                         # the total is exactly 1,000
    f = one("G-STR-10", camps(*rows, metrics=m))
    assert f["result"] == "warn" and "3 big Search campaigns are losing 40% or more" in f["found"]
    assert f["items"] == ["Big lost (55% lost to rank)", "Edge (40% lost to rank)", "Five percent (60% lost to rank)"]   # 20 and 49.9 of 1,000 are under 5% of spend
    single = camps(A.campaign("10", "Only one"), metrics=[A.campaign_metrics("10", cost=600.0, rank_lost=0.55)])
    assert one("G-STR-10", single)["found"] == "1 big Search campaign is losing 40% or more of impressions to rank."
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
    assert one("G-STR-11", read(0, 0))["found"] == "0 paused campaigns and 0 enabled."
    assert one("G-STR-11", campaigns=None)["result"] == "unknown"


# ── Keywords and search terms ───────────────────────────────────────────────

def kw_read(keywords, metrics=(), campaigns=None, **answers):
    ans = {r.KEYWORDS_GAQL: list(keywords), r.KEYWORD_METRICS_GAQL: list(metrics)}
    if campaigns is not None:
        ans[r.CAMPAIGNS_GAQL] = list(campaigns)
    ans.update(answers)
    return A.a_read(answers=ans)


def test_negative_keywords_in_search_campaigns():
    assert one("G-KWD-01")["result"] == "pass" and "Checked 1 Search campaign: all have negative keywords" in one("G-KWD-01")["found"]
    rows = [A.campaign("10", "Has own"), A.campaign("11", "Has list"), A.campaign("12", "Has none"), A.campaign("13", "PMax", ctype="PERFORMANCE_MAX")]
    f = one("G-KWD-01", A.a_read(answers={r.CAMPAIGNS_GAQL: rows, r.CAMPAIGN_NEGATIVES_GAQL: [A.negative("10")], r.CAMPAIGN_LISTS_GAQL: [A.attached_list("11")]}))
    assert f["result"] == "warn" and f["items"] == ["Has none"] and "1 of 3 Search campaigns have no negative keyword and no negative keyword list." == f["found"]
    nothing = A.a_read(answers={r.CAMPAIGN_NEGATIVES_GAQL: [], r.CAMPAIGN_LISTS_GAQL: []})
    f = one("G-KWD-01", nothing)
    assert f["result"] == "warn" and "Negatives set for the whole account cannot be read" in f["found"]
    assert one("G-KWD-01", camps(A.campaign("13", "PMax", ctype="PERFORMANCE_MAX")))["result"] == "na"
    unread = A.a_read()
    unread["campaigns"]["campaigns"][0]["negatives"] = None
    assert one("G-KWD-01", unread)["result"] == "unknown" and one("G-KWD-01", campaigns=None)["result"] == "unknown"
    unread = A.a_read()
    unread["campaigns"]["campaigns"][0]["lists"] = None
    assert one("G-KWD-01", unread)["result"] == "unknown"


def test_broad_match_without_smart_bidding():
    assert one("G-KWD-02")["result"] == "pass" and "No enabled broad match keyword" in one("G-KWD-02")["found"]
    camps_ = [A.campaign("10", "Manual", bidding="MANUAL_CPC"), A.campaign("11", "Smart", bidding="TARGET_CPA", target_cpa="30000000"), A.campaign("12", "Clicks", bidding="TARGET_SPEND")]
    kws = [A.keyword(cid="10", gid="100", crit=str(i), text="m%d" % i, match="BROAD") for i in range(3)] + [A.keyword(cid="11", gid="101", crit="9", text="smart", match="BROAD"),
           A.keyword(cid="12", gid="102", crit="8", text="phrase", match="PHRASE"), A.keyword(cid="12", gid="102", crit="7", text="c", match="BROAD")]
    f = one("G-KWD-02", kw_read(kws, campaigns=camps_))
    assert f["result"] == "fail" and "4 broad match keywords in 2 campaigns without a conversion-based bidding strategy." == f["found"]
    assert f["items"] == ["Manual (3 broad keywords, manual cpc)", "Clicks (1 broad keywords, target spend)"]
    ok = kw_read([A.keyword(cid="11", gid="101", crit="9", match="BROAD")], campaigns=camps_)
    assert one("G-KWD-02", ok)["result"] == "pass" and "1 broad match keyword, all in" in one("G-KWD-02", ok)["found"]
    assert one("G-KWD-02", keywords=None)["result"] == "unknown" and one("G-KWD-02", campaigns=None)["result"] == "unknown"


def qs_read(rows):
    """rows: [(qs, impressions, below flags)]"""
    kws, mets = [], []
    for i, (qs, imp, flags) in enumerate(rows):
        kws.append(A.keyword(qs=qs, crit=str(i), text="kw %d" % i, creative="BELOW_AVERAGE" if "ad" in flags else "AVERAGE",
                             post_click="BELOW_AVERAGE" if "lp" in flags else "AVERAGE", ctr="BELOW_AVERAGE" if "ctr" in flags else "AVERAGE"))
        mets.append(A.keyword_metrics(crit=str(i), impressions=imp))
    return kw_read(kws, mets)


def test_quality_score_share_of_impressions():
    assert one("G-KWD-03")["result"] == "pass"
    f = one("G-KWD-03", qs_read([(3, 300, ()), (9, 700, ())]))
    assert f["result"] == "fail" and f["found"].startswith("30% of impressions") and f["items"] == ["kw 0 (Quality Score 3, 300 impressions, Search | Implants)"]
    assert one("G-KWD-03", qs_read([(3, 299, ()), (9, 701, ())]))["result"] == "warn"                 # 29.9% is a warning, not a failure
    assert one("G-KWD-03", qs_read([(3, 150, ()), (9, 850, ())]))["result"] == "warn"
    f = one("G-KWD-03", qs_read([(3, 149, ()), (9, 851, ())]))
    assert f["result"] == "pass" and f["items"] == []
    assert one("G-KWD-03", qs_read([(4, 500, ()), (5, 500, ())]))["result"] == "fail"                 # 4 counts as low, 5 does not
    assert one("G-KWD-03", kw_read([A.keyword()], [A.keyword_metrics(impressions=100)]))["result"] == "na"       # nothing scored
    assert one("G-KWD-03", keywords=None)["result"] == "unknown"
    nometrics = A.a_read(answers={r.KEYWORD_METRICS_GAQL: []})
    assert one("G-KWD-03", nometrics)["result"] == "na"


def test_quality_score_parts_say_what_to_fix():
    healthy = qs_read([(8, 1000, ())])
    f = one("G-KWD-04", healthy)
    assert f["result"] == "pass" and "ad relevance 0%, landing page 0%, expected click-through rate 0%" in f["found"]
    f = one("G-KWD-04", qs_read([(8, 650, ("ad",)), (8, 350, ())]))
    assert f["result"] == "warn" and "ad relevance is below average on 65% of impressions" in f["found"] and "landing page" not in f["found"]
    f = one("G-KWD-04", qs_read([(8, 1000, ("ad", "lp", "ctr"))]))
    assert f["found"] == ("On scored keywords, ad relevance is below average on 100% of impressions; landing page experience is below average on 100%; "
                          "expected click-through rate is below average on 100%.")
    assert one("G-KWD-04", qs_read([(8, 350, ("lp",)), (8, 650, ())]))["result"] == "warn"            # 35% is the line (the read keeps whole percents)
    assert one("G-KWD-04", qs_read([(8, 340, ("lp",)), (8, 660, ())]))["result"] == "pass"
    assert one("G-KWD-04", qs_read([(8, 350, ("ad",)), (8, 650, ())]))["result"] == "warn"            # ad relevance is judged on the same 35% line
    assert one("G-KWD-04", qs_read([(8, 340, ("ad",)), (8, 660, ())]))["result"] == "pass"
    f = one("G-KWD-04", qs_read([(3, 100, ("ad",)), (5, 100, ("ad",))]))                              # low and mid impressions are equal
    assert f["result"] == "warn" and "ad relevance is below average on 100%" in f["found"]
    f = one("G-KWD-04", qs_read([(3, 100, ("ad",)), (8, 100, ("ad",))]))                              # low and high impressions are equal
    assert f["result"] == "warn" and "ad relevance is below average on 100%" in f["found"]
    assert one("G-KWD-04", qs_read([(8, 500, ("ctr",)), (8, 500, ())]))["result"] == "warn"
    assert one("G-KWD-04", qs_read([(8, 490, ("ctr",)), (8, 510, ())]))["result"] == "pass"
    assert one("G-KWD-04", kw_read([A.keyword()], [A.keyword_metrics()]))["result"] == "na" and one("G-KWD-04", keywords=None)["result"] == "unknown"


def test_duplicate_keywords():
    assert one("G-KWD-05")["result"] == "pass"
    rows = [A.keyword(gid="100", crit="1", text="dup"), A.keyword(gid="101", crit="2", text="dup")]
    f = one("G-KWD-05", kw_read(rows, campaigns=[A.campaign("10", "Search | Implants")], **{r.AD_GROUPS_GAQL: [A.ad_group("100", "10", "A"), A.ad_group("101", "10", "B")]}))
    assert f["result"] == "warn" and "Keywords in more than one ad group of the same campaign: 1 (1 extra copies)." == f["found"]
    assert f["items"] == ["dup (phrase, 2 ad groups, Search | Implants)"] and f["count"] == 1
    assert one("G-KWD-05", keywords=None)["result"] == "unknown"


def test_keywords_that_spend_and_never_convert():
    def read(cost):
        return kw_read([A.keyword(crit="1", text="waste")], [A.keyword_metrics(crit="1", clicks=40, cost=cost, conversions=0.0)])
    base_spend = 1000.0                                                                  # the fixture's campaign spent USD 1,000
    f = one("G-KWD-06", read(100.0))
    assert f["result"] == "fail" and "USD 100 on 1 keyword with 15 or more clicks and no conversion in 30 days (10% of spend)." == f["found"]
    assert f["items"] == ["waste (USD 100, 40 clicks, Search | Implants)"]
    assert one("G-KWD-06", read(99.0))["result"] == "warn" and one("G-KWD-06", read(30.0))["result"] == "warn"
    assert one("G-KWD-06", read(29.0))["result"] == "pass" and one("G-KWD-06")["result"] == "pass"
    assert one("G-KWD-06", keywords=None)["result"] == "unknown" and one("G-KWD-06", campaigns=None)["result"] == "unknown"
    nometrics = A.a_read(answers={r.KEYWORD_METRICS_GAQL: []})
    assert one("G-KWD-06", nometrics)["result"] == "pass"
    unread = A.a_read()
    unread["keywords"]["wasted"] = None
    assert one("G-KWD-06", unread)["result"] == "unknown"
    assert base_spend == 1000.0


def test_search_term_spend_that_never_converted():
    def read(wasted, total, rows=2):
        return A.a_read(answers={r.TERMS_GAQL: [A.term("ok", total - wasted, 10, 3.0), A.term("junk", wasted, 9, 0.0)][:rows]})
    f = one("G-KWD-07", read(600.0, 1000.0))
    assert f["result"] == "fail" and f["found"] == "60% of spend on search terms (USD 600) went to terms that never converted." and f["items"] == ["junk (USD 600, 9 clicks, Search | Implants)"]
    assert one("G-KWD-07", read(599.0, 1000.0))["result"] == "warn" and one("G-KWD-07", read(350.0, 1000.0))["result"] == "warn"
    assert one("G-KWD-07", read(349.0, 1000.0))["result"] == "pass"
    full = A.a_read(answers={r.TERMS_GAQL: [A.term("t%d" % i, 2.0, 1, 0.0) for i in range(r.TERMS_LIMIT)]})
    assert "the 1,000 most expensive search terms" in one("G-KWD-07", full)["found"]
    assert one("G-KWD-07", A.a_read(answers={r.TERMS_GAQL: []}))["result"] == "na" and one("G-KWD-07", terms=None)["result"] == "unknown"


def test_disapproved_and_rarely_served_keywords():
    assert one("G-KWD-08")["result"] == "pass" and "(0 keywords rarely served)" in one("G-KWD-08")["found"]
    f = one("G-KWD-08", kw_read([A.keyword(crit="1", text="bad", approval="DISAPPROVED"), A.keyword(crit="2")]))
    assert f["result"] == "warn" and f["found"] == "1 keyword disapproved." and f["items"] == ["bad (phrase, Search | Implants)"]
    ten = [A.keyword(crit=str(i), text="k%d" % i, serving="RARELY_SERVED" if i == 0 else "ELIGIBLE") for i in range(10)]
    f = one("G-KWD-08", kw_read(ten))
    assert f["result"] == "warn" and f["found"] == "1 keyword (10%) rarely served."
    nine = [A.keyword(crit=str(i), text="k%d" % i, serving="RARELY_SERVED" if i == 0 else "ELIGIBLE") for i in range(11)]
    assert one("G-KWD-08", kw_read(nine))["result"] == "pass"
    both = [A.keyword(crit="1", text="a", approval="DISAPPROVED", serving="RARELY_SERVED")]
    assert one("G-KWD-08", kw_read(both))["found"] == "1 keyword disapproved and 1 keyword (100%) rarely served."
    assert one("G-KWD-08", kw_read([]))["result"] == "na" and one("G-KWD-08", keywords=None)["result"] == "unknown"


def test_ad_groups_with_too_many_or_no_keywords():
    groups = [A.ad_group("100", "10", "Big"), A.ad_group("101", "10", "Empty"), A.ad_group("102", "10", "Fine")]
    kws = [A.keyword(gid="100", crit="b%d" % i, text="big %d" % i) for i in range(41)] + [A.keyword(gid="102", crit="f", text="fine")]
    rd = kw_read(kws, **{r.AD_GROUPS_GAQL: groups})
    f = one("G-KWD-09", rd)
    assert f["result"] == "warn" and f["found"] == "Search ad groups with more than 40 keywords: 1." and f["items"] == ["Big (Search | Implants)"]
    assert one("G-KWD-09")["result"] == "pass"
    f = one("G-KWD-10", rd)
    assert f["result"] == "warn" and f["found"] == "1 of 3 Search ad groups have no enabled keyword." and f["items"] == ["Empty (Search | Implants)"]
    assert one("G-KWD-10")["result"] == "pass"
    half = kw_read([A.keyword(gid="100", crit="1")], **{r.AD_GROUPS_GAQL: groups[:2]})
    assert one("G-KWD-10", half)["result"] == "fail"                                                  # 1 of 2 is 50%
    four = groups + [A.ad_group("103", "10", "Four")]
    assert one("G-KWD-10", kw_read([A.keyword(gid="100", crit="1"), A.keyword(gid="102", crit="3")], **{r.AD_GROUPS_GAQL: four}))["result"] == "fail"       # 2 of 4
    five = four + [A.ad_group("104", "10", "Five")]
    assert one("G-KWD-10", kw_read([A.keyword(gid="100", crit="1"), A.keyword(gid="102", crit="3"), A.keyword(gid="104", crit="4")], **{r.AD_GROUPS_GAQL: five}))["result"] == "warn"
    assert one("G-KWD-10", A.a_read(answers={r.AD_GROUPS_GAQL: [A.ad_group("1", "10", "Display", "DISPLAY_STANDARD")]}))["result"] == "na"
    for cid in ("G-KWD-09", "G-KWD-10"):
        assert one(cid, A.a_read(fail={r.KEYWORDS_GAQL}))["result"] == "unknown", cid                 # the keywords part was not read
        assert one(cid, A.a_read(fail={r.AD_GROUPS_GAQL}))["result"] == "unknown", cid                # nor the ad groups
        assert one(cid, structure={"searchAdGroups": 1})["result"] == "unknown", cid


# ── Ads and landing pages ───────────────────────────────────────────────────

def ads_read(ads, groups=None, **answers):
    ans = {r.ADS_GAQL: list(ads)}
    if groups is not None:
        ans[r.AD_GROUPS_GAQL] = list(groups)
    ans.update(answers)
    return A.a_read(answers=ans)


def test_an_ad_group_with_no_live_ad_is_critical():
    f = one("G-ADS-01")
    assert f["result"] == "pass" and "Checked 1 enabled Search ad group: all have a live ad." == f["found"]
    groups = [A.ad_group("100", "10", "Has ads"), A.ad_group("101", "10", "Dark")]
    f = one("G-ADS-01", ads_read([A.ad(gid="100")], groups))
    assert f["result"] == "fail" and f["severity"] == "critical" and f["found"] == "1 of 2 enabled Search ad groups have no live ad." and f["items"] == ["Dark (Search | Implants)"]
    assert one("G-ADS-01", A.a_read(answers={r.AD_GROUPS_GAQL: []}))["result"] == "na"
    assert one("G-ADS-01", A.a_read(fail={r.ADS_GAQL}))["result"] == "unknown" and one("G-ADS-01", A.a_read(fail={r.AD_GROUPS_GAQL}))["result"] == "unknown"
    assert one("G-ADS-01", structure=None)["result"] == "unknown"
    paused = ads_read([A.ad(gid="100", status="PAUSED")], groups[:1])
    assert one("G-ADS-01", paused)["result"] == "fail"


def test_an_ad_group_with_only_old_formats_needs_a_responsive_search_ad():
    groups = [A.ad_group("100", "10", "Old"), A.ad_group("101", "10", "Dark"), A.ad_group("102", "10", "Good")]
    rd = ads_read([A.ad(gid="100", atype="EXPANDED_TEXT_AD", aid="1"), A.ad(gid="102", aid="2")], groups)
    f = one("G-ADS-02", rd)
    assert f["result"] == "warn" and f["found"] == "Search ad groups with only older or call ad formats and no responsive search ad: 1." and f["items"] == ["Old (Search | Implants)"]
    assert f["count"] == 1                                                                       # the dark ad group is a different, critical finding
    assert one("G-ADS-02")["result"] == "pass" and one("G-ADS-02", structure=None)["result"] == "unknown" and one("G-ADS-02", A.a_read(fail={r.ADS_GAQL}))["result"] == "unknown"
    assert one("G-ADS-02", A.a_read(answers={r.AD_GROUPS_GAQL: []}))["result"] == "na"


def test_one_ad_per_ad_group():
    groups = [A.ad_group(str(100 + i), "10", "G%d" % i) for i in range(4)]
    def read(n_single):
        ads = []
        for i in range(4):
            ads.append(A.ad(gid=str(100 + i), aid="a%d" % i))
            if i >= n_single:
                ads.append(A.ad(gid=str(100 + i), aid="b%d" % i))
        return ads_read(ads, groups)
    assert one("G-ADS-03", read(0))["result"] == "pass" and one("G-ADS-03", read(0))["found"] == "0 of 4 Search ad groups have a single ad."
    f = one("G-ADS-03", read(1))
    assert f["result"] == "warn" and f["found"] == "1 of 4 Search ad groups (25%) have a single ad, so nothing is being tested." and f["items"] == ["G0 (Search | Implants)"]
    assert one("G-ADS-03", read(2))["result"] == "warn" and one("G-ADS-03", read(3))["result"] == "fail" and one("G-ADS-03", read(4))["result"] == "fail"
    assert one("G-ADS-03", A.a_read(answers={r.AD_GROUPS_GAQL: []}))["result"] == "na" and one("G-ADS-03", structure=None)["result"] == "unknown" and one("G-ADS-03", A.a_read(fail={r.ADS_GAQL}))["result"] == "unknown"


def test_poor_ad_strength():
    def read(poor, good):
        return ads_read([A.ad(aid="p%d" % i, strength="POOR") for i in range(poor)] + [A.ad(aid="g%d" % i, strength="GOOD") for i in range(good)] + [A.ad(aid="a", strength="AVERAGE")])
    assert one("G-ADS-04", read(0, 3))["result"] == "pass" and one("G-ADS-04", read(0, 3))["found"] == "Rated Poor: 0 of 4 responsive search ads. Rated Average: 1."
    only_good = ads_read([A.ad(aid="g", strength="GOOD")])
    assert one("G-ADS-04", only_good)["found"] == "Rated Poor: 0 of 1 responsive search ad. Rated Average: 0."
    assert one("G-ADS-04", read(1, 3))["result"] == "warn" and one("G-ADS-04", read(1, 3))["result"] == "warn"
    assert one("G-ADS-04", read(2, 2))["result"] == "fail"                                         # 2 of 5 is 40%
    assert one("G-ADS-04", read(1, 2))["result"] == "fail"                                         # 1 of 4 is 25%
    assert one("G-ADS-04", read(1, 3))["result"] == "warn"                                         # 1 of 5 is 20%
    assert one("G-ADS-04", A.a_read(answers={r.ADS_GAQL: []}))["result"] == "na" and one("G-ADS-04", ads=None)["result"] == "unknown"


def test_disapproved_ads():
    f = one("G-ADS-05")
    assert f["result"] == "pass" and f["found"] == "No live ad is disapproved."
    rd = ads_read([A.ad(aid="1", approval="DISAPPROVED", topics=("HEALTHCARE_AND_MEDICINE",)), A.ad(aid="2", approval="APPROVED_LIMITED"), A.ad(aid="3", approval="DISAPPROVED")])
    f = one("G-ADS-05", rd)
    assert f["result"] == "fail" and f["found"] == "2 live ads are disapproved, and 1 more approved with limits."
    assert f["items"] == ["Search | Implants / Implants (healthcare and medicine)", "Search | Implants / Implants (no topic given)"] and f["count"] == 2
    assert "approved with limits" in one("G-ADS-05", ads_read([A.ad(aid="2", approval="APPROVED_LIMITED")]))["found"]
    assert one("G-ADS-05", ads_read([A.ad(aid="1", approval="DISAPPROVED")]))["found"] == "1 live ad is disapproved."
    assert one("G-ADS-05", ads=None)["result"] == "unknown"


def test_retired_ad_formats():
    assert one("G-ADS-06")["result"] == "pass"
    rd = ads_read([A.ad(aid="1", atype="EXPANDED_TEXT_AD"), A.ad(aid="2", atype="EXPANDED_TEXT_AD"), A.ad(aid="3", atype="CALL_ONLY_AD"), A.ad(aid="4")])
    f = one("G-ADS-06", rd)
    assert f["result"] == "warn" and f["found"] == "3 live ads use a retired format (2 expanded text ad, 1 call only ad)."
    assert one("G-ADS-06", ads_read([A.ad(aid="1", atype="EXPANDED_TEXT_AD")]))["found"] == "1 live ad uses a retired format (1 expanded text ad)."
    assert one("G-ADS-06", ads=None)["result"] == "unknown"


def test_pinned_headlines():
    f = one("G-ADS-07")
    assert f["result"] == "pass" and "(0 pin some)" in f["found"]
    rd = ads_read([A.ad(aid="1", pins=3), A.ad(aid="2", pins=1), A.ad(aid="3", pins=4)])
    f = one("G-ADS-07", rd)
    assert f["result"] == "warn" and f["found"] == "2 responsive search ads pin three or more headlines."
    assert one("G-ADS-07", ads_read([A.ad(aid="1", pins=2), A.ad(aid="2", pins=1)]))["result"] == "pass"
    assert one("G-ADS-07", A.a_read(answers={r.ADS_GAQL: []}))["result"] == "na" and one("G-ADS-07", ads=None)["result"] == "unknown"


def test_too_many_responsive_search_ads_in_one_ad_group():
    assert one("G-ADS-08")["result"] == "pass"
    four = [A.ad(aid=str(i)) for i in range(4)]
    f = one("G-ADS-08", ads_read(four))
    assert f["result"] == "warn" and f["found"] == "Search ad groups with more than three responsive search ads: 1." and f["items"] == ["Implants (Search | Implants)"]
    assert one("G-ADS-08", ads_read(four[:3]))["result"] == "pass" and one("G-ADS-08", structure=None)["result"] == "unknown" and one("G-ADS-08", A.a_read(fail={r.ADS_GAQL}))["result"] == "unknown"


def pages(*results, of=None):
    return {"checked": len(results), "of": of or len(results), "results": list(results)}


def test_landing_pages_that_do_not_load():
    ok = {"url": "https://a.test/x", "status": 200, "error": "", "ms": 400}
    f = one("G-ADS-09", pages=pages(ok))
    assert f["result"] == "pass" and f["found"] == "Checked 1 landing page: all loaded."
    f = one("G-ADS-09", pages=pages(ok, {"url": "https://a.test/gone", "status": 404, "error": "", "ms": 200}, {"url": "https://a.test/down", "status": 0, "error": "could not connect", "ms": 0}))
    assert f["result"] == "fail" and f["found"] == "2 of 3 landing pages did not load." and f["items"] == ["https://a.test/gone (HTTP 404)", "https://a.test/down (could not connect)"]
    assert one("G-ADS-09", pages=pages({"url": "u", "status": 399, "error": "", "ms": 1}))["result"] == "pass" and one("G-ADS-09", pages=pages({"url": "u", "status": 400, "error": "", "ms": 1}))["result"] == "fail"
    f = one("G-ADS-09", pages=pages(ok, {"url": "https://a.test/slow", "status": 200, "error": "", "ms": 3001}))
    assert f["result"] == "warn" and f["found"] == "1 of 2 landing pages were slow: they took more than 3 seconds to answer." and f["items"] == ["https://a.test/slow (3.0 seconds)"]
    assert one("G-ADS-09", pages=pages({"url": "u", "status": 200, "error": "", "ms": 3000}))["result"] == "pass"
    assert one("G-ADS-09", pages=pages(*[ok] * 40, of=90))["found"] == "Checked 40 of 90 landing pages: all loaded."
    assert one("G-ADS-09", pages=pages())["result"] == "na" and one("G-ADS-09", pages=None)["result"] == "unknown" and one("G-ADS-09")["result"] == "unknown"


def test_a_page_that_never_answered_is_slow_not_broken_and_other_errors_are_failures():
    ok = {"url": "https://a.test/x", "status": 200, "error": "", "ms": 400, "blocked": False}
    gone = {"url": "https://a.test/gone", "status": 0, "error": "timed out", "ms": 8004, "blocked": False}
    f = one("G-ADS-09", pages=pages(ok, gone))
    assert f["result"] == "warn" and f["found"] == "1 of 2 landing pages were slow: they took more than 3 seconds to answer." and f["items"] == ["https://a.test/gone (no answer in 8 seconds, twice)"]
    dns = {"url": "https://a.test/dns", "status": 0, "error": "the address does not resolve", "ms": 20, "blocked": False}
    f = one("G-ADS-09", pages=pages(ok, gone, dns))
    assert f["result"] == "fail" and f["found"] == "1 of 3 landing pages did not load." and f["items"] == ["https://a.test/dns (the address does not resolve)"]    # the slow one is not listed with the broken
    quick = dict(gone, ms=300)
    assert one("G-ADS-09", pages=pages(ok, quick))["items"] == ["https://a.test/gone (no answer in 1 seconds, twice)"]
    f = one("G-ADS-09", pages=pages(ok, dict(gone, ms=3500, error="")))
    assert f["items"] == ["https://a.test/gone (3.5 seconds)"]


def test_pages_that_refuse_the_automated_check_are_not_called_broken():
    ok = {"url": "https://a.test/x", "status": 200, "error": "", "ms": 400, "blocked": False}
    refused = {"url": "https://a.test/shield", "status": 403, "error": "", "ms": 100, "blocked": True}
    f = one("G-ADS-09", pages=pages(ok, refused))
    assert f["result"] == "pass" and f["found"] == "Checked 2 landing pages: all loaded. 1 page refused the automated check and could not be verified."
    f = one("G-ADS-09", pages=pages(ok, refused, {"url": "https://a.test/gone", "status": 404, "error": "", "ms": 5, "blocked": False}))
    assert f["result"] == "fail" and f["items"] == ["https://a.test/gone (HTTP 404)"] and f["found"].endswith("1 page refused the automated check and could not be verified.")
    f = one("G-ADS-09", pages=pages(ok, refused, {"url": "https://a.test/slow", "status": 200, "error": "", "ms": 9000, "blocked": False}))
    assert f["result"] == "warn" and f["items"] == ["https://a.test/slow (9.0 seconds)"]
    slow_blocked = dict(refused, ms=9000)
    assert one("G-ADS-09", pages=pages(ok, slow_blocked))["result"] == "pass"                  # a refusal is never slow or broken
    f = one("G-ADS-09", pages=pages(refused, dict(refused, status=429, url="https://a.test/other")))
    assert f["result"] == "unknown" and "refused the automated check (HTTP 403/429)" in f["found"]


def test_an_account_with_no_enabled_campaign_says_it_is_not_advertising():
    f = one("G-STR-12")
    assert f["result"] == "pass" and f["found"] == "1 enabled campaign."
    none = camps(A.campaign("1", status="PAUSED"), A.campaign("2", status="PAUSED"))
    f = one("G-STR-12", none)
    assert f["result"] == "warn" and f["severity"] == "high" and f["found"].startswith("No campaign is enabled (2 paused campaigns). The account is not advertising")
    assert one("G-STR-12", camps())["found"].startswith("No campaign is enabled (0 paused campaigns).")
    assert one("G-STR-12", campaigns=None)["result"] == "unknown"
    assert one("G-STR-12", camps(A.campaign("1"), A.campaign("2")))["found"] == "2 enabled campaigns."


def test_a_paused_or_nearly_idle_account_is_not_failed_for_actions_that_recorded_nothing():
    idle = {r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 50, 4, 20.0, 0.0)]}
    paused = {r.CAMPAIGNS_GAQL: [A.campaign("10", status="PAUSED")], r.CAMPAIGN_METRICS_GAQL: []}
    quiet = lambda answers: A.a_read(answers=dict({r.CONVERSIONS_GAQL: [A.action("1", "Form")], r.CONVERSION_VOLUME_GAQL: [A.volume("1", 0.0, all_conversions=0.0)]}, **answers))
    for answers in (idle, paused):
        f = one("G-CNV-03", quiet(answers))
        assert f["result"] == "na" and "too little to expect an action to record anything" in f["found"]
    assert one("G-CNV-03", quiet({r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 5000, 400, 100.0, 0.0)]}))["result"] == "fail"          # at the minimum it is judged
    assert one("G-CNV-03", quiet({}), campaigns=None)["result"] == "fail"                                                                   # spend unread: judged on the actions alone
    value = A.campaign("10", bidding="MAXIMIZE_CONVERSION_VALUE")
    novalue = {r.CONVERSIONS_GAQL: [A.action("1", "Sale", category="PURCHASE")], r.CONVERSION_VOLUME_GAQL: [A.volume("1", 5.0, value=0.0)], r.CAMPAIGNS_GAQL: [value]}
    f = one("G-CNV-07", A.a_read(answers=dict(novalue, **{r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 50, 4, 20.0, 5.0)]})))
    assert f["result"] == "na" and "too little to expect conversion values" in f["found"]
    assert one("G-CNV-07", A.a_read(answers=dict(novalue, **{r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 5000, 400, 100.0, 5.0)]})))["result"] == "fail"


def test_a_threshold_is_decided_on_the_real_share_not_a_rounded_one():
    # budget: 19.6 percent lost is under the 20 line even though it rounds to 20
    rows = [A.campaign("10", "Near")]
    near = camps(*rows, metrics=[A.campaign_metrics("10", cost=500.0, conversions=5.0, budget_lost=0.196)])
    assert one("G-STR-04", near)["result"] == "pass"
    assert one("G-STR-04", camps(*rows, metrics=[A.campaign_metrics("10", cost=500.0, conversions=5.0, budget_lost=0.2)]))["items"] == ["Near (20% of impressions lost to budget)"]
    # rank: 39.6 is under 40
    rank = lambda v: camps(A.campaign("10", "R"), metrics=[A.campaign_metrics("10", cost=600.0, rank_lost=v)])
    assert one("G-STR-10", rank(0.396))["result"] == "pass" and one("G-STR-10", rank(0.4))["result"] == "warn"
    # quality score parts: 34.6 is under 35 on ad relevance, 49.6 under 50 on click-through rate
    assert one("G-KWD-04", qs_read([(8, 346, ("ad",)), (8, 654, ())]))["result"] == "pass"
    assert one("G-KWD-04", qs_read([(8, 496, ("ctr",)), (8, 504, ())]))["result"] == "pass"
    f = one("G-KWD-04", qs_read([(8, 355, ("lp",)), (8, 645, ())]))
    assert f["result"] == "warn" and "landing page experience is below average on 36%" in f["found"]
    # optimization score: 49.6 is under 50 even though it rounds to 50
    assert one("G-SET-07", customer_with(optimizationScore=49.6))["result"] == "warn" and one("G-SET-07", customer_with(optimizationScore=49.6))["found"] == "The optimization score is 50%."


def test_a_portfolio_strategy_is_judged_on_its_campaigns_together():
    pf = "customers/1112223334/biddingStrategies/5"
    rows = [A.campaign("10", "Pool A", bidding="TARGET_CPA", target_cpa="40000000", portfolio=pf), A.campaign("11", "Pool B", bidding="TARGET_CPA", target_cpa="40000000", portfolio=pf),
            A.campaign("12", "Alone", bidding="TARGET_CPA", target_cpa="40000000")]
    enough = [A.campaign_metrics("10", cost=300.0, conversions=20.0), A.campaign_metrics("11", cost=300.0, conversions=20.0), A.campaign_metrics("12", cost=300.0, conversions=20.0)]
    f = one("G-STR-07", camps(*rows, metrics=enough))
    assert f["result"] == "warn" and f["found"] == "1 of 3 Smart Bidding campaigns had fewer conversions in 30 days than Google suggests." and f["items"] == ["Alone (20 conversions, about 30 suggested)"]
    short = [A.campaign_metrics("10", cost=300.0, conversions=10.0), A.campaign_metrics("11", cost=300.0, conversions=15.0), A.campaign_metrics("12", cost=300.0, conversions=40.0)]
    f = one("G-STR-07", camps(*rows, metrics=short))
    assert f["found"].startswith("2 of 3") and f["items"] == ["Pool A (25 conversions together with the other 1 campaign on its portfolio strategy, about 30 suggested)",
                                                              "Pool B (25 conversions together with the other 1 campaign on its portfolio strategy, about 30 suggested)"]
    one_in_pool = camps(A.campaign("10", "Lone pool", bidding="TARGET_CPA", target_cpa="40000000", portfolio=pf), metrics=[A.campaign_metrics("10", cost=300.0, conversions=10.0)])
    assert one("G-STR-07", one_in_pool)["items"] == ["Lone pool (10 conversions, about 30 suggested)"]            # a portfolio of one reads like a campaign
    idle = camps(*rows[:2], metrics=[A.campaign_metrics("10", cost=0.0, conversions=0.0)])
    assert one("G-STR-07", idle)["result"] == "pass"


def test_a_list_that_only_serves_display_is_named_with_its_search_size_and_an_ended_campaign_is_not_running():
    mixed = lists_read(A.user_list("Display only", search=0, display=500, lid="1"), A.user_list("Small", search=300, display=300, lid="2"), A.user_list("Empty", lid="3"),
                       A.user_list("Full", search=5000, display=5000, lid="4"))
    f = one("G-LNK-03", mixed)
    assert f["result"] == "warn" and f["items"] == ["Display only (0 for Search)", "Small (300 for Search)"]                  # the empty list is not "small"
    assert one("G-LNK-02", mixed)["result"] == "pass"
    ended = camps(A.campaign("1", "Old promo", primary="ENDED"), A.campaign("2", "Older promo", primary="ENDED"))
    f = one("G-STR-12", ended)
    assert f["result"] == "warn" and f["found"].startswith("No campaign is running (2 enabled campaigns ended). The account is not advertising") and f["caps"] == "the account is not advertising"
    some = camps(A.campaign("1", "Old promo", primary="ENDED"), A.campaign("2", "Live"))
    assert one("G-STR-12", some)["result"] == "pass" and one("G-STR-12", some)["found"] == "1 enabled campaign."


def test_too_little_spend_to_expect_conversions_is_about_a_hundred_dollars_in_any_currency():
    """100 was read as 100 of the account's own currency: about $1 in rupees, so a rupee account spending $35 and converting nothing failed a critical check."""
    assert model.min_spend("USD") == 100 and model.min_spend("EUR") == 100 and model.min_spend("") == 100 and model.min_spend(None) == 100
    assert model.min_spend("INR") == 8500 and model.min_spend("jpy") == 15000
    small = A.a_read(answers={r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 500, 40, 3000.0, 0.0)]})
    small["customer"] = dict(small["customer"], currency="INR")
    assert one("G-CNV-02", small)["result"] == "na"                                              # INR 3,000 is about $35
    big = A.a_read(answers={r.CAMPAIGN_METRICS_GAQL: [A.campaign_metrics("10", 500, 40, 9000.0, 0.0)]})
    big["customer"] = dict(big["customer"], currency="INR")
    assert one("G-CNV-02", big)["result"] == "fail"                                              # INR 9,000 is over $100 and nothing converted
