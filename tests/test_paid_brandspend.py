"""Brand Spend Alert: reading Google's answer, an MCC, the limit and overrides, the report and its cache (tracker/paid_brandspend.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_brandspend as b  # noqa: E402
from tracker import paid_brandspend_math as m  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 20)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}
USUAL = [203, 196, 210, 189, 203, 210, 196]


def api(cid="11", name="P2_Brand", date="2026-10-14", cost="29000000", clicks="10", impr="100", status="ENABLED", strategy="MAXIMIZE_CLICKS", budget="40000000"):
    row = {"campaign": {"id": cid, "name": name, "status": status, "biddingStrategyType": strategy}, "segments": {"date": date},
           "metrics": {"impressions": impr, "clicks": clicks, "costMicros": cost}}
    if budget is not None:
        row["campaignBudget"] = {"amountMicros": budget}
    return row


def weeks_of(cid="11", name="P2_Brand", weekly=None, clicks=70, **kw):
    """API rows for a campaign: one row a day for the eight whole weeks, `weekly` costs (oldest first, None for a week with no spend) spread over the seven days."""
    weekly = weekly if weekly is not None else USUAL + [203]
    out = []
    first = m.weeks(TODAY)[0][0]
    for i, cost in enumerate(weekly):
        if cost is None:
            continue
        for d in range(7):
            out.append(api(cid, name, (first + dt.timedelta(days=7 * i + d)).isoformat(), str(int(round(cost / 7.0 * 1e6))), str(int(clicks / 7)), **kw))
    return out


def searcher(by_account=None, log=None, fail=()):
    by_account = by_account if by_account is not None else {ACCT["id"]: weeks_of()}

    def search(cid, query):
        if log is not None:
            log.append((cid, query))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        return by_account.get(cid, [])
    return search


# ── Reading the answer ──────────────────────────────────────────────────────

def test_a_row_is_read_into_the_shape_the_arithmetic_wants():
    r = b.read_rows([api()])[0]
    assert r == {"id": "11", "name": "P2_Brand", "date": "2026-10-14", "status": "ENABLED", "strategy": "MAXIMIZE_CLICKS", "budget": 40.0, "impressions": 100, "clicks": 10, "cost": 29.0}


def test_a_missing_budget_stays_none_and_strings_are_numbers():
    r = b.read_rows([api(budget=None, cost="12345678", clicks="7")])[0]
    assert r["budget"] is None and r["cost"] == 12.35 and r["clicks"] == 7


def test_a_row_without_a_campaign_or_a_day_is_dropped_and_so_is_a_row_with_nothing_in_it():
    rows = [api(), {"campaign": {}, "segments": {"date": "2026-10-14"}, "metrics": {}}, {"campaign": {"id": "9"}, "segments": {}, "metrics": {}}, {},
            api(cid="12", cost="0", clicks="0", impr="0"), api(cid="13", cost="0", clicks="0", impr="50")]
    assert [r["id"] for r in b.read_rows(rows)] == ["11", "13"]                      # impressions with no cost are kept: the campaign is there and spent nothing


def test_the_query_asks_for_search_campaigns_not_removed_by_day_from_the_oldest_week_to_yesterday():
    first, last = m.window(TODAY)
    q = b.gaql(first, last)
    assert "campaign.advertising_channel_type = 'SEARCH'" in q and "campaign.status != 'REMOVED'" in q and "segments.date" in q and "campaign.status," in q
    assert "BETWEEN '2026-08-24' AND '2026-10-19'" in q
    for f in ("metrics.cost_micros", "metrics.clicks", "metrics.impressions", "bidding_strategy_type", "campaign_budget.amount_micros"):
        assert f in q


# ── An account and an MCC ───────────────────────────────────────────────────

def test_one_account_is_read_once():
    log = []
    raw = b.google_raw(ACCT, TODAY, search_fn=searcher(log=log))
    assert len(log) == 1 and log[0][0] == "1234567890" and raw["members"] == [] and raw["warnings"] == []
    assert raw["accounts"][0]["name"] == "Acme Dental" and len(raw["accounts"][0]["rows"]) == 56


def test_an_mcc_reads_every_account_and_says_the_one_it_could_not_read():
    mcc = {"id": "mcc-1", "name": "Group", "currency": "USD", "members": [ACCT, ACCT2]}
    raw = b.google_raw(mcc, TODAY, search_fn=searcher(fail=(ACCT2["id"],)))
    assert [a["name"] for a in raw["accounts"]] == ["Acme Dental"] and raw["members"] == ["Acme Dental", "Beta Clinic"]
    assert len(raw["warnings"]) == 1 and "Beta Clinic could not be read" in raw["warnings"][0]


def test_every_account_failing_is_raised():
    mcc = {"id": "mcc-1", "name": "Group", "members": [ACCT, ACCT2]}
    with pytest.raises(gads.GadsError, match="cannot be read"):
        b.google_raw(mcc, TODAY, search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])))


# ── The limit ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,want", [(None, 0.3), ("", 0.3), ("30", 0.3), ("30%", 0.3), (" 25 ", 0.25), ("0.4", 0.4), (50, 0.5), (0.2, 0.2), ("10", 0.1), ("90", 0.9)])
def test_the_limit_is_read_as_a_percentage_or_a_fraction(value, want):
    assert b.parse_limit(value) == want


@pytest.mark.parametrize("value", ["9", "91", "0.05", "abc", "-5", "nan", "inf", "100"])
def test_a_limit_outside_10_to_90_or_not_a_number_is_refused(value):
    with pytest.raises(b.BadRequest):
        b.parse_limit(value)


def test_the_overrides_are_brand_is_monitors():
    assert b.parse_overrides("1,2", "3") == {"1": True, "2": True, "3": False}


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
    return b.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, **kw)


def test_the_report_says_which_week_which_limit_and_what_it_found(cache):
    rep = report(searcher({ACCT["id"]: weeks_of(weekly=USUAL + [301])}))
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and rep["limit"] == 0.3
    assert rep["window"] == {"from": "2026-08-24", "to": "2026-10-18", "weeks": 8, "latestFrom": "2026-10-12", "latestTo": "2026-10-18"}
    a = rep["accounts"][0]
    assert a["status"] == "over" and a["brand"]["cost"] == 301.0 and a["expected"]["cost"] == 203.0 and a["findings"][0]["level"] == "medium" and a["campaignCount"] == 1
    assert rep["summary"] == {"counts": {"stopped": 0, "over": 1, "under": 0, "new": 0, "ok": 0, "idle": 0, "none": 0}, "accounts": 1, "needsLook": 1, "high": 0, "thisWeek": 0,
                              "overall": {"cost": 301.0, "expected": 203.0, "accounts": 1}}
    assert rep["readAt"]


def test_a_second_ask_a_new_limit_and_a_new_override_never_read_google_again_and_refresh_does(cache):
    calls = []
    search = searcher({ACCT["id"]: weeks_of(weekly=USUAL + [301])}, log=calls)
    first = report(search)
    n = len(calls)
    assert first["accounts"][0]["status"] == "over"
    wide = report(search, limit="60")
    assert len(calls) == n and wide["accounts"][0]["status"] == "ok" and wide["limit"] == 0.6
    forced_out = report(search, overrides={"11": False})
    assert len(calls) == n and forced_out["accounts"][0]["status"] == "none" and forced_out["accounts"][0]["otherCampaigns"][0]["byOverride"] is True
    report(search, force=True)
    assert len(calls) > n


def test_an_mcc_report_puts_the_worst_account_first_and_counts_each_state(cache):
    accounts = [ACCT, ACCT2, {"id": "3333333333", "name": "Gamma", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"},
                {"id": "5555555555", "name": "Epsilon", "currency": "USD"}, {"id": "6666666666", "name": "Zeta", "currency": "USD"}]
    search = searcher({ACCT["id"]: weeks_of(weekly=USUAL + [203]),                                        # on its usual
                       ACCT2["id"]: weeks_of(weekly=USUAL + [301]),                                       # over by 48%
                       "3333333333": weeks_of(cid="33", name="Core", weekly=USUAL + [301]),               # no brand campaign
                       "4444444444": weeks_of(cid="44", weekly=USUAL + [None]),                           # stopped
                       "5555555555": weeks_of(cid="55", weekly=USUAL + [98]),                             # under by 52%
                       "6666666666": weeks_of(cid="66", weekly=[None] * 5 + [100, 100, 100])})            # new: too few weeks
    rep = b.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=search, today=TODAY)
    assert [(a["name"], a["status"]) for a in rep["accounts"]] == [("Delta", "stopped"), ("Beta Clinic", "over"), ("Epsilon", "under"), ("Zeta", "new"), ("Acme Dental", "ok"), ("Gamma", "none")]
    s = rep["summary"]
    assert s["counts"] == {"stopped": 1, "over": 1, "under": 1, "new": 1, "ok": 1, "idle": 0, "none": 1} and s["needsLook"] == 3 and s["high"] == 1 and s["accounts"] == 6
    assert s["overall"] == {"cost": 203.0 + 301.0 + 0 + 98.0, "expected": 203.0 * 4, "accounts": 4}               # the accounts that have a usual week: Acme, Beta, Delta and Epsilon
    assert rep["account"]["isGroup"] and rep["account"]["accountCount"] == 6


def test_accounts_in_the_same_state_are_ordered_by_how_far_they_are_from_usual_and_so_high_comes_first(cache):
    accounts = [ACCT, ACCT2, {"id": "3333333333", "name": "Gamma", "currency": "USD"}]
    search = searcher({ACCT["id"]: weeks_of(weekly=USUAL + [301]), ACCT2["id"]: weeks_of(cid="22", weekly=USUAL + [329]), "3333333333": weeks_of(cid="33", weekly=USUAL + [259])})
    rep = b.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=search, today=TODAY)
    assert [(a["name"], a["level"]) for a in rep["accounts"]] == [("Beta Clinic", "high"), ("Acme Dental", "medium")] + [("Gamma", None)]


def test_a_read_that_lost_an_account_is_shown_with_a_warning_and_is_not_saved_for_hours(cache):
    accounts = [ACCT, ACCT2]
    kw = dict(accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
              search_fn=searcher(fail=(ACCT2["id"],)), today=TODAY)
    rep = b.report_for("mcc-9923720096-USD", **kw)
    assert len(rep["accounts"]) == 1 and "Beta Clinic could not be read" in rep["warnings"][0]
    saved = lambda: [k for k in cache if not k.startswith("rl:")]
    assert saved() == []                                         # the saved copy waits for a whole read
    whole = b.report_for("mcc-9923720096-USD", **dict(kw, search_fn=searcher(), force=True))
    assert whole["warnings"] == [] and len(saved()) == 1


def test_a_spent_quota_serves_the_last_saved_read_with_its_date(cache, monkeypatch):
    from tracker import paid_geo
    assert "stale" not in report(searcher(), now=0)

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: None)
    monkeypatch.setattr(paid_store, "get_gads_cache_any", lambda key: (cache.get(key), "2026-10-19T10:00:00+00:00") if key in cache else None, raising=False)
    paid_geo.clear_cache()
    base.forget("")
    old = report(spent, now=10 ** 9)
    assert old["stale"] == "2026-10-19T10:00:00+00:00" and old["accounts"][0]["status"] == "ok"


def test_with_no_saved_read_a_spent_quota_is_raised(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        report(spent)


def test_an_account_that_is_not_position2s_is_refused(cache):
    with pytest.raises(b.BadRequest):
        b.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY)


def test_this_week_so_far_is_counted_only_for_an_account_that_is_otherwise_on_its_usual(cache):
    thu = dt.date(2026, 10, 22)

    def part_week(rows):
        return rows + [api(cid=rows[0]["campaign"]["id"], date=(dt.date(2026, 10, 19) + dt.timedelta(days=d)).isoformat(), cost="87000000", clicks="10") for d in range(3)]       # three times the usual pace
    accounts = [ACCT, ACCT2]
    search = searcher({ACCT["id"]: part_week(weeks_of()), ACCT2["id"]: part_week(weeks_of(cid="22", weekly=USUAL + [301]))})
    rep = b.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=search, today=thu)
    by = {a["name"]: a for a in rep["accounts"]}
    assert by["Acme Dental"]["status"] == "ok" and by["Acme Dental"]["partial"]["status"] == "over" and by["Beta Clinic"]["status"] == "over" and by["Beta Clinic"]["partial"]["status"] == "over"
    assert rep["summary"]["thisWeek"] == 1 and rep["summary"]["needsLook"] == 1                  # Beta is already counted as over; only Acme is a part week on its own
    assert b.overall([]) == {"cost": None, "expected": None, "accounts": 0}
