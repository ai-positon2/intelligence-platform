"""The Alert Hub: severity, de-duplication across monitors, one message per destination, marking and the weekly summary (tracker/paid_alerthub.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tracker import paid_alerthub as hub  # noqa: E402
from tracker import paid_master  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402
from tracker import paid_monitors as pm  # noqa: E402

BASE = "https://p.example"
NOW = dt.datetime(2026, 10, 26, 7, 30, tzinfo=dt.timezone.utc)
G1, G2, G3, MA = "1111111111", "2222222222", "3333333333", "act_4444"
LABEL = {a: v["label"] for a, v in pm.AGENTS.items()}
STAMP = "2026-10-20T07:00:00+00:00"


def row(agent, kind, account=G1, level="high", title=None, name=None, platform="google", status="open", alerted=None, fp=None):
    r = mon.make_issue(agent, fp or "%s:%s:%s" % (agent, account, kind), {"id": account, "name": name or "Acct " + account[-2:], "platform": platform}, kind, level, title or "%s %s" % (agent, kind), "detail of %s" % kind,
                       "Start with: fix it")
    r.update(status=status, firstSeen=STAMP, lastSeen=STAMP, resolvedAt=None if status == "open" else "2026-10-25T07:00:00+00:00", reopened=0, alertedAt=alerted, alertedLevel=None, alert={})
    return r


def entry(agent, rows, resolved=(), event="new"):
    pending = [dict(r, event=event, ageDays=0) for r in rows]
    run = {"status": "done", "startedAt": "2026-10-26T07:00:%02d+00:00" % list(pm.AGENTS).index(agent), "alerted": 0, "email": None, "slack": None}
    return {"agent": agent, "label": LABEL[agent], "link": BASE + pm.AGENTS[agent]["page"], "pending": pending, "resolved": list(resolved), "run": run}


def seeded(rows, alerts=None, master=None, routes=None):
    s = MemStore()
    s.state_set("alerts", alerts if alerts is not None else {"emails": [], "slackChannel": "#paid-alerts"})
    s.issues_put(rows)
    for a in {r["agent"] for r in rows}:
        s.state_set("runs:" + a, [{"startedAt": entry(a, [])["run"]["startedAt"], "alerted": 0}])
    if master:
        s.state_set(paid_master.KEY, master)
    if routes:
        s.state_set(hub.KEY, {"routes": routes, "changes": []})
    return s


def client(name, accounts):
    return {"name": name, "accounts": {a: {} for a in accounts}, "shared": {}}


def flush(s, entries, dv=None):
    dv = dv or Deliver()
    return hub.flush(s, entries, base_link=BASE, deliver=dv, now=NOW, trigger="schedule", agents=pm.AGENTS), dv


def stored(s, agent, fp):
    return next(r for r in s.issues_all(agent) if r["fingerprint"] == fp)


# ── severity and the rules ──────────────────────────────────────────────────

def test_severity_is_p1_for_high_p2_for_medium_and_p3_for_the_rest():
    assert [hub.severity(l) for l in ("high", "medium", "low", "info", "unheard")] == ["P1", "P2", "P3", "P3", "P3"]


def test_the_rules_only_name_monitors_that_exist_and_each_family_has_a_text():
    names = set(pm.AGENTS)
    assert {a for a, k in hub.ROOT} <= names and set(hub.SYMPTOMS) <= names and {a for o in hub.FAMILIES.values() for a, k in o} <= names
    assert set(hub.FAMILIES) == set(hub.FAMILY_TEXT) and all(len(o) >= 2 and len(set(o)) == len(o) for o in hub.FAMILIES.values())
    assert not ({k for k in hub.ROOT} & {k for o in hub.FAMILIES.values() for k in o})            # a root cause is not also a member of a family


def test_kinds_the_rules_name_are_kinds_the_monitors_really_raise():
    """The rules match on (monitor, kind); a kind the monitor never raises would make a rule silently dead."""
    from tracker import paid_kpi_math, paid_track_math, paid_watch_math
    assert {"no_conv", "not_delivering"} <= set(paid_kpi_math.RULES) and {"no_conv", "conv_drop"} <= set(paid_track_math.RULES) and {"account_status", "account_gone"} <= set(paid_watch_math.RULES)
    from tracker import paid_anomaly_math
    import inspect
    src = inspect.getsource(paid_anomaly_math)
    assert '"drop"' in src
    from tracker import paid_disapproved_monitor
    assert '"stopped"' in inspect.getsource(paid_disapproved_monitor)


# ── de-duplication ──────────────────────────────────────────────────────────

def test_an_account_that_cannot_serve_covers_what_follows_from_it():
    down = row("watch", "account_status")
    cpa = row("kpi", "cpa", level="medium")
    drop = row("spend", "drop", level="medium")
    spike = row("spend", "spike", level="medium")
    conv = row("track", "no_conv")
    terms = row("track", "terms", level="medium")
    cm = hub.cover_map([down, cpa, drop, spike, conv, terms])
    assert set(cm) == {("kpi", cpa["fingerprint"]), ("spend", drop["fingerprint"]), ("track", conv["fingerprint"])}
    assert all(v["agent"] == "watch" for v in cm.values())                                 # a spike or a tracking term problem is its own problem


def test_account_gone_is_a_root_cause_too_and_another_account_is_not_covered():
    gone = row("watch", "account_gone")
    other = row("kpi", "cpa", account=G2, level="medium")
    mine = row("kpi", "cpa", level="medium")
    assert set(hub.cover_map([gone, other, mine])) == {("kpi", mine["fingerprint"])}


def test_the_same_fact_from_two_monitors_is_said_once_by_the_one_that_names_the_cause():
    stopped, nd, drop = row("disapproved", "stopped"), row("kpi", "not_delivering"), row("spend", "drop")
    cm = hub.cover_map([stopped, nd, drop])
    assert ("disapproved", stopped["fingerprint"]) not in cm
    assert cm[("kpi", nd["fingerprint"])]["agent"] == "disapproved" and cm[("spend", drop["fingerprint"])]["agent"] == "disapproved"
    nconv, kconv = row("track", "no_conv"), row("kpi", "no_conv")
    cm = hub.cover_map([nconv, kconv])
    assert set(cm) == {("kpi", kconv["fingerprint"])} and cm[("kpi", kconv["fingerprint"])]["agent"] == "track"
    cm = hub.cover_map([nd, drop])                                                          # without the stopped ad group, KPI Monitoring names it
    assert set(cm) == {("spend", drop["fingerprint"])} and cm[("spend", drop["fingerprint"])]["agent"] == "kpi"


def test_an_issue_is_never_hidden_behind_a_lower_one():
    lead_medium = row("disapproved", "stopped", level="medium")
    follower_high = row("kpi", "not_delivering", level="high")
    assert hub.cover_map([lead_medium, follower_high]) == {}
    lead_high = row("disapproved", "stopped", level="high")
    assert set(hub.cover_map([lead_high, row("kpi", "not_delivering", level="medium")])) == {("kpi", "kpi:%s:not_delivering" % G1)}
    assert hub.cover_map([lead_high, row("kpi", "not_delivering", level="high")]) != {}      # the same level is covered


def test_the_root_cause_is_the_one_named_when_several_cover_an_issue():
    down, stopped, nd = row("watch", "account_status"), row("disapproved", "stopped"), row("kpi", "not_delivering")
    assert hub.cover_map([stopped, nd, down])[("kpi", nd["fingerprint"])]["agent"] == "watch"
    assert hub.cover_map([stopped, nd, down])[("disapproved", stopped["fingerprint"])]["agent"] == "watch"
    assert "account itself" in hub.why_covered(down, nd) and "another monitor" in hub.why_covered(stopped, nd) and hub.why_covered(row("kpi", "cpa"), nd) == "Another issue already says it."


def test_a_monitor_never_covers_itself_or_a_row_with_its_own_fingerprint():
    a = row("kpi", "no_conv", fp="kpi:x:1")
    b = row("kpi", "no_conv", fp="kpi:x:2")
    assert hub.cover_map([a, b]) == {} and hub.cover_map([a, dict(a)]) == {}


# ── routing ─────────────────────────────────────────────────────────────────

def test_an_account_goes_to_its_clients_destination_or_to_the_default():
    master = {"clients": {"c-acme": client("Acme", [G1, MA]), "c-beta": client("Beta", [G2])}}
    h = {"routes": {"c-acme": {"slackChannel": "#acme", "emails": [], "floor": "P1"}}, "changes": []}
    d = {"emails": ["d@x.co"], "slackChannel": "#all"}
    a = hub.route_for(master, h, d, G1)
    assert a == {"key": "c-acme", "client": {"id": "c-acme", "name": "Acme"}, "slack": "#acme", "emails": ["d@x.co"], "floor": "P1", "own": True}
    assert hub.route_for(master, h, d, MA)["key"] == "c-acme"
    b = hub.route_for(master, h, d, G2)                                                   # a client with no route of its own
    assert b["key"] == hub.DEFAULT and b["client"]["name"] == "Beta" and b["slack"] == "#all" and b["floor"] == "P2" and b["own"] is False
    c = hub.route_for(master, h, d, G3)
    assert c["key"] == hub.DEFAULT and c["client"] is None


def test_a_route_that_sets_only_a_floor_keeps_the_default_destination_but_is_its_own_message():
    master = {"clients": {"c-acme": client("Acme", [G1])}}
    h = {"routes": {"c-acme": {"slackChannel": "", "emails": [], "floor": "P1"}}, "changes": []}
    r = hub.route_for(master, h, {"emails": [], "slackChannel": "#all"}, G1)
    assert r["key"] == "c-acme" and r["slack"] == "#all" and r["floor"] == "P1" and r["own"] is False


def test_a_route_is_cleaned_and_saved_with_who_and_cleared_when_it_says_nothing():
    s = MemStore()
    names = {"c-acme": "Acme"}
    r = hub.save_route(s, "c-acme", {"slackChannel": "#acme-paid", "emails": "A@B.co, a@b.co", "floor": "p1"}, "ana@position2.com", NOW, names)
    assert r == {"slackChannel": "#acme-paid", "emails": ["a@b.co"], "floor": "P1", "by": "ana@position2.com", "at": NOW.isoformat()}
    assert hub.load(s)["routes"] == {"c-acme": r} and hub.load(s)["changes"][0]["by"] == "ana@position2.com" and "Acme" in hub.load(s)["changes"][0]["what"]
    assert hub.save_route(s, "c-acme", {"slackChannel": "", "emails": "", "floor": "P2"}, "ana@position2.com", NOW, names) is None
    assert hub.load(s)["routes"] == {} and "cleared" in hub.load(s)["changes"][0]["what"]
    n = len(hub.load(s)["changes"])
    assert hub.save_route(s, "c-acme", {}, "ana@position2.com", NOW, names) is None and len(hub.load(s)["changes"]) == n        # clearing what is not there says nothing


@pytest.mark.parametrize("data,msg", [({"slackChannel": "not a channel!"}, "not a Slack channel"), ({"emails": "nope"}, "not an email address"), ({"floor": "P3"}, "must be P1 or P2"),
                                      ({"emails": ",".join("a%d@b.co" % i for i in range(11))}, "At most 10")])
def test_a_route_that_is_not_valid_is_refused_and_nothing_is_saved(data, msg):
    s = MemStore()
    with pytest.raises(hub.BadRequest, match=msg):
        hub.save_route(s, "c-acme", data, "ana", NOW, {"c-acme": "Acme"})
    assert s.state_get(hub.KEY) is None


def test_a_client_that_does_not_exist_cannot_be_given_a_route():
    with pytest.raises(hub.BadRequest, match="does not exist"):
        hub.save_route(MemStore(), "c-ghost", {"slackChannel": "#x"}, "ana", NOW, {"c-acme": "Acme"})


# ── one message, marking, retrying ──────────────────────────────────────────

def test_every_monitors_alerts_go_out_as_one_message_and_are_marked_alerted():
    r1, r2, r3 = row("kpi", "cpa", level="medium"), row("watch", "account_status", account=G2), row("creative", "tired", account=G3, level="medium")
    s = seeded([r1, r2, r3])
    out, dv = flush(s, [entry("kpi", [r1]), entry("watch", [r2]), entry("creative", [r3])])
    assert len(dv.slacks) == 1 and dv.slacks[0]["channel"] == "#paid-alerts" and not dv.emails
    assert out["sends"][0]["issues"] == 3 and out["sends"][0]["p1"] == 1 and out["sends"][0]["p2"] == 2 and out["held"] == 0
    assert dv.slacks[0]["text"].startswith("\U0001F534 Paid alerts: 1 P1, 2 P2 in 3 accounts")
    text = repr(dv.slacks[0]["blocks"])
    assert "KPI Monitoring" in text and "Account Watch" in text and "Creative &amp; Asset Health" in text and "Open Paid alerts" in text and BASE + "/p2/paid-marketing/alert-hub" in text
    for a, r in (("kpi", r1), ("watch", r2), ("creative", r3)):
        m = stored(s, a, r["fingerprint"])
        assert m["alertedAt"] == NOW.isoformat() and m["alertedLevel"] == r["level"] and m["alert"]["slack"] == "sent" and m["alert"]["route"] == "default"


def test_the_email_and_the_slack_text_say_severity_monitor_account_and_the_page_to_open():
    r1 = row("watch", "account_status", name="Acme Dental")
    s = seeded([r1], alerts={"emails": ["ppc@example.com"], "slackChannel": "#paid-alerts"})
    out, dv = flush(s, [entry("watch", [r1])])
    m = dv.emails[0]
    assert m["to"] == ["ppc@example.com"] and m["subject"] == "P1: Paid alerts: 1 P1 (1 account)"
    assert "[P1] Acme Dental (Google Ads), Account Watch" in m["text"] and "Next: Start with: fix it" in m["text"] and m["text"].endswith("Open the Alert Hub: " + BASE + "/p2/paid-marketing/alert-hub")
    assert "Acme Dental" in m["html"] and "Open the Alert Hub" in m["html"] and "P1" in m["html"]


def test_each_monitors_run_record_says_what_became_of_its_alerts():
    r1, r2 = row("kpi", "cpa", level="medium"), row("watch", "account_status", account=G2)
    s = seeded([r1, r2])
    e1, e2 = entry("kpi", [r1]), entry("watch", [r2])
    flush(s, [e1, e2])
    assert e1["run"]["alerted"] == 1 and e1["run"]["slack"] == {"status": "sent"} and e1["run"]["email"]["status"] == "not_configured" and e1["run"]["via"] == "hub" and e1["run"]["heldBack"] == 0
    saved = s.state_get("runs:kpi")[0]
    assert saved["alerted"] == 1 and saved["slack"] == {"status": "sent"} and saved["via"] == "hub"


def test_only_the_run_that_alerted_has_its_record_patched():
    r1 = row("kpi", "cpa", level="medium")
    s = seeded([r1])
    e = entry("kpi", [r1])
    s.state_set("runs:kpi", [{"startedAt": e["run"]["startedAt"], "alerted": 0}, {"startedAt": "2026-10-25T07:00:00+00:00", "alerted": 5, "slack": {"status": "sent"}}])
    flush(s, [e])
    new, old = s.state_get("runs:kpi")
    assert new["alerted"] == 1 and new["via"] == "hub" and old == {"startedAt": "2026-10-25T07:00:00+00:00", "alerted": 5, "slack": {"status": "sent"}}


def test_an_issue_a_channel_refused_is_not_marked_and_is_alerted_again_at_the_next_run():
    r1 = row("kpi", "cpa", level="medium")
    s = seeded([r1])
    e = entry("kpi", [r1])
    out, dv = flush(s, [e], Deliver(fail=("slack",)))
    assert out["sends"][0]["sent"] is False and out["sends"][0]["slack"]["status"] == "failed" and stored(s, "kpi", r1["fingerprint"])["alertedAt"] is None
    assert e["run"]["alerted"] == 0 and e["run"]["slack"]["status"] == "failed"


def test_one_channel_taking_the_message_is_enough_to_mark_it():
    r1 = row("kpi", "cpa", level="medium")
    s = seeded([r1], alerts={"emails": ["a@b.co"], "slackChannel": "#paid-alerts"})
    out, dv = flush(s, [entry("kpi", [r1])], Deliver(fail=("slack",)))
    m = stored(s, "kpi", r1["fingerprint"])
    assert out["sends"][0]["sent"] is True and m["alertedAt"] and m["alert"] == {"email": "sent", "slack": "failed", "route": "default"}


def test_a_destination_with_no_channel_and_no_email_sends_nothing_and_marks_nothing():
    r1 = row("kpi", "cpa", level="medium")
    s = seeded([r1], alerts={"emails": [], "slackChannel": ""})
    e = entry("kpi", [r1])
    out, dv = flush(s, [e])
    assert not dv.slacks and not dv.emails and out["sends"][0]["sent"] is False and out["sends"][0]["slack"]["status"] == "not_configured"
    assert stored(s, "kpi", r1["fingerprint"])["alertedAt"] is None and e["run"]["alerted"] == 0 and e["run"]["slack"]["status"] == "not_configured"


def test_a_run_that_only_fixed_things_sends_nothing_and_a_run_with_nothing_changes_nothing():
    s = seeded([row("kpi", "cpa", level="medium", status="resolved")])
    out, dv = flush(s, [entry("kpi", [], resolved=[row("kpi", "cpa", level="medium", status="resolved")])])
    assert out == {"sends": [], "held": 0, "belowFloor": 0} and not dv.slacks and s.state_get(hub.LOG_KEY) is None
    out, dv = flush(s, [])
    assert out == {"sends": [], "held": 0, "belowFloor": 0} and not dv.slacks


def test_what_was_fixed_since_the_last_run_is_counted_in_the_message_of_its_destination():
    r1 = row("kpi", "cpa", level="medium")
    old = row("kpi", "ctr", status="resolved")
    s = seeded([r1, old])
    out, dv = flush(s, [entry("kpi", [r1], resolved=[old])])
    assert "1 fixed since the last run" in repr(dv.slacks[0]["blocks"])


# ── held back ───────────────────────────────────────────────────────────────

def test_a_symptom_is_held_back_not_sent_and_not_marked_while_the_account_is_down():
    down, cpa = row("watch", "account_status"), row("kpi", "cpa", level="medium")
    s = seeded([down, cpa])
    ew, ek = entry("watch", [down]), entry("kpi", [cpa])
    out, dv = flush(s, [ew, ek])
    assert out["held"] == 1 and out["sends"][0]["issues"] == 1 and len(dv.slacks) == 1
    assert stored(s, "kpi", cpa["fingerprint"])["alertedAt"] is None and stored(s, "watch", down["fingerprint"])["alertedAt"] == NOW.isoformat()
    assert ek["run"]["heldBack"] == 1 and ek["run"]["alerted"] == 0 and ek["run"]["slack"] is None and ew["run"]["alerted"] == 1
    assert "also on KPI Monitoring" in repr(dv.slacks[0]["blocks"]) and "Also on: KPI Monitoring" in (dv.emails[0]["text"] if dv.emails else "Also on: KPI Monitoring")


def test_the_held_issue_is_alerted_once_nothing_covers_it_any_more():
    down, cpa = row("watch", "account_status"), row("kpi", "cpa", level="medium")
    s = seeded([down, cpa])
    flush(s, [entry("watch", [down]), entry("kpi", [cpa])])
    s.issues_put([dict(down, status="resolved", resolvedAt=NOW.isoformat())])                  # the account is back
    out, dv = flush(s, [entry("kpi", [cpa])])
    assert out["held"] == 0 and out["sends"][0]["issues"] == 1 and stored(s, "kpi", cpa["fingerprint"])["alertedAt"] == NOW.isoformat()
    assert "Account Watch" not in repr(dv.slacks[0]["blocks"]) and "KPI Monitoring" in repr(dv.slacks[0]["blocks"])


def test_an_issue_is_held_back_by_an_open_issue_that_was_alerted_on_an_earlier_day():
    stopped = row("disapproved", "stopped", alerted="2026-10-20T07:30:00+00:00")
    nd = row("kpi", "not_delivering")
    s = seeded([stopped, nd])
    out, dv = flush(s, [entry("kpi", [nd])])
    assert out["held"] == 1 and not dv.slacks and stored(s, "kpi", nd["fingerprint"])["alertedAt"] is None
    assert s.state_get(hub.LOG_KEY)[0]["held"] == 1 and s.state_get(hub.LOG_KEY)[0]["sends"] == []


def test_a_held_issue_does_not_stop_the_alert_for_what_is_not_held():
    down, cpa, ctr = row("watch", "account_status"), row("kpi", "cpa", level="medium"), row("creative", "tired", account=G2, level="medium")
    s = seeded([down, cpa, ctr])
    out, dv = flush(s, [entry("watch", [down]), entry("kpi", [cpa]), entry("creative", [ctr])])
    assert out["held"] == 1 and out["sends"][0]["issues"] == 2


# ── per client ──────────────────────────────────────────────────────────────

def clients_setup(floor="P2"):
    master = {"clients": {"c-acme": client("Acme", [G1]), "c-beta": client("Beta", [G2])}, "changes": []}
    routes = {"c-acme": {"slackChannel": "#acme-paid", "emails": [], "floor": floor}}
    return master, routes


def test_a_client_with_its_own_channel_gets_its_own_message_and_the_rest_go_to_the_default():
    master, routes = clients_setup()
    a, b, c = row("kpi", "cpa", account=G1, level="medium"), row("kpi", "cpa", account=G2, level="medium"), row("kpi", "cpa", account=G3, level="medium")
    s = seeded([a, b, c], master=master, routes=routes)
    out, dv = flush(s, [entry("kpi", [a, b, c])])
    by = {m["channel"]: m for m in dv.slacks}
    assert sorted(by) == ["#acme-paid", "#paid-alerts"] and len(dv.slacks) == 2
    assert by["#acme-paid"]["blocks"][0]["text"]["text"].endswith("Paid alerts · Acme") and "Acct 11" in repr(by["#acme-paid"]["blocks"]) and "Acct 22" not in repr(by["#acme-paid"]["blocks"])
    dflt = repr(by["#paid-alerts"]["blocks"])
    assert "Acct 22 (Beta)" in dflt and "Acct 33" in dflt and "Acct 11" not in dflt and "Beta)" in dflt           # the default message names the client of an account that has one
    assert {s_["route"]: s_["issues"] for s_ in out["sends"]} == {"default": 2, "c-acme": 1} and [s_["route"] for s_ in out["sends"]][0] == "default"
    assert stored(s, "kpi", a["fingerprint"])["alert"]["route"] == "c-acme" and stored(s, "kpi", b["fingerprint"])["alert"]["route"] == "default"


def test_a_route_that_fails_is_retried_without_holding_back_the_others():
    master, routes = clients_setup()
    a, c = row("kpi", "cpa", account=G1, level="medium"), row("kpi", "cpa", account=G3, level="medium")
    s = seeded([a, c], master=master, routes=routes)

    class OneRefuses(Deliver):
        def send_slack(self, channel, text, blocks):
            if channel == "#acme-paid":
                raise RuntimeError("not_in_channel")
            super().send_slack(channel, text, blocks)
    out, dv = flush(s, [entry("kpi", [a, c])], OneRefuses())
    assert stored(s, "kpi", a["fingerprint"])["alertedAt"] is None and stored(s, "kpi", c["fingerprint"])["alertedAt"] and [m["channel"] for m in dv.slacks] == ["#paid-alerts"]
    assert {x["route"]: x["sent"] for x in out["sends"]} == {"c-acme": False, "default": True}


def test_the_floor_of_a_client_keeps_p2_out_of_its_channel_and_marks_it_below_the_floor():
    master, routes = clients_setup(floor="P1")
    p1, p2 = row("watch", "account_status", account=G1), row("spend", "spike", account=G1, level="medium")
    s = seeded([p1, p2], master=master, routes=routes)
    e1, e2 = entry("watch", [p1]), entry("spend", [p2])
    out, dv = flush(s, [e1, e2])
    assert len(dv.slacks) == 1 and out["sends"][0]["issues"] == 1 and out["belowFloor"] == 1 and "Spend Anomaly" not in repr(dv.slacks[0]["blocks"])
    m = stored(s, "spend", p2["fingerprint"])
    assert m["alertedAt"] == NOW.isoformat() and m["alert"] == {"route": "c-acme", "reason": "below floor"} and e2["run"]["belowFloor"] == 1 and e2["run"]["alerted"] == 0


def test_an_issue_under_the_floor_that_gets_worse_is_alerted_again_like_any_other():
    """The engine alerts a row again when its level goes up past what was alerted (track()); the hub marks alertedLevel so that keeps working under a floor."""
    master, routes = clients_setup(floor="P1")
    p2 = row("kpi", "cpa", account=G1, level="medium")
    s = seeded([p2], master=master, routes=routes)
    flush(s, [entry("kpi", [p2])])
    assert stored(s, "kpi", p2["fingerprint"])["alertedLevel"] == "medium"
    s.state_set("baseline:kpi", {"at": STAMP, "by": "t"})
    s.state_set("seen:kpi", {"ids": [G1]})

    def current(level):
        return [mon.make_issue("kpi", p2["fingerprint"], {"id": G1, "name": "Acct 11", "platform": "google"}, "cpa", level, "t", "d", "n")]
    assert mon.track(s, "kpi", current("medium"), {G1}, NOW)["pending"] == []
    assert [p["event"] for p in mon.track(s, "kpi", current("high"), {G1}, NOW)["pending"]] == ["worse"]


def test_a_floor_does_not_apply_to_the_default_destination():
    p2 = row("kpi", "cpa", account=G3, level="medium")
    s = seeded([p2])
    out, dv = flush(s, [entry("kpi", [p2])])
    assert out["belowFloor"] == 0 and len(dv.slacks) == 1


def test_a_clients_own_email_is_used_and_falls_back_to_the_default_emails():
    master = {"clients": {"c-acme": client("Acme", [G1]), "c-beta": client("Beta", [G2])}, "changes": []}
    routes = {"c-acme": {"slackChannel": "", "emails": ["acme@x.co"], "floor": "P2"}, "c-beta": {"slackChannel": "#beta", "emails": [], "floor": "P2"}}
    a, b = row("kpi", "cpa", account=G1, level="medium"), row("kpi", "cpa", account=G2, level="medium")
    s = seeded([a, b], alerts={"emails": ["d@x.co"], "slackChannel": "#all"}, master=master, routes=routes)
    out, dv = flush(s, [entry("kpi", [a, b])])
    assert {tuple(m["to"]) for m in dv.emails} == {("acme@x.co",), ("d@x.co",)} and sorted(m["channel"] for m in dv.slacks) == ["#all", "#beta"]
    acme = next(m for m in dv.emails if m["to"] == ["acme@x.co"])
    assert acme["subject"] == "Paid alerts · Acme: 1 P2 (1 account)"


def test_the_send_log_says_where_each_message_went_and_keeps_thirty():
    master, routes = clients_setup()
    a, c = row("kpi", "cpa", account=G1, level="medium"), row("kpi", "cpa", account=G3, level="medium")
    s = seeded([a, c], master=master, routes=routes)
    flush(s, [entry("kpi", [a, c])])
    log = s.state_get(hub.LOG_KEY)
    assert len(log) == 1 and log[0]["trigger"] == "schedule" and log[0]["agents"] == ["KPI Monitoring"] and log[0]["at"] == NOW.isoformat()
    assert [(x["route"], x["name"], x["channel"], x["issues"], x["sent"]) for x in log[0]["sends"]] == [("default", "", "#paid-alerts", 1, True), ("c-acme", "Acme", "#acme-paid", 1, True)]
    s.state_set(hub.LOG_KEY, [{"at": "x"}] * 40)
    flush(s, [entry("kpi", [row("kpi", "ctr", account=G3, level="medium")])])
    assert len(s.state_get(hub.LOG_KEY)) == hub.LOG_KEPT


def test_a_long_alert_lists_the_worst_accounts_and_says_how_many_more():
    rows = [row("kpi", "cpa", account="%010d" % (1000000000 + n), level="medium" if n else "high", name="Account %02d" % n) for n in range(10)]
    s = seeded(rows)
    out, dv = flush(s, [entry("kpi", rows)])
    blocks = repr(dv.slacks[0]["blocks"])
    assert "+4 more accounts, 4 issues" in blocks and "Account 00" in blocks and out["sends"][0]["issues"] == 10
    assert all(stored(s, "kpi", r["fingerprint"])["alertedAt"] for r in rows)                  # every one is marked, the message only lists the worst


def test_a_test_send_goes_to_the_destination_it_is_for_and_says_it_is_a_test():
    dv = Deliver()
    r = hub.test_route({"slack": "#acme-paid", "emails": ["a@x.co"], "floor": "P2"}, "Acme", BASE + "/p2/paid-marketing/alert-hub", dv)
    assert r["sent"] is True and dv.slacks[0]["channel"] == "#acme-paid" and dv.emails[0]["to"] == ["a@x.co"]
    assert dv.emails[0]["subject"].startswith("TEST: P1: Paid alerts · Acme") and dv.slacks[0]["text"].startswith("TEST ") and "Test" in dv.slacks[0]["blocks"][0]["text"]["text"]
    none = hub.test_route({"slack": "", "emails": [], "floor": "P2"}, "", BASE, dv)
    assert none["sent"] is False and none["slack"]["status"] == "not_configured"


# ── the weekly summary ──────────────────────────────────────────────────────

def weekly(s, dv, now=NOW):
    return hub.weekly(s, base_link=BASE, deliver=dv, now=now, agents=pm.AGENTS)


def test_the_weekly_summary_is_one_message_for_every_monitor_with_low_ones_and_what_was_fixed():
    a, b, low = row("kpi", "cpa", level="medium"), row("watch", "account_status", account=G2), row("creative", "tired", account=G3, level="low")
    fixed = row("track", "no_conv", status="resolved")
    s = seeded([a, b, low, fixed])
    dv = Deliver()
    out = weekly(s, dv)
    assert out == {"default": {"sent": True, "why": "", "email": {"status": "not_configured", "error": "No email address is set."}, "slack": {"status": "sent"}}} and len(dv.slacks) == 1
    text = repr(dv.slacks[0]["blocks"])
    assert "3 open" in text and "1 P1" in text and "1 fixed this week" in text and "Creative &amp; Asset Health" in text
    assert s.state_get("digest:hub") == {"day": "2026-10-26"} and s.state_get("digest:hub:default") == {"day": "2026-10-26"}


def test_the_weekly_summary_goes_once_a_day_and_only_when_a_channel_took_it():
    a = row("kpi", "cpa", level="medium")
    s = seeded([a])
    dv = Deliver(fail=("slack",))
    first = weekly(s, dv)["default"]
    assert first["sent"] is False and first["why"] == "no channel took it" and s.state_get("digest:hub") is None
    dv = Deliver()
    assert weekly(s, dv)["default"]["sent"] is True
    assert weekly(s, dv, NOW + dt.timedelta(hours=5))["default"] == {"sent": False, "why": "already sent today"} and len(dv.slacks) == 1
    assert weekly(s, dv, NOW + dt.timedelta(days=7))["default"]["sent"] is True


def test_the_weekly_summary_is_not_sent_when_nothing_is_open_or_was_fixed_lately():
    s = seeded([row("kpi", "cpa", level="medium", status="resolved")])
    s.issues_put([dict(row("kpi", "cpa", level="medium", status="resolved"), resolvedAt="2026-10-01T07:00:00+00:00")])
    dv = Deliver()
    assert weekly(s, dv) == {} and not dv.slacks                                            # nothing open and nothing fixed in the last seven days: no message, and no entry
    assert weekly(MemStore(), dv) == {} and not dv.slacks


def test_each_client_gets_its_own_weekly_summary_and_a_destination_with_no_channel_says_so():
    master, routes = clients_setup()
    a, c = row("kpi", "cpa", account=G1, level="medium"), row("kpi", "cpa", account=G3, level="medium")
    s = seeded([a, c], master=master, routes=routes)
    dv = Deliver()
    out = weekly(s, dv)
    assert sorted(out) == ["c-acme", "default"] and sorted(m["channel"] for m in dv.slacks) == ["#acme-paid", "#paid-alerts"]
    assert "Paid alerts · Acme" in repr(dv.slacks[0]["blocks"]) or "Paid alerts · Acme" in repr(dv.slacks[1]["blocks"])
    s2 = seeded([c], alerts={"emails": [], "slackChannel": ""})
    assert weekly(s2, Deliver()) == {"default": {"sent": False, "why": "no channel is set"}}


def test_the_weekly_summary_text_lists_open_by_severity_and_by_monitor():
    rows = [row("kpi", "cpa", level="medium"), row("kpi", "ctr", level="medium"), row("watch", "account_status", account=G2)]
    s = seeded(rows, alerts={"emails": ["a@b.co"], "slackChannel": ""})
    dv = Deliver()
    weekly(s, dv)
    m = dv.emails[0]
    assert m["subject"] == "Paid alerts: weekly summary, 3 open (1 P1), 0 fixed this week"
    assert "3 open (1 P1, 2 P2, 0 P3); 0 fixed in the last 7 days." in m["text"] and "By monitor: KPI Monitoring 2, Account Watch 1." in m["text"]
    assert m["text"].index("[P1]") < m["text"].index("[P2]") and m["text"].endswith("Open the Alert Hub: " + BASE + "/p2/paid-marketing/alert-hub")


# ── the page's state ────────────────────────────────────────────────────────

def test_the_page_state_lists_every_open_issue_with_its_severity_client_and_what_covers_it():
    master, routes = clients_setup()
    down, cpa, low = row("watch", "account_status", account=G1), row("kpi", "cpa", account=G1, level="medium"), row("creative", "tired", account=G3, level="low")
    s = seeded([down, cpa, low, row("kpi", "ctr", account=G2, status="resolved")], master=master, routes=routes)
    v = hub.view(s, Deliver(), NOW, pm.AGENTS)
    assert v["openTotal"] == 3 and v["counts"] == {"P1": 1, "P2": 1, "P3": 1} and v["held"] == 1 and v["hasClients"] is True
    assert [i["p"] for i in v["open"]] == ["P1", "P2", "P3"] and v["open"][0]["clientName"] == "Acme" and v["open"][2]["clientName"] == ""
    assert v["open"][1]["coveredBy"]["agentLabel"] == "Account Watch" and "account itself" in v["open"][1]["coveredBy"]["why"] and v["open"][0]["coveredBy"] is None
    assert v["open"][1]["page"] == "/p2/paid-marketing/kpi-monitoring" and v["open"][1]["ageDays"] == 6
    assert [(c["id"], c["name"], c["accounts"], (c["route"] or {}).get("slackChannel")) for c in v["clients"]] == [("c-acme", "Acme", 1, "#acme-paid"), ("c-beta", "Beta", 1, None)]
    assert v["default"] == {"emails": [], "slackChannel": "#paid-alerts"} and v["senders"] == {"email": True, "slack": True} and [a["agent"] for a in v["agents"]] == list(pm.AGENTS)


def test_the_page_state_says_it_has_no_clients_when_the_master_has_none_and_lists_the_rules():
    v = hub.view(MemStore(), Deliver(), NOW, pm.AGENTS)
    assert v["hasClients"] is False and v["clients"] == [] and v["open"] == [] and v["openTotal"] == 0 and v["log"] == []
    assert v["rules"]["root"] == ["watch.account_gone", "watch.account_status"] and v["rules"]["families"]["conv_stopped"] == ["track.no_conv", "kpi.no_conv"] and v["rules"]["symptoms"]["kpi"] is None
    assert v["rules"]["symptoms"]["spend"] == ["drop"]


def test_the_page_lists_four_hundred_open_issues_at_most_and_says_how_many_there_are():
    rows = [row("kpi", "cpa", account="%010d" % (1000000000 + n), level="medium", fp="kpi:%d" % n) for n in range(hub.OPEN_SHOWN + 25)]
    s = seeded(rows)
    v = hub.view(s, Deliver(), NOW, pm.AGENTS)
    assert len(v["open"]) == hub.OPEN_SHOWN and v["openTotal"] == hub.OPEN_SHOWN + 25 and v["counts"]["P2"] == hub.OPEN_SHOWN + 25
