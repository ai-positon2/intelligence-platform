"""Paid Marketing Agents: Seasonality Analysis, the numbers and the wiring.

What is pinned here: a seasonal index that recovers a known pattern and does not mistake growth for a season; the day-of-week
index that holds under growth; the holiday calendar and what each holiday did against its ordinary days; year over year; the
verdict and the budget step for the months ahead; what is said when there is too little history; that the account-level read
of Google Ads asks one row a day, is stored apart from the campaign rows, and is read from the store the second time; that a
long history is read in slices of at most a year, newest first; that one platform failing leaves the other's numbers and
both failing raises; and the routes (the page, the slice read, the analysis, their errors).
"""
import datetime as dt
import json
import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tests.gads_fakes import FakeStore  # noqa: E402
from tracker import paid_gads as g  # noqa: E402
from tracker import paid_meta as meta  # noqa: E402
from tracker import paid_seasonality as S  # noqa: E402
from tracker import paid_store  # noqa: E402

CID = "1234567890"
ACT = "act_1017230410171605"
TODAY = dt.date(2026, 10, 16)

SEASON = {1: .9, 2: .95, 3: 1.0, 4: 1.0, 5: 1.05, 6: .9, 7: .7, 8: .85, 9: 1.0, 10: 1.1, 11: 1.6, 12: 1.3}
WEEK = [1.1, 1.15, 1.1, 1.0, .9, .75, .8]


def days_between(a, b):
    x, y = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [(x + dt.timedelta(days=i)).isoformat() for i in range((y - x).days + 1)]


def synth(start="2023-10-01", end="2026-10-14", season=None, week=None, growth=1.0, cost=200.0, spike=None, pause=()):
    """Daily totals with a known shape: conversions follow season x weekday x growth; spend only partly follows the season.
    `spike` is {date: factor} on conversions, `pause` is dates with no spend at all."""
    days = days_between(start, end)
    out = {"days": days, "impr": [], "clicks": [], "cost": [], "conv": [], "value": []}
    for i, ds in enumerate(days):
        d = dt.date.fromisoformat(ds)
        f = (season or {}).get(d.month, 1.0) * (week or [1] * 7)[d.weekday()] * growth ** (i / 365)
        f *= (spike or {}).get(ds, 1.0)
        paused = ds in pause
        out["impr"].append(0 if paused else 1000 * f)
        out["clicks"].append(0 if paused else 100 * f)
        out["cost"].append(0 if paused else cost * growth ** (i / 365))
        out["conv"].append(0 if paused else 10 * f)
        out["value"].append(0 if paused else 600 * f)
    return out


# ── The seasonal index ─────────────────────────────────────────────────────

def test_a_known_seasonal_pattern_is_recovered_when_the_account_also_grows():
    out = S.analyze(synth(season=SEASON, growth=1.2), today=TODAY)
    assert out["coverage"]["yearly"] and out["coverage"]["calendarMonths"] == list(range(1, 13))
    mean = sum(SEASON.values()) / 12
    for e in out["index"]["conv"]:
        assert e["index"] == pytest.approx(SEASON[e["m"]] / mean * 100, abs=2.5), e
    assert sum(e["index"] for e in out["index"]["conv"]) / 12 == pytest.approx(100, abs=0.2)


def test_growth_alone_is_not_a_season():
    """The reason for the moving average: an account that grew 40% a year has no seasonality, and must not look as if
    its late months were peaks."""
    out = S.analyze(synth(growth=1.4), today=TODAY)
    for e in out["index"]["conv"]:
        assert e["index"] == pytest.approx(100, abs=4), e
    assert not any(S.PEAK <= (e["index"] or 0) for e in out["index"]["conv"])


def test_each_month_says_how_many_years_stand_behind_it_and_whether_they_agree():
    out = S.analyze(synth(season=SEASON), today=TODAY)
    nov = out["index"]["conv"][10]
    assert nov["n"] == 2 and len(nov["years"]) == 2 and nov["agree"] == 2           # 36 whole months: two ratios a month
    assert sorted(y["y"] for y in nov["years"]) == [2024, 2025]
    one = S.analyze(synth(start="2024-10-01", season=SEASON), today=TODAY)           # 24 whole months: one
    assert one["index"]["conv"][10]["n"] == 1
    # a month whose years disagree is reported as mixed: a July that was strong in one year and weak in the other
    bad = synth(season=SEASON, spike={d: 4.0 for d in days_between("2024-07-01", "2024-07-31")})
    jul = S.analyze(bad, today=TODAY)["index"]["conv"][6]
    assert jul["n"] == 2 and jul["agree"] == 1
    assert jul["index"] > S.analyze(synth(season=SEASON), today=TODAY)["index"]["conv"][6]["index"]


def test_the_rates_are_indexed_too_and_cost_per_conversion_moves_against_demand():
    out = S.analyze(synth(season=SEASON), today=TODAY)
    conv = {e["m"]: e["index"] for e in out["index"]["conv"]}
    cpa = {e["m"]: e["index"] for e in out["index"]["cpa"]}
    assert conv[11] > 130 and cpa[11] < 80          # spend flat, conversions up: a conversion is cheaper in November
    assert conv[7] < 80 and cpa[7] > 120
    assert out["index"]["roas"][10]["index"] > 130
    assert all(e["index"] == pytest.approx(100, abs=2) for e in out["index"]["cvr"])     # conversion rate never moved


def test_less_than_13_months_in_a_row_gives_no_yearly_pattern_and_says_why():
    out = S.analyze(synth(start="2025-11-01", end="2026-10-14", season=SEASON), today=TODAY)
    c = out["coverage"]
    assert not c["yearly"] and c["monthsInARow"] == 11 and c["needed"] == 13
    assert "13 months" in c["note"] and "11" in c["note"]
    assert out["index"] is None and out["upcoming"] == []
    assert out["dow"] is not None and out["monthly"]           # what can be said, still is


def test_a_paused_stretch_is_left_out_instead_of_read_as_a_trough():
    pause = days_between("2025-03-01", "2025-04-30")
    out = S.analyze(synth(season=SEASON, pause=pause), today=TODAY)
    by = {(m["y"], m["m"]): m for m in out["monthly"]}
    assert not by[(2025, 3)]["usable"] and not by[(2025, 4)]["usable"] and by[(2025, 5)]["usable"]
    mar = out["index"]["conv"][2]
    assert mar["index"] is None or mar["index"] > 80        # it is not 0: the paused year is not counted


def test_an_account_that_never_spent_has_nothing_to_analyse():
    daily = synth(season=SEASON)
    daily["cost"] = [0.0] * len(daily["days"])
    assert S.analyze(daily, today=TODAY)["empty"] is True


def test_the_first_month_of_a_new_account_is_partial_and_not_used():
    out = S.analyze(synth(start="2024-01-10", end="2026-10-14", season=SEASON), today=TODAY)
    assert not out["monthly"][0]["full"] and not out["monthly"][0]["usable"] and out["monthly"][0]["days"] == 22
    assert out["monthly"][-1]["full"] is False             # the month under way


def test_an_account_with_no_conversion_value_gets_no_value_or_roas_index():
    daily = synth(season=SEASON)
    daily["value"] = [0.0] * len(daily["days"])
    out = S.analyze(daily, today=TODAY)
    assert all(e["index"] is None for e in out["index"]["value"]) and all(e["index"] is None for e in out["index"]["roas"])
    assert out["index"]["conv"][10]["index"] > 130


# ── The days of the week ───────────────────────────────────────────────────

def test_the_weekday_pattern_is_recovered_under_growth():
    out = S.analyze(synth(week=WEEK, growth=1.5), today=TODAY)
    mean = sum(WEEK) / 7
    idx = out["dow"]["idx"]["conv"]
    for j in range(7):
        assert idx[j] == pytest.approx(WEEK[j] / mean * 100, abs=1.5)
    assert out["dow"]["weeks"] == 52
    assert sum(idx) / 7 == pytest.approx(100, abs=0.5)


def test_the_weekday_index_needs_eight_whole_weeks():
    out = S.analyze(synth(start="2026-09-01", end="2026-10-14", week=WEEK), today=TODAY)
    assert out["dow"] is None


def test_rates_by_weekday_come_from_that_days_totals():
    daily = synth(week=WEEK)
    daily["cost"] = [c * (2.0 if dt.date.fromisoformat(d).weekday() == 5 else 1.0) for c, d in zip(daily["cost"], daily["days"])]
    idx = S.analyze(daily, today=TODAY)["dow"]["idx"]
    assert idx["cpa"][5] > idx["cpa"][1] * 2        # Saturday: dearer cost per conversion (twice the cost, fewer conversions)
    assert idx["cvr"][0] == pytest.approx(100, abs=1)


# ── The holiday calendar and what each holiday did ─────────────────────────

def test_the_us_retail_calendar_is_right_for_two_known_years():
    ev = {k: (a, b) for k, _n, a, b in S.events_of(2025)}
    assert ev["bfcm"] == (dt.date(2025, 11, 27), dt.date(2025, 12, 1))
    assert ev["mothers"][1] == dt.date(2025, 5, 11) and ev["fathers"][1] == dt.date(2025, 6, 15)
    assert ev["memorial"][1] == dt.date(2025, 5, 26) and ev["labor"][1] == dt.date(2025, 9, 1)
    assert ev["easter"][1] == dt.date(2025, 4, 20)
    ev = {k: (a, b) for k, _n, a, b in S.events_of(2026)}
    assert ev["bfcm"][0] == dt.date(2026, 11, 26) and ev["easter"][1] == dt.date(2026, 4, 5)
    assert ev["mothers"][1] == dt.date(2026, 5, 10) and ev["memorial"][1] == dt.date(2026, 5, 25) and ev["labor"][1] == dt.date(2026, 9, 7)


def test_easter_for_known_years():
    assert S.easter(2024) == dt.date(2024, 3, 31) and S.easter(2023) == dt.date(2023, 4, 9) and S.easter(2038) == dt.date(2038, 4, 25)


def test_a_holiday_that_doubled_conversions_shows_about_plus_100_percent():
    spike = {}
    for y in (2023, 2024, 2025):
        for _k, _n, a, b in S.events_of(y):
            if _k == "bfcm":
                spike.update({d: 2.0 for d in days_between(a.isoformat(), b.isoformat())})
    out = S.analyze(synth(spike=spike), today=TODAY)
    bf = next(e for e in out["events"] if e["key"] == "bfcm")
    assert bf["n"] == 2 or bf["n"] == 3
    assert bf["convLift"] == pytest.approx(1.0, abs=0.05) and bf["spendLift"] == pytest.approx(0.0, abs=0.05)
    assert bf["cpaChange"] == pytest.approx(-0.5, abs=0.03)            # twice the conversions for the same spend
    assert out["events"][0]["key"] == "bfcm"                           # strongest first


def test_a_holiday_window_not_fully_in_the_data_is_skipped_and_never_averaged_in():
    out = S.analyze(synth(start="2025-11-30", end="2026-10-14", spike={}), today=TODAY)
    keys = {e["key"] for e in out["events"]}
    assert "bfcm" not in keys                       # BFCM 2025 starts before the data does
    assert "xmasrun" not in keys                    # no ordinary days before it either


def test_the_baseline_never_contains_another_holidays_days():
    spike = {}
    for _k, _n, a, b in S.events_of(2025):
        if _k in ("prebf",):
            spike.update({d: 3.0 for d in days_between(a.isoformat(), b.isoformat())})
    out = S.analyze(synth(start="2024-10-01", spike=spike), today=TODAY)
    bf = next(e for e in out["events"] if e["key"] == "bfcm")
    assert abs(bf["convLift"]) < 0.05               # the tripled week before it did not become BFCM's "ordinary" days


def test_an_account_that_did_not_run_through_a_holiday_is_not_measured_for_it():
    pause = days_between("2024-11-20", "2024-12-05")
    out = S.analyze(synth(pause=pause), today=TODAY)
    bf = next(e for e in out["events"] if e["key"] == "bfcm")
    assert [y["y"] for y in bf["years"]] == [2023, 2025]          # 2024 was paused through it


# ── This year against last ─────────────────────────────────────────────────

def test_year_over_year_compares_the_same_weekdays_a_year_earlier():
    out = S.analyze(synth(growth=1.0, season=None), today=TODAY)
    assert [y["label"] for y in out["yoy"]] == ["Last 4 weeks", "Last 13 weeks", "Last 52 weeks"]
    assert all(y["change"]["conv"] == pytest.approx(0, abs=0.001) for y in out["yoy"])
    daily = synth()
    n = len(daily["days"])
    for k in ("impr", "clicks", "cost", "conv", "value"):
        daily[k] = [v * (2.0 if i >= n - 364 else 1.0) for i, v in enumerate(daily[k])]
    y = S.analyze(daily, today=TODAY)["yoy"][-1]
    assert y["change"]["conv"] == pytest.approx(1.0, abs=0.001) and y["change"]["cost"] == pytest.approx(1.0, abs=0.001)
    assert y["change"]["cpa"] == pytest.approx(0.0, abs=0.001)
    assert (dt.date.fromisoformat(y["end"]) - dt.date.fromisoformat(y["prevEnd"])).days == 364


def test_year_over_year_is_left_out_when_last_year_is_not_in_the_data():
    out = S.analyze(synth(start="2026-01-01"), today=TODAY)
    assert out["yoy"] == []


# ── What to do ─────────────────────────────────────────────────────────────

def test_the_verdict_and_the_budget_step():
    assert S.verdict(138, 80) == ("peak", pytest.approx(S.verdict(138, 80)[1]), 20)
    assert S.verdict(160, 80)[2] == 30 and S.verdict(300, 80)[2] == S.BUDGET_CAP_UP
    code, _a, step = S.verdict(138, 125)
    assert code == "peak_costly" and step == 20             # half the gap would be 19 -> 20, and the costly cap is 20
    assert S.verdict(200, 125)[2] == S.BUDGET_CAP_COSTLY
    assert S.verdict(71, 100)[0] == "quiet" and S.verdict(71, 100)[2] == -15
    assert S.verdict(20, 100)[2] == -S.BUDGET_CAP_DOWN
    assert S.verdict(75, 120)[0] == "quiet_costly"
    assert S.verdict(100, 100) == ("typical", S.verdict(100, 100)[1], 0)
    assert S.verdict(None, None)[0] == "unknown"
    assert S.verdict(125, None)[0] == "peak"                # no cost per conversion index: it does not block a raise


def test_the_steps_are_multiples_of_five():
    for c in range(60, 200, 3):
        assert S.verdict(c, 90)[2] % 5 == 0


def test_the_next_three_months_cross_the_year_end_and_carry_the_holidays_still_to_come():
    out = S.analyze(synth(season=SEASON), today=dt.date(2026, 11, 20))
    names = [u["name"] for u in out["upcoming"]]
    assert names == ["November", "December", "January"] and [u["y"] for u in out["upcoming"]] == [2026, 2026, 2027]
    nov, dec = out["upcoming"][0], out["upcoming"][1]
    assert nov["verdict"] in ("peak", "peak_costly") and nov["budget"] > 0
    assert [e["key"] for e in nov["events"]] == ["prebf", "bfcm"]       # the week before Thanksgiving is still running on the 20th
    assert [e["key"] for e in dec["events"]] == ["xmasrun", "xmasweek"] and out["upcoming"][2]["events"] == []


def test_a_holiday_already_over_is_not_listed_as_coming_up():
    out = S.analyze(synth(season=SEASON), today=dt.date(2026, 12, 20))
    dec = out["upcoming"][0]
    assert "bfcm" not in {e["key"] for e in dec["events"]} and "xmasrun" in {e["key"] for e in dec["events"]}


def test_no_summary_sentence_uses_an_em_dash_or_invents_a_month():
    out = S.analyze(synth(season=SEASON, week=WEEK), today=TODAY)
    text = " ".join(out["headlines"]) + " " + " ".join(u["advice"] for u in out["upcoming"])
    assert "—" not in text and "–" not in text
    assert "November" in out["headlines"][0] and "July" in out["headlines"][0]
    assert any("Tuesday" in h for h in out["headlines"])


def test_spend_that_did_not_follow_demand_is_named():
    out = S.analyze(synth(season=SEASON), today=TODAY)
    assert any("In November conversions ran at" in h and "budget did not follow demand" in h for h in out["headlines"])
    assert any("In July spend ran at" in h for h in out["headlines"])


# ── Dates and the plan of reads ────────────────────────────────────────────

def test_the_window_is_whole_months_through_yesterday():
    assert S.window(24, TODAY) == ("2024-10-01", "2026-10-15")
    assert S.window(36, dt.date(2026, 11, 1)) == ("2023-10-01", "2026-10-31")
    assert S.window(24, dt.date(2026, 1, 1)) == ("2023-12-01", "2025-12-31")


def test_slices_cover_the_range_exactly_newest_first_and_never_longer_than_the_limit():
    start, end = S.window(36, TODAY)
    plan = S.slices(start, end)
    assert all((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1 <= S.SLICE_DAYS for a, b in plan)
    assert plan[0][1] == end and plan[-1][0] == start
    ordered = plan[::-1]
    assert all(dt.date.fromisoformat(ordered[i + 1][0]) - dt.date.fromisoformat(ordered[i][1]) == dt.timedelta(days=1) for i in range(len(ordered) - 1))
    assert len(plan) == 3 and len(S.slices(*S.window(24, TODAY))) == 2
    lens = [(dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1 for a, b in plan]
    assert max(lens) - min(lens) <= 1                      # even, not a full year and a stub
    assert S.slices("2026-01-01", "2026-01-01") == [("2026-01-01", "2026-01-01")]


def test_months_and_accounts_are_validated():
    assert S.check_months(None) == 36 and S.check_months("24") == 24
    for bad in ("12", "abc", "48", "-24"):
        with pytest.raises(S.BadRequest):
            S.check_months(bad)
    assert S.parse_account(CID) == [CID] and S.parse_account(ACT) == [ACT] and S.parse_account(CID + "+" + ACT) == [CID, ACT]
    for bad in ("", "123", ACT + "+" + CID, CID + "+" + CID, "x+y", CID + "+" + ACT + "+" + ACT):
        with pytest.raises(S.BadRequest):
            S.parse_account(bad)


# ── Reading Google Ads ─────────────────────────────────────────────────────

class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {}

    def json(self):
        return self._b


def span(q):
    return re.search(r"BETWEEN '(\d{4}-\d\d-\d\d)' AND '(\d{4}-\d\d-\d\d)'", q).groups()


def google_account(log, fail=None):
    """A Google that answers an account-level query with one row a day."""
    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        log.append(q)
        if fail:
            return Resp(fail[0], fail[1])
        a, b = span(q)
        rows = [{"segments": {"date": d}, "metrics": {"impressions": "100", "clicks": "10", "costMicros": "5000000",
                                                      "conversions": 2.0, "conversionsValue": 90.0}} for d in days_between(a, b)]
        return Resp(200, {"results": rows})
    return http


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in (("GOOGLE_ADS_CLIENT_ID", "cid"), ("GOOGLE_ADS_CLIENT_SECRET", "sec"),
                 ("GOOGLE_ADS_DEVELOPER_TOKEN", "dev"), ("GOOGLE_ADS_REFRESH_TOKEN", "rt"), ("META_ADS_ACCESS_TOKEN", "x")):
        monkeypatch.setenv(k, v)
    g.reset_token_cache()
    g.reset_state()
    monkeypatch.setitem(g._TOKEN, "value", "tok")
    monkeypatch.setitem(g._TOKEN, "exp", 9e18)


def yesterday_range(n=30):
    end = dt.date.today() - dt.timedelta(days=1)
    return (end - dt.timedelta(days=n - 1)).isoformat(), end.isoformat()


def test_the_google_read_is_one_row_a_day_for_the_whole_account_and_asks_for_no_campaigns():
    log, st = [], FakeStore()
    a, b = yesterday_range(40)
    day = S.read_part(st, {"id": CID, "platform": "google"}, a, b, http=google_account(log))
    assert len(log) == 1 and "FROM customer" in log[0] and "campaign" not in log[0].lower().split("from")[0]
    assert len(day["days"]) == 40 and day["cost"][0] == 5.0 and day["conv"][3] == 2.0 and day["value"][5] == 90.0
    assert day["impr"][0] == 100 and day["clicks"][0] == 10


def test_account_days_are_stored_apart_from_the_campaign_days_and_read_back_without_asking():
    log, st = [], FakeStore()
    a, b = yesterday_range(30)
    S.read_part(st, {"id": CID, "platform": "google"}, a, b, http=google_account(log))
    assert {c for c, _d in st.days} == {CID + ":acct"}                   # never the plain account key
    n = len(log)
    again = S.read_part(st, {"id": CID, "platform": "google"}, a, b, http=google_account(log))
    assert len(log) == n and sum(again["cost"]) == 150.0                 # stored: nothing asked
    # and a campaign-level read of the same account still asks Google, because its days are a different store
    campaign_log = []

    def campaign_http(url, headers=None, data=None, **k):
        campaign_log.append(json.loads(data)["query"])
        return Resp(200, {"results": []})
    g.campaign_days(st, CID, a, b, http=campaign_http)
    assert campaign_log and "FROM campaign" in campaign_log[0]


def test_refresh_reads_the_asked_days_again_even_when_stored():
    log, st = [], FakeStore()
    a, b = yesterday_range(10)
    S.read_part(st, {"id": CID, "platform": "google"}, a, b, http=google_account(log))
    n = len(log)
    S.read_part(st, {"id": CID, "platform": "google"}, a, b, force=True, http=google_account(log))
    assert len(log) == n + 1


def test_a_read_that_fails_after_days_were_stored_serves_the_stored_days():
    log, st = [], FakeStore()
    a, b = yesterday_range(10)
    S.read_part(st, {"id": CID, "platform": "google"}, a, b, http=google_account(log))
    notes = []
    day = S.read_part(st, {"id": CID, "platform": "google"}, a, b, force=True, notes=notes,
                      http=google_account(log, fail=(503, {"error": {"message": "backend"}})))
    assert len(notes) >= 1 and sum(day["cost"]) == 50.0


def test_a_long_history_is_read_in_slices_and_laid_end_to_end():
    log, st = [], FakeStore()
    start, end = S.window(24, dt.date.today())
    daily, problems, notes, stamps = S.read(st, [{"id": CID, "platform": "google"}], start, end, http=google_account(log))
    assert len(log) == len(S.slices(start, end)) == 2
    assert daily["days"][0] == start and daily["days"][-1] == end and len(daily["days"]) == len(set(daily["days"]))
    assert problems == [] and all(c == 5.0 for c in daily["cost"])


def test_warm_reads_one_slice_newest_first_and_reports_how_many_there_are():
    log, st = [], FakeStore()
    start, end = S.window(24, dt.date.today())
    w0 = S.warm(st, [{"id": CID, "platform": "google"}], start, end, 0, http=google_account(log))
    assert w0["part"] == 0 and w0["parts"] == 2 and w0["end"] == end and len(log) == 1
    w1 = S.warm(st, [{"id": CID, "platform": "google"}], start, end, 1, http=google_account(log))
    assert w1["start"] == start and w1["end"] < w0["start"]
    with pytest.raises(S.BadRequest):
        S.warm(st, [{"id": CID, "platform": "google"}], start, end, 2, http=google_account(log))
    with pytest.raises(S.BadRequest):
        S.warm(st, [{"id": CID, "platform": "google"}], start, end, -1, http=google_account(log))


def test_a_google_error_with_nothing_stored_is_raised_for_a_single_account():
    st = FakeStore()
    a, b = yesterday_range(10)
    with pytest.raises(g.AdsError):
        S.read_part(st, {"id": CID, "platform": "google"}, a, b, http=google_account([], fail=(403, {"error": {"message": "no"}})))


# ── Google and Meta together ───────────────────────────────────────────────

def fake_meta(monkeypatch, per_day=(3.0, 0.5), fail=None):
    """Meta's campaign_days replaced: one campaign, `per_day` = (cost, conversions) a day."""
    def campaign_days(store, act, start, end, **k):
        if fail:
            raise fail
        days = days_between(start, end)
        return {"days": days, "campaigns": [{"id": "1", "type": "META_SALES"}],
                "rows": [[0, i, 50, 5, per_day[0], per_day[1], 20.0] + [0] * 12 for i in range(len(days))], "missing": []}
    monkeypatch.setattr(meta, "campaign_days", campaign_days)


def test_two_platforms_are_added_day_by_day(monkeypatch):
    fake_meta(monkeypatch)
    st, log = FakeStore(), []
    a, b = yesterday_range(20)
    accts = [{"id": CID, "platform": "google", "currency": "USD"}, {"id": ACT, "platform": "meta", "currency": "USD"}]
    daily, problems, _n, _s = S.read(st, accts, a, b, http=google_account(log))
    assert problems == [] and daily["cost"][0] == 8.0 and daily["conv"][0] == 2.5 and daily["clicks"][0] == 15 and daily["impr"][0] == 150
    assert daily["value"][0] == 110.0


def test_a_platform_that_cannot_be_read_is_left_out_and_named(monkeypatch):
    fake_meta(monkeypatch, fail=meta.AdsError("Meta said no"))
    st, log = FakeStore(), []
    a, b = yesterday_range(10)
    accts = [{"id": CID, "platform": "google", "currency": "USD"}, {"id": ACT, "platform": "meta", "currency": "USD"}]
    daily, problems, _n, _s = S.read(st, accts, a, b, http=google_account(log))
    assert daily["cost"][0] == 5.0
    assert len(problems) == 1 and problems[0].startswith("Meta Ads could not be read") and "Meta said no" in problems[0]


def test_both_failing_raises(monkeypatch):
    fake_meta(monkeypatch, fail=meta.AdsError("Meta said no"))
    st = FakeStore()
    a, b = yesterday_range(10)
    accts = [{"id": CID, "platform": "google", "currency": "USD"}, {"id": ACT, "platform": "meta", "currency": "USD"}]
    with pytest.raises(g.AdsError):
        S.read(st, accts, a, b, http=google_account([], fail=(403, {"error": {"message": "no"}})))


def test_the_daily_allowance_being_spent_is_never_swallowed_as_a_one_platform_problem(monkeypatch):
    fake_meta(monkeypatch, fail=g.OverQuota("spent", until=1.0))
    st = FakeStore()
    a, b = yesterday_range(10)
    accts = [{"id": CID, "platform": "google", "currency": "USD"}, {"id": ACT, "platform": "meta", "currency": "USD"}]
    with pytest.raises(g.OverQuota):
        S.read(st, accts, a, b, http=google_account([]))


def test_accounts_billed_in_different_currencies_cannot_be_added():
    with pytest.raises(S.BadRequest):
        S.report(FakeStore(), [{"id": CID, "platform": "google", "currency": "USD"}, {"id": ACT, "platform": "meta", "currency": "EUR"}], 24)


def test_a_meta_account_is_read_through_meta_not_google(monkeypatch):
    seen = []
    monkeypatch.setattr(meta, "campaign_days", lambda store, act, a, b, **k: seen.append(act) or {"days": days_between(a, b), "campaigns": [], "rows": [], "missing": []})
    a, b = yesterday_range(5)
    day = S.read_part(FakeStore(), {"id": ACT, "platform": "meta", "currency": "USD"}, a, b)
    assert seen == [ACT] and sum(day["cost"]) == 0


# ── Adding up stored days ──────────────────────────────────────────────────

def test_daily_totals_add_every_campaign_of_a_day_and_keep_quiet_days_as_zero():
    shape = {"days": ["2026-01-01", "2026-01-02", "2026-01-03"], "campaigns": [{}, {}],
             "rows": [[0, 0, 100, 10, 5.0, 1.0, 40.0] + [0] * 12, [1, 0, 50, 5, 2.5, 0.5, 10.0] + [0] * 12, [0, 2, 10, 1, 1.0, 0.0, 0.0] + [0] * 12]}
    t = S.daily_totals(shape)
    assert t["impr"] == [150, 0, 10] and t["clicks"] == [15, 0, 1] and t["cost"] == [7.5, 0, 1.0] and t["conv"] == [1.5, 0, 0] and t["value"] == [50.0, 0, 0]


def test_two_reads_over_different_days_are_never_added():
    a = {"days": ["2026-01-01"], **{k: [1.0] for k in S.SUMS}}
    b = {"days": ["2026-01-02"], **{k: [1.0] for k in S.SUMS}}
    with pytest.raises(ValueError):
        S._add(a, b)


# ── The whole report ───────────────────────────────────────────────────────

def test_the_report_carries_the_window_the_currency_and_when_it_was_read(monkeypatch):
    st = FakeStore()
    seen = {}

    def read(store, accts, start, end, **k):
        seen.update(start=start, end=end)
        return synth(start=start, end=end, season=SEASON), ["a warning"], [], ["2026-10-10T08:00:00+00:00"]
    monkeypatch.setattr(S, "read", read)
    out = S.report(st, [{"id": CID, "platform": "google", "currency": "EUR"}], 24, today=TODAY)
    assert out["currency"] == "EUR" and out["months"] == 24 and out["warnings"] == ["a warning"]
    assert (out["requestedStart"], out["requestedEnd"]) == ("2024-10-01", "2026-10-15") == (seen["start"], seen["end"])
    assert out["fetchedAt"] == "2026-10-10T08:00:00+00:00" and out["stale"] is None
    assert out["thresholds"] == {"peak": S.PEAK, "trough": S.TROUGH, "dear": S.DEAR}


# ── The routes ─────────────────────────────────────────────────────────────

STAFF = "someone@position2.com"
ACCTS = [{"id": CID, "name": "Acme", "currency": "USD", "tz": "America/New_York"}]


def client_as(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def routes(monkeypatch):
    fs = FakeStore()
    for name in ("get_gads_entry", "put_gads_cache", "get_gads_cache", "gads_calls", "get_gads_block"):
        monkeypatch.setattr(paid_store, name, getattr(fs, name))
    monkeypatch.setattr(g, "list_accounts", lambda st, **k: ACCTS)
    monkeypatch.setattr(meta, "list_accounts", lambda st, **k: [{"id": ACT, "name": "Acme Meta", "currency": "USD", "tz": "", "platform": "meta"}])
    calls = []

    def read_part(store, acct, first, last, **k):
        calls.append((acct["id"], first, last, k.get("force")))
        return synth(start=first, end=last, season=SEASON)
    monkeypatch.setattr(S, "read_part", read_part)
    return calls


def test_the_page_opens_for_staff_and_not_for_anyone_else():
    path = "/p2/paid-marketing/seasonality-analysis"
    page = client_as(STAFF).get(path)
    assert page.status_code == 200 and b"sa_data.js" in page.data and b"seasonality_analysis.js" in page.data
    assert b"Seasonality Analysis" in page.data
    assert appmod.app.test_client().get(path).status_code in (302, 401, 403)
    assert client_as("someone@gmail.com").get(path).status_code in (302, 401, 403)


def test_the_analysis_route_returns_the_report_and_the_account(routes):
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/seasonality?account=" + CID)
    j = r.get_json()
    assert r.status_code == 200 and j["coverage"]["yearly"] is True and j["accounts"][0]["name"] == "Acme" and j["accounts"][0]["platform"] == "google"
    assert j["months"] == 36 and len(j["upcoming"]) == 3 and j["blockedUntil"] is None
    assert len(routes) == len(S.slices(*S.window(36)))


def test_a_combined_account_reads_both_halves_and_names_both(routes):
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/seasonality?account=%s%%2B%s&months=24" % (CID, ACT))
    j = r.get_json()
    assert r.status_code == 200 and [a["platform"] for a in j["accounts"]] == ["google", "meta"] and j["months"] == 24
    assert {c[0] for c in routes} == {CID, ACT}


def test_a_plus_sign_that_arrives_as_a_space_is_not_taken_for_a_combined_account(routes):
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/seasonality?account=%s+%s" % (CID, ACT))
    assert r.status_code == 400


def test_bad_arguments_are_a_400_and_never_reach_the_platform(routes):
    c = client_as(STAFF)
    for url in ("account=abc", "account=" + CID + "&months=12", "account=", "account=" + CID + "&months=x"):
        assert c.get("/p2/paid-marketing/api/gads/seasonality?" + url).status_code == 400, url
        assert c.get("/p2/paid-marketing/api/gads/seasonality/warm?" + url).status_code == 400, url
    assert c.get("/p2/paid-marketing/api/gads/seasonality/warm?account=" + CID + "&part=9").status_code == 400
    assert c.get("/p2/paid-marketing/api/gads/seasonality/warm?account=" + CID + "&part=zero").status_code == 400
    assert routes == []


def test_an_account_the_manager_does_not_hold_is_refused(routes):
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/seasonality?account=9999999999")
    assert r.status_code == 400 and "not under Position2" in r.get_json()["error"]
    assert routes == []


def test_the_warm_route_reads_one_slice_and_says_how_many_there_are(routes):
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/seasonality/warm?account=%s&part=1" % CID)
    j = r.get_json()
    assert r.status_code == 200 and j["part"] == 1 and j["parts"] == 3 and j["problems"] == [] and len(routes) == 1
    assert routes[0][1:3] == (j["start"], j["end"]) and routes[0][3] in (False, None)


def test_refresh_reads_only_the_latest_45_days_forced_and_is_refused_twice_in_a_row(routes):
    c = client_as(STAFF)
    r = c.get("/p2/paid-marketing/api/gads/seasonality/warm?account=%s&refresh=1" % CID)
    assert r.status_code == 200
    _cid, first, last, force = routes[0]
    assert force is True and (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days + 1 == S.REFRESH_DAYS
    again = c.get("/p2/paid-marketing/api/gads/seasonality/warm?account=%s&refresh=1" % CID)
    assert again.status_code == 429 and again.get_json()["cooldown"] is True and len(routes) == 1


def test_an_unconnected_platform_is_a_409_for_one_account_and_a_warning_for_a_combined_one(routes, monkeypatch):
    monkeypatch.delenv("META_ADS_ACCESS_TOKEN")
    c = client_as(STAFF)
    r = c.get("/p2/paid-marketing/api/gads/seasonality?account=" + ACT)
    assert r.status_code == 409 and "META_ADS_ACCESS_TOKEN" in r.get_json()["configProblem"]
    j = c.get("/p2/paid-marketing/api/gads/seasonality?account=%s%%2B%s" % (CID, ACT)).get_json()
    assert [a["platform"] for a in j["accounts"]] == ["google"] and any("META_ADS_ACCESS_TOKEN" in w for w in j["warnings"])


def test_the_spent_allowance_is_a_429_with_the_time_it_comes_back(routes, monkeypatch):
    def spent(*a, **k):
        raise g.OverQuota("The daily allowance is spent.", until=1893456000.0)
    monkeypatch.setattr(S, "read_part", spent)
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/seasonality/warm?account=" + CID)
    assert r.status_code == 429 and r.get_json()["blockedUntil"] == 1893456000.0


def test_a_platform_error_is_a_502_with_its_message(routes, monkeypatch):
    def boom(*a, **k):
        raise g.AdsError("Google said no")
    monkeypatch.setattr(S, "read_part", boom)
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/seasonality?account=" + CID)
    assert r.status_code == 502 and r.get_json()["error"] == "Google said no"


# ── Boundaries and the cases a first pass of mutation testing found untested ──

def test_with_fewer_than_twelve_months_of_ratios_growth_is_still_taken_out_exactly():
    """No scaling to 100 happens when some calendar months have no ratio, so the moving average itself must be centred: with
    steady 50% growth and no season every ratio the 15 months give is 100."""
    out = S.analyze(synth(start="2025-07-01", end="2026-10-14", growth=1.5), today=TODAY)
    got = [e["index"] for e in out["index"]["conv"] if e["index"] is not None]
    assert 1 <= len(got) < 12
    assert all(v == pytest.approx(100, abs=0.9) for v in got), got


def test_the_twelve_indexes_are_scaled_to_average_exactly_100():
    out = S.analyze(synth(season=SEASON, growth=1.6), today=TODAY)
    vals = [e["index"] for e in out["index"]["conv"]]
    assert sum(vals) / 12 == pytest.approx(100, abs=0.06)


def test_another_holidays_days_are_not_ordinary_days_for_the_baseline():
    spike = {d: 3.0 for d in days_between("2024-12-15", "2024-12-24")}              # the Christmas run-up of 2024 tripled
    spike.update({d: 3.0 for d in days_between("2025-12-15", "2025-12-24")})
    out = S.analyze(synth(start="2023-10-01", spike=spike), today=TODAY)
    xw = next(e for e in out["events"] if e["key"] == "xmasweek")
    assert abs(xw["convLift"]) < 0.05              # its baseline would be the tripled run-up if it were not left out


def test_the_run_up_days_just_before_a_window_are_not_its_baseline():
    spike = {}
    for y in (2024, 2025):
        spike.update({d: 3.0 for d in days_between("%d-01-31" % y, "%d-02-06" % y)})   # the week before Valentine's run-up
    out = S.analyze(synth(spike=spike), today=TODAY)
    v = next(e for e in out["events"] if e["key"] == "valentines")
    assert abs(v["convLift"]) < 0.05


def test_the_boundaries_of_the_verdict_are_where_they_are_said_to_be():
    assert S.verdict(110, 100)[0] == "peak" and S.verdict(109.9, 100)[0] == "typical"
    assert S.verdict(90, 100)[0] == "quiet" and S.verdict(90.1, 100)[0] == "typical"
    assert S.verdict(130, 105)[0] == "peak" and S.verdict(130, 105.1)[0] == "peak_costly"
    assert S.verdict(90, 105)[0] == "quiet_costly" and S.verdict(90, 104.9)[0] == "quiet"
    assert (S.PEAK, S.TROUGH, S.DEAR) == (110, 90, 105)


def test_the_budget_caps_are_the_numbers_the_page_promises():
    assert S.verdict(300, 80)[2] == 40 and S.verdict(300, 120)[2] == 20 and S.verdict(0.0, 80)[2] == -30 and S.verdict(0.0, 120)[2] == -30


def test_spend_that_follows_conversions_month_for_month_raises_no_gap_sentence():
    daily = synth(season=SEASON)
    daily["cost"] = [200.0 * SEASON[dt.date.fromisoformat(d).month] for d in daily["days"]]
    heads = " ".join(S.analyze(daily, today=TODAY)["headlines"])
    assert "did not follow demand" not in heads and "ran ahead of results" not in heads


def test_no_weekday_headline_when_every_day_is_alike():
    out = S.analyze(synth(season=SEASON), today=TODAY)
    assert not any("strongest day" in h for h in out["headlines"])


def test_an_account_with_spend_but_no_conversions_does_not_crash_and_has_no_cost_per_conversion():
    daily = synth(season=SEASON)
    daily["conv"] = [0.0] * len(daily["days"])
    daily["value"] = [0.0] * len(daily["days"])
    out = S.analyze(daily, today=TODAY)
    assert all(e["index"] is None for e in out["index"]["cpa"]) and all(e["index"] is None for e in out["index"]["conv"])
    assert out["upcoming"][0]["verdict"] == "unknown" and out["upcoming"][0]["budget"] == 0
    assert out["index"]["cost"][10]["index"] is not None


def test_meta_is_read_in_small_slices_and_google_alone_in_whole_years():
    start, end = S.window(36, TODAY)
    g_only, m_only = [{"platform": "google"}], [{"platform": "meta"}]
    assert S.slice_size(g_only) == S.SLICE_DAYS and S.slice_size(m_only) == S.SLICE_DAYS_META and S.slice_size(g_only + m_only) == S.SLICE_DAYS_META
    plan = S.slices(start, end, S.slice_size(m_only))
    assert len(plan) == 12 and all((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1 <= 93 for a, b in plan)
    assert plan[0][1] == end and plan[-1][0] == start


def test_the_warm_route_counts_more_parts_for_a_meta_account_than_for_a_google_one(routes):
    c = client_as(STAFF)
    g_parts = c.get("/p2/paid-marketing/api/gads/seasonality/warm?account=" + CID).get_json()["parts"]
    m_parts = c.get("/p2/paid-marketing/api/gads/seasonality/warm?account=" + ACT).get_json()["parts"]
    meta_slices = len(S.slices(*S.window(36, dt.date.today()), S.SLICE_DAYS_META))               # the route reads up to today, so the count moves with the calendar (12 one day, 13 the next)
    assert (g_parts, m_parts) == (3, meta_slices) and meta_slices in (12, 13)
    assert c.get("/p2/paid-marketing/api/gads/seasonality/warm?account=%s%%2B%s" % (CID, ACT)).get_json()["parts"] == meta_slices
