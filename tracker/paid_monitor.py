"""Paid Marketing Agents: the monitor engine the Brand Spend Alert and the Disapproved Ads Tracker run on (no network, no clock but the one passed in).

A monitor reads every ad account on a schedule and turns what it finds into issues: a fingerprint (the same problem in the same place has the same fingerprint every day), a level,
a title, the figures behind it and one next step. This engine keeps them and says what changed since the last run:

  * new         an issue not seen before, or one that came back after it was resolved (it counts from the day it came back, and says how many times it has);
  * worse       an open issue whose level went up since it was alerted;
  * reminder    a high issue still open a week after it was last alerted;
  * resolved    an open issue that is gone from an account that was read (an account that could not be read never resolves anything).

Rules it keeps:
  * nothing is alerted until a monitor has run once: the first run sets the baseline, marks what it found as already seen and sends nothing, so the first alert is about what turns up
    after it (a team does not want fifty emails on day one);
  * only medium and high issues are alerted (low ones are on the page and in the weekly digest);
  * an issue is marked alerted only when a channel took the alert, so a digest that went nowhere (no recipient set, Slack refused) is tried again at the next run;
  * two runs of one monitor never run at once (a lock in the store that expires, so a run that died does not block the next);
  * an account that cannot be read is named on the run and never counted as clean.

Everything outside the platform (the ad accounts, the senders, the store) is passed in, so the whole of it is tested without a network.
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime, timedelta, timezone

LEVEL_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
ALERT_FROM = "medium"            # the lowest level that is alerted
REMIND_DAYS = 7                  # a high issue still open this long after its alert is alerted again
LOCK_TTL_S = 30 * 60
RUNS_KEPT = 20
SNAP_DAYS = 120                  # days of daily figures kept for the history
MAX_EMAILS = 10
SHOWN = 12                       # issues one alert lists before it says "and N more"
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_CHANNEL = re.compile(r"^[#@]?[A-Za-z0-9._-]{1,80}$")
PLATFORM = {"google": "Google Ads", "meta": "Meta Ads"}


class BadRequest(ValueError):
    pass


def _iso(now):
    return now.isoformat()


def _parse(v):
    try:
        return datetime.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def age_days(since, now):
    """Whole days from an ISO time to `now` (at least 0), or None."""
    t = _parse(since)
    return None if t is None else max(0, int((now - t).total_seconds() // 86400))


def rank(level):
    return LEVEL_RANK.get(level, 0)


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


# ── Settings ────────────────────────────────────────────────────────────────

def clean_alert_settings(data):
    """{emails, slackChannel} from what the page sent, or BadRequest saying what is wrong."""
    raw = data.get("emails") if isinstance(data, dict) else None
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    emails = []
    for e in raw or []:
        e = str(e).strip().lower()
        if not e:
            continue
        if not _EMAIL.match(e):
            raise BadRequest("%s is not an email address." % e)
        if e not in emails:
            emails.append(e)
    if len(emails) > MAX_EMAILS:
        raise BadRequest("At most %d email addresses." % MAX_EMAILS)
    channel = str((data or {}).get("slackChannel") or "").strip()
    if channel and not _CHANNEL.match(channel):
        raise BadRequest("%s is not a Slack channel name or id." % channel)
    return {"emails": emails, "slackChannel": channel}


def alert_settings(store):
    s = store.state_get("alerts") or {}
    return {"emails": s.get("emails") or [], "slackChannel": s.get("slackChannel") or ""}


def save_alert_settings(store, data):
    s = clean_alert_settings(data)
    store.state_set("alerts", s)
    return s


# ── Issues ──────────────────────────────────────────────────────────────────

def make_issue(agent, fingerprint, account, kind, level, title, detail, next_step, data=None):
    """An issue as a monitor hands it to the engine. `account` is {id, name, platform}."""
    return {"agent": agent, "fingerprint": fingerprint, "accountId": account["id"], "accountName": account.get("name") or "", "platform": account.get("platform") or "google",
            "kind": kind, "level": level, "title": title, "detail": detail, "nextStep": next_step, "data": data or {}}


def baseline(store, agent):
    return store.state_get("baseline:" + agent)


def seen_accounts(store, agent, existing):
    """The accounts this monitor has read before. Saved by every run; for a monitor that ran before the record existed it is the accounts that have a saved issue (a clean account is not known, so
    its first issue is held back once, which is why the record is kept from now on)."""
    saved = store.state_get("seen:" + agent)
    if saved is not None:
        return set(saved.get("ids") or [])
    return {f["accountId"] for f in existing.values()}


def track(store, agent, current, ok_ids, now):
    """Hold today's issues against the saved ones. Returns {writes, pending, resolved, baselineSet, seenIds, newAccounts}:
    writes   every row to save;
    pending  [{event: new|worse|reminder, ...the issue, ageDays, reopened}] to alert, worst first (empty on the baseline run);
    resolved [{...the issue, ageDays (how long it was open)}] that went away since the last run;
    seenIds  every account read so far (to save);
    newAccounts the accounts read for the first time in this run: what is wrong there already is that account's baseline, saved as seen and never alerted as news (a Meta account that could not be
             read until now, an account added to the manager account later)."""
    first_run = baseline(store, agent) is None
    stamp = _iso(now)
    existing = {f["fingerprint"]: f for f in store.issues_all(agent)}
    writes, seen = [], set()
    for c in current:
        fp = c["fingerprint"]
        seen.add(fp)
        old = existing.get(fp)
        row = dict(c, status="open", lastSeen=stamp, resolvedAt=None)
        if old is None:
            row.update(firstSeen=stamp, reopened=0, alertedAt=None, alertedLevel=None, alert={})
        elif old["status"] == "resolved":
            row.update(firstSeen=stamp, reopened=int(old.get("reopened") or 0) + 1, alertedAt=None, alertedLevel=None, alert={})
        else:
            row.update(firstSeen=old["firstSeen"], reopened=int(old.get("reopened") or 0), alertedAt=old.get("alertedAt"), alertedLevel=old.get("alertedLevel"), alert=old.get("alert") or {})
            if old.get("alertedAt") and rank(c["level"]) > rank(old.get("alertedLevel")):
                row["alertedAt"] = None                                           # worse than what was alerted: alert it again (alertedLevel says what it was)
        writes.append(row)
    known = set() if first_run else seen_accounts(store, agent, existing)
    seen_ids, new_accounts = known | set(ok_ids), (set() if first_run else set(ok_ids) - known)
    resolved = []
    for fp, old in existing.items():
        if fp not in seen and old["status"] == "open" and old["accountId"] in ok_ids:
            writes.append(dict(old, status="resolved", resolvedAt=stamp))
            resolved.append(dict(old, status="resolved", resolvedAt=stamp, ageDays=age_days(old["firstSeen"], now)))
    if first_run:
        for w in writes:
            if w["status"] == "open" and not w.get("alertedAt"):
                w.update(alertedAt=stamp, alertedLevel=w["level"], alert={"baseline": True})
        return {"writes": writes, "pending": [], "resolved": [], "baselineSet": True, "seenIds": sorted(seen_ids), "newAccounts": set()}
    for w in writes:
        if w["status"] == "open" and w["accountId"] in new_accounts and not w.get("alertedAt"):
            w.update(alertedAt=stamp, alertedLevel=w["level"], alert={"baseline": True})
    pending = []
    for w in writes:
        if w["status"] != "open" or rank(w["level"]) < rank(ALERT_FROM):
            continue
        if not w.get("alertedAt"):
            pending.append(dict(w, event="worse" if w.get("alertedLevel") else "new"))
        elif w["level"] == "high" and (age_days(w["alertedAt"], now) or 0) >= REMIND_DAYS:
            pending.append(dict(w, event="reminder"))
    for p in pending:
        p["ageDays"] = age_days(p["firstSeen"], now)
    order = {"new": 0, "worse": 1, "reminder": 2}
    pending.sort(key=lambda p: (order[p["event"]], -rank(p["level"]), p["accountName"].lower(), p["title"]))
    return {"writes": writes, "pending": pending, "resolved": resolved, "baselineSet": False, "seenIds": sorted(seen_ids), "newAccounts": new_accounts}


def mark_alerted(rows, pending, result, now):
    """The saved rows with the pending issues marked alerted (only call it when a channel took the alert)."""
    done = {p["fingerprint"] for p in pending}
    stamp = _iso(now)
    return [dict(w, alertedAt=stamp, alertedLevel=w["level"], alert={"email": result["email"]["status"], "slack": result["slack"]["status"]}) if w["fingerprint"] in done else w for w in rows]


# ── The alert ───────────────────────────────────────────────────────────────

EVENT_WORD = {"new": "New", "worse": "Worse than before", "reminder": "Still open"}


def _age_text(p):
    n = p.get("ageDays")
    bits = []
    if p["event"] == "worse":
        bits.append("was %s when last alerted" % (p.get("alertedLevel") or "lower"))
    if n is not None and n >= 1:
        bits.append("open %s" % plural(n, "day"))
    if p.get("reopened"):
        bits.append("has come back %s" % plural(p["reopened"], "time"))
    return " · ".join(bits)


DOT = {"high": "\U0001F534", "medium": "\U0001F7E0", "low": "\U0001F7E1", "info": "\u26AA"}
ACCOUNTS_SHOWN = 6               # accounts one Slack alert lists, worst first, before it says "+N more"
PER_ACCOUNT = 3                  # issues listed under an account in Slack
CLEAR = "\U0001F7E2"


def _mk(v):
    """Slack's markup text: the three characters it reads as markup are escaped."""
    return str(v if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def _context(text):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:2900]}]}


def _slack_message(label, items, link, *, lead, header_prefix="", fixed=0, empty="Nothing is open right now."):
    """The Slack blocks of an alert, short: a header with the worst level's dot, one line of counts, then each account (worst first) with its worst issues as one line each, and the button to the
    page. `items` are dicts with accountId, accountName, platform, level, title and an optional tag."""
    top = DOT[max(items, key=lambda i: rank(i["level"]))["level"]] if items else CLEAR
    groups = sorted(({} if not items else _group(items)).values(), key=lambda g: (-max(rank(i["level"]) for i in g), -len(g), g[0]["accountName"].lower()))
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": ("%s%s %s" % (header_prefix, top, label))[:150], "emoji": True}},
              _context(lead if items else empty), {"type": "divider"}]
    for g in groups[:ACCOUNTS_SHOWN]:
        g = sorted(g, key=lambda i: (-rank(i["level"]), i["title"]))
        lines = ["%s *%s*  \u00B7  %s" % (DOT[g[0]["level"]], _mk(g[0]["accountName"]), PLATFORM.get(g[0].get("platform") or "google", g[0].get("platform") or "google"))]
        lines += ["\u2022  %s%s" % (_mk(i["title"]), ("  _%s_" % _mk(i["tag"])) if i.get("tag") else "") for i in g[:PER_ACCOUNT]]
        if len(g) > PER_ACCOUNT:
            lines.append("_+%d more_" % (len(g) - PER_ACCOUNT))
        blocks.append(_section("\n".join(lines)))
    rest = groups[ACCOUNTS_SHOWN:]
    foot = (["+%d more %s, %s" % (len(rest), "account" if len(rest) == 1 else "accounts", plural(sum(len(g) for g in rest), "issue"))] if rest else []) + (["\u2705 %d fixed since the last run" % fixed] if fixed else [])
    if foot:
        blocks.append(_context("  \u00B7  ".join(foot)))
    blocks.append({"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": ("Open " + label)[:70]}, "url": link, "action_id": "open_page", "style": "primary"}]})
    return blocks


def _group(items):
    out = {}
    for i in items:
        out.setdefault(i.get("accountId") or i["accountName"], []).append(i)
    return out


def digest(label, pending, resolved, link):
    """{subject, text, html, slack_text, slack_blocks} for what changed. `link` is the monitor page's address."""
    counts = {e: sum(1 for p in pending if p["event"] == e) for e in EVENT_WORD}
    accounts = len({p["accountId"] for p in pending})
    parts = ["%d new" % counts["new"]] if counts["new"] else []
    parts += ["%d worse" % counts["worse"]] if counts["worse"] else []
    parts += ["%d still open" % counts["reminder"]] if counts["reminder"] else []
    top = "HIGH: " if any(p["level"] == "high" for p in pending) else ""
    subject = "%s%s: %s (%s)" % (top, label, ", ".join(parts) or "nothing new", plural(accounts, "account"))
    shown = pending[:SHOWN]
    more = len(pending) - len(shown)
    fixed = resolved[:SHOWN]
    lines = []
    for p in shown:
        lines.append("- [%s] %s (%s)\n    %s\n    %s%s" % (EVENT_WORD[p["event"]].upper(), p["accountName"], PLATFORM.get(p["platform"], p["platform"]), p["title"], p["detail"],
                                                         ("\n    Next: " + p["nextStep"]) if p.get("nextStep") else ""))
        age = _age_text(p)
        if age:
            lines[-1] += "\n    (%s)" % age
    text = "\n".join(lines + (["...and %d more on the page." % more] if more else [])
                     + ([""] + ["Fixed since the last run:"] + ["- %s: %s%s" % (r["accountName"], r["title"], (" (was open %s)" % plural(r["ageDays"], "day")) if r.get("ageDays") else "") for r in fixed] if fixed else [])
                     + ["", "Open the page: " + link])
    e = _html.escape
    rows = "".join("<li><b>%s</b> (%s) <span style=\"color:%s\">%s</span><br>%s<br>%s%s%s</li>" % (
        e(p["accountName"]), e(PLATFORM.get(p["platform"], p["platform"])), "#b91c1c" if p["level"] == "high" else "#b45309", e(EVENT_WORD[p["event"]]), e(p["title"]), e(p["detail"]),
        ("<br><i>Next: %s</i>" % e(p["nextStep"])) if p.get("nextStep") else "", ("<br><small>%s</small>" % e(_age_text(p))) if _age_text(p) else "") for p in shown)
    fixed_html = ("<p><b>Fixed since the last run</b></p><ul>%s</ul>" % "".join("<li>%s: %s</li>" % (e(r["accountName"]), e(r["title"])) for r in fixed)) if fixed else ""
    html_body = ("<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.5\"><p><b>%s</b></p><ul>%s</ul>%s%s<p><a href=\"%s\">Open the page</a></p></div>"
                 % (e(subject), rows, ("<p>...and %d more on the page.</p>" % more) if more else "", fixed_html, e(link)))
    tags = {"new": "", "worse": "worse", "reminder": "still open"}
    items = [dict(p, tag=tags[p["event"]]) for p in pending]
    lead = "  \u00B7  ".join(([ "*%d new*" % counts["new"]] if counts["new"] else []) + (["%d worse" % counts["worse"]] if counts["worse"] else []) + (["%d still open" % counts["reminder"]] if counts["reminder"] else [])
                           + [plural(accounts, "account")])
    blocks = _slack_message(label, items, link, lead=lead, fixed=len(resolved))
    dot = DOT[max(pending, key=lambda p: rank(p["level"]))["level"]] if pending else CLEAR
    slack_text = "%s %s: %s in %s" % (dot, label, ", ".join(parts) or "nothing new", plural(accounts, "account"))
    return {"subject": subject, "text": text, "html": html_body, "slack_text": slack_text, "slack_blocks": blocks}


def send(store, message, deliver):
    """Send an alert message to the saved recipients and channel: {email: {status, error?}, slack: {...}, sent}; `sent` is True when at least one channel took it."""
    s = alert_settings(store)
    email = (deliver.attempt(lambda: deliver.send_email(message["subject"], message["text"], message["html"], s["emails"]))
             if s["emails"] else {"status": "not_configured", "error": "No email address is set."})
    slack = (deliver.attempt(lambda: deliver.send_slack(s["slackChannel"], message["slack_text"], message["slack_blocks"]))
             if s["slackChannel"] else {"status": "not_configured", "error": "No Slack channel is set."})
    return {"email": email, "slack": slack, "sent": "sent" in (email["status"], slack["status"])}


def test_alert(store, label, link, deliver):
    """A made-up issue through the real senders, to prove the wiring."""
    p = {"event": "new", "level": "high", "accountId": "0", "accountName": "Test account (not real)", "platform": "google", "title": "This is a test alert",
         "detail": "Nothing was found. It only proves that the recipients and the Slack channel work.", "nextStep": "", "ageDays": 0, "reopened": 0}
    m = digest(label, [p], [], link)
    m["subject"] = "TEST: " + m["subject"]
    m["slack_text"] = "TEST " + m["slack_text"]
    m["slack_blocks"][0]["text"]["text"] = "\U0001F9EA Test  \u00B7  " + label
    return send(store, m, deliver)


def preview_alert(store, agent, label, link, deliver):
    """What is open right now, in every account the monitor has read, sent as an alert marked TEST: the real look of an alert with real findings (only what would be alerted, medium and above),
    and it says it is not new. Marks nothing alerted. {email, slack, sent, issues, accounts}."""
    open_rows = [i for i in store.issues_all(agent) if i["status"] == "open" and rank(i["level"]) >= rank(ALERT_FROM)]
    accounts = len({i["accountId"] for i in open_rows})
    by_level = "  ".join("%s %d" % (DOT[lv], n) for lv in ("high", "medium") for n in [sum(1 for i in open_rows if i["level"] == lv)] if n)
    lead = "*Test*  \u00B7  what is open now, not new  \u00B7  %s in %s  \u00B7  %s" % (plural(len(open_rows), "issue"), plural(accounts, "account"), by_level)
    blocks = _slack_message(label, open_rows, link, lead=lead, header_prefix="\U0001F9EA ", empty="Test  \u00B7  nothing is open right now.")
    text = "TEST: %s open in %s (not new)" % (plural(len(open_rows), "issue"), plural(accounts, "account"))
    msg = {"subject": "TEST: %s, %s open in %s" % (label, plural(len(open_rows), "issue"), plural(accounts, "account")),
           "text": "\n".join(["%s: %s open now in %s (a test, not new)." % (label, len(open_rows), plural(accounts, "account")), ""]
                            + ["- [%s] %s: %s" % (i["level"].upper(), i["accountName"], i["title"]) for i in sorted(open_rows, key=lambda i: (-rank(i["level"]), i["accountName"].lower()))[:SHOWN]]
                            + ["", "Open the page: " + link]),
           "html": "<p>%s</p>" % _html.escape(text), "slack_text": "\U0001F9EA " + text, "slack_blocks": blocks}
    res = send(store, msg, deliver)
    return dict(res, issues=len(open_rows), accounts=accounts)


# ── A run ───────────────────────────────────────────────────────────────────

def _keep_run(store, agent, run):
    runs = store.state_get("runs:" + agent) or []
    store.state_set("runs:" + agent, [run] + runs[:RUNS_KEPT - 1])


def run_monitor(store, spec, *, trigger, link, deliver, accounts, groups, problems=(), now_fn=None):
    """One run of a monitor. `spec` is {agent, label, collect}; collect(accounts, groups) returns
    {issues: [make_issue...], snaps: {account id: {...}}, okIds: set of the accounts that were read, unreadable: [{accountId, name, error}]}.
    Returns the run record ({status: "busy"} when another run of the monitor is going)."""
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    agent = spec["agent"]
    if not store.claim("lock:" + agent, LOCK_TTL_S):
        return {"status": "busy"}
    started = now_fn()
    try:
        got = spec["collect"](accounts, groups)
        now = now_fn()
        tracked = track(store, agent, got["issues"], set(got["okIds"]), now)
        run = {"status": "done", "trigger": trigger, "startedAt": _iso(started), "finishedAt": _iso(now), "accounts": len(accounts), "read": len(got["okIds"]),
               "unreadable": (got.get("unreadable") or [])[:30], "unreadableCount": len(got.get("unreadable") or []), "problems": list(problems), "issues": len(got["issues"]),
               "new": sum(1 for p in tracked["pending"] if p["event"] == "new"), "worse": sum(1 for p in tracked["pending"] if p["event"] == "worse"),
               "reminders": sum(1 for p in tracked["pending"] if p["event"] == "reminder"), "resolved": len(tracked["resolved"]), "baseline": bool(tracked["baselineSet"]) or baseline(store, agent) is not None,
               "alerted": 0, "email": None, "slack": None, "firstRead": len(tracked["newAccounts"])}
        writes = tracked["writes"]
        if tracked["pending"]:
            res = send(store, digest(spec["label"], tracked["pending"], tracked["resolved"], link), deliver)
            run.update(email=res["email"], slack=res["slack"])
            if res["sent"]:
                writes = mark_alerted(writes, tracked["pending"], res, now_fn())
                run["alerted"] = len(tracked["pending"])
        store.issues_put(writes)
        store.state_set("seen:" + agent, {"ids": tracked["seenIds"]})
        if got.get("snaps"):
            store.snaps_put(agent, now.date().isoformat(), got["snaps"])
            store.snaps_prune(agent, (now.date() - timedelta(days=SNAP_DAYS)).isoformat())
        if tracked["baselineSet"]:
            store.state_set("baseline:" + agent, {"at": _iso(now), "by": trigger})
        _keep_run(store, agent, run)
        return run
    except Exception as e:                                                          # noqa: BLE001
        run = {"status": "failed", "trigger": trigger, "startedAt": _iso(started), "finishedAt": _iso(now_fn()), "error": ("%s: %s" % (type(e).__name__, e))[:300]}
        _keep_run(store, agent, run)
        return run
    finally:
        store.release("lock:" + agent)


# ── The weekly summary ──────────────────────────────────────────────────────

DIGEST_OPEN = 15                 # open issues the weekly summary lists
DIGEST_FIXED = 10
FIXED_WINDOW_DAYS = 7


def weekly_digest(label, issues, now, link):
    """{subject, text, html, slack_text, slack_blocks, open, fixed} for the week: every open issue (low ones too, which are never alerted one by one) and what was fixed in the last seven
    days with how long it had been open, or None when there is nothing to say. Sending it marks nothing alerted."""
    opened = sorted([i for i in issues if i["status"] == "open"], key=lambda i: (-rank(i["level"]), i["accountName"].lower(), i["title"]))
    cut = now - timedelta(days=FIXED_WINDOW_DAYS)
    fixed = sorted([i for i in issues if i["status"] == "resolved" and (_parse(i.get("resolvedAt")) or cut) > cut], key=lambda i: (i["accountName"].lower(), i["title"]))
    if not opened and not fixed:
        return None
    high = sum(1 for i in opened if i["level"] == "high")
    subject = "%s: weekly summary, %d open%s, %d fixed this week" % (label, len(opened), (" (%d high)" % high) if high else "", len(fixed))
    shown, shown_fixed = opened[:DIGEST_OPEN], fixed[:DIGEST_FIXED]

    def line(i):
        age = age_days(i["firstSeen"], now)
        return "- [%s] %s: %s%s" % (i["level"].upper(), i["accountName"], i["title"], (" (open %s)" % plural(age, "day")) if age else "")

    def fixed_line(i):
        age = age_days(i["firstSeen"], _parse(i["resolvedAt"]))
        return "- %s: %s%s" % (i["accountName"], i["title"], (" (was open %s)" % plural(age, "day")) if age else "")
    text = "\n".join(["%d open, %d of them high; %d fixed in the last 7 days." % (len(opened), high, len(fixed)), ""]
                     + (["Open:"] + [line(i) for i in shown] + (["...and %d more on the page." % (len(opened) - len(shown))] if len(opened) > len(shown) else []) + [""] if shown else [])
                     + (["Fixed this week:"] + [fixed_line(i) for i in shown_fixed] + [""] if shown_fixed else []) + ["Open the page: " + link])
    e = _html.escape
    html_body = ("<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.5\"><p><b>%s</b></p>%s%s<p><a href=\"%s\">Open the page</a></p></div>" % (
        e(subject), ("<p><b>Open</b></p><ul>%s</ul>%s" % ("".join("<li>%s</li>" % e(line(i)[2:]) for i in shown), ("<p>...and %d more on the page.</p>" % (len(opened) - len(shown))) if len(opened) > len(shown) else "")) if shown else "",
        ("<p><b>Fixed this week</b></p><ul>%s</ul>" % "".join("<li>%s</li>" % e(fixed_line(i)[2:]) for i in shown_fixed)) if shown_fixed else "", e(link)))
    items = [dict(i, tag=("open %s" % plural(age_days(i["firstSeen"], now), "day")) if age_days(i["firstSeen"], now) else "") for i in opened]
    lead = "*Weekly summary*  \u00B7  %s%s  \u00B7  %d fixed this week" % ("%d open" % len(opened), (" (%d high)" % high) if high else "", len(fixed))
    blocks = _slack_message(label, items, link, lead=lead, fixed=0, empty="Weekly summary  \u00B7  nothing is open.")
    if shown_fixed:
        blocks.insert(-1, _context("\u2705 *Fixed this week*  \u00B7  " + "; ".join("%s: %s" % (_mk(i["accountName"]), _mk(i["title"])) for i in shown_fixed[:4]) + ("  \u00B7  +%d more" % (len(fixed) - 4) if len(fixed) > 4 else "")))
    return {"subject": subject, "text": text, "html": html_body, "slack_text": "*%s*" % subject, "slack_blocks": blocks, "open": len(opened), "fixed": len(fixed)}
