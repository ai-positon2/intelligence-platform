"""Paid Marketing Agents: Brand Spend Alert, the readings that explain a move in the brand spend (no network, no clock but the one passed in).

paid_brandspend_math.py says that the brand spend moved and whether it was clicks or their price. This says what else the same rows show about why, and what the account
itself has agreed:

  efficiency  did the conversions move with the spend, or was it more money for the same result;
  share       did the brand lose impression share and to what (a competitor's rank, or the budget), or did it win the same searches at a higher price;
  spikes      single days far above the usual day, and whether their clicks converted;
  month       what the month is on pace to cost, against a monthly brand budget the team saved;
  limits      last week against a weekly floor and ceiling the team saved;
  leak        brand searches that non-brand campaigns paid for, at what price against the brand campaign's own, from the search terms read for the team's brand words;
  inventory   brand campaigns that have no spend at all (paused, or enabled and silent), which the daily rows alone cannot show;
  history     how many days in a row the account has been outside its band, from the daily figures the monitor saved.

A campaign-day row is {id, name, date, cost, clicks, impressions, conversions, share, lostBudget, lostRank}. Every reading says what it could not measure instead of guessing.
"""
from __future__ import annotations

import calendar
import datetime as dt

from . import paid_brandis_math as bis
from . import paid_brandspend_math as m

SPIKE_X = 2.5                   # a day this many times the usual day is a spike
SPIKE_DAYS = 28                 # the days looked at for spikes
MAX_SPIKES = 3
RANK_RISE = 0.10                # lost-to-rank share rising by this much with the share falling names a competitor
SHARE_DROP = 0.05
STEADY = 0.03                   # a share that moved less than this is steady
MONTH_MIN_DAYS = 3              # days of the month needed before it is projected
PACE_OVER = 1.05
PACE_UNDER = 0.80
LEAK_DAYS = 30
LEAK_SHARE = 0.10               # leaked spend this share of the brand spend (or an overpay worth a swing) is medium
MAX_LEAK_CAMPAIGNS = 5
SAME_RESULT = 1.15              # conversions up by less than this while spend rose: the same result for more
FOLLOWED = 0.60                 # conversions rose by this share of the spend's rise: the spend followed demand
MIN_SHARE_WEEKS = 3


# ── Efficiency ──────────────────────────────────────────────────────────────

def efficiency(now, base_weeks, status):
    """{conv, usualConv, cpa, usualCpa, ratioConv, kind} for the last week against the usual one, or None without a usual week or with no conversions ever.
    kind: for a rise "followed" (conversions rose with it), "same-for-more" (about the same result for more money) or "partly"; for a fall "same-for-less" or "lost"; None when it is
    not over or under."""
    weeks = [w for w in base_weeks if w["cost"] > 0]
    usual_conv = m.median([w.get("conversions") or 0.0 for w in weeks]) if weeks else None
    if not weeks or not usual_conv:
        return None
    usual_cost = m.median([w["cost"] for w in weeks])
    conv = now.get("conversions") or 0.0
    ratio_c = conv / usual_conv
    ratio_s = now["cost"] / usual_cost if usual_cost else None
    kind = None
    if status == "over" and ratio_s:
        kind = "followed" if ratio_c >= 1 + FOLLOWED * (ratio_s - 1) else "same-for-more" if ratio_c <= SAME_RESULT else "partly"
    elif status in ("under", "stopped"):
        kind = "same-for-less" if ratio_c >= 0.85 else "lost"
    return {"conv": round(conv, 1), "usualConv": round(usual_conv, 1), "cpa": round(now["cost"] / conv, 2) if conv else None,
            "usualCpa": round(usual_cost / usual_conv, 2), "ratioConv": round(ratio_c, 4), "kind": kind}


# ── Impression share ────────────────────────────────────────────────────────

def weekly_share(rows, wk):
    """[{share, lostRank, lostBudget, eligible}] one per week, from the rows' own shares put together by eligible searches (the Brand IS Monitor's way)."""
    out = []
    for monday, sunday in wk:
        c = bis.combine([r for r in rows if monday.isoformat() <= str(r.get("date"))[:10] <= sunday.isoformat()])
        out.append({"share": c["share"], "lostRank": c["lostRank"], "lostBudget": c["lostBudget"], "eligible": c["eligible"]})
    return out


def share_view(weeks):
    """{now, usual, drop, lostRank, usualLostRank, lostBudget, usualLostBudget, signal} from weekly_share, or None when the last week or the weeks before it have too few searches.
    signal: "competitor" (the loss to rank rose and the share fell), "budget" (the loss to budget did), "steady" (the share held) or None."""
    now, prev = weeks[-1], [w for w in weeks[:-1] if w["share"] is not None and w["eligible"] >= bis.MIN_ELIGIBLE]
    if now["share"] is None or now["eligible"] < bis.MIN_ELIGIBLE or len(prev) < MIN_SHARE_WEEKS:
        return None
    usual = m.median([w["share"] for w in prev])
    ul_rank = m.median([w["lostRank"] for w in prev if w["lostRank"] is not None]) or 0.0
    ul_bud = m.median([w["lostBudget"] for w in prev if w["lostBudget"] is not None]) or 0.0
    lr, lb = now["lostRank"] or 0.0, now["lostBudget"] or 0.0
    drop = round(usual - now["share"], 4)
    signal = None
    if drop >= SHARE_DROP - 1e-9 and lr - ul_rank >= RANK_RISE - 1e-9:
        signal = "competitor"
    elif drop >= SHARE_DROP - 1e-9 and lb - ul_bud >= RANK_RISE - 1e-9:
        signal = "budget"
    elif abs(drop) < STEADY:
        signal = "steady"
    return {"now": round(now["share"], 4), "usual": round(usual, 4), "drop": drop, "lostRank": round(lr, 4), "usualLostRank": round(ul_rank, 4), "lostBudget": round(lb, 4),
            "usualLostBudget": round(ul_bud, 4), "signal": signal}


# ── Spikes ──────────────────────────────────────────────────────────────────

def spikes(rows, today):
    """The days of the last SPIKE_DAYS far above the usual day: [{date, weekday, cost, usual, clicks, usualClicks, conversions, kind}], the dearest first.
    kind: "unconverted" (clicks at least doubled and nothing converted: the shape of clicks that are not people), "converting" or "price" (the clicks did not rise)."""
    by = {}
    for r in rows:
        d = by.setdefault(str(r.get("date"))[:10], {"cost": 0.0, "clicks": 0.0, "conv": 0.0})
        d["cost"] += m._f(r.get("cost"))
        d["clicks"] += m._f(r.get("clicks"))
        d["conv"] += m._f(r.get("conversions"))
    live = [v for v in by.values() if v["cost"] > 0]
    if len(live) < 14:
        return []
    usual, usual_clicks = m.median([v["cost"] for v in live]), m.median([v["clicks"] for v in live])
    usual_conv = m.median([v["conv"] for v in live])
    first = (today - dt.timedelta(days=SPIKE_DAYS)).isoformat()
    out = []
    for day, v in by.items():
        if day < first or day >= today.isoformat():
            continue
        if v["cost"] >= SPIKE_X * usual and v["cost"] - usual >= m.MIN_SWING:
            x = v["clicks"] / usual_clicks if usual_clicks else None
            kind = "price" if x is not None and x < 1.5 else "unconverted" if (x or 0) >= 2 and v["conv"] == 0 else "converting"
            out.append({"date": day, "weekday": dt.date.fromisoformat(day).strftime("%A"), "cost": round(v["cost"], 2), "usual": round(usual, 2), "clicks": round(v["clicks"]),
                        "usualClicks": round(usual_clicks), "conversions": round(v["conv"], 1), "usualConv": round(usual_conv, 1), "kind": kind})
    out.sort(key=lambda s: (-s["cost"], s["date"]))
    return out[:MAX_SPIKES]


# ── The month and the limits the team saved ─────────────────────────────────

def month_view(rows, today, budget=None):
    """{spent, days, daysInMonth, projected, perDay, budget, status, runsOutOn} for this month so far (through yesterday), or None before MONTH_MIN_DAYS days of it.
    status (only with a budget): "over" (already spent more), "pace-over" (on pace to pass it by more than 5%), "pace-under" (on pace to use under 80% of it) or "ok"."""
    elapsed = today.day - 1
    if elapsed < MONTH_MIN_DAYS:
        return None
    first = today.replace(day=1).isoformat()
    spent = sum(m._f(r.get("cost")) for r in rows if first <= str(r.get("date"))[:10] < today.isoformat())
    dim = calendar.monthrange(today.year, today.month)[1]
    per_day = spent / elapsed
    out = {"spent": round(spent, 2), "days": elapsed, "daysInMonth": dim, "perDay": round(per_day, 2), "projected": round(per_day * dim, 2), "budget": None, "status": None, "runsOutOn": None}
    if budget and budget > 0:
        out["budget"] = round(float(budget), 2)
        if spent > budget:
            out["status"] = "over"
        elif per_day * dim > PACE_OVER * budget:
            out["status"] = "pace-over"
            if per_day > 0:
                out["runsOutOn"] = (today + dt.timedelta(days=int((budget - spent) / per_day))).isoformat()
        elif per_day * dim < PACE_UNDER * budget:
            out["status"] = "pace-under"
        else:
            out["status"] = "ok"
    return out


def limits_view(last_week_cost, weekly_min=None, weekly_max=None):
    """{min, max, cost, status, by} for the last whole week against the floor and ceiling the team saved, or None when none is set. status "over", "under" or "ok"."""
    if not weekly_min and not weekly_max:
        return None
    status, by = "ok", 0.0
    if weekly_max and last_week_cost > weekly_max:
        status, by = "over", last_week_cost - weekly_max
    elif weekly_min and last_week_cost < weekly_min:
        status, by = "under", weekly_min - last_week_cost
    return {"min": weekly_min or None, "max": weekly_max or None, "cost": round(last_week_cost, 2), "status": status, "by": round(by, 2)}


# ── Brand searches in other campaigns ───────────────────────────────────────

def terms_of(text):
    """The brand words from what the team typed: lower case, 3 to 40 characters, no repeats, at most 20."""
    raw = [t.strip().lower() for t in str(text or "").replace(";", ",").replace("\n", ",").split(",")]
    out = []
    for t in raw:
        if 3 <= len(t) <= 40 and t not in out:
            out.append(t)
    return out[:20]


_GENERIC = {"brand", "branded", "search", "campaign", "core", "exact", "phrase", "broad", "google", "ads", "the", "and", "for", "usa", "ppc", "sem", "main", "local", "keywords", "terms", "name"}


def suggest_terms(names):
    """Words that a brand campaign's name suggests are the brand ("Northwind | Brand | Search" gives northwind), for the page to offer; never used unless the team saves them."""
    out = []
    for n in names:
        for t in str(n or "").lower().replace("|", " ").replace("_", " ").replace("-", " ").replace("/", " ").split():
            t = t.strip(".,:;()[]")
            if len(t) >= 4 and t.isalpha() and t not in _GENERIC and t not in out:
                out.append(t)
    return out[:6]


def leak(rows, terms, brand_ids, brand_cpc, brand_cost):
    """The searches that held a brand word and were paid for by a campaign that is not a brand campaign:
    {terms, cost, clicks, conversions, cpc, brandCpc, overpay, shareOfBrand, campaigns: [{id, name, cost, clicks, conversions, cpc, top: [search terms]}]} or None when there are none.
    rows are {campaignId, campaign, term, cost, clicks, conversions} of the last LEAK_DAYS days; overpay is the clicks times what each cost over the brand campaign's own price."""
    if not terms:
        return None
    by = {}
    for r in rows:
        if str(r["campaignId"]) in brand_ids:
            continue
        t = str(r["term"]).lower()
        if not any(w in t for w in terms) or m._f(r["cost"]) <= 0:
            continue
        c = by.setdefault(str(r["campaignId"]), {"id": str(r["campaignId"]), "name": r["campaign"], "cost": 0.0, "clicks": 0.0, "conversions": 0.0, "top": {}})
        c["cost"] += m._f(r["cost"])
        c["clicks"] += m._f(r["clicks"])
        c["conversions"] += m._f(r.get("conversions"))
        c["top"][t] = c["top"].get(t, 0.0) + m._f(r["cost"])
    if not by:
        return None
    camps = []
    for c in by.values():
        top = sorted(c["top"].items(), key=lambda kv: (-kv[1], kv[0]))
        camps.append({"id": c["id"], "name": c["name"], "cost": round(c["cost"], 2), "clicks": round(c["clicks"]), "conversions": round(c["conversions"], 1),
                      "cpc": round(c["cost"] / c["clicks"], 2) if c["clicks"] else None, "top": [t for t, _v in top[:3]]})
    camps.sort(key=lambda c: (-c["cost"], c["name"].lower()))
    cost, clicks = sum(c["cost"] for c in camps), sum(c["clicks"] for c in camps)
    cpc = cost / clicks if clicks else None
    over = clicks * max(0.0, cpc - brand_cpc) if cpc is not None and brand_cpc else None
    return {"terms": list(terms), "days": LEAK_DAYS, "cost": round(cost, 2), "clicks": int(clicks), "conversions": round(sum(c["conversions"] for c in camps), 1),
            "cpc": round(cpc, 2) if cpc is not None else None, "brandCpc": round(brand_cpc, 2) if brand_cpc else None, "overpay": round(over, 2) if over is not None else None,
            "shareOfBrand": round(cost / brand_cost, 4) if brand_cost else None, "campaigns": camps[:MAX_LEAK_CAMPAIGNS], "campaignCount": len(camps)}


# ── Campaigns the daily rows cannot show ────────────────────────────────────

def silent_campaigns(campaigns, brand_ids, spent_ids):
    """[{id, name, status}] of the brand campaigns Google lists that have no spend row at all in the window, enabled ones first."""
    out = [{"id": str(c["id"]), "name": c["name"], "status": str(c.get("status") or "")} for c in campaigns if str(c["id"]) in brand_ids and str(c["id"]) not in spent_ids]
    out.sort(key=lambda c: (c["status"] != "ENABLED", c["name"].lower()))
    return out


# ── History ─────────────────────────────────────────────────────────────────

OUTSIDE = ("over", "under", "stopped")


def history_view(snaps):
    """{days, streak, since, worst, statuses} from the daily figures the monitor saved for an account (oldest first, each {day, status, ratio, cost}), or None without any.
    streak is how many days in a row, ending with the latest, the account has been outside its band; since is the first of them."""
    if not snaps:
        return None
    snaps = sorted(snaps, key=lambda s: s["day"])
    streak, since = 0, None
    for s in reversed(snaps):
        if s.get("status") in OUTSIDE:
            streak += 1
            since = s["day"]
        else:
            break
    outside = [s for s in snaps if s.get("status") in OUTSIDE]
    return {"days": len(snaps), "streak": streak, "since": since, "outsideDays": len(outside), "first": snaps[0]["day"],
            "statuses": [{"day": s["day"], "status": s.get("status"), "cost": s.get("cost")} for s in snaps[-60:]]}
