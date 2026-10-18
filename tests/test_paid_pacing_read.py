"""Pacing Desk: the month read from Google Ads (tracker/paid_pacing_read.py)."""
import datetime as dt

import pytest

from tracker import paid_gads_segments as gads
from tracker import paid_pacing_read as rd


def utc(y, m, d, h=12):
    return dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc)


def acct(i="1111111111", name="Acme", tz="America/New_York", cur="USD"):
    return {"id": i, "name": name, "timeZone": tz, "currency": cur}


# ── The window ──────────────────────────────────────────────────────────────

def test_the_run_is_for_the_day_after_the_newest_yesterday():
    as_of, start, ends = rd.plan_window([acct()], now=utc(2026, 10, 12))
    assert as_of == dt.date(2026, 10, 12) and start == dt.date(2026, 10, 1) and ends == {"1111111111": "2026-10-11"}


def test_one_account_in_a_distant_zone_does_not_move_the_whole_runs_today():
    la1, la2, au = acct("1111111111", tz="America/Los_Angeles"), acct("2222222222", tz="America/New_York"), acct("3333333333", tz="Australia/Sydney")
    # 20:00 UTC on the 11th: the 11th in America, already the 12th in Sydney
    as_of, start, ends = rd.plan_window([la1, la2, au], now=utc(2026, 10, 11, 20))
    assert as_of == dt.date(2026, 10, 11)                      # the Americans' today, not Sydney's
    assert ends == {"1111111111": "2026-10-10", "2222222222": "2026-10-10", "3333333333": "2026-10-10"}     # Sydney is not read past the run's day


def test_an_account_a_day_behind_is_read_through_its_own_yesterday():
    ny, ny2, la = acct("1111111111", tz="America/New_York"), acct("2222222222", tz="America/New_York"), acct("3333333333", tz="America/Los_Angeles")
    # 05:00 UTC on the 12th: still the 11th in Los Angeles (yesterday the 10th), already the 12th in New York (yesterday the 11th)
    as_of, _, ends = rd.plan_window([ny, ny2, la], now=utc(2026, 10, 12, 5))
    assert as_of == dt.date(2026, 10, 12) and ends == {"1111111111": "2026-10-11", "2222222222": "2026-10-11", "3333333333": "2026-10-10"}


def test_a_tie_takes_the_earlier_day():
    a, b = acct("1111111111", tz="America/Los_Angeles"), acct("2222222222", tz="Asia/Kolkata")
    as_of, _, ends = rd.plan_window([a, b], now=utc(2026, 10, 11, 20))
    assert as_of == dt.date(2026, 10, 11) and ends == {"1111111111": "2026-10-10", "2222222222": "2026-10-10"}


def test_the_first_of_the_month_stays_on_the_month_that_ended():
    as_of, start, ends = rd.plan_window([acct()], now=utc(2026, 10, 1))
    assert as_of == dt.date(2026, 9, 30) and start == dt.date(2026, 9, 1)
    assert ends == {"1111111111": "2026-09-29"}                # the engine reads the 30th as today, so the data stops the day before it


def test_a_finished_month_is_not_projected_over_for_a_day_that_was_already_read():
    """The run for the last day, from the rows plan_window asks for: a perfectly paced account is on target, not 3% over."""
    from tracker import paid_refresh as rf, paid_pacing_budgets as pb
    _, _, ends = rd.plan_window([acct()], now=utc(2026, 11, 1, 20))
    last = int(ends["1111111111"][-2:])
    rows = [{"id": "1", "name": "Camp", "status": "ENABLED", "type": "Search", "bid": "Manual CPC", "budget": 100.0, "pool": "", "date": "2026-10-%02d" % d,
             "impr": 100.0, "clicks": 10.0, "cost": 100.0, "conv": 2.0, "value": 0.0, "sis": 0.5, "account": "Acme", "accountId": "1"} for d in range(1, last + 1)]
    body, _ = rf.run_engine(rd.export_rows(rows), pb.dpr_rows([pb.check_row({"client": "Acme", "account": "Acme", "allocated": 3100})], "2026-10"), dt.date(2026, 10, 31))
    a = body["clients"][0]["accounts"][0]
    assert last == 30 and a["landing"] == "ON TARGET" and a["projected"] == 3100.0


def test_a_zone_this_machine_does_not_know_falls_back_instead_of_failing():
    as_of, _, ends = rd.plan_window([acct(tz="Mars/Olympus")], now=utc(2026, 10, 12))
    assert ends["1111111111"] == "2026-10-11"
    assert rd.account_today("", utc(2026, 10, 12, 3)) == dt.date(2026, 10, 11)     # 3am UTC is still the 11th in Los Angeles


def test_nothing_to_read_is_refused():
    with pytest.raises(ValueError):
        rd.plan_window([])


# ── The queries ─────────────────────────────────────────────────────────────

def test_the_queries_ask_for_the_budget_and_the_days_and_never_for_removed_campaigns():
    q = rd.q_days("2026-10-01", "2026-10-11")
    for want in ("campaign_budget.amount_micros", "campaign_budget.explicitly_shared", "segments.date", "metrics.cost_micros",
                 "metrics.conversions_value", "metrics.search_impression_share", "BETWEEN '2026-10-01' AND '2026-10-11'", "!= 'REMOVED'"):
        assert want in q
    assert "search_impression_share" not in rd.q_days("2026-10-01", "2026-10-11", with_share=False)
    assert "campaign.status = 'ENABLED'" in rd.q_enabled() and "metrics" not in rd.q_enabled()


# ── Parsing ─────────────────────────────────────────────────────────────────

def day_row(cid="1", name="P2_Brand", day="2026-10-02", cost=12_500_000, status="ENABLED", kind="SEARCH", budget=40_000_000, shared=False,
            pool="", sis=0.4, conv=2, value=300.5, bid="TARGET_CPA"):
    m = {"impressions": "100", "clicks": "10", "costMicros": str(cost), "conversions": conv, "conversionsValue": value}
    if sis is not None:
        m["searchImpressionShare"] = sis
    return {"campaign": {"id": cid, "name": name, "status": status, "advertisingChannelType": kind, "biddingStrategyType": bid},
            "campaignBudget": {"id": "9", "name": pool, "amountMicros": str(budget), "explicitlyShared": shared},
            "segments": {"date": day}, "metrics": m}


def test_a_day_row_becomes_the_numbers_and_settings_the_engine_reads():
    [r] = rd.parse_days([day_row()])
    assert (r["id"], r["name"], r["date"], r["status"], r["type"], r["bid"]) == ("1", "P2_Brand", "2026-10-02", "ENABLED", "Search", "Target CPA")
    assert (r["cost"], r["conv"], r["value"], r["impr"], r["clicks"], r["budget"], r["sis"]) == (12.5, 2.0, 300.5, 100.0, 10.0, 40.0, 0.4)
    assert r["pool"] == ""


def test_money_is_kept_to_the_cent():
    r = rd.parse_days([day_row(cost=12_345_678, budget=40_126_000)])[0]
    assert r["cost"] == 12.35 and r["budget"] == 40.13


def test_only_an_explicitly_shared_budget_is_a_pool():
    own = rd.parse_days([day_row(pool="P2_Brand", shared=False)])[0]            # Google names every budget after its campaign
    shared = rd.parse_days([day_row(pool="MRI", shared=True)])[0]
    assert own["pool"] == "" and shared["pool"] == "MRI"


def test_a_missing_share_is_none_not_zero():
    assert rd.parse_days([day_row(sis=None)])[0]["sis"] is None
    assert rd.parse_days([day_row(sis="")])[0]["sis"] is None
    assert rd.parse_days([day_row(sis="0.25")])[0]["sis"] == 0.25
    assert rd.parse_days([day_row(sis=0)])[0]["sis"] == 0.0                      # a real zero share is a share


def test_campaign_types_and_bid_strategies_are_named_the_way_the_ui_names_them():
    got = {k: rd.parse_days([day_row(kind=k)])[0]["type"] for k in ("PERFORMANCE_MAX", "DEMAND_GEN", "SHOPPING", "VIDEO", "DISPLAY", "WEIRD")}
    assert got == {"PERFORMANCE_MAX": "Performance Max", "DEMAND_GEN": "Demand Gen", "SHOPPING": "Shopping", "VIDEO": "Video",
                   "DISPLAY": "Display", "WEIRD": "Other"}
    assert rd.parse_days([day_row(bid="MAXIMIZE_CONVERSIONS")])[0]["bid"] == "Maximize conversions"
    assert rd.parse_days([day_row(bid="SOMETHING_NEW")])[0]["bid"] == "Something new"


def test_rows_without_a_campaign_or_a_day_are_skipped():
    assert rd.parse_days([day_row(cid=""), {"campaign": {"id": "5"}, "metrics": {}}, {}]) == []
    assert rd.parse_enabled([{"campaign": {}}]) == []


# ── Campaigns that served nothing ───────────────────────────────────────────

def enabled_row(cid, name="P2_Dark", budget=25_000_000):
    return {"campaign": {"id": cid, "name": name, "status": "ENABLED", "advertisingChannelType": "SEARCH"},
            "campaignBudget": {"name": name, "amountMicros": str(budget), "explicitlyShared": False}}


def test_an_enabled_campaign_that_never_served_still_holds_its_budget():
    days = rd.parse_days([day_row(cid="1", day="2026-10-02")])
    out = rd.add_idle(days, rd.parse_enabled([enabled_row("1"), enabled_row("2", budget=30_000_000)]), "2026-10-11")
    idle = [r for r in out if r["id"] == "2"]
    assert len(idle) == 1 and idle[0]["date"] == "2026-10-11" and idle[0]["cost"] == 0 and idle[0]["budget"] == 30.0
    # campaign 1 spent on the 2nd but not yesterday: it gets a zero row for yesterday, the real day is kept
    assert sorted(r["date"] for r in out if r["id"] == "1") == ["2026-10-02", "2026-10-11"]


def test_a_campaign_that_served_yesterday_gets_no_extra_row():
    days = rd.parse_days([day_row(cid="1", day="2026-10-11")])
    assert len(rd.add_idle(days, rd.parse_enabled([enabled_row("1")]), "2026-10-11")) == 1


# ── Reading an account ──────────────────────────────────────────────────────

class Fake:
    """search_fn(customer id, gaql) -> rows, by what the query is about."""

    def __init__(self, days=None, enabled=None, fail=None):
        self.days, self.enabled, self.fail, self.calls = days or [], enabled or [], fail or {}, []

    def __call__(self, cid, gaql):
        self.calls.append((cid, gaql))
        kind = "days" if "segments.date" in gaql else "enabled"
        err = self.fail.get((cid, kind))
        if err and not (err.get("only_with_share") and "search_impression_share" not in gaql):
            raise gads.GadsError(err["kind"], err["detail"])
        return self.days if kind == "days" else self.enabled


def test_an_account_is_read_with_two_queries_and_its_idle_campaigns_added():
    f = Fake(days=[day_row(cid="1", day="2026-10-02")], enabled=[enabled_row("1"), enabled_row("2")])
    got = rd.read_account(acct(), "2026-10-01", "2026-10-11", search_fn=f)
    assert len(f.calls) == 2 and got["notes"] == []
    assert {r["id"] for r in got["rows"]} == {"1", "2"}


def test_when_google_refuses_the_share_the_days_are_asked_for_again_without_it():
    f = Fake(days=[day_row(sis=None)], enabled=[], fail={("1111111111", "days"): {"kind": "bad_request", "detail": "x", "only_with_share": True}})
    got = rd.read_account(acct(), "2026-10-01", "2026-10-11", search_fn=f)
    assert got["notes"] == ["impression share could not be read"] and len(got["rows"]) == 1
    assert sum("search_impression_share" in c[1] for c in f.calls) == 1


def test_any_other_refusal_of_the_days_is_raised():
    f = Fake(fail={("1111111111", "days"): {"kind": "permission", "detail": "no access"}})
    with pytest.raises(gads.GadsError):
        rd.read_account(acct(), "2026-10-01", "2026-10-11", search_fn=f)
    f = Fake(fail={("1111111111", "enabled"): {"kind": "api", "detail": "boom"}})
    with pytest.raises(gads.GadsError):
        rd.read_account(acct(), "2026-10-01", "2026-10-11", search_fn=f)


def test_the_first_day_of_the_month_is_a_day_to_read():
    f = Fake(days=[day_row(day="2026-10-01")], enabled=[])
    got = rd.read_account(acct(), "2026-10-01", "2026-10-01", search_fn=f)
    assert len(f.calls) == 2 and [r["date"] for r in got["rows"]] == ["2026-10-01"]


def test_an_account_whose_month_has_not_had_a_finished_day_only_reports_its_campaigns():
    f = Fake(enabled=[enabled_row("2")])
    got = rd.read_account(acct(), "2026-10-01", "2026-09-30", search_fn=f)
    assert len(f.calls) == 1 and [r["date"] for r in got["rows"]] == ["2026-10-01"]


# ── Reading them all ────────────────────────────────────────────────────────

def test_an_account_that_cannot_be_read_is_named_and_left_out_not_turned_into_zeros():
    a, b = acct("1111111111", "Acme"), acct("2222222222", "Beta")
    f = Fake(days=[day_row()], fail={("2222222222", "days"): {"kind": "permission", "detail": "User does not have access"}})
    got = rd.read_all([a, b], "2026-10-01", {"1111111111": "2026-10-11", "2222222222": "2026-10-11"}, search_fn=f)
    assert {r["account"] for r in got["rows"]} == {"Acme"}
    assert got["unread"] == [{"id": "2222222222", "name": "Beta", "reason": "User does not have access"}]


def test_the_reason_an_account_could_not_be_read_is_kept_short():
    f = Fake(fail={("1111111111", "days"): {"kind": "api", "detail": "x" * 500}})
    got = rd.read_all([acct()], "2026-10-01", {"1111111111": "2026-10-11"}, search_fn=f)
    assert len(got["unread"][0]["reason"]) == 200


def test_a_spent_allowance_stops_the_whole_read_instead_of_failing_every_account():
    f = Fake(fail={("1111111111", "days"): {"kind": "quota", "detail": "used up"}, ("2222222222", "days"): {"kind": "quota", "detail": "used up"}})
    with pytest.raises(gads.GadsError) as e:
        rd.read_all([acct("1111111111"), acct("2222222222", "B")], "2026-10-01", {"1111111111": "2026-10-11", "2222222222": "2026-10-11"}, search_fn=f)
    assert e.value.kind == "quota"


def test_odd_accounts_are_noted_a_foreign_currency_and_two_with_one_name():
    accts = [acct("1111111111", "Acme", cur="EUR"), acct("2222222222", "acme"), acct("3333333333", "Other")]
    got = rd.read_all(accts, "2026-10-01", {a["id"]: "2026-10-11" for a in accts}, search_fn=Fake(days=[day_row()]))
    assert any("Acme reports in EUR" in n for n in got["notes"])
    assert any("2 accounts are named \"Acme\"" in n for n in got["notes"])
    assert not any("Other" in n for n in got["notes"])


def test_too_many_unread_accounts_is_not_worth_publishing():
    accts = [acct(str(1111111110 + i), "A%d" % i) for i in range(8)]
    assert rd.too_many_unread(accts, [{}] * 2) is False          # a quarter is the line
    assert rd.too_many_unread(accts, [{}] * 3) is True
    assert rd.too_many_unread([], []) is False


# ── The engine's input ──────────────────────────────────────────────────────

def test_the_rows_are_laid_out_like_the_google_ads_export_the_engine_reads():
    rows = [dict(r, account="Acme") for r in rd.parse_days([day_row(cid="1", day="2026-10-03"), day_row(cid="1", day="2026-10-02", sis=None, pool="MRI", shared=True)])]
    out = rd.export_rows(rows)
    assert out[0] == rd.HEADER
    h = {n: i for i, n in enumerate(rd.HEADER)}
    first, second = out[1], out[2]
    assert [first[h["Day"]], second[h["Day"]]] == ["2026-10-02", "2026-10-03"]          # by day within a campaign
    assert first[h["Campaign status"]] == "Enabled" and first[h["Budget"]] == 40.0 and first[h["Budget name"]] == "MRI" and first[h["Budget type"]] == "Shared"
    assert first[h["Search impr. share"]] == " --" and second[h["Search impr. share"]] == 0.4
    assert second[h["Budget name"]] == "--" and second[h["Campaign type"]] == "Search" and second[h["Cost"]] == 12.5
    assert second[h["Conv. value"]] == 300.5 and second[h["Account"]] == "Acme"
