"""Paid Marketing Agents: Creative & Asset Health, which creatives are wearing out and which assets are weak or missing (no network, no clock).

An account is passed in as plain data (see tracker/paid_creative.py for how it is read):

  platform   "google" or "meta"
  creatives  [{id, name, kind, campaign, group, weeks: [{impr, clicks, cost, reach}], rank}] the live creatives (a Google ad or Performance Max asset group, a Meta ad), `weeks` the last
             WEEKS whole weeks oldest first (a week with no delivery is all zeros), `rank` Meta's three rankings {quality, engagement, conversion} (words, None when unranked) or None
  weak       Google only: [{creativeId, name, campaign, group, field, text, label}] the assets Google itself rates Low, or None when they could not be read
  gaps       [{key, scope, campaign, campaignId, label, severity, have, target, add, message}] the assets a campaign or ad set is missing (Asset Coverage's own rules), or None when not read
  weeksRead  False when the weekly numbers could not be read

The last RECENT weeks are held against the BASE weeks before them. A creative with too little behind it is never judged and is counted as one the page could not judge. A fall that the whole
account shares (a quiet season, a dearer auction) is not blamed on the creative: it counts only when the creative moved clearly more than the account did. What the API does not show is
said in NOT_MEASURED, never left out silently.
"""
from __future__ import annotations

import math

LEVEL_RANK = {"high": 3, "medium": 2, "low": 1}
WEEKS = 5                           # whole weeks read
RECENT, BASE = 2, 3                 # the last 2 weeks against the 3 before them
MIN_RECENT_IMPR = 1000              # impressions the last weeks must hold before a creative is judged
MIN_BASE_IMPR = 2000                # and the weeks before them
MIN_BASE_CLICKS = 30                # clicks the weeks before must hold, so the usual rate is measured and not guessed
CTR_FALL = 0.25                     # a fall of this share in the click-through rate is a signal ...
CTR_FALL_HIGH = 0.40                # ... and this much a strong one
Z_MIN = 1.96                        # ... if it is also unlikely (5 in 100) to be chance
VS_ACCOUNT = 0.85                   # the creative must have moved at least this much more than the account (its ratio over the account's ratio)
FREQ_SIGNAL = 3.0                   # Meta: people reached this many times in the last week
FREQ_STRONG = 4.0                   # at this many, no comparison with the weeks before is needed
FREQ_RISE = 1.2                     # between the two, the frequency must be this much higher than the weeks before
CPM_RISE = 1.25                     # Meta: the cost of 1,000 impressions up this much
CPM_VS_ACCOUNT = 1.15
LOW_RANKS = ("BELOW_AVERAGE_35", "BELOW_AVERAGE_20", "BELOW_AVERAGE_10")

RULES = {
    "tired": {"level": "high", "area": "Tired creatives", "label": "A creative is wearing out",
              "fix": "Add a fresh variant next to it now: a new hook, a new angle or a new format, not a reworded copy. Keep the old one running until the new one has about 1,000 impressions, then pause the old one if its click-through rate stays lower."},
    "low_rating": {"level": "medium", "area": "Weak assets", "label": "Assets the platform rates low",
                   "fix": "Replace the assets rated Low with new ones that say something different (the platform scores a text that repeats another as weak). Keep the ones rated Good or Best."},
    "missing_asset": {"level": "medium", "area": "Missing assets", "label": "Assets a campaign is missing",
                      "fix": "Add the missing assets the finding names (each one gives the ad more room to show and the platform more to combine)."},
}
ORDER = ["tired", "low_rating", "missing_asset"]
FIX_META_TIRED = ("Add a fresh creative to the ad set now (a new hook, a new face, a new format) and widen the audience or lower the budget if the frequency is high. "
                  "Let the tired ad run down or pause it once the new one is delivering.")
FIX_META_RATING = ("Meta ranks this ad below the ads competing for the same people. Rework the hook and the first seconds or the first line, or replace the ad; "
                   "an ad ranked low on quality is usually shown less and charged more.")

NOT_MEASURED = [
    "How old a creative is, or when it was last changed: Google's API does not carry it, so a creative is judged on its last weeks, not its age.",
    "Frequency on Google: Google does not report how often one person saw a Search or Performance Max ad, so Google ads are judged on click-through rate and the platform's own asset ratings.",
    "Frequency on Meta is for the week, not for the ad's whole life, so a person reached many times over months is not seen.",
    "Performance Max assets have no weekly numbers of their own: the asset group is judged, and Google's Low ratings name the weak assets inside it.",
    "Meta ranks an ad only after 500 impressions; a newer ad is left out of the ratings.",
]


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


def _tot(weeks, field):
    return sum(_f(w.get(field)) for w in weeks)


def _split(weeks):
    """(recent weeks, base weeks), or (None, None) when there are too few weeks."""
    weeks = list(weeks or [])
    if len(weeks) < RECENT + 2:
        return None, None
    return weeks[-RECENT:], weeks[-(RECENT + BASE):-RECENT]


def _ctr(weeks):
    i = _tot(weeks, "impr")
    return _tot(weeks, "clicks") / i if i else None


def _cpm(weeks):
    i = _tot(weeks, "impr")
    return _tot(weeks, "cost") / i * 1000 if i else None


def _freq(weeks):
    """Impressions over reach for the weeks that have both."""
    w = [x for x in weeks if _f(x.get("reach")) > 0]
    r = _tot(w, "reach")
    return _tot(w, "impr") / r if r else None


def _z_fell(recent, base):
    """How many standard errors the recent click-through rate sits below the base one (two-proportion test)."""
    n1, n0 = _tot(recent, "impr"), _tot(base, "impr")
    c1, c0 = _tot(recent, "clicks"), _tot(base, "clicks")
    if n1 <= 0 or n0 <= 0:
        return 0.0
    p = (c1 + c0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    return ((c0 / n0) - (c1 / n1)) / se if se > 0 else 0.0


def pct(v):
    return "%.1f%%" % (v * 100) if v < 0.1 else "%.0f%%" % (v * 100)


def _ratio(recent, base, fn):
    a, b = fn(recent), fn(base)
    return (a / b) if a is not None and b else None


def account_totals(creatives):
    """The account's own movement: the weeks of every creative added together."""
    n = max((len(c.get("weeks") or []) for c in creatives), default=0)
    weeks = []
    for i in range(n):
        w = {"impr": 0.0, "clicks": 0.0, "cost": 0.0, "reach": 0.0}
        for c in creatives:
            cw = c.get("weeks") or []
            if i < len(cw):
                for k in w:
                    w[k] += _f(cw[i].get(k))
        weeks.append(w)
    return weeks


def rest_of(acct_weeks, c):
    """The account's weeks without this creative's own: a creative is held against the rest of the account, never against a total it makes up (a lone creative would then always move with
    'the account'). None when the rest has too little delivery to compare with."""
    own = c.get("weeks") or []
    rest = []
    for i, w in enumerate(acct_weeks):
        o = own[i] if i < len(own) else {}
        rest.append({k: max(0.0, _f(w.get(k)) - _f(o.get(k))) for k in ("impr", "clicks", "cost", "reach")})
    recent, base = _split(rest)
    if recent is None or _tot(recent, "impr") < MIN_RECENT_IMPR or _tot(base, "impr") < MIN_BASE_IMPR:
        return None
    return rest


def judge(c, acct_weeks, platform):
    """(signals, reason): the ways one creative is wearing out, or why it could not be judged (signals is then None). A signal is {kind, text, drop}."""
    recent, base = _split(c.get("weeks"))
    if recent is None:
        return None, "too few weeks of numbers"
    if _tot(recent, "impr") < MIN_RECENT_IMPR or _tot(base, "impr") < MIN_BASE_IMPR:
        return None, "too few impressions"
    rest = rest_of(acct_weeks, c)
    a_recent, a_base = _split(rest) if rest else (None, None)
    signals = []
    ctr_r, ctr_b = _ctr(recent), _ctr(base)
    if _tot(base, "clicks") >= MIN_BASE_CLICKS and ctr_b and ctr_r is not None:
        fell = 1 - ctr_r / ctr_b
        acct_ratio = _ratio(a_recent, a_base, _ctr) if a_recent else None
        ahead = acct_ratio is None or (ctr_r / ctr_b) <= acct_ratio * VS_ACCOUNT
        if fell >= CTR_FALL and _z_fell(recent, base) >= Z_MIN and ahead:
            signals.append({"kind": "ctr", "drop": fell, "text": "click-through rate down %d%%" % round(fell * 100),
                            "evidence": "Click-through rate: %s over the last %d weeks, %s over the %d weeks before (down %d%%)" % (pct(ctr_r), RECENT, pct(ctr_b), BASE, round(fell * 100))})
    if platform == "meta":
        last = [c["weeks"][-1]] if c.get("weeks") else []
        f_last, f_base = _freq(last), _freq(base)
        if f_last is not None and _tot(last, "impr") >= MIN_RECENT_IMPR / RECENT and f_last >= FREQ_SIGNAL and (f_last >= FREQ_STRONG or (f_base and f_last >= f_base * FREQ_RISE)):
            signals.append({"kind": "freq", "drop": 0.0, "text": "frequency %.1f" % f_last,
                            "evidence": "Frequency: each person reached %.1f times in the last week%s" % (f_last, " (%.1f in the weeks before)" % f_base if f_base else "")})
        cpm_r, cpm_b = _cpm(recent), _cpm(base)
        acct_cpm = _ratio(a_recent, a_base, _cpm) if a_recent else None
        if cpm_r is not None and cpm_b and cpm_r / cpm_b >= CPM_RISE and (acct_cpm is None or (cpm_r / cpm_b) >= acct_cpm * CPM_VS_ACCOUNT):
            rise = cpm_r / cpm_b - 1
            signals.append({"kind": "cpm", "drop": 0.0, "text": "cost per 1,000 views up %d%%" % round(rise * 100),
                            "evidence": "Cost per 1,000 impressions: %.2f over the last %d weeks, %.2f over the %d weeks before (up %d%%)" % (cpm_r, RECENT, cpm_b, BASE, round(rise * 100))})
    return signals, ""


def _short(s, n=70):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _finding(rule, key, title, detail, *, level=None, evidence=None, fix=None, where=None):
    r = RULES[rule]
    return {"rule": rule, "key": key, "level": level or r["level"], "area": r["area"], "title": title, "detail": detail, "fix": fix or r["fix"], "evidence": evidence or [], "where": where or {}}


def _where(c):
    return {"id": c.get("id"), "name": c.get("name"), "campaign": c.get("campaign") or "", "group": c.get("group") or "", "kind": c.get("kind") or ""}


def _tired(a, acct_weeks, platform):
    """([finding], check, judged, not judged): the wearing-out signals, one finding per creative."""
    found, judged, skipped = [], 0, {}
    cur = a.get("currency") or "USD"
    if not a.get("weeksRead", True):
        return [], {"key": "tired", "label": "Creatives hold their click-through rate", "state": "unknown", "note": "The weekly numbers could not be read, so no creative was judged."}, 0, {}
    for c in a.get("creatives") or []:
        signals, why = judge(c, acct_weeks, platform)
        if signals is None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        judged += 1
        if not signals:
            continue
        strong = len(signals) >= 2 or any(s["kind"] == "ctr" and s["drop"] >= CTR_FALL_HIGH for s in signals)
        spent = _tot(c["weeks"][-RECENT:], "cost")
        title = "%s: %s" % (_short(c.get("name")), ", ".join(s["text"] for s in signals))
        detail = ("%s spent in the last %d weeks on a creative that is wearing out%s. The platform shows it to the same people while fewer respond."
                  % (_money(spent, cur), RECENT, " (%s)" % c["campaign"] if c.get("campaign") else ""))
        found.append(_finding("tired", "tired:%s" % c["id"], title, detail, level="high" if strong else "medium", evidence=[s["evidence"] for s in signals],
                              fix=FIX_META_TIRED if platform == "meta" else None, where=_where(c)))
    n_skip = sum(skipped.values())
    if judged:
        note = "%s judged on the last %d weeks against the %d before%s." % (plural(judged, "creative"), RECENT, BASE, ("; %d had too little delivery to judge" % n_skip) if n_skip else "")
        state = "problem" if found else "ok"
    else:
        note = "None of the %s had enough delivery to judge (%s)." % (plural(n_skip, "creative"), ", ".join(sorted(skipped))) if n_skip else "There is no live creative to judge."
        state = "unknown"
    return found, {"key": "tired", "label": "Creatives hold their click-through rate", "state": state, "note": note}, judged, skipped


def _money(v, cur):
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "INR": "₹", "NZD": "NZ$", "SGD": "S$"}.get(cur)
    body = "{:,.0f}".format(v) if abs(v) >= 100 else "{:.2f}".format(v)
    return (sym + body) if sym else "%s %s" % (body, cur or "")


def _ratings(a, platform):
    """([finding], check): Google's Low asset ratings (grouped by creative) or Meta's below-average rankings."""
    found, cur = [], a.get("currency") or "USD"
    label = "Platform ratings of the assets" if platform == "google" else "Meta's quality rankings"
    if platform == "google":
        weak = a.get("weak")
        if weak is None:
            return [], {"key": "ratings", "label": label, "state": "unknown", "note": "Google's asset ratings could not be read."}
        by = {}
        for w in weak:
            by.setdefault(w["creativeId"], []).append(w)
        impr = {c["id"]: _tot(c.get("weeks") or [], "impr") for c in a.get("creatives") or []}
        for cid, items in by.items():
            first = items[0]
            seen = impr.get(cid, 0.0)
            level = "medium" if seen >= MIN_BASE_IMPR else "low"
            texts = ["%s: %s" % (w["field"], _short(w["text"], 60)) if w.get("text") else w["field"] for w in items]
            found.append(_finding("low_rating", "low:%s" % cid, "%s: %s rated Low by Google" % (_short(first.get("name")), plural(len(items), "asset")),
                                  "Google rates %s in this creative Low, which means %s performs worse than the others it is combined with." % ("it" if len(items) == 1 else "them", "it" if len(items) == 1 else "each"),
                                  level=level, evidence=texts[:6] + (["and %d more" % (len(texts) - 6)] if len(texts) > 6 else []), where={"id": cid, "name": first.get("name"), "campaign": first.get("campaign") or "", "group": first.get("group") or "", "kind": first.get("kind") or ""}))
        state = "problem" if found else "ok"
        note = "%s rated Low in %s." % (plural(len(weak), "asset"), plural(len(found), "creative")) if found else "Google rates no live asset Low."
        return found, {"key": "ratings", "label": label, "state": state, "note": note}
    ranked = unranked = 0
    for c in a.get("creatives") or []:
        r = c.get("rank")
        if not r or not any(r.values()):
            unranked += 1
            continue
        ranked += 1
        low = [k for k in ("quality", "engagement", "conversion") if r.get(k) in LOW_RANKS]
        if len(low) < 2:
            continue
        bottom = sum(1 for k in low if r.get(k) == "BELOW_AVERAGE_10")
        level = "high" if len(low) == 3 or bottom >= 2 else "medium"
        found.append(_finding("low_rating", "low:%s" % c["id"], "%s: ranked below average on %s" % (_short(c.get("name")), " and ".join(low)),
                              "Meta ranks this ad below the ads competing for the same people on %s." % plural(len(low), "of its three rankings", "of its three rankings"),
                              level=level, evidence=["%s ranking: %s" % (k.capitalize(), r[k].replace("_", " ").lower()) for k in low], fix=FIX_META_RATING, where=_where(c)))
    if not ranked:
        return [], {"key": "ratings", "label": label, "state": "unknown", "note": "Meta has ranked none of the %s yet (it needs 500 impressions)." % plural(unranked, "ad")}
    return found, {"key": "ratings", "label": label, "state": "problem" if found else "ok",
                   "note": "%s ranked%s." % (plural(ranked, "ad"), ("; %d too new to rank" % unranked) if unranked else "")}


def _gaps(a):
    """([finding], check): one finding per campaign (or ad set) and kind of missing asset."""
    gaps = a.get("gaps")
    if gaps is None:
        return [], {"key": "assets", "label": "Campaigns hold the assets they need", "state": "unknown", "note": "The asset coverage could not be read."}
    found = []
    for g in gaps:
        level = g.get("severity") if g.get("severity") in LEVEL_RANK else "low"
        where = {"id": g.get("campaignId"), "name": g.get("campaign"), "campaign": g.get("campaign") or "", "group": g.get("scope") or "", "kind": g.get("key") or ""}
        found.append(_finding("missing_asset", "gap:%s:%s:%s" % (g.get("campaignId") or g.get("campaign"), g.get("scope") or "", g.get("key")),
                              "%s: %s" % (_short(g.get("campaign") or "A campaign"), (g.get("message") or g.get("label") or "an asset is missing")[:140]),
                              "%s has %s of %s %s." % (g.get("scope") or "The campaign", g.get("have", 0), g.get("target", "?"), str(g.get("label") or "assets").lower()),
                              level=level, evidence=["%s: has %s, target %s, add %s" % (g.get("label") or g.get("key"), g.get("have", 0), g.get("target", "?"), g.get("add", "?"))], where=where))
    bad = sum(1 for f in found if f["level"] in ("high", "medium"))
    return found, {"key": "assets", "label": "Campaigns hold the assets they need", "state": "problem" if bad else "ok",
                   "note": "%s short of what it needs." % plural(bad, "place is", "places are") if bad else "Every campaign holds what it needs."}


def analyse(a, *, platform=None):
    """{status, counts, findingCount, findings, checks, byRule, headline}: one account. `status` is fix (a high or medium finding), check (only low ones) or clean."""
    platform = platform or a.get("platform") or "google"
    creatives = a.get("creatives") or []
    acct_weeks = account_totals(creatives)
    tired, tired_check, judged, skipped = _tired(a, acct_weeks, platform)
    rated, rated_check = _ratings(a, platform)
    gaps, gaps_check = _gaps(a)
    findings = tired + rated + gaps
    findings.sort(key=lambda f: (-LEVEL_RANK[f["level"]], ORDER.index(f["rule"]), f["key"]))
    counts = {k: sum(1 for f in findings if f["level"] == k) for k in LEVEL_RANK}
    status = "fix" if counts["high"] or counts["medium"] else "check" if counts["low"] else "clean"
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f["rule"], []).append(f)
    cur = a.get("currency") or "USD"
    return {"status": status, "counts": counts, "findingCount": len(findings), "findings": findings, "checks": [tired_check, rated_check, gaps_check],
            "byRule": [{"rule": r, "label": RULES[r]["label"], "level": RULES[r]["level"], "findings": len(by_rule[r])} for r in ORDER if r in by_rule],
            "headline": {"creatives": len(creatives), "judged": judged, "tired": len(tired), "notJudged": sum(skipped.values()), "spend2w": round(sum(_tot(c.get("weeks", [])[-RECENT:], "cost") for c in creatives), 2),
                         "currency": cur}}
