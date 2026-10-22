"""Paid Marketing Agents: KPI Monitoring, what a campaign's last week says about its targets (no network, no clock: `today` is passed in).

An account is passed in as plain lists (see tracker/paid_kpi.py for how they are read):

  campaigns  [{id, name, channel, primaryStatus, reasons, systemStatus, strategy, targetCpa, targetRoas, startDate, lostBudget, days}]
             days is [{date, impr, clicks, cost, conv, value}] for the last 14 days; targetCpa is in the account's currency, targetRoas a ratio (4.0 is 400 percent), lostBudget the share of
             impressions the budget lost over the week (0.25) or None; primaryStatus, reasons and systemStatus are Google's words (Meta's are mapped onto them by the reader)
  config     what the team saved: {targets: {cpa, roas, cvr, ctr}, tolerance, campaigns: {id: {cpa, roas, cvr, ctr}}, muted: {id: why}}; cvr and ctr are percents
  learning_days   {campaign id: days the monitor has seen it learning}

The last 7 whole days (the 7 days before `today`) are held against the campaign's target, and against the 7 days before them. A figure with too little behind it is never judged: it is listed
as one the page could not judge, with the number it had. Muted campaigns are shown and have no findings.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from .paid_brandspend_math import money, pct

LEVEL_RANK = {"high": 3, "medium": 2, "low": 1}
WINDOW = 7
MIN_CONV = 5                       # conversions in the week before a CPA or ROAS is judged
MIN_CLICKS = 100                   # clicks before a conversion rate is judged
MIN_IMPR = 1000                    # impressions before a click-through rate is judged
DEFAULT_TOLERANCE = 0.20
HIGH_RATIO = 2.0                   # off by this multiple of the target is high on its own
SUSTAINED_RATIO = 1.5              # off by this and the week before was off too is high
NO_CONV_SPEND = 2.0                # a week of spend of this many target CPAs with no conversion
DROP_SPEND = 0.5                   # ... or this share of last week's spend after a week that converted
FAR_BELOW = 0.6                    # a CVR or CTR at this share of its minimum is medium, not low
STUCK_DAYS = 14                    # learning for this many days is not learning, it is stuck
BUDGET_LOST = 0.2                  # impressions lost to the budget
NEW_DAYS = 3                       # a campaign younger than this is not "not delivering" yet
EPS = 1e-9                         # a figure that lands on a limit within rounding is on the limit (a CPA of 60.000000000001 against 50 is exactly 20 percent over)

RULES = {
    "not_delivering": {"level": "high", "label": "Not delivering", "fix": "Open the campaign in Google Ads and read the message under its status: it names what stops it (no live ads, a policy block, an end date, a missing setting)."},
    "bidding": {"level": "medium", "label": "Bid strategy held back or misconfigured", "fix": "Open Settings, then Bidding: a misconfigured strategy needs what it is missing (usually conversion tracking or a valid target); a bid ceiling or floor stops the strategy from bidding what it needs, so loosen it."},
    "no_conv": {"level": "high", "label": "Stopped converting", "fix": "Check the conversion tracking first (a tag that stopped firing, a form or page that changed), then the landing page and the ads. If tracking is fine, pause the spend until the cause is found."},
    "cpa": {"level": "medium", "label": "Cost per conversion above target", "fix": "Look at what spent without converting (search terms, placements, audiences), tighten it, and only then touch the target or the budget. Do not change several things at once."},
    "roas": {"level": "medium", "label": "Return on ad spend below target", "fix": "Look at what spent without returning (products, search terms, audiences), tighten it, and check the conversion value is recorded. Change one thing at a time."},
    "learning": {"level": "low", "label": "Still learning", "fix": "Leave the bids, targets and budget alone: every change restarts the learning. A campaign limited by data needs more conversions: widen the conversion event or merge campaigns."},
    "budget": {"level": "medium", "label": "Limited by budget", "fix": "If it is on target, raise the budget in steps (about 20 percent at a time); if it is not, fix the cost first, because more budget buys more of the same."},
    "cvr": {"level": "low", "label": "Conversion rate below the lowest wanted", "fix": "Check the landing page (speed, the form, the message the ad promised) and the traffic that arrives (search terms, audiences)."},
    "ctr": {"level": "low", "label": "Click-through rate below the lowest wanted", "fix": "Check the ads (does the headline say what the search asked), the keywords' match types and the position."},
}
ORDER = ["not_delivering", "bidding", "no_conv", "cpa", "roas", "learning", "budget", "cvr", "ctr"]
METRICS = ("cpa", "roas", "cvr", "ctr")
LABEL = {"cpa": "Cost per conversion", "roas": "Return on ad spend", "cvr": "Conversion rate", "ctr": "Click-through rate"}
SOURCE = {"campaign": "saved for this campaign", "account": "saved for the account", "strategy": "the campaign's own bid strategy target"}
BLOCKED = {"NOT_ELIGIBLE", "MISCONFIGURED", "ENDED"}
NOT_STARTED = {"PENDING"}
OFF = {"PAUSED", "REMOVED"}
HARD_REASONS = {"META_WITH_ISSUES", "NO_ACTIVE_AD_GROUPS", "NO_ACTIVE_AD_GROUP_ADS", "BIDDING_STRATEGY_MISCONFIGURED", "MOST_ADS_UNDER_REVIEW", "HAS_ADS_DISAPPROVED"}
REASON_TEXT = {"META_WITH_ISSUES": "Meta reports issues with it (a rejected ad or a payment or account problem)", "NO_ACTIVE_AD_GROUPS": "it has no live ad group", "NO_ACTIVE_AD_GROUP_ADS": "an ad group has no live ad", "BIDDING_STRATEGY_MISCONFIGURED": "its bid strategy is set up wrongly",
               "MOST_ADS_UNDER_REVIEW": "most of its ads are under review", "HAS_ADS_DISAPPROVED": "some of its ads are disapproved", "AD_GROUPS_PAUSED": "its ad groups are paused",
               "BUDGET_CONSTRAINED": "it is limited by budget", "BIDDING_STRATEGY_CONSTRAINED": "its bid strategy is held back by its own settings", "BIDDING_STRATEGY_LEARNING": "its bid strategy is learning",
               "BIDDING_STRATEGY_PENDING": "a bid strategy change is not in effect yet", "HAS_ADS_LIMITED_BY_POLICY": "some of its ads are limited by policy", "SEARCH_VOLUME_LIMITED": "few searches match its keywords"}
STATUS_TEXT = {"NOT_ELIGIBLE": "not eligible to show", "MISCONFIGURED": "misconfigured", "ENDED": "ended (its end date has passed)", "PENDING": "waiting to start"}
BID_TEXT = {"CEILING": "a maximum CPC limit is stopping the strategy from bidding what it needs", "FLOOR": "a minimum CPC limit is holding the bids up"}


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def num(x):
    """A count as the page writes it: 12 and 4.5, never 12.0 or 4.5000001."""
    x = round(float(x), 1)
    return str(int(x)) if x == int(x) else str(x)


def count(x, one, many=None):
    return "%s %s" % (num(x), one if round(float(x), 1) == 1 else (many or one + "s"))


def _f(v, default=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if x == x else default


def _day(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def window(days, first, last):
    """{impr, clicks, cost, conv, value, days} of the days from `first` to `last` (dates), the days with no row counted as nothing."""
    out = {"impr": 0.0, "clicks": 0.0, "cost": 0.0, "conv": 0.0, "value": 0.0, "days": 0}
    for d in days or []:
        day = _day(d.get("date"))
        if day is None or day < first or day > last:
            continue
        out["days"] += 1
        for k in ("impr", "clicks", "cost", "conv", "value"):
            out[k] += _f(d.get(k))
    return out


def windows(days, today):
    """(recent, prior): the 7 whole days before `today` and the 7 before those."""
    last = today - dt.timedelta(days=1)
    first = last - dt.timedelta(days=WINDOW - 1)
    return window(days, first, last), window(days, first - dt.timedelta(days=WINDOW), first - dt.timedelta(days=1))


def value_of(metric, w):
    """The figure of one window and whether there is enough behind it: (value or None, why not). `why not` is words for the page."""
    if metric in ("cpa", "roas"):
        if w["conv"] < MIN_CONV:
            return None, "only %s in the week (at least %d are needed)" % (count(w["conv"], "conversion"), MIN_CONV)
        if metric == "cpa":
            return w["cost"] / w["conv"], ""
        if w["cost"] <= 0 or w["value"] <= 0:
            return None, "no conversion value was recorded"
        return w["value"] / w["cost"], ""
    if metric == "cvr":
        if w["clicks"] < MIN_CLICKS:
            return None, "only %s in the week (at least %d are needed)" % (count(w["clicks"], "click"), MIN_CLICKS)
        return w["conv"] / w["clicks"], ""
    if w["impr"] < MIN_IMPR:
        return None, "only %s in the week (at least %d are needed)" % (count(w["impr"], "impression"), MIN_IMPR)
    return w["clicks"] / w["impr"], ""


def targets_for(c, config):
    """{metric: {value, source}} for the metrics this campaign has a target for: cvr and ctr as fractions, the others as they are saved."""
    config = config or {}
    own = (config.get("campaigns") or {}).get(str(c["id"])) or {}
    acct = config.get("targets") or {}
    out = {}
    for m in METRICS:
        for src, src_cfg in (("campaign", own), ("account", acct)):
            v = _f(src_cfg.get(m), 0.0)
            if v > 0:
                out[m] = {"value": v / 100.0 if m in ("cvr", "ctr") else v, "source": src}
                break
        else:
            if m == "cpa" and _f(c.get("targetCpa")) > 0:
                out[m] = {"value": _f(c["targetCpa"]), "source": "strategy"}
            elif m == "roas" and _f(c.get("targetRoas")) > 0:
                out[m] = {"value": _f(c["targetRoas"]), "source": "strategy"}
    return out


def _worse(metric, value, target, tol):
    """The ratio by which a figure misses its target (1.0 is on it, 1.5 is half as bad again) and whether it is beyond the tolerance."""
    if value is None or not target:
        return None, False
    if metric == "cpa":
        ratio = value / target
        return ratio, ratio > 1 + tol + EPS
    ratio = (target / value) if value > 0 else float("inf")
    return ratio, value < target * (1 - tol) - EPS


def _fmt(metric, v, currency):
    if v is None:
        return "no figure"
    if metric == "cpa":
        return money(v, currency)
    if metric == "roas":
        return "%.2fx" % v
    return pct(v, 1)


def judge(metric, target, recent, prior, tol):
    """{metric, label, target, source, value, prior, state, ratio, why, sustained}: state is "on", "off" or "unknown" (no target is "none")."""
    value, why = value_of(metric, recent)
    before, _ = value_of(metric, prior)
    value, before = (None if v is None else round(v, 6) for v in (value, before))
    out = {"metric": metric, "label": LABEL[metric], "target": target["value"] if target else None, "source": target["source"] if target else None, "value": value, "prior": before,
           "state": "none" if not target else "unknown", "ratio": None, "why": why if target else "", "sustained": False}
    if not target or value is None:
        return out
    ratio, off = _worse(metric, value, target["value"], tol)
    _r2, off_before = _worse(metric, before, target["value"], tol)
    return dict(out, state="off" if off else "on", ratio=round(ratio, 3) if ratio != float("inf") else None, sustained=bool(off and off_before), why="")


def _finding(rule, key, title, detail, c, *, level=None, evidence=None, facts=None):
    return {"rule": rule, "level": level or RULES[rule]["level"], "key": key, "title": title, "detail": detail, "campaignId": str(c["id"]), "campaign": c["name"],
            "evidence": list(evidence or []), "facts": facts or {}, "fix": RULES[rule]["fix"]}


def _reasons_text(reasons):
    words = [REASON_TEXT[r] for r in reasons or [] if r in REASON_TEXT]
    return words[:3]


def _delivery(c, today, recent):
    """The not_delivering finding of a campaign, or None."""
    status = str(c.get("primaryStatus") or "")
    name = c["name"]
    reasons = [r for r in c.get("reasons") or [] if r]
    if status in BLOCKED:
        why = _reasons_text(reasons)
        return _finding("not_delivering", "not_delivering:%s" % c["id"], "%s is %s" % (name, STATUS_TEXT[status]),
                        "Google Ads reports %s as %s%s." % (name, STATUS_TEXT[status], (": " + "; ".join(why)) if why else ""), c, level="high", evidence=["Status: %s" % status.replace("_", " ").lower()] + why)
    if status in NOT_STARTED:
        return _finding("not_delivering", "not_delivering:%s" % c["id"], "%s has not started" % name, "%s is enabled but waiting to start." % name, c, level="low", evidence=["Status: pending"])
    hard = [r for r in reasons if r in HARD_REASONS]
    if hard and status not in OFF:
        return _finding("not_delivering", "not_delivering:%s" % c["id"], "%s cannot show properly" % name,
                        "Google Ads says %s." % "; ".join(_reasons_text(hard)), c, level="medium", evidence=_reasons_text(hard))
    started = _day(c.get("startDate"))
    old_enough = started is None or started <= today - dt.timedelta(days=NEW_DAYS)
    if status not in OFF and recent["impr"] <= 0 and old_enough:
        return _finding("not_delivering", "not_delivering:%s" % c["id"], "%s had no impressions in 7 days" % name,
                        "%s is enabled and eligible but did not show once in the last 7 days." % name, c, level="medium", evidence=["Impressions in the last 7 days: 0"])
    return None


def _bidding(c):
    status = str(c.get("systemStatus") or "")
    if status.startswith("MISCONFIGURED"):
        return _finding("bidding", "bidding:%s" % c["id"], "%s has a misconfigured bid strategy" % c["name"],
                        "Google reports the bid strategy status %s, so the strategy cannot bid properly." % status.replace("_", " ").lower(), c, level="high", evidence=["Bid strategy status: %s" % status.lower().replace("_", " ")])
    for key, word in (("LIMITED_BY_CPC_BID_CEILING", "CEILING"), ("LIMITED_BY_CPC_BID_FLOOR", "FLOOR")):
        if status == key:
            return _finding("bidding", "bidding:%s" % c["id"], "%s: the bid strategy is limited by a CPC limit" % c["name"], "Google reports that %s." % BID_TEXT[word], c, level="medium",
                            evidence=["Bid strategy status: %s" % status.lower().replace("_", " ")])
    return None


def _learning(c, learning_days):
    status, primary = str(c.get("systemStatus") or ""), str(c.get("primaryStatus") or "")
    cid = str(c["id"])
    if status == "LIMITED_BY_DATA":
        return _finding("learning", "learning:%s" % cid, "%s is limited by data" % c["name"], "The bid strategy does not have enough conversions to learn from, so its bids stay rough.", c, level="medium",
                        evidence=["Bid strategy status: limited by data"])
    if "LEARNING" in status or primary == "LEARNING":
        days = int(learning_days.get(cid) or 0)
        stuck = days >= STUCK_DAYS
        return _finding("learning", "learning:%s" % cid, "%s is %s" % (c["name"], "stuck in learning" if stuck else "still learning"),
                        ("It has been learning for %s." % plural(days, "day")) if stuck else "The bid strategy is learning, so its results are not steady yet.", c, level="medium" if stuck else "low",
                        evidence=["Bid strategy status: %s" % (status or primary).lower().replace("_", " ")] + (["Seen learning for %s" % plural(days, "day")] if days else []), facts={"days": days})
    return None


def _budget(c, verdicts):
    lost = c.get("lostBudget")
    limited = "BUDGET_CONSTRAINED" in (c.get("reasons") or []) or str(c.get("systemStatus") or "") == "LIMITED_BY_BUDGET" or (lost is not None and _f(lost) >= BUDGET_LOST)
    if not limited:
        return None
    judged = [v for v in verdicts if v["metric"] in ("cpa", "roas") and v["state"] in ("on", "off")]
    on_target = bool(judged) and all(v["state"] == "on" for v in judged)
    evidence = ["Google marks it limited by budget"] if ("BUDGET_CONSTRAINED" in (c.get("reasons") or []) or str(c.get("systemStatus") or "") == "LIMITED_BY_BUDGET") else []
    if lost is not None:
        evidence.append("Impressions lost to the budget in the last 7 days: %s" % pct(_f(lost), 0))
    off = any(v["state"] == "off" for v in judged)
    if on_target:
        detail = "%s is held back by its budget and is on its target, so more budget would buy more of what works." % c["name"]
    else:
        detail = "%s is held back by its budget%s." % (c["name"], ", but it is not on its target, so more budget would mostly buy more of the same" if off else "")
    return _finding("budget", "budget:%s" % c["id"], "%s is limited by budget" % c["name"], detail, c, level="medium" if on_target else "low", evidence=evidence, facts={"lost": lost, "onTarget": on_target})


def _drift(c, verdicts, recent, prior, currency):
    out = []
    for v in verdicts:
        if v["state"] != "off":
            continue
        m, t = v["metric"], v["target"]
        shown = (_fmt(m, v["value"], currency), _fmt(m, t, currency))
        facts = {"metric": m, "value": v["value"], "target": t, "prior": v["prior"], "source": v["source"]}
        evidence = ["Last 7 days: %s (target %s, %s)" % (shown[0], shown[1], SOURCE[v["source"]])]
        if v["prior"] is not None:
            evidence.append("The 7 days before: %s" % _fmt(m, v["prior"], currency))
        if m in ("cpa", "roas"):
            ratio = v["ratio"] if v["ratio"] is not None else float("inf")
            high = ratio >= HIGH_RATIO - EPS or (ratio >= SUSTAINED_RATIO - EPS and v["sustained"])
            level = "high" if high else "medium"
            what = "costs %s a conversion" % shown[0] if m == "cpa" else "returns %s" % shown[0]
            evidence.append("%s and %s spent in the week" % (count(recent["conv"], "conversion"), money(recent["cost"], currency)))
            out.append(_finding(m, "%s:%s" % (m, c["id"]), "%s %s against a target of %s" % (c["name"], what, shown[1]),
                                "%s %s over the last 7 days; its target is %s.%s" % (c["name"], what, shown[1], " The 7 days before were off target too." if v["sustained"] else ""), c, level=level, evidence=evidence, facts=facts))
        else:
            far = v["value"] <= t * FAR_BELOW + EPS
            out.append(_finding(m, "%s:%s" % (m, c["id"]), "%s has a %s of %s against %s" % (c["name"], LABEL[m].lower(), shown[0], shown[1]),
                                "%s is at %s over the last 7 days; the lowest wanted is %s." % (LABEL[m], shown[0], shown[1]), c, level="medium" if far else "low", evidence=evidence, facts=facts))
    return out


def _no_conv(c, targets, recent, prior, currency):
    if recent["conv"] > 0 or recent["cost"] <= 0:
        return None
    cpa = (targets.get("cpa") or {}).get("value")
    dropped = prior["conv"] >= MIN_CONV and recent["cost"] >= prior["cost"] * DROP_SPEND
    wasted = bool(cpa) and recent["cost"] >= cpa * NO_CONV_SPEND
    if not (dropped or wasted):
        return None
    evidence = ["Last 7 days: %s spent, 0 conversions" % money(recent["cost"], currency)]
    if dropped:
        evidence.append("The 7 days before: %s on %s" % (count(prior["conv"], "conversion"), money(prior["cost"], currency)))
        detail = "%s converted %s the week before and none in the last 7 days, while the spend went on (%s)." % (c["name"], count(prior["conv"], "time"), money(recent["cost"], currency))
    else:
        evidence.append("Target cost per conversion: %s" % money(cpa, currency))
        detail = "%s spent %s in the last 7 days, %.1f times its target cost per conversion (%s), and got no conversion." % (c["name"], money(recent["cost"], currency), recent["cost"] / cpa, money(cpa, currency))
    return _finding("no_conv", "no_conv:%s" % c["id"], "%s stopped converting" % c["name"] if dropped else "%s spent %s with no conversion" % (c["name"], money(recent["cost"], currency)), detail, c,
                    evidence=evidence, facts={"spent": recent["cost"], "before": prior["conv"]})


def judge_campaign(c, config, today, learning_days, currency):
    """(item, findings): one campaign's figures, the verdict of each against its target and what was found."""
    config = config or {}
    tol = _f(config.get("tolerance"), 0.0) / 100.0 or DEFAULT_TOLERANCE
    recent, prior = windows(c.get("days"), today)
    targets = targets_for(c, config)
    verdicts = [judge(m, targets.get(m), recent, prior, tol) for m in METRICS]
    muted = str(c["id"]) in (config.get("muted") or {})
    findings = []
    delivery = _delivery(c, today, recent)
    if delivery:
        findings.append(delivery)
    blocked = bool(delivery and delivery["level"] == "high")
    bid = _bidding(c)
    if bid:
        findings.append(bid)
    if not blocked:
        stopped = _no_conv(c, targets, recent, prior, currency)
        if stopped:
            findings.append(stopped)
        findings += [f for f in _drift(c, verdicts, recent, prior, currency) if not (stopped and f["rule"] == "cvr")]
    for f in (_learning(c, learning_days), _budget(c, verdicts)):
        if f:
            findings.append(f)
    findings.sort(key=lambda f: (-LEVEL_RANK[f["level"]], ORDER.index(f["rule"]), f["key"]))
    if muted:
        findings = []
    judged = [v for v in verdicts if v["state"] in ("on", "off")]
    item = {"id": str(c["id"]), "name": c["name"], "channel": c.get("channel") or "", "status": str(c.get("primaryStatus") or ""), "strategy": str(c.get("strategy") or ""), "muted": muted,
            "mutedWhy": (config.get("muted") or {}).get(str(c["id"])) or "", "level": findings[0]["level"] if findings else "", "findings": findings, "cost": round(recent["cost"], 2),
            "recent": {k: round(v, 4) for k, v in recent.items()}, "prior": {k: round(v, 4) for k, v in prior.items()}, "metrics": verdicts,
            "judged": len(judged), "off": sum(1 for v in judged if v["state"] == "off"), "hasTarget": any(v["state"] != "none" for v in verdicts)}
    return item, findings


def headline(campaigns, config, today, currency):
    """The account as one: the same four figures over every campaign, against the account's own saved targets."""
    days = {}
    for c in campaigns:
        for d in c.get("days") or []:
            row = days.setdefault(str(d.get("date")), {"date": d.get("date"), "impr": 0.0, "clicks": 0.0, "cost": 0.0, "conv": 0.0, "value": 0.0})
            for k in ("impr", "clicks", "cost", "conv", "value"):
                row[k] += _f(d.get(k))
    tol = _f((config or {}).get("tolerance"), 0.0) / 100.0 or DEFAULT_TOLERANCE
    recent, prior = windows(list(days.values()), today)
    acct = (config or {}).get("targets") or {}
    out = []
    for m in METRICS:
        v = _f(acct.get(m))
        target = {"value": v / 100.0 if m in ("cvr", "ctr") else v, "source": "account"} if v > 0 else None
        out.append(judge(m, target, recent, prior, tol))
    return {"recent": {k: round(v, 2) for k, v in recent.items()}, "prior": {k: round(v, 2) for k, v in prior.items()}, "metrics": out}


def analyse(account, *, config=None, today=None, learning_days=None, platform="google"):
    """The account's answer from what was read. `config` is what the team saved (see the module's docstring); `today` the account's date."""
    today = today or dt.date.today()
    currency = account.get("currency") or "USD"
    campaigns = [c for c in account.get("campaigns") or [] if c.get("id")]
    learning_days = learning_days or {}
    items, findings = [], []
    for c in campaigns:
        item, fs = judge_campaign(c, config, today, learning_days, currency)
        items.append(item)
        findings += fs
    items.sort(key=lambda i: (-LEVEL_RANK.get(i["level"], 0), -len(i["findings"]), -i["cost"], i["name"].lower()))
    counts = Counter(f["level"] for f in findings)
    seen = Counter(f["rule"] for f in findings)
    by_rule = [{"rule": r, "label": RULES[r]["label"], "level": max((f["level"] for f in findings if f["rule"] == r), key=lambda x: LEVEL_RANK[x]), "findings": seen[r], "fix": RULES[r]["fix"]} for r in ORDER if seen[r]]
    live = [i for i in items if not i["muted"]]
    status = "fix" if counts["high"] or counts["medium"] else ("check" if counts["low"] else "clean")
    return {"status": status, "campaigns": len(campaigns), "muted": len(items) - len(live), "judged": sum(1 for i in live if i["judged"]), "offTarget": sum(1 for i in live if i["off"]),
            "onTarget": sum(1 for i in live if i["judged"] and not i["off"]), "withoutTarget": sum(1 for i in live if not i["hasTarget"]),
            "notJudged": sum(1 for i in live if i["hasTarget"] and not i["judged"]), "targetsSet": any(i["hasTarget"] for i in items), "counts": {k: counts.get(k, 0) for k in ("high", "medium", "low")},
            "findingCount": len(findings), "byRule": by_rule, "items": items, "headline": headline(campaigns, config, today, currency)}
