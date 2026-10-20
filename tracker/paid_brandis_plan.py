"""Paid Marketing Agents: Brand IS Monitor, the fix plan (no network).

An account whose brand impression share is low gets an ordered plan: what to do, why it applies (the figures behind it), how to do it in Google Ads and what
it is worth. Every step is chosen and sized from what the agent read (the shares lost to budget and to rank, the bidding strategy, the budget, last week's spend
and average CPC, the trend); nothing is a fixed paragraph. A figure that is an estimate says so. The agent changes nothing: every step is for a person.

Google reports the share lost to rank and the share lost to budget separately, so the plan works the two apart: budget first (the quicker one to check and fix),
then the bids, then relevance, then who else is bidding (Auction Insights, which the API does not carry).
"""
from __future__ import annotations

from . import paid_brandis_math as m

MIN_LOSS = 0.05                 # a loss of this much (5 points) or more gets a step of its own
LAGGARD_SPREAD = 0.20           # a brand campaign this far behind the best one is named
CPC_CEILING_X = 2.0             # a suggested CPC ceiling to start from: this many times last week's average CPC
BIG_DROP = 0.15                 # a fall this big on the week before comes first

_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "INR": "₹", "NZD": "NZ$", "SGD": "S$"}

SMART = ("TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE")


def money(v, currency="USD"):
    """12.5 as "$12.50", 1234.5 as "$1,235": a number from 100 up has no pence; a currency without a symbol is written as its code after the figure."""
    if v is None:
        return "n/a"
    body = format(round(v), ",") if abs(v) >= 100 else "%.2f" % v
    sym = _SYMBOL.get(currency)
    return sym + body if sym else "%s %s" % (body, currency or "")


def _names(campaigns):
    names = [c["name"] for c in campaigns]
    return ", ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else "") if names else "the brand campaigns"


def weeks_below(a):
    """How many whole weeks in a row, ending with the latest, the brand share was under the threshold (weeks too thin to judge end the run)."""
    n = 0
    for w in reversed(a["trend"]):
        if w["share"] is None or w["eligible"] < m.MIN_ELIGIBLE or w["share"] >= a["threshold"]:
            break
        n += 1
    return n


def _step(sid, level, title, why, how, effect=None, gain=None, campaigns=None):
    return {"id": sid, "level": level, "title": title, "why": why, "how": how, "effect": effect, "gain": gain, "campaigns": campaigns or []}


def _level(gain):
    return "high" if gain is not None and gain >= 0.10 - 1e-9 else "medium" if gain is not None and gain >= MIN_LOSS - 1e-9 else "low"


# ── Budget ──────────────────────────────────────────────────────────────────

def budget_step(a, cur):
    s, th = a["brand"], a["threshold"]
    lb = s["lostBudget"] or 0.0
    if lb < MIN_LOSS - 1e-9 or not s["share"]:
        return None
    hit = [c for c in a["brandCampaigns"] if (c.get("lostBudget") or 0) >= MIN_LOSS - 1e-9] or a["brandCampaigns"]
    hit = sorted(hit, key=lambda c: -(c.get("lostBudget") or 0))
    day = s["cost"] / 7.0
    budgets = [c.get("budget") for c in a["brandCampaigns"]]
    total = sum(b for b in budgets if b) if all(b for b in budgets) else None
    gain = min(lb, max(th - s["share"], 0.0))
    extra_gain = day * gain / s["share"]
    extra_full = day * lb / s["share"]
    why = "%s of the brand searches were lost to budget last week." % m.pct(lb)
    if total:
        used = day / total
        why += " The brand campaigns spent about %s a day against daily budgets of %s in total (%s of it)." % (money(day, cur), money(total, cur), m.pct(used))
        why += (" They are spending right up to the budget, so it is the real limit." if used >= 0.9 else
                " They spent well under the budget on average, so the budget runs out on some days and not others: look at the campaign by day to find which.")
    else:
        why += " The brand campaigns spent about %s a day." % money(day, cur)
    how = [
        "In Google Ads open Campaigns, find %s, and click the amount in the Budget column (or open the campaign, then Settings, then Budget)." % _names(hit),
        "Raise the daily budget by about %s in total, shared out in proportion to the budget each one lost. At last week's cost per impression that should win back about %s points of share%s; "
        "closing the whole budget loss (%s points) would take about %s a day. This is an estimate: a campaign that gets more impressions can cost more per click." % (
            money(extra_gain, cur), round(gain * 100), "" if gain >= lb - 1e-9 else " and bring the brand to the %s threshold" % m.pct(th), round(lb * 100), money(extra_full, cur)),
        "If the account cannot spend more overall, take it from the campaigns that earn least (usually broad non-brand ones): brand clicks are normally the cheapest and the "
        "best converting in the account, so brand budget is the last to cut.",
        "If a brand campaign uses a shared budget (its Budget column shows the shared budget's name), give it its own budget so a broader campaign cannot use it up first.",
        "Read this page again next Monday: the share lost to budget should fall. Google can spend up to twice the daily budget on a busy day, but not more than the daily budget times the days in the month.",
    ]
    return _step("budget", _level(gain), "Raise the daily budget of the brand campaigns", why, how,
                 "Worth up to %s points of brand impression share." % round(gain * 100), gain, [c["name"] for c in hit])


# ── Bids and rank ───────────────────────────────────────────────────────────

def _strategies(a):
    out = {}
    for c in a["brandCampaigns"]:
        out.setdefault(c.get("strategy") or "", []).append(c)
    return out


def _cpc(a):
    s = a["brand"]
    return s["cost"] / s["clicks"] if s["clicks"] else None


def bid_step(a, cur):
    s, th = a["brand"], a["threshold"]
    lr = s["lostRank"] or 0.0
    if lr < MIN_LOSS - 1e-9 or not s["share"]:
        return None
    gain = min(lr, max(th - s["share"], 0.0))
    cpc = _cpc(a)
    ceiling = money(cpc * CPC_CEILING_X, cur) if cpc else "twice last week's average CPC"
    cpc_txt = ("Last week's average CPC on the brand campaigns was %s." % money(cpc, cur)) if cpc else ""
    groups = _strategies(a)
    strategy = max(groups, key=lambda k: sum(c["eligible"] for c in groups[k])) if groups else ""
    campaigns = groups.get(strategy) or a["brandCampaigns"]
    names = _names(campaigns)
    word = strategy.replace("_", " ").lower() or "unknown"
    why = "%s of the brand searches were lost to rank last week: the brand ads were eligible but lost the auction. The main bidding strategy is %s (%s)." % (m.pct(lr), word, names)
    tis = ("In Settings, then Bidding, choose Change bid strategy, then Target impression share. Set the location to Absolute top of page (or Top of page if the brand ad should not "
           "fight for the very top), the target to 90% to 95%, and a Maximum CPC bid limit. " + cpc_txt + " Start the limit at about %s and lower it as the share climbs." % ceiling)
    if strategy == "TARGET_IMPRESSION_SHARE":
        title = "Raise the impression share target and the bid limit"
        how = ["Open %s, then Settings, then Bidding, and check the three settings of Target impression share." % names,
               "If the target is under 90%, raise it to 90% to 95%. If the location is Anywhere on the results page, set it to Top of page or Absolute top of page.",
               "The Maximum CPC bid limit is the usual cause when the target is already high: a ceiling below what brand clicks cost holds the share down. " + cpc_txt +
               " Raise the limit to about %s and lower it again once the share holds." % ceiling,
               "Watch the cost per click for a week; the share should rise within days."]
    elif strategy in SMART:
        target = strategy in ("TARGET_CPA", "TARGET_ROAS")
        title = "Stop the automated bidding from holding the brand back"
        how = ["Automated bidding bids only what it expects to convert. Brand searches are the highest intent in the account, but a tight target (or a short conversion history) still makes it bid low in some auctions.",
               ("Quickest: in Settings, then Bidding, loosen the target (for a target CPA, raise it by about 30%; for a target ROAS, lower it by about 30%) and read this page again in a week." if target else
                "This strategy has no target to loosen, so the loss comes from how little it expects each brand search to be worth."),
               "More reliable for a brand campaign: move it to Target impression share. " + tis,
               "Do it on the brand campaigns only (%s); keep the non-brand campaigns on automated bidding." % names]
    elif strategy == "TARGET_SPEND":
        title = "Raise the maximum CPC limit, or move to Target impression share"
        how = ["Maximize clicks bids as high as it needs within the budget, unless a Maximum CPC bid limit is set. Open %s, then Settings, then Bidding, and look for the limit." % names,
               "If there is a limit, raise it. " + cpc_txt + " About %s is a fair place to start." % ceiling,
               "If the share is still low, move the brand campaigns to Target impression share. " + tis]
    elif strategy == "MANUAL_CPC":
        title = "Raise the bids on the brand keywords"
        how = ["Open %s, then Keywords, and sort by impressions. Raise the bid on the exact brand keywords first by about 20%%, then on the phrase ones." % names,
               cpc_txt + " Check each week and raise again until the share lost to rank falls under 5 points." if cpc_txt else "Check each week and raise again until the share lost to rank falls under 5 points.",
               "If you would rather not set bids by hand, move the brand campaigns to Target impression share. " + tis]
    else:
        title = "Check the bidding of the brand campaigns"
        how = ["Open %s, then Settings, then Bidding, to see which strategy is in use (this agent could not tell)." % names,
               "For a brand campaign the usual choice is Target impression share. " + tis,
               "Change one thing at a time and read this page again after a week, so the effect of each change can be seen."]
    return _step("bids", _level(gain), title, why, how, "Worth up to %s points of brand impression share." % round(gain * 100), gain, [c["name"] for c in campaigns])


def relevance_step(a, cur):
    s = a["brand"]
    lr = s["lostRank"] or 0.0
    if lr < MIN_LOSS - 1e-9:
        return None
    hit = [c for c in a["brandCampaigns"] if (c.get("lostRank") or 0) >= MIN_LOSS - 1e-9] or a["brandCampaigns"]
    why = "Rank is the bid multiplied by the quality of the ad, so a brand ad with a weak Quality Score loses even with a high bid, and a good Quality Score makes the same bid go further."
    how = ["In Google Ads open Keywords for %s, then Columns, then Modify columns, and add Quality Score, Exp. CTR, Ad relevance and Landing page exp." % _names(hit),
           "Anything marked Below average is the cause. Expected CTR and ad relevance are fixed in the ad: put the brand name in headline 1 (pin it) and in a description, and keep one tight group of brand keywords per ad.",
           "Landing page experience is fixed on the page: the brand page the ad sends people to should load fast on a phone and show the brand name and the thing they searched for.",
           "Check the ads are approved and rated Good or Excellent for ad strength: an ad that is disapproved, limited or Poor wins fewer auctions."]
    return _step("relevance", "medium" if lr >= 0.15 else "low", "Check the Quality Score of the brand keywords and ads", why, how,
                 "A better Quality Score raises the rank the same bid earns, so the bid limit can stay lower.", None, [c["name"] for c in hit])


def competition_step(a, cur):
    s = a["brand"]
    lr = s["lostRank"] or 0.0
    if lr < MIN_LOSS - 1e-9:
        return None
    why = "Rank is a contest. When the share lost to rank stays high after the bids and the quality are right, someone else is bidding on the brand and outranking it. The Google Ads API does not carry Auction Insights, so this agent cannot name them."
    how = ["In Google Ads open Campaigns, select the brand campaign, then Insights and reports (or the Auction insights report), and set the date range to the last 7 days.",
           "Look at each competitor's Impression share, Overlap rate and Outranking share. A competitor that overlaps often and outranks the brand is bidding on it.",
           "Answer it with the bid and the ad: raise the bid (above), and make the brand ad claim the brand (for example Official site in a headline and the brand name in headline 1).",
           "If a competitor's ad uses the brand's trademark in its text, send Google a trademark complaint (in Google Ads Help, search for the trademark complaint form). Using the brand as a keyword is allowed; using it in the ad text is not."]
    return _step("competition", "low", "See who else is bidding on the brand", why, how, "Tells you whether the loss is a competitor or the account's own settings.", None, [])


# ── What changed, and the campaign behind it ────────────────────────────────

def changed_step(a, cur):
    d = a["delta"]
    if d is None or d > -m.DROP + 1e-9:
        return None
    s, prev = a["brand"], a["previous"]
    why = "The brand share fell from %s to %s in a week (%s). A fall that sharp usually has a cause that can be found." % (m.pct(prev["share"]), m.pct(s["share"]), m.points(d))
    how = ["In Google Ads open Tools, then Change history, set the last 14 days and filter to the brand campaigns. Look for a budget cut, a bidding strategy or target that was changed, keywords or ads that were paused or removed, and a new negative keyword.",
           "Open Campaigns and read the Status column of the brand campaigns: Limited by budget, Learning, Eligible (limited) or an ad schedule that ended early all show up there.",
           "Check Ads for any disapproved or limited brand ads, and Keywords for brand keywords that are paused or not eligible.",
           "If nothing changed in the account, the cause is outside it: a competitor started bidding on the brand (see Auction Insights) or the brand's search volume moved."]
    return _step("changed", "high" if d <= -BIG_DROP + 1e-9 else "medium", "Find out what changed last week", why, how, "Finds the cause of the fall, not just the symptom.", None, [])


def laggard_step(a, cur):
    camps = [c for c in a["brandCampaigns"] if c["share"] is not None and c["eligible"] >= m.MIN_ELIGIBLE]
    if len(camps) < 2:
        return None
    best, worst = max(camps, key=lambda c: c["share"]), min(camps, key=lambda c: c["share"])
    if best["share"] - worst["share"] < LAGGARD_SPREAD - 1e-9:
        return None
    cause = m.cause(worst["lostBudget"], worst["lostRank"])
    why = "%s is at %s while %s is at %s. Its loss is %s." % (worst["name"], m.pct(worst["share"]), best["name"], m.pct(best["share"]), (
        "%s lost to budget and %s lost to rank" % (m.pct(worst["lostBudget"]), m.pct(worst["lostRank"]))))
    how = ["Fix this campaign first: %s." % ("its budget (step above)" if cause == "budget" else "its bids and quality (steps above)" if cause == "rank" else "its budget first, then its bids and quality"),
           "Check whether it overlaps another brand campaign (an exact and a phrase campaign on the same brand, for example). Google shows one ad per advertiser for a search, so overlapping campaigns compete with each other.",
           "If the campaigns do overlap, add the exact brand terms as negative keywords to the broader one, or pause one of them."]
    return _step("laggard", "medium", "%s is the campaign holding the share down" % worst["name"], why, how,
                 "Raising it to the level of %s would lift the brand share most." % best["name"], None, [worst["name"]])


# ── The plan ────────────────────────────────────────────────────────────────

def build(a, currency="USD"):
    """{note, steps}: the plan for an analysed account (tracker/paid_brandis_math.analyse). Empty for an account that is on target and has not fallen, or that cannot be judged."""
    if a["status"] not in ("below", "ok"):
        return {"note": None, "steps": []}
    steps = []
    changed = changed_step(a, currency)
    if a["status"] == "below":
        parts = [budget_step(a, currency), bid_step(a, currency), laggard_step(a, currency), relevance_step(a, currency), competition_step(a, currency)]
        if changed and changed["level"] == "high":
            steps.append(changed)
            changed = None
        steps += [p for p in parts if p]
        if changed:
            steps.insert(1 if steps else 0, changed)
    elif changed:
        steps.append(changed)
    for i, s in enumerate(steps, 1):
        s["n"] = i
    note = None
    n = weeks_below(a) if a["status"] == "below" else 0
    if n >= 2:
        note = "The brand has been under %s for %d weeks in a row, so this is not a one-off." % (m.pct(a["threshold"]), n)
    return {"note": note, "steps": steps}
