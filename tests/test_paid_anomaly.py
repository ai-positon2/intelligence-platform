"""Spend Anomaly Alert: reading Google and Meta, the extras of an account that looks wrong, the report and its cache (tracker/paid_anomaly.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_anomaly as an  # noqa: E402
from tracker import paid_anomaly_math as m  # noqa: E402
from tracker import paid_anomaly_settings as st  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

NOW = dt.datetime(2026, 10, 22, 19, 0, tzinfo=dt.timezone.utc)         # 12:00 in Los Angeles (PDT)
TODAY = dt.date(2026, 10, 22)
TZ = "America/Los_Angeles"
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}


def hourly_rows(per_hour=10.0, today_per_hour=10.0, boost=None, through=12, tz=TZ, days=15):
    """The customer read: `days` whole days at per_hour, and today's hours before `through` at today_per_hour (with `boost` {hour: cost})."""
    boost = boost or {}
    rows = []
    for n in range(1, days + 1):
        d = TODAY - dt.timedelta(days=n)
        rows += [{"customer": {"timeZone": tz}, "segments": {"date": d.isoformat(), "hour": h}, "metrics": {"costMicros": str(int(per_hour * 1e6))}} for h in range(24)]
    rows += [{"customer": {"timeZone": tz}, "segments": {"date": TODAY.isoformat(), "hour": h}, "metrics": {"costMicros": str(int(boost.get(h, today_per_hour) * 1e6))}} for h in range(through)]
    return rows


def camps(spike=True):
    out = []
    for n in range(1, 15):
        d = (TODAY - dt.timedelta(days=n)).isoformat()
        out += [{"campaign": {"id": "1", "name": "Brand", "startDate": "2020-01-01"}, "segments": {"date": d}, "metrics": {"costMicros": str(100 * 10 ** 6)}},
                {"campaign": {"id": "2", "name": "Generic", "startDate": "2020-01-01"}, "segments": {"date": d}, "metrics": {"costMicros": str(140 * 10 ** 6)}}]
    out += [{"campaign": {"id": "1", "name": "Brand", "startDate": "2020-01-01"}, "segments": {"date": TODAY.isoformat()}, "metrics": {"costMicros": str(50 * 10 ** 6)}},
            {"campaign": {"id": "2", "name": "Generic", "startDate": "2020-01-01"}, "segments": {"date": TODAY.isoformat()}, "metrics": {"costMicros": str((520 if spike else 70) * 10 ** 6)}}]
    return out


def budgets():
    return [{"campaign": {"id": "1"}, "campaignBudget": {"id": "b1", "amountMicros": str(100 * 10 ** 6), "explicitlySharedd": False}},
            {"campaign": {"id": "2"}, "campaignBudget": {"id": "b2", "amountMicros": str(150 * 10 ** 6)}}]


def change(at, user, kind="CAMPAIGN_BUDGET", op="UPDATE", fields="amountMicros", old=None, new=None, client="GOOGLE_ADS_WEB_CLIENT"):
    return {"changeEvent": {"changeDateTime": at, "userEmail": user, "clientType": client, "changeResourceType": kind, "resourceChangeOperation": op, "changedFields": fields,
                            "oldResource": old or {}, "newResource": new or {}}}


def searcher(rows=None, extra=None, log=None, fail=(), refuse_hours=False, quota_extra=False):
    rows = rows if rows is not None else {ACCT["id"]: hourly_rows()}
    extra = extra or {}

    def run(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        if "FROM customer" in q:
            if "segments.hour" in q:
                if refuse_hours:
                    raise gads.GadsError("bad_request", "Unknown field segments.hour")
                return rows.get(cid, [])
            return [dict(r, segments={"date": r["segments"]["date"]}) for r in _daily(rows.get(cid, []))]
        if quota_extra:
            raise gads.GadsError("quota", "The daily limit is used up.")
        if "FROM change_event" in q:
            return extra.get("changes", {}).get(cid, [])
        if "campaign_budget.amount_micros" in q:
            return extra.get("budgets", {}).get(cid, [])
        if "FROM campaign" in q:
            return extra.get("campaigns", {}).get(cid, [])
        raise AssertionError("unexpected query " + q)
    return run


def _daily(rows):
    by = {}
    for r in rows:
        d = r["segments"]["date"]
        by.setdefault(d, dict(r, metrics={"costMicros": 0}))
        by[d]["metrics"]["costMicros"] += int(r["metrics"]["costMicros"])
    return list(by.values())


# ── Reading the answers ─────────────────────────────────────────────────────

def test_the_hourly_read_gives_the_time_zone_and_the_cost_in_money():
    tz, rows = an.parse_hourly([{"customer": {"timeZone": TZ}, "segments": {"date": "2026-10-21", "hour": 5}, "metrics": {"costMicros": "12500000"}},
                                {"customer": {"timeZone": TZ}, "segments": {"date": "2026-10-21", "hour": 0}, "metrics": {"costMicros": 0}},
                                {"segments": {"hour": 1}, "metrics": {"costMicros": "5"}}, {"segments": {"date": "2026-10-21", "hour": 1}, "metrics": {}}])
    assert tz == TZ and rows == [{"date": "2026-10-21", "hour": 5, "cost": 12.5}, {"date": "2026-10-21", "hour": 0, "cost": 0.0}]


def test_the_daily_form_has_no_hours():
    _tz, rows = an.parse_hourly([{"customer": {"timeZone": "UTC"}, "segments": {"date": "2026-10-21"}, "metrics": {"costMicros": "3000000"}}])
    assert rows == [{"date": "2026-10-21", "hour": None, "cost": 3.0}]


def test_campaign_days_keep_the_start_date_and_leave_out_a_day_without_cost():
    rows = an.parse_campaign_days([{"campaign": {"id": 7, "name": "X", "startDate": "2026-10-20"}, "segments": {"date": "2026-10-21"}, "metrics": {"costMicros": "2500000"}},
                                   {"campaign": {"id": 8}, "segments": {"date": "2026-10-21"}, "metrics": {"costMicros": "0"}}, {"segments": {"date": "2026-10-21"}, "metrics": {"costMicros": "1"}}])
    assert rows == [{"id": "7", "name": "X", "date": "2026-10-21", "cost": 2.5, "startDate": "2026-10-20"}]


def test_budgets_are_added_up_and_a_shared_budget_counts_once():
    rows = [{"campaignBudget": {"id": "1", "amountMicros": "100000000"}}, {"campaignBudget": {"id": "2", "amountMicros": "50000000", "explicitlyShared": True}},
            {"campaignBudget": {"id": "2", "amountMicros": "50000000", "explicitlyShared": True}}, {"campaignBudget": {"id": "3"}}, {}]
    assert an.parse_budgets(rows) == 150.0
    assert an.parse_budgets([]) is None and an.parse_budgets([{"campaignBudget": {"id": "3"}}]) is None


LOCAL = dt.datetime(2026, 10, 22, 12, 0)


def test_the_recent_changes_say_what_was_changed_and_by_whom():
    rows = [change("2026-10-22 09:15:00", "Ann@x.com", old={"campaignBudget": {"amountMicros": "50000000"}}, new={"campaignBudget": {"amountMicros": "400000000"}}),
            change("2026-10-22 08:00:00", "", kind="CAMPAIGN", op="CREATE", fields="", client="GOOGLE_ADS_SCRIPTS"),
            change("2026-10-21 18:00:00", "ann@x.com", kind="CAMPAIGN", fields="status"),
            change("2026-10-22 07:00:00", "ann@x.com", kind="AD", op="UPDATE", fields="x"),               # an ad is not one of the things named
            change("2026-10-22 06:00:00", "ann@x.com", kind="CAMPAIGN_BUDGET", op="REMOVE", fields=""),
            change("2026-10-22 05:00:00", "ann@x.com", kind="CAMPAIGN_BUDGET", fields="name"),            # a budget's name is not worth a word
            change("not a time", "ann@x.com"),
            change("2026-10-10 10:00:00", "ann@x.com")]                                                    # earlier than 48 hours: only shows who is not new
    out, truncated = an.parse_changes(rows, LOCAL)
    assert truncated is False
    assert [(c["at"], c["user"], c["label"], c["newUser"]) for c in out] == [
        ("2026-10-22 09:15:00", "ann@x.com", "a budget raised from $50.00 to $400", False), ("2026-10-22 08:00:00", "a Google Ads script", "a campaign created", False),
        ("2026-10-22 06:00:00", "ann@x.com", "a budget removed", False), ("2026-10-21 18:00:00", "ann@x.com", "a campaign switched on or off", False)]


def test_someone_with_no_earlier_change_is_marked_new_but_only_when_the_history_is_whole():
    rows = [change("2026-10-22 09:15:00", "zed@y.com", old={"campaignBudget": {"amountMicros": "50000000"}}, new={"campaignBudget": {"amountMicros": "40000000"}}), change("2026-10-12 09:15:00", "ann@x.com")]
    out, _ = an.parse_changes(rows, LOCAL)
    assert out[0]["label"] == "a budget lowered from $50.00 to $40.00" and out[0]["newUser"] is True
    long = rows + [change("2026-10-11 09:15:00", "ann@x.com")] * an.CHANGES_LIMIT
    out, truncated = an.parse_changes(long, LOCAL)
    assert truncated is True and out[0]["newUser"] is False


def test_a_budget_change_without_the_old_and_new_amounts_still_says_so():
    out, _ = an.parse_changes([change("2026-10-22 09:15:00", "ann@x.com")], LOCAL)
    assert out[0]["label"] == "a budget amount changed"


def test_a_change_with_no_user_is_named_by_where_it_came_from_and_changes_are_capped():
    out, _ = an.parse_changes([change("2026-10-22 09:15:00", "", client="GOOGLE_ADS_API"), change("2026-10-22 09:16:00", "", client="WHO_KNOWS")], LOCAL)
    assert [c["user"] for c in out] == ["an unknown source", "the Google Ads API"]
    many = [change("2026-10-22 09:%02d:00" % i, "a%d@x.com" % i) for i in range(20)]
    assert len(an.parse_changes(many, LOCAL)[0]) == an.SHOWN_CHANGES


# ── Meta ────────────────────────────────────────────────────────────────────

def test_meta_rows_are_money_by_day_or_by_hour():
    rows = an.parse_meta_rows([{"date_start": "2026-10-21", "spend": "12.50"}, {"date_start": "2026-10-21", "spend": "3", "hourly_stats_aggregated_by_advertiser_time_zone": "07:00:00 - 07:59:59"},
                               {"spend": "3"}, {"date_start": "2026-10-21"}])
    assert rows == [{"date": "2026-10-21", "hour": None, "cost": 12.5}, {"date": "2026-10-21", "hour": 7, "cost": 3.0}]


def test_the_meta_requests():
    path, p = an.meta_daily_request("act_1", "2026-10-06", "2026-10-23")
    assert path == "/act_1/insights" and p["level"] == "account" and p["time_increment"] == 1 and p["fields"] == "spend,date_start" and "breakdowns" not in p
    assert p["time_range"] == '{"since": "2026-10-06", "until": "2026-10-23"}'
    _p, h = an.meta_daily_request("act_1", "a", "b", hourly=True)
    assert h["breakdowns"] == "hourly_stats_aggregated_by_advertiser_time_zone"
    _p, c = an.meta_daily_request("act_1", "a", "b", level="campaign")
    assert c["level"] == "campaign" and "campaign_name" in c["fields"]
    assert an.parse_meta_campaigns([{"campaign_id": 5, "campaign_name": "C", "date_start": "2026-10-21", "spend": "9"}, {"campaign_id": 6, "date_start": "2026-10-21", "spend": "0"}]) == [
        {"id": "5", "name": "C", "date": "2026-10-21", "cost": 9.0, "startDate": ""}]


# ── Time ────────────────────────────────────────────────────────────────────

def test_the_accounts_own_day_and_hour_are_worked_out_from_its_zone():
    local, warn = an.local_now(TZ, NOW)
    assert local == dt.datetime(2026, 10, 22, 12, 0) and warn == ""
    local, _ = an.local_now("Asia/Kolkata", NOW)
    assert local == dt.datetime(2026, 10, 23, 0, 30)                                  # already tomorrow there


def test_an_unknown_or_missing_zone_falls_back_to_utc_and_says_so():
    local, warn = an.local_now("Mars/Olympus", NOW)
    assert local == dt.datetime(2026, 10, 22, 19, 0) and "not recognised (Mars/Olympus)" in warn
    local, warn = an.local_now("", NOW)
    assert local == dt.datetime(2026, 10, 22, 19, 0) and "not known" in warn


# ── One Google account ──────────────────────────────────────────────────────

def test_a_google_account_is_read_with_one_query_and_its_own_day_and_hour():
    log = []
    raw = an.read_google(ACCT, searcher(log=log), NOW)
    assert len(log) == 1 and "segments.hour" in log[0][1] and "FROM customer" in log[0][1]
    assert raw["tz"] == TZ and raw["today"] == "2026-10-22" and raw["hour"] == 12 and raw["local"] == "2026-10-22 12:00" and raw["hourly"] is True and raw["warnings"] == []
    assert "BETWEEN '2026-10-06' AND '2026-10-23'" in log[0][1]
    assert len(raw["rows"]) == 15 * 24 + 12


def test_google_refusing_the_hours_gives_the_daily_form_and_says_so():
    log = []
    raw = an.read_google(ACCT, searcher(log=log, refuse_hours=True), NOW)
    assert len(log) == 2 and "segments.hour" not in log[1][1] and raw["hourly"] is False and "only whole days are judged" in raw["warnings"][0]
    assert all(r["hour"] is None for r in raw["rows"])


def test_a_spent_quota_is_raised_not_hidden():
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        an.read_google(ACCT, spent, NOW)


def test_a_healthy_account_costs_one_read():
    log = []
    raw = an.read_account(dict(ACCT, platform="google"), search=searcher(log=log), now_utc=NOW)
    assert len(log) == 1 and raw["extra"] is None and raw["id"] == ACCT["id"] and raw["platform"] == "google" and raw["currency"] == "USD"


def test_an_account_that_looks_wrong_is_read_further_for_the_cause_the_budgets_and_the_changes():
    log = []
    rows = {ACCT["id"]: hourly_rows(boost={9: 60, 10: 60, 11: 60})}
    extra = {"campaigns": {ACCT["id"]: camps()}, "budgets": {ACCT["id"]: budgets()}, "changes": {ACCT["id"]: [change("2026-10-22 08:30:00", "zed@y.com", old={"campaignBudget": {"amountMicros": "50000000"}},
                                                                                                                     new={"campaignBudget": {"amountMicros": "400000000"}}), change("2026-10-12 08:30:00", "ann@x.com")]}}
    raw = an.read_account(dict(ACCT, platform="google"), search=searcher(rows, extra, log=log), now_utc=NOW)
    assert len(log) == 4 and raw["extra"]["budget"] == 250.0 and len(raw["extra"]["campaigns"]) == 30 and raw["extra"]["changes"][0]["newUser"] is True and raw["warnings"] == []
    a = an.analyse_raw(raw)
    spike = [f for f in a["findings"] if f["kind"] == "spike"][0]
    assert spike["cause"]["kind"] == "one" and "\"Generic\"" in spike["detail"] and "zed@y.com (made no change in the days before)" in spike["detail"] and "a budget raised from $50.00 to $400" in spike["detail"]
    assert a["budget"] == 250.0


def test_a_part_that_cannot_be_read_is_a_warning_and_never_costs_the_account():
    rows = {ACCT["id"]: hourly_rows(boost={9: 60, 10: 60, 11: 60})}
    raw = an.read_account(dict(ACCT, platform="google"), search=searcher(rows, quota_extra=True), now_utc=NOW)
    assert raw["extra"]["campaigns"] is None and raw["extra"]["budget"] is None and raw["extra"]["changes"] is None
    assert [w.split(" could not")[0] for w in raw["warnings"]] == ["The campaigns' spend by day", "The campaigns' budgets", "The change history"]
    assert [f["kind"] for f in an.analyse_raw(raw)["findings"] if f["kind"] == "spike"] == ["spike"]


def test_only_a_medium_or_high_spike_or_surge_asks_for_the_extras():
    mild = {ACCT["id"]: hourly_rows(today_per_hour=16)}                                # 1.6x: a low spike
    log = []
    raw = an.read_account(dict(ACCT, platform="google"), search=searcher(mild, log=log), now_utc=NOW)
    assert len(log) == 1 and raw["extra"] is None
    assert an.needs_extra(an.analyse_raw(raw)) is False
    drop = {ACCT["id"]: hourly_rows(today_per_hour=0.0)}
    log = []
    an.read_account(dict(ACCT, platform="google"), search=searcher(drop, log=log), now_utc=dt.datetime(2026, 10, 22, 23, 0, tzinfo=dt.timezone.utc))
    assert len(log) == 1                                                              # a drop is explained by the account, not by its campaigns


# ── One Meta account ────────────────────────────────────────────────────────

def meta_pages(daily=10.0 * 24, hours=True, today_hours=12, boost=None, refuse_hours=False, log=None, campaigns=None):
    boost = boost or {}

    def pages(path, params):
        if log is not None:
            log.append((path, params))
        if params.get("level") == "campaign":
            return campaigns or []
        if "breakdowns" in params:
            if refuse_hours:
                raise RuntimeError("Meta would not give hours")
            rows = []
            for n in range(1, 16):
                d = (TODAY - dt.timedelta(days=n)).isoformat()
                rows += [{"date_start": d, "spend": "10", "hourly_stats_aggregated_by_advertiser_time_zone": "%02d:00:00 - %02d:59:59" % (h, h)} for h in range(24)]
            rows += [{"date_start": TODAY.isoformat(), "spend": str(boost.get(h, 10)), "hourly_stats_aggregated_by_advertiser_time_zone": "%02d:00:00 - %02d:59:59" % (h, h)} for h in range(today_hours)]
            return rows
        rows = [{"date_start": (TODAY - dt.timedelta(days=n)).isoformat(), "spend": str(daily)} for n in range(1, 16)]
        return rows + [{"date_start": TODAY.isoformat(), "spend": "120"}]
    return pages


META = {"id": "act_5555", "name": "Beta Meta", "currency": "EUR", "platform": "meta", "tz": TZ}


def test_a_meta_account_uses_hours_when_meta_gives_them():
    log = []
    raw = an.read_meta("act_5555", meta_pages(log=log), META, NOW)
    assert len(log) == 2 and raw["hourly"] is True and raw["hour"] == 12 and raw["today"] == "2026-10-22" and len(raw["rows"]) == 15 * 24 + 12 and raw["warnings"] == []


def test_meta_refusing_the_hours_gives_whole_days_and_says_so():
    raw = an.read_meta("act_5555", meta_pages(refuse_hours=True), META, NOW)
    assert raw["hourly"] is False and len(raw["rows"]) == 16 and raw["warnings"] == ["The spend by hour could not be read (RuntimeError: Meta would not give hours)."]


def test_meta_with_no_hour_rows_says_so():
    raw = an.read_meta("act_5555", lambda path, params: [] if "breakdowns" in params else [{"date_start": "2026-10-21", "spend": "5"}], META, NOW)
    assert raw["hourly"] is False and raw["warnings"] == ["Meta gave no spend by hour, so only whole days are judged."]


def test_a_meta_spike_reads_the_campaigns_but_has_no_budget_or_changes():
    camps_meta = [{"campaign_id": "1", "campaign_name": "Prospecting", "date_start": (TODAY - dt.timedelta(days=n)).isoformat(), "spend": "100"} for n in range(1, 15)]
    camps_meta += [{"campaign_id": "1", "campaign_name": "Prospecting", "date_start": TODAY.isoformat(), "spend": "560"}]
    log = []
    raw = an.read_account(META, pages=meta_pages(boost={9: 60, 10: 60, 11: 60}, log=log, campaigns=camps_meta), meta_account=META, now_utc=NOW)
    assert raw["extra"]["budget"] is None and raw["extra"]["changes"] is None and len(raw["extra"]["campaigns"]) == 15 and len(log) == 3
    a = an.analyse_raw(raw)
    assert [f for f in a["findings"] if f["kind"] == "spike"][0]["cause"]["kind"] == "one" and raw["platform"] == "meta" and raw["currency"] == "EUR"


# ── The report and its cache ────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    monkeypatch.setattr(paid_store, "get_gads_entry", lambda key, ttl: (saved[key], "2026-10-22T19:00:00+00:00") if key in saved else None, raising=False)
    an.clear_cache()
    base.reset_state()
    yield saved
    an.clear_cache()


def report(search, account_id="1234567890", **kw):
    return an.report_for(account_id, accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [], search_fn=search, now_utc=NOW, clock=kw.pop("clock", 1000.0), **kw)


def test_the_report_names_the_account_the_status_and_what_it_found(cache):
    rep = report(searcher({ACCT["id"]: hourly_rows(boost={9: 60, 10: 60, 11: 60})}))
    a = rep["accounts"][0]
    assert rep["account"] == {"id": "1234567890", "name": "Acme Dental", "currency": "USD", "platform": "google", "isGroup": False, "accountCount": 0}
    assert a["status"] == "over" and a["level"] in ("medium", "high") and a["id"] == ACCT["id"] and a["tz"] == TZ and a["local"] == "2026-10-22 12:00" and a["hourly"] is True and a["readAt"]
    assert rep["summary"]["counts"] == {"over": 1, "under": 0, "ok": 0, "new": 0, "none": 0} and rep["summary"]["needsLook"] == 1 and rep["warnings"] == [] and rep["sensitivity"] == "normal"
    assert rep["summary"]["overall"] == {"cost": a["today"]["cost"], "usual": a["today"]["usualByNow"], "accounts": 1, "currency": "USD"}
    assert [s["kind"] for s in a["plan"]["steps"]] == ["surge", "spike", "recheck"] and a["plan"]["steps"][0]["evidence"] == a["findings"][0]["detail"]


def test_a_second_ask_never_reads_again_and_refresh_does_but_not_twice_in_two_minutes(cache):
    log = []
    s = searcher(log=log)
    report(s)
    n = len(log)
    report(s, clock=1100.0)
    assert len(log) == n
    report(s, clock=1000.0 + an.CACHE_TTL_S + 1)
    assert len(log) == n + 1
    report(s, force=True)
    assert len(log) == n + 2
    with pytest.raises(base.RefreshTooSoon):
        report(s, force=True)


def test_what_the_team_saved_is_applied_to_the_saved_read_without_reading_again(cache):
    log, mon = [], MemStore()
    s = searcher(log=log)
    rep = report(s, mon=mon)
    assert rep["accounts"][0]["status"] == "ok"
    n = len(log)
    st.save_account(mon, ACCT["id"], {"maxDaily": 100}, "me@x.co", today=TODAY)
    rep = report(s, mon=mon)
    a = rep["accounts"][0]
    assert len(log) == n and a["status"] == "over" and [f["kind"] for f in a["findings"] if f["day"] == "today"] == ["ceiling"] and a["saved"]["maxDaily"] == 100.0
    st.set_sensitivity(mon, "high", "me@x.co")
    assert report(s, mon=mon)["sensitivity"] == "high" and len(log) == n


def test_unreadable_settings_are_a_warning_and_the_defaults_apply(cache):
    class Broken(MemStore):
        def state_get(self, key, default=None):
            raise RuntimeError("db down")
    rep = report(searcher(), mon=Broken())
    assert rep["warnings"][0].startswith("What the team saved") and "db down" in rep["warnings"][0] and rep["saved"]["teamSettings"] is False and rep["accounts"][0]["status"] == "ok"


def test_an_mcc_report_puts_the_accounts_that_need_a_look_first_and_names_an_unreadable_one(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"], "3333333333"]}
    third = {"id": "3333333333", "name": "Gamma Co", "currency": "USD"}
    rows = {ACCT["id"]: hourly_rows(), ACCT2["id"]: hourly_rows(boost={9: 60, 10: 60, 11: 60})}
    rep = an.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2, third], groups_fn=lambda: [group], search_fn=searcher(rows, fail=("3333333333",)), now_utc=NOW, clock=1000.0)
    assert [a["name"] for a in rep["accounts"]] == ["Beta Clinic", "Acme Dental"] and rep["account"]["isGroup"] is True and rep["account"]["accountCount"] == 3
    assert rep["warnings"] == ["Gamma Co could not be read: Account 3333333333 cannot be read."] and rep["summary"]["counts"]["over"] == 1


def test_every_account_failing_is_raised_and_a_spent_quota_is_a_quota_error(cache):
    with pytest.raises(gads.GadsError) as e:
        report(searcher(fail=(ACCT["id"],)))
    assert e.value.kind == "api"

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError) as e:
        report(spent)
    assert e.value.kind == "quota"


def test_an_account_that_is_not_position2s_is_refused(cache):
    with pytest.raises(an.BadRequest, match="not one of Position2"):
        report(searcher(), account_id="9999999999")


def test_a_meta_account_report(cache):
    rep = an.report_for("act_5555", search_fn=searcher(), meta_pages_fn=meta_pages(boost={9: 60, 10: 60, 11: 60}), meta_account_fn=lambda act: dict(META), now_utc=NOW, clock=1000.0)
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["platform"] == "meta" and a["currency"] == "EUR" and a["status"] == "over" and rep["summary"]["overall"]["currency"] == "EUR"


def test_a_meta_failure_for_a_lone_account_is_raised(cache):
    def broken(path, params):
        raise RuntimeError("Meta is down")
    with pytest.raises(RuntimeError, match="Meta is down"):
        an.report_for("act_5555", meta_pages_fn=broken, meta_account_fn=lambda act: dict(META), now_utc=NOW, clock=1000.0)


def test_an_unexpected_failure_of_one_account_in_a_group_is_named_and_the_others_are_kept(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    good = searcher({ACCT["id"]: hourly_rows(), ACCT2["id"]: hourly_rows()})

    def run(cid, q):
        if cid == ACCT2["id"]:
            raise ValueError("odd answer")
        return good(cid, q)
    rep = an.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=run, now_utc=NOW, clock=1000.0)
    assert [a["name"] for a in rep["accounts"]] == ["Acme Dental"] and rep["warnings"] == ["Beta Clinic could not be read: ValueError: odd answer"]


def test_the_overall_total_needs_one_currency_and_a_usual():
    a = {"today": {"cost": 100.0, "usualByNow": 80.0}, "currency": "USD"}
    b = {"today": {"cost": 50.0, "usualByNow": None}, "currency": "USD"}
    c = {"today": {"cost": 10.0, "usualByNow": 10.0}, "currency": "EUR"}
    assert an.overall([a, b]) == {"cost": 100.0, "usual": 80.0, "accounts": 1, "currency": "USD"}
    assert an.overall([a, c]) is None and an.overall([b]) is None and an.overall([]) is None


# ── the edges of the changes, the hours and the extras ──────────────────────

def test_a_budget_set_to_the_same_amount_is_not_called_raised():
    same = {"campaignBudget": {"amountMicros": "50000000"}}
    out, _ = an.parse_changes([change("2026-10-22 09:15:00", "ann@x.com", old=same, new=same)], LOCAL)
    assert out[0]["label"] == "a budget amount changed"


def test_a_campaign_edit_that_is_not_status_or_bidding_is_not_named():
    rows = [change("2026-10-22 09:15:00", "ann@x.com", kind="CAMPAIGN", fields="name"), change("2026-10-22 09:16:00", "ann@x.com", kind="CAMPAIGN", fields="targetCpa"),
            change("2026-10-22 09:17:00", "ann@x.com", kind="CAMPAIGN", fields="status")]
    out, _ = an.parse_changes(rows, LOCAL)
    assert [c["label"] for c in out] == ["a campaign switched on or off", "a campaign's bidding changed"]


def test_a_change_is_recent_for_forty_eight_hours():
    out, _ = an.parse_changes([change("2026-10-20 12:00:01", "ann@x.com"), change("2026-10-20 11:59:59", "bob@x.com"), change("2026-10-21 12:00:00", "cy@x.com")], LOCAL)
    assert [c["user"] for c in out] == ["cy@x.com", "ann@x.com"]
    assert [c["newUser"] for c in out] == [True, True]                    # bob's change was just before the window: it is not listed, and it makes nobody else's new or known


def test_a_history_cut_at_the_limit_is_truncated_even_at_exactly_the_limit():
    rows = [change("2026-10-22 09:15:00", "zed@y.com")] + [change("2026-10-22 08:00:00", "ann@x.com")] * (an.CHANGES_LIMIT - 1)
    out, truncated = an.parse_changes(rows, LOCAL)
    assert len(rows) == an.CHANGES_LIMIT and truncated is True and all(c["newUser"] is False for c in out)
    out, truncated = an.parse_changes(rows[1:], LOCAL)
    assert truncated is False


def test_meta_hour_rows_without_an_hour_are_not_hours():
    raw = an.read_meta("act_5555", lambda path, params: [{"date_start": "2026-10-21", "spend": "5"}], META, NOW)
    assert raw["hourly"] is False and raw["warnings"] == ["Meta gave no spend by hour, so only whole days are judged."]


def test_a_spent_quota_on_the_hours_is_not_asked_again_in_the_daily_form():
    log = []

    def spent(cid, q):
        log.append(q)
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        an.read_google(ACCT, spent, NOW)
    assert len(log) == 1


def test_a_muted_or_low_sensitivity_spike_does_not_ask_for_the_extras():
    big = {ACCT["id"]: hourly_rows(boost={9: 60, 10: 60, 11: 60})}
    mild = {ACCT["id"]: hourly_rows(boost={9: 35, 10: 35, 11: 35})}                 # the last three hours are 3.5x: a medium surge at normal, nothing at low
    for rows, saved, sens in ((big, {"mutedUntil": "2026-10-22"}, None), (mild, {}, "low")):
        log = []
        raw = an.read_account(dict(ACCT, platform="google"), search=searcher(rows, log=log), now_utc=NOW, saved=saved, sensitivity=sens)
        assert len(log) == 1 and raw["extra"] is None
    log = []
    raw = an.read_account(dict(ACCT, platform="google"), search=searcher(mild, log=log), now_utc=NOW)
    assert len(log) == 4 and raw["extra"] is not None
