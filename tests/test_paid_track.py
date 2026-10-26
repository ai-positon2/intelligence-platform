"""Tracking & Data Health: reading Google and Meta, the report and its cache (tracker/paid_track.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402
from tracker import paid_track as t  # noqa: E402

TODAY = dt.date(2026, 10, 22)
AT = dt.datetime(2026, 10, 22, 15, 0, tzinfo=dt.timezone.utc)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}


def day_rows(cost=50.0, conv=3.0, first=1, last=30, tz="America/New_York"):
    return [{"customer": {"timeZone": tz}, "segments": {"date": (TODAY - dt.timedelta(days=i)).isoformat()}, "metrics": {"costMicros": str(int(cost * 1e6)), "conversions": conv}} for i in range(first, last + 1)]


def action_row(aid="11", name="Form fill", status="ENABLED", kind="WEBPAGE", primary=True):
    return {"conversionAction": {"id": aid, "name": name, "status": status, "type": kind, "primaryForGoal": primary}}


def action_day_rows(aid="11", conv=3.0, first=1, last=30):
    return [{"segments": {"conversionAction": "customers/1234567890/conversionActions/%s" % aid, "date": (TODAY - dt.timedelta(days=i)).isoformat()}, "metrics": {"conversions": conv}} for i in range(first, last + 1)]


def list_row(lid="7", name="Visitors", size_search="5000", size_display="9000", status="OPEN", kind="REMARKETING"):
    return {"userList": {"id": lid, "name": name, "type": kind, "membershipStatus": status, "sizeForSearch": size_search, "sizeForDisplay": size_display}}


def searcher(days=None, actions=None, action_days=None, lists=None, terms=True, uploads=None, log=None, fail=(), fail_kinds=()):
    days = days if days is not None else {ACCT["id"]: day_rows()}
    actions = actions if actions is not None else {ACCT["id"]: [action_row()]}
    action_days = action_days if action_days is not None else {ACCT["id"]: action_day_rows()}
    lists = lists if lists is not None else {ACCT["id"]: [list_row()]}
    uploads = uploads if uploads is not None else {}

    def run(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        for kind, marker in (("actions", "FROM conversion_action"), ("action_days", "segments.conversion_action"), ("lists", "FROM user_list"), ("terms", "accepted_customer_data_terms"), ("uploads", "offline_conversion_upload")):
            if marker in q:
                if kind in fail_kinds:
                    raise gads.GadsError("api", "no %s read" % kind)
                if kind == "actions":
                    return actions.get(cid, [])
                if kind == "action_days":
                    return action_days.get(cid, [])
                if kind == "lists":
                    return lists.get(cid, [])
                if kind == "terms":
                    return [{"customer": {"conversionTrackingSetting": {"acceptedCustomerDataTerms": terms}}}]
                return uploads.get(cid, [])
        if "customer.time_zone" in q:
            return days.get(cid, [])
        raise AssertionError("unexpected query: " + q[:80])
    return run


# ── Parsing Google's rows ───────────────────────────────────────────────────

def test_days_are_summed_per_date_in_order_with_the_accounts_time_zone():
    rows = day_rows(cost=10.0, conv=1.5, first=1, last=2, tz="Asia/Kolkata") + day_rows(cost=5.0, conv=0.5, first=1, last=1, tz="Asia/Kolkata") + [{"metrics": {"conversions": 9}}]
    days, tz = t.read_days(rows)
    assert tz == "Asia/Kolkata" and [d["date"] for d in days] == ["2026-10-20", "2026-10-21"]
    assert days[1] == {"date": "2026-10-21", "cost": 15.0, "conv": 2.0} and days[0] == {"date": "2026-10-20", "cost": 10.0, "conv": 1.5}


def test_conversion_actions_carry_their_own_days_taken_from_the_resource_name():
    rows = [action_row("11", "Form"), action_row("12", "Call", status="HIDDEN", kind="UPLOAD_CALLS", primary=False), {"conversionAction": {"name": "no id"}}]
    got = {a["id"]: a for a in t.read_actions(rows, action_day_rows("11", 2.0, 1, 3) + action_day_rows("12", 1.0, 2, 2) + [{"segments": {"date": "2026-10-01"}, "metrics": {"conversions": 5}}])}
    assert sorted(got) == ["11", "12"] and got["12"]["status"] == "HIDDEN" and got["12"]["type"] == "UPLOAD_CALLS" and got["12"]["primary"] is False and got["11"]["primary"] is True
    assert got["11"]["days"] == [{"date": "2026-10-19", "conv": 2.0}, {"date": "2026-10-20", "conv": 2.0}, {"date": "2026-10-21", "conv": 2.0}] and got["12"]["days"] == [{"date": "2026-10-20", "conv": 1.0}]


def test_a_list_is_as_big_as_its_larger_size_and_open_only_while_it_collects():
    got = {x["id"]: x for x in t.read_lists([list_row("1", size_search="5000", size_display="9000"), list_row("2", size_search="300", size_display="100", status="CLOSED", kind="CRM_BASED"), {"userList": {"name": "no id"}}])}
    assert sorted(got) == ["1", "2"] and got["1"]["size"] == 9000 and got["1"]["open"] is True and got["2"]["size"] == 300 and got["2"]["open"] is False and got["2"]["type"] == "CRM_BASED"


def test_offline_uploads_count_only_with_an_enabled_upload_action_and_the_newest_log_row_wins():
    acts = [{"status": "ENABLED", "type": "UPLOAD_CLICKS"}, {"status": "HIDDEN", "type": "UPLOAD_CALLS"}, {"status": "ENABLED", "type": "WEBPAGE"}]
    rows = [{"offlineConversionUploadClientSummary": {"status": "GOOD", "lastUploadDateTime": "2026-10-01 09:00:00"}}, {"offlineConversionUploadClientSummary": {"status": "NO_RECENT_UPLOAD", "lastUploadDateTime": "2026-10-10 09:00:00"}}]
    assert t.read_uploads(rows, acts) == {"hasUploadActions": True, "status": "NO_RECENT_UPLOAD", "lastUpload": "2026-10-10 09:00:00"}
    assert t.read_uploads([], [{"status": "HIDDEN", "type": "UPLOAD_CLICKS"}, {"status": "ENABLED", "type": "WEBPAGE"}]) == {"hasUploadActions": False, "status": "", "lastUpload": ""}


def test_the_accounts_own_date_decides_what_today_is():
    late = dt.datetime(2026, 10, 22, 2, 0, tzinfo=dt.timezone.utc)
    assert t.local_day("America/Los_Angeles", late) == dt.date(2026, 10, 21) and t.local_day("Asia/Kolkata", late) == dt.date(2026, 10, 22)
    assert t.local_day("", late) == dt.date(2026, 10, 22) and t.local_day("Not/AZone", late) == dt.date(2026, 10, 22)


def test_one_account_is_read_with_everything_the_rules_need():
    log = []
    got = t.read_account(ACCT["id"], searcher(log=log, terms=False, uploads={ACCT["id"]: [{"offlineConversionUploadClientSummary": {"status": "GOOD", "lastUploadDateTime": "2026-10-20 09:00:00"}}]},
                                              actions={ACCT["id"]: [action_row("11"), action_row("12", kind="UPLOAD_CLICKS")]}), AT)
    assert got["today"] == "2026-10-22" and got["warnings"] == [] and got["terms"] is False and len(got["days"]) == 30
    assert [a["id"] for a in got["actions"]] == ["11", "12"] and got["lists"][0]["size"] == 9000 and got["pixels"] is None
    assert got["uploads"] == {"hasUploadActions": True, "status": "GOOD", "lastUpload": "2026-10-20 09:00:00"}
    days_q = [q for _c, q in log if "customer.time_zone" in q][0]
    assert "BETWEEN '2026-09-22' AND '2026-10-22'" in days_q                          # 30 days before the server's date, to the server's date


def test_every_read_but_the_days_is_soft_and_says_what_was_skipped():
    got = t.read_account(ACCT["id"], searcher(fail_kinds=("actions", "lists", "terms", "uploads")), AT)
    assert got["actions"] is None and got["lists"] is None and got["terms"] is None and got["uploads"] is None
    assert got["warnings"] == ["The conversion actions could not be read (no actions read), so that check was skipped.", "The audience lists could not be read (no lists read), so that check was skipped.",
                               "Whether the customer data terms are accepted could not be read (no terms read), so that check was skipped."]
    only_days = t.read_account(ACCT["id"], searcher(fail_kinds=("action_days",)), AT)
    assert only_days["actions"] is None and only_days["uploads"] is None and len(only_days["warnings"]) == 1          # without what each action recorded the actions are not judged, nor are uploads
    only_up = t.read_account(ACCT["id"], searcher(fail_kinds=("uploads",)), AT)
    assert only_up["uploads"] is None and only_up["actions"] is not None and only_up["warnings"] == ["The offline conversion upload log could not be read (no uploads read), so that check was skipped."]
    with pytest.raises(gads.GadsError):
        t.read_account(ACCT["id"], searcher(fail=(ACCT["id"],)), AT)


# ── Meta ────────────────────────────────────────────────────────────────────

def test_pixels_and_audiences_are_parsed_and_an_audience_without_a_size_is_never_taken_for_an_empty_one():
    assert t.parse_pixels([{"id": "1", "name": "Site", "last_fired_time": "2026-10-21T10:00:00+0000", "is_unavailable": False}, {"id": "2", "is_unavailable": True}, {"name": "no id"}]) == [
        {"id": "1", "name": "Site", "lastFired": "2026-10-21T10:00:00+0000", "unavailable": False}, {"id": "2", "name": "2", "lastFired": "", "unavailable": True}]
    got = t.parse_audiences([{"id": "1", "name": "A", "approximate_count_lower_bound": 1000, "subtype": "CUSTOM"}, {"id": "2", "approximate_count_lower_bound": -1}, {"id": "3"},
                             {"id": "4", "approximate_count_lower_bound": "abc"}, {"approximate_count_lower_bound": 5}, {"id": "5", "approximate_count_lower_bound": 0}])
    assert [(x["id"], x["size"]) for x in got] == [("1", 1000), ("5", 0)] and got[0]["type"] == "CUSTOM" and got[0]["open"] is True


def meta_row(cid, day, spend="40", leads="2", objective="OUTCOME_LEADS"):
    return {"campaign_id": cid, "campaign_name": cid, "objective": objective, "date_start": day, "spend": spend, "impressions": "1000", "inline_link_clicks": "100", "actions": [{"action_type": "lead", "value": leads}]}


def meta_pages(insights=None, pixels=None, audiences=None, fail=()):
    insights = insights if insights is not None else [meta_row(c, (TODAY - dt.timedelta(days=i)).isoformat()) for i in range(1, 31) for c in ("9", "10")]
    pixels = pixels if pixels is not None else [{"id": "1", "name": "Site", "last_fired_time": "2026-10-22T10:00:00+0000"}]
    audiences = audiences if audiences is not None else [{"id": "5", "name": "Visitors", "approximate_count_lower_bound": 4000}]

    def pages(path, params):
        for kind, suffix, rows in (("pixels", "/adspixels", pixels), ("audiences", "/customaudiences", audiences), ("insights", "/insights", insights)):
            if path.endswith(suffix):
                if kind in fail:
                    raise RuntimeError("Meta says no")
                return rows
        raise AssertionError(path)
    return pages


def test_a_meta_account_is_read_as_totals_by_day_pixels_and_audiences():
    got = t.read_meta_account("act_1234567", meta_pages(), AT)
    assert got["today"] == "2026-10-22" and got["warnings"] == [] and got["actions"] is None and got["terms"] is None and got["uploads"] is None
    assert len(got["days"]) == 30 and got["days"][0]["date"] == "2026-09-22" and got["days"][-1] == {"date": "2026-10-21", "cost": 80.0, "conv": 4.0}      # two campaigns a day are one day
    assert got["pixels"][0]["name"] == "Site" and got["lists"] == [{"id": "5", "name": "Visitors", "size": 4000, "open": True, "type": ""}]


def test_meta_pixels_and_audiences_are_soft_and_the_days_are_not():
    got = t.read_meta_account("act_1234567", meta_pages(fail=("pixels", "audiences")), AT)
    assert got["pixels"] is None and got["lists"] is None and len(got["warnings"]) == 2 and got["warnings"][0].startswith("The pixels could not be read (Meta says no)")
    with pytest.raises(RuntimeError):
        t.read_meta_account("act_1234567", meta_pages(fail=("insights",)), AT)


def test_the_meta_requests_ask_for_the_fields_the_rules_use():
    path, params = t.meta_pixels_request("act_1")
    assert path == "/act_1/adspixels" and "last_fired_time" in params["fields"] and "is_unavailable" in params["fields"]
    path, params = t.meta_audiences_request("act_1")
    assert path == "/act_1/customaudiences" and "approximate_count_lower_bound" in params["fields"]


# ── The report and its cache ────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def report(search, **kw):
    return t.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, at=AT, **kw)


def dark(cid=ACCT["id"]):
    return {cid: day_rows(cost=50.0, conv=3.0, first=4, last=30) + day_rows(cost=50.0, conv=0.0, first=1, last=3)}


def test_the_report_names_the_account_and_what_it_found(cache):
    rep = report(searcher(days=dark()))
    a = rep["accounts"][0]
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and a["id"] == "1234567890" and a["platform"] == "google" and a["today"] == "2026-10-22"
    assert a["status"] == "fix" and [f["rule"] for f in a["findings"]] == ["no_conv"] and a["counts_read"] == {"actions": 1, "lists": 1, "pixels": None}
    assert rep["summary"] == {"counts": {"fix": 1, "check": 0, "clean": 0}, "accounts": 1, "high": 1, "medium": 0, "low": 0, "findings": 1, "dark": 1}
    assert [r["rule"] for r in rep["rules"]] == t.math.ORDER and rep["readAt"] and rep["warnings"] == [] and len(rep["notMeasured"]) == 4
    assert rep["thresholds"] == {"expected": 5, "shrinkPercent": 30, "listMin": 1000, "uploadDays": 14, "pixelHours": 48, "baselineDays": 21}


def test_a_second_ask_never_reads_google_again_and_refresh_does(cache):
    calls = []
    s = searcher(log=calls)
    report(s)
    n = len(calls)
    assert n == 6                                                                      # the days, the actions, what each recorded, the lists, the terms and the upload log
    report(s)
    assert len(calls) == n
    report(s, force=True)
    assert len(calls) == 2 * n


def test_the_lists_are_held_against_the_monitors_earlier_readings(cache):
    mon = MemStore()
    mon.snaps_put("track", "2026-10-18", {"1234567890": {"lists": {"7": {"name": "Visitors", "size": 20000}}}})
    rep = report(searcher(lists={ACCT["id"]: [list_row("7", size_search="9000", size_display="12000")]}), mon=mon)
    f = rep["accounts"][0]["findings"][0]
    assert f["rule"] == "list_shrunk" and "lost 40 percent" in f["title"]
    no_mon = t.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, at=AT, mon=None)
    assert no_mon["accounts"][0]["findings"] == []


def test_a_store_that_cannot_be_read_leaves_the_lists_listed_not_judged(cache):
    class Broken(MemStore):
        def snaps_get(self, *a):
            raise RuntimeError("down")
    rep = report(searcher(), mon=Broken())
    assert rep["accounts"][0]["findings"] == [] and [c for c in rep["accounts"][0]["checks"] if c["key"] == "lists"][0]["state"] == "unknown"


def test_an_mcc_report_puts_the_accounts_to_fix_first(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    s = searcher(days={ACCT["id"]: day_rows(), ACCT2["id"]: dark(ACCT2["id"])[ACCT2["id"]]}, actions={}, action_days={}, lists={})
    rep = t.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY, at=AT)
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"] and rep["account"]["isGroup"] is True and rep["account"]["accountCount"] == 2
    assert rep["accounts"][0]["findings"][0]["rule"] == "no_conv" and rep["summary"]["counts"] == {"fix": 1, "check": 0, "clean": 1}


def test_an_account_that_cannot_be_read_is_named_and_all_failing_is_raised(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    rep = t.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=searcher(fail=(ACCT2["id"],)), today=TODAY, at=AT)
    assert [a["name"] for a in rep["accounts"]] == ["Acme Dental"] and rep["warnings"] == ["Beta Clinic could not be read: Account 2222222222 cannot be read."]
    other = dict(group, id="mcc-1111111111-USD")
    with pytest.raises(gads.GadsError):
        t.report_for(other["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [other], search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])), today=TODAY, at=AT)


def test_a_read_that_lost_a_part_is_shown_but_not_saved_for_hours(cache):
    rep = report(searcher(fail_kinds=("lists",)))
    assert rep["warnings"] == ["The audience lists could not be read (no lists read), so that check was skipped."] and cache == {}
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.forget("")
    report(searcher())
    assert len(cache) == 1


def test_a_report_for_a_meta_account_reads_its_days_pixels_and_audiences(cache):
    rows = [meta_row("9", (TODAY - dt.timedelta(days=i)).isoformat(), leads="3" if i > 3 else "0") for i in range(1, 31)]
    rep = t.report_for("act_1234567", meta_pages_fn=meta_pages(insights=rows, pixels=[{"id": "1", "name": "Site", "last_fired_time": "2026-10-17T10:00:00+0000"}]),
                       meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"}, today=TODAY, at=AT, search_fn=searcher())
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["platform"] == "meta" and a["currency"] == "EUR" and a["counts_read"] == {"actions": None, "lists": 1, "pixels": 1}
    assert {f["rule"]: f["level"] for f in a["findings"]} == {"no_conv": "high", "pixel_stale": "high"} and a["status"] == "fix"


def test_a_spent_quota_with_nothing_saved_is_raised_and_an_account_that_is_not_position2s_is_refused(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        report(spent)
    with pytest.raises(t.BadRequest, match="not one of Position2"):
        t.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, at=AT)


def test_what_the_monitor_saves_of_an_account_is_its_open_lists_and_the_outcome():
    raw = {"lists": [{"id": "1", "name": "A", "size": 5000, "open": True}, {"id": "2", "name": "B", "size": 10, "open": False}, {"id": "3", "name": "C", "size": None, "open": True}]}
    assert t.snapshot_of(raw) == {"lists": {"1": {"name": "A", "size": 5000}}}
    res = {"status": "fix", "counts": {"high": 1, "medium": 2, "low": 3}, "findingCount": 6}
    assert t.snapshot_of(raw, res) == {"lists": {"1": {"name": "A", "size": 5000}}, "status": "fix", "high": 1, "medium": 2, "low": 3, "findings": 6}
    assert t.snapshot_of({"lists": None}) == {"lists": {}}
