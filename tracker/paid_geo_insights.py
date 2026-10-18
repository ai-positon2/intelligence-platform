"""Paid Marketing Agents: Geo Performance, what the numbers mean.

For one table of locations (one campaign type, one level) this judges each location against the
account's own average, suggests a geo bid adjustment where there is enough data, and writes the
few things worth doing, each with its numbers beside it so every one can be checked. Pure functions: no
database, no network, no clock.

The judgement, and why it is built this way:

  * **Efficiency index.** A location's results per unit of spend against the account's:
        value tracked:   ROAS of the location / ROAS of the account
        otherwise:       CPA of the account / CPA of the location          (a lower CPA is better)
    100 is the average, 130 is a third better. It is relative on purpose: there is no target to
    get wrong, and a place is only ever "good" or "bad" compared with the rest of this account.
  * **Enough data first.** Fewer than MIN_CONV conversions is noise. Such a place is "Too little
    data" and is never given an adjustment; one that spent a lot and got nothing is "Leaking
    spend" only when it also had real traffic (LEAK_MIN_CLICKS clicks) and spent more than twice
    the account's CPA and 1% of the spend.
  * **Suggested bid adjustment.** 80% of the gap to average, in steps of 5%, no more than +40%
    or less than -50%, and none (0) for a place on par, an index of 90 to 110. It is a starting
    point to apply gradually and check, not an instruction: the report says how much data is
    behind each one.
"""
from __future__ import annotations

MIN_CONV = 5
STAR, GOOD, BELOW, WEAK = 1.3, 1.1, 0.9, 0.75
DAMPEN, STEP, MAX_UP, MAX_DOWN = 0.8, 0.05, 0.40, -0.50
LEAK_MIN_CLICKS, LEAK_CPA_MULT, LEAK_MIN_SHARE = 30, 2.0, 0.01
LEVEL_NOUN = {"country": "country", "region": "region", "metro": "metro area", "dma": "metro area", "city": "city",
              "county": "county", "postal_code": "postal code"}
VERDICT_LABEL = {"star": "Strong", "good": "Above average", "par": "On par", "below": "Below average", "weak": "Weak",
                 "leak": "Leaking spend", "thin": "Too little data", "none": "Not judged"}
CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "INR": "₹", "NZD": "NZ$"}


def plural(noun):
    return noun[:-1] + "ies" if noun.endswith("y") else noun + "s"


def money(v, currency="USD", dec=0):
    if v is None:
        return "Na"
    sym = CURRENCY_SYMBOL.get(currency)
    s = format(abs(v), ",.%df" % dec)
    return ("-" if v < 0 else "") + (sym + s if sym else "%s %s" % (currency, s))


def pct(v, dec=0):
    return "Na" if v is None else format(v * 100, ".%df" % dec) + "%"


def num(v):
    if v is None:
        return "Na"
    return format(round(v), ",") if abs(v - round(v)) < 0.05 else format(v, ",.1f")


MIXED_WHY = ("This account runs campaigns whose results are not the same thing (sales, leads, app installs), so a place cannot be fairly compared "
             "across them. Pick one campaign type above.")


def basis_of(total):
    """'roas' when the account tracks conversion value, 'cpa' when it only counts conversions, None when it has neither."""
    if total.get("roas") and total.get("value", 0) > 0:
        return "roas"
    if total.get("conversions", 0) > 0 and total.get("cpa"):
        return "cpa"
    return None


def index_of(row, total, basis):
    """The efficiency index (1.0 = the account's average) or None when it cannot be told."""
    if basis == "roas":
        if not row["cost"]:
            return None
        return (row["value"] / row["cost"]) / total["roas"]
    if basis == "cpa":
        if row["conversions"] <= 0:
            return 0.0 if row["cost"] > 0 else None
        return total["cpa"] / row["cpa"]
    return None


def confidence_of(conv):
    return "high" if conv >= 15 else "medium" if conv >= 8 else "low"


def adjustment_for(idx):
    """The suggested bid adjustment (a fraction: 0.25 is +25%) for an index: most of the gap, in steps, capped. (A place on par never
    gets one: judge() leaves those at zero.)"""
    raw = max(MAX_DOWN, min(MAX_UP, (idx - 1) * DAMPEN))
    stepped = round(raw / STEP) * STEP
    return 0.0 if abs(stepped) < STEP - 1e-9 else round(stepped, 2)


def judge(row, total, basis):
    """(verdict, index, adjustment, confidence, why) for one location."""
    idx = index_of(row, total, basis)
    if basis is None:
        return "none", None, None, None, "The account recorded no conversions in this range, so places cannot be judged on results."
    conv, cost = row["conversions"], row["cost"]
    if conv <= 0:
        leaking = (row["clicks"] >= LEAK_MIN_CLICKS and total.get("cpa") and cost >= LEAK_CPA_MULT * total["cpa"]
                   and cost >= LEAK_MIN_SHARE * max(total["cost"], 1e-9))
        if leaking:
            return "leak", idx, -0.5, "medium", "It spent %s on %s clicks and did not convert." % (money(cost, total.get("_cur", "USD")), num(row["clicks"]))
        return "thin", None, None, None, "No conversions yet and not enough spend or clicks to call it a leak."
    if conv < MIN_CONV:
        return "thin", None, None, None, "%s conversion%s is too few to judge (it takes %d)." % (num(conv), "" if conv == 1 else "s", MIN_CONV)
    if idx is None:
        return "thin", None, None, None, "It cannot be compared with the account."
    verdict = "star" if idx >= STAR else "good" if idx >= GOOD else "par" if idx >= BELOW else "below" if idx > WEAK else "weak"
    adj = adjustment_for(idx) if verdict != "par" else 0.0
    return verdict, idx, adj, confidence_of(conv), ""


def _label(row):
    return row["label"]


def analyse(view, *, level="region", currency="USD", platform="google", mixed=False):
    """The view with every row judged, and `insights`, `basis` and `average` added. `view` is
    {rows, total, coverage, account} as tracker/paid_geo.py builds it."""
    total = dict(view["total"], _cur=currency)
    basis = None if mixed else basis_of(total)
    rows = []
    for r in view["rows"]:
        verdict, idx, adj, conf, why = judge(r, total, basis)
        if mixed:
            why = MIXED_WHY
        rows.append(dict(r, verdict=verdict, verdictLabel=VERDICT_LABEL[verdict], index=None if idx is None else round(idx * 100),
                         adjustment=adj, confidence=conf, why=why))
    out = dict(view, rows=rows, basis=basis)
    out["insights"] = make_insights(rows, view["total"], view.get("coverage"), basis, level, currency, platform, mixed)
    out["counts"] = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICT_LABEL if any(r["verdict"] == v for r in rows)}
    return out


def _basis_words(r, total, basis, currency):
    if basis == "roas":
        return "ROAS %s against %s for the account" % (format(r["roas"] or 0, ".2f"), format(total["roas"], ".2f"))
    return "CPA %s against %s for the account" % (money(r["cpa"], currency, 2) if r["cpa"] else "Na", money(total["cpa"], currency, 2))


def make_insights(rows, total, coverage, basis, level, currency, platform="google", mixed=False):
    """On Meta there is no bid adjustment by place: the same index says how much more or less budget a place deserves, and the advice is in ad sets and
    location targeting. (On Google, and on the two added together, it is Google's bid adjustment.)"""
    noun = LEVEL_NOUN.get(level, "location")
    meta = platform == "meta"
    out = []
    if not rows:
        return out

    def add_(kind, severity, headline, evidence, action, conf, keys):
        out.append({"kind": kind, "severity": severity, "headline": headline, "evidence": evidence, "action": action,
                    "confidence": conf, "keys": keys})

    if mixed:
        add_("mixed", "watch", "Campaign types with different results are added together here",
             "%s spent across %s %s on campaigns whose results are different things (sales, leads, app installs), so one place's cost per result "
             "cannot be set against another's." % (money(total["cost"], currency), num(len(rows)), plural(noun)),
             "Pick one campaign type above: the places are judged within it.", "high", [])
        return out
    if basis is None:
        add_("nodata", "watch", "No conversions in this range, so no place can be judged on results",
             "%s spent across %s %s with no conversions recorded." % (money(total["cost"], currency), num(len(rows)), plural(noun)),
             "Check conversion tracking, or pick a longer date range, before %s by location." % ("changing the budget" if meta else "adjusting any bids"), "high", [])
        return out

    def ev(r):
        return "%s: %s on %s conversion%s (index %d), %s of spend and %s of conversions." % (
            _label(r), _basis_words(r, total, basis, currency), num(r["conversions"]), "" if r["conversions"] == 1 else "s",
            r["index"], pct(r["pctCost"], 1), pct(r["pctConv"], 1))

    pushes = sorted([r for r in rows if r["verdict"] in ("star", "good") and (r["adjustment"] or 0) >= 0.10],
                    key=lambda r: -r["conversions"])[:3]
    for r in pushes:
        if meta:
            add_("push", "action", "Put more budget behind %s, about %d%% more" % (_label(r), round(r["adjustment"] * 100)),
                 ev(r), "Give %s an ad set of its own, or about %d%% more of the budget, and check it after two weeks. %s." % (
                     _label(r), round(r["adjustment"] * 100), "A place this small needs its own ad set to keep its budget"
                     if r["pctCost"] and r["pctCost"] < 0.03 else "It already takes a real share of the spend"),
                 r["confidence"], [r["key"]])
            continue
        add_("push", "action", "Raise bids in %s by about %d%%" % (_label(r), round(r["adjustment"] * 100)),
             ev(r), "Add a +%d%% location bid adjustment for %s and check it after two weeks. %s." % (
                 round(r["adjustment"] * 100), _label(r), "It is also worth a campaign of its own if the budget is capped"
                 if r["pctCost"] and r["pctCost"] < 0.03 else "It already takes a real share of the spend"),
             r["confidence"], [r["key"]])

    trims = sorted([r for r in rows if r["verdict"] in ("weak", "below") and (r["adjustment"] or 0) <= -0.10],
                   key=lambda r: -r["cost"])[:3]
    for r in trims:
        if meta:
            add_("trim", "action", "Take budget out of %s, about %d%% less" % (_label(r), round(-r["adjustment"] * 100)),
                 ev(r), "Cut its share of the budget by about %d%% (an ad set of its own makes that possible). If it stays weak after a month, exclude %s from the location targeting."
                 % (round(-r["adjustment"] * 100), _label(r)), r["confidence"], [r["key"]])
            continue
        add_("trim", "action", "Lower bids in %s by about %d%%" % (_label(r), round(-r["adjustment"] * 100)),
             ev(r), "Add a %d%% location bid adjustment for %s. If it stays weak after a month, exclude it." % (round(r["adjustment"] * 100), _label(r)),
             r["confidence"], [r["key"]])

    leaks = sorted([r for r in rows if r["verdict"] == "leak"], key=lambda r: -r["cost"])[:3]
    for r in leaks:
        add_("exclude", "action", "%s spent %s and did not convert" % (_label(r), money(r["cost"], currency)),
             "%s: %s on %s clicks, no conversions; the account's CPA is %s, so this is %s times a normal conversion's cost." % (
                 _label(r), money(r["cost"], currency), num(r["clicks"]), money(total["cpa"], currency, 2),
                 format(r["cost"] / total["cpa"], ".1f") if total.get("cpa") else "Na"),
             "Exclude %s, or cut its %s by half, unless it matters for a reason the numbers cannot show (a new market, brand awareness)." % (_label(r), "budget" if meta else "bid"),
             "medium", [r["key"]])

    top = rows[:3]
    if len(rows) >= 4:
        share = sum(r["pctCost"] or 0 for r in top)
        add_("concentration", "info", "Three %s hold %s of the spend" % (plural(noun), pct(share)),
             "%s: %s of spend between them, out of %s %s." % (", ".join(_label(r) for r in top), pct(share, 1), num(len(rows)), plural(noun)),
             "These are where a %s change moves the most money; judge them first." % ("budget" if meta else "bid") if share >= 0.5 else
             "Spend is spread out, so no single %s decides the result." % noun, "high", [r["key"] for r in top])

    thin = [r for r in rows if r["verdict"] == "thin"]
    thin_cost = sum(r["pctCost"] or 0 for r in thin)
    if thin and (len(thin) >= 0.3 * len(rows) or thin_cost >= 0.15):
        add_("thin", "watch", "%s of %s %s have too little data to judge" % (num(len(thin)), num(len(rows)), plural(noun)),
             "%s of the %s, %s of the spend, have fewer than %d conversions in this range." % (num(len(thin)), plural(noun), pct(thin_cost, 1), MIN_CONV),
             "Do not adjust these one by one. Use a wider date range or a bigger level (region instead of city) to decide about them.", "high", [])

    if coverage is not None and coverage < 0.85:
        add_("coverage", "watch", "This table covers %s of the spend" % pct(coverage),
             "Locations are reported for %s of this account's spend; the rest is in campaign types that do not report a location at this level." % pct(coverage, 1),
             "Read the shares above as shares of the located spend, not of the whole account.", "high", [])

    rank = {"action": 0, "watch": 1, "info": 2}
    out.sort(key=lambda i: rank[i["severity"]])
    return out
