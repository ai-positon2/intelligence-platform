"""Account Watch: the daily run over every account (tracker/paid_watch_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_watch as T  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402
from tracker import paid_watch_math as math  # noqa: E402
from tracker import paid_watch_monitor as wm  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/account-watch"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="USD")]
DAY = dt.date(2026, 10, 26)


def d(n):
    return DAY + dt.timedelta(days=n)


class World:
    """What Google and Meta answer: the manager's list, each account's users, goals and changes (all settable between runs)."""

    def __init__(self):
        self.status = {A["id"]: "ENABLED", B["id"]: "ENABLED"}
        self.extra = []                                                              # more clients of the manager account (suspended ones the platform's list leaves out)
        self.users = {A["id"]: [T.user_row("ana@position2.com")], B["id"]: [T.user_row("ana@position2.com")]}
        self.changes = {A["id"]: [], B["id"]: []}
        self.fail = set()
        self.quota = False
        self.meta_status = {"account_status": 1, "disable_reason": 0, "spend_cap": "0", "amount_spent": "9900", "currency": "USD"}
        self.pages = [{"id": "10", "name": "Acme", "is_published": True}]
        self.activities = []

    def clients(self):
        return [T.client_row(c, "Acme Dental" if c == A["id"] else "Beta Clinic", s) for c, s in self.status.items()] + self.extra

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        if "FROM customer_client" in q:
            return self.clients()
        return T.searcher(clients=[], users=self.users.get(cid, []), changes=self.changes.get(cid, []), fail=self.fail)(cid, q)

    def get(self, path, params):
        return self.meta_status

    def pages_fn(self, path, params):
        if path.endswith("/promote_pages"):
            return self.pages
        return self.activities


def run(store, world, deliver, day=DAY, accounts=(A, B), hour=7, workers=4):
    now = lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc)                    # noqa: E731
    spec = wm.make_spec(today=day, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, meta_pages_fn=world.pages_fn, meta_account_fn=lambda act: {"name": "Meta Co", "currency": "USD"},
                        meta_get_fn=world.get, now_fn=now, workers=workers)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def fnd(rule, key, level, title="t"):
    return {"rule": rule, "key": key, "level": level, "area": "x", "title": title, "detail": "d", "fix": "Do the thing.", "evidence": ["e"]}


# ── What becomes an issue ───────────────────────────────────────────────────

def test_a_high_or_medium_finding_is_one_issue_each_with_the_fix_as_the_next_step_and_a_low_one_is_quiet():
    out = wm.issues_of(A, {"findings": [fnd("terms", "t", "low", "A small thing"), fnd("access_change", "access:x", "medium"), fnd("account_status", "status", "high")]})
    assert [(i["fingerprint"], i["level"], i["kind"]) for i in out] == [("watch:1234567890:status", "high", "account_status"), ("watch:1234567890:access:x", "medium", "access_change"), ("watch:1234567890:small", "low", "small")]
    assert out[0]["nextStep"] == "Start with: Do the thing." and out[0]["agent"] == "watch" and out[2]["title"] == "1 small finding to check" and out[2]["detail"] == "A small thing."


def test_only_the_worst_hundred_findings_of_an_account_follow_one_by_one_and_the_rest_is_counted():
    many = [fnd("activity", "a:%d" % i, "medium") for i in range(103)] + [fnd("account_status", "status", "high")]
    out = wm.issues_of(A, {"findings": many})
    assert len(out) == 101 and out[0]["kind"] == "account_status" and out[-1]["kind"] == "more" and out[-1]["title"] == "4 more changes to check" and out[-1]["data"] == {"findings": 4}
    assert wm.issues_of(A, {"findings": []}) == []


# ── The daily run ───────────────────────────────────────────────────────────

def test_an_account_that_becomes_suspended_is_alerted_once_and_resolved_when_it_is_enabled_again():
    s, dv, w = configured(), Deliver(), World()
    r0 = run(s, w, dv, d(0))
    assert r0["baseline"] is True and r0["issues"] == 0 and not dv.emails and not dv.slacks
    w.status[B["id"]] = "SUSPENDED"
    r1 = run(s, w, dv, d(1))
    assert r1["new"] == 1 and r1["alerted"] == 1 and dv.emails[0]["subject"] == "HIGH: Account Watch: 1 new (1 account)" and "The account is suspended" in dv.emails[0]["text"] and "Beta Clinic" in dv.emails[0]["text"]
    assert dv.slacks[0]["blocks"][0]["text"]["text"] == "\U0001F534 Account Watch"
    assert run(s, w, dv, d(2))["alerted"] == 0 and len(dv.emails) == 1
    w.status[B["id"]] = "ENABLED"
    assert run(s, w, dv, d(3))["resolved"] == 1


def test_an_account_the_platforms_list_leaves_out_is_still_an_issue_and_one_that_vanished_stays_one_until_listed():
    s, dv, w = configured(), Deliver(), World()
    w.extra = [T.client_row("3333333333", "Gamma", "SUSPENDED"), T.client_row("5555555555", "Vanished Co")]
    run(s, w, dv, d(0))
    assert s.snaps_get("watch", "mcc", "2026-10-01")[0]["ids"]["5555555555"] == "Vanished Co"
    w.extra = [T.client_row("3333333333", "Gamma", "SUSPENDED")]
    r1 = run(s, w, dv, d(1))
    assert r1["new"] == 1 and "Vanished Co" in dv.emails[0]["text"] and "no longer in the manager account" in dv.emails[0]["text"]
    open_now = sorted(i["fingerprint"] for i in s.issues_all("watch") if i["status"] == "open")
    assert open_now == ["watch:3333333333:status", "watch:5555555555:gone"]                          # Gamma was in the baseline (quiet); Vanished Co is news
    r2 = run(s, w, dv, d(2))
    assert r2["alerted"] == 0 and sorted(i["fingerprint"] for i in s.issues_all("watch") if i["status"] == "open") == open_now
    w.extra = [T.client_row("3333333333", "Gamma", "ENABLED"), T.client_row("5555555555", "Vanished Co")]
    r3 = run(s, w, dv, d(3))
    assert r3["resolved"] == 2 and not [i for i in s.issues_all("watch") if i["status"] == "open"]


def test_a_new_administrator_is_alerted_from_the_second_day_and_a_budget_change_by_the_day_it_is_read():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0))
    w.users[A["id"]] = [T.user_row("ana@position2.com"), T.user_row("x@evil.com", "ADMIN")]
    at = (dt.datetime.combine(d(1), dt.time(7), tzinfo=dt.timezone.utc) - dt.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    w.changes[B["id"]] = [T.change_row(at=at, old="50000000", new="150000000")]
    r = run(s, w, dv, d(1))
    assert r["new"] == 2 and r["alerted"] == 2
    text = dv.emails[0]["text"]
    assert "Access changed: x@evil.com was added as admin" in text and "Budget change: a budget raised from $50.00 to $150 by ana@position2.com" in text and "Acme Dental" in text and "Beta Clinic" in text
    assert run(s, w, dv, d(2))["alerted"] == 0                                                        # the same ones are not alerted again


def test_a_meta_account_that_is_unsettled_is_alerted_and_its_pages_and_activity_are_read():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0), accounts=[A, B, M])
    w.meta_status = {"account_status": 3, "disable_reason": 0, "spend_cap": "0", "amount_spent": "9900", "currency": "USD"}
    w.pages = []
    w.activities = [{"event_type": "ad_account_add_user_to_role", "event_time": (dt.datetime.combine(d(1), dt.time(0), tzinfo=dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S+0000"), "actor_name": "jim@client.com"}]
    r = run(s, w, dv, d(1), accounts=[A, B, M])
    text = dv.emails[0]["text"]
    assert r["read"] == 3 and "The ad account is unsettled (a payment failed)" in text and "1 Page lost or unpublished" in text and "Ad account add user to role by jim@client.com" in text
    assert "jim@client.com outside Position2 made 1 change" in text


def test_a_spent_google_budget_does_not_stop_the_read_of_a_meta_account():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    r = run(s, w, dv, d(0), accounts=[A, B, M], workers=1)
    assert r["read"] == 1 and [u["accountId"] for u in r["unreadable"] if u["accountId"] != "mcc"] == [A["id"], B["id"]]
    assert any(u["accountId"] == "mcc" and u["error"] == "The daily limit is used up." for u in r["unreadable"])
    assert [u["error"] for u in r["unreadable"] if u["accountId"] == B["id"]][0].startswith("The Google Ads daily read budget was used up earlier")


def test_an_account_that_cannot_be_read_is_unreadable_and_what_is_open_there_is_not_resolved():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0))
    w.status[A["id"]] = "SUSPENDED"
    run(s, w, dv, d(1))
    w.fail = {A["id"]}
    r = run(s, w, dv, d(2))
    assert r["resolved"] == 0 and any(u["accountId"] == A["id"] for u in r["unreadable"])
    assert "watch:1234567890:status" in [i["fingerprint"] for i in s.issues_all("watch") if i["status"] == "open"]


def test_each_run_saves_who_has_access_and_the_goals_for_the_next_days_comparison_and_the_managers_list():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0))
    [snap] = s.snaps_get("watch", A["id"], "2026-10-01")
    assert snap["users"] == {"ana@position2.com": "ADMIN"} and snap["actions"]["11"]["name"] == "Form" and snap["status"] == "clean" and snap["findings"] == 0
    [m] = s.snaps_get("watch", "mcc", "2026-10-01")
    assert m["ids"] == {A["id"]: "Acme Dental", B["id"]: "Beta Clinic"} and m["gone"] == {}


def test_an_account_new_to_the_manager_account_is_quiet_on_its_first_read_and_one_that_was_there_and_is_suspended_is_news():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0), accounts=[A])
    w.extra = [T.client_row("3333333333", "Gamma", "SUSPENDED")]                         # an account added to the manager account after the baseline, already suspended
    r = run(s, w, dv, d(1), accounts=[A, B])
    assert r["firstRead"] == 1 and r["new"] == 0 and r["alerted"] == 0 and not dv.emails and any(i["fingerprint"] == "watch:3333333333:status" for i in s.issues_all("watch"))
    w.status[B["id"]] = "SUSPENDED"
    r2 = run(s, w, dv, d(2), accounts=[A, B])
    assert r2["new"] == 1 and "Beta Clinic" in dv.emails[0]["text"] and "Gamma" not in dv.emails[0]["text"]


def test_the_spec_names_the_monitor_and_its_page():
    spec = wm.make_spec(search_fn=lambda *a: [], list_fn=lambda: [])
    assert spec["agent"] == "watch" and spec["label"] == "Account Watch" and wm.PAGE == "/p2/paid-marketing/account-watch" and math.ORDER[0] == "account_status"
