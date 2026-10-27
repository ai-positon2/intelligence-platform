"""Paid Marketing Agents: the Alert Hub, the one layer every monitor's alert goes through (sheet #38; no network, no clock but the one passed in).

The monitors (tracker/paid_monitor.py, run by tracker/paid_monitors.py) still find, keep and age their own issues. What they hand over after a run is `pending` (the issues that are new, worse or due
a reminder) and `resolved`; this module decides what becomes of them and sends one message per destination:

  severity   P1 is a high issue, P2 a medium one, P3 a low or information one; P1 and P2 are alerted (the engine has never alerted a low one), P3 is on the pages and in the Monday summary
  routes     a client of the Master Agent can have its own Slack channel, emails and a floor (P1 only, or P1 and P2); an account in no client, or in a client with no route, goes to the default
             destination (the settings every monitor page already shows)
  de-dup     a pending issue is held back, not sent and not marked alerted, while an open issue of the same or a higher level on the same account already says it (ROOT, SYMPTOMS, FAMILIES below);
             when that issue is fixed, the held one is alerted at the next run because nothing covers it any more
  marking    sent first, marked after, per destination: an issue is marked alerted only when a channel took the message it was in (the engine's rule), an issue under a destination's floor is
             marked alerted as `below floor` so it is not counted as new every day, and a destination with no channel at all marks nothing
  weekly     Mondays, one summary per destination for every monitor, not one per monitor
"""
from __future__ import annotations

import html as _html
from datetime import timedelta

from . import paid_monitor as mon

KEY = "settings:alerthub"
LOG_KEY = "hub:log"
LOG_KEPT = 30
CHANGES_KEPT = 20
DEFAULT = "default"
PAGE = "/p2/paid-marketing/alert-hub"
SEVERITY = {"high": "P1", "medium": "P2", "low": "P3", "info": "P3"}
FLOORS = ("P1", "P2")
FLOOR_RANK = {"P1": 3, "P2": 2}                  # the lowest issue rank (paid_monitor.LEVEL_RANK) a floor lets through
OPEN_SHOWN = 400                                 # open alerts the page lists

# An account that cannot serve at all: what follows from it is not a separate problem.
ROOT = {("watch", "account_status"), ("watch", "account_gone")}
# What follows from it: every finding of the monitor (None) or only the kinds listed.
SYMPTOMS = {"kpi": None, "kvis": None, "creative": None, "spend": {"drop"}, "track": {"no_conv", "conv_drop"}, "disapproved": {"stopped"}}
# Two monitors saying the same thing about one account: the first in each list names the cause, so it is the one that is sent.
FAMILIES = {
    "not_delivering": (("disapproved", "stopped"), ("kpi", "not_delivering"), ("spend", "drop")),
    "conv_stopped": (("track", "no_conv"), ("kpi", "no_conv")),
}
FAMILY_TEXT = {"not_delivering": "cannot show or deliver", "conv_stopped": "stopped converting"}


class BadRequest(ValueError):
    pass


def severity(level):
    return SEVERITY.get(level, "P3")


# ── De-duplication ──────────────────────────────────────────────────────────

def _key(row):
    return (row["agent"], row.get("kind"))


def _is_symptom(agent, kind):
    if agent not in SYMPTOMS:
        return False
    kinds = SYMPTOMS[agent]
    return kinds is None or kind in kinds


def _covers(lead, row):
    """True when the open issue `lead` already says what `row` says."""
    if mon.rank(lead["level"]) < mon.rank(row["level"]):
        return False                                                         # a high issue is never hidden behind a medium one
    if _key(lead) in ROOT and _is_symptom(*_key(row)):
        return True
    for order in FAMILIES.values():
        if _key(lead) in order and _key(row) in order and order.index(_key(lead)) < order.index(_key(row)):
            return True
    return False


def _best(leads):
    """Of several issues that cover a row, the root cause first, then the highest level, then the same order every time."""
    return sorted(leads, key=lambda l: (_key(l) not in ROOT, -mon.rank(l["level"]), l["agent"], l["fingerprint"]))[0] if leads else None


def cover_map(open_rows):
    """{(agent, fingerprint) of an open issue: the open issue that covers it} for every covered one."""
    by_account = {}
    for r in open_rows:
        by_account.setdefault(r["accountId"], []).append(r)
    out = {}
    for rows in by_account.values():
        for r in rows:
            lead = _best([l for l in rows if _covers(l, r)])
            if lead is not None:
                out[(r["agent"], r["fingerprint"])] = lead
    return out


def why_covered(lead, row):
    """The sentence that says why `row` is not sent on its own."""
    if _key(lead) in ROOT:
        return "The account itself is the problem (%s)." % lead["title"]
    for name, order in FAMILIES.items():
        if _key(lead) in order and _key(row) in order:
            return "The same thing (%s) is already said by another monitor." % FAMILY_TEXT[name]
    return "Another issue already says it."


# ── What the team saved ─────────────────────────────────────────────────────

def load(store):
    s = store.state_get(KEY) or {}
    return {"routes": {k: dict(v) for k, v in (s.get("routes") or {}).items()}, "changes": list(s.get("changes") or [])}


def clean_route(data):
    """{slackChannel, emails, floor} from what the page sent, or BadRequest saying what is wrong."""
    try:
        base = mon.clean_alert_settings(data if isinstance(data, dict) else {})
    except mon.BadRequest as e:
        raise BadRequest(str(e))
    floor = str((data or {}).get("floor") or "P2").strip().upper()
    if floor not in FLOORS:
        raise BadRequest("The floor must be P1 or P2.")
    return {"slackChannel": base["slackChannel"], "emails": base["emails"], "floor": floor}


def _note(s, who, what, now):
    s["changes"] = ([{"at": now.isoformat(), "by": who, "what": what}] + s["changes"])[:CHANGES_KEPT]


def save_route(store, client_id, data, who, now, clients):
    """Save one client's destination. `clients` is {client id: name} (the Master Agent's). A route that says nothing (no channel, no email, the P2 floor) is the same as none, so it removes it."""
    cid = str(client_id or "")
    if cid not in clients:
        raise BadRequest("That client does not exist.")
    r = clean_route(data)
    s = load(store)
    if not r["slackChannel"] and not r["emails"] and r["floor"] == "P2":
        if cid in s["routes"]:
            del s["routes"][cid]
            _note(s, who, "cleared the destination of %s" % clients[cid], now)
            store.state_set(KEY, s)
        return None
    s["routes"][cid] = dict(r, by=who, at=now.isoformat())
    _note(s, who, "set the destination of %s (%s, %d %s, floor %s)" % (clients[cid], r["slackChannel"] or "the default channel", len(r["emails"]), "email" if len(r["emails"]) == 1 else "emails", r["floor"]), now)
    store.state_set(KEY, s)
    return s["routes"][cid]


def route_for(master, hub, default, account_id):
    """Where an account's alerts go: {key, client, slack, emails, floor, own}. `own` is True when the destination is the client's own (not the default's)."""
    c = None
    for cid, cl in (master.get("clients") or {}).items():
        if str(account_id) in (cl.get("accounts") or {}):
            c = {"id": cid, "name": cl["name"]}
            break
    r = hub["routes"].get(c["id"]) if c else None
    if not r:
        return {"key": DEFAULT, "client": c, "slack": default["slackChannel"], "emails": list(default["emails"]), "floor": "P2", "own": False}
    return {"key": c["id"], "client": c, "slack": r.get("slackChannel") or default["slackChannel"], "emails": list(r.get("emails") or default["emails"]), "floor": r.get("floor") or "P2",
            "own": bool(r.get("slackChannel") or r.get("emails"))}


# ── The message ─────────────────────────────────────────────────────────────

def _label(route_name):
    return "Paid alerts" + (" \u00B7 " + route_name if route_name else "")


def _tag(item):
    bits = [item["p"], item["agentLabel"]]
    if item["event"] == "worse":
        bits.append("worse")
    elif item["event"] == "reminder":
        bits.append("still open")
    if item.get("alsoOn"):
        bits.append("also on " + ", ".join(item["alsoOn"]))
    return " \u00B7 ".join(bits)


def _counts(items):
    return {p: sum(1 for i in items if i["p"] == p) for p in ("P1", "P2", "P3")}


def compose(route_name, items, resolved, link, *, show_client=False):
    """{subject, text, html, slack_text, slack_blocks} for one destination. `items` are pending issues with p, agentLabel, event and alsoOn; `resolved` is the number fixed since the last runs.
    `show_client` adds the client to an account's name (the default destination mixes clients)."""
    label = _label(route_name)
    n = _counts(items)
    accounts = len({i["accountId"] for i in items})
    events = {e: sum(1 for i in items if i["event"] == e) for e in mon.EVENT_WORD}
    parts = ["%d P1" % n["P1"]] if n["P1"] else []
    parts += ["%d P2" % n["P2"]] if n["P2"] else []
    top = "P1: " if n["P1"] else ""
    subject = "%s%s: %s (%s)" % (top, label, ", ".join(parts) or "nothing new", mon.plural(accounts, "account"))
    ordered = sorted(items, key=lambda i: (-mon.rank(i["level"]), i["accountName"].lower(), i["title"]))
    shown, more = ordered[:mon.SHOWN], max(0, len(ordered) - mon.SHOWN)

    def who(i):
        return "%s (%s)" % (i["accountName"], i["clientName"]) if show_client and i.get("clientName") else i["accountName"]
    lines = []
    for i in shown:
        lines.append("- [%s] %s (%s), %s%s\n    %s\n    %s%s" % (i["p"], who(i), mon.PLATFORM.get(i["platform"], i["platform"]), i["agentLabel"], (", " + mon.EVENT_WORD[i["event"]].lower()) if i["event"] != "new" else "",
                                                               i["title"], i["detail"], ("\n    Next: " + i["nextStep"]) if i.get("nextStep") else ""))
        if i.get("alsoOn"):
            lines[-1] += "\n    Also on: " + ", ".join(i["alsoOn"])
        age = mon._age_text(i)
        if age:
            lines[-1] += "\n    (%s)" % age
    text = "\n".join(lines + (["...and %d more on the Alert Hub." % more] if more else []) + (["", "%d fixed since the last runs." % resolved] if resolved else []) + ["", "Open the Alert Hub: " + link])
    e = _html.escape
    rows = "".join("<li><b>%s</b> (%s) <span style=\"color:%s\">%s</span> <small>%s</small><br>%s<br>%s%s%s</li>" % (
        e(who(i)), e(mon.PLATFORM.get(i["platform"], i["platform"])), "#b91c1c" if i["p"] == "P1" else "#b45309", e(i["p"]), e(i["agentLabel"]), e(i["title"]), e(i["detail"]),
        ("<br><i>Next: %s</i>" % e(i["nextStep"])) if i.get("nextStep") else "", ("<br><small>Also on: %s</small>" % e(", ".join(i["alsoOn"]))) if i.get("alsoOn") else "") for i in shown)
    html_body = ("<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.5\"><p><b>%s</b></p><ul>%s</ul>%s%s<p><a href=\"%s\">Open the Alert Hub</a></p></div>"
                 % (e(subject), rows, ("<p>...and %d more on the Alert Hub.</p>" % more) if more else "", ("<p>%d fixed since the last runs.</p>" % resolved) if resolved else "", e(link)))
    slack_items = [dict(i, accountName=who(i), tag=_tag(i)) for i in items]
    bits = (["*%d P1*" % n["P1"]] if n["P1"] else []) + (["%d P2" % n["P2"]] if n["P2"] else []) + [mon.plural(accounts, "account")]
    bits += ["%d %s" % (events[e], word) for e, word in (("new", "new"), ("worse", "worse"), ("reminder", "still open")) if events[e]]
    lead = "  \u00B7  ".join(bits)
    blocks = mon._slack_message(label, slack_items, link, lead=lead, fixed=resolved)
    dot = mon.DOT[max(items, key=lambda i: mon.rank(i["level"]))["level"]] if items else mon.CLEAR
    return {"subject": subject, "text": text, "html": html_body, "slack_text": "%s %s: %s in %s" % (dot, label, ", ".join(parts) or "nothing new", mon.plural(accounts, "account")), "slack_blocks": blocks}


def _destination(route):
    return {"emails": route["emails"], "slackChannel": route["slack"]}


def test_route(route, name, link, deliver):
    """A made-up P1 through the real senders to one destination, to prove the wiring."""
    item = {"p": "P1", "agentLabel": "Alert Hub", "event": "new", "level": "high", "accountId": "0", "accountName": "Test account (not real)", "platform": "google", "title": "This is a test alert",
            "detail": "Nothing was found. It only proves that this destination works.", "nextStep": "", "ageDays": 0, "reopened": 0, "alsoOn": []}
    m = compose(name, [item], 0, link)
    m["subject"] = "TEST: " + m["subject"]
    m["slack_text"] = "TEST " + m["slack_text"]
    m["slack_blocks"][0]["text"]["text"] = "\U0001F9EA Test  \u00B7  " + _label(name)
    return mon.send_to(_destination(route), m, deliver)


# ── Deciding and sending ────────────────────────────────────────────────────

def _open_rows(store, agents):
    out = []
    for agent in agents:
        out += [r for r in store.issues_all(agent) if r["status"] == "open"]
    return out


def _agent_label(agents, agent):
    return (agents.get(agent) or {}).get("label") or agent


def plan(entries, open_rows, master, hub, default, agents):
    """What each destination gets. `entries` are the runs' outboxes. Returns {routes: {key: {route, name, items, skipped, resolved}}, held: [{row, by, why}], covers: {(agent, fp): [open rows]}}.
    items    pending issues to send (with p, agentLabel, clientName, alsoOn)
    skipped  pending issues under the destination's floor (marked alerted, not sent)
    held     pending issues another open issue already covers"""
    covered = cover_map(open_rows)
    covers = {}
    for k, lead in covered.items():
        covers.setdefault((lead["agent"], lead["fingerprint"]), []).append(k)
    by_key = {(r["agent"], r["fingerprint"]): r for r in open_rows}
    routes, held = {}, []

    def slot(account_id):
        r = route_for(master, hub, default, account_id)
        return routes.setdefault(r["key"], {"route": r, "name": (r["client"] or {}).get("name", "") if r["key"] != DEFAULT else "", "items": [], "skipped": [], "resolved": 0})
    for e in entries:
        for p in e["pending"]:
            lead = covered.get((p["agent"], p["fingerprint"]))
            if lead is not None:
                held.append({"row": p, "by": lead, "why": why_covered(lead, p)})
                continue
            s = slot(p["accountId"])
            also = sorted({_agent_label(agents, a) for a, fp in covers.get((p["agent"], p["fingerprint"]), ())})
            item = dict(p, p=severity(p["level"]), agentLabel=e["label"], clientName=(s["route"]["client"] or {}).get("name", ""), alsoOn=also)
            (s["items"] if mon.rank(p["level"]) >= FLOOR_RANK[s["route"]["floor"]] else s["skipped"]).append(item)
        for r in e["resolved"]:
            slot(r["accountId"])["resolved"] += 1
    return {"routes": routes, "held": held, "covers": covers, "byKey": by_key}


def _mark(store, agents_items, status, now):
    """Mark the issues of {agent: {fingerprint: reason or None}} alerted in the store."""
    stamp = now.isoformat()
    for agent, fps in agents_items.items():
        rows = store.issues_all(agent)
        out = []
        for r in rows:
            if r["fingerprint"] in fps:
                out.append(dict(r, alertedAt=stamp, alertedLevel=r["level"], alert=dict(status, reason=fps[r["fingerprint"]]) if fps[r["fingerprint"]] else dict(status)))
        if out:
            store.issues_put(out)


def _worst(statuses):
    """One status from several: sent only if all were, else the first that was not."""
    for s in statuses:
        if s["status"] != "sent":
            return s
    return {"status": "sent"} if statuses else None


def flush(store, entries, *, base_link, deliver, now, trigger, agents):
    """Decide and send what the runs in `entries` (their outboxes) have pending: one message per destination. Marks what a channel took, patches each run's record with what became of its alerts,
    and keeps a line in the send log. Returns {sends: [...], held: n, belowFloor: n}."""
    if not any(e["pending"] or e["resolved"] for e in entries):
        return {"sends": [], "held": 0, "belowFloor": 0}
    master, hub, default = _master(store), load(store), mon.alert_settings(store)
    p = plan(entries, _open_rows(store, agents), master, hub, default, agents)
    link = base_link.rstrip("/") + PAGE
    sends, outcome = [], {e["agent"]: {"delivered": 0, "held": 0, "below": 0, "email": [], "slack": []} for e in entries}
    for h in p["held"]:
        outcome[h["row"]["agent"]]["held"] += 1
    for key, slot in sorted(p["routes"].items(), key=lambda kv: (kv[0] != DEFAULT, kv[1]["name"].lower())):
        r = slot["route"]
        if slot["skipped"]:
            _mark(store, _by_agent(slot["skipped"], "below floor"), {"route": key}, now)
            for i in slot["skipped"]:
                outcome[i["agent"]]["below"] += 1
        if not slot["items"]:
            continue
        send = {"route": key, "name": slot["name"], "channel": r["slack"], "emails": len(r["emails"]), "issues": len(slot["items"]), "p1": sum(1 for i in slot["items"] if i["p"] == "P1"),
                "p2": sum(1 for i in slot["items"] if i["p"] == "P2"), "floor": r["floor"]}
        res = mon.send_to(_destination(r), compose(slot["name"], slot["items"], slot["resolved"], link, show_client=key == DEFAULT), deliver)    # says not_configured for a missing channel or email
        send.update(email=res["email"], slack=res["slack"], sent=res["sent"])
        sends.append(send)
        for i in slot["items"]:
            outcome[i["agent"]]["email"].append(res["email"])
            outcome[i["agent"]]["slack"].append(res["slack"])
            outcome[i["agent"]]["delivered"] += 1 if res["sent"] else 0
        if res["sent"]:
            _mark(store, _by_agent(slot["items"], None), {"email": res["email"]["status"], "slack": res["slack"]["status"], "route": key}, now)
    for e in entries:
        o = outcome[e["agent"]]
        e["run"].update(alerted=o["delivered"], email=_worst(o["email"]), slack=_worst(o["slack"]), heldBack=o["held"], belowFloor=o["below"], via="hub")
        _patch_run(store, e["agent"], e["run"])
    n_held, n_below = len(p["held"]), sum(o["below"] for o in outcome.values())
    if sends or n_held or n_below:
        log = store.state_get(LOG_KEY) or []
        store.state_set(LOG_KEY, [{"at": now.isoformat(), "trigger": trigger, "agents": [e["label"] for e in entries], "sends": sends, "held": n_held, "belowFloor": n_below}] + log[:LOG_KEPT - 1])
    return {"sends": sends, "held": n_held, "belowFloor": n_below}


def _by_agent(items, reason):
    out = {}
    for i in items:
        out.setdefault(i["agent"], {})[i["fingerprint"]] = reason
    return out


def _patch_run(store, agent, run):
    """Save what became of a run's alerts into its record in the store (the run is found by when it started)."""
    runs = store.state_get("runs:" + agent) or []
    store.state_set("runs:" + agent, [dict(r, **{k: run[k] for k in ("alerted", "email", "slack", "heldBack", "belowFloor", "via")}) if r.get("startedAt") == run.get("startedAt") else r for r in runs])


def _master(store):
    from . import paid_master
    return paid_master.load(store)


# ── The weekly summary ──────────────────────────────────────────────────────

def weekly(store, *, base_link, deliver, now, agents):
    """Send each destination that has something open or fixed in the last seven days one summary for every monitor, once a day at most: {route key: {sent, why}}. The open issues of every P level
    and what was fixed in the last seven days."""
    master, hub, default = _master(store), load(store), mon.alert_settings(store)
    link = base_link.rstrip("/") + PAGE
    cut = now - timedelta(days=mon.FIXED_WINDOW_DAYS)
    groups = {}

    def slot(account_id):
        r = route_for(master, hub, default, account_id)
        return groups.setdefault(r["key"], {"route": r, "name": (r["client"] or {}).get("name", "") if r["key"] != DEFAULT else "", "open": [], "fixed": []})
    for agent in agents:
        for i in store.issues_all(agent):
            i = dict(i, agentLabel=_agent_label(agents, agent), p=severity(i["level"]))
            if i["status"] == "open":
                slot(i["accountId"])["open"].append(i)
            elif (mon._parse(i.get("resolvedAt")) or cut) > cut:
                slot(i["accountId"])["fixed"].append(i)
    out = {}
    for key, g in sorted(groups.items(), key=lambda kv: (kv[0] != DEFAULT, kv[1]["name"].lower())):
        day_key = "digest:hub:" + key
        if (store.state_get(day_key) or {}).get("day") == now.date().isoformat():
            out[key] = {"sent": False, "why": "already sent today"}
            continue
        r = g["route"]
        if not r["slack"] and not r["emails"]:
            out[key] = {"sent": False, "why": "no channel is set"}
            continue
        res = mon.send_to(_destination(r), _weekly_message(g, now, link), deliver)
        if res["sent"]:
            store.state_set(day_key, {"day": now.date().isoformat()})
            store.state_set("digest:hub", {"day": now.date().isoformat()})
        out[key] = {"sent": res["sent"], "why": "" if res["sent"] else "no channel took it", "email": res["email"], "slack": res["slack"]}
    return out


def _weekly_message(g, now, link):
    opened = sorted(g["open"], key=lambda i: (-mon.rank(i["level"]), i["accountName"].lower(), i["title"]))
    fixed = sorted(g["fixed"], key=lambda i: (i["accountName"].lower(), i["title"]))
    n = _counts(opened)
    by_agent = {}
    for i in opened:
        by_agent[i["agentLabel"]] = by_agent.get(i["agentLabel"], 0) + 1
    label = _label(g["name"])
    subject = "%s: weekly summary, %d open%s, %d fixed this week" % (label, len(opened), (" (%d P1)" % n["P1"]) if n["P1"] else "", len(fixed))
    shown, shown_fixed = opened[:mon.DIGEST_OPEN], fixed[:mon.DIGEST_FIXED]
    agents_line = ", ".join("%s %d" % (a, c) for a, c in sorted(by_agent.items(), key=lambda kv: (-kv[1], kv[0])))

    def line(i):
        age = mon.age_days(i["firstSeen"], now)
        return "- [%s] %s: %s (%s)%s" % (i["p"], i["accountName"], i["title"], i["agentLabel"], (", open %s" % mon.plural(age, "day")) if age else "")

    def fixed_line(i):
        age = mon.age_days(i["firstSeen"], mon._parse(i["resolvedAt"]))
        return "- %s: %s (%s)%s" % (i["accountName"], i["title"], i["agentLabel"], (", was open %s" % mon.plural(age, "day")) if age else "")
    text = "\n".join(["%d open (%d P1, %d P2, %d P3); %d fixed in the last 7 days." % (len(opened), n["P1"], n["P2"], n["P3"], len(fixed)), ("By monitor: " + agents_line + ".") if agents_line else "", ""]
                     + (["Open, worst first:"] + [line(i) for i in shown] + (["...and %d more on the Alert Hub." % (len(opened) - len(shown))] if len(opened) > len(shown) else []) + [""] if shown else [])
                     + (["Fixed this week:"] + [fixed_line(i) for i in shown_fixed] + [""] if shown_fixed else []) + ["Open the Alert Hub: " + link])
    e = _html.escape
    html_body = ("<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.5\"><p><b>%s</b></p>%s%s%s<p><a href=\"%s\">Open the Alert Hub</a></p></div>" % (
        e(subject), ("<p>By monitor: %s.</p>" % e(agents_line)) if agents_line else "",
        ("<p><b>Open, worst first</b></p><ul>%s</ul>" % "".join("<li>%s</li>" % e(line(i)[2:]) for i in shown)) if shown else "",
        ("<p><b>Fixed this week</b></p><ul>%s</ul>" % "".join("<li>%s</li>" % e(fixed_line(i)[2:]) for i in shown_fixed)) if shown_fixed else "", e(link)))
    items = [dict(i, tag=" \u00B7 ".join([i["p"], i["agentLabel"]] + (["open %s" % mon.plural(mon.age_days(i["firstSeen"], now), "day")] if mon.age_days(i["firstSeen"], now) else []))) for i in opened]
    lead = "*Weekly summary*  \u00B7  %d open  \u00B7  %d P1  \u00B7  %d P2  \u00B7  %d P3  \u00B7  %d fixed this week" % (len(opened), n["P1"], n["P2"], n["P3"], len(fixed))
    blocks = mon._slack_message(label, items, link, lead=lead, fixed=0, empty="Weekly summary  \u00B7  nothing is open.")
    if shown_fixed:
        blocks.insert(-1, mon._context("\u2705 *Fixed this week*  \u00B7  " + "; ".join("%s: %s" % (mon._mk(i["accountName"]), mon._mk(i["title"])) for i in shown_fixed[:4]) + ("  \u00B7  +%d more" % (len(fixed) - 4) if len(fixed) > 4 else "")))
    return {"subject": subject, "text": text, "html": html_body, "slack_text": "*%s*" % subject, "slack_blocks": blocks}


# ── What the page shows ─────────────────────────────────────────────────────

def view(store, deliver, now, agents):
    """The Alert Hub page's state: the destinations (the default and each client's), every open issue of every monitor with its severity, client and what covers it, and the last sends."""
    from . import paid_master
    master, hub, default = paid_master.load(store), load(store), mon.alert_settings(store)
    rows = _open_rows(store, agents)
    covered = cover_map(rows)
    clients = [{"id": cid, "name": c["name"], "accounts": len(c.get("accounts") or {}), "route": hub["routes"].get(cid)} for cid, c in sorted((master.get("clients") or {}).items(), key=lambda kv: kv[1]["name"].lower())]
    out = []
    for r in rows:
        lead = covered.get((r["agent"], r["fingerprint"]))
        rt = route_for(master, hub, default, r["accountId"])
        out.append({"agent": r["agent"], "agentLabel": _agent_label(agents, r["agent"]), "fingerprint": r["fingerprint"], "accountId": r["accountId"], "accountName": r["accountName"], "platform": r["platform"],
                    "kind": r.get("kind"), "level": r["level"], "p": severity(r["level"]), "title": r["title"], "detail": r["detail"], "nextStep": r["nextStep"], "firstSeen": r["firstSeen"],
                    "ageDays": mon.age_days(r["firstSeen"], now), "alertedAt": r.get("alertedAt"), "clientId": (rt["client"] or {}).get("id", ""), "clientName": (rt["client"] or {}).get("name", ""),
                    "coveredBy": {"agentLabel": _agent_label(agents, lead["agent"]), "title": lead["title"], "why": why_covered(lead, r)} if lead else None,
                    "page": (agents.get(r["agent"]) or {}).get("page", "")})
    out.sort(key=lambda i: (-mon.rank(i["level"]), i["clientName"].lower(), i["accountName"].lower(), i["title"]))
    counts = _counts(out)
    return {"default": default, "senders": deliver.ready(), "clients": clients, "hasClients": bool(clients), "changes": hub["changes"][:10], "open": out[:OPEN_SHOWN], "openTotal": len(out),
            "counts": counts, "held": sum(1 for i in out if i["coveredBy"]), "log": (store.state_get(LOG_KEY) or [])[:15],
            "agents": [{"agent": a, "label": v["label"], "page": v["page"]} for a, v in agents.items()],
            "rules": {"root": sorted("%s.%s" % k for k in ROOT), "families": {n: ["%s.%s" % k for k in o] for n, o in FAMILIES.items()}, "symptoms": {a: (sorted(k) if k else None) for a, k in SYMPTOMS.items()}}}
