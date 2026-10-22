"""Keyword Visibility Tracker: reading Google, the report and its cache, what the team saved and the plan (tracker/paid_kvis*.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_kvis_math as TM  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_kvis as kv  # noqa: E402
from tracker import paid_kvis_plan as plan  # noqa: E402
from tracker import paid_kvis_settings as st  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = TM.TODAY
AT = dt.datetime(2026, 10, 28, 9, 0, tzinfo=dt.timezone.utc)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}


def top_row(gid="9", cid="101", text="dentist quincy", cost=400.0, qs=7, camp="5", cname="Search Core", match="EXACT"):
    return {"campaign": {"id": camp, "name": cname}, "adGroup": {"id": gid, "name": "G"}, "adGroupCriterion": {"criterionId": cid, "keyword": {"text": text, "matchType": match}, "qualityInfo": {"qualityScore": qs}},
            "metrics": {"costMicros": str(int(cost * 1e6))}}


def week_row(gid="9", cid="101", week=TM.RECENT, top=0.6, share=0.8, lost=0.1, impressions=1000):
    return {"adGroup": {"id": gid}, "adGroupCriterion": {"criterionId": cid}, "segments": {"week": week},
            "metrics": {"impressions": str(impressions), "clicks": "50", "costMicros": "50000000", "conversions": 2.0, "searchImpressionShare": share, "searchTopImpressionShare": top,
                        "searchAbsoluteTopImpressionShare": 0.2, "searchRankLostImpressionShare": lost}}


def all_weeks(gid="9", cid="101", **kw):
    return [week_row(gid, cid, w, **kw) for w in TM.MONDAYS]


def qs_row(gid="9", cid="101", week=TM.RECENT, score=7):
    return {"adGroup": {"id": gid}, "adGroupCriterion": {"criterionId": cid}, "segments": {"week": week}, "metrics": {"historicalQualityScore": score}}


def auc_row(domain="rival.com", week=TM.RECENT, overlap=0.3, camp="5", cname="Search Core"):
    return {"campaign": {"id": camp, "name": cname}, "segments": {"week": week, "auctionInsightDomain": domain}, "metrics": {"auctionInsightSearchOverlapRate": overlap, "auctionInsightSearchImpressionShare": 0.2,
                                                                                                                       "auctionInsightSearchOutrankingShare": 0.4, "auctionInsightSearchPositionAboveRate": 0.3,
                                                                                                                       "auctionInsightSearchTopImpressionPercentage": 0.5}}


def searcher(top=None, weeks=None, qs=None, auction=None, log=None, fail=(), fail_kinds=()):
    top = top if top is not None else {ACCT["id"]: [top_row()]}
    weeks = weeks if weeks is not None else {ACCT["id"]: all_weeks()}
    qs, auction = qs or {}, auction or {}

    def run(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        if "ORDER BY metrics.cost_micros DESC" in q:
            return top.get(cid, [])
        if "historical_quality_score" in q:
            if "qs" in fail_kinds:
                raise gads.GadsError("api", "no history")
            return qs.get(cid, [])
        if "auction_insight_domain" in q:
            if "auction403" in fail_kinds:
                raise gads.GadsError("permission", "Google Ads answered 403: The developer doesn't have access to metrics: 'auction_insight_search_overlap_rate'.")
            if "auction" in fail_kinds:
                raise gads.GadsError("api", "no auction insights")
            return auction.get(cid, [])
        if "segments.week, metrics.impressions" in q:
            ids = {x.strip() for x in q.split("criterion_id IN (")[1].rstrip(")").split(",")}
            return [r for r in weeks.get(cid, []) if r["adGroupCriterion"]["criterionId"] in ids]       # Google answers only for the keywords asked about
        raise AssertionError("unexpected query: " + q[:80])
    return run


# ── Parsing Google's rows ───────────────────────────────────────────────────

def test_the_top_keywords_come_with_their_names_score_and_spend_highest_first():
    rows = [top_row("9", "101", "a", cost=10.0), top_row("9", "102", "b", cost=900.5, qs=None, match="PHRASE"), {"adGroup": {"id": "9"}, "adGroupCriterion": {"criterionId": "103"}}, top_row("", "104", "x")]
    got = kv.read_top(rows)
    assert [(k["id"], k["text"], k["cost"], k["qs"], k["match"]) for k in got] == [("9-102", "b", 900.5, None, "PHRASE"), ("9-101", "a", 10.0, 7.0, "EXACT")]
    assert got[0]["campaignId"] == "5" and got[0]["campaign"] == "Search Core" and got[0]["adGroup"] == "G" and got[0]["criterionId"] == "102"


def test_the_weeks_of_a_keyword_keep_each_share_and_none_for_one_google_did_not_give():
    row = week_row(top=None, lost=None)
    row["metrics"]["searchImpressionShare"] = None
    got = kv.read_weeks([week_row(week="2026-10-12", top=0.4), row, {"adGroup": {"id": "9"}, "segments": {"week": TM.RECENT}}])
    assert sorted(got) == ["9-101"] and [w["week"] for w in got["9-101"]] == ["2026-10-12", TM.RECENT]
    first, second = got["9-101"]
    assert (first["top"], first["share"], first["lostRank"], first["impressions"], first["clicks"], first["cost"], first["conv"], first["absTop"]) == (0.4, 0.8, 0.1, 1000, 50, 50.0, 2.0, 0.2)
    assert (second["top"], second["share"], second["lostRank"]) == (None, None, None)


def test_the_quality_score_kept_for_each_week_and_a_zero_is_no_score():
    got = kv.read_qs([qs_row(week="2026-09-21", score=8), qs_row(week=TM.RECENT, score=5), qs_row(week="2026-10-05", score=0), {"adGroup": {"id": "9"}, "metrics": {"historicalQualityScore": 4}}])
    assert got == {"9-101": {"2026-09-21": 8.0, TM.RECENT: 5.0}}


def test_the_auction_insights_by_campaign_domain_and_week():
    got = kv.read_auction([auc_row(), auc_row(domain="", overlap=0.1), auc_row(week=""), {"segments": {"week": TM.RECENT, "auctionInsightDomain": "x.com"}}])
    assert got == [{"campaignId": "5", "campaign": "Search Core", "domain": "rival.com", "week": TM.RECENT, "overlap": 0.3, "outranking": 0.4, "above": 0.3, "topPct": 0.5, "share": 0.2}]


def test_the_read_of_one_account_puts_the_weeks_the_scores_the_auction_and_the_brand_campaigns_with_each_keyword():
    log = []
    s = searcher(top={ACCT["id"]: [top_row("9", "101", cost=400.0), top_row("9", "102", "other", cost=50.0, camp="6", cname="P2 Brand")]},
                 weeks={ACCT["id"]: all_weeks("9", "101") + all_weeks("9", "102", top=0.9)}, qs={ACCT["id"]: [qs_row("9", "101", TM.OLDEST, 8)]}, auction={ACCT["id"]: [auc_row()]}, log=log)
    got = kv.read_account(ACCT["id"], s, TODAY)
    assert got["today"] == "2026-10-28" and got["warnings"] == [] and got["brandIds"] == ["6"]
    k1, k2 = got["keywords"]
    assert len(k1["rows"]) == 5 and k1["qsWeeks"] == {TM.OLDEST: 8.0} and len(k2["rows"]) == 5 and k2["qsWeeks"] == {} and got["auction"][0]["domain"] == "rival.com"
    top_q = [q for _c, q in log if "ORDER BY" in q][0]
    assert "BETWEEN '2026-09-28' AND '2026-10-25'" in top_q and "LIMIT 100" in top_q and "ad_group_criterion.negative = FALSE" in top_q                     # the 28 days that end with the last whole week
    weeks_q = [q for _c, q in log if "segments.week, metrics.impressions" in q][0]
    assert "BETWEEN '2026-09-21' AND '2026-10-25'" in weeks_q and "criterion_id IN (101, 102)" in weeks_q


def test_the_limits_of_a_read():
    assert kv.MAX_READ == 100 and kv.ID_CHUNK == 100 and "LIMIT 100" in kv.GAQL_TOP


def test_the_keywords_are_asked_for_in_chunks_of_the_limit(monkeypatch):
    monkeypatch.setattr(kv, "ID_CHUNK", 2)
    log = []
    top = {ACCT["id"]: [top_row("9", str(100 + i), "k%d" % i, cost=100.0 - i) for i in range(5)]}
    got = kv.read_account(ACCT["id"], searcher(top=top, weeks={ACCT["id"]: sum((all_weeks("9", str(100 + i)) for i in range(5)), [])}, log=log), TODAY)
    asked = [q.split("IN (")[1] for _c, q in log if "segments.week, metrics.impressions" in q]
    assert asked == ["100, 101)", "102, 103)", "104)"] and all(len(k["rows"]) == 5 for k in got["keywords"])


def test_a_keyword_with_no_weeks_is_still_there_and_the_soft_reads_say_when_they_fail():
    got = kv.read_account(ACCT["id"], searcher(weeks={ACCT["id"]: []}, fail_kinds=("qs", "auction")), TODAY)
    assert got["keywords"][0]["rows"] == [] and got["auction"] == []
    assert got["warnings"] == ["The Quality Score history could not be read (no history), so that check was skipped.", "The auction insights could not be read (no auction insights), so that check was skipped."]
    with pytest.raises(gads.GadsError):
        kv.read_account(ACCT["id"], searcher(fail=(ACCT["id"],)), TODAY)


def test_auction_insights_google_refuses_to_this_app_are_a_standing_limit_not_a_failed_read():
    got = kv.read_account(ACCT["id"], searcher(fail_kinds=("auction403",)), TODAY)
    assert got["auction"] == [] and got["warnings"] == [] and got["limits"] == [kv.LIMIT_AUCTION]
    assert kv.LIMIT_AUCTION.startswith("Auction insights are not available to this app") and "—" not in kv.LIMIT_AUCTION
    other = kv.read_account(ACCT["id"], searcher(fail_kinds=("auction",)), TODAY)
    assert other["limits"] == [] and other["warnings"] == ["The auction insights could not be read (no auction insights), so that check was skipped."]       # any other refusal is still a failed read
    fine = kv.read_account(ACCT["id"], searcher(auction={ACCT["id"]: [auc_row()]}), TODAY)
    assert fine["limits"] == [] and len(fine["auction"]) == 1


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
    return kv.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, at=AT, **kw)


def low():
    return {ACCT["id"]: all_weeks(top=0.3, lost=0.1)}


def test_the_report_names_the_account_and_what_it_found(cache):
    rep = report(searcher(weeks=low(), auction={ACCT["id"]: [auc_row(week=w, overlap=0.0 if w != TM.RECENT else 0.3) for w in TM.MONDAYS]}))
    a = rep["accounts"][0]
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and a["id"] == "1234567890" and a["platform"] == "google" and a["today"] == "2026-10-28" and a["week"] == TM.RECENT
    assert sorted(f["rule"] for i in a["items"] for f in i["findings"]) == ["top_is_low"] and [f["rule"] for f in a["competitorFindings"]] == ["comp_new"] and a["status"] == "fix"
    assert rep["summary"] == {"counts": {"fix": 1, "check": 0, "clean": 0}, "accounts": 1, "keywords": 1, "judged": 1, "notJudged": 0, "competitors": 1, "high": 0, "medium": 2, "low": 0, "findings": 2}
    assert [r["rule"] for r in rep["rules"]] == kv.math.ORDER and rep["readAt"] and rep["warnings"] == []
    assert rep["minimums"] == {"eligible": 100, "topIs": 50, "qsMin": 5, "topN": 25, "weeks": 4}
    assert [s["id"] for s in a["plan"]["steps"]] == ["comp_new", "top_is_low", "recheck"] and a["saved"] == {"topIs": None, "qsMin": None, "topN": None, "ignore": 0} and a["tracked"] == 25 and a["read"] == 1


def test_a_second_ask_never_reads_google_again_and_refresh_does(cache):
    calls = []
    s = searcher(log=calls)
    report(s)
    n = len(calls)
    assert n == 4                                                                      # the keywords, their weeks, the Quality Score history and the auction insights
    report(s)
    assert len(calls) == n
    report(s, force=True)
    assert len(calls) == 2 * n


def test_what_the_team_saved_applies_without_reading_again(cache):
    calls, mon = [], MemStore()
    s = searcher(weeks=low(), log=calls)
    assert report(s, mon=mon)["accounts"][0]["status"] == "fix"
    n = len(calls)
    st.save_account(mon, ACCT["id"], "Acme", {"topIs": 25}, "a@b.co")                  # 30 percent against a lowest wanted of 25 is fine
    assert report(s, mon=mon)["accounts"][0]["status"] == "clean" and report(s, mon=mon)["accounts"][0]["saved"]["topIs"] == 25
    st.save_account(mon, ACCT["id"], "Acme", {"topIs": 25, "qsMin": 8}, "a@b.co")                   # a Quality Score of 7 is now under what is wanted
    assert [(f["rule"], f["level"]) for i in report(s, mon=mon)["accounts"][0]["items"] for f in i["findings"]] == [("qs_low", "low")]     # the 25 percent is still wanted, a score of 7 is under 8
    assert len(calls) == n


def test_how_many_keywords_are_tracked_cuts_the_saved_read_without_reading_again(cache):
    top = {ACCT["id"]: [top_row("9", str(100 + i), "k%d" % i, cost=1000.0 - i) for i in range(30)]}
    weeks = {ACCT["id"]: sum((all_weeks("9", str(100 + i), top=0.3) for i in range(30)), [])}
    calls, mon = [], MemStore()
    s = searcher(top=top, weeks=weeks, log=calls)
    a = report(s, mon=mon)["accounts"][0]
    assert a["keywords"] == 25 and a["tracked"] == 25 and a["read"] == 30                # the top 25 by spend unless saved
    assert sorted(i["keyword"] for i in a["items"]) == sorted("k%d" % i for i in range(25))
    n = len(calls)
    st.save_account(mon, ACCT["id"], "Acme", {"topN": 5}, "a@b.co")
    b = report(s, mon=mon)["accounts"][0]
    assert b["keywords"] == 5 and sorted(i["keyword"] for i in b["items"]) == ["k0", "k1", "k2", "k3", "k4"] and len(calls) == n


def test_the_domains_the_team_ignores_are_left_out_of_the_competitors(cache):
    mon = MemStore()
    s = searcher(auction={ACCT["id"]: [auc_row(week=w, overlap=0.0 if w != TM.RECENT else 0.3) for w in TM.MONDAYS]})
    assert len(report(s, mon=mon)["accounts"][0]["competitorFindings"]) == 1
    st.add_ignore(mon, ACCT["id"], "Rival.com", "a@b.co")
    assert report(s, mon=mon)["accounts"][0]["competitorFindings"] == []


def test_what_the_team_saved_that_cannot_be_read_is_said_and_the_report_is_still_built(cache):
    class Broken(MemStore):
        def state_get(self, key, default=None):
            raise RuntimeError("db down")
    rep = report(searcher(), mon=Broken())
    assert rep["warnings"] == ["What the team saved (targets, ignored domains) could not be read (db down), so it was not applied."] and rep["accounts"][0]["id"] == "1234567890"


def test_an_mcc_report_puts_the_accounts_to_fix_first(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    s = searcher(top={ACCT["id"]: [top_row()], ACCT2["id"]: [top_row()]}, weeks={ACCT["id"]: all_weeks(top=0.6), ACCT2["id"]: all_weeks(top=0.3)})
    rep = kv.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY, at=AT)
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"] and rep["account"]["isGroup"] is True and rep["account"]["accountCount"] == 2
    assert rep["summary"]["counts"] == {"fix": 1, "check": 0, "clean": 1}


def test_an_account_that_cannot_be_read_is_named_and_all_failing_is_raised(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    rep = kv.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=searcher(fail=(ACCT2["id"],)), today=TODAY, at=AT)
    assert [a["name"] for a in rep["accounts"]] == ["Acme Dental"] and rep["warnings"] == ["Beta Clinic could not be read: Account 2222222222 cannot be read."]
    other = dict(group, id="mcc-1111111111-USD")
    with pytest.raises(gads.GadsError):
        kv.report_for(other["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [other], search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])), today=TODAY, at=AT)


def test_a_read_that_lost_a_part_is_shown_with_the_account_name_and_not_saved_for_hours(cache):
    rep = report(searcher(fail_kinds=("auction",)))
    assert rep["warnings"] == ["The auction insights could not be read (no auction insights), so that check was skipped."] and cache == {}
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.forget("")
    report(searcher())
    assert len(cache) == 1
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"]]}
    many = kv.report_for(group["id"], accounts_fn=lambda: [ACCT], groups_fn=lambda: [group], search_fn=searcher(fail_kinds=("qs",)), today=TODAY, at=AT)
    assert many["warnings"] == ["Acme Dental: The Quality Score history could not be read (no history), so that check was skipped."]


def test_the_limit_reaches_the_report_once_and_a_read_with_only_a_limit_is_saved_for_hours(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    s = searcher(top={ACCT["id"]: [top_row()], ACCT2["id"]: [top_row()]}, weeks={ACCT["id"]: all_weeks(), ACCT2["id"]: all_weeks()}, fail_kinds=("auction403",))
    rep = kv.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY, at=AT)
    assert rep["limits"] == [kv.LIMIT_AUCTION] and rep["warnings"] == [] and len(cache) == 1                  # one limit for both accounts, and nothing to retry: the read is kept


def test_a_meta_account_is_refused_and_so_is_one_that_is_not_position2s(cache):
    with pytest.raises(kv.BadRequest, match="Google Ads accounts only"):
        kv.report_for("act_1234567", search_fn=searcher(), today=TODAY, at=AT)
    with pytest.raises(kv.BadRequest, match="not one of Position2"):
        kv.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, at=AT)


def test_a_spent_quota_with_nothing_saved_is_raised(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        report(spent)


# ── What the team saved ─────────────────────────────────────────────────────

def test_the_settings_are_saved_for_one_account_and_logged():
    mon = MemStore()
    got = st.save_account(mon, "1234567890", "Acme", {"topIs": "35%", "qsMin": "6", "topN": "40", "ignore": "Sister.com, https://www.partner.org/page; sister.com"}, "krishna", AT)
    assert got == {"topIs": 35.0, "qsMin": 6, "topN": 40, "ignore": ["sister.com", "partner.org"], "name": "Acme"}
    assert st.load(mon)["changes"] == [{"at": AT.isoformat(timespec="seconds"), "by": "krishna", "what": "saved the settings of account 1234567890"}]
    assert st.save_account(mon, "1234567890", "Acme", {}, "krishna") == {"topIs": None, "qsMin": None, "topN": None, "ignore": [], "name": ""} and st.load(mon)["accounts"] == {}


@pytest.mark.parametrize("data,message", [({"topIs": "abc"}, "The lowest top-of-page share must be a number."), ({"topIs": "0"}, "must be between 1 and 100"), ({"topIs": "101"}, "must be between 1 and 100"),
                                           ({"qsMin": "1"}, "The lowest Quality Score must be between 2 and 10"), ({"qsMin": "11"}, "must be between 2 and 10"),
                                           ({"topN": "4"}, "The number of keywords must be between 5 and 100"), ({"topN": "101"}, "must be between 5 and 100"),
                                           ({"topIs": "nan"}, "must be a number"), ({"ignore": "not a domain"}, "is not a domain"), ({"ignore": "localhost"}, "is not a domain")])
def test_figures_and_domains_that_are_not_are_refused(data, message):
    with pytest.raises(st.BadRequest, match=message):
        st.save_account(MemStore(), "1234567890", "Acme", data, "krishna")


def test_a_share_keeps_one_decimal_and_the_log_keeps_the_last_thirty():
    mon = MemStore()
    assert st.save_account(mon, "1234567890", "Acme", {"topIs": "33.33"}, "a")["topIs"] == 33.3
    for i in range(35):
        st.save_account(mon, "1234567890", "Acme", {"topIs": str(10 + i)}, "a%d" % i)
    log = st.load(mon)["changes"]
    assert len(log) == 30 and log[0]["by"] == "a34" and log[-1]["by"] == "a5"


def test_the_limits_themselves_are_allowed():
    got = st.save_account(MemStore(), "1234567890", "Acme", {"topIs": "1", "qsMin": "2", "topN": "5"}, "k")
    assert (got["topIs"], got["qsMin"], got["topN"]) == (1.0, 2, 5)
    got = st.save_account(MemStore(), "1234567890", "Acme", {"topIs": "100", "qsMin": "10", "topN": "100"}, "k")
    assert (got["topIs"], got["qsMin"], got["topN"]) == (100.0, 10, 100)


def test_nothing_is_saved_for_an_mcc_or_an_unknown_id():
    for bad in ("mcc-9923720096-USD", "", "abc", None, "act_1234567"):
        with pytest.raises(st.BadRequest, match="Pick one Google Ads account"):
            st.save_account(MemStore(), bad, "x", {"topIs": 5}, "krishna")
        with pytest.raises(st.BadRequest, match="Pick one Google Ads account"):
            st.add_ignore(MemStore(), bad, "x.com", "krishna")


def test_a_domain_is_added_to_the_ignored_ones_once_and_the_limit_holds():
    mon = MemStore()
    st.save_account(mon, "1234567890", "Acme", {"topIs": 40}, "a")
    got = st.add_ignore(mon, "1234567890", ["A.com", "b.com"], "a")
    assert got["ignore"] == ["a.com", "b.com"] and got["topIs"] == 40.0
    assert st.add_ignore(mon, "1234567890", "a.com, c.com", "a")["ignore"] == ["a.com", "b.com", "c.com"]
    with pytest.raises(st.BadRequest, match="at least one domain"):
        st.add_ignore(mon, "1234567890", "", "a")
    with pytest.raises(st.BadRequest, match="At most 100"):
        st.save_account(mon, "1234567890", "Acme", {"ignore": ["d%d.com" % i for i in range(101)]}, "a")
    assert len(st.save_account(mon, "1234567890", "Acme", {"ignore": ["d%d.com" % i for i in range(100)]}, "a")["ignore"]) == 100
    full = st.add_ignore(mon, "1234567890", "extra.com", "a")
    assert len(full["ignore"]) == 100 and "extra.com" not in full["ignore"]
    assert len(st.load(mon)["changes"]) <= 30
    assert st.view(mon)["limits"] == {"topIs": [1, 100], "qsMin": [2, 10], "topN": [5, 100]} and len(st.view(mon)["accounts"]["1234567890"]["ignore"]) == 100


# ── The plan ────────────────────────────────────────────────────────────────

def analysed(keywords, auction=None, config=None):
    from tracker import paid_kvis_math as m
    return m.analyse({"keywords": keywords, "auction": auction or [], "currency": "USD", "brandIds": []}, config=config or {}, today=TODAY)


def test_the_steps_follow_the_rules_and_name_the_first_keywords_and_domains():
    ks = [TM.kw(str(i), "kw %d" % i, cost=900.0 - i, rows_=TM.rows(recent={"top": 0.3}, prior={"top": 0.3})) for i in range(5)]
    steps = plan.build(analysed(ks, TM.history("rival.com", 0.3, 0.0)))["steps"]
    assert [s["id"] for s in steps] == ["comp_new", "top_is_low", "recheck"]
    first, second = steps[0], steps[1]
    assert first["title"] == "Look at 1 competitor that appeared in the auctions" and first["where"] == ["rival.com"] and first["findings"] == 1
    assert second["title"] == "Decide whether 5 keywords should be at the top of the page more often" and len(second["where"]) == 3 and len(second["examples"]) == 3 and second["level"] == "medium"
    assert steps[-1]["title"] == "Check again next week" and steps[-1]["level"] == "info"
    one = plan.build(analysed(ks[:1]))["steps"][0]
    assert one["title"] == "Decide whether 1 keyword should be at the top of the page more often"


def test_nothing_found_is_no_steps():
    assert plan.build(analysed([TM.kw(rows_=TM.rows(recent={"top": 0.6}, prior={"top": 0.6}))]))["steps"] == []
    assert plan.build(analysed([]))["steps"] == []
