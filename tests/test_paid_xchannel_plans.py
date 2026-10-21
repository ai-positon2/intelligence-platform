"""Cross-Channel & Attribution Analysis: how to act on a finding and what it is worth (tracker/paid_xchannel_plans.py), and the read of how late Google's conversions come.

The figure that matters is how much of Google's count lies beyond Meta's window, since that is the part of the cost-per-conversion gap that is the setting and not the ads. It
is read from Google's own lag buckets, sized against the platform's cost and conversions, and said to be an estimate on a window only where Google could be read. The other figure
is the most a 15 percent budget shift could buy, which is an upper bound and says so.
"""
import pytest

from tests.gads_fakes import FakeStore
from tests.test_paid_xchannel import fresh  # noqa: F401  (the autouse fixture that stands in for Meta's account lookup)
from tests.test_paid_xchannel_findings import channel, report
from tracker import paid_gads
from tracker import paid_xchannel as xc
from tracker import paid_xchannel_findings as fd
from tracker import paid_xchannel_plans as pl
from tracker import paid_xchannel_settings as st

EM, EN = chr(0x2014), chr(0x2013)


# ── The lag read ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,day", [("LESS_THAN_ONE_DAY", 0), ("ONE_TO_TWO_DAYS", 1), ("SIX_TO_SEVEN_DAYS", 6), ("SEVEN_TO_EIGHT_DAYS", 7), ("THIRTEEN_TO_FOURTEEN_DAYS", 13),
                                      ("FOURTEEN_TO_TWENTY_ONE_DAYS", 14), ("TWENTY_ONE_TO_THIRTY_DAYS", 21), ("THIRTY_TO_FORTY_FIVE_DAYS", 30), ("FORTY_FIVE_TO_SIXTY_DAYS", 45),
                                      ("SIXTY_TO_NINETY_DAYS", 60), ("UNKNOWN", None), ("UNSPECIFIED", None), ("", None), (None, None), ("SOMETHING_ELSE_TO_TWO_DAYS", None)])
def test_a_lag_bucket_is_read_as_the_day_it_starts_on(name, day):
    assert st.bucket_days(name) == day


def lag_rows(**by):
    return [{"segments": {"conversionLagBucket": k.upper()}, "metrics": {"conversions": v}} for k, v in by.items()]


def test_the_lag_is_summed_by_the_day_each_bucket_starts_and_nothing_gives_none():
    seen = []
    out = st.read_lag(lambda g: seen.append(g) or lag_rows(less_than_one_day=40, one_to_two_days=30, seven_to_eight_days=20, thirty_to_forty_five_days=10), "2026-09-01", "2026-09-30")
    assert out == {"buckets": {"0": 40.0, "1": 30.0, "7": 20.0, "30": 10.0}, "total": 100.0}
    assert "segments.conversion_lag_bucket" in seen[0] and "BETWEEN '2026-09-01' AND '2026-09-30'" in seen[0]
    assert st.read_lag(lambda g: [], "a", "b") is None
    assert st.read_lag(lambda g: lag_rows(unknown=5), "a", "b") is None                         # buckets it cannot place are not a lag
    mixed = st.read_lag(lambda g: lag_rows(unknown=50, one_to_two_days=50), "a", "b")
    assert mixed["total"] == 100.0 and mixed["buckets"] == {"1": 50.0}                           # the share is of everything, so a half unplaced is not read as all of it


def test_the_share_beyond_a_window_counts_the_buckets_that_start_on_or_after_it():
    lag = {"buckets": {"0": 40.0, "1": 30.0, "6": 10.0, "7": 15.0, "30": 5.0}, "total": 100.0}
    assert st.window_share(lag, 7) == 0.2 and st.window_share(lag, 1) == 0.6 and st.window_share(lag, 30) == 0.05 and st.window_share(lag, 0) == 1.0
    assert st.window_share(None, 7) is None and st.window_share({"buckets": {}, "total": 0}, 7) is None and st.window_share(lag, None) is None


def test_settings_read_the_lag_beside_the_actions_and_a_failure_leaves_only_the_lag_out():
    def search(gaql):
        if "FROM conversion_action" in gaql:
            return [st.gread.CONVERSIONS_GAQL and {"conversionAction": {"resourceName": "customers/1/conversionActions/1", "id": "1", "name": "Booked", "status": "ENABLED", "type": "WEBSITE",
                                                                         "category": "PURCHASE", "primaryForGoal": True, "includeInConversionsMetric": True, "clickThroughLookbackWindowDays": "30",
                                                                         "viewThroughLookbackWindowDays": "1", "attributionModelSettings": {"attributionModel": "GOOGLE_SEARCH_ATTRIBUTION_DATA_DRIVEN"}}}]
        if "conversion_lag_bucket" in gaql:
            return lag_rows(less_than_one_day=60, seven_to_eight_days=40)
        return []
    target = [{"platform": "google", "id": "1112223334", "name": "A", "currency": "USD"}]
    out, warns = xc.read_settings(target, "2026-09-01", "2026-09-30", store=FakeStore(), search_fn=search)
    assert out["googleLag"] == {"buckets": {"0": 60.0, "7": 40.0}, "total": 100.0} and out["google"] and not warns
    paid_gads.forget()

    def broken(gaql):
        if "conversion_lag_bucket" in gaql:
            raise RuntimeError("not allowed")
        return search(gaql)
    out2, warns2 = xc.read_settings(target, "2026-09-02", "2026-09-30", store=FakeStore(), search_fn=broken)
    assert out2["google"] and out2.get("googleLag") is None and not warns2                       # the actions still stand, and no alarm is raised for a nicety
    paid_gads.forget()


# ── The plans ─────────────────────────────────────────────────────────────

NOTE = lambda id_: {"id": id_, "level": "high", "title": "t", "text": "x"}      # noqa: E731


def finding(r, id_):
    return next(f for f in fd.findings(r) if f["id"] == id_)


def plan(r, id_):
    return pl.plan_for(finding(r, id_), r)


def text(p):
    return " ".join(p["steps"])


def r_window(google_days=30, meta_days=7, later=0.4):
    return report(channel(1000, 50), channel(2000, 50), notes=[NOTE("click-window")], window={"google": google_days, "meta": meta_days, "later": later})


def test_the_window_plan_sizes_the_part_of_googles_count_that_lies_beyond_metas_window():
    p = plan(r_window(), "attr-click-window")
    assert p["worth"] == "About 40% of Google's conversions came 7 days or more after the click, which a 7-day window would not count. On that window Google's cost per conversion would be about $33.33 instead of $20.00."
    t = text(p)
    assert "Days to conversion" in t and "Time lag report" in t and "click-through conversion window to 7 days" in t and "one action at a time" in t and "longest it offers (7)" in t
    assert p["note"] is None


def test_without_the_lag_the_window_plan_says_it_cannot_size_the_gap():
    p = plan(r_window(later=None), "attr-click-window")
    assert p["worth"] is None and "could not be read, so the effect of the window cannot be sized here" in p["note"] and "7 days" in p["note"]


def test_when_metas_window_is_the_longer_one_meta_is_the_one_to_shorten():
    p = plan(r_window(google_days=1, meta_days=7, later=None), "attr-click-window")
    assert "Meta can only be made to count over a shorter window" in text(p) and "nearest click window Meta offers to Google's 1 days (1 or 7)" in text(p) and p["worth"] is None
    assert "restarts learning" in text(p)


def test_a_window_plan_needs_both_windows():
    r = report(channel(1000, 50), channel(2000, 50), notes=[NOTE("click-window")], window=None)
    assert plan(r, "attr-click-window") is None
    assert plan(report(channel(1000, 50), channel(2000, 50), notes=[NOTE("click-window")], window={"google": None, "meta": 7}), "attr-click-window") is None


def test_each_attribution_note_has_its_steps():
    for nid, word in (("view-through", "Compare attribution settings"), ("models", "auto-tagging"), ("goal-mismatch", "Primary"), ("secondary", "primary action")):
        r = report(channel(1000, 50), channel(2000, 50), notes=[NOTE(nid)], window=None)
        p = plan(r, "attr-" + nid)
        assert p and word in text(p) and len(p["steps"]) >= 2, nid
    r = report(channel(1000, 50), channel(2000, 50), notes=[NOTE("models")], window=None)
    assert "utm_source=meta&utm_medium=paid&utm_campaign={{campaign.name}}" in text(plan(r, "attr-models"))
    assert "view-through" in plan(report(channel(1000, 50), channel(2000, 50), notes=[NOTE("view-through")]), "attr-view-through")["note"]
    assert "All conversions measure" in plan(report(channel(1000, 50), channel(2000, 50), notes=[NOTE("secondary")]), "attr-secondary")["note"]


def test_the_cost_gap_plan_moves_fifteen_percent_and_says_the_gain_is_an_upper_bound():
    r = report(channel(1000, 50), channel(2000, 50))                                  # Google $20 a conversion, Meta $40
    p = plan(r, "cpa-gap")
    assert p["steps"][0] == "In Ads Manager open the campaign's ad sets, sort by cost per result, and lower the budgets of the ad sets with the highest cost per result by about 15% in all."
    assert p["steps"][1] == "In Google Ads open Campaigns, sort by Cost, raise the daily budgets of the campaigns with the lowest cost per conversion by about the same amount of money in all."
    assert "two to four weeks" in p["steps"][2] and "your own system records" in p["steps"][3]
    # 15% of Meta's $2,000 is $300: at $20 it buys 15, at $40 it buys 7.5, so at most 7.5 more
    assert p["worth"] == "Moving 15% of Meta Ads's spend ($300.00) to Google Ads at its average cost would buy about 7.5 more conversions than it does now. It is an upper bound: cost per conversion rises as a platform takes more budget, so the test is how much of it survives."


def test_the_cost_gap_plan_follows_which_platform_is_the_dearer():
    r = report(channel(2000, 50), channel(1000, 50))                                   # now Google is the dear one
    p = plan(r, "cpa-gap")
    assert p["steps"][0].startswith("In Google Ads open Campaigns, sort by Cost, lower the daily budgets") and p["steps"][1].startswith("In Ads Manager open the campaign's ad sets") and "raise" in p["steps"][1]


def test_the_overlap_findings_say_how_to_set_targets_from_the_scaled_costs():
    rec = {"conversions": {"ratio": 1.5, "over": True, "claimed": 150, "actual": 100, "excess": 50, "perPlatform": {"google": {"cpa": 30.0}, "meta": {"cpa": 50.0}}}, "blendedCpa": 40.0, "spend": 4000, "value": None}
    r = report(channel(1000, 50), channel(2000, 50), reconcile=rec)
    p = plan(r, "overlap")
    assert p and "blended cost per recorded conversion" in text(p) and "no more than 10 to 15 percent at a time" in text(p)


def test_findings_without_a_how_have_no_plan_and_every_plan_is_clean():
    assert pl.plan_for({"id": "idle-meta"}, {}) is None and pl.plan_for({"id": "attr-nonsense"}, {}) is None
    blobs = []
    for nid in ("click-window", "view-through", "models", "goal-mismatch", "secondary"):
        blobs.append(repr(plan(r_window() if nid == "click-window" else report(channel(1000, 50), channel(2000, 50), notes=[NOTE(nid)], window=None), "attr-" + nid)))
    blobs.append(repr(plan(report(channel(1000, 50), channel(2000, 50)), "cpa-gap")))
    for b in blobs:
        assert EM not in b and EN not in b and "None, " not in b.split("'worth'")[0] and "{" not in b.replace("{{", "").replace("}}", "").replace("{'", "").replace("'}", ""), b[:200]


# ── Through the report ────────────────────────────────────────────────────

def test_the_report_carries_the_window_and_the_plan_of_each_finding_it_can_help_with():
    from tests.test_paid_xchannel import run, settings_fns
    fns = settings_fns()
    inner = fns["search_fn"]
    fns["search_fn"] = lambda gaql: lag_rows(less_than_one_day=54, seven_to_eight_days=36) if "conversion_lag_bucket" in gaql else inner(gaql)       # 40% of Google's conversions came after 7 days
    r = run(**fns)
    assert r["attribution"]["window"] == {"google": 30, "meta": 7, "later": 0.4}
    f = next(x for x in r["findings"] if x["id"] == "attr-click-window")
    assert f["plan"]["worth"] == "About 40% of Google's conversions came 7 days or more after the click, which a 7-day window would not count. On that window Google's cost per conversion would be about $33.33 instead of $20.00."
    assert all("plan" in x for x in r["findings"] if x["id"] in ("attr-view-through", "attr-models", "cpa-gap"))
    assert not any("plan" in x for x in r["findings"] if x["id"].startswith("idle-"))


def test_without_the_lag_the_report_still_has_the_window_and_says_the_gap_cannot_be_sized():
    from tests.test_paid_xchannel import run, settings_fns
    r = run(**settings_fns())
    assert r["attribution"]["window"] == {"google": 30, "meta": 7, "later": None}
    assert "cannot be sized here" in next(x for x in r["findings"] if x["id"] == "attr-click-window")["plan"]["note"]
    paid_gads.forget()
    assert run()["attribution"]["window"] is None                                                # nothing about either platform's settings was read
