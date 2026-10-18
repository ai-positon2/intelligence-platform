"""Paid Marketing Agents: Audience Segment Analysis, what the numbers mean.

For one table of audiences (one campaign type) this judges each audience against the account's own average for that campaign type,
says what to do about it given how the audience is attached (observing or targeting) and how the campaign bids, and writes the few
things worth doing, each with its numbers beside it so every one can be checked. Pure functions: no database, no network, no clock.

The judgement is Geo Performance's (tracker/paid_geo_insights.py): an efficiency index against the account's average (100 is average,
ROAS when the account tracks value, otherwise cost per conversion), nothing judged under 5 conversions, "leaking spend" for real
clicks and more than twice the average cost per conversion with none, and a suggested adjustment of 80% of the gap in 5% steps from
+40% to -50%. What differs is what that means for an audience:

  * **A bid adjustment only works where the campaign lets it.** Smart Bidding (Target CPA, Target ROAS, Maximize conversions or
    conversion value) sets each bid itself and ignores audience bid adjustments, and Demand Gen and Performance Max take none. For
    those the advice is a campaign or ad group of its own, a budget, or an exclusion, never an adjustment.
  * **Observing and targeting differ.** An audience that only observes adds a bid adjustment and limits nobody, so a weak one is
    lowered before it is excluded; excluding stops ads reaching those people altogether. An audience that targets limits who sees the ad,
    so a weak one is removed from targeting or excluded.
  * **A list that cannot serve is not a performance problem.** A remarketing, customer match or similar list under what Google needs
    (about 1,000 active members for Search, 100 for Display) shows ads to almost nobody however it is set up.
"""
from __future__ import annotations

from . import paid_geo_insights as gi

MIN_CONV = gi.MIN_CONV
SEARCH_MIN, DISPLAY_MIN = 1000, 100
NO_BID_TYPES = ("Demand Gen", "Performance Max")
ADJUSTABLE_STRATEGIES = ("MANUAL_CPC", "MANUAL_CPM", "MANUAL_CPV", "TARGET_SPEND", "PERCENT_CPC")      # the bidding that honours an audience adjustment
KIND_ORDER = ["in_market", "affinity", "interest", "behavior", "remarketing", "customer_match", "similar", "custom", "other"]
KINDS_SHORT = {"in_market": "In-market", "affinity": "Affinity", "interest": "Interests", "behavior": "Behaviors", "remarketing": "Remarketing",
               "customer_match": "Customer match", "similar": "Similar", "custom": "Custom segments", "other": "Other"}
META_MIN = 1000                         # an audience Meta estimates under this many people delivers poorly (it needs at least 100 to run at all)
VERDICT_LABEL = dict(gi.VERDICT_LABEL)
money, pct, num = gi.money, gi.pct, gi.num


def adjustable(use):
    """True when a bid adjustment on this audience, in this campaign, is honoured."""
    return use["ctype"] not in NO_BID_TYPES and use.get("bidding") in ADJUSTABLE_STRATEGIES


def _signed(adj):
    return "%+d%%" % round(adj * 100)


def _meta(row):
    """True for an audience that is a Meta ad set's: Meta has no audience bid adjustment, so its advice is about ad sets, budget and targeting."""
    return row.get("platform") == "meta"


def _meta_action(row, verdict, adj):
    suggestion = row["mode"] == "observation"
    note = " Its ad sets use Advantage+ audience, which treats an audience as a suggestion, so keep it as a strict target in an ad set of its own to test it fairly." if suggestion else ""
    if verdict in ("star", "good") and (adj or 0) >= 0.10:
        return {"kind": "raise", "text": "Give it an ad set of its own, or a larger share of the budget, and check it after two weeks.%s" % note}
    if verdict in ("weak", "below") and (adj or 0) <= -0.10:
        return {"kind": "lower", "text": "Give it a smaller budget (an ad set of its own makes that possible), and take it out of the ad sets' targeting if it stays weak after a month."}
    if verdict == "leak":
        return {"kind": "exclude", "text": "Take it out of the ad sets' targeting, or exclude it, unless it matters for a reason the numbers cannot show."}
    return {"kind": "hold", "text": "Leave it as it is."}


def action_for(row, verdict, adj):
    """{kind, text}: what to do with one audience, given how it is attached and how its campaigns bid."""
    if verdict in ("none", "thin"):
        return {"kind": "none", "text": ""}
    if _meta(row):
        return _meta_action(row, verdict, adj)
    uses = row["uses"]
    can_bid = any(adjustable(u) for u in uses)
    observing = row["mode"] == "observation"
    cur = (" (it is at %s now)" % _signed(row["bid"]) if row["bid"] is not None else
           " (it differs by campaign now)" if row.get("bidVaries") else " (it has none now)")
    if verdict in ("none", "thin"):
        return {"kind": "none", "text": ""}
    if verdict in ("star", "good") and (adj or 0) >= 0.10:
        if can_bid:
            return {"kind": "raise", "text": "Set its bid adjustment to %s%s." % (_signed(adj), cur if row["bid"] != adj else "")}
        return {"kind": "raise", "text": "Its campaigns use Smart Bidding or take no audience adjustment, so give it an ad group or a budget of its own instead."}
    if verdict in ("weak", "below") and (adj or 0) <= -0.10:
        if can_bid and observing:
            return {"kind": "lower", "text": "Set its bid adjustment to %s%s. If it stays weak after a month, exclude it." % (_signed(adj), cur if row["bid"] != adj else "")}
        if can_bid:
            return {"kind": "lower", "text": "Set its bid adjustment to %s, or remove it from the targeting if it stays weak." % _signed(adj)}
        return {"kind": "lower", "text": "Its campaigns cannot take a bid adjustment, so move it to its own ad group with a small budget or exclude it."}
    if verdict == "leak":
        if can_bid and observing:
            return {"kind": "exclude", "text": "Lower its bid adjustment by half first. Exclude it if it still does not convert after a month."}
        return {"kind": "exclude", "text": "Exclude it, or take it out of the targeting, unless it matters for a reason the numbers cannot show."}
    return {"kind": "hold", "text": "Leave it as it is."}


def analyse(view, *, currency="USD", ctype="All", mixed=False, force_cpa=False):
    """The view with every audience judged, and `insights`, `basis` and `counts` added. `view` is {rows, total, account} as
    tracker/paid_audiences.py builds it. `mixed`: the view adds together results that are not the same thing (a Meta account's sales and leads), so
    nothing is ranked. `force_cpa`: Google and Meta are added, and Meta's results carry no value, so ROAS would call every Meta audience a failure: cost per
    conversion is the one measure both have."""
    total = dict(view["account"], _cur=currency)
    basis = None if mixed else gi.basis_of(total)
    if force_cpa and not mixed:
        basis = "cpa" if total.get("conversions", 0) > 0 and total.get("cpa") else None
    rows = []
    for r in view["rows"]:
        verdict, idx, adj, conf, why = gi.judge(r, total, basis)
        if verdict == "none":
            why = gi.MIXED_WHY.replace("campaigns", "audiences' campaigns").replace("a place", "an audience") if mixed else \
                "The account recorded no conversions in this range, so audiences cannot be judged on results."
        row = dict(r, verdict=verdict, verdictLabel=VERDICT_LABEL[verdict], index=None if idx is None else round(idx * 100), adjustment=adj,
                   confidence=conf, why=why)
        row["adjustable"] = any(adjustable(u) for u in r["uses"])
        row["action"] = action_for(row, verdict, adj)
        rows.append(row)
    out = dict(view, rows=rows, basis=basis)
    out["insights"] = make_insights(rows, total, basis, currency, ctype, mixed)
    out["counts"] = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICT_LABEL if any(r["verdict"] == v for r in rows)}
    return out


def _basis_words(r, total, basis, currency):
    if basis == "roas":
        return "ROAS %s against %s for the account" % (format(r["roas"] or 0, ".2f"), format(total["roas"], ".2f"))
    return "cost per conversion %s against %s for the account" % (money(r["cpa"], currency, 2) if r["cpa"] else "Na", money(total["cpa"], currency, 2))


def _ev(r, total, basis, currency):
    return "%s (%s): %s on %s conversion%s (index %d), %s of spend and %s of conversions." % (
        r["label"], r["kindLabel"].lower(), _basis_words(r, total, basis, currency), num(r["conversions"]), "" if r["conversions"] == 1 else "s",
        r["index"], pct(r["pctCost"], 1), pct(r["pctConv"], 1))


def make_insights(rows, total, basis, currency, ctype, mixed=False):
    out = []
    if not rows:
        return out

    def add_(kind, severity, headline, evidence, action, conf, keys):
        out.append({"kind": kind, "severity": severity, "headline": headline, "evidence": evidence, "action": action,
                    "confidence": conf, "keys": keys})
    if mixed:
        add_("mixed", "watch", "Campaign types with different results are added together here",
             "%s spent across %s audiences on campaigns whose results are different things (sales, leads, app installs), so one audience's cost per result "
             "cannot be set against another's." % (money(total["cost"], currency), num(len(rows))),
             "Pick one campaign type above: the audiences are judged within it.", "high", [])
        return out
    if basis is None:
        add_("nodata", "watch", "No conversions in this range, so no audience can be judged on results",
             "%s spent across %s audiences with no conversions recorded." % (money(total["cost"], currency), num(len(rows))),
             "Check conversion tracking, or pick a longer date range, before changing %s." % ("any audience" if all(_meta(r) for r in rows) else "any audience bids"), "high", [])
        return out
    pushes = sorted([r for r in rows if r["action"]["kind"] == "raise"], key=lambda r: -r["conversions"])[:3]
    for r in pushes:
        add_("push", "action", "%s is earning more than the account average" % r["label"], _ev(r, total, basis, currency),
             r["action"]["text"], r["confidence"], [r["key"]])
    trims = sorted([r for r in rows if r["action"]["kind"] == "lower"], key=lambda r: -r["cost"])[:3]
    for r in trims:
        add_("trim", "action", "%s is earning less than the account average" % r["label"], _ev(r, total, basis, currency),
             r["action"]["text"], r["confidence"], [r["key"]])
    for r in sorted([r for r in rows if r["verdict"] == "leak"], key=lambda r: -r["cost"])[:3]:
        add_("exclude", "action", "%s spent %s and did not convert" % (r["label"], money(r["cost"], currency)),
             "%s (%s): %s on %s clicks, no conversions; the average cost of a conversion here is %s, so this is %s times that." % (
                 r["label"], r["kindLabel"].lower(), money(r["cost"], currency), num(r["clicks"]), money(total["cpa"], currency, 2),
                 format(r["cost"] / total["cpa"], ".1f") if total.get("cpa") else "Na"), r["action"]["text"], "medium", [r["key"]])
    stuck = [r for r in rows if r["verdict"] in ("star", "good", "weak", "below", "leak") and not r["adjustable"] and r["mode"] != "targeting" and not _meta(r)]
    if stuck:
        add_("smart", "info", "Bid adjustments do not apply to %s of these audiences" % num(len(stuck)),
             "%s sit in campaigns on Smart Bidding, Demand Gen or Performance Max, which set their own bids and ignore an audience adjustment." % _names(stuck),
             "For these, change the audience itself (its own ad group or budget, or an exclusion) rather than a bid.", "high", [r["key"] for r in stuck[:5]])
    thin = [r for r in rows if r["verdict"] == "thin"]
    thin_cost = sum(r["pctCost"] or 0 for r in thin)
    if thin and (len(thin) >= 0.4 * len(rows) or thin_cost >= 0.15):
        add_("thin", "watch", "%s of %s audiences have too little data to judge" % (num(len(thin)), num(len(rows))),
             "%s of the audiences, %s of the spend, have fewer than %d conversions in this range." % (num(len(thin)), pct(thin_cost, 1), MIN_CONV),
             "Do not adjust these one by one. Use a wider date range, or judge them by kind (the strip above).", "high", [])
    rank = {"action": 0, "watch": 1, "info": 2}
    out.sort(key=lambda i: rank[i["severity"]])
    return out


def _names(rows, n=3):
    shown = ", ".join(r["label"] for r in rows[:n])
    return shown + (" and %d more" % (len(rows) - n) if len(rows) > n else "")


# ── Lists and the whole report ──────────────────────────────────────────────

def judge_lists(lists):
    """Each list with what stops it serving: a size under what Google needs, a closed list, or no use at all."""
    out = []
    for l in lists:
        if l.get("platform") == "meta":
            out.append(_judge_meta_list(l))
            continue
        problems = []
        if l["status"] == "CLOSED":
            problems.append("The list is closed, so no new people join it.")
        if l["sizeSearch"] < SEARCH_MIN:
            problems.append("Google reports %s on Google Search; about %s are needed for it to serve there." % (
                "no members" if not l["sizeSearch"] else num(l["sizeSearch"]) + " members", num(SEARCH_MIN)))
        if l["sizeDisplay"] < DISPLAY_MIN:
            problems.append("Google reports %s on Display and YouTube; about %s are needed." % (
                "no members" if not l["sizeDisplay"] else num(l["sizeDisplay"]) + " members", num(DISPLAY_MIN)))
        out.append(dict(l, problems=problems, serves=not problems))
    out.sort(key=lambda l: (l["serves"], l["used"], l["label"].lower()))
    return out


def _judge_meta_list(l):
    """A Meta audience with what stops it delivering: Meta says it is not ready, or estimates fewer people than it needs."""
    problems = []
    if l["status"] == "NOT_READY":
        problems.append("Meta says the audience is not ready%s." % (": " + l["why"] if l.get("why") else ""))
    size = l.get("sizeSearch")
    if size is not None and size < META_MIN:
        problems.append("Meta estimates %s people; an audience needs at least 100 to run and about %s to deliver well." % (
            "fewer than " + num(META_MIN) if not size else num(size), num(META_MIN)))
    return dict(l, problems=problems, serves=not problems)


def kind_totals(view):
    """How each kind of audience does as a whole, against the account: {kind, label, count, cost, conversions, cpa, roas, index...}."""
    total = dict(view["account"], _cur="USD")
    basis = view["basis"] if "basis" in view else gi.basis_of(total)          # the basis the view was judged on (cost per conversion when it adds Google and Meta)
    out = []
    by = {}
    for r in view["rows"]:
        k = by.setdefault(r["kind"], {"cost": 0.0, "conv": 0.0, "val": 0.0, "clk": 0.0, "imp": 0.0, "count": 0})
        k["cost"] += r["cost"]
        k["conv"] += r["conversions"]
        k["val"] += r["value"]
        k["clk"] += r["clicks"]
        k["imp"] += r["impressions"]
        k["count"] += 1
    for kind in KIND_ORDER:
        k = by.get(kind)
        if not k:
            continue
        row = {"cost": round(k["cost"], 2), "conversions": round(k["conv"], 2), "value": round(k["val"], 2), "clicks": int(k["clk"]),
               "cpa": None if k["conv"] <= 0 else round(k["cost"] / k["conv"], 2),
               "roas": None if not k["cost"] or k["val"] <= 0 else round(k["val"] / k["cost"], 3)}
        idx = gi.index_of(row, total, basis) if basis and k["conv"] >= MIN_CONV else None
        out.append(dict(row, kind=kind, label=KINDS_SHORT[kind], count=k["count"], index=None if idx is None else round(idx * 100),
                        share=None if not view["account"]["cost"] else k["cost"] / view["account"]["cost"]))
    return out


def report_insights(report, view):
    """The All view's insights, plus the ones that are about the whole report: which kind drives results, lists that cannot serve,
    lists nobody uses, the exclusions, and what is not here."""
    out = list(view["insights"])
    cur = report["account"]["currency"]

    def add_(kind, severity, headline, evidence, action, conf, keys=()):
        out.append({"kind": kind, "severity": severity, "headline": headline, "evidence": evidence, "action": action,
                    "confidence": conf, "keys": list(keys)})
    kinds = [k for k in kind_totals(view) if k["index"] is not None]
    if len(kinds) >= 2:
        best, worst = max(kinds, key=lambda k: k["index"]), min(kinds, key=lambda k: k["index"])
        if best["index"] - worst["index"] >= 20:
            add_("kinds", "action", "%s audiences are doing the most for the money" % best["label"],
                 "%s: index %d on %s conversions and %s of spend, against %s: index %d on %s conversions and %s of spend (100 is the account average)." % (
                     best["label"], best["index"], num(best["conversions"]), pct(best["share"], 1), worst["label"], worst["index"],
                     num(worst["conversions"]), pct(worst["share"], 1)),
                 "Shift budget and bid room toward %s and look hard at %s before adding more of it." % (best["label"].lower(), worst["label"].lower()),
                 "high" if min(best["conversions"], worst["conversions"]) >= 15 else "medium")
    bad = [l for l in report["lists"] if l["problems"] and l["used"]]
    if bad:
        add_("lists", "watch", "%s audience list%s in use cannot serve properly" % (num(len(bad)), "" if len(bad) == 1 else "s"),
             "%s. %s" % (_names(bad, 4), bad[0]["problems"][0]),
             "A list this small shows ads to almost nobody. Let it grow, widen its rules or its window, or stop counting on it.", "high")
    unused = [l for l in report["lists"] if not l["used"] and l["serves"] and not l.get("readOnly")]       # Google's own lists are not the team's to use or not
    if unused:
        add_("unused", "info", "%s audience list%s the account has but nothing uses" % (num(len(unused)), "" if len(unused) == 1 else "s"),
             "%s %s big enough to serve and %s attached to no campaign or ad group and excluded nowhere." % (
                 _names(unused, 4), "is" if len(unused) == 1 else "are", "is" if len(unused) == 1 else "are"),
             "Add them as observation audiences on the campaigns they fit to learn how they perform, or remove them.", "high")
    if report.get("exclusions"):
        n = len(report["exclusions"])
        add_("exclusions", "info", "%s audience exclusion%s in place" % (num(n), "" if n == 1 else "s"),
             "%s." % _names([{"label": "%s (%s)" % (e["label"], e["adGroup"] or e["campaign"])} for e in report["exclusions"]], 4),
             "These are listed below. An excluded audience is never recommended above.", "high")
    platform = report["account"].get("platform") or "google"
    if platform != "meta":
        out.append({"kind": "pmax", "severity": "info", "headline": "Performance Max audiences are not in this table",
                    "evidence": "Google reports no results for the audience signals of a Performance Max campaign.",
                    "action": "Judge Performance Max by its asset groups and its campaign totals instead.", "confidence": "high", "keys": []})
    if platform in ("meta", "both"):
        joined = [r for r in view["rows"] if r.get("members")]
        out.append({"kind": "meta", "severity": "info", "headline": "Meta's audiences are judged by the ad sets that use them",
                    "evidence": "Meta reports results per ad set, not per audience, so an audience's numbers are those of the ad sets aimed at it, and an ad set aimed at two audiences counts under both."
                    + ((" %s of these rows join audiences that exactly the same ad sets use, so Meta cannot tell them apart and they share one set of numbers." % num(len(joined))) if joined else ""),
                    "action": "Read an audience's row as how the ad sets built on it do. To test one fairly, give it an ad set of its own.", "confidence": "high", "keys": []})
    rank = {"action": 0, "watch": 1, "info": 2}
    out.sort(key=lambda i: rank[i["severity"]])
    return out
