"""Paid Marketing Agents: the monitors together (the Brand Spend Alert, the Disapproved Ads Tracker, the Ad Copy Checker and the Spend Anomaly Alert), what the routes and the scheduled run use (no network of its own).

  spec_for      the spec the engine (tracker/paid_monitor.py) runs for one monitor, built with what the team saved
  run_agent     one monitor over the accounts it is given
  run_scheduled the daily run: every monitor except the "frequent" ones (the Ad Copy Checker on Mondays only), then on Mondays each one's weekly summary (once a day at most, and only when a
                a channel took it); or, when `only` names monitors, just those and no summaries (the Spend Anomaly Alert has its own run every two hours)
  state_for     what an alert panel on a monitor's page shows: the recipients, the senders that are set up, the baseline, the last runs, the open issues and the ones fixed lately
"""
from __future__ import annotations

import datetime as dt

from . import paid_monitor as mon

AGENTS = {
    "brandspend": {"label": "Brand Spend Alert", "page": "/p2/paid-marketing/brand-spend"},
    "disapproved": {"label": "Disapproved Ads Tracker", "page": "/p2/paid-marketing/disapproved-ads"},
    "copycheck": {"label": "Ad Copy Checker", "page": "/p2/paid-marketing/ad-copy-checker", "weekdays": (0,)},          # Mondays only: ad text changes slowly and the read is five queries an account
    "spend": {"label": "Spend Anomaly Alert", "page": "/p2/paid-marketing/spend-anomaly", "frequent": True},               # its own run every two hours: a spike is worth an alert the hour it starts
}
SHOWN_OPEN = 40
SHOWN_FIXED = 10


class BadRequest(ValueError):
    pass


def agent_of(name):
    if name not in AGENTS:
        raise BadRequest("The monitor must be one of: %s." % ", ".join(sorted(AGENTS)))
    return name


def link_for(agent, base):
    return base.rstrip("/") + AGENTS[agent]["page"]


def spec_for(agent, store, *, today=None, search_fn=None, list_fn=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None):
    agent_of(agent)
    day = today or (now_fn() if now_fn else dt.datetime.now(dt.timezone.utc)).date()
    if agent == "brandspend":
        from . import paid_brandspend_monitor as bm
        from . import paid_brandspend_settings as st
        return bm.make_spec(today=day, search_fn=search_fn, list_fn=list_fn, settings=st.load(store), mon_store=store)
    if agent == "copycheck":
        from . import paid_copycheck_monitor as cm
        return cm.make_spec(today=day, search_fn=search_fn, list_fn=list_fn, mon_store=store, meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn, now_fn=now_fn)
    if agent == "spend":
        from . import paid_anomaly_monitor as am
        return am.make_spec(today=day, search_fn=search_fn, list_fn=list_fn, mon_store=store, meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn, now_fn=now_fn)
    from . import paid_disapproved_monitor as dm
    return dm.make_spec(today=day, search_fn=search_fn, list_fn=list_fn, mon_store=store, meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn, now_fn=now_fn)


def run_agent(agent, store, *, trigger, base_link, deliver, accounts, groups, problems=(), now_fn=None, **inject):
    """One run of one monitor. `inject` hands the reads (search_fn, list_fn, meta_pages_fn, meta_account_fn) in for tests."""
    spec = spec_for(agent, store, now_fn=now_fn, **inject)
    return mon.run_monitor(store, spec, trigger=trigger, link=link_for(agent, base_link), deliver=deliver, accounts=accounts, groups=groups, problems=problems, now_fn=now_fn)


def send_weekly(agent, store, *, base_link, deliver, now):
    """Send one monitor's weekly summary, once a day at most; {sent, why}."""
    key = "digest:" + agent
    last = (store.state_get(key) or {}).get("day")
    if last == now.date().isoformat():
        return {"sent": False, "why": "already sent today"}
    msg = mon.weekly_digest(AGENTS[agent]["label"], store.issues_all(agent), now, link_for(agent, base_link))
    if msg is None:
        return {"sent": False, "why": "nothing to say"}
    res = mon.send(store, msg, deliver)
    if res["sent"]:
        store.state_set(key, {"day": now.date().isoformat()})
    return {"sent": res["sent"], "why": "" if res["sent"] else "no channel took it", "email": res["email"], "slack": res["slack"]}


def run_scheduled(store, *, base_link, deliver, inventory_fn, now_fn=None, only=None, **inject):
    """The monitors over every account. With no `only` (the daily run) every monitor that is not "frequent" runs, and on a Monday each one's weekly summary (the frequent ones' too);
    with `only` (a list of monitor names) just those run and no summary is sent. {agent: run record, "weekly": {agent: {...}}}."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
    names = [agent_of(a) for a in only] if only else None
    accounts, groups, problems = inventory_fn()
    out = {}
    for agent in AGENTS:
        if names is not None and agent not in names:
            continue
        if names is None and AGENTS[agent].get("frequent"):
            continue
        days = AGENTS[agent].get("weekdays")
        if names is None and days and now_fn().weekday() not in days:
            continue
        out[agent] = run_agent(agent, store, trigger="schedule", base_link=base_link, deliver=deliver, accounts=accounts, groups=groups, problems=problems, now_fn=now_fn, **inject)
    if names is None and now_fn().weekday() == 0:
        out["weekly"] = {agent: send_weekly(agent, store, base_link=base_link, deliver=deliver, now=now_fn()) for agent in AGENTS}
    return out


def state_for(store, agent, deliver, now=None):
    """What the alert panel of a monitor's page shows."""
    agent_of(agent)
    now = now or dt.datetime.now(dt.timezone.utc)
    issues = store.issues_all(agent)
    opened = sorted([i for i in issues if i["status"] == "open"], key=lambda i: (-mon.rank(i["level"]), i["accountName"].lower(), i["title"]))
    fixed = sorted([i for i in issues if i["status"] == "resolved"], key=lambda i: i.get("resolvedAt") or "", reverse=True)

    def row(i):
        until = mon._parse(i["resolvedAt"]) if i["status"] == "resolved" and i.get("resolvedAt") else now                  # how long it was open: to the day it was fixed, or to now
        return {"fingerprint": i["fingerprint"], "accountId": i["accountId"], "accountName": i["accountName"], "platform": i["platform"], "kind": i["kind"], "level": i["level"], "title": i["title"],
                "detail": i["detail"], "nextStep": i["nextStep"], "firstSeen": i["firstSeen"], "ageDays": mon.age_days(i["firstSeen"], until), "reopened": i.get("reopened") or 0,
                "alertedAt": i.get("alertedAt"), "resolvedAt": i.get("resolvedAt")}
    return {"agent": agent, "label": AGENTS[agent]["label"], "alerts": mon.alert_settings(store), "senders": deliver.ready(), "baseline": mon.baseline(store, agent),
            "runs": (store.state_get("runs:" + agent) or [])[:10], "counts": {lv: sum(1 for i in opened if i["level"] == lv) for lv in ("high", "medium", "low")},
            "open": [row(i) for i in opened[:SHOWN_OPEN]], "openTotal": len(opened), "fixed": [row(i) for i in fixed[:SHOWN_FIXED]],
            "digest": (store.state_get("digest:" + agent) or {}).get("day"), "running": bool((store.state_get("lock:" + agent) or {}).get("locked"))}
