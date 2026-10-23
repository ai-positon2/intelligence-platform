"""The monitors together: the scheduled run, the weekly summary and what the alert panel shows (tracker/paid_monitors.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_brandspend as TB  # noqa: E402
from tests import test_paid_anomaly as TA  # noqa: E402
from tests import test_paid_disapproved as TD  # noqa: E402
from tracker import paid_brandspend_settings as st  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

BASE = "https://p.example"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
LISTED = [dict(A, currency="USD")]
TUE = dt.datetime(2026, 10, 20, 7, 0, tzinfo=dt.timezone.utc)
MON = dt.datetime(2026, 10, 26, 7, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    from tracker import paid_geo, paid_store
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield
    paid_geo.clear_cache()
    base.forget("")


class Search:
    """One stand-in for Google Ads that answers both monitors: the brand spend rows and the flagged ads."""

    def __init__(self):
        self.rows, self.ads = {A["id"]: TB.weeks_of(weekly=TB.USUAL + [203])}, {A["id"]: []}

    def __call__(self, cid, q):
        if "FROM customer" in q:
            return TA.hourly_rows()
        if "FROM keyword_view" in q or "auction_insight_domain" in q:
            return []                                                                    # Keyword Visibility Tracker: an account with no keyword that spent has nothing to track
        if "campaign.primary_status_reasons" in q or q.startswith("SELECT customer.time_zone, campaign.id") or q.startswith("SELECT campaign.id, metrics.search_budget_lost_impression_share"):
            return []                                                                    # KPI Monitoring: an account with no campaign has nothing off target
        if "geo_target_type_setting" in q or "FROM campaign_criterion" in q or "FROM geo_target_constant" in q or "FROM ad_group WHERE" in q:
            return []                                                                    # Location Conflicts: an account with no campaign set to show anywhere has nothing to conflict
        if "FROM ad_group_ad WHERE" in q or "FROM asset_group_asset" in q or "FROM ad_group_criterion" in q or "FROM asset_group " in q or ("segments.week" in q) or ("FROM ad_group_ad" in q):
            return TD.searcher(self.ads)(cid, q)
        return TB.searcher(self.rows)(cid, q)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def inventory():
    return [A], [], []


def sched(store, dv, search, now):
    return pm.run_scheduled(store, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: now, search_fn=search, list_fn=lambda: LISTED)


def test_only_the_four_monitors_are_known_and_each_has_its_page_link():
    assert sorted(pm.AGENTS) == ["brandspend", "copycheck", "disapproved", "geoconf", "kpi", "kvis", "spend"] and pm.agent_of("brandspend") == "brandspend"
    with pytest.raises(pm.BadRequest, match="must be one of: brandspend, copycheck, disapproved, geoconf, kpi, kvis, spend"):
        pm.agent_of("other")
    assert pm.link_for("copycheck", BASE) == BASE + "/p2/paid-marketing/ad-copy-checker" and pm.AGENTS["copycheck"]["weekdays"] == (0,) and "weekdays" not in pm.AGENTS["brandspend"]
    assert pm.AGENTS["spend"]["frequent"] is True and not [a for a in ("brandspend", "copycheck", "disapproved", "geoconf", "kpi", "kvis") if pm.AGENTS[a].get("frequent")] and pm.link_for("spend", BASE) == BASE + "/p2/paid-marketing/spend-anomaly"
    assert pm.link_for("brandspend", "https://p.example/") == "https://p.example/p2/paid-marketing/brand-spend" and pm.link_for("disapproved", BASE) == BASE + "/p2/paid-marketing/disapproved-ads"


def test_the_scheduled_run_runs_every_monitor_once_and_the_first_run_of_each_is_its_baseline():
    s, dv, se = configured(), Deliver(), Search()
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, TUE)
    assert sorted(out) == ["brandspend", "disapproved", "geoconf", "kpi", "kvis"] and "copycheck" not in out and out["brandspend"]["baseline"] and out["disapproved"]["baseline"] and not dv.emails
    assert s.issues_all("disapproved")[0]["kind"] == "stopped" and s.state_get("baseline:brandspend") and s.state_get("baseline:disapproved")


def test_the_ad_copy_checker_runs_on_mondays_only_and_its_first_run_is_a_baseline():
    s, dv, se = configured(), Deliver(), Search()
    assert "copycheck" not in sched(s, dv, se, TUE) and "kvis" not in sched(configured(), dv, se, MON)
    out = sched(s, dv, se, MON)
    assert sorted(k for k in out if k != "weekly") == ["brandspend", "copycheck", "disapproved", "geoconf", "kpi"] and out["copycheck"]["baseline"] is True and s.state_get("baseline:copycheck")
    assert sorted(out["weekly"]) == ["brandspend", "copycheck", "disapproved", "geoconf", "kpi", "kvis", "spend"]


def test_a_problem_after_the_baseline_is_alerted_by_the_monitor_it_belongs_to_with_its_own_link():
    s, dv, se = configured(), Deliver(), Search()
    sched(s, dv, se, TUE)
    se.rows[A["id"]] = TB.weeks_of(weekly=TB.USUAL + [301])
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, TUE + dt.timedelta(days=1))
    assert out["brandspend"]["new"] == 1 and out["disapproved"]["new"] == 1 and len(dv.emails) == 2
    subjects = sorted(e["subject"] for e in dv.emails)
    assert subjects == ["Brand Spend Alert: 1 new (1 account)", "HIGH: Disapproved Ads Tracker: 1 new (1 account)"]
    by = {e["subject"].split(":")[-2].strip() if e["subject"].startswith("HIGH") else e["subject"].split(":")[0]: e for e in dv.emails}
    assert by["Disapproved Ads Tracker"]["text"].endswith("Open the page: https://p.example/p2/paid-marketing/disapproved-ads") and by["Brand Spend Alert"]["text"].endswith("Open the page: https://p.example/p2/paid-marketing/brand-spend")


def test_the_weekly_summary_goes_out_on_a_monday_only_once_and_only_when_a_channel_took_it():
    s, dv, se = configured(), Deliver(), Search()
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, TUE)
    assert "weekly" not in out and not dv.emails                                         # a Tuesday: no summary
    out = sched(s, dv, se, MON)                                                          # that Monday the brand rows no longer reach the last whole week: that is an open issue too
    w = out["weekly"]
    assert w["disapproved"]["sent"] is True and w["brandspend"]["sent"] is True and len(dv.emails) == 3                    # the brand spend alert for what turned up, and the two summaries
    subjects = sorted(e["subject"] for e in dv.emails if "weekly summary" in e["subject"])
    assert len(subjects) == 2 and subjects[1] == "Disapproved Ads Tracker: weekly summary, 1 open (1 high), 0 fixed this week" and subjects[0].startswith("Brand Spend Alert: weekly summary, ")
    assert s.state_get("digest:disapproved") == {"day": "2026-10-26"} and s.state_get("digest:brandspend") == {"day": "2026-10-26"}
    again = sched(s, dv, se, MON + dt.timedelta(hours=5))
    assert again["weekly"]["disapproved"] == {"sent": False, "why": "already sent today"} and again["weekly"]["brandspend"] == {"sent": False, "why": "already sent today"} and len(dv.emails) == 3


def test_an_agent_with_nothing_open_or_fixed_has_no_weekly_summary():
    s, dv = configured(), Deliver()
    assert pm.send_weekly("brandspend", s, base_link=BASE, deliver=dv, now=MON) == {"sent": False, "why": "nothing to say"} and not dv.emails


def test_a_weekly_summary_that_no_channel_took_is_tried_again_the_next_run():
    s, dv, se = MemStore(), Deliver(), Search()
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, MON)
    assert out["weekly"]["disapproved"]["sent"] is False and out["weekly"]["disapproved"]["why"] == "no channel took it" and s.state_get("digest:disapproved") is None
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": ""})
    assert sched(s, dv, se, MON + dt.timedelta(hours=1))["weekly"]["disapproved"]["sent"] is True


def test_the_monitors_use_what_the_team_saved():
    s, dv, se = configured(), Deliver(), Search()
    se.rows[A["id"]] = TB.weeks_of(weekly=TB.USUAL + [301])
    st.set_mark(s, "11", False, "ana", TUE)
    sched(s, dv, se, TUE)
    assert not [i for i in s.issues_all("brandspend") if i["kind"] == "over"]


def test_the_alert_panel_shows_the_recipients_the_senders_the_runs_and_the_open_and_fixed_issues():
    s, dv, se = configured(), Deliver(), Search()
    sched(s, dv, se, TUE)
    se.ads[A["id"]] = [TD.api()]
    sched(s, dv, se, TUE + dt.timedelta(days=1))
    se.ads[A["id"]] = []
    sched(s, dv, se, TUE + dt.timedelta(days=4))
    se.ads[A["id"]] = [TD.api(ad="9", group="11")]
    sched(s, dv, se, TUE + dt.timedelta(days=5))
    v = pm.state_for(s, "disapproved", dv, now=TUE + dt.timedelta(days=5))
    assert v["label"] == "Disapproved Ads Tracker" and v["alerts"] == {"emails": ["ppc@example.com"], "slackChannel": "#alerts"} and v["senders"] == {"email": True, "slack": True}
    assert v["baseline"]["by"] == "schedule" and len(v["runs"]) == 4 and v["runs"][0]["trigger"] == "schedule" and v["counts"] == {"high": 1, "medium": 0, "low": 0} and v["openTotal"] == 1
    o = v["open"][0]
    assert o["title"] == "Group 11 cannot show any ad" and o["level"] == "high" and o["ageDays"] == 0 and o["accountName"] == "Acme Dental" and o["alertedAt"]
    f = v["fixed"][0]
    assert f["title"] == "Group 10 cannot show any ad" and f["resolvedAt"].startswith("2026-10-24") and f["ageDays"] == 3 and v["digest"] is None
    with pytest.raises(pm.BadRequest):
        pm.state_for(s, "other", dv)


def test_the_panel_lists_forty_open_issues_at_most_and_says_how_many_there_are():
    s, dv = configured(), Deliver()
    rows = [{"agent": "brandspend", "fingerprint": "f%d" % n, "accountId": "1", "accountName": "A", "platform": "google", "kind": "x", "level": "low", "title": "T%02d" % n, "detail": "", "nextStep": "",
             "data": {}, "status": "open", "firstSeen": "2026-10-10T07:00:00+00:00", "lastSeen": "2026-10-20T07:00:00+00:00", "resolvedAt": None, "reopened": 0, "alertedAt": None, "alertedLevel": None, "alert": {}} for n in range(55)]
    s.issues_put(rows)
    v = pm.state_for(s, "brandspend", dv, now=TUE)
    assert len(v["open"]) == 40 and v["openTotal"] == 55 and v["counts"]["low"] == 55 and v["open"][0]["title"] == "T00"


NOON = dt.datetime(2026, 10, 22, 19, 0, tzinfo=dt.timezone.utc)                # a Thursday, 12:00 in Los Angeles


def test_the_daily_run_leaves_the_frequent_monitor_to_its_own_run():
    s, dv, se = configured(), Deliver(), Search()
    out = sched(s, dv, se, NOON)
    assert sorted(out) == ["brandspend", "disapproved", "geoconf", "kpi"] and not s.state_get("baseline:spend")


def test_a_run_for_named_monitors_runs_only_those_and_sends_no_weekly_summary_even_on_a_monday():
    s, dv, se = configured(), Deliver(), Search()
    out = pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: MON.replace(hour=19), search_fn=se, list_fn=lambda: LISTED, only=["spend"])
    assert sorted(out) == ["spend"] and out["spend"]["status"] == "done" and out["spend"]["baseline"] is True and s.state_get("baseline:spend") and not s.state_get("baseline:brandspend")
    assert "weekly" not in out


def test_named_monitors_run_whatever_the_weekday_and_an_unknown_name_is_refused():
    s, dv, se = configured(), Deliver(), Search()
    out = pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=["copycheck", "spend"])
    assert sorted(out) == ["copycheck", "spend"]
    with pytest.raises(pm.BadRequest, match="must be one of"):
        pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=["other"])


def test_an_empty_list_is_the_daily_run():
    s, dv, se = configured(), Deliver(), Search()
    out = pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=[])
    assert sorted(out) == ["brandspend", "disapproved", "geoconf", "kpi"]


def test_the_monday_summary_covers_the_spend_alert_too():
    s, dv, se = configured(), Deliver(), Search()
    pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=["spend"])
    s.issues_put([{"agent": "spend", "fingerprint": "x:spike:2026-10-19", "accountId": "1", "accountName": "Acme", "platform": "google", "kind": "spike", "level": "medium", "title": "t", "detail": "d",
                   "nextStep": "n", "status": "open", "firstSeen": "2026-10-19T07:00:00+00:00", "lastSeen": "2026-10-26T06:00:00+00:00", "data": {}}])
    sent = pm.send_weekly("spend", s, base_link=BASE, deliver=dv, now=MON)
    assert sent["sent"] is True and dv.emails[-1]["subject"].startswith("Spend Anomaly Alert: weekly summary, 1 open") and dv.emails[-1]["text"].endswith("/p2/paid-marketing/spend-anomaly")


@pytest.mark.parametrize("agent", ["disapproved", "geoconf", "kpi", "copycheck"])
def test_a_real_run_reads_meta_accounts_with_the_platforms_own_meta_reads_when_none_are_handed_in(monkeypatch, agent):
    """The first live run failed every Meta account with "'NoneType' object is not callable": the monitors were built with no Meta reader and only the tests handed one in."""
    from tracker import paid_meta, paid_store
    asked = []
    monkeypatch.setattr(paid_meta, "_pages", lambda path, params, store, **kw: asked.append((path, store is paid_store)) or [])
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act, **kw: {"id": act, "name": "Meta Co", "currency": "EUR"})
    m = {"id": "act_55", "name": "Meta Co", "platform": "meta", "group": "g"}
    spec = pm.spec_for(agent, MemStore(), today=dt.date(2026, 10, 1), search_fn=lambda *a, **k: [], list_fn=lambda: LISTED, now_fn=lambda: TUE)
    got = spec["collect"]([m], [])
    assert asked and all(path.startswith("/act_55") and ours for path, ours in asked)
    assert got["unreadable"] == [] and got["okIds"] == {"act_55"}


def test_a_meta_account_whose_details_cannot_be_read_is_still_read(monkeypatch):
    from tracker import paid_meta
    monkeypatch.setattr(paid_meta, "_pages", lambda path, params, store, **kw: [])

    def down(store, act, **kw):
        raise RuntimeError("Meta is down")
    monkeypatch.setattr(paid_meta, "account_by_id", down)
    m = {"id": "act_55", "name": "Meta Co", "platform": "meta", "group": "g"}
    got = pm.spec_for("disapproved", MemStore(), today=dt.date(2026, 10, 1), search_fn=lambda *a, **k: [], list_fn=lambda: LISTED, now_fn=lambda: TUE)["collect"]([m], [])
    assert got["unreadable"] == [] and got["okIds"] == {"act_55"}
