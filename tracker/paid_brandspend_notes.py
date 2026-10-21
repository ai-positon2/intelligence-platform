"""Paid Marketing Agents: Brand Spend Alert, what the readings of tracker/paid_brandspend_insight.py say, as findings and as sentences for the main finding (no network).

The readings are on the analysed account: efficiency, share, spikes, month, limits, leak, silent, history. Each finding states the figures it rests on and says what it could not
measure instead of guessing. The fix plan's matching steps are in tracker/paid_brandspend_plan.py.
"""
from __future__ import annotations

from . import paid_brandspend_insight as ins
from . import paid_brandspend_math as m


def _money(v, cur):
    return m.money(v, cur)


def _pct(v):
    return "%d%%" % round(v * 100)


def _first_step(a, *ids):
    for s in (a.get("plan") or {}).get("steps") or []:
        if not ids or s["id"] in ids:
            return s["title"]
    return None


def _next(a, sid, fallback):
    t = _first_step(a, sid)
    return ("Start with: %s." % t) if t else fallback


# ── Sentences for the main finding ──────────────────────────────────────────

def efficiency_sentence(a, cur):
    e = a.get("efficiency")
    if not e or not e["kind"]:
        return ""
    conv, usual = e["conv"], e["usualConv"]
    cpa = (" Cost per conversion went from %s to %s." % (_money(e["usualCpa"], cur), _money(e["cpa"], cur))) if e["cpa"] else ""
    if e["kind"] == "followed":
        return " Conversions rose too (%s against a usual %s), so the extra spend bought results.%s" % (_num(conv), _num(usual), cpa)
    if e["kind"] == "same-for-more":
        return " Conversions were about the same (%s against a usual %s): the account paid more for the same result.%s" % (_num(conv), _num(usual), cpa)
    if e["kind"] == "partly":
        return " Conversions rose only part of the way (%s against a usual %s).%s" % (_num(conv), _num(usual), cpa)
    if e["kind"] == "same-for-less":
        return " Conversions held (%s against a usual %s), so it was cheaper for the same result." % (_num(conv), _num(usual))
    return " Conversions fell too (%s against a usual %s)." % (_num(conv), _num(usual))


def _num(v):
    return ("%d" % round(v)) if abs(v - round(v)) < 0.05 else ("%.1f" % v)


def share_sentence(a):
    s = a.get("share")
    if not s or a["status"] not in ("over", "under"):
        return ""
    if s["signal"] == "competitor":
        return " The brand's impression share fell from %s to %s and the part lost to rank rose from %s to %s: that is what a competitor bidding on the brand name looks like." % (
            _pct(s["usual"]), _pct(s["now"]), _pct(s["usualLostRank"]), _pct(s["lostRank"]))
    if s["signal"] == "budget":
        return " The brand's impression share fell from %s to %s and the part lost to budget rose from %s to %s: the brand campaigns are running out of daily budget." % (
            _pct(s["usual"]), _pct(s["now"]), _pct(s["usualLostBudget"]), _pct(s["lostBudget"]))
    if s["signal"] == "steady" and a["status"] == "over" and a.get("cause") in ("cpc", "both"):
        return " The impression share held (%s, usually %s), so the same searches were won at a higher price: a bid or bidding change, or a competitor raising the price without taking searches." % (_pct(s["now"]), _pct(s["usual"]))
    return ""


def streak_sentence(a):
    h = a.get("history")
    if not h or h["streak"] < 2 or a["status"] not in ("over", "under", "stopped"):
        return ""
    return " It has now been outside its band for %d days in a row (since %s)." % (h["streak"], h["since"])


def detail_extras(a, cur):
    return efficiency_sentence(a, cur) + share_sentence(a) + streak_sentence(a)


# ── Findings ────────────────────────────────────────────────────────────────

def extra_findings(a, cur):
    """[{level, kind, title, detail, nextStep}] the readings add to an account's findings."""
    out = []
    lim = a.get("limits")
    if lim and lim["status"] != "ok":
        word, edge = ("over", lim["max"]) if lim["status"] == "over" else ("under", lim["min"])
        out.append({"level": "high", "kind": "limit-" + lim["status"], "title": "Brand spend is %s %s the agreed weekly %s of %s" % (
            _money(lim["by"], cur), word, "maximum" if lim["status"] == "over" else "minimum", _money(edge, cur)),
            "detail": "The brand campaigns spent %s from %s to %s; the limit the team saved for this account is %s." % (_money(lim["cost"], cur), a["weekFrom"], a["weekTo"], _money(edge, cur)),
            "nextStep": _next(a, "agreed", "Bring the daily budgets back inside the agreed limit.")})
    mo = a.get("month")
    if mo and mo["status"] == "over":
        out.append({"level": "high", "kind": "month-over", "title": "The brand has already spent %s this month, over its %s budget" % (_money(mo["spent"], cur), _money(mo["budget"], cur)),
                    "detail": "%d of %d days of the month have gone; at %s a day it is on pace for %s." % (mo["days"], mo["daysInMonth"], _money(mo["perDay"], cur), _money(mo["projected"], cur)),
                    "nextStep": _next(a, "pace", "Lower the daily budgets so the month ends where it was agreed.")})
    elif mo and mo["status"] == "pace-over":
        out.append({"level": "medium", "kind": "month-pace", "title": "On pace for %s this month against a %s brand budget" % (_money(mo["projected"], cur), _money(mo["budget"], cur)),
                    "detail": "%s spent in %d days (%s a day); at that rate the budget runs out on %s, %d days before the month ends." % (
                        _money(mo["spent"], cur), mo["days"], _money(mo["perDay"], cur), mo["runsOutOn"], max(0, mo["daysInMonth"] - mo["days"] - int((mo["budget"] - mo["spent"]) / mo["perDay"]))),
                    "nextStep": _next(a, "pace", "Lower the daily budgets so the month ends where it was agreed.")})
    elif mo and mo["status"] == "pace-under":
        out.append({"level": "low", "kind": "month-under", "title": "On pace to use only %s of the %s brand budget this month" % (_pct(mo["projected"] / mo["budget"]), _money(mo["budget"], cur)),
                    "detail": "%s spent in %d days (%s a day), so the month ends near %s." % (_money(mo["spent"], cur), mo["days"], _money(mo["perDay"], cur), _money(mo["projected"], cur)),
                    "nextStep": "If the brand is not short of searches, nothing to do. If it is losing impression share to budget, there is room to raise the daily budget."})
    lk = a.get("leak")
    if lk and lk["cost"] >= m.MIN_SWING:
        big = (lk["shareOfBrand"] or 0) >= ins.LEAK_SHARE or (lk["overpay"] or 0) >= m.MIN_SWING
        names = ", ".join(c["name"] for c in lk["campaigns"][:3]) + (" and %d more" % (lk["campaignCount"] - 3) if lk["campaignCount"] > 3 else "")
        price = ""
        if lk["cpc"] and lk["brandCpc"]:
            price = " at %s a click against %s in the brand campaigns%s" % (_money(lk["cpc"], cur), _money(lk["brandCpc"], cur),
                                                                         (", about %s more than the same clicks cost there" % _money(lk["overpay"], cur)) if lk["overpay"] else "")
        out.append({"level": "medium" if big else "low", "kind": "leak", "title": "Non-brand campaigns paid %s for searches that hold the brand name (last %d days)" % (_money(lk["cost"], cur), lk["days"]),
                    "detail": "%s clicks in %s%s. The brand words checked were %s." % (format(lk["clicks"], ","), names, price, ", ".join('"%s"' % t for t in lk["terms"])),
                    "nextStep": _next(a, "leak", "Add the brand name as an exact negative keyword to those campaigns so brand searches go to the brand campaign.")})
    sp = a.get("spikes") or []
    if sp:
        top = sp[0]
        bad = top["kind"] == "unconverted"
        kind_text = {"unconverted": "its clicks were %.1f times the usual and none converted: the shape of clicks that are not people" % (top["clicks"] / max(1, top["usualClicks"])),
                     "converting": "its clicks were %.1f times the usual and they converted (%s conversions against a usual %s a day): a real burst of demand" % (
                         top["clicks"] / max(1, top["usualClicks"]), _num(top["conversions"]), _num(top["usualConv"])),
                     "price": "the clicks were not unusual (%d against a usual %d), so each cost far more than usual" % (top["clicks"], top["usualClicks"])}[top["kind"]]
        out.append({"level": "medium" if bad else "low", "kind": "spike", "title": "%s %s: %s spent against a usual day of %s" % (top["weekday"], top["date"], _money(top["cost"], cur), _money(top["usual"], cur)),
                    "detail": "%s%s." % (kind_text[0].upper() + kind_text[1:], (" (%d more day%s above the usual in the last 4 weeks)" % (len(sp) - 1, "" if len(sp) == 2 else "s")) if len(sp) > 1 else ""),
                    "nextStep": _next(a, "spike", "Open the search terms and the invalid clicks for that day.")})
    s = a.get("share")
    if s and s["signal"] == "competitor" and a["status"] not in ("over", "under"):
        out.append({"level": "high" if s["drop"] >= 0.20 else "medium", "kind": "competitor", "title": "Brand impression share fell from %s to %s, mostly lost to rank" % (_pct(s["usual"]), _pct(s["now"])),
                    "detail": "The part lost to rank rose from %s to %s: that is what a competitor bidding on the brand name looks like. Spend did not move out of its band, but the brand is being shown on fewer of its own searches." % (_pct(s["usualLostRank"]), _pct(s["lostRank"])),
                    "nextStep": _next(a, "defend", "Open Auction Insights for the brand campaign to see who is bidding.")})
    for c in a.get("silent") or []:
        if c["status"] == "ENABLED":
            out.append({"level": "medium", "kind": "silent", "title": "%s is enabled but has not spent in the last 8 weeks" % c["name"],
                        "detail": "Google lists it as enabled, yet it has no spend on any day read. A brand campaign that is on and silent usually has a budget of zero, a refused ad, no billing, a bid too low to enter the auction or keywords with too little search volume.",
                        "nextStep": _next(a, "nospend", "Open the campaign's status in Google Ads and read the reason beside it.")})
        else:
            out.append({"level": "low", "kind": "silent-paused", "title": "%s is %s and has not spent in the last 8 weeks" % (c["name"], (c["status"] or "not enabled").lower()),
                        "detail": "A paused brand campaign that never spent in the window is not a problem if it is meant to be off.",
                        "nextStep": "If it should be running, enable it and check its budget and ads."})
    return out
