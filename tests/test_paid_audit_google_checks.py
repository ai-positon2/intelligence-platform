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
    assert f["result"] == "fail" and "1 call conversion actions" in f["found"] and "off" in f["found"]
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
