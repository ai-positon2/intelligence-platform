"""Paid Marketing Agents: Tracking Data Health, the rules (no network, no clock but the one passed in).

Can an account's conversion numbers be trusted? One account's read (tracker/paid_tracking.py) is held against its own last four weeks:

  conv_stopped     no conversion yesterday while clicks held: critical when the account usually has at least 2 a day; under that, medium, and only after 3 days at zero in a row
  conv_drop        conversions down 60 percent or more yesterday while clicks held (high)
  cvr_drop         the conversion rate of the last 3 days down 40 percent or more, with at least 10 conversions expected (medium)
  action_stopped   a primary Google conversion action silent for as long as it takes it to record about 3 conversions at its own rate (3 to 14 days) (high)
  auto_tagging     a Google account with auto-tagging off, so clicks cannot be tied to conversions (high)
  pixel_silent     a Meta pixel that was firing has not fired for 24 hours while the account spends (critical when no other pixel fires, else high)
  list_below       a remarketing list that held Google's 1,000 for Search has dropped under it (medium)
  list_drop        a remarketing list down 25 percent or more over 14 days (low: shown, never alerted)

The baseline is the 28 days before yesterday, from the account's first day with clicks, leaving out the days the team marked as an expected drop; fewer than 7 such days and the conversion
rules say so instead of judging. A finding on a day the team expected a drop is kept but lowered to low, and so is one the team acknowledged (for ACK_DAYS), so neither is alerted or reminded.
When conversions stopped or dropped, the conversion actions that stopped with them are evidence of that one finding, not findings of their own. List trends need the sizes the daily check
keeps (pm_mon_snaps), so they start once it has run for a while. The health score is 100 less what each finding costs.
"""
from __future__ import annotations

import datetime as dt

READ_DAYS = 35                  # days read before today: the 28 of the baseline, yesterday and room for the 3-day rules and a time zone
BASE_DAYS = 28
MIN_BASE_DAYS = 7
STOP_MIN_DAILY = 2.0            # usual conversions a day for one day at zero to be critical
STOP_CLICKS = 0.5               # clicks still at least this share of usual, or it is traffic that stopped, not tracking
LOW_STOP_DAYS = 3
LOW_STOP_MIN_EXPECTED = 1.0     # conversions the 3 days should have held for 3 at zero to mean anything
DROP = 0.6
DROP_CLICKS = 0.7
CVR_DROP = 0.4
CVR_DAYS = 3
CVR_MIN_EXPECTED = 10.0
ACTION_STOP_DAYS = 3             # the shortest silence that counts
ACTION_MAX_DAYS = 14            # the longest: an action too slow to expect ACTION_EXPECT in this many days is not judged
ACTION_EXPECT = 3.0             # conversions the silent days should have held at the action's own rate (5 in 28 days and 3 quiet days is normal, as a live account showed)
LIST_MIN = 1000                 # what Google needs in a list for it to serve on Search
LIST_DROP = 0.25
LIST_DAYS = 14
LIST_KINDS = ("REMARKETING", "RULE_BASED", "BASIC", "LOGICAL", "WEBSITE", "APP")    # lists fed by a tag or a pixel (customer match is not)
PIXEL_HOURS = 24
ACK_DAYS = 14
SCORE_COST = {"critical": 40, "high": 25, "medium": 10, "low": 3, "info": 0}
LEVEL_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "INR": "₹", "NZD": "NZ$", "SGD": "S$"}

RULES = {
    "conv_stopped": {"label": "Conversions stopped", "fix": "Open the site's thank-you page and check the conversion tag or pixel fires, then check the conversion actions in the account and whether the site or its tag manager changed."},
    "conv_drop": {"label": "Conversions dropped", "fix": "Compare the conversion actions day by day to find the one that fell, then check its tag on the site and any site or form change."},
    "cvr_drop": {"label": "Conversion rate dropped", "fix": "Check the forms and the thank-you page still work, then whether one conversion action or one campaign accounts for the fall."},
    "action_stopped": {"label": "Conversion action stopped", "fix": "Check that action's tag fires on its page (Google Tag Assistant), and in Goals that its status is still Recording conversions."},
    "auto_tagging": {"label": "Auto-tagging off", "fix": "Turn auto-tagging on in the account settings, so each click carries its click id and conversions can be tied to it."},
    "pixel_silent": {"label": "Pixel silent", "fix": "Check the pixel on the site with Meta Pixel Helper and in Events Manager, then whether the site or its tag manager changed."},
    "list_below": {"label": "List below serving size", "fix": "Check the remarketing tag still fires on the pages that feed this list; a list under 1,000 stops serving on Search."},
    "list_drop": {"label": "List shrinking", "fix": "Check the tag or pixel that feeds this list still fires, and whether site traffic fell."},
}
ORDER = list(RULES)


def plural(n, one, many=None):
    return "%s %s" % (num(n), one if n == 1 else (many or one + "s"))


def num(v):
    if v is None:
        return "n/a"
    if abs(v - round(v)) < 1e-9:
        return format(int(round(v)), ",")
    return format(round(v, 1), ",")


def money(v, cur):
    if v is None:
        return "n/a"
    body = format(int(round(v)), ",") if abs(v) >= 100 else "%.2f" % v
    sym = SYMBOL.get(cur or "")
    return sym + body if sym else "%s %s" % (body, cur or "")


def pct(v):
    return "%d%%" % round(v * 100)


def day_text(d):
    return d.strftime("%b %d").replace(" 0", " ")


def _date(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


# ── Settings as the rules read them ─────────────────────────────────────────

def in_window(day, windows):
    """The why of the expected-drop window `day` falls in, or ""."""
    for w in windows or []:
        a, b = _date(w.get("from")), _date(w.get("to"))
        if a and b and a <= day <= b:
            return w.get("why") or "an expected drop"
    return ""


def ack_of(acks, key, now):
    """The team's acknowledgement of finding `key` while it holds (ACK_DAYS), or None."""
    a = (acks or {}).get(key)
    if not a:
        return None
    at = a.get("at")
    try:
        when = dt.datetime.fromisoformat(at) if at else None
    except ValueError:
        when = None
    if when is None or (now - when).days >= ACK_DAYS:
        return None
    return a


# ── The days ────────────────────────────────────────────────────────────────

def dense(days, first, last):
    """{date: {clicks, cost, conv}} for every day first..last (a day the platform sent nothing for spent and converted nothing)."""
    out = {}
    d = first
    while d <= last:
        out[d] = {"clicks": 0.0, "cost": 0.0, "conv": 0.0}
        d += dt.timedelta(days=1)
    for r in days or []:
        d = _date(r.get("date"))
        if d in out:
            for k in ("clicks", "cost", "conv"):
                out[d][k] += float(r.get(k) or 0)
    return out


def baseline(series, yesterday, windows):
    """{days, conv, clicks, cost, cvr, first}: the daily averages over the 28 days before yesterday, from the first day with clicks, without the expected-drop days."""
    start = yesterday - dt.timedelta(days=BASE_DAYS)
    live = [d for d in sorted(series) if series[d]["clicks"] > 0]
    first = live[0] if live else None
    picked = [d for d in sorted(series) if start <= d < yesterday and first and d >= first and not in_window(d, windows)]
    n = len(picked)
    tot = {k: sum(series[d][k] for d in picked) for k in ("conv", "clicks", "cost")}
    return {"days": n, "conv": tot["conv"] / n if n else 0.0, "clicks": tot["clicks"] / n if n else 0.0, "cost": tot["cost"] / n if n else 0.0,
            "cvr": tot["conv"] / tot["clicks"] if tot["clicks"] else 0.0, "convTotal": tot["conv"], "first": first.isoformat() if first else None}


def _finding(kind, key, level, title, detail, evidence=(), **extra):
    return dict({"kind": kind, "key": key, "level": level, "baseLevel": level, "title": title, "detail": detail, "fix": RULES[kind]["fix"], "evidence": list(evidence),
                 "expected": "", "acked": None}, **extra)


def conversion_findings(series, yesterday, base, base3):
    """The account-wide rules (conv_stopped, conv_drop, cvr_drop) and a note when the history is too short to judge. `base` is the baseline before yesterday, `base3` the one before
    the 3 days the 3-day rules judge (so a stop does not lower its own usual)."""
    out, notes = [], []
    if base["days"] < MIN_BASE_DAYS:
        notes.append("Only %s of history before yesterday, so conversions were not judged (at least %d are needed)." % (plural(base["days"], "usable day"), MIN_BASE_DAYS))
        return out, notes
    y = series[yesterday]
    usual_conv, usual_clicks = base["conv"], base["clicks"]
    if usual_clicks <= 0:
        return out, notes
    held = y["clicks"] >= STOP_CLICKS * usual_clicks
    if y["conv"] == 0 and usual_conv >= STOP_MIN_DAILY and held:
        out.append(_finding("conv_stopped", "conv_stopped", "critical", "Conversions stopped",
                            "No conversions on %s, against %s a day over the %s before, while clicks held (%s against %s usual)." % (
                                day_text(yesterday), num(usual_conv), plural(base["days"], "day"), num(y["clicks"]), num(usual_clicks)),
                            day=yesterday.isoformat(), usual=round(usual_conv, 2), clicks=y["clicks"], usualClicks=round(usual_clicks, 1)))
    elif usual_conv < STOP_MIN_DAILY and base3["days"] >= MIN_BASE_DAYS:
        last = [yesterday - dt.timedelta(days=i) for i in range(LOW_STOP_DAYS)]
        clicks3 = sum(series[d]["clicks"] for d in last if d in series)
        usual_conv, usual_clicks = base3["conv"], base3["clicks"]
        if (all(d in series and series[d]["conv"] == 0 for d in last) and usual_conv * LOW_STOP_DAYS >= LOW_STOP_MIN_EXPECTED
                and clicks3 >= STOP_CLICKS * usual_clicks * LOW_STOP_DAYS):
            out.append(_finding("conv_stopped", "conv_stopped", "medium", "No conversions for %d days" % LOW_STOP_DAYS,
                                "No conversions from %s to %s. The account usually has %s a day (%s over the %s before), so %d days at zero is unusual, and clicks held (%s against %s usual)." % (
                                    day_text(last[-1]), day_text(yesterday), num(usual_conv), num(base3["convTotal"]), plural(base3["days"], "day"), LOW_STOP_DAYS, num(clicks3),
                                    num(usual_clicks * LOW_STOP_DAYS)),
                                day=yesterday.isoformat(), usual=round(usual_conv, 2), clicks=clicks3, usualClicks=round(usual_clicks * LOW_STOP_DAYS, 1)))
    usual_conv, usual_clicks = base["conv"], base["clicks"]
    if not out and usual_conv >= STOP_MIN_DAILY and y["conv"] <= (1 - DROP) * usual_conv and y["clicks"] >= DROP_CLICKS * usual_clicks:
        out.append(_finding("conv_drop", "conv_drop", "high", "Conversions down %s" % pct(1 - y["conv"] / usual_conv),
                            "%s conversions on %s against %s a day over the %s before, while clicks held (%s against %s usual)." % (
                                num(y["conv"]), day_text(yesterday), num(usual_conv), plural(base["days"], "day"), num(y["clicks"]), num(usual_clicks)),
                            day=yesterday.isoformat(), usual=round(usual_conv, 2), conv=y["conv"], clicks=y["clicks"], usualClicks=round(usual_clicks, 1)))
    if not out and base3["cvr"] > 0 and base3["days"] >= MIN_BASE_DAYS:
        last = [yesterday - dt.timedelta(days=i) for i in range(CVR_DAYS)]
        clicks3 = sum(series[d]["clicks"] for d in last if d in series)
        conv3 = sum(series[d]["conv"] for d in last if d in series)
        expected = clicks3 * base3["cvr"]
        if expected >= CVR_MIN_EXPECTED and clicks3 and conv3 / clicks3 <= (1 - CVR_DROP) * base3["cvr"]:
            out.append(_finding("cvr_drop", "cvr_drop", "medium", "Conversion rate down %s" % pct(1 - (conv3 / clicks3) / base3["cvr"]),
                                "%s of clicks converted from %s to %s, against %s over the %s before: %s conversions where about %s were expected." % (
                                    "%.1f%%" % (100 * conv3 / clicks3), day_text(last[-1]), day_text(yesterday), "%.1f%%" % (100 * base3["cvr"]), plural(base3["days"], "day"),
                                    num(conv3), num(round(expected))),
                                day=yesterday.isoformat(), cvr=round(conv3 / clicks3, 4), usualCvr=round(base3["cvr"], 4), conv=conv3, expected=round(expected, 1)))
    return out, notes


def action_window(base):
    """The days an action must be silent to count as stopped: long enough to have expected ACTION_EXPECT conversions at its rate over the BASE_DAYS before, at least ACTION_STOP_DAYS;
    None when that is more than ACTION_MAX_DAYS (too slow to judge)."""
    if base <= 0:
        return None
    n = max(ACTION_STOP_DAYS, int(-(-ACTION_EXPECT * BASE_DAYS // base)))
    return n if n <= ACTION_MAX_DAYS else None


def action_rows(actions, yesterday, muted):
    """[{id, name, category, type, primary, base, recent, days, state}] for the conversion actions: `base` is what it recorded in the BASE_DAYS before its window, `recent` in its window of
    `days` (action_window); state is stopped, ok, quiet (too slow to judge), secondary or muted."""
    out = []
    for a in actions or []:
        conv = a.get("days") or {}

        def total(first, last):
            return sum(v for d, v in conv.items() if first.isoformat() <= d <= last.isoformat())
        rough = total(yesterday - dt.timedelta(days=BASE_DAYS), yesterday - dt.timedelta(days=1))
        n = action_window(rough)
        win = n or ACTION_STOP_DAYS
        first = yesterday - dt.timedelta(days=win - 1)
        base = total(first - dt.timedelta(days=BASE_DAYS), first - dt.timedelta(days=1))
        n = action_window(base)
        win = n or ACTION_STOP_DAYS                                                          # the window the action is judged on, and shown with
        recent = total(yesterday - dt.timedelta(days=win - 1), yesterday)
        why = (muted or {}).get("action:%s" % a["id"])
        if why:
            state = "muted"
        elif not a.get("primary"):
            state = "secondary"
        elif n is None:
            state = "quiet"
        elif recent == 0:
            state = "stopped"
        else:
            state = "ok"
        out.append({"id": a["id"], "name": a.get("name") or a["id"], "category": a.get("category") or "", "type": a.get("type") or "", "primary": bool(a.get("primary")),
                    "base": round(base, 2), "recent": round(recent, 2), "days": win, "state": state, "mutedWhy": why or ""})
    out.sort(key=lambda r: ({"stopped": 0, "ok": 1, "quiet": 2, "secondary": 3, "muted": 4}[r["state"]], -r["base"], r["name"].lower()))
    return out


def action_findings(rows, yesterday):
    out = []
    for r in rows:
        if r["state"] != "stopped":
            continue
        out.append(_finding("action_stopped", "action_stopped:%s" % r["id"], "high", "%s stopped converting" % r["name"],
                            "The primary conversion action %s recorded %s in the %d days before and none in the last %s (%s to %s), when about %s were expected." % (
                                r["name"], plural(r["base"], "conversion"), BASE_DAYS, plural(r["days"], "day"), day_text(yesterday - dt.timedelta(days=r["days"] - 1)), day_text(yesterday),
                                num(round(r["base"] / BASE_DAYS * r["days"], 1))),
                            actionId=r["id"], base=r["base"], days=r["days"]))
    return out


def list_rows(lists, history, today, muted):
    """[{id, name, kind, size, before, peak, change, state}]: state below, shrinking, ok, new (no size kept yet) or muted. `history` is [{day, lists: {id: size}}], oldest first."""
    cut = (today - dt.timedelta(days=LIST_DAYS)).isoformat()
    out = []
    for l in lists or []:
        if str(l.get("kind") or "").upper() not in LIST_KINDS:
            continue
        lid = str(l["id"])
        past = [(h["day"], (h.get("lists") or {}).get(lid)) for h in history or [] if (h.get("lists") or {}).get(lid) is not None]
        before = None
        for d, size in past:
            if d <= cut:
                before = size
        recent = [size for d, size in past if d > cut]
        peak = max(recent + ([before] if before is not None else []), default=None)
        size = l.get("size")
        why = (muted or {}).get("list:%s" % lid)
        change = (size - before) / before if before and size is not None else None
        if why:
            state = "muted"
        elif size is None or (before is None and peak is None):
            state = "new"
        elif l.get("platform", "google") == "google" and peak is not None and peak >= LIST_MIN and size < LIST_MIN:
            state = "below"
        elif change is not None and change <= -LIST_DROP:
            state = "shrinking"
        else:
            state = "ok"
        out.append({"id": lid, "name": l.get("name") or lid, "kind": l.get("kind") or "", "size": size, "before": before, "peak": peak,
                    "change": round(change, 4) if change is not None else None, "state": state, "mutedWhy": why or ""})
    out.sort(key=lambda r: ({"below": 0, "shrinking": 1, "ok": 2, "new": 3, "muted": 4}[r["state"]], -(r["size"] or 0), r["name"].lower()))
    return out


def list_findings(rows):
    out = []
    for r in rows:
        if r["state"] == "below":
            out.append(_finding("list_below", "list_below:%s" % r["id"], "medium", "%s is under 1,000" % r["name"],
                                "The list held %s lately and has %s now, under the %s Google needs for it to serve on Search." % (num(r["peak"]), num(r["size"]), format(LIST_MIN, ",")),
                                listId=r["id"], size=r["size"], peak=r["peak"]))
        elif r["state"] == "shrinking":
            out.append(_finding("list_drop", "list_drop:%s" % r["id"], "low", "%s down %s" % (r["name"], pct(-r["change"])),
                                "The list had %s %d days ago and has %s now." % (num(r["before"]), LIST_DAYS, num(r["size"])), listId=r["id"], size=r["size"], before=r["before"]))
    return out


def pixel_rows(pixels, now, spent, muted):
    """[{id, name, lastFired, hours, state}]: state silent (fired in the read's days, not in the last 24 hours, while the account spent), firing, unused (not fired in 35 days) or muted."""
    out = []
    for p in pixels or []:
        last = None
        try:
            last = dt.datetime.fromisoformat(str(p.get("lastFired")).replace("Z", "+00:00")) if p.get("lastFired") else None
        except ValueError:
            last = None
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        hours = (now - last).total_seconds() / 3600 if last else None
        why = (muted or {}).get("pixel:%s" % p["id"])
        if why:
            state = "muted"
        elif hours is None or hours > READ_DAYS * 24:
            state = "unused"
        elif hours <= PIXEL_HOURS:
            state = "firing"
        else:
            state = "silent" if spent else "idle"
        out.append({"id": str(p["id"]), "name": p.get("name") or str(p["id"]), "lastFired": last.isoformat() if last else None, "hours": round(hours, 1) if hours is not None else None,
                    "state": state, "mutedWhy": why or ""})
    return out


def pixel_findings(rows):
    firing = any(r["state"] == "firing" for r in rows)
    out = []
    for r in rows:
        if r["state"] != "silent":
            continue
        out.append(_finding("pixel_silent", "pixel_silent:%s" % r["id"], "high" if firing else "critical", "%s has not fired for %s" % (r["name"], plural(int(r["hours"] // 1), "hour")),
                            "The pixel last fired %s ago%s, while the account spent yesterday." % (plural(int(r["hours"] // 1), "hour"), "" if firing else " and no other pixel of the account is firing"),
                            pixelId=r["id"], hours=r["hours"]))
    return out


def score_of(findings):
    return max(0, 100 - sum(SCORE_COST.get(f["level"], 0) for f in findings))


def status_of(findings):
    levels = {f["level"] for f in findings}
    if "critical" in levels:
        return "broken"
    if levels & {"high", "medium"}:
        return "fix"
    if "low" in levels:
        return "check"
    return "healthy"


def analyse(raw, *, config=None, history=None, now=None):
    """One account's health from its read. `config` is the team's saved settings for it (tracker/paid_tracking_settings.py account_of), `history` the sizes the daily check kept
    ([{day, lists}], oldest first), `now` the time (UTC) acknowledgements and pixels are aged from."""
    config = config or {"windows": [], "muted": {}, "acks": {}, "owner": ""}
    now = now or dt.datetime.now(dt.timezone.utc)
    today = _date(raw.get("today")) or now.date()
    yesterday = today - dt.timedelta(days=1)
    series = dense(raw.get("days"), today - dt.timedelta(days=READ_DAYS), yesterday)
    windows, muted = config.get("windows") or [], config.get("muted") or {}
    base = baseline(series, yesterday, windows)
    findings, notes = conversion_findings(series, yesterday, base, baseline(series, yesterday - dt.timedelta(days=LOW_STOP_DAYS - 1), windows))
    actions = action_rows(raw.get("actions"), yesterday, muted) if raw.get("actions") is not None else None
    stopped = action_findings(actions or [], yesterday)
    top = next((f for f in findings if f["kind"] in ("conv_stopped", "conv_drop")), None)
    if top is not None:
        top["evidence"] += [f["detail"] for f in stopped]                                   # one cause, one alert: the actions that stopped with the account say where
        top["actions"] = [f["actionId"] for f in stopped]
    else:
        findings += stopped
    if raw.get("autoTagging") is False and not muted.get("rule:auto_tagging"):
        findings.append(_finding("auto_tagging", "auto_tagging", "high", "Auto-tagging is off",
                                 "Google Ads is not adding a click id (gclid) to the ads' links, so conversions imported from analytics or a CRM cannot be tied to a click."))
    lists = list_rows(raw.get("lists"), history, today, muted) if raw.get("lists") is not None else None
    findings += list_findings(lists or [])
    pixels = pixel_rows(raw.get("pixels"), now, series[yesterday]["cost"] > 0, muted) if raw.get("pixels") is not None else None
    findings += pixel_findings(pixels or [])
    expected = in_window(yesterday, windows)
    for f in findings:
        if expected and f["kind"] in ("conv_stopped", "conv_drop", "cvr_drop", "action_stopped", "pixel_silent"):
            f["expected"] = expected
            f["level"] = "low"
        a = ack_of(config.get("acks"), f["key"], now)
        if a:
            f["acked"] = a
            f["level"] = "low"
    findings.sort(key=lambda f: (-LEVEL_RANK[f["level"]], ORDER.index(f["kind"]), f["key"]))
    counts = {lv: sum(1 for f in findings if f["level"] == lv) for lv in ("critical", "high", "medium", "low")}
    chart = [{"date": d.isoformat(), "clicks": series[d]["clicks"], "conv": round(series[d]["conv"], 2), "cost": round(series[d]["cost"], 2), "expected": bool(in_window(d, windows))}
             for d in sorted(series) if d >= yesterday - dt.timedelta(days=READ_DAYS - 1)]
    y = series[yesterday]
    return {"score": score_of(findings), "status": status_of(findings), "thin": base["days"] < MIN_BASE_DAYS, "findings": findings, "counts": counts, "notes": notes,
            "baseline": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in base.items()},
            "yesterday": {"date": yesterday.isoformat(), "clicks": y["clicks"], "conv": round(y["conv"], 2), "cost": round(y["cost"], 2)}, "days": chart,
            "actions": actions, "lists": lists, "pixels": pixels, "autoTagging": raw.get("autoTagging"), "expectedToday": expected}


def snapshot_of(res, raw):
    """What the daily check keeps of one account: the score and the counts, and every list's size (what the list rules read on later days)."""
    return {"score": res["score"], "status": res["status"], "counts": res["counts"], "conv": res["yesterday"]["conv"], "clicks": res["yesterday"]["clicks"],
            "lists": {str(l["id"]): l["size"] for l in (raw.get("lists") or []) if l.get("size") is not None and str(l.get("kind") or "").upper() in LIST_KINDS}}
