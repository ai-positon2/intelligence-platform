"""Paid Marketing Agents: Keyword Visibility Tracker, what a keyword's weeks and the auction say (no network, no clock: `today` is passed in).

An account is passed in as plain lists (see tracker/paid_kvis.py for how they are read):

  keywords  [{id, campaignId, campaign, adGroupId, adGroup, text, match, qs, cost, rows, qsWeeks}]
            rows is [{week (the Monday, ISO), impressions, clicks, cost, conv, share, top, absTop, lostRank}], one per week, shares between 0 and 1 or None;
            qs is the Quality Score now (1 to 10) or None, qsWeeks {week: score} the Quality Score Google kept for each week; cost is the last 28 days
  auction   [{campaignId, campaign, domain, week, overlap, outranking, above, topPct, share}]     the auction insights of the live Search campaigns by week
  config    what the team saved: {topIs (percent), qsMin, ignore: [domains]}

The last whole week is held against the four before it (tracker/paid_brandis_math.py's weeks and its eligible-searches weighting). A keyword's share is judged only with MIN_ELIGIBLE searches it was
eligible for (impressions divided by its share) in the week, and the average of the four before only with that many more: a keyword without that is counted as not judged, never called fine.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from . import paid_brandis_math as bm
from .paid_brandspend_math import money, pct

LEVEL_RANK = {"high": 3, "medium": 2, "low": 1}
WEEKS = 5                         # the last whole week and the four before it
MIN_ELIGIBLE = 100
DEFAULT_TOP_IS = 0.50
DEFAULT_QS_MIN = 5
TOP_N = 25
DROP_MEDIUM, DROP_HIGH = 0.15, 0.30        # a fall of top-of-page share, in share points
FAR_UNDER = 0.20                            # this far under the lowest wanted top share is medium
LOST_LOW, LOST_MEDIUM = 0.40, 0.60          # share of eligible searches lost to rank
QS_DROP, QS_DROP_BAD = 2, 4                 # a fall of this many points; high when the score is now this or less
QS_BAD = 4                                  # a Quality Score at or under this is medium
NEW_MIN, NEW_PRIOR = 0.10, 0.02             # a competitor with this overlap now and less than that before is new
UP_MIN, UP_BY = 0.20, 0.15                  # a competitor at this overlap, up by this much on its average, is rising
BRAND_NEW_MIN = 0.05                        # on a brand campaign a new competitor counts from here
EPS = 1e-9
OWN = {"you", "your domain", "your_domain"}

RULES = {
    "brand_hijack": {"level": "high", "label": "A competitor in the brand auctions", "fix": "Check what that advertiser's ad says (search the brand yourself in a private window), then protect the brand: a trademark complaint for its name in ad text, a higher brand bid or target impression share, and a look at the competitor's landing page."},
    "comp_new": {"level": "medium", "label": "A new competitor in the auctions", "fix": "Search the keywords it overlaps to see its ad and offer, then decide whether to answer it (an offer, a sharper headline) or leave it. Add its name to the ignore list below if it is a sister site or a partner."},
    "comp_up": {"level": "low", "label": "A competitor overlapping much more", "fix": "Compare its ad and offer with yours on the keywords it overlaps, and check your bids and Quality Score there before it takes more of the searches."},
    "is_drop": {"level": "medium", "label": "Top-of-page share fell", "fix": "Look at what changed the week it fell (a bid or target change, a budget, Quality Score, a new competitor) in the change history, and undo it or bid back up on that keyword."},
    "top_is_low": {"level": "medium", "label": "Rarely at the top of the page", "fix": "If the keyword earns its cost, raise its bid or improve its ad and landing page so the rank rises; if it does not, leave it where it is and say so with a lower target below."},
    "qs_drop": {"level": "medium", "label": "Quality Score fell", "fix": "Open the keyword's Quality Score columns (expected click-through rate, ad relevance, landing page experience) to see which part fell, then fix that part before changing bids."},
    "qs_low": {"level": "medium", "label": "Low Quality Score", "fix": "Fix the weakest of the three parts Google shows for the keyword (expected click-through rate, ad relevance, landing page experience): a poor score makes every click dearer."},
    "lost_rank": {"level": "low", "label": "Losing searches to rank", "fix": "The keyword is eligible but not winning the auction: raise the bid or the Quality Score on it, or accept the share if it does not pay."},
}
ORDER = ["brand_hijack", "comp_new", "is_drop", "top_is_low", "qs_drop", "qs_low", "lost_rank", "comp_up"]


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def num(x):
    x = round(float(x), 1)
    return str(int(x)) if x == int(x) else str(x)


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def points(a, b):
    """The fall from a to b in whole share points."""
    return int(round((a - b) * 100))


def week_list(today):
    """[(monday, sunday)] of the last WEEKS whole weeks before `today`, oldest first."""
    return bm.weeks(today, WEEKS)


def split_weeks(rows, today):
    """(recent combined, prior combined, recent week's Monday): the last whole week and the four before it, each put together by eligible searches."""
    wk = week_list(today)
    mondays = [m.isoformat() for m, _s in wk]
    recent = [r for r in rows if str(r.get("week")) == mondays[-1]]
    prior = [r for r in rows if str(r.get("week")) in mondays[:-1]]
    return bm.combine(recent), bm.combine(prior), mondays[-1]


def _finding(rule, key, title, detail, *, level=None, keyword=None, campaigns=None, evidence=None, facts=None):
    return {"rule": rule, "level": level or RULES[rule]["level"], "key": key, "title": title, "detail": detail, "keywordId": keyword["id"] if keyword else "", "keyword": keyword["text"] if keyword else "",
            "campaignId": str(keyword["campaignId"]) if keyword else "", "campaign": keyword["campaign"] if keyword else "", "campaigns": list(campaigns or []), "evidence": list(evidence or []),
            "facts": facts or {}, "fix": RULES[rule]["fix"]}


def _label(k):
    return '"%s" (%s)' % (k["text"], str(k.get("match") or "").lower()) if k.get("match") else '"%s"' % k["text"]


def judge_keyword(k, config, today, currency):
    """(item, findings) for one keyword."""
    config = config or {}
    top_is = (_f(config.get("topIs")) or DEFAULT_TOP_IS * 100) / 100.0
    qs_min = int(_f(config.get("qsMin")) or DEFAULT_QS_MIN)
    recent, prior, week = split_weeks(k.get("rows") or [], today)
    wk = week_list(today)
    old_week = wk[0][0].isoformat()
    ok_recent = recent["eligible"] >= MIN_ELIGIBLE and recent["top"] is not None
    ok_prior = prior["eligible"] >= MIN_ELIGIBLE and prior["top"] is not None
    qs_now = _f((k.get("qsWeeks") or {}).get(week))
    qs_now = _f(k.get("qs")) if qs_now is None else qs_now
    qs_before = _f((k.get("qsWeeks") or {}).get(old_week))
    findings = []
    name = _label(k)
    where = "%s in %s" % (name, k["campaign"])
    spend = "%s in 28 days" % money(k.get("cost") or 0.0, currency)
    if ok_recent and recent["top"] < top_is - EPS:
        under = top_is - recent["top"]
        evidence = ["Top of the page: %s of the searches it was eligible for (the lowest wanted is %s)" % (pct(recent["top"]), pct(top_is))]
        if recent["lostRank"] is not None:
            evidence.append("Lost to rank: %s" % pct(recent["lostRank"]))
        findings.append(_finding("top_is_low", "top_is_low:%s" % k["id"], "%s is at the top of the page %s of the time" % (name, pct(recent["top"])),
                                 "%s reached the top of the page for %s of the searches it was eligible for last week; %s is the lowest wanted." % (where, pct(recent["top"]), pct(top_is)),
                                 level="medium" if under >= FAR_UNDER - EPS else "low", keyword=k, evidence=evidence + [spend], facts={"top": recent["top"], "target": top_is}))
    if ok_recent and ok_prior:
        fall = prior["top"] - recent["top"]
        if fall >= DROP_MEDIUM - EPS:
            findings.append(_finding("is_drop", "is_drop:%s" % k["id"], "%s lost %d points of top-of-page share" % (name, points(prior["top"], recent["top"])),
                                     "%s reached the top of the page %s of the time last week against %s on average in the four weeks before." % (where, pct(recent["top"]), pct(prior["top"])),
                                     level="high" if fall >= DROP_HIGH - EPS else "medium", keyword=k,
                                     evidence=["Last week: %s at the top" % pct(recent["top"]), "The four weeks before: %s" % pct(prior["top"]), spend], facts={"top": recent["top"], "before": prior["top"]}))
    if recent["eligible"] >= MIN_ELIGIBLE and recent["lostRank"] is not None and recent["lostRank"] >= LOST_LOW - EPS:
        findings.append(_finding("lost_rank", "lost_rank:%s" % k["id"], "%s loses %s of its searches to rank" % (name, pct(recent["lostRank"])),
                                 "%s was eligible for about %s searches last week and lost %s of them because its rank was too low." % (where, "{:,}".format(recent["eligible"]), pct(recent["lostRank"])),
                                 level="medium" if recent["lostRank"] >= LOST_MEDIUM - EPS else "low", keyword=k, evidence=["Lost to rank: %s" % pct(recent["lostRank"]), spend],
                                 facts={"lostRank": recent["lostRank"]}))
    if qs_now is not None and qs_now < qs_min:
        findings.append(_finding("qs_low", "qs_low:%s" % k["id"], "%s has a Quality Score of %s" % (name, num(qs_now)), "%s has a Quality Score of %s; the lowest wanted is %d." % (where, num(qs_now), qs_min),
                                 level="medium" if qs_now <= QS_BAD else "low", keyword=k, evidence=["Quality Score now: %s" % num(qs_now), spend], facts={"qs": qs_now}))
    if qs_now is not None and qs_before is not None and qs_before - qs_now >= QS_DROP - EPS:
        findings.append(_finding("qs_drop", "qs_drop:%s" % k["id"], "%s Quality Score fell from %s to %s" % (name, num(qs_before), num(qs_now)),
                                 "%s had a Quality Score of %s four weeks ago and has %s now." % (where, num(qs_before), num(qs_now)), level="high" if qs_now <= QS_BAD else "medium", keyword=k,
                                 evidence=["Four weeks ago: %s" % num(qs_before), "Now: %s" % num(qs_now), spend], facts={"qs": qs_now, "before": qs_before}))
    findings.sort(key=lambda f: (-LEVEL_RANK[f["level"]], ORDER.index(f["rule"]), f["key"]))
    judged = ok_recent
    item = {"id": k["id"], "keyword": k["text"], "match": k.get("match") or "", "campaignId": str(k["campaignId"]), "campaign": k["campaign"], "adGroup": k.get("adGroup") or "", "cost": round(k.get("cost") or 0.0, 2),
            "qs": qs_now, "qsBefore": qs_before, "judged": judged, "recent": recent, "prior": prior, "level": findings[0]["level"] if findings else "", "findings": findings,
            "why": "" if judged else "only %s searches it was eligible for last week (at least %d are needed)" % ("{:,}".format(recent["eligible"]), MIN_ELIGIBLE)}
    return item, findings


def _domain(v):
    return str(v or "").strip().lower()


def _series(auction, ignore, today):
    """{(domain): {campaigns: {id: name}, recent: max overlap, prior: [overlap of each of the four weeks, the best across campaigns]}} for the domains not ignored."""
    mondays = [m.isoformat() for m, _s in week_list(today)]
    skip = {_domain(d) for d in ignore or []} | OWN
    per = {}
    for r in auction or []:
        dom = _domain(r.get("domain"))
        wk = str(r.get("week"))
        if not dom or dom in skip or wk not in mondays:
            continue
        cid, ov = str(r.get("campaignId")), _f(r.get("overlap"))
        if ov is None:
            continue
        d = per.setdefault(dom, {"domain": dom, "weeks": {m: {} for m in mondays}, "names": {}})
        d["names"][cid] = r.get("campaign") or cid
        d["weeks"][wk][cid] = max(ov, d["weeks"][wk].get(cid, 0.0))
    return per, mondays


def judge_auction(auction, config, today, brand_ids=None):
    """[finding] for the competitors: new ones and rising ones, those on a brand campaign as a possible brand hijack. `brand_ids` is the set of brand campaign ids."""
    per, mondays = _series(auction, (config or {}).get("ignore"), today)
    brand_ids = {str(b) for b in brand_ids or []}
    findings = []
    for dom in sorted(per):
        d = per[dom]
        recent_by_c = d["weeks"][mondays[-1]]
        prior_weeks = [d["weeks"][m] for m in mondays[:-1]]
        for scope in ("brand", "other"):
            cids = [c for c in d["names"] if (c in brand_ids) == (scope == "brand")]
            if not cids:
                continue
            recent = max([recent_by_c.get(c, 0.0) for c in cids] or [0.0])
            before = [max([w.get(c, 0.0) for c in cids] or [0.0]) for w in prior_weeks]
            avg = sum(before) / len(before)
            names = sorted(d["names"][c] for c in cids if recent_by_c.get(c, 0.0) > 0)[:3]
            is_new = max(before) < NEW_PRIOR - EPS and recent >= (BRAND_NEW_MIN if scope == "brand" else NEW_MIN) - EPS
            rising = not is_new and recent >= UP_MIN - EPS and recent - avg >= UP_BY - EPS
            if not (is_new or rising):
                continue
            facts = {"domain": dom, "overlap": recent, "before": avg}
            evidence = ["Overlap with your ads last week: %s" % pct(recent), "Average of the four weeks before: %s" % pct(avg), "In: %s" % ", ".join(names)]
            if scope == "brand":
                findings.append(_finding("brand_hijack", "brand_hijack:%s" % dom, "%s is %s in your brand auctions" % (dom, "new" if is_new else "overlapping more"),
                                         "%s showed in %s of your brand auctions last week%s." % (dom, pct(recent), " and had not before" if is_new else ", up from %s" % pct(avg)),
                                         level="high" if is_new else "medium", campaigns=names, evidence=evidence, facts=facts))
            elif is_new:
                findings.append(_finding("comp_new", "comp_new:%s" % dom, "%s is a new competitor" % dom, "%s showed in %s of your auctions last week and in almost none before." % (dom, pct(recent)),
                                         campaigns=names, evidence=evidence, facts=facts))
            else:
                findings.append(_finding("comp_up", "comp_up:%s" % dom, "%s overlaps %s now" % (dom, pct(recent)), "%s showed in %s of your auctions last week, up from %s on average." % (dom, pct(recent), pct(avg)),
                                         campaigns=names, evidence=evidence, facts=facts))
    return findings


def competitors(auction, config, today, limit=10):
    """The competitors to show: [{domain, overlap, before, outranking, above, campaigns}] by last week's overlap, highest first."""
    per, mondays = _series(auction, (config or {}).get("ignore"), today)
    out = []
    for dom, d in per.items():
        recent = max(d["weeks"][mondays[-1]].values() or [0.0])
        before = [max(d["weeks"][m].values() or [0.0]) for m in mondays[:-1]]
        out.append({"domain": dom, "overlap": recent, "before": round(sum(before) / len(before), 4), "campaigns": len([c for c, v in d["weeks"][mondays[-1]].items() if v > 0])})
    out = [c for c in out if c["overlap"] > 0]
    out.sort(key=lambda c: (-c["overlap"], c["domain"]))
    return out[:limit]


def analyse(account, *, config=None, today=None):
    """The account's answer from what was read (see the module's docstring)."""
    today = today or dt.date.today()
    currency = account.get("currency") or "USD"
    keywords = sorted(account.get("keywords") or [], key=lambda k: (-(k.get("cost") or 0.0), k["text"].lower()))
    items, findings = [], []
    for k in keywords:
        item, fs = judge_keyword(k, config, today, currency)
        items.append(item)
        findings += fs
    brand_ids = {str(c) for c in account.get("brandIds") or []}
    comp = judge_auction(account.get("auction"), config, today, brand_ids) if account.get("auction") else []
    findings += comp
    for n, it in enumerate(items, 1):
        it["rank"] = n
    items.sort(key=lambda i: (-LEVEL_RANK.get(i["level"], 0), -len(i["findings"]), -i["cost"], i["keyword"].lower()))
    counts = Counter(f["level"] for f in findings)
    seen = Counter(f["rule"] for f in findings)
    by_rule = [{"rule": r, "label": RULES[r]["label"], "level": max((f["level"] for f in findings if f["rule"] == r), key=lambda x: LEVEL_RANK[x]), "findings": seen[r], "fix": RULES[r]["fix"]} for r in ORDER if seen[r]]
    status = "fix" if counts["high"] or counts["medium"] else ("check" if counts["low"] else "clean")
    judged = [i for i in items if i["judged"]]
    return {"status": status, "keywords": len(items), "judged": len(judged), "notJudged": len(items) - len(judged), "withQs": sum(1 for i in items if i["qs"] is not None),
            "counts": {k: counts.get(k, 0) for k in ("high", "medium", "low")}, "findingCount": len(findings), "byRule": by_rule, "items": items, "competitorFindings": comp,
            "competitors": competitors(account.get("auction"), config, today), "auctionRead": bool(account.get("auction")), "week": week_list(today)[-1][0].isoformat()}
