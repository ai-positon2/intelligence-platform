"""Tracking & Data Health: the daily run over every account (tracker/paid_track_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_track as T  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402
from tracker import paid_track_math as math  # noqa: E402
from tracker import paid_track_monitor as tm  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/tracking-health"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="EUR")]
DAY = dt.date(2026, 10, 26)


def d(n):
    return DAY + dt.timedelta(days=n)


def shifted(rows, day):
    """T's rows are dated back from T.TODAY: move them so they are dated back from `day`."""
    shift = (day - T.TODAY).days
    out = []
    for r in rows:
        r = dict(r, segments=dict(r["segments"]))
        r["segments"]["date"] = (dt.date.fromisoformat(r["segments"]["date"]) + dt.timedelta(days=shift)).isoformat()
        out.append(r)
    return out


class World:
    """What Google and Meta answer, regenerated for the day of each run: `quiet_from` is how many whole days ago an account's conversions stopped (0: they have not)."""

    def __init__(self):
        self.day = DAY
        self.quiet = {A["id"]: 0, B["id"]: 0}
        self.list_size = {A["id"]: "9000", B["id"]: "9000"}
        self.fail, self.quota = set(), False
        self.fail_kinds, self.meta_fail = (), ()                                                    # the parts of the read that are refused (Google's, Meta's) while the account itself reads
        self.pixel_fired, self.meta_quiet = "2026-10-26T06:00:00+0000", 0

    def days(self, cid):
        n = self.quiet[cid]
        return shifted(T.day_rows(cost=50.0, conv=3.0, first=n + 1, last=30) + T.day_rows(cost=50.0, conv=0.0, first=1, last=n), self.day)

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        return T.searcher(days={c: self.days(c) for c in (A["id"], B["id"])}, actions={c: [T.action_row()] for c in (A["id"], B["id"])},
                          action_days={c: shifted(T.action_day_rows(conv=3.0 if not self.quiet[c] else 0.0), self.day) for c in (A["id"], B["id"])},
                          lists={c: [T.list_row("7", size_search=self.list_size[c], size_display=self.list_size[c])] for c in (A["id"], B["id"])}, fail=self.fail, fail_kinds=self.fail_kinds)(cid, q)

    def pages(self, path, params):
        n = self.meta_quiet
        rows = [T.meta_row("9", (self.day - dt.timedelta(days=i)).isoformat(), leads="0" if i <= n else "3") for i in range(1, 31)]
        return T.meta_pages(insights=rows, pixels=[{"id": "1", "name": "Site", "last_fired_time": self.pixel_fired}], fail=self.meta_fail)(path, params)


def spec_of(store, world, day=DAY, hour=7, workers=4):
    world.day = day
    now = lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc)                    # noqa: E731
    return tm.make_spec(today=day, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, meta_pages_fn=world.pages, meta_account_fn=lambda act: {"name": "Meta Co", "currency": "GBP"},
                        now_fn=now, workers=workers), now


def run(store, world, deliver, day=DAY, accounts=(A, B), hour=7, workers=4):
    spec, now = spec_of(store, world, day, hour, workers)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def fnd(rule, key, level, title="t"):
    return {"rule": rule, "key": key, "level": level, "area": "x", "title": title, "detail": "d", "fix": "Do the thing.", "evidence": ["e"]}


def analysed(*findings):
    return {"findings": list(findings)}


# ── What becomes an issue ───────────────────────────────────────────────────

def test_a_high_or_medium_finding_is_one_issue_each_with_the_fix_as_the_next_step_and_a_low_one_is_quiet():
    out = tm.issues_of(A, analysed(fnd("terms", "terms", "low", "Customer data terms are not accepted"), fnd("list_shrunk", "list:7", "medium"), fnd("no_conv", "no_conv", "high")))
    assert [(i["fingerprint"], i["level"], i["kind"]) for i in out] == [("track:1234567890:no_conv", "high", "no_conv"), ("track:1234567890:list:7", "medium", "list_shrunk"),
                                                                       ("track:1234567890:small", "low", "small")]
    assert out[0]["nextStep"] == "Start with: Do the thing." and out[0]["data"] == {"evidence": ["e"], "area": "x"} and out[0]["agent"] == "track"
    assert out[2]["title"] == "1 small finding to check" and "Customer data terms are not accepted" in out[2]["detail"] and out[2]["detail"].endswith("None of it stops tracking.")


def test_only_the_worst_hundred_findings_of_an_account_follow_one_by_one_and_the_rest_is_counted():
    many = [fnd("list_shrunk", "list:%d" % i, "medium") for i in range(104)] + [fnd("no_conv", "no_conv", "high")]
    out = tm.issues_of(A, analysed(*many))
    assert len(out) == 101 and out[0]["kind"] == "no_conv" and out[-1]["kind"] == "more" and out[-1]["title"] == "5 more tracking problems" and out[-1]["data"] == {"findings": 5}
    one = tm.issues_of(A, analysed(*[fnd("list_shrunk", "list:%d" % i, "medium") for i in range(101)]))
    assert one[-1]["title"] == "1 more tracking problem"
    assert tm.issues_of(A, analysed()) == []


# ── The daily run ───────────────────────────────────────────────────────────

def test_conversions_that_stop_after_the_baseline_are_alerted_once_and_resolved_when_they_return():
    s, dv, w = configured(), Deliver(), World()
    r0 = run(s, w, dv, d(0))
    assert r0["baseline"] is True and r0["issues"] == 0 and not dv.emails and not dv.slacks
    w.quiet[A["id"]] = 3
    r1 = run(s, w, dv, d(1))
    assert r1["new"] == 1 and r1["alerted"] == 1 and len(dv.emails) == 1 and len(dv.slacks) == 1
    assert dv.emails[0]["subject"] == "HIGH: Tracking & Data Health: 1 new (1 account)" and "No conversions in the last 3 days" in dv.emails[0]["text"] and "Acme Dental" in dv.emails[0]["text"]
    assert dv.slacks[0]["blocks"][0]["text"]["text"] == "\U0001F534 Tracking & Data Health"
    r2 = run(s, w, dv, d(2))
    assert r2["new"] == 0 and r2["alerted"] == 0 and len(dv.emails) == 1                                  # still broken: not alerted again
    w.quiet[A["id"]] = 0
    r3 = run(s, w, dv, d(3))
    assert r3["resolved"] == 1 and r3["issues"] == 0


def test_a_list_that_shrinks_is_alerted_from_the_second_day_the_monitor_has_seen_it():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0))
    assert s.snaps_get("track", A["id"], "2026-10-01")[0]["lists"] == {"7": {"name": "Visitors", "size": 9000}} if hasattr(s, "snaps_get") else True
    w.list_size[A["id"]] = "4000"
    r = run(s, w, dv, d(1))
    assert r["new"] == 1 and "lost 56 percent" in dv.emails[0]["text"] and "Acme Dental" in dv.emails[0]["text"]


def test_each_run_saves_the_lists_and_the_outcome_for_the_next_days_comparison():
    s, dv, w = configured(), Deliver(), World()
    w.quiet[B["id"]] = 7
    run(s, w, dv, d(0))
    [snap_a] = s.snaps_get("track", A["id"], "2026-10-01")
    [snap_b] = s.snaps_get("track", B["id"], "2026-10-01")
    assert snap_a["day"] == "2026-10-26" and snap_a["lists"] == {"7": {"name": "Visitors", "size": 9000}} and snap_a["status"] == "clean" and snap_a["findings"] == 0
    assert snap_b["status"] == "fix" and snap_b["high"] == 1 and snap_b["findings"] >= 1


def test_a_meta_account_is_read_with_its_pixel_a_silent_pixel_is_quiet_while_conversions_arrive_and_alerted_when_they_stop():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0), accounts=[A, B, M])
    w.pixel_fired = "2026-10-20T06:00:00+0000"
    r = run(s, w, dv, d(1), accounts=[A, B, M])
    assert r["read"] == 3 and r["new"] == 0 and r["alerted"] == 0 and not dv.emails                       # conversions are still arriving: the pixel is probably one the account no longer uses
    quiet = [i for i in s.issues_all("track") if i["accountId"] == M["id"]]
    assert [i["fingerprint"] for i in quiet] == ["track:act_1234567:small"] and "last fired 7 days ago" in quiet[0]["detail"] and quiet[0]["level"] == "low"
    w.meta_quiet = 3
    r2 = run(s, w, dv, d(2), accounts=[A, B, M])
    assert r2["new"] == 2 and r2["alerted"] == 2 and "No conversions in the last 3 days" in dv.emails[-1]["text"] and "The pixel \"Site\" last fired 8 days ago" in dv.emails[-1]["text"]


def test_a_spent_google_budget_does_not_stop_the_read_of_a_meta_account():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    r = run(s, w, dv, d(0), accounts=[A, B, M], workers=1)
    assert r["read"] == 1 and [u["accountId"] for u in r["unreadable"]] == [A["id"], B["id"]]
    assert r["unreadable"][0]["error"] == "The daily limit is used up." and "daily read budget was used up earlier" in r["unreadable"][1]["error"]


def test_an_account_that_cannot_be_read_is_unreadable_and_what_is_open_there_is_not_resolved():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0))
    w.quiet[A["id"]] = 3
    run(s, w, dv, d(1))
    w.fail = {A["id"]}
    r = run(s, w, dv, d(2))
    assert r["resolved"] == 0 and r["unreadable"][0]["accountId"] == A["id"] and r["read"] == 1
    open_rows = [i for i in s.issues_all("track") if i["status"] == "open"]
    assert [i["fingerprint"] for i in open_rows] == ["track:1234567890:no_conv"]


def test_an_account_read_for_the_first_time_after_the_baseline_does_not_alert_what_is_already_wrong():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0), accounts=[A])
    w.quiet[B["id"]] = 5
    r = run(s, w, dv, d(1), accounts=[A, B])
    assert r["firstRead"] == 1 and r["new"] == 0 and r["alerted"] == 0 and not dv.emails and r["issues"] >= 1


# ── A part of the read that failed ──────────────────────────────────────────

GOOGLE_READ = {"actions": [], "lists": [], "terms": True, "uploads": {"hasUploadActions": False}, "pixels": None}
META_READ = {"actions": None, "lists": [], "terms": None, "uploads": None, "pixels": []}
QUIET = {"small", "more"}


def test_every_rule_but_the_two_of_the_days_belongs_to_a_soft_part_of_the_read():
    assert set().union(*(set(v) for v in tm.PART_RULES.values())) == set(math.RULES) - {"no_conv", "conv_drop"}              # the days are a hard read: without them the account is unreadable


def test_the_rules_of_a_part_that_failed_to_read_are_held_with_the_quiet_and_the_counted_issues():
    assert tm.unread_rules(GOOGLE_READ, "google") == set() and tm.unread_rules(META_READ, "meta") == set()
    for part, rules in (("actions", {"action_stopped"}), ("lists", {"list_lost", "list_shrunk"}), ("terms", {"terms"}), ("uploads", {"upload_stale"})):
        assert tm.unread_rules(dict(GOOGLE_READ, **{part: None}), "google") == rules | QUIET, part
    assert tm.unread_rules(dict(META_READ, pixels=None), "meta") == {"pixel_stale", "pixel_unavailable"} | QUIET
    assert tm.unread_rules(dict(META_READ, lists=None), "meta") == {"list_lost", "list_shrunk"} | QUIET
    assert tm.unread_rules(dict(GOOGLE_READ, actions=None, terms=None), "google") == {"action_stopped", "terms"} | QUIET


def test_a_part_the_platform_never_reads_is_not_held_so_a_fully_read_account_holds_nothing():
    s, w = configured(), World()
    spec, _now = spec_of(s, w)
    got = spec["collect"]([A, B, M], [])
    assert got["okIds"] == {A["id"], B["id"], M["id"]} and got["hold"] == {}
    w.fail_kinds, w.meta_fail = ("lists", "uploads"), ("pixels",)
    held = spec_of(s, w)[0]["collect"]([A, B, M], [])["hold"]
    assert held == {A["id"]: {"list_lost", "list_shrunk", "upload_stale"} | QUIET, B["id"]: {"list_lost", "list_shrunk", "upload_stale"} | QUIET, M["id"]: {"pixel_stale", "pixel_unavailable"} | QUIET}


def test_a_list_issue_stays_open_while_the_lists_cannot_be_read_and_is_not_alerted_as_new_when_they_can():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0))
    w.list_size[A["id"]] = "4000"
    assert run(s, w, dv, d(1))["alerted"] == 1
    w.fail_kinds = ("lists",)
    r2 = run(s, w, dv, d(2))
    assert r2["resolved"] == 0 and r2["held"] >= 1 and r2["new"] == 0
    assert [i["status"] for i in s.issues_all("track") if i["kind"] == "list_shrunk"] == ["open"]
    w.fail_kinds = ()
    r3 = run(s, w, dv, d(3))
    assert r3["new"] == 0 and r3["alerted"] == 0 and r3["resolved"] == 0 and len(dv.emails) == 1
    [row] = [i for i in s.issues_all("track") if i["kind"] == "list_shrunk"]
    assert row["status"] == "open" and row["reopened"] == 0


def test_a_held_part_does_not_hold_the_other_issues_of_the_account():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0))
    w.list_size[A["id"]] = "4000"
    w.quiet[A["id"]] = 3
    assert run(s, w, dv, d(1))["alerted"] == 2                                                        # the list and the stopped conversions
    w.fail_kinds, w.quiet[A["id"]] = ("lists",), 0
    r2 = run(s, w, dv, d(2))
    states = {i["kind"]: i["status"] for i in s.issues_all("track")}
    assert states == {"list_shrunk": "open", "no_conv": "resolved"} and r2["resolved"] == 1


def test_a_silent_pixel_stays_open_while_the_pixels_cannot_be_read_and_the_quiet_issue_is_held_too():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, d(0), accounts=[A, B, M])
    w.pixel_fired = "2026-10-20T06:00:00+0000"
    run(s, w, dv, d(1), accounts=[A, B, M])                                                           # conversions arrive: the silent pixel is a quiet `small` issue
    assert [(i["kind"], i["status"]) for i in s.issues_all("track") if i["accountId"] == M["id"]] == [("small", "open")]
    w.meta_fail = ("pixels",)
    r2 = run(s, w, dv, d(2), accounts=[A, B, M])
    assert r2["resolved"] == 0 and [(i["kind"], i["status"]) for i in s.issues_all("track") if i["accountId"] == M["id"]] == [("small", "open")]
    w.meta_fail, w.meta_quiet = (), 3
    assert run(s, w, dv, d(3), accounts=[A, B, M])["new"] == 2                                       # readable again and now broken: the pixel and the conversions are news
    w.meta_fail = ("pixels",)
    r4 = run(s, w, dv, d(4), accounts=[A, B, M])
    assert r4["resolved"] == 0 and r4["new"] == 0 and sorted(i["kind"] for i in s.issues_all("track") if i["accountId"] == M["id"] and i["status"] == "open") == ["no_conv", "pixel_stale"]
    w.meta_fail = ()
    r5 = run(s, w, dv, d(5), accounts=[A, B, M])
    assert r5["new"] == 0 and r5["alerted"] == 0 and r5["resolved"] == 0


def test_the_spec_names_the_monitor_and_its_page():
    spec = tm.make_spec(search_fn=lambda *a: [], list_fn=lambda: [])
    assert spec["agent"] == "track" and spec["label"] == "Tracking & Data Health" and tm.PAGE == "/p2/paid-marketing/tracking-health"
    assert math.ORDER[0] == "no_conv"
