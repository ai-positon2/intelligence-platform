"""Paid Marketing Agents: Keyword Visibility Tracker, what to do and in what order (no network).

One step per rule that found something: a competitor in the brand auctions first (it costs the brand's own searches), then a new competitor and a keyword that lost its place at the top of the page,
then the Quality Score, then the searches lost to rank. Each step says how many things it covers, what to do about it and the first keywords or domains to open.
"""
from __future__ import annotations

WHERE = 3
EXAMPLES = 3

TITLES = {
    "brand_hijack": "Protect the brand auctions from %s",
    "comp_new": "Look at %s that appeared in the auctions",
    "is_drop": "Win back the top of the page on %s",
    "top_is_low": "Decide whether %s should be at the top of the page more often",
    "qs_drop": "Find what lowered the Quality Score of %s",
    "qs_low": "Raise the Quality Score of %s",
    "lost_rank": "Look at the searches %s lose to rank",
    "comp_up": "Watch %s overlapping more",
}
NOUN = {"brand_hijack": ("competitor", "competitors"), "comp_new": ("competitor", "competitors"), "comp_up": ("competitor", "competitors")}


def _n(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def build(a):
    """{steps: [...]} for one analysed account (see tracker/paid_kvis_math.py); steps is empty when there is nothing to say."""
    steps = []
    findings = [f for it in a["items"] for f in it["findings"]] + list(a.get("competitorFindings") or [])
    for b in a["byRule"]:
        rule = b["rule"]
        hit = [f for f in findings if f["rule"] == rule]
        one, many = NOUN.get(rule, ("keyword", "keywords"))
        where = []
        for f in hit:
            name = f["keyword"] or f["facts"].get("domain") or ""
            if name and len(where) < WHERE:
                where.append(name)
        steps.append({"id": rule, "title": TITLES[rule] % _n(len(hit), one, many), "level": b["level"], "findings": b["findings"], "how": b["fix"], "where": where,
                      "examples": [f["detail"] for f in hit[:EXAMPLES]]})
    if steps:
        steps.append({"id": "recheck", "title": "Check again next week", "level": "info", "findings": 0, "where": [], "examples": [],
                      "how": "Press Refresh to read the account again, or let the weekly run do it. Give a change two weeks before judging it: the figures are whole weeks, and the average of the four before moves slowly."})
    return {"steps": steps}
