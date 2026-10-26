"""Tracking & Data Health: what an account's last days say about its measurement (tracker/paid_track_math.py). No network, no clock."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_track_math as m  # noqa: E402

TODAY = dt.date(2026, 10, 20)
NOW = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)


def days(per_day, cost=50.0, recent_conv=None, recent_days=0, recent_cost=None):
    """30 whole days before TODAY: `per_day` conversions a day, except the last `recent_days` days, which hold `recent_conv` a day."""
    out = []
    for back in range(30, 0, -1):
        d = TODAY - dt.timedelta(days=back)
        recent = back <= recent_days
        out.append({"date": d.isoformat(), "cost": (recent_cost if recent and recent_cost is not None else cost), "conv": (recent_conv if recent else per_day)})
    return out


def acct(**kw):
    base = {"currency": "USD", "days": days(3), "actions": [], "lists": [], "terms": True, "uploads": {"hasUploadActions": False}, "pixels": None}
    base.update(kw)
    return base


def run(a, platform="google", history=None):
    return m.analyse(a, history=history, today=TODAY, now=NOW, platform=platform)


def rules(r):
    return [f["rule"] for f in r["findings"]]


def check(r, key):
    return next(c for c in r["checks"] if c["key"] == key)


# ── Conversions ─────────────────────────────────────────────────────────────

def test_a_steady_account_is_clean_and_says_what_it_recorded():
    r = run(acct())
    assert r["status"] == "clean" and r["findings"] == [] and check(r, "conversions")["state"] == "ok"
    assert r["headline"] == {"conv7": 21.0, "usualConv7": 21.0, "spend7": 350.0, "currency": "USD"} and "21 in the last 7 days" in check(r, "conversions")["note"]


def test_no_conversions_in_three_days_for_an_account_that_usually_makes_three_a_day_is_high():
    r = run(acct(days=days(3, recent_conv=0, recent_days=3)))
    [f] = r["findings"]
    assert f["rule"] == "no_conv" and f["level"] == "high" and f["title"] == "No conversions in the last 3 days, usually about 3 a day" and r["status"] == "fix" and r["counts"]["high"] == 1
    assert "$150 were spent in the last 3 days" in f["detail"] and f["evidence"][0] == "Last 3 days: 0 conversions, $150 spent" and "test conversion" in f["fix"]
    assert check(r, "conversions")["state"] == "problem"


def test_a_slower_account_is_judged_over_seven_days_not_three():
    quiet_three = run(acct(days=days(1.2, recent_conv=0, recent_days=3)))                 # 3.6 expected in 3 days: not enough to call a stop
    assert quiet_three["findings"] == [] and check(quiet_three, "conversions")["state"] == "ok"
    dark_week = run(acct(days=days(1.2, recent_conv=0, recent_days=7)))                    # 8.4 expected in 7 days: a stop
    assert rules(dark_week) == ["no_conv"] and "last 7 days" in dark_week["findings"][0]["title"]
    four_days = run(acct(days=days(1.2, recent_conv=0, recent_days=4)))                    # 4 days dark but the week is not: nothing to say yet
    assert four_days["findings"] == []


def test_an_account_with_too_few_conversions_is_listed_as_not_judged_never_called_fine():
    r = run(acct(days=days(0.3, recent_conv=0, recent_days=3)))
    c = check(r, "conversions")
    assert r["findings"] == [] and c["state"] == "unknown" and "too few to tell a stop from a quiet spell" in c["note"]


def test_nothing_spent_means_nothing_to_judge_even_with_no_conversions():
    r = run(acct(days=days(3, recent_conv=0, recent_days=7, recent_cost=0.0)))
    assert r["findings"] == [] and check(r, "conversions")["state"] == "unknown" and "Nothing was spent" in check(r, "conversions")["note"]


def test_a_sharp_drop_with_the_spend_going_on_is_medium_and_a_drop_with_the_spend_cut_is_a_pause_not_a_finding():
    drop = run(acct(days=days(5, recent_conv=0.5, recent_days=7)))
    [f] = drop["findings"]
    assert f["rule"] == "conv_drop" and f["level"] == "medium" and f["title"] == "Conversions fell 90 percent with the spend going on"
    paused = run(acct(days=days(5, recent_conv=0.5, recent_days=7, recent_cost=10.0)))      # spend at a fifth of its usual: someone cut it
    assert paused["findings"] == []
    mild = run(acct(days=days(5, recent_conv=2.5, recent_days=7)))                         # half of usual is not a drop to 20 percent
    assert mild["findings"] == []


# ── Conversion actions ──────────────────────────────────────────────────────

def action(aid, name, per_day, recent=None, status="ENABLED", kind="WEBPAGE"):
    return {"id": aid, "name": name, "status": status, "type": kind, "primary": True,
            "days": [{"date": (TODAY - dt.timedelta(days=b)).isoformat(), "conv": (per_day if recent is None or b > 7 else recent)} for b in range(30, 0, -1)]}


def test_an_action_that_carried_most_of_the_conversions_and_stopped_is_high_and_a_small_one_is_medium():
    big = run(acct(actions=[action("1", "Form fill", 2.0, recent=0), action("2", "Phone call", 1.0)]))
    [f] = big["findings"]
    assert f["rule"] == "action_stopped" and f["level"] == "high" and f["key"] == "action:1" and "\"Form fill\"" in f["title"] and "67 percent" in f["detail"] and "still recording" in f["detail"]
    small = run(acct(actions=[action("1", "Form fill", 1.0), action("2", "Brochure", 2.0, recent=0)]))
    assert small["findings"][0]["key"] == "action:2" and small["findings"][0]["level"] == "high"        # carrying 67 percent
    little = run(acct(actions=[action("1", "Form fill", 2.6), action("2", "Brochure", 0.4, recent=0)]))     # 0.4 a day is 2.8 a week: too quiet to judge
    assert little["findings"] == [] and "1 too quiet to judge" in check(little, "actions")["note"]
    mid = run(acct(actions=[action("1", "Form fill", 2.0), action("2", "Chat", 1.0, recent=0), action("3", "Call", 1.0)]))
    assert mid["findings"][0]["level"] == "medium" and "25 percent" in mid["findings"][0]["detail"]


def test_when_the_whole_account_stopped_the_actions_are_not_listed_one_by_one_and_a_hidden_action_says_so():
    dark = run(acct(days=days(3, recent_conv=0, recent_days=7), actions=[action("1", "Form fill", 2.0, recent=0), action("2", "Phone call", 1.0, recent=0)]))
    assert rules(dark) == ["no_conv"]
    hidden = run(acct(actions=[action("1", "Old goal", 1.0, recent=0, status="HIDDEN"), action("2", "Form", 2.0)]))
    assert "hidden (it no longer counts)" in hidden["findings"][0]["detail"]
    removed = run(acct(actions=[action("1", "Gone", 1.0, recent=0, status="REMOVED"), action("2", "Form", 2.0)]))
    assert removed["findings"] == []


def test_actions_that_could_not_be_read_are_said_so():
    r = run(acct(actions=None))
    assert check(r, "actions") == {"key": "actions", "label": "Each conversion action records", "state": "unknown", "note": "The conversion actions could not be read."}


# ── Audience lists ──────────────────────────────────────────────────────────

def snap(day_back, **sizes):
    return {"day": (TODAY - dt.timedelta(days=day_back)).isoformat(), "lists": {k: {"name": k, "size": v} for k, v in sizes.items()}}


def lst(lid, size, open_=True, kind="REMARKETING"):
    return {"id": lid, "name": "List " + lid, "size": size, "open": open_, "type": kind}


def test_a_list_that_lost_a_third_of_its_people_is_medium_and_one_below_googles_minimum_is_high():
    hist = [snap(8, a=10000, b=5000, c=3000), snap(1, a=9000, b=5000, c=3000)]
    r = run(acct(lists=[lst("a", 5000), lst("b", 4900), lst("c", 600)]), history=hist)
    got = {f["key"]: f for f in r["findings"]}
    assert set(got) == {"list:a", "list:c"} and got["list:a"]["rule"] == "list_shrunk" and got["list:a"]["level"] == "medium" and "lost 50 percent" in got["list:a"]["title"]
    assert got["list:c"]["rule"] == "list_lost" and got["list:c"]["level"] == "high" and "under the 1,000 people" in got["list:c"]["detail"]
    assert r["findings"][0]["key"] == "list:c"                                                          # high first


def test_an_empty_list_is_lost_and_the_largest_earlier_reading_is_the_one_held_against():
    r = run(acct(lists=[lst("a", 0)]), history=[snap(10, a=4000), snap(3, a=1500)])
    assert r["findings"][0]["rule"] == "list_lost" and "fell from 4,000 to 0" in r["findings"][0]["title"] and "is empty now" in r["findings"][0]["detail"]


def test_a_closed_list_a_new_list_a_tiny_list_and_an_old_reading_are_not_judged():
    hist = [snap(20, a=9000), snap(3, b=100, c=8000)]
    r = run(acct(lists=[lst("a", 1), lst("b", 10), lst("c", 100, open_=False), lst("d", 50)]), history=hist)
    assert r["findings"] == [] and check(r, "lists")["state"] == "ok"                                    # a: its reading is 20 days old; b: under 500 earlier; c: closed; d: no reading


def test_without_an_earlier_reading_the_lists_are_listed_not_judged_and_unreadable_lists_say_so():
    r = run(acct(lists=[lst("a", 5000)]), history=[])
    assert r["findings"] == [] and check(r, "lists")["state"] == "unknown" and "compares from tomorrow" in check(r, "lists")["note"]
    assert check(run(acct(lists=None)), "lists")["note"] == "The audience lists could not be read."
    assert check(run(acct(lists=[])), "lists")["note"] == "This account has no audience list."


def test_a_meta_audience_that_shrinks_is_judged_the_same_way_but_a_size_under_googles_minimum_alone_is_not_lost():
    hist = [snap(5, a=20000, b=5000)]
    r = run(acct(lists=[lst("a", 9000), lst("b", 800)]), history=hist, platform="meta")
    got = {f["key"]: f for f in r["findings"]}
    assert got["list:a"]["rule"] == "list_shrunk" and got["list:b"]["rule"] == "list_shrunk"            # 800 of 5000 is a drop; Google's 1,000 rule is Google's only


# ── Customer data, offline uploads and the pixel ────────────────────────────

def test_unaccepted_customer_data_terms_are_low_and_medium_when_the_account_has_a_customer_list():
    plain = run(acct(terms=False))
    assert plain["findings"][0]["rule"] == "terms" and plain["findings"][0]["level"] == "low" and plain["status"] == "check"
    crm = run(acct(terms=False, lists=[lst("a", 5000, kind="CRM_BASED")]), history=[snap(2, a=5000)])
    assert crm["findings"][0]["level"] == "medium" and "(this account has one)" in crm["findings"][0]["detail"] and crm["status"] == "fix"
    assert check(run(acct(terms=None)), "terms")["state"] == "unknown" and check(run(acct(terms=True)), "terms")["state"] == "ok"


def test_offline_conversions_that_stopped_arriving_are_medium_with_the_reason_in_words():
    old = run(acct(uploads={"hasUploadActions": True, "status": "GOOD", "lastUpload": "2026-09-25 10:00:00"}))
    assert old["findings"][0]["rule"] == "upload_stale" and "the last upload was 25 days ago" in old["findings"][0]["title"]
    flagged = run(acct(uploads={"hasUploadActions": True, "status": "NO_RECENT_UPLOAD", "lastUpload": "2026-10-19 10:00:00"}))
    assert "Google's log says no recent upload" in flagged["findings"][0]["title"]
    never = run(acct(uploads={"hasUploadActions": True, "status": "", "lastUpload": ""}))
    assert "no upload is on record" in never["findings"][0]["title"]
    fine = run(acct(uploads={"hasUploadActions": True, "status": "GOOD", "lastUpload": "2026-10-18 10:00:00"}))
    assert fine["findings"] == [] and "2 days ago" in check(fine, "uploads")["note"]
    assert run(acct(uploads={"hasUploadActions": False}))["findings"] == [] and check(run(acct(uploads=None)), "uploads")["state"] == "unknown"


def px(pid, fired, unavailable=False):
    return {"id": pid, "name": "Pixel " + pid, "lastFired": fired, "unavailable": unavailable}


def test_a_pixel_that_has_been_silent_is_medium_high_with_conversions_gone_and_an_unavailable_one_is_high():
    two_days = run(acct(pixels=[px("1", "2026-10-17T08:00:00+0000")]), platform="meta")
    [f] = two_days["findings"]
    assert f["rule"] == "pixel_stale" and f["level"] == "medium" and f["title"] == "The pixel \"Pixel 1\" last fired 3 days ago"
    gone = run(acct(days=days(3, recent_conv=0, recent_days=3), pixels=[px("1", "2026-10-15T08:00:00+0000")]), platform="meta")
    assert {f["rule"]: f["level"] for f in gone["findings"]} == {"no_conv": "high", "pixel_stale": "high"}
    off = run(acct(pixels=[px("1", "2026-10-20T10:00:00+0000", unavailable=True)]), platform="meta")
    assert off["findings"][0]["rule"] == "pixel_unavailable" and off["findings"][0]["level"] == "high"
    never = run(acct(pixels=[px("1", "")]), platform="meta")
    assert never["findings"][0]["title"] == "The pixel \"Pixel 1\" has never fired"
    ok = run(acct(pixels=[px("1", "2026-10-20T06:00:00+0000")]), platform="meta")
    assert ok["findings"] == [] and "fired in the last 48 hours" in check(ok, "pixels")["note"]


def test_a_silent_pixel_on_an_account_that_spent_nothing_means_nothing_and_no_pixel_is_not_a_fault():
    quiet = run(acct(days=days(3, recent_cost=0.0, recent_conv=0, recent_days=7), pixels=[px("1", "2026-09-01T00:00:00+0000")]), platform="meta")
    assert quiet["findings"] == [] and "Nothing was spent" in check(quiet, "pixels")["note"]
    none = run(acct(pixels=[]), platform="meta")
    assert none["findings"] == [] and "lead forms or messages only" in check(none, "pixels")["note"]
    assert check(run(acct(pixels=None), platform="meta"), "pixels")["state"] == "unknown"


# ── The account as a whole ──────────────────────────────────────────────────

def test_the_checks_differ_by_platform_and_findings_are_ordered_high_first_with_counts_and_rules():
    g = run(acct(terms=False, days=days(3, recent_conv=0, recent_days=3)))
    assert [c["key"] for c in g["checks"]] == ["conversions", "actions", "lists", "terms", "uploads"]
    assert [f["rule"] for f in g["findings"]] == ["no_conv", "terms"] and g["counts"] == {"high": 1, "medium": 0, "low": 1} and g["findingCount"] == 2
    assert g["byRule"] == [{"rule": "no_conv", "label": "Conversions stopped", "level": "high", "findings": 1}, {"rule": "terms", "label": "Customer data terms not accepted", "level": "low", "findings": 1}]
    mt = run(acct(pixels=[px("1", "2026-10-20T06:00:00+0000")]), platform="meta")
    assert [c["key"] for c in mt["checks"]] == ["conversions", "pixels", "lists"]


def test_every_rule_has_a_level_an_area_a_label_and_a_fix_and_what_is_not_measured_is_written_down():
    assert set(m.ORDER) == set(m.RULES)
    for k, r in m.RULES.items():
        assert r["level"] in m.LEVEL_RANK and r["area"] and r["label"] and len(r["fix"]) > 60 and "—" not in r["fix"] + r["label"], k
    assert len(m.NOT_MEASURED) == 4 and all("—" not in t for t in m.NOT_MEASURED)
