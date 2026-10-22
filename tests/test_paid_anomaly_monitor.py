"""Spend Anomaly Alert: the run every few hours over every account (tracker/paid_anomaly_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests.test_paid_anomaly import ACCT, ACCT2, NOW, TZ, hourly_rows, meta_pages, searcher  # noqa: E402
from tests.test_paid_anomaly import camps, budgets  # noqa: E402
from tracker import paid_anomaly as an  # noqa: E402
from tracker import paid_anomaly_math as m  # noqa: E402
from tracker import paid_anomaly_monitor as am  # noqa: E402
from tracker import paid_anomaly_settings as st  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/spend-anomaly"
A = dict(ACCT, platform="google", group="g")
B = dict(ACCT2, platform="google", group="g")
LISTED = [dict(A, currency="USD"), dict(B, currency="USD")]
SPIKE = {9: 60, 10: 60, 11: 60}
MILD = {9: 35, 10: 35, 11: 35}


def spec(search, store=None, now=NOW, **kw):
    return am.make_spec(search_fn=search, list_fn=lambda: LISTED, mon_store=store, now_fn=lambda: now, **kw)


def go(store, deliver, search, accounts=(A,), now=NOW, **kw):
    return mon.run_monitor(store, spec(search, store, now, **kw), trigger="schedule", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=lambda: now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def by(*accounts, **boosts):
    return {a["id"]: hourly_rows(boost=boosts.get(a["id"])) for a in accounts}


# ── What an issue is ────────────────────────────────────────────────────────

def test_a_medium_or_high_finding_is_an_issue_with_a_fingerprint_of_account_kind_and_day():
    a = m.analyse([{"date": d, "hour": h, "cost": c} for d, h, c in _rows(SPIKE)], dt.date(2026, 10, 22), 12)
    issues = am.issues_of({"id": "1", "name": "Acme", "platform": "google"}, a)
    assert sorted((i["fingerprint"], i["level"], i["kind"]) for i in issues) == [("1:spike:2026-10-22", "medium", "spike"), ("1:surge:2026-10-22", "high", "surge")]
    spike = [i for i in issues if i["kind"] == "spike"][0]
    assert spike["agent"] == "spend" and spike["accountName"] == "Acme" and spike["title"].startswith("Spending 2.") and spike["nextStep"].startswith("Open the campaigns that spent most today")
    assert spike["data"]["spent"] == 270.0 and spike["data"]["usual"] == 120.0 and spike["data"]["today"] == 270.0 and spike["data"]["usualDay"] == 240.0 and spike["data"]["day"] == "today"


def _rows(boost):
    out = []
    for n in range(1, 16):
        d = (dt.date(2026, 10, 22) - dt.timedelta(days=n)).isoformat()
        out += [(d, h, 10.0) for h in range(24)]
    return out + [("2026-10-22", h, boost.get(h, 10.0)) for h in range(12)]


def test_low_and_muted_findings_are_not_issues_and_the_cause_is_kept():
    low = m.analyse([{"date": d, "hour": h, "cost": c} for d, h, c in _rows({h: 16.0 for h in range(12)})], dt.date(2026, 10, 22), 12)
    assert [f["level"] for f in low["findings"]] == ["low"] and am.issues_of({"id": "1", "name": "A", "platform": "google"}, low) == []
    muted = m.analyse([{"date": d, "hour": h, "cost": c} for d, h, c in _rows(SPIKE)], dt.date(2026, 10, 22), 12, saved={"mutedUntil": "2026-10-22"})
    assert am.issues_of({"id": "1", "name": "A", "platform": "google"}, muted) == []


# ── The run ─────────────────────────────────────────────────────────────────

def test_the_first_run_is_the_baseline_and_sends_nothing_even_when_a_spike_is_on():
    s, dv = configured(), Deliver()
    run = go(s, dv, searcher(by(A, **{A["id"]: SPIKE})))
    assert run["status"] == "done" and run["baseline"] is True and run["issues"] == 2 and run["new"] == 0 and not dv.emails and not dv.slacks and s.state_get("baseline:spend")
    assert {i["status"] for i in s.issues_all("spend")} == {"open"}


def test_a_spike_after_the_baseline_is_alerted_once_with_its_cause_and_the_changes():
    s, dv = configured(), Deliver()
    go(s, dv, searcher(by(A)))
    extra = {"campaigns": {A["id"]: camps()}, "budgets": {A["id"]: budgets()}, "changes": {A["id"]: []}}
    later = NOW + dt.timedelta(minutes=10)
    hot = searcher(by(A, **{A["id"]: SPIKE}), extra)
    r = go(s, dv, hot, now=later)
    assert r["new"] == 2 and r["alerted"] == 2 and len(dv.emails) == 1 and len(dv.slacks) == 1
    mail = dv.emails[0]
    assert mail["subject"] == "HIGH: Spend Anomaly Alert: 2 new (1 account)" and "Acme Dental" in mail["text"] and "\"Generic\" spent $520" in mail["text"] and mail["text"].endswith("Open the page: " + LINK)
    again = go(s, dv, hot, now=later + dt.timedelta(minutes=10))
    assert again["new"] == 0 and again["alerted"] == 0 and len(dv.emails) == 1                                  # still open, already alerted: nothing more


def test_a_spike_that_gets_worse_is_alerted_again_and_one_that_is_gone_is_fixed():
    s, dv = configured(), Deliver()
    go(s, dv, searcher(by(A)))
    go(s, dv, searcher(by(A, **{A["id"]: MILD})), now=NOW + dt.timedelta(minutes=10))
    assert [(i["kind"], i["level"]) for i in s.issues_all("spend")] == [("surge", "medium")] and len(dv.emails) == 1                    # a 3.5x surge, and a day only 1.6x
    go(s, dv, searcher(by(A, **{A["id"]: {9: 40, 10: 40, 11: 40}})), now=NOW + dt.timedelta(minutes=20))                          # 4x: high; the day is now 1.75x: a new medium spike
    assert sorted((i["kind"], i["level"]) for i in s.issues_all("spend") if i["status"] == "open") == [("spike", "medium"), ("surge", "high")] and len(dv.emails) == 2
    assert dv.emails[1]["text"].count("[WORSE THAN BEFORE]") == 1 and dv.emails[1]["text"].count("[NEW]") == 1 and "was medium when last alerted" in dv.emails[1]["text"]
    fixed = go(s, dv, searcher(by(A)), now=NOW + dt.timedelta(minutes=30))
    assert fixed["resolved"] == 2 and {i["status"] for i in s.issues_all("spend")} == {"resolved"}


def test_an_account_that_cannot_be_read_is_named_and_resolves_nothing():
    s, dv = configured(), Deliver()
    go(s, dv, searcher(by(A, B, **{A["id"]: SPIKE})), accounts=(A, B))
    run = go(s, dv, searcher(by(A, B), fail=(A["id"],)), accounts=(A, B), now=NOW + dt.timedelta(minutes=10))
    assert run["read"] == 1 and run["unreadableCount"] == 1 and run["unreadable"][0] == {"accountId": A["id"], "name": "Acme Dental", "platform": "google", "error": "Account 1234567890 cannot be read."}
    assert run["resolved"] == 0 and {i["status"] for i in s.issues_all("spend")} == {"open"}


def test_a_spent_quota_skips_the_other_google_accounts_but_not_meta():
    s, dv = configured(), Deliver()
    meta = {"id": "act_5555", "name": "Beta Meta", "platform": "meta", "group": ""}

    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    run = go(s, dv, spent, accounts=(A, B, meta), workers=1, meta_pages_fn=meta_pages(), meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR", "tz": TZ})
    assert run["read"] == 1 and run["unreadableCount"] == 2
    assert sorted(u["name"] for u in run["unreadable"]) == ["Acme Dental", "Beta Clinic"]
    assert sorted(u["error"] for u in run["unreadable"]) == ["The Google Ads daily read budget was used up earlier in this run.", "The daily limit is used up."]
    assert s.state_get("baseline:spend") and {u["platform"] for u in run["unreadable"]} == {"google"}


def test_a_meta_account_is_read_and_a_spike_is_alerted():
    s, dv = configured(), Deliver()
    meta = {"id": "act_5555", "name": "Beta Meta", "platform": "meta", "group": ""}
    info = lambda act: {"name": "Beta Meta", "currency": "EUR", "tz": TZ}      # noqa: E731
    go(s, dv, searcher(), accounts=(meta,), meta_pages_fn=meta_pages(), meta_account_fn=info)
    r = go(s, dv, searcher(), accounts=(meta,), now=NOW + dt.timedelta(minutes=10), meta_pages_fn=meta_pages(boost=SPIKE), meta_account_fn=info)
    assert r["new"] == 2 and dv.emails[0]["text"].count("(Meta Ads)") == 2 and "€" in dv.emails[0]["text"]


def test_what_the_team_saved_is_applied_a_muted_push_makes_no_issue_and_a_ceiling_does():
    s, dv = configured(), Deliver()
    go(s, dv, searcher(by(A)))
    st.save_account(s, A["id"], {"mutedUntil": "2026-10-22", "maxDaily": 100}, "me@x.co", today=dt.date(2026, 10, 22))
    r = go(s, dv, searcher(by(A, **{A["id"]: SPIKE})), now=NOW + dt.timedelta(minutes=10))
    kinds = sorted(i["kind"] for i in s.issues_all("spend") if i["status"] == "open")
    assert kinds == ["ceiling", "ceiling"] and r["new"] == 2                                                   # today and yesterday are over a ceiling of 100; the spike is muted


def test_the_sensitivity_saved_is_applied():
    s, dv = configured(), Deliver()
    st.set_sensitivity(s, "low", "me@x.co")
    go(s, dv, searcher(by(A)))
    r = go(s, dv, searcher(by(A, **{A["id"]: MILD})), now=NOW + dt.timedelta(minutes=10))
    assert r["issues"] == 0 and r["new"] == 0


def test_unreadable_settings_do_not_stop_the_run():
    class Broken(MemStore):
        def state_get(self, key, default=None):
            if key == st.KEY:
                raise RuntimeError("db down")
            return super().state_get(key, default)
    s, dv = Broken(), Deliver()
    run = go(s, dv, searcher(by(A, **{A["id"]: SPIKE})))
    assert run["status"] == "done" and run["issues"] == 2


def test_the_days_figures_are_kept_for_the_history():
    s, dv = configured(), Deliver()
    go(s, dv, searcher(by(A, **{A["id"]: SPIKE})))
    snap = s.snaps_get("spend", A["id"], "2026-10-01")
    assert len(snap) == 1 and snap[0]["day"] == "2026-10-22" and snap[0]["status"] == "over" and snap[0]["today"] == 270.0 and snap[0]["usualByNow"] == 120.0 and snap[0]["usualDay"] == 240.0


def test_each_run_reads_fresh_and_a_healthy_account_costs_one_read():
    log = []
    s, dv = configured(), Deliver()
    go(s, dv, searcher(by(A, B), log=log), accounts=(A, B))
    assert len(log) == 2
    go(s, dv, searcher(by(A, B), log=log), accounts=(A, B), now=NOW + dt.timedelta(minutes=10))
    assert len(log) == 4


def test_the_account_currency_comes_from_the_account_list():
    s, dv = configured(), Deliver()
    eur = [dict(A, currency="EUR")]
    sp = am.make_spec(search_fn=searcher(by(A, **{A["id"]: SPIKE})), list_fn=lambda: eur, mon_store=s, now_fn=lambda: NOW)
    mon.run_monitor(s, sp, trigger="schedule", link=LINK, deliver=dv, accounts=[A], groups=[], now_fn=lambda: NOW)
    assert "€" in s.issues_all("spend")[0]["detail"]
