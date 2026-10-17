"""Paid Marketing Agents: Test & Experiment Tracker, the arithmetic that says whether a test has said anything.

Two arms (or two ads) are compared on one rate, conversions per click or clicks per impression, with a two-sided two-proportion z-test. Nothing here is
a model's opinion, and nothing is called a result before there is enough to count:

  * a test needs MIN_DAYS days;
  * conversion rates are compared once each arm has MIN_CLICKS clicks and the two together MIN_CONVERSIONS conversions;
  * click rates are compared once each arm has MIN_IMPRESSIONS impressions, and only when there are no conversions to look at, or after LONG_DAYS days
    when the conversions are still too few (the verdict then says it was decided on click rate).

An arm is a dict with impressions, clicks, conversions, cost and value. `compare` returns one verdict (see VERDICTS); `rank_group` holds several ads in one
ad group against the leader at a stricter bar, so picking the best of five is not read as a win.
"""
from __future__ import annotations

import math
from statistics import NormalDist

MIN_DAYS = 7
LONG_DAYS = 28
MIN_CLICKS = 100
MIN_CONVERSIONS = 30
MIN_IMPRESSIONS = 1000
ALPHA = 0.05
POWER = 0.8

METRICS = {
    "convRate": {"label": "conversion rate", "hits": "conversions", "of": "clicks", "unit": "conversions per click"},
    "ctr": {"label": "click rate", "hits": "clicks", "of": "impressions", "unit": "clicks per impression"},
}
VERDICTS = ("winner", "partial", "behind", "no_difference", "too_early", "no_data", "not_measured")
_KEYS = ("impressions", "clicks", "conversions", "cost", "value")

_N = NormalDist()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def arm(impressions=0, clicks=0, conversions=0, cost=0.0, value=0.0):
    """A normalised arm: numbers only, never negative."""
    return {"impressions": max(0.0, _num(impressions)), "clicks": max(0.0, _num(clicks)), "conversions": max(0.0, _num(conversions)),
            "cost": max(0.0, _num(cost)), "value": max(0.0, _num(value))}


def rate(hits, of):
    return hits / of if of > 0 else None


def metric_rate(a, metric):
    spec = METRICS[metric]
    return rate(a[spec["hits"]], a[spec["of"]])


def z_test(x1, n1, x2, n2):
    """{p1, p2, z, p} for the rate x1/n1 against x2/n2 (two-sided, pooled). None when either side has nothing to count. Counts may be fractional (Google's
    conversions are). When the pooled rate is 0 or 1 there is no spread to test and z is 0, p is 1."""
    if n1 <= 0 or n2 <= 0:
        return None
    p1, p2 = x1 / n1, x2 / n2
    pool = (x1 + x2) / (n1 + n2)
    var = pool * (1 - pool) * (1 / n1 + 1 / n2)
    if var <= 0:
        return {"p1": p1, "p2": p2, "z": 0.0, "p": 1.0}
    z = (p2 - p1) / math.sqrt(var)
    return {"p1": p1, "p2": p2, "z": z, "p": min(1.0, 2 * (1 - _N.cdf(abs(z))))}


def needed_per_arm(p1, p2, alpha=ALPHA, power=POWER):
    """Observations each arm needs for a gap as big as p1 against p2 to be told apart from chance. None when there is no gap to size."""
    if p1 is None or p2 is None or p1 == p2 or not (0 <= p1 <= 1 and 0 <= p2 <= 1):
        return None
    za, zb = _N.inv_cdf(1 - alpha / 2), _N.inv_cdf(power)
    spread = p1 * (1 - p1) + p2 * (1 - p2)
    if spread <= 0:
        return None
    return int(math.ceil((za + zb) ** 2 * spread / (p1 - p2) ** 2))


def detectable_gap(p, n, alpha=ALPHA, power=POWER):
    """The smallest relative gap (0.25 is 25 percent) from the rate p that two arms of n each could have told apart from chance. None when it cannot be worked out."""
    if p is None or p <= 0 or p >= 1 or n <= 0:
        return None
    za, zb = _N.inv_cdf(1 - alpha / 2), _N.inv_cdf(power)
    return (za + zb) * math.sqrt(2 * p * (1 - p) / n) / p


def pick_metric(a, b, days):
    """"convRate", "ctr" or None: which rate the two arms have enough to be compared on (see the module's rules)."""
    conv = a["conversions"] + b["conversions"]
    if min(a["clicks"], b["clicks"]) >= MIN_CLICKS and conv >= MIN_CONVERSIONS:
        return "convRate"
    if min(a["impressions"], b["impressions"]) >= MIN_IMPRESSIONS and (conv < 1 or days >= LONG_DAYS):
        return "ctr"
    return None


def _aim(a, b):
    """The metric a test that is too early is waiting to be able to use: conversion rate when there are conversions to look at, click rate when there are none."""
    return "convRate" if a["conversions"] + b["conversions"] >= 1 else "ctr"


def _pace(a, b, key, days):
    """The slower arm's count of `key` per day, or 0."""
    return min(a[key], b[key]) / days if days > 0 else 0.0


def _wait(a, b, days, metric):
    """What a test that is too early still needs: {days, more: {key: per arm still missing}, eta: days at the current pace or None}."""
    need = {}
    if metric == "convRate":
        short = max(0.0, MIN_CLICKS - min(a["clicks"], b["clicks"]))
        if short:
            need["clicks"] = int(math.ceil(short))
        gap = max(0.0, MIN_CONVERSIONS - (a["conversions"] + b["conversions"]))
        if gap:
            need["conversions"] = int(math.ceil(gap))
    else:
        short = max(0.0, MIN_IMPRESSIONS - min(a["impressions"], b["impressions"]))
        if short:
            need["impressions"] = int(math.ceil(short))
    waits = [max(0, MIN_DAYS - int(days))]
    for key, miss in need.items():
        per_day = _pace(a, b, key, days) if key != "conversions" else (a["conversions"] + b["conversions"]) / days if days > 0 else 0.0
        waits.append(int(math.ceil(miss / per_day)) if per_day > 0 else None)
    eta = None if any(w is None for w in waits) else max(waits)
    return {"days": max(0, MIN_DAYS - int(days)), "more": need, "eta": eta}


def _cpa(a):
    return round(a["cost"] / a["conversions"], 2) if a["conversions"] >= 1 else None


def summarise(a):
    """The numbers shown for an arm."""
    return {"impressions": round(a["impressions"]), "clicks": round(a["clicks"]), "conversions": round(a["conversions"], 2), "cost": round(a["cost"], 2),
            "value": round(a["value"], 2), "ctr": None if rate(a["clicks"], a["impressions"]) is None else round(100 * rate(a["clicks"], a["impressions"]), 2),
            "convRate": None if rate(a["conversions"], a["clicks"]) is None else round(100 * rate(a["conversions"], a["clicks"]), 2), "cpa": _cpa(a)}


def compare(control, trial, days, alpha=ALPHA):
    """The verdict for `trial` against `control` over `days` days:
    {verdict, metric, metricLabel, control, trial (rates in percent), relative (the trial's change in percent), p, confidence (percent), wait, detectable (percent), note}.
    "winner" and "behind" are about the trial: higher is better for both rates."""
    c, t = arm(**{k: control.get(k, 0) for k in _KEYS}), arm(**{k: trial.get(k, 0) for k in _KEYS})
    out = {"verdict": "no_data", "metric": None, "metricLabel": "", "control": None, "trial": None, "relative": None, "p": None, "confidence": None, "wait": None, "detectable": None,
           "needed": None, "days": int(days), "note": ""}
    if c["impressions"] <= 0 or t["impressions"] <= 0:
        out["note"] = "%s no impressions in this period." % ("Neither arm has" if c["impressions"] <= 0 and t["impressions"] <= 0 else ("The control has" if c["impressions"] <= 0 else "The trial has"))
        return out
    metric = pick_metric(c, t, days)
    shown = metric or _aim(c, t)
    out["metric"], out["metricLabel"] = shown, METRICS[shown]["label"]
    rc, rt = metric_rate(c, shown), metric_rate(t, shown)
    out["control"] = None if rc is None else round(100 * rc, 3)
    out["trial"] = None if rt is None else round(100 * rt, 3)
    if days < MIN_DAYS or metric is None:
        out["verdict"] = "too_early"
        out["wait"] = _wait(c, t, days, shown)
        return out
    spec = METRICS[metric]
    test = z_test(c[spec["hits"]], c[spec["of"]], t[spec["hits"]], t[spec["of"]])
    out["p"] = round(test["p"], 4)
    out["confidence"] = round(100 * (1 - test["p"]), 1)
    out["relative"] = round(100 * (rt - rc) / rc, 1) if rc else None
    if metric == "ctr" and c["conversions"] + t["conversions"] >= 1:
        out["note"] = "Decided on click rate: after %d days there are still too few conversions to compare conversion rates." % LONG_DAYS
    if test["p"] < alpha:
        out["verdict"] = "winner" if rt > rc else "behind"
    else:
        out["verdict"] = "no_difference"
        gap = detectable_gap(max(rc, rt) if (rc or rt) else None, min(c[spec["of"]], t[spec["of"]]), alpha)
        out["detectable"] = None if gap is None else round(100 * gap, 1)
        out["needed"] = needed_per_arm(rc, rt, alpha)
    return out


def rank_group(ads, days, alpha=ALPHA):
    """Holds the ads of one ad group against the best of them. `ads` is a list of {id, + an arm's numbers}. Returns
    {verdict, metric, metricLabel, leader (id or None), ads: {id: {verdict, rate, relative, p, confidence}}, eligible, note}.

    The metric is chosen once for the group (conversion rate when at least two ads have MIN_CLICKS clicks and together MIN_CONVERSIONS conversions, otherwise
    click rate on the same rule as `compare`). An ad below that sample is "too_early". Each other ad is held against the leader at alpha divided by the number of
    comparisons. The group is a "winner" (the leader beat every other eligible ad), "partial" (it beat some), or "no_difference"."""
    norm = [(str(a.get("id")), arm(**{k: a.get(k, 0) for k in _KEYS})) for a in ads]
    live = [(i, a) for i, a in norm if a["impressions"] > 0]
    out = {"verdict": "too_early", "metric": None, "metricLabel": "", "leader": None, "ads": {}, "eligible": 0, "note": "", "days": int(days)}
    if len(live) < 2:
        out["verdict"] = "no_data" if not live else "too_early"
        out["note"] = "Fewer than two ads had impressions in this period, so there is nothing to compare."
        for i, a in norm:
            out["ads"][i] = {"verdict": "no_data" if a["impressions"] <= 0 else "too_early", "rate": None, "relative": None, "p": None, "confidence": None}
        return out
    conv_ads = [(i, a) for i, a in live if a["clicks"] >= MIN_CLICKS]
    total_conv = sum(a["conversions"] for _i, a in live)
    metric, elig = None, []
    if len(conv_ads) >= 2 and sum(a["conversions"] for _i, a in conv_ads) >= MIN_CONVERSIONS:
        metric, elig = "convRate", conv_ads
    else:
        impr_ads = [(i, a) for i, a in live if a["impressions"] >= MIN_IMPRESSIONS]
        if len(impr_ads) >= 2 and (total_conv < 1 or days >= LONG_DAYS):
            metric, elig = "ctr", impr_ads
    shown = metric or ("convRate" if total_conv >= 1 else "ctr")
    out["metric"], out["metricLabel"] = shown, METRICS[shown]["label"]
    spec = METRICS[shown]
    for i, a in norm:
        r = metric_rate(a, shown)
        out["ads"][i] = {"verdict": "no_data" if a["impressions"] <= 0 else "too_early", "rate": None if r is None else round(100 * r, 3), "relative": None, "p": None, "confidence": None}
    if days < MIN_DAYS or metric is None:
        return out
    out["eligible"] = len(elig)
    if metric == "ctr" and total_conv >= 1:
        out["note"] = "Decided on click rate: after %d days there are still too few conversions to compare conversion rates." % LONG_DAYS
    lead_id, lead = max(elig, key=lambda x: (metric_rate(x[1], metric) or 0.0, x[1][spec["of"]], x[0]))
    out["leader"] = lead_id
    bar = alpha / (len(elig) - 1)
    rl = metric_rate(lead, metric)
    behind = 0
    for i, a in elig:
        if i == lead_id:
            continue
        test = z_test(lead[spec["hits"]], lead[spec["of"]], a[spec["hits"]], a[spec["of"]])
        ra = metric_rate(a, metric)
        sig = test["p"] < bar
        behind += 1 if sig else 0
        out["ads"][i].update({"verdict": "behind" if sig else "no_difference", "p": round(test["p"], 4), "confidence": round(100 * (1 - test["p"]), 1),
                              "relative": round(100 * (ra - rl) / rl, 1) if rl else None})
    out["verdict"] = "winner" if behind == len(elig) - 1 else ("no_difference" if behind == 0 else "partial")
    out["ads"][lead_id]["verdict"] = out["verdict"]
    out["behind"] = behind
    out["bar"] = round(bar, 4)
    return out
