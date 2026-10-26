"""Paid Marketing Agents: Tracking & Data Health, what an account's last days say about whether its measurement still works (no network, no clock: `today` is passed in).

An account is passed in as plain data (see tracker/paid_track.py for how it is read):

  days      [{date, cost, conv}] the account's totals by day, about 30 days, `date` an ISO date, `cost` in the account's currency
  actions   Google only: [{id, name, status, type, primary, days: [{date, conv}]}] the conversion actions and what each recorded by day, or None when they could not be read
  lists     [{id, name, size, open}] Google's audience lists or Meta's custom audiences; `size` is the number of people (Google: the larger of the search and display sizes; Meta: the lower bound
            of the range) and `open` says the list is still collecting members; None when they could not be read
  terms     Google only: True or False (customer data terms accepted) or None
  uploads   Google only: {hasUploadActions, status, lastUpload} (offline conversions sent from a CRM) or None
  pixels    Meta only: [{id, name, lastFired (ISO time or ""), unavailable}] or None
  history   the monitor's earlier daily readings, oldest first: [{day, lists: {id: {name, size}}}]

The last whole days (the days before `today`) are held against the 21 days before them. A figure with too little behind it is never judged: it is listed as one the page could not judge, with the
number it had. What the API does not show is said in NOT_MEASURED, never left out silently.
"""
from __future__ import annotations

import datetime as dt

LEVEL_RANK = {"high": 3, "medium": 2, "low": 1}
SHORT_DAYS, LONG_DAYS = 3, 7        # a stop is looked for over the last 3 days (a fast account) and the last 7 (a slower one)
BASE_FROM, BASE_TO = 28, 8          # the usual rate is the 21 days from 28 days ago to 8 days ago
BASE_LEN = BASE_FROM - BASE_TO + 1
MIN_EXPECTED = 5                    # conversions the usual rate must promise in a window before none in it is called a stop (a chance of one in 150)
DROP_SHARE = 0.2                    # a week at or under this share of the usual week is a drop
DROP_MIN_WEEK = 10                  # the usual week must hold this many conversions before a drop is judged
SPEND_KEPT = 0.5                    # the spend must have gone on at this share of its usual to call a drop a tracking problem and not a pause
SHRINK = 0.30                       # an audience list that lost this share of its people since the last readings
LIST_MIN = 1000                     # the people a list needs before Google serves it on Search
HISTORY_DAYS = 14                   # the earlier readings a list is held against
UPLOAD_STALE_DAYS = 14
PIXEL_MEDIUM_HOURS = 48
PIXEL_HIGH_DAYS = 3
SHARE_HIGH = 0.5                    # a conversion action that carried this share of the usual conversions, when it stops, is high

RULES = {
    "no_conv": {"level": "high", "area": "Conversions", "label": "Conversions stopped",
                "fix": "Do this first, before anything else is changed: check the conversion tracking (is the tag on the thank-you page or the form, did the site or the form change, is the pixel or the tag manager container still published) and place a test conversion yourself. If tracking is fine, the spend is not converting: pause it until the cause is found."},
    "conv_drop": {"level": "medium", "area": "Conversions", "label": "Conversions dropped sharply",
                  "fix": "Compare it with what changed on the site, the form and the tag manager in the last week, then place a test conversion. A drop with the spend unchanged is more often broken tracking than lost demand."},
    "action_stopped": {"level": "medium", "area": "Conversion actions", "label": "A conversion action stopped recording",
                       "fix": "Open Goals, then Conversions, and the action: its status says what Google last saw (no recent conversions, tag inactive or unverified). Check the tag or the import behind it (the page, the event name, the Analytics link) and place a test conversion."},
    "list_shrunk": {"level": "medium", "area": "Audience lists", "label": "An audience list is shrinking",
                    "fix": "Find what feeds the list (the tag that adds visitors, the customer list upload, the data source) and whether its membership duration or its source changed. A list that keeps shrinking stops serving."},
    "list_lost": {"level": "high", "area": "Audience lists", "label": "An audience list can no longer serve",
                  "fix": "Check the source of the list first (a tag removed, an upload stopped, a source disconnected), then rebuild it. Campaigns that target or exclude it are not reaching who you meant."},
    "terms": {"level": "low", "area": "Customer data", "label": "Customer data terms not accepted",
              "fix": "Accept the customer data terms in Google Ads (Tools, then Data manager, or Conversions settings): without them customer lists and enhanced conversions are limited."},
    "upload_stale": {"level": "medium", "area": "Offline conversions", "label": "Offline conversions are not arriving",
                     "fix": "The link between the CRM and Google Ads stopped sending: check the upload job, the Google click ID it needs, and the connection (token or integration) in the CRM or the tool that sends them."},
    "pixel_stale": {"level": "medium", "area": "Pixel", "label": "The pixel has not fired lately",
                    "fix": "Open Events Manager and the pixel: the test events tool shows whether the site still fires it. Check the tag on the site (or the tag manager), the domain, and any change to the checkout or the form. Ignore this if the account's campaigns use lead forms or messages only."},
    "pixel_unavailable": {"level": "high", "area": "Pixel", "label": "The pixel is unavailable",
                          "fix": "Meta marks the pixel unavailable (it was deleted, or the account lost access to it). Open Events Manager, restore access or create a pixel, and put it back on the site."},
}
ORDER = ["no_conv", "conv_drop", "action_stopped", "pixel_unavailable", "pixel_stale", "list_lost", "list_shrunk", "upload_stale", "terms"]

NOT_MEASURED = [
    "Whether the tag is actually on the website: the platforms do not show it. A conversion that stops is the sign this page watches for.",
    "Google Analytics 4 and its own data streams, and what a CRM holds: only what reaches Google Ads or Meta is seen (imported actions, offline uploads).",
    "Meta's Conversions API and the events the pixel receives: the platform's Meta access does not include the pixel's event counts.",
    "Dashboards (Looker Studio, sheets): whether one is stale cannot be read from here.",
]


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def _day(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


def window(days, today, first, last, field):
    """Sum of `field` over the days from `today - first` to `today - last` (both counted, both before today)."""
    lo, hi = today - dt.timedelta(days=first), today - dt.timedelta(days=last)
    return sum(_f(d.get(field)) for d in days or [] if (x := _day(d.get("date"))) is not None and lo <= x <= hi)


def money(v, cur):
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "INR": "₹", "NZD": "NZ$", "SGD": "S$"}.get(cur)
    body = "{:,.0f}".format(v) if abs(v) >= 100 else "{:.2f}".format(v)
    return (sym + body) if sym else "%s %s" % (body, cur or "")


def _finding(rule, key, title, detail, *, level=None, evidence=None):
    r = RULES[rule]
    return {"rule": rule, "key": key, "level": level or r["level"], "area": r["area"], "title": title, "detail": detail, "fix": r["fix"], "evidence": evidence or []}


def _check(key, label, state, note):
    return {"key": key, "label": label, "state": state, "note": note}


# ── Conversions ─────────────────────────────────────────────────────────────

def _conversions(a, today):
    """([finding], check, usual conversions a week, conversions in the last 7 days, spend in the last 7 days)"""
    days, cur = a.get("days") or [], a.get("currency") or "USD"
    base = window(days, today, BASE_FROM, BASE_TO, "conv")
    rate = base / BASE_LEN
    conv7, cost7 = window(days, today, LONG_DAYS, 1, "conv"), window(days, today, LONG_DAYS, 1, "cost")
    cost_base7 = window(days, today, BASE_FROM, BASE_TO, "cost") / BASE_LEN * LONG_DAYS
    usual7 = rate * LONG_DAYS
    for n in (SHORT_DAYS, LONG_DAYS):
        rec, spent = window(days, today, n, 1, "conv"), window(days, today, n, 1, "cost")
        if spent > 0 and rec == 0 and rate * n >= MIN_EXPECTED:
            return ([_finding("no_conv", "no_conv", "No conversions in the last %s, usually about %s a day" % (plural(n, "day"), ("%.1f" % rate).rstrip("0").rstrip(".")),
                              "%s were spent in the last %s and none was recorded; the 3 weeks before averaged %s a day. A campaign that works looks dead when its tag breaks."
                              % (money(spent, cur), plural(n, "day"), ("%.1f" % rate).rstrip("0").rstrip(".")), evidence=["Last %s: 0 conversions, %s spent" % (plural(n, "day"), money(spent, cur)),
                                                                                                                   "Usual: %.1f conversions a day over 21 days" % rate])],
                    _check("conversions", "Conversions are being recorded", "problem", "None in the last %s, usually %.1f a day." % (plural(n, "day"), rate)), usual7, conv7, cost7)
    if usual7 >= DROP_MIN_WEEK and conv7 <= usual7 * DROP_SHARE and cost7 >= cost_base7 * SPEND_KEPT and cost7 > 0:
        pct = int(round((1 - conv7 / usual7) * 100))
        return ([_finding("conv_drop", "conv_drop", "Conversions fell %d percent with the spend going on" % pct,
                          "%d conversions in the last 7 days against about %d in a usual week, and %s spent (a usual week is %s). Spend at the usual pace with this few conversions points at tracking, not demand."
                          % (conv7, round(usual7), money(cost7, cur), money(cost_base7, cur)), evidence=["Last 7 days: %d conversions" % conv7, "A usual week: about %d" % round(usual7)])],
                _check("conversions", "Conversions are being recorded", "problem", "%d in the last 7 days against about %d usually." % (conv7, round(usual7))), usual7, conv7, cost7)
    if cost7 <= 0:
        return [], _check("conversions", "Conversions are being recorded", "unknown", "Nothing was spent in the last 7 days, so there is nothing to judge."), usual7, conv7, cost7
    if rate * SHORT_DAYS < MIN_EXPECTED and rate * LONG_DAYS < MIN_EXPECTED:
        return [], _check("conversions", "Conversions are being recorded", "unknown",
                          "Usually only about %.1f conversions a week, too few to tell a stop from a quiet spell (it needs about %d in a week)." % (usual7, MIN_EXPECTED)), usual7, conv7, cost7
    return [], _check("conversions", "Conversions are being recorded", "ok", "%d in the last 7 days (about %d in a usual week)." % (conv7, round(usual7))), usual7, conv7, cost7


def _actions(a, today, account_stopped):
    acts = a.get("actions")
    if acts is None:
        return [], _check("actions", "Each conversion action records", "unknown", "The conversion actions could not be read.")
    cost7 = window(a.get("days"), today, LONG_DAYS, 1, "cost")
    total_base = sum(window(x.get("days"), today, BASE_FROM, BASE_TO, "conv") for x in acts) or 0.0
    found, judged, quiet = [], 0, 0
    for x in acts:
        if str(x.get("status") or "") == "REMOVED":
            continue
        base = window(x.get("days"), today, BASE_FROM, BASE_TO, "conv")
        usual7 = base / BASE_LEN * LONG_DAYS
        rec7 = window(x.get("days"), today, LONG_DAYS, 1, "conv")
        if usual7 < MIN_EXPECTED:
            quiet += 1
            continue
        judged += 1
        if rec7 == 0 and cost7 > 0 and not account_stopped:
            share = base / total_base if total_base else 0.0
            hidden = str(x.get("status") or "") == "HIDDEN"
            found.append(_finding("action_stopped", "action:%s" % x["id"], "The conversion action \"%s\" recorded nothing in 7 days" % (x.get("name") or x["id"]),
                                  "It usually records about %d a week%s and carried %d percent of the account's conversions. %s" % (
                                      round(usual7), ", is %s" % ("hidden (it no longer counts)" if hidden else str(x.get("status") or "").lower()) if str(x.get("status") or "") not in ("", "ENABLED") else "",
                                      int(round(share * 100)), "Other conversion actions are still recording, so the account itself is not dark."),
                                  level="high" if share >= SHARE_HIGH else "medium", evidence=["Last 7 days: 0", "Usual week: about %d" % round(usual7), "Type: %s" % (x.get("type") or "unknown").replace("_", " ").lower()]))
    if found:
        return found, _check("actions", "Each conversion action records", "problem", plural(len(found), "action") + " stopped recording.")
    if not judged:
        return [], _check("actions", "Each conversion action records", "unknown", "No conversion action records enough (about %d a week) to tell a stop from a quiet spell." % MIN_EXPECTED)
    return [], _check("actions", "Each conversion action records", "ok", "%s checked%s." % (plural(judged, "action"), (", %d too quiet to judge" % quiet) if quiet else ""))


# ── Audience lists ──────────────────────────────────────────────────────────

def _history_sizes(history, today):
    """{list id: the largest size in the readings of the last HISTORY_DAYS days}."""
    out = {}
    lo = today - dt.timedelta(days=HISTORY_DAYS)
    for h in history or []:
        d = _day(h.get("day"))
        if d is None or d >= today or d < lo:
            continue
        for lid, v in (h.get("lists") or {}).items():
            out[lid] = max(out.get(lid, 0), int(_f(v.get("size"))))
    return out


def _lists(a, today, history, platform):
    lists = a.get("lists")
    if lists is None:
        return [], _check("lists", "Audience lists keep their people", "unknown", "The audience lists could not be read.")
    if not lists:
        return [], _check("lists", "Audience lists keep their people", "ok", "This account has no audience list.")
    seen = _history_sizes(history, today)
    if not seen:
        return [], _check("lists", "Audience lists keep their people", "unknown", "%s read today; there is no earlier reading to compare with yet (it compares from tomorrow)." % plural(len(lists), "list"))
    found, judged = [], 0
    for lst in lists:
        if not lst.get("open", True) or lst["id"] not in seen or lst.get("size") is None:
            continue
        before, now = seen[lst["id"]], int(_f(lst.get("size")))
        if before < LIST_MIN // 2:
            continue
        judged += 1
        lost = now == 0 or (platform == "google" and before >= LIST_MIN and now < LIST_MIN)
        if lost:
            found.append(_finding("list_lost", "list:%s" % lst["id"], "The list \"%s\" fell from %s to %s people" % (lst.get("name") or lst["id"], "{:,}".format(before), "{:,}".format(now)),
                                  "It %s." % ("is empty now" if now == 0 else "is under the %s people Google needs before a list serves on Search" % "{:,}".format(LIST_MIN)),
                                  evidence=["Earlier: %s" % "{:,}".format(before), "Now: %s" % "{:,}".format(now)]))
        elif now <= before * (1 - SHRINK):
            found.append(_finding("list_shrunk", "list:%s" % lst["id"], "The list \"%s\" lost %d percent of its people" % (lst.get("name") or lst["id"], int(round((1 - now / before) * 100))),
                                  "%s people now, against %s in the last %d days." % ("{:,}".format(now), "{:,}".format(before), HISTORY_DAYS), evidence=["Earlier: %s" % "{:,}".format(before), "Now: %s" % "{:,}".format(now)]))
    if found:
        return found, _check("lists", "Audience lists keep their people", "problem", "%s shrinking or lost." % plural(len(found), "list"))
    if not judged:
        return [], _check("lists", "Audience lists keep their people", "ok", "%s read; none is large enough to judge." % plural(len(lists), "list"))
    return [], _check("lists", "Audience lists keep their people", "ok", "%s held against the last readings; none shrank by %d percent." % (plural(judged, "list"), int(SHRINK * 100)))


# ── Customer data, offline uploads and the pixel ────────────────────────────

def _terms(a):
    t = a.get("terms")
    if t is None:
        return [], _check("terms", "Customer data terms accepted", "unknown", "Not shown by the API for this account.")
    if t:
        return [], _check("terms", "Customer data terms accepted", "ok", "Accepted.")
    crm = any(str(x.get("type") or "") == "CRM_BASED" for x in a.get("lists") or [])
    return [_finding("terms", "terms", "Customer data terms are not accepted", "Customer lists%s are limited until they are." % (" (this account has one)" if crm else " and enhanced conversions"),
                     level="medium" if crm else "low")], _check("terms", "Customer data terms accepted", "problem", "Not accepted.")


def _uploads(a, today, now):
    u = a.get("uploads")
    if u is None:
        return [], _check("uploads", "Offline conversions arrive", "unknown", "The offline upload log could not be read.")
    if not u.get("hasUploadActions"):
        return [], _check("uploads", "Offline conversions arrive", "ok", "No offline conversion action is set up.")
    last = u.get("lastUpload") or ""
    try:
        t = dt.datetime.fromisoformat(last.replace("Z", "+00:00").replace(" ", "T")) if last else None
    except ValueError:
        t = None
    if t is not None and t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    age = (now - t).days if t is not None else None
    status = str(u.get("status") or "")
    if t is None and not last and status not in ("NO_RECENT_UPLOAD", "NEEDS_ATTENTION"):
        return [], _check("uploads", "Offline conversions arrive", "unknown", "An offline conversion action is set up but Google has no upload on record for it, so a broken link cannot be told from one that was never used.")
    if status in ("NO_RECENT_UPLOAD", "NEEDS_ATTENTION") or (age is not None and age >= UPLOAD_STALE_DAYS):
        why = "Google's log says %s" % status.replace("_", " ").lower() if status in ("NO_RECENT_UPLOAD", "NEEDS_ATTENTION") else "the last upload was %s ago" % plural(age, "day")
        return [_finding("upload_stale", "uploads", "Offline conversions are not arriving: %s" % why, "This account has an offline conversion action (a CRM or a form tool sends the sales or the calls), and %s." % why,
                         evidence=["Status: %s" % (status or "unknown"), "Last upload: %s" % (last[:10] if last else "none on record")])], _check("uploads", "Offline conversions arrive", "problem", why[0].upper() + why[1:] + ".")
    return [], _check("uploads", "Offline conversions arrive", "ok", "The last upload was %s ago." % plural(age or 0, "day"))


def _pixels(a, now, conv_stopped, conv_healthy):
    pixels = a.get("pixels")
    if pixels is None:
        return [], _check("pixels", "The pixel fires", "unknown", "The pixels could not be read.")
    if not pixels:
        return [], _check("pixels", "The pixel fires", "unknown", "This ad account has no pixel (fine if its campaigns use lead forms or messages only).")
    spent = a.get("_spent7", 0) > 0
    found = []
    for p in pixels:
        name = p.get("name") or p["id"]
        if p.get("unavailable"):
            found.append(_finding("pixel_unavailable", "pixel:%s" % p["id"], "The pixel \"%s\" is unavailable" % name, "Meta marks it unavailable.", evidence=["Pixel %s" % p["id"]]))
            continue
        if not spent:
            continue
        fired = p.get("lastFired") or ""
        try:
            t = dt.datetime.fromisoformat(fired.replace("Z", "+00:00")) if fired else None
        except ValueError:
            t = None
        if t is not None and t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        hours = (now - t).total_seconds() / 3600 if t is not None else None
        if t is None:
            found.append(_finding("pixel_stale", "pixel:%s" % p["id"], "The pixel \"%s\" has never fired" % name, "Meta has no record of an event from it, and the account spent in the last 7 days.",
                                  level="low" if conv_healthy else None, evidence=["Pixel %s" % p["id"]] + (["The account still records conversions, so this may be a pixel it no longer uses."] if conv_healthy else [])))
        elif hours >= PIXEL_MEDIUM_HOURS:
            days = int(hours // 24)
            found.append(_finding("pixel_stale", "pixel:%s" % p["id"], "The pixel \"%s\" last fired %s ago" % (name, plural(days, "day")),
                                  "The account spent in the last 7 days and the pixel has been silent for %s." % plural(days, "day"),
                                  level="high" if days >= PIXEL_HIGH_DAYS and conv_stopped else ("low" if conv_healthy else None),
                                  evidence=["Last fired: %s" % fired[:16].replace("T", " "), "Pixel %s" % p["id"]] + (["The account still records conversions, so this may be a pixel it no longer uses."] if conv_healthy else [])))
    if found:
        return found, _check("pixels", "The pixel fires", "problem", plural(len(found), "pixel") + " silent or unavailable.")
    if not spent:
        return [], _check("pixels", "The pixel fires", "unknown", "Nothing was spent in the last 7 days, so a silent pixel would mean nothing.")
    return [], _check("pixels", "The pixel fires", "ok", "%s fired in the last %d hours." % (plural(len(pixels), "pixel"), PIXEL_MEDIUM_HOURS))


# ── The account ─────────────────────────────────────────────────────────────

def analyse(a, *, history=None, today=None, now=None, platform="google"):
    """{status, counts, findingCount, findings, checks, headline}: one account. `status` is fix (a high or medium finding), check (only low ones) or clean."""
    today = today or dt.date.today()
    now = now or dt.datetime.combine(today, dt.time(12, 0), tzinfo=dt.timezone.utc)
    cur = a.get("currency") or "USD"
    conv_found, conv_check, usual7, conv7, cost7 = _conversions(a, today)
    a = dict(a, _spent7=cost7)
    stopped = bool(conv_found) and conv_found[0]["rule"] == "no_conv"
    parts = [(conv_found, conv_check)]
    if platform == "google":
        parts += [_actions(a, today, stopped), _lists(a, today, history, "google"), _terms(a), _uploads(a, today, now)]
    else:
        healthy = not conv_found and conv_check["state"] == "ok"
        parts += [_pixels(a, now, stopped, healthy), _lists(a, today, history, "meta")]
    findings = [f for fs, _c in parts for f in fs]
    findings.sort(key=lambda f: (-LEVEL_RANK[f["level"]], ORDER.index(f["rule"]), f["key"]))
    counts = {k: sum(1 for f in findings if f["level"] == k) for k in LEVEL_RANK}
    status = "fix" if counts["high"] or counts["medium"] else "check" if counts["low"] else "clean"
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f["rule"], []).append(f)
    return {"status": status, "counts": counts, "findingCount": len(findings), "findings": findings, "checks": [c for _f, c in parts],
            "byRule": [{"rule": r, "label": RULES[r]["label"], "level": RULES[r]["level"], "findings": len(by_rule[r])} for r in ORDER if r in by_rule],
            "headline": {"conv7": round(conv7, 1), "usualConv7": round(usual7, 1), "spend7": round(cost7, 2), "currency": cur}}
