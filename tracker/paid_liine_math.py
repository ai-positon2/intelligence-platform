"""Paid Marketing Agents: Liine Lead Analysis, the arithmetic.

Liine (call tracking for medical practices) is read through Claude's own Liine connector, which writes one csv per run into a
Drive folder (see tracker/paid_liine.py). This module turns those rows into what the page shows. It is plain arithmetic on
the rows: no model is asked to find anything, and a figure Liine hid (an empty cell) stays unknown, it is never counted as 0.

Row layout (header): table,practice_id,practice,period,key,leads,booked,spend,clicks,value,note
  meta, practices, totals, monthly, channels, notbooked, missed, ppc, forms
"""
from __future__ import annotations

import datetime as dt
import re

REQUIRED = ("meta", "practices", "totals", "monthly", "channels", "notbooked", "missed", "ppc")

STALE_AFTER_HOURS = 72

# thresholds, kept here so the page and the tests quote the same numbers
NOT_CALLED_SHARE = 0.25      # a practice where a quarter of the leads were never called
NOT_CALLED_HIGH = 0.50
NOT_CALLED_MIN = 30
RATE_DROP_PTS = 8.0          # booking rate down this many points on the 90 days before
RATE_DROP_HIGH = 15.0
RATE_MIN_LEADS = 100
WASTE_SPEND_MIN = 500.0      # a paid campaign that spent this much and booked nobody
WASTE_PRACTICE_MIN = 3000.0
MISSED_RATE = 0.15
MISSED_HIGH = 0.25
CHANNEL_MIN_LEADS = 100
CHANNEL_LOW_RATIO = 0.5
HANDLING_SHARE = 0.10

# why a lead did not book, by who can change it
HANDLING = ("Took a Message OR Answering Service", "Transferred Call Not Answered", "Not Reached",
            "Dropped Call / Hang Up", "Scheduling Issue")
FIT = ("Insurance Not Accepted", "Insurance Not Accepted - Medicaid", "Insurance Not Accepted - Medicare",
       "Treatment Not Offered", "Location Too Far", "Not a Candidate", "Called Wrong Location",
       "Getting Info for Someone Else", "Inquiring About Event", "Walk-In Inquiry")
NURTURE = ("Caller Procrastination", "Price Objection", "Records Needed", "Referral Needed", "Meant to Call Competitor",
           "Credit Card Not Provided", "Caller Verifying Insurance", "Office Verifying Insurance", "Provider Not Available",
           "Booking Online")


def num(v):
    """A cell as a number, or None for an empty or unreadable one (Liine's hidden cells are empty)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(v):
    n = num(v)
    return None if n is None else int(round(n))


def rate(part, whole):
    return None if part is None or not whole else part / whole


def reason_group(reason):
    if reason == "Booked":
        return "booked"
    if reason == "Not Called":
        return "not_called"
    if reason in HANDLING:
        return "handling"
    if reason in FIT:
        return "fit"
    if reason in NURTURE:
        return "nurture"
    return "other"


def parse(rows):
    """The csv rows (dicts of strings) as one dataset. Rows of an unknown table are ignored."""
    ds = {"meta": {}, "practices": {}, "totals": {}, "monthly": {}, "channels": {}, "notbooked": {}, "missed": {}, "ppc": {},
          "forms": {}, "tables": set()}
    for r in rows:
        t = (r.get("table") or "").strip()
        if not t:
            continue
        ds["tables"].add(t)
        pid = (r.get("practice_id") or "").strip()
        per = (r.get("period") or "").strip()
        key = (r.get("key") or "").strip()
        if t == "meta":
            ds["meta"][key] = {"period": per, "value": (r.get("value") or "").strip(), "note": (r.get("note") or "").strip()}
        elif t == "practices":
            note = r.get("note") or ""
            flags = dict(p.split("=", 1) for p in note.split(";") if "=" in p)
            ds["practices"][pid] = {"id": pid, "name": (r.get("practice") or "").strip(), "org": key, "start": per,
                                    "ppc": flags.get("ppc") == "1", "formflow": flags.get("formflow") == "1"}
        elif t == "totals":
            ds["totals"][(pid, per)] = (_int(r.get("leads")), _int(r.get("booked")))
        elif t == "monthly":
            ds["monthly"].setdefault(pid, {})[per] = (_int(r.get("leads")), _int(r.get("booked")))
        elif t == "channels":
            ds["channels"].setdefault((pid, per), {})[key] = (_int(r.get("leads")), _int(r.get("booked")))
        elif t == "notbooked":
            ds["notbooked"].setdefault((pid, per), {})[key] = _int(r.get("leads"))
        elif t == "missed":
            ds["missed"].setdefault((pid, per), {})[key] = num(r.get("value"))
        elif t == "ppc":
            ds["ppc"].setdefault((pid, per), []).append({
                "key": key, "leads": _int(r.get("leads")), "booked": _int(r.get("booked")), "spend": num(r.get("spend")),
                "clicks": _int(r.get("clicks")), "impressions": _int(r.get("value")),
                "platform": dict(p.split("=", 1) for p in (r.get("note") or "").split(";") if "=" in p).get("platform", "")})
        elif t == "forms":
            ds["forms"].setdefault((pid, per), {})[key] = num(r.get("value"))
    return ds


def missing_tables(ds):
    return [t for t in REQUIRED if t not in ds["tables"]]


def _meta(ds, key, field="period"):
    return (ds["meta"].get(key) or {}).get(field, "")


def _age_hours(pulled, now):
    try:
        p = dt.datetime.strptime(pulled, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return max(0.0, (now - p).total_seconds() / 3600.0)


def _spend(rows):
    """(spend, leads, booked) of a practice's paid rows, from its own total row; None where Liine gave none."""
    for r in rows:
        if r["key"] == "__total__":
            return r["spend"], r["leads"], r["booked"]
    return None, None, None


def practice_view(ds, pid, period):
    p = ds["practices"][pid]
    leads, booked = ds["totals"].get((pid, period), (None, None))
    nb = ds["notbooked"].get((pid, period), {})
    not_called = nb.get("Not Called")
    spend, pl, pb = _spend(ds["ppc"].get((pid, period), []))
    m = ds["missed"].get((pid, period), {})
    vm = m.get("voicemail_interactions")
    calls = m.get("inbound_analyzed_call_interactions")
    f = ds["forms"].get((pid, period), {})
    return {
        "id": pid, "name": p["name"], "leads": leads, "booked": booked, "rate": rate(booked, leads),
        "notCalled": not_called, "notCalledShare": rate(not_called, leads),
        "spend": spend, "paidLeads": pl, "paidBooked": pb,
        "costPerBooked": (spend / pb) if spend and pb else None,
        "voicemails": None if vm is None else int(vm), "inboundCalls": None if calls is None else int(calls),
        "missedRate": m.get("missed_call_rate") if m.get("missed_call_rate") is not None else rate(vm, calls),
        "recoveredDay": m.get("within_1_day_recovered_rate"), "recoveredHour": m.get("under_1_hour_recovered_rate"),
        "formLeads": f.get("total_leads"), "formCalledShare": f.get("perc_forms_called_or_texted"),
        "formMedianSeconds": f.get("median_speed_to_lead_seconds"),
    }


def monthly_series(ds, now_month, end_day_is_month_end):
    """Per practice and for all together, one point per month. A month is `incomplete` when a practice that had started
    has no figure for it (Liine hid the cell), so the total of that month is not a total; the running month is `partial`."""
    pids = sorted(ds["practices"], key=lambda i: ds["practices"][i]["name"])
    months = sorted({m for pid in pids for m in ds["monthly"].get(pid, {})})
    out = {"all": [], "byPractice": {}}
    for pid in pids:
        start = ds["practices"][pid]["start"][:7]
        pts = []
        for m in months:
            if m < start:
                continue
            cell = ds["monthly"].get(pid, {}).get(m)
            leads, booked = cell if cell else (None, None)
            pts.append({"month": m, "leads": leads, "booked": booked, "rate": rate(booked, leads),
                        "partial": m == now_month and not end_day_is_month_end})
        out["byPractice"][pid] = pts
    for m in months:
        leads = booked = 0
        hidden = 0
        for pid in pids:
            if m < ds["practices"][pid]["start"][:7]:
                continue
            cell = ds["monthly"].get(pid, {}).get(m)
            if not cell or cell[0] is None or cell[1] is None:
                hidden += 1
                continue
            leads += cell[0]
            booked += cell[1]
        out["all"].append({"month": m, "leads": leads, "booked": booked, "rate": rate(booked, leads), "hidden": hidden,
                           "partial": m == now_month and not end_day_is_month_end})
    return out


def channel_table(ds, period, pids=None):
    """Channels over the chosen practices: leads and booked summed over the cells Liine showed. `hidden` counts the cells it
    hid, and the rate uses only cells where both numbers are known."""
    agg = {}
    for pid in (pids or ds["practices"]):
        for ch, (leads, booked) in ds["channels"].get((pid, period), {}).items():
            a = agg.setdefault(ch, {"channel": ch, "leads": 0, "booked": 0, "rateLeads": 0, "rateBooked": 0, "hidden": 0})
            if leads is None:
                a["hidden"] += 1
                continue
            a["leads"] += leads
            if booked is None:
                continue
            a["booked"] += booked
            a["rateLeads"] += leads
            a["rateBooked"] += booked
    rows = []
    for a in agg.values():
        a["rate"] = rate(a["rateBooked"], a["rateLeads"]) if a["rateLeads"] else None
        rows.append(a)
    rows.sort(key=lambda r: -r["leads"])
    return rows


def reason_table(ds, period, pids=None):
    agg = {}
    hidden = 0
    for pid in (pids or ds["practices"]):
        for reason, n in ds["notbooked"].get((pid, period), {}).items():
            if n is None:
                hidden += 1
                continue
            agg[reason] = agg.get(reason, 0) + n
    rows = [{"reason": r, "group": reason_group(r), "leads": n} for r, n in agg.items()]
    rows.sort(key=lambda r: -r["leads"])
    return rows, hidden


def campaign_table(ds, period, pids=None):
    rows = []
    for pid in (pids or ds["practices"]):
        for r in ds["ppc"].get((pid, period), []):
            if r["key"] in ("__total__", "Not Matched"):
                continue
            spend, leads, booked = r["spend"], r["leads"], r["booked"]
            rows.append({"practice": ds["practices"][pid]["name"], "practiceId": pid, "campaign": r["key"],
                         "platform": r["platform"], "spend": spend, "leads": leads, "booked": booked,
                         "clicks": r["clicks"], "impressions": r["impressions"],
                         "costPerLead": (spend / leads) if spend and leads else None,
                         "costPerBooked": (spend / booked) if spend and booked else None})
    rows.sort(key=lambda r: -(r["spend"] or 0))
    return rows


def _plural(n, word):
    if n is None:
        return "? %ss" % word
    return "%s %s%s" % (format(n, ","), word, "" if n == 1 else "s")


def _money(n):
    return "${:,.0f}".format(n)


def _pct(x, digits=0):
    return ("{:." + str(digits) + "f}%").format(x * 100)


def findings(ds):
    """The things worth acting on, most serious first. Every one carries the numbers behind it."""
    out = []
    pcts = {}
    for pid in ds["practices"]:
        pcts[pid] = (practice_view(ds, pid, "last90"), practice_view(ds, pid, "prev90"))

    for pid, (a, b) in pcts.items():
        name = a["name"]
        # 1. leads nobody called
        nc, share = a["notCalled"], a["notCalledShare"]
        if nc is not None and share is not None and nc >= NOT_CALLED_MIN and share >= NOT_CALLED_SHARE:
            ev = ["%s of %s new leads (%s) are form leads marked Not Called in the last 90 days." % (
                format(nc, ","), format(a["leads"], ","), _pct(share))]
            if a["formCalledShare"] is not None and a["formLeads"]:
                ev.append("Form leads: only %s were called or texted (%s of %s)." % (
                    _pct(a["formCalledShare"]), format(int(round(a["formCalledShare"] * a["formLeads"])), ","), format(int(a["formLeads"]), ",")))
            now = ds["forms"].get((pid, "now"), {})
            if now.get("no_outreach_active_leads_count"):
                ev.append("Right now %s active form leads have had no outreach at all." % format(int(now["no_outreach_active_leads_count"]), ","))
            ev.append("What it could be worth: if only 1 in 10 of them booked that would be about %s more patients, if 2 in 10 about %s (illustration, not a forecast)." % (
                format(int(round(nc * 0.1)), ","), format(int(round(nc * 0.2)), ",")))
            out.append({"id": "notcalled", "severity": "high" if share >= NOT_CALLED_HIGH else "medium", "practiceId": pid, "practice": name,
                        "title": "%s: most leads were never called" % name,
                        "why": "A person who filled in a form and is never called rarely books. Liine marks these leads Not Called, and they are the biggest block of lost bookings here.",
                        "evidence": ev,
                        "action": "Have the front desk call every form lead the same day and work through the leads still waiting. Check that form alerts reach a person."})
        # 2. booking rate fell
        if a["rate"] is not None and b["rate"] is not None and (a["leads"] or 0) >= RATE_MIN_LEADS and (b["leads"] or 0) >= RATE_MIN_LEADS:
            drop = round((b["rate"] - a["rate"]) * 100.0, 6)
            if drop >= RATE_DROP_PTS:
                ev = ["Booked %s of new leads in the last 90 days, against %s in the 90 days before (down %.0f points)." % (_pct(a["rate"]), _pct(b["rate"]), drop),
                      "Leads: %s now, %s before." % (format(a["leads"], ","), format(b["leads"], ","))]
                if ds["practices"][pid]["start"] > _meta(ds, "prev90_start"):
                    ev.append("This practice's data starts on %s, so the earlier period is shorter than 90 days." % ds["practices"][pid]["start"])
                out.append({"id": "ratefall", "severity": "high" if drop >= RATE_DROP_HIGH else "medium", "practiceId": pid, "practice": name,
                            "title": "%s: fewer leads are booking" % name,
                            "why": "More people are getting in touch but a smaller share book. Look at what happens after the lead arrives before changing the ads.",
                            "evidence": ev,
                            "action": "Compare the reasons leads did not book now and before (below), and check the follow-up of form leads and missed calls."})
        # 3. paid spend that booked nobody
        rows = [r for r in ds["ppc"].get((pid, "last90"), []) if r["key"] not in ("__total__", "Not Matched")]
        total_spend = _spend(ds["ppc"].get((pid, "last90"), []))[0]
        wasted = [r for r in rows if r["spend"] and r["spend"] >= WASTE_SPEND_MIN and not r["booked"]]
        wasted_spend = sum(r["spend"] for r in wasted)
        if total_spend and wasted_spend >= WASTE_PRACTICE_MIN:
            wasted.sort(key=lambda r: -r["spend"])
            ev = ["%s of %s paid spend (%s) went to %d campaigns with no booked patient in 90 days." % (
                _money(wasted_spend), _money(total_spend), _pct(wasted_spend / total_spend), len(wasted))]
            for r in wasted[:4]:
                ev.append("%s: %s spent, %s, 0 booked." % (r["key"], _money(r["spend"]), _plural(r["leads"], "lead")))
            out.append({"id": "paidwaste", "severity": "high" if wasted_spend / total_spend >= 0.4 else "medium", "practiceId": pid, "practice": name,
                        "title": "%s: paid campaigns that booked nobody" % name,
                        "why": "Leads are counted by the first ad click that brought them. Ad cost is Google Ads (and Microsoft Ads or ChatGPT Ads where linked) as Liine imports it.",
                        "evidence": ev,
                        "action": "Look at these campaigns first: pause or fix the ones that bring clicks and no real enquiries. Check first that their leads were followed up (see above)."})
        # 4. missed calls
        mr = a["missedRate"]
        if mr is not None and mr >= MISSED_RATE and (a["voicemails"] or 0) >= 30:
            ev = ["%s of inbound calls (%s of %s) went to voicemail in business hours." % (_pct(mr), format(a["voicemails"], ","), format(a["inboundCalls"], ",") if a["inboundCalls"] else "?")]
            if a["recoveredDay"] is not None:
                ev.append("Only %s of the people who hit voicemail were seen booking or getting through within a day." % _pct(a["recoveredDay"]))
            elif a["recoveredHour"] is not None:
                ev.append("Only %s of the people who hit voicemail were seen booking or getting through within an hour." % _pct(a["recoveredHour"]))
            out.append({"id": "missed", "severity": "high" if mr >= MISSED_HIGH else "medium", "practiceId": pid, "practice": name,
                        "title": "%s: too many calls go to voicemail" % name,
                        "why": "A caller who reaches voicemail often calls the next practice. Calls in business hours are the ones the practice can still fix.",
                        "evidence": ev,
                        "action": "Check phone staffing in the busiest hours, add a second line or overflow to a call centre, and call back voicemails within the hour."})
        # 5. handling reasons (not the Not Called line)
        nb = ds["notbooked"].get((pid, "last90"), {})
        h = sum(n for r, n in nb.items() if r in HANDLING and n is not None)
        if a["leads"] and h / a["leads"] >= HANDLING_SHARE and h >= 30:
            top = sorted(((n, r) for r, n in nb.items() if r in HANDLING and n), reverse=True)[:3]
            out.append({"id": "handling", "severity": "medium", "practiceId": pid, "practice": name,
                        "title": "%s: leads lost in how the call was handled" % name,
                        "why": "These are reasons the practice controls: a message taken instead of a booking, a transfer nobody answered, a call dropped.",
                        "evidence": ["%s leads (%s) were lost this way: %s." % (format(h, ","), _pct(h / a["leads"]), ", ".join("%s %s" % (r, format(n, ",")) for n, r in top))],
                        "action": "Coach the front desk to book on the first call and not to take messages, and check that transferred calls are picked up."})
        # 6. a channel with volume and a poor booking rate
        for ch in channel_table(ds, "last90", [pid]):
            if ch["channel"] in ("Not attributed",) or not a["rate"] or ch["rate"] is None:
                continue
            if ch["rateLeads"] >= CHANNEL_MIN_LEADS and ch["rate"] <= a["rate"] * CHANNEL_LOW_RATIO:
                out.append({"id": "channel", "severity": "medium", "practiceId": pid, "practice": name,
                            "title": "%s: %s brings leads that rarely book" % (name, ch["channel"]),
                            "why": "This channel produces many leads but its booking rate is under half the practice's own.",
                            "evidence": ["%s: %s leads, %s booked (%s), against %s for the whole practice." % (ch["channel"], format(ch["rateLeads"], ","), format(ch["rateBooked"], ","), _pct(ch["rate"]), _pct(a["rate"]))],
                            "action": "Find out whether these leads are being followed up as well as the others before judging the channel. If they are, review what the channel promises."})
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: (order[f["severity"]], f["practice"], f["id"]))
    return out


def analyse(ds, now=None):
    """The whole page: headline, practice table, findings, months, channels, reasons and campaigns."""
    now = now or dt.datetime.utcnow()
    end = _meta(ds, "last90_end")
    try:
        end_d = dt.date.fromisoformat(end)
    except ValueError:
        end_d = now.date()
    next_day = end_d + dt.timedelta(days=1)
    month_end = next_day.month != end_d.month
    now_month = end_d.strftime("%Y-%m")
    practices = []
    for pid in sorted(ds["practices"], key=lambda i: ds["practices"][i]["name"]):
        a = practice_view(ds, pid, "last90")
        b = practice_view(ds, pid, "prev90")
        a["prev"] = {k: b[k] for k in ("leads", "booked", "rate", "notCalledShare", "spend", "missedRate")}
        a["ratePtsChange"] = (a["rate"] - b["rate"]) * 100 if a["rate"] is not None and b["rate"] is not None else None
        practices.append(a)

    def total(key, items=practices):
        vals = [p[key] for p in items]
        return None if any(v is None for v in vals) else sum(vals)

    leads, booked = total("leads"), total("booked")
    pl = sum((ds["totals"].get((pid, "prev90"), (None, None))[0] or 0) for pid in ds["practices"])
    pb = sum((ds["totals"].get((pid, "prev90"), (None, None))[1] or 0) for pid in ds["practices"])
    spend_known = [p["spend"] for p in practices if p["spend"] is not None]
    paid_booked = sum((p["paidBooked"] or 0) for p in practices if p["spend"] is not None)
    nc = total("notCalled")
    vm = sum(p["voicemails"] or 0 for p in practices)
    calls = sum(p["inboundCalls"] or 0 for p in practices)
    headline = {
        "leads": leads, "booked": booked, "rate": rate(booked, leads),
        "prevLeads": pl, "prevBooked": pb, "prevRate": rate(pb, pl),
        "spend": sum(spend_known) if spend_known else None,
        "paidBooked": paid_booked if spend_known else None,
        "costPerBooked": (sum(spend_known) / paid_booked) if spend_known and paid_booked else None,
        "notCalled": nc, "notCalledShare": rate(nc, leads),
        "voicemails": vm, "inboundCalls": calls, "missedRate": rate(vm, calls),
    }
    pulled = _meta(ds, "pulled_at", "value")
    age = _age_hours(pulled, now)
    reasons, reasons_hidden = reason_table(ds, "last90")
    reasons_prev, _ = reason_table(ds, "prev90")
    months = monthly_series(ds, now_month, month_end)
    notes = []
    if age is not None and age > STALE_AFTER_HOURS:
        notes.append({"level": "warn", "text": "This data was pulled %d days ago. Ask Claude to refresh the Liine data (see below)." % int(age // 24)})
    if any(p["leads"] is None for p in practices):
        notes.append({"level": "warn", "text": "Some practices have no total in this pull, so the headline numbers are left blank."})
    detail = {}
    for pid in ds["practices"]:
        rs, hid = reason_table(ds, "last90", [pid])
        rp, _ = reason_table(ds, "prev90", [pid])
        detail[pid] = {"channels": channel_table(ds, "last90", [pid]), "reasons": rs, "reasonsPrev": rp, "reasonsHidden": hid,
                       "campaigns": campaign_table(ds, "last90", [pid])}
    return {
        "detail": detail,
        "asOf": {"pulledAt": pulled, "ageHours": age, "last90": [_meta(ds, "last90_start"), end],
                 "prev90": [_meta(ds, "prev90_start"), _meta(ds, "prev90_end")],
                 "dateBasis": _meta(ds, "date_basis", "note"), "liineUser": _meta(ds, "liine_user", "note")},
        "headline": headline, "practices": practices, "findings": findings(ds),
        "months": months,
        "channels": channel_table(ds, "last90"), "channelsPrev": channel_table(ds, "prev90"),
        "reasons": reasons, "reasonsPrev": reasons_prev, "reasonsHidden": reasons_hidden,
        "campaigns": campaign_table(ds, "last90")[:60],
        "notes": notes,
        "thresholds": {"notCalledShare": NOT_CALLED_SHARE, "rateDropPts": RATE_DROP_PTS, "wasteSpendMin": WASTE_SPEND_MIN,
                       "missedRate": MISSED_RATE, "channelLowRatio": CHANNEL_LOW_RATIO, "staleAfterHours": STALE_AFTER_HOURS},
    }
