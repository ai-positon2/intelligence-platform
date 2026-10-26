"""Tracking Data Health: the daily run through the real monitor engine, on the stand-ins of tests/tracking_fakes.py and tests/monitor_fakes.py. Every account is made up."""
import datetime as dt

from tests.monitor_fakes import Deliver, MemStore
from tests.tracking_fakes import Google, Meta
from tracker import paid_monitors as pm
from tracker import paid_tracking_settings as st

A = {"id": "1111111111", "name": "Acme Dental", "platform": "google"}
B = {"id": "2222222222", "name": "Bolt Imaging", "platform": "google"}
M = {"id": "act_5550001", "name": "Acme Meta", "platform": "meta"}
LISTED = [{"id": A["id"], "name": A["name"], "currency": "USD"}, {"id": B["id"], "name": B["name"], "currency": "USD"}]
DAY1 = dt.datetime(2026, 10, 20, 7, 30, tzinfo=dt.timezone.utc)


class World:
    def __init__(self, google=None, meta=None):
        self.now = DAY1
        self.google = Google(google or {A["id"]: {}, B["id"]: {}}, lambda: self.now)
        self.meta = Meta(meta or {}, lambda: self.now)
        self.store, self.dv = MemStore(), Deliver()
        self.store.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})

    def run(self, accounts=(A, B), days=0):
        self.now = DAY1 + dt.timedelta(days=days)
        return pm.run_agent("tracking", self.store, trigger="schedule", base_link="https://x.test", deliver=self.dv, accounts=list(accounts), groups=[], now_fn=lambda: self.now,
                            search_fn=self.google, list_fn=lambda: LISTED, meta_pages_fn=self.meta, meta_account_fn=lambda act: {"name": M["name"], "currency": "USD"})

    def open_issues(self):
        return [i for i in self.store.issues_all("tracking") if i["status"] == "open"]


def test_the_first_run_is_the_baseline_and_sends_nothing():
    w = World({A["id"]: {"over": {0: (100, 0)}}, B["id"]: {}})
    run = w.run()
    assert run["status"] == "done" and run["baseline"] and run["read"] == 2 and not w.dv.emails and not w.dv.slacks
    assert [i["kind"] for i in w.open_issues()] == ["conv_stopped"]


def test_conversions_that_stop_after_the_baseline_are_alerted_as_critical_once():
    w = World()
    w.run()
    w.google.accounts[A["id"]]["over"] = {0: (100, 0)}
    w.run(days=1)
    assert len(w.dv.emails) == 1 and w.dv.emails[0]["subject"] == "CRITICAL: Tracking Data Health: 1 new (1 account)"
    assert "Conversions stopped" in w.dv.emails[0]["text"] and w.dv.slacks[0]["blocks"][0]["text"]["text"].startswith("\U0001F6A8")
    w.google.accounts[A["id"]]["over"] = {0: (100, 0), -1: (100, 0)}
    w.run(days=2)
    assert len(w.dv.emails) == 1                                                          # still stopped: the same issue, not news


def test_a_stop_still_open_a_week_after_its_alert_is_reminded():
    w = World()
    w.run()
    w.google.accounts[A["id"]]["over"] = {0: (100, 0)}
    w.run(days=1)
    w.google.accounts[A["id"]]["conv"] = 4
    w.google.accounts[A["id"]]["over"] = {n: (100, 0) for n in range(-8, 1)}
    w.run(days=8)
    assert len(w.dv.emails) == 2 and "1 still open" in w.dv.emails[1]["subject"]


def test_tracking_that_comes_back_resolves_the_issue():
    w = World()
    w.run()
    w.google.accounts[A["id"]]["over"] = {0: (100, 0)}
    w.run(days=1)
    w.google.accounts[A["id"]]["over"] = {}
    run = w.run(days=2)
    assert run["resolved"] == 1 and not w.open_issues()


def test_an_action_that_stopped_with_the_account_is_one_issue_not_two():
    w = World({A["id"]: {"actions": [{"id": "31", "name": "Lead form", "perDay": 4}]}, B["id"]: {}})
    w.run()
    w.google.accounts[A["id"]].update(over={0: (100, 0), -1: (100, 0), -2: (100, 0)}, actions=[{"id": "31", "name": "Lead form", "perDay": 4, "stopFrom": -2}])
    w.run(days=1)
    assert [i["kind"] for i in w.open_issues()] == ["conv_stopped"] and "Lead form" in w.open_issues()[0]["detail"]


def test_an_acknowledged_stop_stays_open_and_is_not_alerted():
    w = World()
    w.run()
    st.acknowledge(w.store, A["id"], "conv_stopped", "tag fix going out today", "ana@position2.com", now=DAY1)
    w.google.accounts[A["id"]]["over"] = {0: (100, 0)}
    w.run(days=1)
    issue = w.open_issues()[0]
    assert not w.dv.emails and issue["level"] == "low" and "tag fix going out today" in issue["detail"] and issue["data"]["acked"]


def test_an_expected_drop_is_kept_on_the_page_and_not_alerted():
    w = World()
    w.run()
    day = (DAY1 + dt.timedelta(days=1)).date() - dt.timedelta(days=1)
    st.add_window(w.store, A["id"], A["name"], day.isoformat(), day.isoformat(), "site migration", "ana@position2.com", now=DAY1)
    w.google.accounts[A["id"]]["over"] = {0: (100, 0)}
    w.run(days=1)
    assert not w.dv.emails and w.open_issues()[0]["level"] == "low" and "site migration" in w.open_issues()[0]["detail"]


def test_an_account_that_cannot_be_read_never_resolves_what_is_open_in_it():
    w = World()
    w.run()
    w.google.accounts[A["id"]]["over"] = {0: (100, 0)}
    w.run(days=1)
    w.google.accounts[A["id"]]["down"] = "permission"
    run = w.run(days=2)
    assert run["unreadableCount"] == 1 and run["resolved"] == 0 and [i["kind"] for i in w.open_issues()] == ["conv_stopped"]


def test_a_part_that_is_refused_is_left_out_and_the_rest_is_judged():
    w = World({A["id"]: {"fail": {"actions", "lists"}, "tagging": False}, B["id"]: {}})
    run = w.run()
    assert run["read"] == 2 and [i["kind"] for i in w.open_issues()] == ["auto_tagging"]


def test_the_quota_running_out_stops_the_google_reads_still_to_come_in_the_run():
    from tracker import paid_tracking_monitor as tm
    w = World({A["id"]: {"down": "quota"}, B["id"]: {}})
    spec = tm.make_spec(search_fn=w.google, list_fn=lambda: LISTED, mon_store=w.store, now_fn=lambda: DAY1, workers=1)
    got = spec["collect"]([A, B], [])
    assert got["okIds"] == set() and "read budget" in got["unreadable"][1]["error"] and not any(cid == B["id"] for cid, _q in w.google.calls)


def test_the_list_sizes_kept_each_day_let_a_later_run_see_a_list_fall_under_a_thousand():
    w = World({A["id"]: {"lists": [{"id": "9", "name": "Site visitors", "size": 1600}]}, B["id"]: {}})
    w.run()
    assert w.store.snaps[("tracking", A["id"], "2026-10-20")]["lists"] == {"9": 1600}
    w.google.accounts[A["id"]]["lists"] = [{"id": "9", "name": "Site visitors", "size": 700}]
    w.run(days=3)
    assert len(w.dv.emails) == 1 and "Site visitors is under 1,000" in w.dv.emails[0]["text"]


def test_a_meta_pixel_that_stopped_firing_is_critical_and_a_refused_audience_read_is_soft():
    w = World(meta={"pixels": [{"id": "88", "name": "Main pixel", "hoursAgo": 2}], "fail": {"customaudiences"}})
    run = w.run(accounts=[M])
    assert run["read"] == 1 and not w.open_issues()
    w.meta.a["pixels"] = [{"id": "88", "name": "Main pixel", "hoursAgo": 40}]
    w.run(accounts=[M], days=1)
    assert w.dv.emails[0]["subject"].startswith("CRITICAL:") and "Main pixel has not fired for 40 hours" in w.dv.emails[0]["text"]


def test_meta_conversions_that_stop_are_judged_like_googles():
    w = World(meta={})
    w.run(accounts=[M])
    w.meta.a["over"] = {0: (100, 0)}
    w.run(accounts=[M], days=1)
    assert [i["kind"] for i in w.open_issues()] == ["conv_stopped"] and w.open_issues()[0]["platform"] == "meta"


def test_the_panel_state_counts_critical_apart_from_the_three_levels():
    w = World({A["id"]: {"over": {0: (100, 0)}}, B["id"]: {"tagging": False}})
    w.run()
    v = pm.state_for(w.store, "tracking", w.dv)
    assert v["critical"] == 1 and v["counts"] == {"high": 1, "medium": 0, "low": 0} and v["label"] == "Tracking Data Health"
