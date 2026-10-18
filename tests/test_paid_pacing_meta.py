"""Pacing Desk on Meta: tracker/paid_pacing_meta.py reads a Meta ad account's month so far into the rows the engine reads for a Google account.

Pinned here: which ad account an allocation row means (by name, never guessed); a campaign's daily budget (its own, else its running ad sets', a lifetime
budget over its flight, and none said so when it cannot be told); the day rows with the results the objective counts; a running campaign with no spend on
the last day still holds its budget; the spend is named as the ALLOCATION names the account; a failing account is named and left out; Meta asking to wait
is raised. Nothing here talks to Meta.
"""
import datetime as dt

import pytest

from tests.gads_fakes import FakeStore
from tracker import paid_gads as base
from tracker import paid_meta as meta
from tracker import paid_pacing_meta as pm
from tracker import paid_pacing_read as reader

ACT = {"id": "act_5550001", "name": "Acme Social", "currency": "USD", "tz": "America/New_York", "platform": "meta"}


def alloc(account="Acme Social", client="Acme", channel="Meta"):
    return {"client": client, "account": account, "allocated": 3000.0, "channel": channel}


def acct(name, aid="act_5550001", cur="USD", tz="America/New_York"):
    return {"id": aid, "name": name, "currency": cur, "tz": tz, "platform": "meta"}


# ── Which ad account an allocation row means ────────────────────────────────

def test_an_allocation_row_is_matched_to_the_ad_account_of_the_same_name_ignoring_case_and_punctuation():
    found, notes = pm.match_accounts([alloc("ACME  social!")], [acct("Acme Social"), acct("Beta", "act_2")])
    assert [(r["account"], a["id"]) for r, a in found] == [("ACME  social!", "act_5550001")] and notes == []


def test_one_name_inside_the_other_counts_when_only_one_ad_account_fits():
    found, _ = pm.match_accounts([alloc("Acme")], [acct("Acme Social - Meta"), acct("Beta", "act_2")])
    assert found[0][1]["name"] == "Acme Social - Meta"


def test_an_allocation_that_fits_no_ad_account_or_several_is_said_so_and_not_guessed():
    found, notes = pm.match_accounts([alloc("Zed"), alloc("Acme")], [acct("Acme Social"), acct("Acme Dental", "act_2")])
    assert found == [] and "fits no Meta ad account" in notes[0] and "fits 2 Meta ad accounts (Acme Social, Acme Dental)" in notes[1]


def test_an_exact_name_wins_over_longer_ones():
    found, _ = pm.match_accounts([alloc("Acme")], [acct("Acme"), acct("Acme Social", "act_2")])
    assert found[0][1]["id"] == "act_5550001"


def test_google_rows_are_not_for_this_reader():
    assert pm.match_accounts([alloc(channel="Google")], [acct("Acme Social")]) == ([], [])


# ── A campaign's daily budget ───────────────────────────────────────────────

def test_a_campaigns_own_daily_budget_is_in_the_currencys_minor_units():
    assert pm.daily_budget({"daily_budget": "12550"}, [], "USD") == (125.5, None)
    assert pm.daily_budget({"daily_budget": "5000"}, [], "JPY") == (5000.0, None)                    # yen have no minor unit


def test_with_no_campaign_budget_the_running_ad_sets_budgets_are_added_and_a_paused_one_is_not():
    sets = [{"effective_status": "ACTIVE", "daily_budget": "3000"}, {"effective_status": "WITH_ISSUES", "daily_budget": "2000"}, {"effective_status": "PAUSED", "daily_budget": "9900"}]
    assert pm.daily_budget({}, sets, "USD") == (50.0, None)


def test_a_lifetime_budget_is_spread_over_its_flight():
    camp = {"lifetime_budget": "310000", "start_time": "2026-10-01T00:00:00+0000", "stop_time": "2026-10-31T00:00:00+0000", "name": "C"}
    assert pm.daily_budget(camp, [], "USD") == (103.33, None)
    sets = [{"effective_status": "ACTIVE", "lifetime_budget": "60000", "start_time": "2026-10-01T00:00:00+0000", "end_time": "2026-10-11T00:00:00+0000"}]
    assert pm.daily_budget({}, sets, "USD") == (60.0, None)


def test_a_lifetime_budget_with_no_end_date_has_no_daily_budget_and_says_so():
    amount, note = pm.daily_budget({"lifetime_budget": "310000", "name": "Evergreen"}, [], "USD")
    assert amount == 0.0 and "Evergreen has a lifetime budget and no end date" in note
    amount, note = pm.daily_budget({"name": "C"}, [{"effective_status": "ACTIVE", "lifetime_budget": "1000"}], "USD")
    assert amount == 0.0 and "ad sets on a lifetime budget with no end date" in note


# ── Rows ────────────────────────────────────────────────────────────────────

def camp(cid="1", name="Leads Oct", status="ACTIVE", objective="OUTCOME_LEADS", daily="10000", **kw):
    return dict({"id": cid, "name": name, "effective_status": status, "objective": objective, "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "daily_budget": daily}, **kw)


def day(cid, d, spend, leads=0, name="Leads Oct", objective="OUTCOME_LEADS"):
    return {"campaign_id": cid, "campaign_name": name, "objective": objective, "date_start": d, "spend": str(spend), "impressions": "1000", "inline_link_clicks": "50",
            "actions": [{"action_type": "lead", "value": str(leads)}] if leads else [], "action_values": []}


def graph(camps=(), sets=(), days=(), fail=()):
    def fetch(path, params):
        part = "campaigns" if path.endswith("/campaigns") else "adsets" if path.endswith("/adsets") else "insights"
        if part in fail:
            raise base.AdsError("Meta answered 400: (#100) permission denied")
        return list({"campaigns": camps, "adsets": sets, "insights": days}[part])
    return fetch


def test_a_campaign_row_says_what_the_engine_needs_to_judge_it():
    c = pm.campaign_row(camp(status="PAUSED", objective="OUTCOME_SALES"), 100.0)
    assert c == {"id": "1", "name": "Leads Oct", "status": "PAUSED", "type": "Meta Sales", "bid": "Lowest cost", "budget": 100.0, "pool": ""}
    assert pm.campaign_row(camp(bid_strategy="COST_CAP"), 1)["bid"] == "Cost cap" and pm.campaign_row(camp(status="WITH_ISSUES"), 1)["status"] == "ENABLED"


def test_a_day_row_carries_the_results_the_objective_counts():
    camps = {"1": pm.campaign_row(camp(), 100.0)}
    rows = pm.parse_days([day("1", "2026-10-01", 80.5, leads=4), day("", "2026-10-01", 5), {"campaign_id": "1"}], camps)
    assert len(rows) == 1 and rows[0]["cost"] == 80.5 and rows[0]["conv"] == 4.0 and rows[0]["clicks"] == 50.0 and rows[0]["impr"] == 1000.0 and rows[0]["budget"] == 100.0
    assert rows[0]["sis"] is None and rows[0]["date"] == "2026-10-01"


def test_the_last_day_to_read_is_the_day_before_the_run_and_never_past_the_accounts_own_yesterday():
    now = dt.datetime(2026, 10, 16, 3, 0, tzinfo=dt.timezone.utc)                                    # 23:00 on the 15th in New York
    assert pm.ends_for([acct("A")], dt.date(2026, 10, 16), now) == {"act_5550001": "2026-10-14"}      # its own yesterday is the 14th
    assert pm.ends_for([acct("A", tz="Australia/Sydney")], dt.date(2026, 10, 16), now) == {"act_5550001": "2026-10-15"}   # Sydney is a day ahead: not past the run's


# ── One account and all of them ─────────────────────────────────────────────

def read(**kw):
    return pm.read_account(ACT, "2026-10-01", "2026-10-15", store=FakeStore(), fetch=graph(**kw))


def test_an_accounts_month_is_a_row_per_campaign_per_day_with_its_budget():
    got = read(camps=[camp()], days=[day("1", "2026-10-01", 90, 3), day("1", "2026-10-02", 110, 5)])
    assert [(r["date"], r["cost"], r["conv"], r["budget"]) for r in got["rows"]] == [("2026-10-01", 90.0, 3.0, 100.0), ("2026-10-02", 110.0, 5.0, 100.0), ("2026-10-15", 0.0, 0.0, 100.0)]
    assert got["notes"] == []                                                      # the idle row is the running campaign that spent nothing on the last day


def test_a_running_campaign_with_no_spend_at_all_still_holds_its_budget_and_a_paused_one_does_not_get_a_row():
    got = read(camps=[camp("1", "Dark"), camp("2", "Off", status="PAUSED")], days=[])
    assert [(r["name"], r["date"], r["cost"], r["budget"]) for r in got["rows"]] == [("Dark", "2026-10-15", 0.0, 100.0)]


def test_a_campaign_with_no_known_budget_is_said_so_once_per_running_campaign():
    got = read(camps=[camp("1", "Evergreen", daily=None, lifetime_budget="310000"), camp("2", "Old", status="PAUSED", daily=None, lifetime_budget="310000")], days=[])
    assert got["notes"] == ["Evergreen has a lifetime budget and no end date, so its budget a day is not known"]


def test_a_month_that_has_no_complete_day_yet_reports_only_the_running_campaigns():
    got = pm.read_account(ACT, "2026-10-01", "2026-09-30", store=FakeStore(), fetch=graph(camps=[camp()], days=[day("1", "2026-10-01", 5)]))
    assert [(r["date"], r["cost"]) for r in got["rows"]] == [("2026-10-01", 0.0)]


def read_all(matched, **kw):
    return pm.read_all(matched, "2026-10-01", {a["id"]: "2026-10-15" for _r, a in matched}, store=FakeStore(), fetch=graph(**kw))


def test_the_spend_is_named_as_the_allocation_names_the_account():
    out = read_all([(alloc("Acme Social (Meta)"), ACT)], camps=[camp()], days=[day("1", "2026-10-01", 90, 3)])
    assert {r["account"] for r in out["rows"]} == {"Acme Social (Meta)"} and {r["accountId"] for r in out["rows"]} == {"act_5550001"} and out["unread"] == []


def test_an_account_that_fails_is_named_and_left_out_never_turned_into_zeros():
    out = read_all([(alloc(), ACT)], camps=[camp()], fail={"insights"})
    assert out["rows"] == [] and out["unread"] == [{"id": "act_5550001", "name": "Acme Social", "reason": "Meta answered 400: (#100) permission denied"}]


def test_meta_asking_the_app_to_wait_stops_the_whole_read():
    def fetch(path, params):
        raise meta.OverQuota("Meta is rate limiting this app right now.")
    with pytest.raises(meta.OverQuota):
        pm.read_all([(alloc(), ACT)], "2026-10-01", {"act_5550001": "2026-10-15"}, store=FakeStore(), fetch=fetch)


def test_an_account_in_another_currency_is_noted_because_the_desk_adds_dollars():
    out = read_all([(alloc(), dict(ACT, currency="CAD"))], camps=[camp()], days=[day("1", "2026-10-01", 90)])
    assert any("reports in CAD" in n for n in out["notes"])


def test_the_rows_go_to_the_engine_in_the_layout_it_reads():
    out = read_all([(alloc(), ACT)], camps=[camp()], days=[day("1", "2026-10-01", 90, 3)])
    sheet = reader.export_rows(out["rows"])
    assert sheet[0] == reader.HEADER and sheet[1][:3] == ["2026-10-01", "Enabled", "Leads Oct"] and sheet[1][6:9] == ["Acme Social", "Meta Leads", "Lowest cost"]
    assert sheet[1][10] == " --" and sheet[1][11] == 90.0 and sheet[1][12] == 3.0
