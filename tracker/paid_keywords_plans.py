"""Paid Marketing Agents: Keyword Research, how to act on the result and which negative is in the way (no network).

The research lists keywords worth adding, negatives worth adding, and keywords the account's own negatives block in some campaigns. This says how to do each in Google Ads and Google
Ads Editor, and for the blocked ones names the negative that is in the way and what to do about it (take it out, narrow it, or move it) with the searches that would open up. The
worth of adding keywords is the size of the market, never a forecast: Keyword Planner gives searches and a top-of-page bid, and nothing here says how many of those searches the
account would win.
"""
from __future__ import annotations

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "$", "AUD": "$"}
UNBLOCK_SHOWN = 10
NAMES_SHOWN = 3
BLOCKS_SHOWN = 5


def money(v, cur="USD"):
    sym = SYMBOLS.get(cur or "USD")
    body = "{:,.2f}".format(v)
    return (sym + body) if sym else "%s %s" % (body, cur)


def num(v):
    return "{:,}".format(int(round(v)))


def _names(items, n=NAMES_SHOWN):
    shown = ", ".join('"%s"' % x for x in items[:n])
    return shown + (" and %d more" % (len(items) - n) if len(items) > n else "")


# ── Adding the keywords ─────────────────────────────────────────────────────

def add_plan(rows, coverage, cur="USD", unread=False):
    """{steps, worth} for the keywords marked to add, or None when there are none or the account's own keywords were not read (nothing is recommended then)."""
    take = [r for r in rows if r.get("include")]
    if unread or not take:
        return None
    steps = ["Download \"Keywords to add, for Google Ads Editor (CSV)\" from the Keywords tab.",
             "Open Google Ads Editor, download the account (Get recent changes), then choose Account, then Import, then From file, and pick the file. Look at the preview before you import.",
             "The keywords arrive Paused, each in the campaign and ad group named in its row (a new one is created if it does not exist yet). Before you enable a theme, check its ad group has a responsive search ad that uses the theme's own words and "
             "that the ad's final URL is the page that answers them; keywords with no ad serve nothing.",
             "Enable the keywords theme by theme, then Post. Leave the bidding to the campaign's strategy until the theme has a few weeks of data, then look at the Search terms report for what it really matched."]
    searches = sum(r.get("avg") or 0 for r in take)
    lows = [(r["lo"], r.get("avg") or 0) for r in take if r.get("lo") is not None and r.get("avg")]
    highs = [(r["hi"], r.get("avg") or 0) for r in take if r.get("hi") is not None and r.get("avg")]
    worth = "The %d keyword%s to add %s searched about %s times a month in all" % (len(take), "" if len(take) == 1 else "s", "is" if len(take) == 1 else "are", num(searches))
    if lows and highs:
        lo = sum(v * w for v, w in lows) / sum(w for _v, w in lows)
        hi = sum(v * w for v, w in highs) / sum(w for _v, w in highs)
        worth += ", and Keyword Planner puts the top-of-page bid for them at about %s to %s a click" % (money(lo, cur), money(hi, cur))
    worth += ". That is the size of the market, not a forecast of what the account would win: how much of it you reach depends on bids, quality and budget."
    return {"steps": steps, "worth": worth}


# ── Negatives to add ────────────────────────────────────────────────────────

def negatives_plan(negatives):
    """{steps, worth} for the negatives the research recommends, or None."""
    if not negatives:
        return None
    steps = ["Press \"Copy as Google Ads negatives\" on the Negatives tab: each negative comes out with its match type written in, [exact] or \"phrase\" or plain words for broad.",
             "For words that are never wanted anywhere (jobs, careers, free), add them at the account level: in Google Ads open Campaigns, then Keywords, then Negative keywords, click the +, choose the account level and paste. If the account level is not offered, use Admin, then Account settings, then Negative keywords.",
             "For words that only do harm in one campaign, open that campaign (Campaigns, the campaign, Keywords, Negative keywords, +) and paste them there. To use the same words in several campaigns, make a shared list (Tools, then Shared library, then Negative keyword lists), paste them in and apply the list to those campaigns.",
             "The research already left out any negative that would block a keyword the account bids on or one it recommends. Keywords you add later can still be blocked by a negative, so check the Account tab again after you add keywords."]
    return {"steps": steps, "worth": None}


# ── Keywords an existing negative blocks ────────────────────────────────────

def unblock_plans(rows):
    """[{negative, match, campaigns, count, searches, blocks, steps, worth}], the negatives of the account that stop wanted keywords in some of its campaigns, the one in the way of the most
    searches first. A wanted keyword is one judged a fit."""
    groups, order = {}, []
    for r in rows:
        neg = r.get("neg") or {}
        if r.get("fit") != "yes" or not neg.get("scope"):
            continue
        for h in neg.get("by") or []:
            key = (str(h["kw"]).lower(), str(h["match"]).lower())
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"negative": h["kw"], "match": h["match"], "campaigns": set(), "kws": {}}
                order.append(key)
            g["campaigns"].update(h.get("campaigns") or [])
            g["kws"][r["kw"]] = r.get("avg") or 0
    out = []
    for key in order:
        g = groups[key]
        blocks = sorted(g["kws"].items(), key=lambda kv: (-kv[1], kv[0]))
        camps = sorted(g["campaigns"])
        where = ("the campaigns %s" % _names(camps)) if camps else "the account's negative keywords (an account-level negative or a shared list)"
        match = g["match"].lower()
        steps = ["In Google Ads open Negative keywords for %s (Campaigns, the campaign, Keywords, Negative keywords) and find \"%s\" (%s match). If it is in a shared list, open Tools, then Shared library, then Negative keyword lists." % (where, g["negative"], match)]
        if match == "exact":
            steps.append("An exact negative stops only this one search, so if the search is wanted, remove the negative.")
        else:
            steps.append("A %s negative stops every search holding %s, so removing it lets in everything else it was blocking. Narrow it instead: remove it, then add an exact negative [ ] for each unwanted search it was there to stop (the Search terms report lists them), "
                         "or apply it only to the campaigns where it belongs." % (match, "all of its words" if match == "broad" else "its words together"))
        steps.append("Before you change it, search the Search terms report for searches that contain \"%s\": if most of what it stopped was spend with no conversions, narrow it rather than remove it." % g["negative"])
        searches = sum(v for _k, v in blocks)
        out.append({"negative": g["negative"], "match": g["match"], "campaigns": camps, "count": len(blocks), "searches": searches,
                    "blocks": [k for k, _v in blocks[:BLOCKS_SHOWN]], "steps": steps,
                    "worth": "It stops %d keyword%s judged a fit, searched about %s times a month in all (%s)." % (
                        len(blocks), "" if len(blocks) == 1 else "s", num(searches), _names([k for k, _v in blocks], BLOCKS_SHOWN))})
    out.sort(key=lambda p: (-p["searches"], p["negative"]))
    return out[:UNBLOCK_SHOWN]


def build(rows, coverage, plan, cur="USD", unread=False):
    return {"add": add_plan(rows, coverage, cur, unread), "negatives": negatives_plan((plan or {}).get("negatives")), "unblock": unblock_plans(rows)}
