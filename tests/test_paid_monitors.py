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
from tracker import paid_assets as assets  # noqa: E402
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


ASSET_ONLY_QUERIES = {assets.CAMPAIGNS_GAQL, assets.CAMPAIGN_METRICS_GAQL, assets.CAMPAIGN_ASSETS_GAQL, assets.CAMPAIGN_ASSETS_SHOWN_GAQL, assets.CUSTOMER_ASSETS_GAQL, assets.AD_GROUP_ASSETS_GAQL,
                      assets.AD_GROUP_COUNT_GAQL, assets.CAMPAIGN_SETS_GAQL, assets.CUSTOMER_SETS_GAQL}      # the ones only Asset Coverage asks (its group queries are also the Disapproved Ads Tracker's)


class Search:
    """One stand-in for Google Ads that answers both monitors: the brand spend rows and the flagged ads."""

    def __init__(self):
        self.rows, self.ads = {A["id"]: TB.weeks_of(weekly=TB.USUAL + [203])}, {A["id"]: []}

    def __call__(self, cid, q):
        if (q.startswith(("SELECT ad_group_ad.ad.id, ad_group_ad.ad.responsive_search_ad.headlines", "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.ad.name, segments.week",
                          "SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, segments.week", "SELECT campaign.id, campaign.name, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad_asset_view",
                          "SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, asset_group_asset.field_type, asset_group_asset.performance_label")) or q in ASSET_ONLY_QUERIES):
            return []                                                                    # Creative & Asset Health (and the asset coverage it reads): an account with no live ad has nothing to judge
        if ("FROM conversion_action" in q or "segments.conversion_action" in q or "FROM user_list" in q or "accepted_customer_data_terms" in q or "offline_conversion_upload" in q
                or q.startswith("SELECT customer.time_zone, segments.date, metrics.cost_micros") or "FROM customer_client" in q or "FROM account_budget" in q or "FROM change_event" in q
                or "FROM customer_user_access" in q):
            return []                                                                    # Tracking & Data Health: an account that recorded nothing and spent nothing has nothing to track
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


def ran(out):
    """The monitors a scheduled run ran (its result also holds the Alert Hub's send and the weekly summaries)."""
    return sorted(k for k in out if k not in ("hub", "weekly"))


def test_only_the_four_monitors_are_known_and_each_has_its_page_link():
    assert sorted(pm.AGENTS) == ["brandspend", "copycheck", "creative", "disapproved", "geoconf", "kpi", "kvis", "spend", "track", "watch"] and pm.agent_of("brandspend") == "brandspend"
    with pytest.raises(pm.BadRequest, match="must be one of: brandspend, copycheck, creative, disapproved, geoconf, kpi, kvis, spend, track, watch"):
        pm.agent_of("other")
    assert pm.link_for("copycheck", BASE) == BASE + "/p2/paid-marketing/ad-copy-checker" and pm.AGENTS["copycheck"]["weekdays"] == (0,) and "weekdays" not in pm.AGENTS["brandspend"]
    assert pm.AGENTS["spend"]["frequent"] is True and not [a for a in ("brandspend", "copycheck", "creative", "disapproved", "geoconf", "kpi", "kvis", "track", "watch") if pm.AGENTS[a].get("frequent")] and pm.link_for("spend", BASE) == BASE + "/p2/paid-marketing/spend-anomaly"
    assert pm.link_for("brandspend", "https://p.example/") == "https://p.example/p2/paid-marketing/brand-spend" and pm.link_for("disapproved", BASE) == BASE + "/p2/paid-marketing/disapproved-ads"


def test_the_scheduled_run_runs_every_monitor_once_and_the_first_run_of_each_is_its_baseline():
    s, dv, se = configured(), Deliver(), Search()
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, TUE)
    assert ran(out) == ["brandspend", "creative", "disapproved", "geoconf", "kpi", "kvis", "track", "watch"] and "copycheck" not in out and out["brandspend"]["baseline"] and out["disapproved"]["baseline"] and not dv.emails
    assert out["hub"] == {"sends": [], "held": 0, "belowFloor": 0}
    assert s.issues_all("disapproved")[0]["kind"] == "stopped" and s.state_get("baseline:brandspend") and s.state_get("baseline:disapproved")


def test_the_ad_copy_checker_runs_on_mondays_only_and_its_first_run_is_a_baseline():
    s, dv, se = configured(), Deliver(), Search()
    assert "copycheck" not in sched(s, dv, se, TUE) and "kvis" not in sched(configured(), dv, se, MON)
    out = sched(s, dv, se, MON)
    assert ran(out) == ["brandspend", "copycheck", "creative", "disapproved", "geoconf", "kpi", "track", "watch"] and out["copycheck"]["baseline"] is True and s.state_get("baseline:copycheck")
    assert "weekly" in out


def test_the_problems_after_the_baseline_of_every_monitor_go_out_as_one_message_through_the_alert_hub():
    s, dv, se = configured(), Deliver(), Search()
    sched(s, dv, se, TUE)
    se.rows[A["id"]] = TB.weeks_of(weekly=TB.USUAL + [301])
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, TUE + dt.timedelta(days=1))
    assert out["brandspend"]["new"] == 1 and out["disapproved"]["new"] == 1 and len(dv.emails) == 1 and len(dv.slacks) == 1
    m = dv.emails[0]
    assert m["subject"] == "P1: Paid alerts: 1 P1, 1 P2 (1 account)" and m["to"] == ["ppc@example.com"] and dv.slacks[0]["channel"] == "#alerts"
    assert "Disapproved Ads Tracker" in m["text"] and "Brand Spend Alert" in m["text"] and m["text"].endswith("Open the Alert Hub: https://p.example/p2/paid-marketing/alert-hub")
    assert out["hub"]["sends"][0]["issues"] == 2 and out["brandspend"]["alerted"] == 1 and out["disapproved"]["alerted"] == 1 and out["disapproved"]["via"] == "hub"
    assert s.state_get("runs:disapproved")[0]["alerted"] == 1 and all(i["alertedAt"] for a in ("brandspend", "disapproved") for i in s.issues_all(a) if i["level"] in ("high", "medium"))


def test_a_monitor_run_by_hand_sends_its_own_message_through_the_alert_hub():
    s, dv, se = configured(), Deliver(), Search()
    pm.run_agent("disapproved", s, trigger="page by ana", base_link=BASE, deliver=dv, accounts=[A], groups=[], search_fn=se, list_fn=lambda: LISTED, now_fn=lambda: TUE)
    se.ads[A["id"]] = [TD.api()]
    run = pm.run_agent("disapproved", s, trigger="page by ana", base_link=BASE, deliver=dv, accounts=[A], groups=[], search_fn=se, list_fn=lambda: LISTED, now_fn=lambda: TUE + dt.timedelta(days=1))
    assert run["alerted"] == 1 and run["via"] == "hub" and len(dv.emails) == 1 and dv.emails[0]["subject"] == "P1: Paid alerts: 1 P1 (1 account)"
    assert s.state_get(hub_log())[0]["trigger"] == "page by ana"


def hub_log():
    from tracker import paid_alerthub
    return paid_alerthub.LOG_KEY


def test_with_an_outbox_a_monitor_sends_nothing_and_leaves_the_issues_for_the_hub_to_mark():
    s, dv, se = configured(), Deliver(), Search()
    box = []
    pm.run_agent("disapproved", s, trigger="schedule", base_link=BASE, deliver=dv, accounts=[A], groups=[], search_fn=se, list_fn=lambda: LISTED, now_fn=lambda: TUE, outbox=box)
    se.ads[A["id"]] = [TD.api()]
    run = pm.run_agent("disapproved", s, trigger="schedule", base_link=BASE, deliver=dv, accounts=[A], groups=[], search_fn=se, list_fn=lambda: LISTED, now_fn=lambda: TUE + dt.timedelta(days=1), outbox=box)
    assert not dv.emails and not dv.slacks and run["alerted"] == 0 and run["new"] == 1
    assert len(box) == 1 and box[0]["agent"] == "disapproved" and [p["event"] for p in box[0]["pending"]] == ["new"] and box[0]["run"] is run
    assert s.issues_all("disapproved")[0]["alertedAt"] is None


def test_the_weekly_summary_goes_out_on_a_monday_as_one_message_only_once_and_only_when_a_channel_took_it():
    s, dv, se = configured(), Deliver(), Search()
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, TUE)
    assert "weekly" not in out and not dv.emails                                         # a Tuesday: no summary
    out = sched(s, dv, se, MON)                                                          # that Monday the brand rows no longer reach the last whole week: that is an open issue too
    assert out["weekly"]["default"]["sent"] is True and len(dv.emails) == 2              # the alert for what turned up, and the one summary of every monitor
    summaries = [e for e in dv.emails if "weekly summary" in e["subject"]]
    assert len(summaries) == 1 and summaries[0]["subject"].startswith("Paid alerts: weekly summary, ") and "Disapproved Ads Tracker" in summaries[0]["text"] and "Brand Spend Alert" in summaries[0]["text"]
    assert s.state_get("digest:hub:default") == {"day": "2026-10-26"} and s.state_get("digest:hub") == {"day": "2026-10-26"}
    again = sched(s, dv, se, MON + dt.timedelta(hours=5))
    assert again["weekly"]["default"] == {"sent": False, "why": "already sent today"} and len([e for e in dv.emails if "weekly summary" in e["subject"]]) == 1


def test_a_weekly_summary_that_no_channel_took_is_tried_again_the_next_run():
    s, dv, se = MemStore(), Deliver(), Search()
    se.ads[A["id"]] = [TD.api()]
    out = sched(s, dv, se, MON)
    assert out["weekly"]["default"]["sent"] is False and out["weekly"]["default"]["why"] == "no channel is set" and s.state_get("digest:hub") is None
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": ""})
    assert sched(s, dv, se, MON + dt.timedelta(hours=1))["weekly"]["default"]["sent"] is True


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
    assert ran(out) == ["brandspend", "creative", "disapproved", "geoconf", "kpi", "track", "watch"] and not s.state_get("baseline:spend")


def test_a_run_for_named_monitors_runs_only_those_and_sends_no_weekly_summary_even_on_a_monday():
    s, dv, se = configured(), Deliver(), Search()
    out = pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: MON.replace(hour=19), search_fn=se, list_fn=lambda: LISTED, only=["spend"])
    assert ran(out) == ["spend"] and out["spend"]["status"] == "done" and out["spend"]["baseline"] is True and s.state_get("baseline:spend") and not s.state_get("baseline:brandspend")
    assert "weekly" not in out


def test_named_monitors_run_whatever_the_weekday_and_an_unknown_name_is_refused():
    s, dv, se = configured(), Deliver(), Search()
    out = pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=["copycheck", "spend"])
    assert ran(out) == ["copycheck", "spend"]
    with pytest.raises(pm.BadRequest, match="must be one of"):
        pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=["other"])


def test_an_empty_list_is_the_daily_run():
    s, dv, se = configured(), Deliver(), Search()
    out = pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=[])
    assert ran(out) == ["brandspend", "creative", "disapproved", "geoconf", "kpi", "track", "watch"]


def test_the_monday_summary_covers_the_spend_alert_too():
    s, dv, se = configured(), Deliver(), Search()
    pm.run_scheduled(s, base_link=BASE, deliver=dv, inventory_fn=inventory, now_fn=lambda: NOON, search_fn=se, list_fn=lambda: LISTED, only=["spend"])
    s.issues_put([{"agent": "spend", "fingerprint": "x:spike:2026-10-19", "accountId": "1", "accountName": "Acme", "platform": "google", "kind": "spike", "level": "medium", "title": "t", "detail": "d",
                   "nextStep": "n", "status": "open", "firstSeen": "2026-10-19T07:00:00+00:00", "lastSeen": "2026-10-26T06:00:00+00:00", "data": {}}])
    from tracker import paid_alerthub
    sent = paid_alerthub.weekly(s, base_link=BASE, deliver=dv, now=MON, agents=pm.AGENTS)
    assert sent["default"]["sent"] is True and dv.emails[-1]["subject"].startswith("Paid alerts: weekly summary, 1 open") and "Spend Anomaly Alert" in dv.emails[-1]["text"]


@pytest.mark.parametrize("agent", ["disapproved", "geoconf", "kpi", "copycheck", "track", "watch", "creative"])
def test_a_real_run_reads_meta_accounts_with_the_platforms_own_meta_reads_when_none_are_handed_in(monkeypatch, agent):
    """The first live run failed every Meta account with "'NoneType' object is not callable": the monitors were built with no Meta reader and only the tests handed one in."""
    from tracker import paid_meta, paid_store
    asked = []
    monkeypatch.setattr(paid_meta, "_pages", lambda path, params, store, **kw: asked.append((path, store is paid_store)) or [])
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act, **kw: {"id": act, "name": "Meta Co", "currency": "EUR"})
    m = {"id": "act_5555", "name": "Meta Co", "platform": "meta", "group": "g"}
    spec = pm.spec_for(agent, MemStore(), today=dt.date(2026, 10, 1), search_fn=lambda *a, **k: [], list_fn=lambda: LISTED, now_fn=lambda: TUE)
    got = spec["collect"]([m], [])
    assert asked and all(path.startswith("/act_5555") and ours for path, ours in asked)
    assert got["unreadable"] == [] and got["okIds"] == {"act_5555"}


def test_a_meta_account_whose_details_cannot_be_read_is_still_read(monkeypatch):
    from tracker import paid_meta
    monkeypatch.setattr(paid_meta, "_pages", lambda path, params, store, **kw: [])

    def down(store, act, **kw):
        raise RuntimeError("Meta is down")
    monkeypatch.setattr(paid_meta, "account_by_id", down)
    m = {"id": "act_5555", "name": "Meta Co", "platform": "meta", "group": "g"}
    got = pm.spec_for("disapproved", MemStore(), today=dt.date(2026, 10, 1), search_fn=lambda *a, **k: [], list_fn=lambda: LISTED, now_fn=lambda: TUE)["collect"]([m], [])
    assert got["unreadable"] == [] and got["okIds"] == {"act_5555"}
