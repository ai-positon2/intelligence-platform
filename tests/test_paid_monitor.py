"""The monitor engine: issues, what is new, worse, still open and fixed, the baseline, the alert and the run (tracker/paid_monitor.py). No network."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/brand-spend"
A = {"id": "111", "name": "Acme <Dental>", "platform": "google"}
B = {"id": "222", "name": "Beta Clinic", "platform": "google"}
T0 = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)


class World:
    """What a monitor finds today: set `issues` and `unreadable` before each run."""

    def __init__(self):
        self.issues, self.unreadable, self.snaps, self.boom = [], set(), {}, False

    def spec(self):
        def collect(accounts, groups):
            if self.boom:
                raise RuntimeError("Google is down")
            ok = {a["id"] for a in accounts if a["id"] not in self.unreadable}
            return {"issues": [i for i in self.issues if i["accountId"] in ok], "snaps": self.snaps, "okIds": ok,
                    "unreadable": [{"accountId": a["id"], "name": a["name"], "error": "no"} for a in accounts if a["id"] in self.unreadable]}
        return {"agent": "brandspend", "label": "Brand Spend Alert", "collect": collect}


def issue(fp="over:111", acct=A, level="medium", title="Brand spend is $301, above the usual $203", detail="48% above a usual week.", nxt="Start with: Find out why each click costs more.", kind="over"):
    return mon.make_issue("brandspend", fp, acct, kind, level, title, detail, nxt, {"week": "2026-10-12"})


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def run(store, world, deliver, at, trigger="test"):
    return mon.run_monitor(store, world.spec(), trigger=trigger, link=LINK, deliver=deliver, accounts=[A, B], groups=[], now_fn=lambda: at)


def day(n, hour=7):
    return T0 + timedelta(days=n, hours=hour - 7)


# ── The baseline ────────────────────────────────────────────────────────────

def test_the_first_run_sets_the_baseline_alerts_nothing_and_marks_what_it_found_as_seen():
    s, dv, w = configured(), Deliver(), World()
    w.issues = [issue(), issue("over:222", B, "high", "Brand spend stopped")]
    r = run(s, w, dv, day(0))
    assert r["status"] == "done" and r["alerted"] == 0 and r["new"] == 0 and r["baseline"] is True and r["issues"] == 2 and not dv.emails and not dv.slacks
    assert s.state_get("baseline:brandspend")["by"] == "test"
    rows = s.issues_all("brandspend")
    assert len(rows) == 2 and all(x["alertedAt"] and x["alert"] == {"baseline": True} and x["alertedLevel"] == x["level"] for x in rows)
    assert run(s, w, dv, day(1))["alerted"] == 0 and not dv.emails                    # the same problems the next day are not news


def test_an_issue_that_turns_up_after_the_baseline_is_alerted_once_by_email_and_slack():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue()]
    r = run(s, w, dv, day(1))
    assert r["new"] == 1 and r["alerted"] == 1 and len(dv.emails) == 1 and len(dv.slacks) == 1 and dv.emails[0]["to"] == ["ppc@example.com"] and dv.slacks[0]["channel"] == "#alerts"
    assert dv.emails[0]["subject"] == "Brand Spend Alert: 1 new (1 account)"
    [row] = s.issues_all("brandspend")
    assert row["alertedAt"].startswith("2026-10-21T07:00") and row["alertedLevel"] == "medium" and row["alert"] == {"email": "sent", "slack": "sent"}
    r2 = run(s, w, dv, day(2))
    assert r2["alerted"] == 0 and len(dv.emails) == 1                                  # the same issue the next day is not alerted again


def test_a_low_issue_is_kept_and_shown_but_never_alerted():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue("l:1", level="low", title="A small thing")]
    r = run(s, w, dv, day(1))
    assert r["new"] == 0 and r["alerted"] == 0 and not dv.emails and s.issues_all("brandspend")[0]["level"] == "low" and s.issues_all("brandspend")[0]["alertedAt"] is None


# ── Worse, still open, fixed and back ───────────────────────────────────────

def test_an_issue_that_gets_worse_is_alerted_again_and_says_what_it_was():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue()]
    run(s, w, dv, day(1))
    w.issues = [issue(level="high", title="Brand spend is $450")]
    r = run(s, w, dv, day(3))
    assert r["worse"] == 1 and r["new"] == 0 and r["alerted"] == 1 and len(dv.emails) == 2
    assert dv.emails[1]["subject"] == "HIGH: Brand Spend Alert: 1 worse (1 account)" and "[WORSE THAN BEFORE]" in dv.emails[1]["text"]
    assert "was medium when last alerted" in dv.emails[1]["text"] and "open 2 days" in dv.emails[1]["text"]
    assert run(s, w, dv, day(4))["alerted"] == 0                                         # high now; not worse again


def test_a_high_issue_still_open_a_week_after_its_alert_is_a_reminder_and_a_medium_one_is_not():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue("h:1", level="high", title="Stopped"), issue("m:1", level="medium", title="Over")]
    run(s, w, dv, day(1))
    assert len(dv.emails) == 1
    assert run(s, w, dv, day(7))["alerted"] == 0                                          # 6 days after the alert
    r = run(s, w, dv, day(8))
    assert r["reminders"] == 1 and r["alerted"] == 1 and "[STILL OPEN] Acme <Dental> (Google Ads)\n    Stopped" in dv.emails[1]["text"] and "Over" not in dv.emails[1]["text"].split("Stopped")[1].split("Open the page")[0]
    assert run(s, w, dv, day(9))["alerted"] == 0                                          # the reminder reset the clock


def test_an_issue_that_is_gone_from_an_account_that_was_read_is_resolved_and_the_next_alert_says_so():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue("a:1", title="First"), issue("b:1", B, title="Second")]
    run(s, w, dv, day(1))
    w.issues = [issue("b:1", B, title="Second"), issue("c:1", title="Third")]
    r = run(s, w, dv, day(4))
    assert r["resolved"] == 1 and r["new"] == 1
    assert "Fixed since the last run:\n- Acme <Dental>: First (was open 3 days)" in dv.emails[-1]["text"]
    states = {x["fingerprint"]: x["status"] for x in s.issues_all("brandspend")}
    assert states == {"a:1": "resolved", "b:1": "open", "c:1": "open"}
    assert [x for x in s.issues_all("brandspend") if x["fingerprint"] == "a:1"][0]["resolvedAt"].startswith("2026-10-24")
    again = run(s, w, dv, day(6))
    assert again["resolved"] == 0 and [x for x in s.issues_all("brandspend") if x["fingerprint"] == "a:1"][0]["resolvedAt"].startswith("2026-10-24")       # resolved once, not every run


def test_an_account_that_could_not_be_read_never_resolves_its_issues_and_is_named_on_the_run():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue()]
    run(s, w, dv, day(1))
    w.issues, w.unreadable = [], {A["id"]}
    r = run(s, w, dv, day(2))
    assert r["resolved"] == 0 and r["unreadableCount"] == 1 and r["unreadable"][0]["accountId"] == "111" and r["read"] == 1
    assert s.issues_all("brandspend")[0]["status"] == "open"
    w.unreadable = set()
    assert run(s, w, dv, day(3))["resolved"] == 1                                         # read again and gone: now it is resolved


def test_a_problem_that_comes_back_is_new_again_counts_from_the_day_it_came_back_and_says_how_often():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue()]
    run(s, w, dv, day(1))
    w.issues = []
    run(s, w, dv, day(5))
    w.issues = [issue()]
    r = run(s, w, dv, day(9))
    assert r["new"] == 1 and r["alerted"] == 1
    [row] = s.issues_all("brandspend")
    assert row["reopened"] == 1 and row["status"] == "open" and row["firstSeen"].startswith("2026-10-29") and row["resolvedAt"] is None
    assert "has come back 1 time" in dv.emails[-1]["text"] and "open" not in dv.emails[-1]["text"].split("has come back")[0].split("\n")[-1]


# ── Alerts that go nowhere ──────────────────────────────────────────────────

def test_with_no_recipient_nothing_is_marked_alerted_and_the_issue_is_tried_again_when_one_is_set():
    s, dv, w = MemStore(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue()]
    r = run(s, w, dv, day(1))
    assert r["alerted"] == 0 and r["email"]["status"] == "not_configured" and r["slack"]["status"] == "not_configured" and s.issues_all("brandspend")[0]["alertedAt"] is None
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": ""})
    r2 = run(s, w, dv, day(2))
    assert r2["alerted"] == 1 and r2["new"] == 1 and len(dv.emails) == 1 and dv.slacks == [] and r2["slack"]["status"] == "not_configured"


def test_one_channel_taking_the_alert_is_enough_and_a_refusal_is_said():
    s, dv, w = configured(), Deliver(fail=("email",)), World()
    run(s, w, dv, day(0))
    w.issues = [issue()]
    r = run(s, w, dv, day(1))
    assert r["alerted"] == 1 and r["email"]["status"] == "failed" and "smtp down" in r["email"]["error"] and r["slack"]["status"] == "sent"
    s2, dv2, w2 = configured(), Deliver(fail=("email", "slack")), World()
    run(s2, w2, dv2, day(0))
    w2.issues = [issue()]
    r2 = run(s2, w2, dv2, day(1))
    assert r2["alerted"] == 0 and s2.issues_all("brandspend")[0]["alertedAt"] is None
    dv2.fail = set()
    assert run(s2, w2, dv2, day(2))["alerted"] == 1


# ── The run ─────────────────────────────────────────────────────────────────

def test_two_runs_never_go_at_once_and_a_failed_run_is_recorded_and_frees_the_lock():
    s, dv, w = configured(), Deliver(), World()
    s.claim("lock:brandspend", 1800)
    assert run(s, w, dv, day(0)) == {"status": "busy"}
    s.release("lock:brandspend")
    w.boom = True
    r = run(s, w, dv, day(0))
    assert r["status"] == "failed" and "RuntimeError: Google is down" in r["error"] and s.state_get("runs:brandspend")[0]["status"] == "failed"
    w.boom = False
    assert run(s, w, dv, day(1))["status"] == "done"


def test_the_runs_are_kept_newest_first_and_twenty_at_most():
    s, dv, w = configured(), Deliver(), World()
    for n in range(23):
        run(s, w, dv, day(n), trigger="t%d" % n)
    runs = s.state_get("runs:brandspend")
    assert len(runs) == 20 and runs[0]["trigger"] == "t22" and runs[-1]["trigger"] == "t3"


def test_the_days_figures_are_saved_for_the_history_and_old_days_forgotten():
    s, dv, w = configured(), Deliver(), World()
    w.snaps = {"111": {"status": "over", "cost": 301}}
    s.snaps_put("brandspend", "2026-01-01", {"111": {"status": "ok"}})
    run(s, w, dv, day(0))
    run(s, w, dv, day(1))
    got = s.snaps_get("brandspend", "111", "2000-01-01")
    assert [g["day"] for g in got] == ["2026-10-20", "2026-10-21"] and got[0]["status"] == "over"


# ── The alert's words ───────────────────────────────────────────────────────

def pend(n, level="medium", event="new"):
    return [dict(issue("i:%d" % i, level=level, title="Issue %d" % i), event=event, ageDays=0, reopened=0) for i in range(n)]


def test_the_alert_lists_twelve_and_points_to_the_page_for_the_rest():
    m = mon.digest("Brand Spend Alert", pend(15), [], LINK)
    assert m["subject"] == "Brand Spend Alert: 15 new (1 account)" and "...and 3 more on the page." in m["text"] and m["text"].count("[NEW]") == 12 and "Open the page: " + LINK in m["text"]
    assert m["slack_text"] == "*Brand Spend Alert: 15 new (1 account)*" and m["slack_blocks"][0]["type"] == "header" and len(m["slack_blocks"]) == 14 and "...and 3 more." in m["slack_blocks"][-1]["text"]["text"]


def test_a_high_issue_puts_high_first_in_the_subject_and_markup_in_names_is_escaped_in_the_html():
    m = mon.digest("Disapproved Ads Tracker", pend(1, "high") + pend(2, "medium", "worse"), [], LINK)
    assert m["subject"] == "HIGH: Disapproved Ads Tracker: 1 new, 2 worse (1 account)"
    assert "Acme &lt;Dental&gt;" in m["html"] and "<Dental>" not in m["html"] and "href=\"%s\"" % LINK in m["html"]
    assert "Next: Start with" in m["text"] and "_Next: Start with" in m["slack_blocks"][1]["text"]["text"]


def test_the_alert_lists_new_before_worse_before_still_open_then_the_highest_level_then_the_account():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, day(0))
    w.issues = [issue("a", B, "medium", "Zed medium"), issue("b", A, "medium", "Alpha medium"), issue("c", B, "high", "Beta high"), issue("d", A, "medium", "Old one")]
    run(s, w, dv, day(1))
    w.issues = [issue("a", B, "medium", "Zed medium"), issue("b", A, "medium", "Alpha medium"), issue("c", B, "high", "Beta high"), issue("d", A, "high", "Old one gets worse"),
                issue("e", B, "medium", "Aaa new beta"), issue("f", A, "medium", "Zzz new alpha"), issue("g", A, "high", "New high")]
    run(s, w, dv, day(8))                                                    # a week on: the high ones that were alerted a week ago are reminders too
    text = dv.emails[-1]["text"]
    order = [text.index(x) for x in ("New high", "Zzz new alpha", "Aaa new beta", "Old one gets worse", "Beta high")]
    assert order == sorted(order) and text.index("[NEW]") < text.index("[WORSE THAN BEFORE]") < text.index("[STILL OPEN]")


def test_the_subject_counts_accounts_and_the_three_kinds_of_news():
    p = pend(1) + [dict(issue("w:1", B), event="worse", ageDays=2, reopened=0, alertedLevel="medium")] + [dict(issue("r:1", B, "high"), event="reminder", ageDays=9, reopened=2)]
    m = mon.digest("X", p, [], LINK)
    assert m["subject"] == "HIGH: X: 1 new, 1 worse, 1 still open (2 accounts)" and "open 9 days · has come back 2 times" in m["text"]


# ── Settings and ages ───────────────────────────────────────────────────────

def test_the_recipients_are_checked_and_saved():
    s = MemStore()
    assert mon.save_alert_settings(s, {"emails": " A@x.co, b@y.co ; a@x.co", "slackChannel": "#ppc-alerts"}) == {"emails": ["a@x.co", "b@y.co"], "slackChannel": "#ppc-alerts"}
    assert mon.alert_settings(s) == {"emails": ["a@x.co", "b@y.co"], "slackChannel": "#ppc-alerts"} and mon.alert_settings(MemStore()) == {"emails": [], "slackChannel": ""}
    for bad, why in (({"emails": "nope"}, "nope is not an email address."), ({"emails": ["a@x.co"] * 1 + ["b%d@x.co" % i for i in range(10)]}, "At most 10 email addresses."), ({"slackChannel": "two words"}, "is not a Slack channel")):
        with pytest.raises(mon.BadRequest, match=why):
            mon.clean_alert_settings(bad)
    assert mon.clean_alert_settings({}) == {"emails": [], "slackChannel": ""} and mon.clean_alert_settings(None) == {"emails": [], "slackChannel": ""}


def test_the_test_alert_goes_through_the_real_senders_and_says_it_is_a_test():
    s, dv = configured(), Deliver()
    res = mon.test_alert(s, "Brand Spend Alert", LINK, dv)
    assert res["sent"] is True and dv.emails[0]["subject"].startswith("TEST: ") and dv.slacks[0]["text"].startswith("TEST ") and "Test account (not real)" in dv.emails[0]["text"]
    assert mon.test_alert(MemStore(), "X", LINK, dv)["sent"] is False


@pytest.mark.parametrize("since, want", [("2026-10-20T07:00:00+00:00", 3), ("2026-10-23T06:59:00+00:00", 0), ("2026-10-22T07:00:00+00:00", 1), (None, None), ("garbage", None), ("2026-10-30T00:00:00+00:00", 0)])
def test_the_age_of_an_issue_is_whole_days_never_negative(since, want):
    assert mon.age_days(since, datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc)) == want


# ── The weekly summary ──────────────────────────────────────────────────────

def stored(fp, level="medium", status="open", first="2026-10-10T07:00:00+00:00", resolved=None, acct="Acme <Dental>", title="Brand spend is $301"):
    return {"fingerprint": fp, "level": level, "status": status, "firstSeen": first, "resolvedAt": resolved, "accountName": acct, "title": title}


def test_the_weekly_summary_lists_every_open_issue_highest_first_and_what_was_fixed_this_week():
    now = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
    issues = [stored("a", "low", title="A small thing"), stored("b", "high", title="Stopped", acct="Beta"), stored("c", "medium", title="Over", acct="Acme <Dental>"),
              stored("d", status="resolved", resolved="2026-10-18T07:00:00+00:00", title="Was over", first="2026-10-14T07:00:00+00:00"),
              stored("e", status="resolved", resolved="2026-10-01T07:00:00+00:00", title="Fixed long ago"),
              stored("g", status="resolved", resolved="2026-10-12T19:00:00+00:00", title="Fixed seven and a half days ago")]
    m = mon.weekly_digest("Brand Spend Alert", issues, now, LINK)
    assert m["subject"] == "Brand Spend Alert: weekly summary, 3 open (1 high), 1 fixed this week" and m["open"] == 3 and m["fixed"] == 1
    lines = m["text"].split("\n")
    assert lines[0] == "3 open, 1 of them high; 1 fixed in the last 7 days."
    assert lines[2:6] == ["Open:", "- [HIGH] Beta: Stopped (open 10 days)", "- [MEDIUM] Acme <Dental>: Over (open 10 days)", "- [LOW] Acme <Dental>: A small thing (open 10 days)"]
    assert "Fixed this week:\n- Acme <Dental>: Was over (was open 4 days)" in m["text"] and "Fixed long ago" not in m["text"] and "seven and a half days ago" not in m["text"] and m["text"].endswith("Open the page: " + LINK)
    assert "Acme &lt;Dental&gt;" in m["html"] and "<Dental>" not in m["html"] and m["slack_text"] == "*%s*" % m["subject"] and m["slack_blocks"][0]["type"] == "header" and "*Open*" in m["slack_blocks"][1]["text"]["text"]


def test_the_weekly_summary_caps_its_lists_and_says_how_many_more_there_are():
    now = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
    issues = [stored("i%d" % n, title="Issue %d" % n) for n in range(20)] + [stored("f%d" % n, status="resolved", resolved="2026-10-19T07:00:00+00:00", title="Fixed %d" % n) for n in range(12)]
    m = mon.weekly_digest("X", issues, now, LINK)
    assert "...and 5 more on the page." in m["text"] and m["text"].count("\n- [MEDIUM]") == 15 and m["text"].split("Fixed this week:")[1].count("\n- Acme") == 10 and m["subject"] == "X: weekly summary, 20 open, 12 fixed this week"


def test_a_quiet_week_has_no_summary_and_a_week_with_only_fixes_has_one():
    now = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
    assert mon.weekly_digest("X", [], now, LINK) is None and mon.weekly_digest("X", [stored("e", status="resolved", resolved="2026-10-01T07:00:00+00:00")], now, LINK) is None
    only = mon.weekly_digest("X", [stored("d", status="resolved", resolved="2026-10-18T07:00:00+00:00")], now, LINK)
    assert only["subject"] == "X: weekly summary, 0 open, 1 fixed this week" and "Open:" not in only["text"] and "Fixed this week:" in only["text"]
    assert mon.weekly_digest("X", [stored("d", status="resolved", resolved=None)], now, LINK) is None                 # a resolved issue with no time is not counted as fixed this week


def test_the_weekly_summary_goes_through_the_same_senders():
    s, dv = configured(), Deliver()
    now = datetime(2026, 10, 20, 7, 0, tzinfo=timezone.utc)
    m = mon.weekly_digest("X", [stored("a")], now, LINK)
    assert mon.send(s, m, dv)["sent"] is True and dv.emails[0]["subject"] == m["subject"] and dv.slacks[0]["channel"] == "#alerts"
