"""Test & Experiment Tracker: the report (tracker/paid_exp.py): verdicts, next steps, the shared cache."""
import datetime as dt

import pytest

from tests.gads_fakes import FakeStore
from tests.test_paid_exp_read import CUST, ad_row, arm_row, camp_row, day_row, exp_row
from tracker import paid_exp as ex
from tracker import paid_exp_read as rd
from tracker import paid_geo, paid_gads
from tracker import paid_gads_segments as gads

TODAY = dt.date(2026, 10, 18)
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)
ACCOUNT_ID = "1112223334"
ACCOUNT = {"id": ACCOUNT_ID, "name": "Acme Dental", "currency": "USD", "timeZone": "America/New_York"}


@pytest.fixture(autouse=True)
def fresh():
    paid_geo.clear_cache()
    paid_gads.forget()
    yield
    paid_gads.forget()


def tot(impr, clicks, conv, cost=100.0):
    return {"impressions": impr, "clicks": clicks, "cost": cost, "conversions": conv, "value": 0.0}


def made(state="running", start="2026-09-20", end="2026-10-30", control=None, trial=None, split=(50, 50), measured=True, window=("2026-09-20", "2026-10-17"), typ="SEARCH_CUSTOM", extra=0):
    def arm_(name, is_control, total, sp):
        return {"name": name, "control": is_control, "split": sp, "campaigns": [{"id": "1", "name": name, "type": "SEARCH"}],
                "results": {"days": {"2026-10-01": [10, 1, 1.0, 0.1, 0.0]}, "total": dict(total)} if total else None}
    arms = [arm_("Base", True, control, split[0]), arm_("Trial", False, trial, split[1])]
    for i in range(extra):
        arms.append(arm_("Trial %d" % (i + 2), False, trial, split[1]))
    return {"resource": CUST + "/experiments/1", "id": "1", "name": "Headline test", "description": "", "status": "ENABLED", "state": state, "type": typ, "typeLabel": "Search custom",
            "start": start, "end": end, "window": list(window) if window else None, "clipped": False, "arms": arms, "measured": measured}


def build(**kw):
    return ex.build_experiment(made(**kw), TODAY)


WIN = dict(control=tot(20000, 1000, 50), trial=tot(20000, 1000, 90))
LOSE = dict(control=tot(20000, 1000, 90), trial=tot(20000, 1000, 50))
FLAT = dict(control=tot(20000, 1000, 50), trial=tot(20000, 1000, 52))
EARLY = dict(control=tot(8000, 40, 3), trial=tot(8000, 50, 4))


# ── experiments: verdict and what to do ─────────────────────────────────────

def test_a_running_experiment_that_is_ahead_says_apply():
    r = build(**WIN)
    assert r["verdict"] == "winner" and r["action"]["kind"] == "apply"
    assert "ahead on conversion rate (5% against 9%" in r["action"]["text"] and "sure)" in r["action"]["text"] and r["measured"]
    assert r["daysRead"] == 28 and r["spend"] == 200.0 and r["daysRunning"] == 28 and r["daysLeft"] == 12 and not r["overdue"]


def test_one_that_is_behind_says_end_it():
    r = build(**LOSE)
    assert r["verdict"] == "behind" and r["action"]["kind"] == "end" and "behind on conversion rate" in r["action"]["text"] and "End it" in r["action"]["text"]


def test_no_difference_early_says_let_it_run_and_how_small_a_gap_it_could_see():
    r = build(start="2026-10-01", window=("2026-10-01", "2026-10-17"), **FLAT)
    assert r["verdict"] == "no_difference" and r["action"]["kind"] == "wait" and "Let it run" in r["action"]["text"]
    assert "could only have told apart gaps of" in r["action"]["text"]


def test_no_difference_after_four_weeks_says_end_it():
    r = build(start="2026-09-20", **FLAT)
    assert r["action"]["kind"] == "end" and "after 28 days" in r["action"]["text"] and "simpler" in r["action"]["text"]


def test_no_difference_at_27_days_is_still_let_it_run():
    r = build(start="2026-09-21", window=("2026-09-21", "2026-10-17"), **FLAT)
    assert r["action"]["kind"] == "wait"


def test_too_early_says_what_it_needs_and_how_long():
    r = build(start="2026-10-08", window=("2026-10-08", "2026-10-17"), **EARLY)
    assert r["verdict"] == "too_early" and r["action"]["kind"] == "wait"
    assert "60 more clicks in each arm and 23 more conversions" in r["action"]["text"] and "about 33 days" in r["action"]["text"]


def test_too_early_that_will_never_get_there_is_stuck():
    r = build(start="2026-10-08", window=("2026-10-08", "2026-10-17"), control=tot(8000, 0, 0), trial=tot(8000, 50, 5))
    assert r["action"]["kind"] == "stuck" and "enabled and have budget" in r["action"]["text"]


def test_too_early_for_the_days_alone_says_a_day_count():
    r = build(start="2026-10-12", window=("2026-10-12", "2026-10-17"), control=tot(50000, 2000, 400), trial=tot(50000, 2000, 400))
    assert r["verdict"] == "too_early" and "1 more day" in r["action"]["text"]


def test_an_arm_with_no_impressions_says_check_it():
    r = build(control=tot(0, 0, 0, 0.0), trial=tot(5000, 100, 5))
    assert r["verdict"] == "no_data" and r["action"]["kind"] == "check" and "control has no impressions" in r["action"]["text"] and "enabled and have budget" in r["action"]["text"]


def test_an_experiment_past_its_end_date_needs_a_decision_whatever_it_found():
    short = dict(start="2026-10-08", window=("2026-10-08", "2026-10-17"))
    for kw, want in ((WIN, "ahead"), (LOSE, "behind"), (FLAT, "No difference"), (dict(EARLY, **short), "Too early")):
        r = build(end="2026-10-10", **kw)
        assert r["overdue"] and r["action"]["kind"] == "decide", want
        assert "Past its end date (Oct 10) and still running." in r["action"]["text"] and want in r["action"]["text"]
    assert build(end="2026-10-10", start="2026-10-08", window=("2026-10-08", "2026-10-17"), **EARLY)["action"]["text"].endswith("End it or extend its dates.")


def test_an_experiment_ending_within_a_week_says_so_and_keeps_its_own_action():
    r = build(end="2026-10-22", **WIN)
    assert r["action"]["text"].startswith("Ends in 4 days. ") and r["action"]["kind"] == "apply"
    assert build(end="2026-10-25", **WIN)["action"]["text"].startswith("Ends in 7 days. ")
    assert not build(end="2026-10-26", **WIN)["action"]["text"].startswith("Ends in")
    assert build(end="2026-10-19", **WIN)["action"]["text"].startswith("Ends in 1 day. ")


def test_an_experiment_with_no_end_date_never_runs_late():
    r = build(end=None, **WIN)
    assert r["overdue"] is False and r["daysLeft"] is None


def test_an_ended_experiment_is_a_record_with_how_it_finished():
    r = build(state="ended", end="2026-10-10", **WIN)
    assert r["action"] == {"kind": "record", "text": "Ended. The trial finished ahead."} and not r["overdue"] and r["daysLeft"] is None
    assert build(state="ended", end="2026-10-10", **LOSE)["action"]["text"] == "Ended. The trial finished behind."
    assert build(state="ended", end="2026-10-10", **FLAT)["action"]["text"] == "Ended. It found no difference."
    assert build(state="ended", end="2026-10-10", start="2026-10-08", window=("2026-10-08", "2026-10-17"), **EARLY)["action"]["text"] == "Ended."


def test_an_applied_experiment_says_it_was_applied():
    r = build(state="applied", **WIN)
    assert r["action"]["kind"] == "record" and r["action"]["text"].startswith("Applied to the campaign.")


def test_an_experiment_being_set_up_says_when_it_starts():
    r = build(state="setting up", start="2026-11-01", end="2026-11-30", window=None, measured=False)
    assert r["action"] == {"kind": "wait", "text": "Not started yet (set to start on Nov 1)."} and r["verdict"] == "not_measured"
    assert build(state="setting up", start=None, end=None, window=None, measured=False)["action"]["text"] == "Not started yet."


def test_an_unknown_state_is_recorded_as_is():
    assert build(state="paused somehow", **WIN)["action"] == {"kind": "record", "text": "Status: paused somehow."}


def test_an_experiment_whose_arms_are_not_reported_is_sent_to_the_screen():
    r = build(measured=False, control=None, trial=None, typ="AD_VARIATION")
    assert r["verdict"] == "not_measured" and r["comparison"] is None and r["action"]["kind"] == "manual" and "Google Ads screen" in r["action"]["text"]
    r2 = build(measured=False, control=None, trial=None, end="2026-10-10")
    assert r2["action"]["text"].startswith("Past its end date (Oct 10) and still running. Google does not report")


def test_an_arm_that_got_the_wrong_share_of_the_traffic_is_flagged():
    r = build(control=tot(30000, 1000, 50), trial=tot(10000, 1000, 90))
    assert "25% of the impressions against a 50% share" in r["splitNote"]
    assert build(**WIN)["splitNote"] is None
    assert build(control=tot(550, 20, 1), trial=tot(100, 5, 0))["splitNote"] is None               # too few impressions to say
    assert build(control=tot(30000, 1000, 50), trial=tot(10000, 1000, 90), split=(None, 50))["splitNote"] is None
    assert build(control=tot(30000, 1000, 50), trial=tot(10000, 1000, 90), split=(75, 25))["splitNote"] is None    # 25 of 100 is what a 75/25 split promises


def test_the_split_slack_is_fifteen_points():
    assert build(control=tot(35000, 1000, 50), trial=tot(15000, 1000, 90))["splitNote"] is not None   # 30% against 50%: 20 points out
    assert build(control=tot(12000, 1000, 50), trial=tot(8000, 1000, 90))["splitNote"] is None        # 40% against 50%
    assert build(control=tot(6500, 1000, 50), trial=tot(3500, 1000, 90))["splitNote"] is None         # 35% against 50%: exactly 15 points is allowed
    assert build(control=tot(6600, 1000, 50), trial=tot(3400, 1000, 90))["splitNote"] is not None     # 34%: 16 points is not


def test_more_than_one_trial_arm_is_compared_on_the_first_and_says_so():
    r = build(extra=1, **WIN)
    assert r["extraTrials"] == 1 and r["armCount"] == 3 and r["verdict"] == "winner"


def test_the_arms_carry_their_numbers_and_their_days():
    r = build(**WIN)
    control = r["arms"][0]
    assert control["control"] and control["total"]["convRate"] == 5.0 and control["total"]["ctr"] == 5.0 and control["days"] == [["2026-10-01", 10, 1, 1.0, 0.1, 0.0]]


# ── ad copy tests ───────────────────────────────────────────────────────────

def ad(i, impr, clicks, conv, cost=50.0, heads=None):
    return {"id": str(i), "type": "RESPONSIVE_SEARCH_AD", "headlines": heads if heads is not None else ["Headline %d" % i, "Second", "Third", "Fourth"], "descriptions": ["Desc"], "url": "https://x.com",
            "strength": "GOOD", "impressions": impr, "clicks": clicks, "cost": cost, "conversions": conv, "value": 0.0}


def group(*ads, name="Implants", campaign="Search Implants"):
    return {"id": "5", "name": name, "campaign": campaign, "campaignId": "1", "campaignType": "SEARCH", "ads": list(ads), "adCount": len(ads), "cost": round(sum(a["cost"] for a in ads), 2)}


def test_a_leader_that_beats_all_says_pause_the_rest():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50), ad(3, 20000, 1000, 55)))
    assert t["verdict"] == "winner" and t["leader"] == "1" and t["action"]["kind"] == "pause"
    assert "Headline 1 | Second | Third is ahead of every other ad on conversion rate. Pause or replace the 2 ads behind it" in t["action"]["text"]
    assert [a["id"] for a in t["ads"]] == ["1", "3", "2"] or [a["id"] for a in t["ads"]][0] == "1"
    assert t["ads"][0]["verdict"] == "winner" and {a["verdict"] for a in t["ads"][1:]} == {"behind"} and t["ads"][1]["relative"] < 0


def test_a_partial_result_names_how_many_are_behind():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 150), ad(2, 20000, 1000, 148), ad(3, 20000, 1000, 60)))
    assert t["verdict"] == "partial" and t["action"]["kind"] == "pause" and "ahead of 1 of the other ads" in t["action"]["text"] and "the 1 ad that is behind it" in t["action"]["text"]


def test_no_difference_between_ads_says_how_small_a_gap_it_could_see():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 50), ad(2, 20000, 1000, 52)))
    assert t["verdict"] == "no_difference" and t["action"]["kind"] == "wait" and t["detectable"] and "could only have told apart gaps of" in t["action"]["text"]


def test_no_difference_with_too_many_ads_says_cut_it_down():
    t = ex.build_ad_test(group(*[ad(i, 20000, 1000, 50 + i % 2) for i in range(1, 6)]))
    assert t["verdict"] == "no_difference" and t["action"]["kind"] == "end" and "5 live ads" in t["action"]["text"] and "best two or three" in t["action"]["text"]
    four = ex.build_ad_test(group(*[ad(i, 20000, 1000, 50 + i % 2) for i in range(1, 5)]))
    assert four["action"]["kind"] == "wait"


def test_too_early_names_the_missing_thing():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 900, 40, 2)))
    assert t["verdict"] == "too_early" and t["action"]["kind"] == "wait"
    assert "Too early to call" in t["action"]["text"] and "clicks" in t["action"]["text"]


def test_too_early_for_the_ones_with_no_conversions_names_impressions():
    t = ex.build_ad_test(group(ad(1, 900, 40, 0), ad(2, 800, 30, 0)))
    assert "2 ads have not had enough impressions yet (1000 each)" in t["action"]["text"]
    one = ex.build_ad_test(group(ad(1, 20000, 1000, 0), ad(2, 800, 30, 0)))
    assert "1 ad has not had enough impressions yet" in one["action"]["text"]
    enough = [{"id": "1", "verdict": "too_early", "numbers": {"clicks": 150, "impressions": 900}}, {"id": "2", "verdict": "too_early", "numbers": {"clicks": 120, "impressions": 800}}]
    conv = ex._ad_action(group(), {"verdict": "too_early", "days": 30, "metric": "convRate", "metricLabel": "conversion rate"}, enough)
    assert "too few conversions to compare (a test needs 30)" in conv["text"]
    young = ex.build_ad_test(dict(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50)), ads=group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50))["ads"]))
    assert young["verdict"] == "winner"


def test_ads_without_two_shown_ads_say_there_is_nothing_to_compare():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 0, 0, 0)))
    assert t["verdict"] == "too_early" and ("Fewer than two" in t["action"]["text"] or "Too early" in t["action"]["text"])
    z = ex.build_ad_test(group(ad(1, 0, 0, 0), ad(2, 0, 0, 0)))
    assert z["verdict"] == "no_data" and "Fewer than two of these ads were shown" in z["action"]["text"]


def test_an_ad_with_no_headlines_is_named_by_its_id():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 100, heads=[]), ad(2, 20000, 1000, 50, heads=[])))
    assert t["ads"][0]["label"] == "Ad 1"


def test_the_ads_come_back_leader_first_then_by_what_they_spent():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 50, cost=10), ad(2, 20000, 1000, 100, cost=20), ad(3, 20000, 1000, 55, cost=30)))
    assert [a["id"] for a in t["ads"]] == ["2", "3", "1"]
    assert t["ads"][0]["numbers"]["convRate"] == 10.0 and t["ads"][0]["strength"] == "good"


def test_the_test_carries_its_group_and_the_bar():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50), ad(3, 20000, 1000, 55)))
    assert t["name"] == "Implants" and t["campaign"] == "Search Implants" and t["adCount"] == 3 and t["shown"] == 3 and t["eligible"] == 3 and t["bar"] == 0.025 and t["spend"] == 150.0


# ── the report ──────────────────────────────────────────────────────────────

def read_with(exps, groups, tests=None, ok=None, warnings=None):
    return {"read": {"experiments": exps, "adGroups": groups, "adTests": len(groups) if tests is None else tests, "ok": ok or {"experiments": True, "ads": True},
                     "warnings": warnings or [], "today": "2026-10-18"}, "account": {"id": ACCOUNT_ID, "name": "Acme", "currency": "USD"},
            "accounts": [{"platform": "google", "id": ACCOUNT_ID, "name": "Acme", "currency": "USD"}], "readAt": NOW.isoformat()}


def test_the_needs_list_orders_by_the_decision_then_by_money():
    over = made(end="2026-10-10", **WIN)
    over["id"], over["name"] = "2", "Overdue test"
    win = made(**WIN)
    win["id"], win["name"] = "3", "Winning test"
    flat = made(**FLAT)
    flat["id"], flat["name"] = "4", "Flat test"
    raw = read_with([made(**LOSE), win, over, flat], [group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50))])
    rep = ex.assemble(raw, now=NOW, today=TODAY)
    kinds = [n["kind"] for n in rep["needs"]]
    assert kinds == ["decide", "apply", "end", "end", "pause"]
    assert rep["needs"][0]["title"] == "Overdue test" and rep["needs"][0]["ref"] == {"type": "experiment", "id": "2"}
    assert [n["title"] for n in rep["needs"][2:4]] == ["Flat test", "Headline test"]
    assert rep["needs"][4]["ref"] == {"type": "adTest", "id": "5"} and rep["needs"][4]["title"] == "Implants, Search Implants"
    assert rep["summary"]["needsAction"] == 5


def test_the_needs_list_leaves_out_what_only_waits_or_is_a_record():
    rep = ex.assemble(read_with([made(state="ended", **WIN), made(start="2026-10-08", window=("2026-10-08", "2026-10-17"), **EARLY), made(measured=False, control=None, trial=None)], []), now=NOW, today=TODAY)
    assert rep["needs"] == [] and rep["summary"]["needsAction"] == 0


def test_money_orders_decisions_of_the_same_kind():
    a, b = made(**LOSE), made(**LOSE)
    a["id"], a["name"], b["id"], b["name"] = "1", "Small", "2", "Big"
    a["arms"][0]["results"]["total"]["cost"] = 10.0
    b["arms"][0]["results"]["total"]["cost"] = 5000.0
    rep = ex.assemble(read_with([a, b], []), now=NOW, today=TODAY)
    assert [n["title"] for n in rep["needs"]] == ["Big", "Small"]


def test_the_summary_counts_what_is_running_and_what_it_found():
    early = made(start="2026-10-08", window=("2026-10-08", "2026-10-17"), **EARLY)
    exps = [made(**WIN), made(**LOSE), made(**FLAT), early, made(state="ended", **WIN), made(state="applied", **WIN), made(state="setting up", measured=False, window=None, control=None, trial=None),
            made(end="2026-10-10", **WIN), made(end="2026-10-22", **WIN)]
    groups = [group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50)), group(ad(3, 20000, 1000, 50), ad(4, 20000, 1000, 52)), group(ad(5, 900, 40, 0), ad(6, 800, 30, 0))]
    s = ex.assemble(read_with(exps, groups, tests=5), now=NOW, today=TODAY)["summary"]
    assert (s["experiments"], s["running"], s["ended"], s["applied"], s["settingUp"], s["measured"]) == (9, 6, 1, 1, 1, 8)
    assert (s["winners"], s["behind"], s["noDifference"], s["tooEarly"]) == (3, 1, 1, 1)
    assert s["overdue"] == 1 and s["endsSoon"] == 1
    assert (s["adTests"], s["adTestsShown"], s["adConclusive"], s["adNoDifference"], s["adTooEarly"]) == (5, 3, 1, 1, 1)
    assert s["experimentSpend"] == 1600.0 and s["adSpend"] == 300.0


def test_the_notes_say_when_ad_tests_were_capped_clipped_or_had_extra_arms():
    many = made(extra=1, **WIN)
    many["clipped"] = True
    rep = ex.assemble(read_with([many], [group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50))], tests=40), now=NOW, today=TODAY)
    joined = " ".join(rep["notes"])
    assert "40 ad groups run two or more live ads. The 1 that spent most" in joined
    assert "1 experiment started more than 365 days ago" in joined and "first trial arm only" in joined
    plain = ex.assemble(read_with([made(**WIN)], [group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50))]), now=NOW, today=TODAY)
    assert len(plain["notes"]) == len(ex.NOTES) + len(ex.NOTES_GOOGLE)


def test_a_part_that_could_not_be_read_is_reported_not_hidden():
    rep = ex.assemble(read_with([], [], ok={"experiments": False, "ads": True}, warnings=["The experiments could not be read (x)."]), now=NOW, today=TODAY)
    assert rep["unread"] == ["Google Ads: experiments"] and rep["warnings"] == ["The experiments could not be read (x)."]
    assert rep["experiments"] == [] and rep["summary"]["experiments"] == 0


def test_an_account_with_nothing_to_test_reports_zeros():
    rep = ex.assemble(read_with([], []), now=NOW, today=TODAY)
    assert rep["needs"] == [] and rep["summary"]["adTests"] == 0 and rep["account"]["name"] == "Acme" and rep["window"] == "the last 30 days"


def test_no_em_or_en_dash_anywhere_in_a_report():
    import json
    exps = [made(**WIN), made(**LOSE), made(**FLAT), made(**EARLY), made(end="2026-10-10", **WIN), made(measured=False, control=None, trial=None, typ="AD_VARIATION"),
            made(state="ended", **WIN), made(state="applied", **LOSE), made(state="setting up", measured=False, window=None, control=None, trial=None)]
    groups = [group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50)), group(ad(3, 20000, 1000, 50), ad(4, 20000, 1000, 52)), group(ad(5, 900, 40, 0), ad(6, 800, 30, 0)),
              group(*[ad(i, 20000, 1000, 50 + i % 2) for i in range(10, 16)])]
    text = json.dumps(ex.assemble(read_with(exps, groups), now=NOW, today=TODAY), ensure_ascii=False)
    assert "—" not in text and "–" not in text


# ── reading, caching ────────────────────────────────────────────────────────

def search(calls=None, fail=None):
    fail = fail or {}
    data = {"SELECT experiment.resource_name": [exp_row(1, start="2026-10-01", end="2026-10-30")], "SELECT experiment_arm": [arm_row(1, "Base", True, 50, 11), arm_row(1, "Trial", False, 50, 22)],
            "SELECT campaign.resource_name": [camp_row(11, "Base"), camp_row(22, "Trial", exp="EXPERIMENT")],
            "SELECT campaign.id, segments.date": [day_row(11, "2026-10-01"), day_row(22, "2026-10-01")], "SELECT campaign.id, campaign.name": [ad_row(5, 1), ad_row(5, 2)]}

    def run(account, gaql):
        if calls is not None:
            calls.append(gaql)
        for prefix, rows in data.items():
            if gaql.startswith(prefix):
                key = {"SELECT experiment.resource_name": "experiments", "SELECT campaign.id, campaign.name": "ads"}.get(prefix)
                if key in fail:
                    raise fail[key]
                return rows
        raise AssertionError(gaql[:60])
    return run


def run_report(**kw):
    kw.setdefault("store", FakeStore())
    kw.setdefault("search_fn", search())
    return ex.report_for(ACCOUNT_ID, accounts_fn=lambda: [ACCOUNT], now=NOW, today=TODAY, **kw)


def test_an_account_that_is_not_ours_is_refused():
    with pytest.raises(ex.BadRequest):
        ex.report_for("9998887776", store=FakeStore(), accounts_fn=lambda: [ACCOUNT], search_fn=search(), now=NOW, today=TODAY)


def test_a_report_comes_back_for_an_account():
    out = run_report()
    assert out["account"]["name"] == "Acme Dental" and len(out["experiments"]) == 1 and out["experiments"][0]["measured"] and len(out["adTests"]) == 1 and out["warnings"] == []


def test_the_second_report_uses_the_saved_read():
    store, calls = FakeStore(), []
    run_report(store=store, search_fn=search(calls))
    n = len(calls)
    assert n == 5
    run_report(store=store, search_fn=search(calls))
    assert len(calls) == n


def test_the_saved_read_is_per_account():
    store, calls = FakeStore(), []
    run_report(store=store, search_fn=search(calls))
    other = dict(ACCOUNT, id="2223334445", name="Other")
    ex.report_for("2223334445", store=store, accounts_fn=lambda: [ACCOUNT, other], search_fn=search(calls), now=NOW, today=TODAY)
    assert len(calls) == 10


def test_refresh_reads_again_but_not_twice_in_the_cooldown():
    store, calls = FakeStore(), []
    run_report(store=store, search_fn=search(calls))
    run_report(store=store, search_fn=search(calls), force=True)
    assert len(calls) == 10
    with pytest.raises(paid_gads.RefreshTooSoon):
        run_report(store=store, search_fn=search(calls), force=True)
    assert len(calls) == 10


def test_when_the_allowance_is_spent_the_last_saved_read_is_served_with_its_date():
    store = FakeStore()
    run_report(store=store)
    key = ex.cache_key(ACCOUNT_ID)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    out = run_report(store=store, search_fn=search(fail={"experiments": gads.GadsError("quota", "allowance spent"), "ads": gads.GadsError("quota", "allowance spent")}))
    assert out["stale"] == [{"platform": "google", "savedAt": "2026-10-10T08:00:00+00:00"}] and len(out["experiments"]) == 1


def test_a_spent_allowance_with_nothing_saved_is_the_error():
    boom = gads.GadsError("quota", "allowance spent")
    with pytest.raises(gads.GadsError) as e:
        run_report(search_fn=search(fail={"experiments": boom, "ads": boom}))
    assert e.value.kind == "quota"


def test_only_a_quota_error_falls_back_to_a_saved_read():
    store = FakeStore()
    run_report(store=store)
    key = ex.cache_key(ACCOUNT_ID)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    boom = gads.GadsError("api", "Google answered 500")
    with pytest.raises(gads.GadsError):
        run_report(store=store, search_fn=search(fail={"experiments": boom, "ads": boom}))


def test_a_read_with_a_part_missing_is_kept_only_five_minutes():
    store, calls = FakeStore(), []
    bad = {"ads": gads.GadsError("api", "x")}
    out = run_report(store=store, search_fn=search(calls, fail=bad))
    assert out["unread"] == ["Google Ads: ads"] and len(calls) == 5
    run_report(store=store, search_fn=search(calls, fail=bad))
    assert len(calls) == 5
    paid_gads.forget()
    store.at[ex.cache_key(ACCOUNT_ID)] -= ex.PARTIAL_TTL_S + 5
    run_report(store=store, search_fn=search(calls))
    assert len(calls) == 10
    paid_gads.forget()
    store.at[ex.cache_key(ACCOUNT_ID)] -= 1800                       # a complete read is still good half an hour on
    run_report(store=store, search_fn=search(calls))
    assert len(calls) == 10


def test_a_failed_save_never_costs_the_report():
    class Broken(FakeStore):
        def put_gads_cache(self, key, value):
            raise RuntimeError("db down")
    assert run_report(store=Broken())["experiments"]


def test_two_people_asking_at_once_share_one_read():
    import threading
    store, calls = FakeStore(), []
    results = []

    def go():
        results.append(run_report(store=store, search_fn=search(calls)))
    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 4 and len(calls) == 5


def test_the_cache_key_changes_with_the_version_and_the_account():
    assert ex.cache_key("1") != ex.cache_key("2") and ("exp%s:" % ex.CACHE_VERSION) in ex.cache_key("1")


# ── pinned by mutation ──────────────────────────────────────────────────────

def test_a_split_that_is_off_is_flagged_from_a_thousand_impressions_up():
    assert build(control=tot(1500, 100, 5), trial=tot(500, 100, 9))["splitNote"] is not None
    assert build(control=tot(600, 30, 1), trial=tot(300, 30, 2))["splitNote"] is None               # 900 impressions: too few to say


def test_an_arm_with_no_split_recorded_is_never_flagged():
    assert build(control=tot(30000, 1000, 50), trial=tot(10000, 1000, 90), split=(50, None))["splitNote"] is None


def test_waiting_on_click_rate_asks_for_impressions():
    r = build(start="2026-10-08", window=("2026-10-08", "2026-10-17"), control=tot(400, 10, 0), trial=tot(600, 12, 0))
    assert "600 more impressions in each arm" in r["action"]["text"]


def test_an_experiment_ending_today_says_so_and_is_not_late():
    r = build(end="2026-10-18", **WIN)
    assert r["overdue"] is False and r["daysLeft"] == 0 and r["action"]["text"].startswith("Ends today. ") and r["action"]["kind"] == "apply"


def test_a_short_flat_experiment_past_its_end_still_needs_a_decision():
    r = build(start="2026-10-08", end="2026-10-10", window=("2026-10-08", "2026-10-17"), **FLAT)
    assert r["overdue"] and r["action"]["kind"] == "decide" and "after 10 days" in r["action"]["text"]


def test_the_comparison_is_on_the_first_trial_arm():
    e = made(extra=1, **WIN)
    e["arms"][2]["results"]["total"] = tot(20000, 1000, 10)
    assert ex.build_experiment(e, TODAY)["verdict"] == "winner"
    e["arms"][1]["results"]["total"], e["arms"][2]["results"]["total"] = tot(20000, 1000, 10), tot(20000, 1000, 90)
    assert ex.build_experiment(e, TODAY)["verdict"] == "behind"


def test_what_needs_a_look_comes_after_what_is_to_be_decided():
    stuck = made(start="2026-10-08", window=("2026-10-08", "2026-10-17"), control=tot(8000, 0, 0), trial=tot(8000, 50, 5))
    stuck["id"], stuck["name"] = "7", "Stuck test"
    dead = made(control=tot(0, 0, 0, 0.0), trial=tot(5000, 100, 5))
    dead["id"], dead["name"] = "8", "Dead arm test"
    rep = ex.assemble(read_with([dead, stuck, made(**LOSE)], [group(ad(1, 20000, 1000, 100), ad(2, 20000, 1000, 50))]), now=NOW, today=TODAY)
    assert [n["kind"] for n in rep["needs"]] == ["end", "pause", "stuck", "check"]
    assert rep["summary"]["needsAction"] == 4


def test_spend_orders_decisions_of_the_same_kind_whatever_their_names():
    a, b = made(**LOSE), made(**LOSE)
    a["id"], a["name"], b["id"], b["name"] = "1", "Alpha small", "2", "Zed big"
    b["arms"][0]["results"]["total"]["cost"] = 5000.0
    rep = ex.assemble(read_with([a, b], []), now=NOW, today=TODAY)
    assert [n["title"] for n in rep["needs"]] == ["Zed big", "Alpha small"]


def test_the_leader_is_listed_first_even_when_there_is_no_difference():
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 50, cost=90), ad(2, 20000, 1000, 52, cost=10)))
    assert t["verdict"] == "no_difference" and t["leader"] == "2" and t["ads"][0]["id"] == "2"


def test_the_smallest_visible_gap_comes_from_the_ads_that_were_compared():
    from tracker import paid_exp_stats as st
    t = ex.build_ad_test(group(ad(1, 20000, 1000, 50), ad(2, 20000, 1200, 62), ad(3, 900, 40, 2)))
    assert t["verdict"] == "no_difference" and t["eligible"] == 2
    want = round(100 * st.detectable_gap(62 / 1200.0, 1000), 1)
    assert t["detectable"] == want


def test_an_ad_with_exactly_enough_impressions_is_not_called_short():
    t = ex.build_ad_test(group(ad(1, 1000, 30, 0), ad(2, 999, 30, 0)))
    assert "1 ad has not had enough impressions yet (1000 each)" in t["action"]["text"]
    none = ex.build_ad_test(group(ad(1, 0, 0, 0), ad(2, 5000, 100, 0)))
    assert "1 ad has not had enough impressions" in none["action"]["text"]


def test_the_summary_counts_a_partial_ad_result_as_conclusive_and_an_experiment_ending_today_as_ending_soon():
    groups = [group(ad(1, 20000, 1000, 150), ad(2, 20000, 1000, 148), ad(3, 20000, 1000, 60))]
    rep = ex.assemble(read_with([made(end="2026-10-18", **WIN)], groups), now=NOW, today=TODAY)
    assert rep["summary"]["adConclusive"] == 1 and rep["summary"]["endsSoon"] == 1


def test_a_second_report_is_served_from_memory_when_the_database_is_down():
    store = FakeStore()
    run_report(store=store)

    class Down(FakeStore):
        def get_gads_entry(self, key, ttl):
            raise RuntimeError("db down")
    down = Down()
    out = run_report(store=down, search_fn=search(fail={"experiments": RuntimeError("must not read")}))
    assert out["experiments"]


def test_an_experiment_with_only_one_arm_read_is_not_compared():
    r = build(measured=False, control=tot(20000, 1000, 50), trial=None)
    assert r["verdict"] == "not_measured" and r["comparison"] is None and r["action"]["kind"] == "manual"
    r2 = build(measured=False, control=None, trial=tot(20000, 1000, 50))
    assert r2["verdict"] == "not_measured" and r2["comparison"] is None


def test_a_result_that_rounds_to_certain_says_over_99_9_not_100():
    r = build(control=tot(200000, 10000, 400), trial=tot(200000, 10000, 900))
    assert r["comparison"]["confidence"] == 100.0 and "over 99.9% sure" in r["action"]["text"] and "100% sure" not in r["action"]["text"]
    assert ex._sure(99.9) == "99.9% sure" and ex._sure(95.0) == "95% sure" and ex._sure(100.0) == "over 99.9% sure"


def test_dates_in_the_words_read_as_month_and_day():
    assert ex._date_text("2026-10-14") == "Oct 14" and ex._date_text("2026-11-01") == "Nov 1" and ex._date_text(dt.date(2026, 1, 5)) == "Jan 5" and ex._date_text(None) == "" and ex._date_text("junk") == ""
