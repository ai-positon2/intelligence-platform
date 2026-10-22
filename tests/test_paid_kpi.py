"""KPI Monitoring: reading Google and Meta, the report and its cache, what the team saved and the plan (tracker/paid_kpi*.py). No network."""
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
from tracker import paid_kpi as k  # noqa: E402
from tracker import paid_kpi_plan as plan  # noqa: E402
from tracker import paid_kpi_settings as st  # noqa: E402
from tracker import paid_store  # noqa: E402

TODAY = dt.date(2026, 10, 22)
AT = dt.datetime(2026, 10, 22, 15, 0, tzinfo=dt.timezone.utc)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}


def camp_row(i="5", name="Search Core", channel="SEARCH", status="ELIGIBLE", reasons=(), system="ENABLED", strategy="TARGET_CPA", **kw):
    c = {"id": i, "name": name, "advertisingChannelType": channel, "primaryStatus": status, "primaryStatusReasons": list(reasons), "biddingStrategySystemStatus": system,
         "biddingStrategyType": strategy, "startDateTime": "2026-01-01 00:00:00"}
    c.update(kw)
    return {"campaign": c}


def day_rows(cid="5", cost=100.0, conv=1.0, impr=2000, clicks=200, value=0.0, first=1, last=14, tz="America/New_York"):
    return [{"customer": {"timeZone": tz}, "campaign": {"id": cid}, "segments": {"date": (TODAY - dt.timedelta(days=i)).isoformat()},
             "metrics": {"impressions": str(impr), "clicks": str(clicks), "costMicros": str(int(cost * 1e6)), "conversions": conv, "conversionsValue": value}} for i in range(first, last + 1)]


def searcher(camps=None, days=None, lost=None, log=None, fail=(), fail_kinds=()):
    camps = camps if camps is not None else {ACCT["id"]: [camp_row(targetCpa={"targetCpaMicros": "40000000"})]}
    days = days if days is not None else {ACCT["id"]: day_rows(cost=40.0, conv=1.0)}
    lost = lost or {}

    def run(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        if "search_budget_lost_impression_share" in q:
            if "lost" in fail_kinds:
                raise gads.GadsError("api", "no budget read")
            return lost.get(cid, [])
        if "customer.time_zone" in q:
            return days.get(cid, [])
        if "campaign.primary_status" in q:
            return camps.get(cid, [])
        raise AssertionError("unexpected query: " + q[:80])
    return run


# ── Parsing Google's rows ───────────────────────────────────────────────────

def test_campaigns_with_their_status_and_the_strategys_own_target():
    rows = [camp_row("1", "A", targetCpa={"targetCpaMicros": "40000000"}), camp_row("2", "B", strategy="MAXIMIZE_CONVERSIONS", maximizeConversions={"targetCpaMicros": "25500000"}),
            camp_row("3", "C", strategy="TARGET_ROAS", targetRoas={"targetRoas": 4.5}), camp_row("4", "D", strategy="MAXIMIZE_CONVERSION_VALUE", maximizeConversionValue={"targetRoas": 3.0}),
            camp_row("5", "E", status="LIMITED", reasons=["BUDGET_CONSTRAINED"], system="LIMITED_BY_BUDGET", strategy="MANUAL_CPC"), {"campaign": {"name": "no id"}}]
    got = {c["id"]: c for c in k.read_campaigns(rows)}
    assert sorted(got) == ["1", "2", "3", "4", "5"]
    assert (got["1"]["targetCpa"], got["2"]["targetCpa"], got["3"]["targetRoas"], got["4"]["targetRoas"]) == (40.0, 25.5, 4.5, 3.0)
    assert (got["1"]["targetRoas"], got["3"]["targetCpa"], got["5"]["targetCpa"]) == (None, None, None)
    e = got["5"]
    assert (e["primaryStatus"], e["reasons"], e["systemStatus"], e["strategy"], e["channel"], e["startDate"]) == ("LIMITED", ["BUDGET_CONSTRAINED"], "LIMITED_BY_BUDGET", "MANUAL_CPC", "SEARCH", "2026-01-01")


def test_days_by_campaign_with_the_accounts_time_zone():
    days, tz = k.read_days(day_rows("5", cost=12.5, conv=2.5, impr=300, clicks=30, value=99.0, first=1, last=2, tz="Asia/Kolkata") + day_rows("6", first=1, last=1) + [{"campaign": {"id": "7"}}])
    assert tz == "Asia/Kolkata" and sorted(days) == ["5", "6"] and len(days["5"]) == 2
    assert days["5"][0] == {"date": "2026-10-21", "impr": 300.0, "clicks": 30.0, "cost": 12.5, "conv": 2.5, "value": 99.0}


def test_the_share_lost_to_the_budget_is_kept_between_nothing_and_everything():
    rows = [{"campaign": {"id": "1"}, "metrics": {"searchBudgetLostImpressionShare": 0.35}}, {"campaign": {"id": "2"}, "metrics": {"searchBudgetLostImpressionShare": 1.7}},
            {"campaign": {"id": "3"}, "metrics": {"searchBudgetLostImpressionShare": -0.2}}, {"campaign": {"id": "4"}, "metrics": {}}, {"metrics": {"searchBudgetLostImpressionShare": 0.5}}]
    assert k.read_lost(rows) == {"1": 0.35, "2": 1.0, "3": 0.0}


def test_the_accounts_own_date_decides_what_today_is():
    late = dt.datetime(2026, 10, 22, 2, 0, tzinfo=dt.timezone.utc)                  # still the evening of the 21st on the American west coast
    assert k.local_day("America/Los_Angeles", late) == dt.date(2026, 10, 21)
    assert k.local_day("Asia/Kolkata", late) == dt.date(2026, 10, 22)
    assert k.local_day("", late) == dt.date(2026, 10, 22)
    assert k.local_day("Not/AZone", late) == dt.date(2026, 10, 22)


def test_one_account_is_read_with_its_days_the_budget_share_and_its_own_date():
    log = []
    s = searcher(days={ACCT["id"]: day_rows(tz="America/Los_Angeles")}, lost={ACCT["id"]: [{"campaign": {"id": "5"}, "metrics": {"searchBudgetLostImpressionShare": 0.3}}]}, log=log)
    late = dt.datetime(2026, 10, 22, 2, 0, tzinfo=dt.timezone.utc)
    got = k.read_account(ACCT["id"], s, late)
    assert got["today"] == "2026-10-21" and got["warnings"] == []
    c = got["campaigns"][0]
    assert (c["id"], c["lostBudget"], len(c["days"])) == ("5", 0.3, 14)
    days_q = [q for _c, q in log if "customer.time_zone" in q][0]
    assert "BETWEEN '2026-10-06' AND '2026-10-22'" in days_q                          # 16 days before the server's date, to the server's date
    lost_q = [q for _c, q in log if "search_budget_lost" in q][0]
    assert "BETWEEN '2026-10-14' AND '2026-10-20'" in lost_q                           # the 7 whole days before the account's own today


def test_a_campaign_with_no_row_for_any_day_is_still_there_with_no_days():
    got = k.read_account(ACCT["id"], searcher(days={ACCT["id"]: []}), AT)
    assert got["campaigns"][0]["days"] == [] and got["campaigns"][0]["lostBudget"] is None and got["today"] == "2026-10-22"


def test_the_budget_read_is_soft_and_the_campaigns_are_not():
    got = k.read_account(ACCT["id"], searcher(fail_kinds=("lost",)), AT)
    assert got["warnings"] == ["The share of impressions lost to the budget could not be read (no budget read), so that check was skipped."]
    assert got["campaigns"][0]["lostBudget"] is None
    with pytest.raises(gads.GadsError):
        k.read_account(ACCT["id"], searcher(fail=(ACCT["id"],)), AT)


# ── Meta ────────────────────────────────────────────────────────────────────

def test_a_learning_ad_set_makes_its_campaign_learning_and_a_failed_one_limited_by_data():
    got = k.meta_learning([{"campaign_id": "1", "learning_stage_info": {"status": "LEARNING"}}, {"campaign_id": "2", "learning_stage_info": {"status": "FAIL"}},
                           {"campaign_id": "2", "learning_stage_info": {"status": "LEARNING"}}, {"campaign_id": "3", "learning_stage_info": {"status": "SUCCESS"}},
                           {"campaign_id": "1", "learning_stage_info": {"status": "FAIL"}}, {"learning_stage_info": {"status": "FAIL"}}, {"campaign_id": "4"}])
    assert got == {"1": "LIMITED_BY_DATA", "2": "LIMITED_BY_DATA"}


def meta_rows(cid="9", day="2026-10-21", spend="40", leads="1", objective="OUTCOME_LEADS", impressions="2000", clicks="200"):
    return {"campaign_id": cid, "campaign_name": "Leads", "objective": objective, "date_start": day, "spend": spend, "impressions": impressions, "inline_link_clicks": clicks,
            "actions": [{"action_type": "lead", "value": leads}]}


def meta_pages(campaigns=None, insights=None, adsets=None, fail_adsets=False):
    campaigns = campaigns if campaigns is not None else [{"id": "9", "name": "Leads", "effective_status": "ACTIVE", "objective": "OUTCOME_LEADS", "start_time": "2026-01-01T00:00:00+0000"}]
    insights = insights if insights is not None else [meta_rows(day=(TODAY - dt.timedelta(days=i)).isoformat()) for i in range(1, 15)]

    def pages(path, params):
        if path.endswith("/campaigns"):
            return campaigns
        if path.endswith("/insights"):
            return insights
        if path.endswith("/adsets"):
            if fail_adsets:
                raise RuntimeError("Meta says no")
            return adsets or []
        raise AssertionError(path)
    return pages


def test_a_meta_account_is_read_as_campaigns_with_their_days_and_learning_state():
    adsets = [{"id": "1", "campaign_id": "9", "effective_status": "ACTIVE", "learning_stage_info": {"status": "LEARNING"}}]
    got = k.read_meta_account("act_1234567", meta_pages(adsets=adsets), AT)
    (c,) = got["campaigns"]
    assert (c["id"], c["name"], c["primaryStatus"], c["systemStatus"], c["startDate"], c["strategy"], c["targetCpa"], c["lostBudget"]) == ("9", "Leads", "ELIGIBLE", "LEARNING_NEW", "2026-01-01", "", None, None)
    assert len(c["days"]) == 14 and c["days"][0]["cost"] == 40.0 and c["days"][0]["conv"] == 1.0 and c["days"][0]["clicks"] == 200 and c["days"][0]["impr"] == 2000
    assert got["today"] == "2026-10-22" and got["warnings"] == []


def test_a_meta_campaign_with_issues_is_limited_with_its_reason_and_one_without_insights_has_no_days():
    pages = meta_pages(campaigns=[{"id": "9", "name": "Leads", "effective_status": "WITH_ISSUES", "objective": "OUTCOME_LEADS"}, {"id": "10", "name": "Quiet", "effective_status": "ACTIVE"}], insights=[])
    got = {c["id"]: c for c in k.read_meta_account("act_1234567", pages, AT)["campaigns"]}
    assert (got["9"]["primaryStatus"], got["9"]["reasons"]) == ("LIMITED", ["META_WITH_ISSUES"]) and got["10"]["days"] == [] and got["10"]["startDate"] == ""


def test_meta_learning_that_cannot_be_read_is_said_and_the_rest_stands():
    got = k.read_meta_account("act_1234567", meta_pages(fail_adsets=True), AT)
    assert got["warnings"] == ["The ad sets' learning state could not be read (Meta says no), so that check was skipped."] and got["campaigns"][0]["systemStatus"] == ""


def test_the_meta_requests_ask_for_live_campaigns_only():
    path, params = k.meta_campaigns_request("act_1")
    assert path == "/act_1/campaigns" and params["effective_status"] == '["ACTIVE", "WITH_ISSUES"]'
    path, params = k.meta_adsets_request("act_1")
    assert path == "/act_1/adsets" and "learning_stage_info" in params["fields"]


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
    return k.report_for("1234567890", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, at=AT, **kw)


def over(cid="5"):
    return {ACCT["id"]: day_rows(cid, cost=100.0, conv=1.0)}                 # 100 a conversion for two weeks against a target of 40


def test_the_report_names_the_account_and_what_it_found(cache):
    rep = report(searcher(days=over()))
    a = rep["accounts"][0]
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and a["id"] == "1234567890" and a["platform"] == "google" and a["today"] == "2026-10-22"
    assert [f["rule"] for it in a["items"] for f in it["findings"]] == ["cpa"] and a["status"] == "fix"
    assert a["items"][0]["findings"][0]["facts"]["source"] == "strategy"                 # the target came from the campaign's own bid strategy
    assert rep["summary"] == {"counts": {"fix": 1, "check": 0, "clean": 0}, "accounts": 1, "campaigns": 1, "offTarget": 1, "onTarget": 0, "notJudged": 0, "withoutTarget": 0, "high": 1, "medium": 0, "low": 0, "findings": 1}
    assert [r["rule"] for r in rep["rules"]] == k.math.ORDER and rep["readAt"] and rep["warnings"] == []
    assert rep["minimums"] == {"conversions": 5, "clicks": 100, "impressions": 1000, "tolerance": 20, "stuckDays": 14}
    assert a["plan"]["steps"][0]["id"] == "cpa" and a["saved"] == {"targets": 0, "campaigns": 0, "muted": 0, "tolerance": None}


def test_a_second_ask_never_reads_google_again_and_refresh_does(cache):
    calls = []
    s = searcher(log=calls)
    report(s)
    n = len(calls)
    assert n == 3                                                                      # the campaigns, the days and the budget
    report(s)
    assert len(calls) == n
    report(s, force=True)
    assert len(calls) == 2 * n


def test_what_the_team_saved_applies_without_reading_again(cache):
    calls, mon = [], MemStore()
    s = searcher(days=over(), log=calls)
    plain = report(s, mon=mon)
    n = len(calls)
    assert plain["accounts"][0]["counts"]["high"] == 1
    st.save_account(mon, ACCT["id"], "Acme Dental", {"targets": {"cpa": 120}}, "krishna")                       # 100 against 120 is on target
    assert report(s, mon=mon)["accounts"][0]["status"] == "clean"
    st.save_account(mon, ACCT["id"], "Acme Dental", {"targets": {"cpa": 50}, "tolerance": 100}, "krishna")      # 100 against 50 with a tolerance of 100 percent is not over it
    assert report(s, mon=mon)["accounts"][0]["status"] == "clean"
    st.save_account(mon, ACCT["id"], "Acme Dental", {"targets": {"cpa": 50}}, "krishna")
    assert report(s, mon=mon)["accounts"][0]["saved"] == {"targets": 1, "campaigns": 0, "muted": 0, "tolerance": None}
    st.mute(mon, ACCT["id"], "5", "Search Core", "being replaced", "krishna")
    muted = report(s, mon=mon)["accounts"][0]
    assert muted["status"] == "clean" and muted["muted"] == 1 and muted["items"][0]["mutedWhy"] == "being replaced"
    assert len(calls) == n


def test_how_long_a_campaign_has_been_learning_comes_from_the_monitors_issues(cache):
    mon = MemStore()
    first = (AT - dt.timedelta(days=15)).isoformat()
    mon.issues_put([{"agent": "kpi", "fingerprint": "kpi:1234567890:learning:5", "accountId": ACCT["id"], "kind": "learning", "status": "open", "firstSeen": first, "lastSeen": first, "data": {"campaignId": "5"}},
                    {"agent": "kpi", "fingerprint": "kpi:1234567890:learning:6", "accountId": ACCT["id"], "kind": "learning", "status": "resolved", "firstSeen": first, "lastSeen": first, "data": {"campaignId": "6"}},
                    {"agent": "kpi", "fingerprint": "kpi:9999999999:learning:5", "accountId": "9999999999", "kind": "learning", "status": "open", "firstSeen": (AT - dt.timedelta(days=30)).isoformat(), "lastSeen": first,
                     "data": {"campaignId": "5"}}])
    assert k.learning_days(mon.issues_all("kpi"), ACCT["id"], AT) == {"5": 15}
    s = searcher(camps={ACCT["id"]: [camp_row(system="LEARNING_NEW")]}, days={ACCT["id"]: day_rows(cost=40.0)})
    (f,) = [f for it in report(s, mon=mon)["accounts"][0]["items"] for f in it["findings"]]
    assert (f["rule"], f["level"], f["title"]) == ("learning", "medium", "Search Core is stuck in learning")
    (g,) = [f for it in report(s)["accounts"][0]["items"] for f in it["findings"]]
    assert (g["level"], g["title"]) == ("low", "Search Core is still learning")                          # without the monitor's history it is only learning


def test_an_mcc_report_puts_the_accounts_to_fix_first(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    target = {"targetCpa": {"targetCpaMicros": "40000000"}}
    s = searcher(camps={ACCT["id"]: [camp_row(**target)], ACCT2["id"]: [camp_row(**target)]}, days={ACCT["id"]: day_rows(cost=40.0), ACCT2["id"]: day_rows(cost=100.0, conv=0.0)})
    rep = k.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=s, today=TODAY, at=AT)
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"] and rep["account"]["isGroup"] is True and rep["account"]["accountCount"] == 2
    assert rep["accounts"][0]["items"][0]["findings"][0]["rule"] == "no_conv" and rep["summary"]["counts"] == {"fix": 1, "check": 0, "clean": 1}


def test_an_account_that_cannot_be_read_is_named_and_all_failing_is_raised(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    rep = k.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=searcher(fail=(ACCT2["id"],)), today=TODAY, at=AT)
    assert [a["name"] for a in rep["accounts"]] == ["Acme Dental"] and rep["warnings"] == ["Beta Clinic could not be read: Account 2222222222 cannot be read."]
    other = dict(group, id="mcc-1111111111-USD")
    with pytest.raises(gads.GadsError):
        k.report_for(other["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [other], search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])), today=TODAY, at=AT)


def test_a_read_that_lost_a_part_is_shown_but_not_saved_for_hours(cache):
    rep = report(searcher(fail_kinds=("lost",)))
    assert rep["warnings"] == ["The share of impressions lost to the budget could not be read (no budget read), so that check was skipped."]
    assert cache == {}                                                                  # the saved copy is for a whole read only: the next ask in a new process reads again
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.forget("")
    report(searcher())
    assert len(cache) == 1                                                              # a whole read is saved


def test_a_report_for_a_meta_account_reads_its_campaigns(cache):
    rows = [meta_rows(day=(TODAY - dt.timedelta(days=i)).isoformat(), spend="100", leads="1") for i in range(1, 15)]
    rep = k.report_for("act_1234567", meta_pages_fn=meta_pages(insights=rows), meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"}, today=TODAY, at=AT, search_fn=searcher())
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["platform"] == "meta" and a["currency"] == "EUR" and a["campaigns"] == 1
    assert a["items"][0]["channel"] == "META_LEADS" and a["items"][0]["recent"]["cost"] == 700.0 and a["status"] == "clean" and a["withoutTarget"] == 1


def test_a_spent_quota_with_nothing_saved_is_raised_and_an_account_that_is_not_position2s_is_refused(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        report(spent)
    with pytest.raises(k.BadRequest, match="not one of Position2"):
        k.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, at=AT)


# ── What the team saved ─────────────────────────────────────────────────────

def test_targets_and_a_tolerance_are_saved_for_one_account_and_logged():
    mon = MemStore()
    got = st.save_account(mon, "1234567890", "Acme", {"targets": {"cpa": "$45.50", "roas": "4x", "cvr": "3.5%", "ctr": ""}, "tolerance": "25%"}, "krishna", AT)
    assert got["targets"] == {"cpa": 45.5, "roas": 4.0, "cvr": 3.5, "ctr": None} and got["tolerance"] == 25.0 and got["name"] == "Acme"
    assert st.load(mon)["changes"] == [{"at": AT.isoformat(timespec="seconds"), "by": "krishna", "what": "saved the targets of account 1234567890"}]
    assert st.save_account(mon, "1234567890", "Acme", {"targets": {}}, "krishna")["targets"] == {"cpa": None, "roas": None, "cvr": None, "ctr": None}
    assert st.load(mon)["accounts"] == {}                                               # nothing saved is nothing kept


@pytest.mark.parametrize("data,message", [({"targets": {"cpa": "abc"}}, "The cost per conversion must be a number."), ({"targets": {"cpa": "-3"}}, "The cost per conversion must be between"),
                                           ({"targets": {"roas": "5000"}}, "The return on ad spend must be between"), ({"targets": {"cvr": "150"}}, "The lowest conversion rate must be between"),
                                           ({"targets": {"ctr": "nan"}}, "The lowest click-through rate must be a number"), ({"tolerance": "2"}, "The tolerance must be between 5 and 100 percent."),
                                           ({"tolerance": "x"}, "The tolerance must be a number")])
def test_figures_that_are_not_figures_are_refused(data, message):
    with pytest.raises(st.BadRequest, match=message):
        st.save_account(MemStore(), "1234567890", "Acme", data, "krishna")


def test_the_limits_themselves_are_allowed_and_a_figure_keeps_its_decimals():
    mon = MemStore()
    got = st.save_account(mon, "1234567890", "Acme", {"targets": {"cpa": "0.01", "roas": "1000", "cvr": "100", "ctr": "0.01"}, "tolerance": "100"}, "a")
    assert got["targets"] == {"cpa": 0.01, "roas": 1000.0, "cvr": 100.0, "ctr": 0.01} and got["tolerance"] == 100.0
    assert st.save_account(mon, "1234567890", "Acme", {"targets": {"cpa": "1000000"}, "tolerance": "5"}, "a")["tolerance"] == 5.0
    assert st.save_account(mon, "1234567890", "Acme", {"targets": {"roas": "2.7549"}}, "a")["targets"]["roas"] == 2.7549
    for bad in ("0.0099", "1000000.01"):
        with pytest.raises(st.BadRequest):
            st.save_account(mon, "1234567890", "Acme", {"targets": {"cpa": bad}}, "a")


def test_only_what_was_saved_is_kept():
    mon = MemStore()
    st.save_account(mon, "1234567890", "Acme", {"targets": {"cpa": 50, "roas": ""}}, "a")
    assert st.load(mon)["accounts"]["1234567890"] == {"targets": {"cpa": 50.0}, "name": "Acme"}


def test_nothing_is_saved_for_an_mcc_or_an_unknown_id():
    for bad in ("mcc-9923720096-USD", "", "abc", None):
        with pytest.raises(st.BadRequest, match="Pick one account"):
            st.save_account(MemStore(), bad, "x", {"targets": {"cpa": 5}}, "krishna")
    assert st.save_account(MemStore(), "act_1234567", "M", {"targets": {"cpa": 5}}, "krishna")["targets"]["cpa"] == 5.0


def test_a_campaigns_own_targets_are_kept_beside_the_accounts_and_removed_when_empty():
    mon = MemStore()
    st.save_account(mon, "1234567890", "Acme", {"targets": {"cpa": 50}}, "a")
    got = st.save_campaign(mon, "1234567890", "5", "Brand", {"cpa": 20, "ctr": 8}, "a")
    assert got["campaigns"] == {"5": {"cpa": 20.0, "roas": None, "cvr": None, "ctr": 8.0}} and got["names"] == {"5": "Brand"} and got["targets"]["cpa"] == 50.0
    st.save_account(mon, "1234567890", "Acme", {"targets": {"cpa": 60}}, "a")                          # saving the account's own figures keeps the campaigns'
    assert st.account_of(st.load(mon), "1234567890")["campaigns"]["5"]["cpa"] == 20.0
    assert st.save_campaign(mon, "1234567890", "5", "Brand", {}, "a")["campaigns"] == {}
    with pytest.raises(st.BadRequest, match="campaign id"):
        st.save_campaign(mon, "1234567890", "abc", "x", {"cpa": 1}, "a")


def test_a_campaign_is_left_out_with_a_reason_and_put_back():
    mon = MemStore()
    with pytest.raises(st.BadRequest, match="Say why"):
        st.mute(mon, "1234567890", "5", "Test", "  ", "a")
    got = st.mute(mon, "1234567890", "5", "Test", "a test campaign", "a")
    assert got["muted"] == {"5": "a test campaign"} and got["names"] == {"5": "Test"}
    assert st.unmute(mon, "1234567890", "5", "a")["muted"] == {}
    assert st.load(mon)["accounts"] == {}


def test_the_limits_on_campaigns_and_the_log_hold():
    mon = MemStore()
    for i in range(st.MAX_CAMPAIGNS):
        st.save_campaign(mon, "1234567890", str(1000 + i), "c", {"cpa": 5}, "a")
    with pytest.raises(st.BadRequest, match="At most 100 campaigns"):
        st.save_campaign(mon, "1234567890", "99999", "c", {"cpa": 5}, "a")
    st.save_campaign(mon, "1234567890", "1000", "c", {"cpa": 6}, "a")                                    # changing one that is there is not adding one
    for i in range(st.MAX_CAMPAIGNS):
        st.mute(mon, "1234567890", str(2000 + i), "c", "why", "a")
    with pytest.raises(st.BadRequest, match="At most 100 campaigns can be left out"):
        st.mute(mon, "1234567890", "99999", "c", "why", "a")
    assert st.mute(mon, "1234567890", "2000", "c", "a better why", "a")["muted"]["2000"] == "a better why"
    assert len(st.load(mon)["changes"]) == 30
    assert st.view(mon)["maxCampaigns"] == 100 and st.view(mon)["tolerance"] == [5, 100] and len(st.view(mon)["changes"]) == 10


# ── The plan ────────────────────────────────────────────────────────────────

def analysed(campaigns, config=None):
    from tracker import paid_kpi_math as m
    return m.analyse({"campaigns": campaigns, "currency": "USD"}, config=config or {}, today=TODAY)


def test_an_account_with_no_target_is_first_told_to_save_one():
    steps = plan.build(analysed([{"id": "1", "name": "A", "primaryStatus": "ELIGIBLE", "days": []}]))["steps"]
    assert steps[0]["id"] == "targets" and steps[0]["level"] == "info" and steps[0]["campaigns"] == 1
    assert [s["id"] for s in steps] == ["targets", "not_delivering", "recheck"]


def test_an_account_with_nothing_wrong_and_no_target_only_asks_for_the_target():
    rows = [{"date": (TODAY - dt.timedelta(days=i)).isoformat(), "impr": 2000, "clicks": 200, "cost": 40.0, "conv": 1.0, "value": 0.0} for i in range(1, 15)]
    assert [s["id"] for s in plan.build(analysed([{"id": "1", "name": "A", "primaryStatus": "ELIGIBLE", "days": rows}]))["steps"]] == ["targets"]


def test_a_clean_account_with_targets_has_no_steps_and_one_without_campaigns_none_either():
    rows = [{"date": (TODAY - dt.timedelta(days=i)).isoformat(), "impr": 2000, "clicks": 200, "cost": 40.0, "conv": 1.0, "value": 0.0} for i in range(1, 15)]
    assert plan.build(analysed([{"id": "1", "name": "A", "primaryStatus": "ELIGIBLE", "days": rows}], {"targets": {"cpa": 50}}))["steps"] == []
    assert plan.build(analysed([]))["steps"] == []


def test_the_steps_follow_the_rules_and_name_the_first_campaigns():
    rows = [{"date": (TODAY - dt.timedelta(days=i)).isoformat(), "impr": 2000, "clicks": 200, "cost": 100.0, "conv": 1.0, "value": 0.0} for i in range(1, 15)]
    campaigns = [{"id": str(i), "name": "Camp %d" % i, "primaryStatus": "ELIGIBLE", "days": rows} for i in range(1, 6)]
    steps = plan.build(analysed(campaigns, {"targets": {"cpa": 40}}))["steps"]
    assert [s["id"] for s in steps] == ["cpa", "recheck"]
    s = steps[0]
    assert s["title"] == "Bring the cost per conversion of 5 campaigns back to its target" and s["campaigns"] == 5 and s["findings"] == 5 and s["level"] == "high"
    assert len(s["where"]) == 3 and len(s["examples"]) == 3 and s["how"].startswith("Look at what spent without converting")
    assert plan.build(analysed(campaigns[:1], {"targets": {"cpa": 40}}))["steps"][0]["title"] == "Bring the cost per conversion of 1 campaign back to its target"
