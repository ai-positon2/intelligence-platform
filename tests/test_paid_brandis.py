"""Brand IS Monitor: reading Google's answer, an MCC, the threshold and overrides, the report and its cache (tracker/paid_brandis.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_brandis as b  # noqa: E402
from tracker import paid_brandis_math as m  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 20)
NOW, BEFORE = "2026-10-12", "2026-10-05"
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}


def api(cid="11", name="P2_Brand", week=NOW, impr="1000", share=0.95, lb=0.02, lr=0.03, top=0.8, abs_top=0.6, budget="20000000", strategy="MAXIMIZE_CLICKS"):
    mt = {"impressions": impr, "clicks": "100", "costMicros": "50500000"}
    for k, v in (("searchImpressionShare", share), ("searchBudgetLostImpressionShare", lb), ("searchRankLostImpressionShare", lr),
                 ("searchTopImpressionShare", top), ("searchAbsoluteTopImpressionShare", abs_top)):
        if v is not None:
            mt[k] = v
    row = {"campaign": {"id": cid, "name": name, "biddingStrategyType": strategy}, "segments": {"week": week}, "metrics": mt}
    if budget is not None:
        row["campaignBudget"] = {"amountMicros": budget}
    return row


def searcher(by_account=None, log=None, fail=()):
    by_account = by_account or {ACCT["id"]: [api(), api(week=BEFORE)]}

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
    assert r == {"id": "11", "name": "P2_Brand", "week": NOW, "strategy": "MAXIMIZE_CLICKS", "budget": 20.0, "impressions": 1000, "clicks": 100, "cost": 50.5,
                 "share": 0.95, "lostBudget": 0.02, "lostRank": 0.03, "top": 0.8, "absTop": 0.6}


def test_a_missing_share_stays_none_and_is_not_a_zero():
    r = b.read_rows([api(share=None, lb=None, lr=None, top=None, abs_top=None, budget=None)])[0]
    assert r["share"] is None and r["lostBudget"] is None and r["lostRank"] is None and r["top"] is None and r["budget"] is None and r["impressions"] == 1000


def test_a_tiny_share_is_kept_as_google_reports_it_and_strings_are_numbers():
    r = b.read_rows([api(share="0.0999", impr="12")])[0]
    assert r["share"] == 0.0999 and r["impressions"] == 12


def test_a_row_without_a_campaign_or_a_week_is_dropped():
    rows = [api(), {"campaign": {}, "segments": {"week": NOW}, "metrics": {}}, {"campaign": {"id": "9"}, "segments": {}, "metrics": {}}, {}]
    assert [r["id"] for r in b.read_rows(rows)] == ["11"]


def test_the_query_asks_for_enabled_search_campaigns_by_week_over_the_eight_weeks():
    wk = m.weeks(TODAY)
    q = b.gaql(wk[0][0], wk[-1][1])
    assert "campaign.advertising_channel_type = 'SEARCH'" in q and "campaign.status = 'ENABLED'" in q and "segments.week" in q
    assert "BETWEEN '2026-08-24' AND '2026-10-18'" in q
    for f in ("search_impression_share", "search_budget_lost_impression_share", "search_rank_lost_impression_share", "search_top_impression_share",
              "search_absolute_top_impression_share", "bidding_strategy_type", "campaign_budget.amount_micros"):
        assert f in q


# ── An account and an MCC ───────────────────────────────────────────────────

def test_one_account_is_read_once():
    log = []
    raw = b.google_raw(ACCT, TODAY, search_fn=searcher(log=log))
    assert len(log) == 1 and log[0][0] == "1234567890" and raw["members"] == [] and raw["warnings"] == []
    assert raw["accounts"][0]["name"] == "Acme Dental" and len(raw["accounts"][0]["rows"]) == 2


def test_an_mcc_reads_every_account_and_says_the_one_it_could_not_read():
    mcc = {"id": "mcc-1", "name": "Group", "currency": "USD", "members": [ACCT, ACCT2]}
    raw = b.google_raw(mcc, TODAY, search_fn=searcher({ACCT["id"]: [api()]}, fail=(ACCT2["id"],)))
    assert [a["name"] for a in raw["accounts"]] == ["Acme Dental"] and raw["members"] == ["Acme Dental", "Beta Clinic"]
    assert len(raw["warnings"]) == 1 and "Beta Clinic could not be read" in raw["warnings"][0]


def test_every_account_failing_is_raised():
    mcc = {"id": "mcc-1", "name": "Group", "members": [ACCT, ACCT2]}
    with pytest.raises(gads.GadsError, match="cannot be read"):
        b.google_raw(mcc, TODAY, search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])))


# ── The threshold and the overrides ─────────────────────────────────────────

@pytest.mark.parametrize("value,want", [(None, 0.9), ("", 0.9), ("90", 0.9), ("90%", 0.9), (" 85 ", 0.85), ("0.8", 0.8), (75, 0.75), (0.95, 0.95), ("50", 0.5), ("99", 0.99)])
def test_the_threshold_is_read_as_a_percentage_or_a_fraction(value, want):
    assert b.parse_threshold(value) == want


@pytest.mark.parametrize("value", ["49", "100", "0.4", "abc", "-5", "nan"])
def test_a_threshold_outside_50_to_99_or_not_a_number_is_refused(value):
    with pytest.raises(b.BadRequest):
        b.parse_threshold(value)


def test_the_overrides_come_from_lists_or_comma_text_and_brand_wins_a_clash():
    assert b.parse_overrides("1,2", "3") == {"1": True, "2": True, "3": False}
    assert b.parse_overrides(["1", " 2 "], ["2", "x", "4"]) == {"1": True, "2": True, "4": False}
    assert b.parse_overrides(None, None) == {} and b.parse_overrides("", "") == {}
    with pytest.raises(b.BadRequest):
        b.parse_overrides([str(i) for i in range(501)])


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


def test_the_report_says_which_week_which_threshold_and_what_it_found(cache):
    rep = report(searcher({ACCT["id"]: [api(share=0.70, lb=0.2, lr=0.1), api(week=BEFORE, share=0.95)]}))
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and rep["threshold"] == 0.9
    assert rep["window"] == {"from": "2026-08-24", "to": "2026-10-18", "weeks": 8, "latestFrom": NOW, "latestTo": "2026-10-18"}
    a = rep["accounts"][0]
    assert a["status"] == "below" and a["brand"]["share"] == 0.7 and a["cause"] == "budget" and a["findings"][0]["level"] == "high" and a["campaignCount"] == 1
    overall = rep["summary"].pop("overall")
    assert overall["share"] == pytest.approx(0.7, abs=0.001) and overall["eligible"] == 1429 and overall["accounts"] == 1          # whole searches, so a hair off 70%
    assert rep["summary"] == {"counts": {"below": 1, "unmeasured": 0, "thin": 0, "none": 0, "ok": 0}, "accounts": 1, "below": 1, "high": 1, "dropped": 1}
    assert rep["readAt"]


def test_a_second_ask_a_new_threshold_and_a_new_override_never_read_google_again_and_refresh_does(cache):
    calls = []
    search = searcher(log=calls)
    first = report(search)
    n = len(calls)
    assert first["accounts"][0]["status"] == "ok"
    low = report(search, threshold="96")
    assert len(calls) == n and low["accounts"][0]["status"] == "below" and low["threshold"] == 0.96
    forced_out = report(search, overrides={"11": False})
    assert len(calls) == n and forced_out["accounts"][0]["status"] == "none" and forced_out["accounts"][0]["otherCampaigns"][0]["byOverride"] is True
    report(search, force=True)
    assert len(calls) > n


def test_an_mcc_report_puts_the_worst_account_first_and_counts_each_state(cache):
    mcc = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD"}
    search = searcher({ACCT["id"]: [api(share=0.97)],
                       ACCT2["id"]: [api(cid="22", share=0.60, lb=0.3, lr=0.1)],
                       "3333333333": [api(cid="33", name="Core", share=0.9)],
                       "4444444444": [api(cid="44", impr="10", share=0.9)]})
    accounts = [ACCT, ACCT2, {"id": "3333333333", "name": "Gamma", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"}]
    rep = b.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [dict(mcc, accountIds=[a["id"] for a in accounts])],
                       search_fn=search, today=TODAY)
    assert [(a["name"], a["status"]) for a in rep["accounts"]] == [("Beta Clinic", "below"), ("Delta", "thin"), ("Gamma", "none"), ("Acme Dental", "ok")]
    assert rep["summary"]["counts"] == {"below": 1, "unmeasured": 0, "thin": 1, "none": 1, "ok": 1} and rep["account"]["isGroup"] and rep["account"]["accountCount"] == 4


def test_below_accounts_are_ordered_lowest_share_first(cache):
    accounts = [ACCT, ACCT2]
    rep = b.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts, groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=searcher({ACCT["id"]: [api(share=0.8)], ACCT2["id"]: [api(cid="22", share=0.5)]}), today=TODAY)
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"]


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


def test_the_overall_share_puts_the_judged_accounts_together_by_eligible_searches_and_leaves_out_the_rest(cache):
    accounts = [ACCT, ACCT2, {"id": "3333333333", "name": "Gamma", "currency": "USD"}, {"id": "4444444444", "name": "Delta", "currency": "USD"}]
    rep = b.report_for("mcc-9923720096-USD", accounts_fn=lambda: accounts,
                       groups_fn=lambda: [{"id": "mcc-9923720096-USD", "name": "G", "currency": "USD", "accountIds": [a["id"] for a in accounts]}],
                       search_fn=searcher({ACCT["id"]: [api(impr="900", share=0.9)], ACCT2["id"]: [api(cid="22", impr="100", share=0.5)],
                                           "3333333333": [api(cid="33", name="Core", impr="5000", share=0.1)],          # no brand campaign: not judged
                                           "4444444444": [api(cid="44", impr="10", share=0.9)]}), today=TODAY)         # too few searches: not judged
    assert rep["summary"]["overall"] == {"share": round(1000 / 1200, 4), "eligible": 1200, "accounts": 2}
    assert b.overall([]) == {"share": None, "eligible": 0, "accounts": 0}
