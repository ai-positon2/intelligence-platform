"""Paid Marketing Agents: Cross-Channel & Attribution Analysis, the arithmetic.

Everything here is plain arithmetic on the stored daily numbers of a Google Ads and a Meta ad account (the shape tracker/paid_gads.campaign_days and
tracker/paid_meta.campaign_days return: campaigns, days and rows [campaign index, day index, impressions, clicks, cost, conversions, value, all conversions,
all value, view-through conversions, ...]). Nothing is estimated and no model is asked; every function is one a test checks against a worked example.

  periods        the period asked for and the one of the same length straight before it
  totals         one platform's numbers over a range of days, all campaigns and by campaign type
  figures        the rates (click rate, cost per click, conversion rate, cost per conversion, return on ad spend) from totals
  shares         each platform's share of the spend, the conversions and the value
  decompose_cpa  the change in the blended cost per conversion, split exactly into a mix part and a rate part
  weekly         whole Monday to Sunday weeks of one platform's days
  links          whether one platform's spend moves with the other's conversions, with a strict bar
  reconcile      the platforms' claims against the conversions and revenue the person's own system recorded
"""
from __future__ import annotations

import datetime as dt
import math

SUMS = ["impr", "clicks", "cost", "conv", "value"]
# The column of a stored row (after its campaign and day) that carries each number. The first seven are the same on both platforms.
AT = {"impr": 0, "clicks": 1, "cost": 2}
MEASURE_AT = {"primary": {"conv": 3, "value": 4}, "all": {"conv": 5, "value": 6}}
VIEW_AT = 7                                      # Google's view-through conversions (Meta has none: its column is 0)

PRESETS = ("30", "60", "90", "last_month", "mtd")
PRESET_LABEL = {"30": "the last 30 days", "60": "the last 60 days", "90": "the last 90 days", "last_month": "last month", "mtd": "this month so far"}

MIN_CONV = 30                                    # conversions a platform needs in a period before its cost per conversion is ranked
CPA_GAP = 1.5                                    # one platform costing this many times the other per conversion is flagged
SHARE_GAP = 15.0                                 # points of share between the spend and the conversions
LINK_PAIRS = 10                                  # week over week pairs the link test needs
LINK_ALPHA = 0.0125                              # 0.05 over the four tests that are run
LINK_R = 0.5

ROLE = {"capture": "Captures demand", "create": "Creates demand", "mixed": "Mixed"}
# Rule of thumb, not a measurement: what a campaign type is for. A campaign that creates demand is not judged on last-click cost per conversion alone.
GOOGLE_TYPES = {
    "SEARCH": ("Search", "capture"), "SHOPPING": ("Shopping", "capture"), "SMART": ("Smart", "capture"), "LOCAL_SERVICES": ("Local Services", "capture"),
    "PERFORMANCE_MAX": ("Performance Max", "mixed"), "MULTI_CHANNEL": ("App", "mixed"), "LOCAL": ("Local", "mixed"), "TRAVEL": ("Travel", "capture"),
    "DEMAND_GEN": ("Demand Gen", "create"), "DISCOVERY": ("Demand Gen", "create"), "DISPLAY": ("Display", "create"), "VIDEO": ("Video", "create"),
    "ACCOUNT": ("All campaigns", "mixed")}
META_TYPES = {
    "META_SALES": ("Sales", "mixed"), "META_LEADS": ("Leads", "mixed"), "META_APP": ("App promotion", "mixed"),
    "META_TRAFFIC": ("Traffic", "create"), "META_AWARENESS": ("Awareness", "create"), "META_ENGAGEMENT": ("Engagement", "create"), "META_OTHER": ("Other objective", "mixed")}
PLATFORM_NAME = {"google": "Google Ads", "meta": "Meta Ads"}


class BadRequest(ValueError):
    pass


# ── Periods ─────────────────────────────────────────────────────────────────

def periods(preset, today=None):
    """{"current": (start, end), "previous": (start, end)} as ISO dates. The end is yesterday (today is not over); the previous period is the same number of days straight
    before the current one."""
    today = today or dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    preset = str(preset or "30")
    if preset not in PRESETS:
        raise BadRequest("range should be one of: %s" % ", ".join(PRESETS))
    if preset == "last_month":
        end = today.replace(day=1) - dt.timedelta(days=1)
        start = end.replace(day=1)
    elif preset == "mtd":
        start, end = today.replace(day=1), yesterday
        if end < start:
            raise BadRequest("This month has no finished day yet: pick another range.")
    else:
        end = yesterday
        start = end - dt.timedelta(days=int(preset) - 1)
    length = (end - start).days + 1
    p_end = start - dt.timedelta(days=1)
    p_start = p_end - dt.timedelta(days=length - 1)
    return {"current": (start.isoformat(), end.isoformat()), "previous": (p_start.isoformat(), p_end.isoformat()), "days": length}


# ── One platform's numbers ──────────────────────────────────────────────────

def blank():
    return {"impr": 0.0, "clicks": 0.0, "cost": 0.0, "conv": 0.0, "value": 0.0, "view": 0.0}


def type_of(platform, raw_type):
    """(key, label, role) of a campaign type: Google's advertising channel type or Meta's META_<objective>."""
    table = META_TYPES if platform == "meta" else GOOGLE_TYPES
    label, role = table.get(str(raw_type or "").upper(), ((str(raw_type or "Other").replace("META_", "").replace("_", " ").title() or "Other"), "mixed"))
    return str(raw_type or "OTHER"), label, role


def days_index(shape, lo, hi):
    """The day indexes of `shape` that fall in lo..hi (ISO dates, both included)."""
    return {i for i, d in enumerate(shape["days"]) if lo <= d <= hi}


def totals(shape, lo, hi, measure="primary", platform="google"):
    """{"all": totals, "types": {key: {"label", "role", **totals}}} over the days lo..hi of a stored-days shape. `measure` picks the conversion columns: "primary" or "all"."""
    cols = dict(AT, **MEASURE_AT[measure])
    keep = days_index(shape, lo, hi)
    out = {"all": blank(), "types": {}}
    kinds = []
    for c in shape["campaigns"]:
        kinds.append(type_of(platform, c.get("type")))
    for ci, di, *nums in shape["rows"]:
        if di not in keep:
            continue
        key, label, role = kinds[ci]
        t = out["types"].setdefault(key, dict(blank(), label=label, role=role))
        for k, at in cols.items():
            v = float(nums[at]) if at < len(nums) else 0.0
            t[k] += v
            out["all"][k] += v
        v = float(nums[VIEW_AT]) if VIEW_AT < len(nums) else 0.0
        t["view"] += v
        out["all"]["view"] += v
    return out


def daily(shape, measure="primary"):
    """One row a day, all campaigns added: {"days", "impr", "clicks", "cost", "conv", "value"}."""
    cols = dict(AT, **MEASURE_AT[measure])
    n = len(shape["days"])
    out = {"days": list(shape["days"])}
    for k in cols:
        out[k] = [0.0] * n
    for _ci, di, *nums in shape["rows"]:
        for k, at in cols.items():
            out[k][di] += float(nums[at]) if at < len(nums) else 0.0
    return out


def _ratio(a, b):
    return a / b if b and b > 0 else None


def figures(t):
    """The rates from totals: click rate (percent), cost per click, conversion rate (percent), cost per conversion, return on ad spend (None where the denominator is 0)."""
    ctr, cvr = _ratio(t["clicks"], t["impr"]), _ratio(t["conv"], t["clicks"])
    return {"impr": t["impr"], "clicks": t["clicks"], "cost": t["cost"], "conv": t["conv"], "value": t["value"],
            "ctr": None if ctr is None else ctr * 100, "cpc": _ratio(t["cost"], t["clicks"]), "cvr": None if cvr is None else cvr * 100,
            "cpa": _ratio(t["cost"], t["conv"]), "roas": _ratio(t["value"], t["cost"])}


def add(a, b):
    return {k: a.get(k, 0.0) + b.get(k, 0.0) for k in ("impr", "clicks", "cost", "conv", "value", "view")}


def change(cur, prev):
    """Relative change in percent from prev to cur, None when there is no earlier number to compare with."""
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100


def shares(parts):
    """{platform: {"cost", "conv", "value"}} each as a percent of the sum over the platforms (None where the sum is 0). `parts` is {platform: totals}."""
    out = {}
    sums = {k: sum(p[k] for p in parts.values()) for k in ("cost", "conv", "value")}
    for name, p in parts.items():
        out[name] = {k: (None if sums[k] <= 0 else p[k] / sums[k] * 100) for k in ("cost", "conv", "value")}
    return out


# ── What moved the blended cost per conversion ──────────────────────────────

def decompose_cpa(cur, prev):
    """The change in the blended cost per conversion from the previous period to the current, split exactly:

        blended cost per conversion = sum over platforms of (share of the conversions x that platform's cost per conversion)

    mix  = what moving the conversions between platforms did, at last period's costs;  rate = what each platform's own cost change did, at this period's mix.
    mix + the rates = the whole change, to the cent. `cur` and `prev` are {platform: totals}. A platform with spend and no conversions in either period has no cost per conversion,
    so nothing is split (`ok` False with the reason)."""
    names = [n for n in cur if cur[n]["cost"] > 0 or prev.get(n, blank())["cost"] > 0]
    if len(names) < 2:
        return {"ok": False, "why": "Only one platform has spend, so there is no mix to split."}
    c0 = sum(prev.get(n, blank())["conv"] for n in names)
    c1 = sum(cur[n]["conv"] for n in names)
    for n in names:
        if prev.get(n, blank())["conv"] <= 0 or cur[n]["conv"] <= 0:
            return {"ok": False, "why": "%s recorded no conversions in one of the two periods, so it has no cost per conversion to compare." % PLATFORM_NAME.get(n, n)}
    cpa0 = {n: prev[n]["cost"] / prev[n]["conv"] for n in names}
    cpa1 = {n: cur[n]["cost"] / cur[n]["conv"] for n in names}
    w0 = {n: prev[n]["conv"] / c0 for n in names}
    w1 = {n: cur[n]["conv"] / c1 for n in names}
    mix = sum((w1[n] - w0[n]) * cpa0[n] for n in names)
    rate = {n: w1[n] * (cpa1[n] - cpa0[n]) for n in names}
    start = sum(w0[n] * cpa0[n] for n in names)
    end = sum(w1[n] * cpa1[n] for n in names)
    return {"ok": True, "from": start, "to": end, "change": end - start, "mix": mix, "rate": rate, "rateTotal": sum(rate.values()),
            "mixShare": {n: {"before": w0[n] * 100, "after": w1[n] * 100} for n in names}}


# ── Weeks and the link between platforms ────────────────────────────────────

def weekly(d, lo, hi):
    """Whole Monday to Sunday weeks inside lo..hi of a `daily` result: [{"start", "cost", "conv", "value"}]."""
    out = []
    days = d["days"]
    i = 0
    while i < len(days) and (days[i] < lo or dt.date.fromisoformat(days[i]).weekday() != 0):
        i += 1
    while i + 6 < len(days) and days[i + 6] <= hi:
        out.append({"start": days[i], **{k: round(sum(d[k][i:i + 7]), 4) for k in ("cost", "conv", "value")}})
        i += 7
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def p_of(r, n):
    """The two-sided p of a correlation r over n pairs, from the Fisher transform (an approximation, good from about ten pairs)."""
    if r is None or n <= 3:
        return None
    r = max(-0.999999, min(0.999999, r))
    z = math.atanh(r) * math.sqrt(n - 3)
    return math.erfc(abs(z) / math.sqrt(2))


def _moves(values):
    """Week over week change of each week against the week before it (None when the week before had none)."""
    return [None] + [(values[i] - values[i - 1]) / values[i - 1] if values[i - 1] > 0 else None for i in range(1, len(values))]


def links(weeks, min_pairs=LINK_PAIRS):
    """Does one platform's spend move with the other's conversions? `weeks` is {platform: [week...]} (the same weeks for both). Four tests: each platform's spend against the other's
    conversions, in the same week and a week later, on week over week changes. A link needs p below LINK_ALPHA and a correlation of at least LINK_R. Returns {"ok", "weeks", "tests"}."""
    names = [n for n in ("google", "meta") if n in weeks]
    n_weeks = min((len(weeks[n]) for n in names), default=0)
    if len(names) < 2:
        return {"ok": False, "weeks": n_weeks, "why": "A link between platforms needs both of them.", "tests": []}
    if n_weeks < min_pairs + 1:
        return {"ok": False, "weeks": n_weeks, "tests": [], "why": "It needs %d whole weeks of both platforms (this range has %d): choose the last 90 days." % (min_pairs + 1, n_weeks)}
    spend = {n: _moves([w["cost"] for w in weeks[n][:n_weeks]]) for n in names}
    conv = {n: _moves([w["conv"] for w in weeks[n][:n_weeks]]) for n in names}
    tests = []
    for x, y in (("meta", "google"), ("google", "meta")):
        for lag in (0, 1):
            pairs = [(spend[x][t], conv[y][t + lag]) for t in range(n_weeks - lag) if spend[x][t] is not None and conv[y][t + lag] is not None]
            r = pearson([a for a, _ in pairs], [b for _, b in pairs]) if len(pairs) >= min_pairs else None
            p = p_of(r, len(pairs))
            reads = "none" if r is None or p is None or p >= LINK_ALPHA or abs(r) < LINK_R else ("together" if r > 0 else "opposite")
            tests.append({"spend": x, "conversions": y, "lag": lag, "n": len(pairs), "r": r, "p": p, "reads": reads, "enough": r is not None})
    return {"ok": True, "weeks": n_weeks, "tests": tests}


# ── The person's own numbers ────────────────────────────────────────────────

def reconcile(parts, actual_conv=None, actual_value=None):
    """The platforms' claims against what the person's own system recorded for the same period. `parts` is {platform: totals}. Returns None when nothing was typed.

    claimed > actual: the platforms between them claim more than happened, so some conversions are counted by both (or by neither being real). Each platform's cost per conversion
    and return are shown scaled down by the same factor, which assumes each platform over-claims by the same share: an assumption, and the page says so.
    claimed <= actual: the platforms claim part of what happened and the rest came from elsewhere (other channels, direct, offline); overlap cannot be seen, and nothing is scaled."""
    if actual_conv in (None, "") and actual_value in (None, ""):
        return None
    cost = sum(p["cost"] for p in parts.values())
    out = {"spend": cost, "conversions": None, "value": None}
    for key, actual, field in (("conversions", actual_conv, "conv"), ("value", actual_value, "value")):
        if actual in (None, "") or actual <= 0:
            continue
        claimed = sum(p[field] for p in parts.values())
        scale = actual / claimed if claimed > actual else 1.0
        out[key] = {"actual": actual, "claimed": claimed, "ratio": (claimed / actual) if actual else None, "over": claimed > actual,
                    "excess": max(0.0, claimed - actual), "scale": scale,
                    "perPlatform": {n: {"claimed": p[field], "scaled": p[field] * scale} for n, p in parts.items()}}
    if out["conversions"]:
        out["blendedCpa"] = cost / out["conversions"]["actual"]
        for n, p in parts.items():
            s = out["conversions"]["perPlatform"][n]["scaled"]
            out["conversions"]["perPlatform"][n]["cpa"] = (p["cost"] / s) if s > 0 else None
    if out["value"]:
        out["blendedRoas"] = out["value"]["actual"] / cost if cost > 0 else None
        for n, p in parts.items():
            s = out["value"]["perPlatform"][n]["scaled"]
            out["value"]["perPlatform"][n]["roas"] = (s / p["cost"]) if p["cost"] > 0 else None
    return out
