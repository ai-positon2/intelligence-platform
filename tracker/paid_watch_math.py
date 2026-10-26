"""Paid Marketing Agents: Account Watch, what changed in an account that nobody may have meant (no network, no clock: `now` is passed in).

An account is passed in as plain data (see tracker/paid_watch.py for how it is read):

  Google  status    the account's status as the manager account lists it (ENABLED, SUSPENDED, CANCELED, CLOSED) or None
          orders    [{status, limit, served, endsAt}] the account budget orders (invoiced accounts only), in the account's currency, or None
          changes   [{at, user, client, kind, op, label, was, now}] the change history of the last days (kind is budget, bidding or other), or None
          actions   [{id, name, status, primary, include}] the conversion actions, or None
          users     [{email, role}] who can open the account, or None
  Meta    status    {code, disableReason, spendCap, spent} the ad account's status and spending limit (the amounts in the currency's main unit)
          pages     [{id, name, published}] the Pages the ad account can promote, or None
          activity  [{at, actor, type, what, object, was, now}] the account's activity log of the last days, or None
  history           the monitor's earlier daily readings, oldest first: [{day, users, actions, pages}]

Nothing here guesses at who is "unexpected": a person outside Position2's own domain, a change large enough to matter and a user or a Page that was there and is not are what is flagged, each with
what the platform said and when. What the platforms do not show is said in NOT_MEASURED, never left out silently.
"""
from __future__ import annotations

import datetime as dt

LEVEL_RANK = {"high": 3, "medium": 2, "low": 1}
TEAM_DOMAINS = ("position2.com",)
CHANGE_DAYS = 3
BUDGET_MOVE = 0.30                  # a budget that moved by this share, either way, is worth a look
BUDGET_BIG_UP, BUDGET_BIG_DOWN = 2.0, 0.3
ORDER_MEDIUM, ORDER_HIGH = 0.85, 0.95
ORDER_END_DAYS = 3
CAP_MEDIUM, CAP_HIGH = 0.90, 0.98
HISTORY_DAYS = 14
SHOWN = 6                           # changes named in one finding's evidence

META_STATUS = {1: ("ACTIVE", None), 2: ("disabled", "high"), 3: ("unsettled (a payment failed)", "high"), 7: ("under risk review", "high"), 8: ("settling a payment", "medium"),
               9: ("in a grace period for a payment", "high"), 100: ("pending closure", "high"), 101: ("closed", "high"), 201: ("active", None), 202: ("closed", "high")}
GOOGLE_STATUS = {"SUSPENDED": "suspended", "CANCELED": "canceled", "CLOSED": "closed"}

RULES = {
    "account_status": {"level": "high", "area": "Account", "label": "The account is not active",
                       "fix": "Open the account in Google Ads or Meta Business Support and read the notice: it names what stopped it (a payment that failed, a policy breach, a request for more information). Fix that first, because nothing runs until it is lifted."},
    "account_gone": {"level": "high", "area": "Account", "label": "The account is no longer in the manager account",
                     "fix": "The manager account lost its link to this account (the client removed it, or it was closed). Ask the client whether it was meant, and ask for the link again."},
    "spend_limit": {"level": "medium", "area": "Payment", "label": "The spending limit is nearly used",
                    "fix": "Ads stop when the account budget order or the spend cap is reached. Ask the client to raise the limit or add a new order before it ends, and check how much a day is being spent."},
    "budget_change": {"level": "medium", "area": "Budgets", "label": "A budget was changed a lot",
                      "fix": "Ask who made the change and why: if it was meant, note it; if not, put the budget back. A budget that doubles overnight spends twice as fast before anyone looks."},
    "bid_change": {"level": "medium", "area": "Bids", "label": "A bid strategy or goal was changed",
                   "fix": "Ask who changed the bidding or the optimisation goal and why. A change restarts the learning, so only a planned one should be left in place."},
    "goal_change": {"level": "medium", "area": "Conversion goals", "label": "A conversion goal was changed",
                    "fix": "Check what the conversion action counts now: a goal that stopped counting, or one that was made primary, changes what every campaign optimises for. Put it back if it was not planned."},
    "access_change": {"level": "medium", "area": "User access", "label": "Who can open the account changed",
                      "fix": "Check each new person against the client's request, and take away access that nobody asked for. An administrator can change anything, including the billing."},
    "outside_editor": {"level": "medium", "area": "User access", "label": "Someone outside Position2 made changes",
                       "fix": "Ask the client who that is and what was changed. If it is a person at the client, agree who may edit; if it is nobody they know, remove the access and change the passwords."},
    "page_lost": {"level": "high", "area": "Pages", "label": "A Page is no longer connected",
                  "fix": "Ads that use the Page stop. Open Business Settings, then Pages: ask the Page owner to give the ad account access again (or publish the Page), then check the ads that use it."},
    "activity": {"level": "medium", "area": "Activity", "label": "A change that touches payment, access or limits",
                 "fix": "Open the account's activity history in Meta and check who did it and why. If it was not planned, undo it and remove the access of whoever made it."},
}
ORDER = ["account_status", "account_gone", "page_lost", "spend_limit", "budget_change", "goal_change", "bid_change", "access_change", "outside_editor", "activity"]

NOT_MEASURED = [
    "A card that was declined: the platforms show the account's status and its limits, not each payment. A failed payment shows up here as a status that is not active.",
    "Changes made before the last readings: the change history is read for the last 3 days, and who has access, the conversion goals and the Pages are held against the monitor's own earlier daily readings, so they are judged from the second day.",
    "Whether a person outside Position2 is meant to be there: it flags them and says what they did, and the team decides.",
    "Edits Google or Meta make to an account on their own, and changes made in tools that do not write to the change history.",
]


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def money(v, cur):
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "INR": "₹", "NZD": "NZ$", "SGD": "S$"}.get(cur)
    body = "{:,.0f}".format(v) if abs(v) >= 100 else "{:.2f}".format(v)
    return (sym + body) if sym else "%s %s" % (body, cur or "")


def _finding(rule, key, title, detail, *, level=None, evidence=None):
    r = RULES[rule]
    return {"rule": rule, "key": key, "level": level or r["level"], "area": r["area"], "title": title, "detail": detail, "fix": r["fix"], "evidence": evidence or []}


def _check(key, label, state, note):
    return {"key": key, "label": label, "state": state, "note": note}


def outside(user):
    """True for a person whose address is not on Position2's own domain; a blank or a system source is not a person."""
    u = str(user or "").strip().lower()
    return "@" in u and not any(u.endswith("@" + d) for d in TEAM_DOMAINS)


def _recent(items, now, field="at"):
    cut = now - dt.timedelta(days=CHANGE_DAYS)
    out = []
    for c in items or []:
        try:
            at = dt.datetime.fromisoformat(str(c.get(field) or "").replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt.timezone.utc)
        if at >= cut:
            out.append((at, c))
    return sorted(out, key=lambda p: p[0], reverse=True)


def _line(c, cur):
    who = c.get("user") or c.get("actor") or "an unknown source"
    return "%s by %s: %s" % (str(c.get("at") or "")[:16].replace("T", " "), who, c.get("label") or c.get("what") or "a change")


def _newest_key(prefix, rows):
    return "%s:%s" % (prefix, str(rows[0][1].get("at") or "")[:19].replace(" ", "T")) if rows else prefix


def _moved(was, now):
    """(level or None) of a budget going from `was` to `now`."""
    if was is None or now is None or was <= 0:
        return None
    ratio = now / was
    if ratio >= BUDGET_BIG_UP or ratio <= BUDGET_BIG_DOWN:
        return "high"
    if abs(ratio - 1) >= BUDGET_MOVE:
        return "medium"
    return None


# ── Status and payment ──────────────────────────────────────────────────────

def _google_status(a):
    s = a.get("status")
    if s is None:
        return [], _check("status", "The account is active", "unknown", "The manager account did not give this account's status.")
    if s in GOOGLE_STATUS:
        return [_finding("account_status", "status", "The account is %s" % GOOGLE_STATUS[s], "The manager account lists it as %s, so nothing runs in it." % s.lower(), evidence=["Status: %s" % s])], \
            _check("status", "The account is active", "problem", "%s." % GOOGLE_STATUS[s].capitalize())
    return [], _check("status", "The account is active", "ok", "Enabled.")


def _meta_status(a):
    st = a.get("status") or {}
    code = st.get("code")
    if code is None:
        return [], _check("status", "The account is active", "unknown", "Meta did not give the ad account's status.")
    word, level = META_STATUS.get(int(code), ("in status %s" % code, "medium"))
    found = []
    if level:
        why = st.get("disableReason")
        found.append(_finding("account_status", "status", "The ad account is %s" % word, "Meta's status for it is %s%s." % (word, (" (reason code %s)" % why) if why else ""),
                              level=level, evidence=["Status code: %s" % code] + (["Disable reason code: %s" % why] if why else [])))
    cap, spent = _f(st.get("spendCap")), _f(st.get("spent"))
    check_note = "Active."
    if cap and cap > 0 and spent is not None:
        share = spent / cap
        cur = a.get("currency") or "USD"
        if share >= CAP_MEDIUM:
            found.append(_finding("spend_limit", "cap", "%d percent of the spend cap is used" % int(share * 100), "%s spent of a %s cap: ads stop at the cap." % (money(spent, cur), money(cap, cur)),
                                  level="high" if share >= CAP_HIGH else "medium", evidence=["Spent: %s" % money(spent, cur), "Cap: %s" % money(cap, cur)]))
    if found and found[0]["rule"] == "account_status":
        return found, _check("status", "The account is active", "problem", "%s." % word.capitalize())
    return found, _check("status", "The account is active", "ok", check_note)


def _orders(a, now):
    orders = a.get("orders")
    if orders is None:
        return [], _check("limit", "The spending limit has room", "unknown", "The account budget orders could not be read.")
    cur = a.get("currency") or "USD"
    found = []
    live = [o for o in orders if str(o.get("status") or "") == "APPROVED" and _f(o.get("limit"))]
    for o in live:
        limit, served = _f(o["limit"]), _f(o.get("served")) or 0.0
        share = served / limit if limit else 0.0
        ends = str(o.get("endsAt") or "")[:10]
        days_left = None
        if ends:
            try:
                days_left = (dt.date.fromisoformat(ends) - now.date()).days
            except ValueError:
                days_left = None
        if share >= ORDER_MEDIUM:
            found.append(_finding("spend_limit", "order", "%d percent of the account budget order is used" % int(share * 100), "%s served of a %s order: ads stop when it is used." % (money(served, cur), money(limit, cur)),
                                  level="high" if share >= ORDER_HIGH else "medium", evidence=["Served: %s" % money(served, cur), "Limit: %s" % money(limit, cur)] + (["Ends: %s" % ends] if ends else [])))
        elif days_left is not None and 0 <= days_left <= ORDER_END_DAYS:
            found.append(_finding("spend_limit", "order_end", "The account budget order ends in %s" % plural(days_left, "day"), "It ends on %s; ads stop then unless a new order is in place." % ends, evidence=["Ends: %s" % ends]))
    if found:
        return found, _check("limit", "The spending limit has room", "problem", "A budget order is nearly used or about to end.")
    if not live:
        return [], _check("limit", "The spending limit has room", "ok", "No account budget order with a limit (the account is on automatic payments or monthly invoicing).")
    return [], _check("limit", "The spending limit has room", "ok", "%s has room." % plural(len(live), "account budget order"))


# ── Changes ─────────────────────────────────────────────────────────────────

def _google_changes(a, now):
    changes = a.get("changes")
    if changes is None:
        return [], [_check("budgets", "Budgets and bids were not changed a lot", "unknown", "The change history could not be read."),
                    _check("editors", "Only Position2 edited the account", "unknown", "The change history could not be read.")]
    cur = a.get("currency") or "USD"
    rows = _recent(changes, now)
    found = []
    big = []
    for at, c in rows:
        if c.get("kind") == "budget":
            lvl = _moved(_f(c.get("was")), _f(c.get("now"))) if (c.get("was") is not None and c.get("now") is not None) else ("medium" if c.get("op") in ("CREATE", "REMOVE") or "changed" in str(c.get("label")) else None)
            if lvl:
                big.append((lvl, at, c))
    if big:
        level = "high" if any(l == "high" for l, _a, _c in big) else "medium"
        found.append(_finding("budget_change", _newest_key("budget_change", [(b[1], b[2]) for b in big]), "%s in the last %s" % (plural(len(big), "big budget change"), plural(CHANGE_DAYS, "day")),
                              "A budget moved by 30 percent or more (or was added or removed)." , level=level, evidence=[_line(c, cur) for _l, _a, c in big[:SHOWN]]))
    bids = [(at, c) for at, c in rows if c.get("kind") == "bidding"]
    if bids:
        found.append(_finding("bid_change", _newest_key("bid_change", bids), "%s in the last %s" % (plural(len(bids), "bidding change"), plural(CHANGE_DAYS, "day")),
                              "A campaign's bid strategy or its target was changed.", evidence=[_line(c, cur) for _a, c in bids[:SHOWN]]))
    by_user = {}
    for at, c in rows:
        if outside(c.get("user")):
            by_user.setdefault(c["user"], []).append((at, c))
    for user, evs in sorted(by_user.items()):
        found.append(_finding("outside_editor", "editor:%s" % user, "%s outside Position2 made %s" % (user, plural(len(evs), "change")),
                              "%s is not on Position2's domain and changed the account in the last %s." % (user, plural(CHANGE_DAYS, "day")), evidence=[_line(c, cur) for _a, c in evs[:SHOWN]]))
    checks = [_check("budgets", "Budgets and bids were not changed a lot", "problem" if (big or bids) else "ok", ("%s in the last %d days." % (plural(len(big) + len(bids), "change"), CHANGE_DAYS)) if (big or bids) else
                     "%s read from the last %d days; none moved a budget a lot or touched a bid strategy." % (plural(len(rows), "change"), CHANGE_DAYS)),
              _check("editors", "Only Position2 edited the account", "problem" if by_user else "ok", ("%s outside Position2 edited it." % plural(len(by_user), "person", "people")) if by_user else "Nobody outside Position2 edited it.")]
    return found, checks


def _history_last(history, field, today):
    """The most recent earlier reading that has `field`: (its value, its day) or (None, None)."""
    lo = today - dt.timedelta(days=HISTORY_DAYS)
    for h in reversed(history or []):
        try:
            d = dt.date.fromisoformat(str(h.get("day") or "")[:10])
        except ValueError:
            continue
        if d < today and d >= lo and h.get(field) is not None:
            return h[field], d
    return None, None


def _actions(a, history, today):
    acts = a.get("actions")
    if acts is None:
        return [], _check("goals", "Conversion goals are as they were", "unknown", "The conversion actions could not be read.")
    prev, _d = _history_last(history, "actions", today)
    if prev is None:
        return [], _check("goals", "Conversion goals are as they were", "unknown", "%s read today; there is no earlier reading to compare with yet (it compares from tomorrow)." % plural(len(acts), "action"))
    now = {x["id"]: x for x in acts}
    ev, level = [], "medium"
    for aid, was in prev.items():
        cur = now.get(aid)
        name = was.get("name") or aid
        if cur is None:
            ev.append("\"%s\" is gone (it was %s%s)" % (name, str(was.get("status") or "").lower(), ", primary" if was.get("primary") else ""))
            level = "high" if was.get("primary") else level
            continue
        if cur.get("status") != was.get("status"):
            ev.append("\"%s\" went from %s to %s" % (name, str(was.get("status") or "").lower(), str(cur.get("status") or "").lower()))
            level = "high" if was.get("primary") and cur.get("status") in ("HIDDEN", "REMOVED") else level
        if bool(cur.get("primary")) != bool(was.get("primary")):
            ev.append("\"%s\" %s primary" % (name, "became" if cur.get("primary") else "is no longer"))
        if bool(cur.get("include")) != bool(was.get("include")):
            ev.append("\"%s\" %s counted in the Conversions column" % (name, "is now" if cur.get("include") else "is no longer"))
            level = "high" if was.get("primary") and not cur.get("include") else level
    for aid, cur in now.items():
        if aid not in prev:
            ev.append("\"%s\" was added" % (cur.get("name") or aid))
    if ev:
        return [_finding("goal_change", "goals:%s" % "|".join(sorted(ev))[:60].replace(" ", "_"), "%s changed since the last reading" % plural(len(ev), "conversion goal"), "What the conversion actions count is not what it was.", level=level, evidence=ev[:SHOWN])], \
            _check("goals", "Conversion goals are as they were", "problem", plural(len(ev), "change") + " since the last reading.")
    return [], _check("goals", "Conversion goals are as they were", "ok", "%s unchanged since the last reading." % plural(len(acts), "action"))


def _users(a, history, today):
    users = a.get("users")
    if users is None:
        return [], _check("access", "Nobody new can open the account", "unknown", "Who can open the account could not be read.")
    prev, _d = _history_last(history, "users", today)
    if prev is None:
        return [], _check("access", "Nobody new can open the account", "unknown", "%s read today; there is no earlier reading to compare with yet (it compares from tomorrow)." % plural(len(users), "person", "people"))
    now = {u["email"]: u.get("role") or "" for u in users if u.get("email")}
    ev, level = [], "medium"
    for email, role in sorted(now.items()):
        if email not in prev:
            ev.append("%s was added as %s" % (email, role.lower().replace("_", " ") or "a user"))
            if role == "ADMIN":
                level = "high"
        elif role != prev[email]:
            ev.append("%s went from %s to %s" % (email, str(prev[email]).lower().replace("_", " "), role.lower().replace("_", " ")))
            if role == "ADMIN":
                level = "high"
    for email in sorted(prev):
        if email not in now:
            ev.append("%s was removed" % email)
    if ev:
        return [_finding("access_change", "access:%s" % "|".join(ev)[:60].replace(" ", "_"), "Access changed: %s" % plural(len(ev), "change"), "Who can open the account is not who could at the last reading.", level=level, evidence=ev[:SHOWN * 2])], \
            _check("access", "Nobody new can open the account", "problem", plural(len(ev), "change") + " since the last reading.")
    return [], _check("access", "Nobody new can open the account", "ok", "%s, the same as at the last reading." % plural(len(now), "person with access", "people with access"))


# ── Meta ────────────────────────────────────────────────────────────────────

def _pages(a, history, today):
    pages = a.get("pages")
    if pages is None:
        return [], _check("pages", "The Pages are still connected", "unknown", "The Pages could not be read.")
    found, ev = [], []
    for p in pages:
        if p.get("published") is False:
            ev.append("\"%s\" is not published" % (p.get("name") or p["id"]))
    prev, _d = _history_last(history, "pages", today)
    if prev is not None:
        have = {p["id"] for p in pages}
        for pid, name in sorted(prev.items()):
            if pid not in have:
                ev.append("\"%s\" was connected at the last reading and is not now" % name)
    if ev:
        return [_finding("page_lost", "pages:%s" % "|".join(ev)[:60].replace(" ", "_"), "%s lost or unpublished" % plural(len(ev), "Page"), "Ads that run as these Pages stop.", evidence=ev[:SHOWN])], \
            _check("pages", "The Pages are still connected", "problem", plural(len(ev), "Page") + " lost or unpublished.")
    if prev is None:
        return [], _check("pages", "The Pages are still connected", "unknown", "%s read today; there is no earlier reading to compare with yet (it compares from tomorrow)." % plural(len(pages), "Page"))
    return [], _check("pages", "The Pages are still connected", "ok", "%s, the same as at the last reading, all published." % plural(len(pages), "Page"))


ACTIVITY_RULES = (("add_user", "access", "high"), ("remove_user", "access", "medium"), ("role", "access", "medium"), ("spend_limit", "limit", "medium"), ("funding", "payment", "medium"),
                  ("payment", "payment", "medium"), ("update_status", "status", "high"), ("account_update", "status", "medium"))


def _activity(a, now):
    acts = a.get("activity")
    if acts is None:
        return [], _check("activity", "Nothing touched payment, access or limits", "unknown", "The account's activity history could not be read.")
    cur = a.get("currency") or "USD"
    rows = _recent(acts, now)
    found, hits, budgets, bids = [], [], [], []
    for at, c in rows:
        t = str(c.get("type") or "").lower()
        if "budget" in t:
            lvl = _moved(_f(c.get("was")), _f(c.get("now"))) if (c.get("was") is not None and c.get("now") is not None) else None
            if lvl or (c.get("was") is None and ("update" in t or "create" in t or "delete" in t)):
                budgets.append((lvl or "medium", at, c))
        elif any(w in t for w in ("bid", "optimization_goal", "bid_strategy")):
            bids.append((at, c))
        else:
            for word, _area, lvl in ACTIVITY_RULES:
                if word in t:
                    hits.append((lvl, at, c))
                    break
    if budgets:
        found.append(_finding("budget_change", _newest_key("budget_change", [(b[1], b[2]) for b in budgets]), "%s in the last %s" % (plural(len(budgets), "big budget change"), plural(CHANGE_DAYS, "day")),
                              "A budget moved by 30 percent or more (or was added or removed).", level="high" if any(b[0] == "high" for b in budgets) else "medium", evidence=[_line(c, cur) for _l, _a, c in budgets[:SHOWN]]))
    if bids:
        found.append(_finding("bid_change", _newest_key("bid_change", bids), "%s in the last %s" % (plural(len(bids), "bid or goal change"), plural(CHANGE_DAYS, "day")), "An ad set's bid or its optimisation goal was changed.",
                              evidence=[_line(c, cur) for _a, c in bids[:SHOWN]]))
    if hits:
        found.append(_finding("activity", _newest_key("activity", [(h[1], h[2]) for h in hits]), "%s touching payment, access or limits" % plural(len(hits), "change"), "The account's activity history shows changes that matter.",
                              level="high" if any(h[0] == "high" for h in hits) else "medium", evidence=[_line(c, cur) for _l, _a, c in hits[:SHOWN]]))
    by_actor = {}
    for at, c in rows:
        if outside(c.get("actor")):
            by_actor.setdefault(c["actor"], []).append((at, c))
    for actor, evs in sorted(by_actor.items()):
        found.append(_finding("outside_editor", "editor:%s" % actor, "%s outside Position2 made %s" % (actor, plural(len(evs), "change")), "%s is not on Position2's domain and changed the account in the last %s." % (actor, plural(CHANGE_DAYS, "day")),
                              evidence=[_line(c, cur) for _a, c in evs[:SHOWN]]))
    n = len(budgets) + len(bids) + len(hits)
    return found, _check("activity", "Nothing touched payment, access or limits", "problem" if (budgets or bids or hits or by_actor) else "ok",
                         ("%s worth a look in the last %d days." % (plural(n + len(by_actor), "change"), CHANGE_DAYS)) if (budgets or bids or hits or by_actor) else "%s read from the last %d days; none touched payment, access or limits." % (plural(len(rows), "entry", "entries"), CHANGE_DAYS))


# ── The account ─────────────────────────────────────────────────────────────

def analyse(a, *, history=None, today=None, now=None, platform="google"):
    """{status, counts, findingCount, findings, checks, byRule}: one account. `status` is fix (a high or medium finding), check (only low ones) or clean."""
    today = today or dt.date.today()
    now = now or dt.datetime.combine(today, dt.time(12, 0), tzinfo=dt.timezone.utc)
    parts, checks = [], []
    if platform == "google":
        f, c = _google_status(a)
        parts.append(f); checks.append(c)
        f, c = _orders(a, now)
        parts.append(f); checks.append(c)
        f, cs = _google_changes(a, now)
        parts.append(f); checks += cs
        f, c = _actions(a, history, today)
        parts.append(f); checks.append(c)
        f, c = _users(a, history, today)
        parts.append(f); checks.append(c)
    else:
        f, c = _meta_status(a)
        parts.append(f); checks.append(c)
        f, c = _pages(a, history, today)
        parts.append(f); checks.append(c)
        f, c = _activity(a, now)
        parts.append(f); checks.append(c)
    findings = [x for fs in parts for x in fs]
    findings.sort(key=lambda f: (-LEVEL_RANK[f["level"]], ORDER.index(f["rule"]), f["key"]))
    counts = {k: sum(1 for f in findings if f["level"] == k) for k in LEVEL_RANK}
    status = "fix" if counts["high"] or counts["medium"] else "check" if counts["low"] else "clean"
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f["rule"], []).append(f)
    return {"status": status, "counts": counts, "findingCount": len(findings), "findings": findings, "checks": checks,
            "byRule": [{"rule": r, "label": RULES[r]["label"], "level": RULES[r]["level"], "findings": len(by_rule[r])} for r in ORDER if r in by_rule]}
