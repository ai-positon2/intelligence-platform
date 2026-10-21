"""Paid Marketing Agents: Brand Spend Alert, what to do about a move in the brand spend (no network).

An account whose brand spend is outside its usual band gets an ordered plan: what to do, why it applies (the figures behind it), how to do it in Google Ads and what it is
worth. Chosen from the cause (more clicks, dearer clicks, both) and sized from what was read. A rise is not always a fault, so a rise that is more clicks starts with the
good reasons to rule out. A figure that assumes something says so. The agent changes nothing: every step is for a person.
"""
from __future__ import annotations

from . import paid_brandspend_math as m

WEEKS_PER_MONTH = 52.0 / 12.0
BRAND_IS_PAGE = "/p2/paid-marketing/brand-is"
DISAPPROVED_PAGE = "/p2/paid-marketing/disapproved-ads"


def _step(n, sid, title, why, how, worth=None, level="medium", campaigns=None):
    return {"n": n, "id": sid, "title": title, "level": level, "why": why, "how": how, "worth": worth, "campaigns": campaigns or []}


def _names(campaigns):
    names = [c["name"] for c in campaigns]
    return ", ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else "")


def _range(a):
    return "%s to %s" % (a["weekFrom"], a["weekTo"])


def _changed_step(a, level="medium"):
    return _step(0, "changed", "Check what changed in the account",
                 "Spend does not move on its own: a budget, a bid, a bidding strategy, a keyword or a status was changed, by a person, by a rule or by Google's own suggestions applied automatically.",
                 ["In Google Ads open Tools, then Change history (the menu names move a little between versions; it is the list of every edit).",
                  "Set the date range to the two weeks around the start of the move (%s and the week before) and filter by the brand campaigns." % _range(a),
                  "Look for: a daily budget raised or lowered, a bidding strategy or target changed, a maximum CPC bid limit changed, a keyword added or its match type widened to broad, a campaign paused or enabled, an ad refused, and changes made by Google (\"auto-applied recommendation\").",
                  "Note who made each change and when, and ask them why. The first change before the move is the likeliest cause."], None, level)


def _where_step(a, cur):
    movers = [c for c in a["campaigns"] if c["state"] in ("over", "under", "stopped", "new")]
    if not movers:
        return None
    top = movers[0]
    why = "%s moved most: %s last week against %s usually." % (top["name"], m.money(top["cost"], cur), m.money(top["usual"], cur) if top["usual"] is not None else "no spend before")
    if len(movers) > 1:
        why += " %d brand campaigns moved in all (%s)." % (len(movers), _names(movers))
    return _step(0, "where", "Find the campaign that moved",
                 why, ["In Google Ads open Campaigns and set the date range to last week (%s), then turn on Compare and compare it with the previous 4 weeks." % _range(a),
                       "Sort by the Cost change column, biggest first, with only the brand campaigns showing (filter by name).",
                       "Open the top campaign: its ad groups and keywords show whether the move is one keyword or the whole campaign."], None, "low", [c["name"] for c in movers[:3]])


def _clicks_step(a, cur):
    base, now = a["expected"], a["brand"]
    ratio = now["clicks"] / base["clicks"] if base.get("clicks") else None
    why = ("Clicks were %d%% %s a usual %s" % (round(abs(ratio - 1) * 100), "up on" if ratio > 1 else "down on", format(round(base["clicks"]), ",")) if ratio else "The clicks moved")
    if base.get("cpc") and now["cpc"]:
        same = abs(now["cpc"] / base["cpc"] - 1) < 0.02
        why += " and the cost per click was %s (%s against %s)%s." % ("level" if same else "also different", m.money(now["cpc"], cur), m.money(base["cpc"], cur),
                                                                   ", so most of the change in spend is the number of clicks" if same else "")
    else:
        why += "."
    return _step(0, "clicks", "Check the extra clicks are real brand searches",
                 why, ["In Google Ads open the brand campaign, then Insights and reports, then Search terms, and set last week.",
                       "Sort by Clicks. Look for the brand name with words that are not about buying (login, jobs, complaints), the names of resellers, marketplaces or competitors, and terms that are new this week.",
                       "Open Campaigns, then Columns, Modify columns, Performance, and add Invalid clicks (and its rate). A high invalid rate means clicks that were not people: Google credits those back, and it is worth reporting a spike.",
                       "Ask the client or look in the calendar for a reason: a launch, a campaign, an event, press or an email send. Brand searches rise after each of them and that spend is wanted."],
                 None, "medium")


def _cpc_step(a, cur):
    base, now = a["expected"], a["brand"]
    ratio = now["cpc"] / base["cpc"] if base.get("cpc") and now["cpc"] else None
    why = ("The cost per click was %s against a usual %s (%d%% %s)" % (m.money(now["cpc"], cur), m.money(base["cpc"], cur), round(abs(ratio - 1) * 100), "up" if ratio > 1 else "down")
           if ratio else "The cost per click moved")
    if base.get("clicks") and now["clicks"]:
        why += ", with clicks %s, so most of the change in spend is what each click costs." % ("level" if abs(now["clicks"] / base["clicks"] - 1) < 0.1 else "also different")
    else:
        why += "."
    worth = None
    if ratio and ratio > 1 and now["clicks"]:
        extra = now["clicks"] * (now["cpc"] - base["cpc"])
        if extra >= m.MIN_SWING:
            worth = "At the usual cost per click the same %s clicks would have cost about %s less last week: an estimate, since a click on the brand can cost more because the competition does, not only the bid." % (format(now["clicks"], ","), m.money(extra, cur))
    return _step(0, "cpc", "Find out why each click costs more" if ratio and ratio > 1 else "See why each click costs less",
                 why, ["In Google Ads open the brand campaign, then Insights and reports, then Auction insights, and set last week against the weeks before. A new name, or a name with a higher overlap rate, is somebody bidding on the brand.",
                       "Open the campaign's Settings, then Bidding. A target impression share has a maximum CPC bid limit: if it was raised or removed, the cost per click rises with it. A manual CPC bid raised on the main keywords does the same.",
                       "Compare the Quality Score of the brand keywords (Keywords, then Columns, Quality Score) with the weeks before: a fall makes every click dearer.",
                       "If a competitor is on the brand, the brand ad and the landing page should be the strongest in the auction; the Brand IS Monitor (%s) shows whether the share is also falling." % BRAND_IS_PAGE],
                 worth, "medium")


def _ceiling_step(a, cur):
    ex, now = a["expected"], a["brand"]
    excess = now["cost"] - ex["cost"]
    daily = ex["cost"] / 7.0
    top = next((c for c in a["campaigns"] if c["state"] == "over"), None)
    on = top["name"] if top else "the brand campaigns"
    return _step(0, "ceiling", "If the rise is not wanted, put a ceiling on it",
                 "Last week ran %s over a usual week. Until the cause is found and fixed, a budget keeps it from running on." % m.money(excess, cur),
                 ["In Google Ads open Campaigns, find %s and click its budget to edit it." % on,
                  "Set the daily budget near the usual daily spend of the brand (about %s a day across the brand campaigns, shared out by what each usually spends). Google can spend up to twice the daily budget on one day, but not more than 30.4 times it in a month." % m.money(daily, cur),
                  "If the cause is cost per click, set a maximum CPC bid limit (on the target impression share strategy, or as a cap on manual bids) a little above the usual cost per click rather than cutting the budget, so impressions are not lost.",
                  "Look again in a week. Remove the ceiling once the cause is gone, or the brand loses searches it should win."],
                 "If every week ran like last week, the extra would be about %s a month (%s a week): an estimate that assumes nothing else changes." % (m.money(excess * WEEKS_PER_MONTH, cur), m.money(excess, cur)), "medium",
                 [top["name"]] if top else None)


def _running_step(a, cur, stopped):
    ex = a["expected"]
    why = ("Nothing was spent from %s although a usual week is %s." % (_range(a), m.money(ex["cost"], cur))) if stopped and ex else "Spend is under the band, and a campaign that is not running is the quickest thing to rule out."
    worth = None
    if stopped and ex and ex.get("clicks"):
        worth = "A usual week brought about %s brand clicks for %s. Those searches were not answered by your ads: some went to whoever else was bidding on the brand, some to the organic result. The share is not known from this figure; the Brand IS Monitor (%s) shows it." % (
            format(round(ex["clicks"]), ","), m.money(ex["cost"], cur), BRAND_IS_PAGE)
    return _step(0, "running", "Check the brand campaigns are running",
                 why, ["In Google Ads open Campaigns, remove any status filter, and look at the Status column of each brand campaign: Paused, Ended, Removed, Limited by budget, Eligible (limited) and Not eligible each say what is wrong.",
                       "Open Ads for the brand campaigns: an ad that is Disapproved or Limited stops the campaign serving. Fix the policy reason it states and request a review.",
                       "Open each campaign's Settings and check the start and end dates and the ad schedule, and open Tools, then Billing, then Summary (or Payment settings) for a declined payment or a suspended account: a banner across the top of Google Ads says so.",
                       "If a campaign was paused on purpose, say so to the account manager and set it to ignore here by marking it Do not count below."],
                 worth, "high" if stopped else "medium")


def _fewer_clicks_step(a, cur):
    base, now = a["expected"], a["brand"]
    lost = (base["clicks"] - now["clicks"]) if base.get("clicks") else None
    worth = None
    if lost and lost > 0 and base.get("cpc"):
        worth = "About %s fewer brand clicks than a usual week, worth %s at the usual cost per click. That is the spend that did not happen; what those searchers did instead is not known: an estimate of the gap, not of lost sales." % (format(round(lost), ","), m.money(lost * base["cpc"], cur))
    return _step(0, "shown", "See whether the brand is still being shown",
                 "Clicks fell with the spend, so the ads were shown on fewer searches or fewer people were searching for the brand.",
                 ["Open the Brand IS Monitor (%s) for this account and read last week's impression share and whether the loss is budget or rank." % BRAND_IS_PAGE,
                  "If the share is on target, the fall is demand: brand searches fall after a campaign ends or in a quiet season. Google Search Console, Performance, with a query filter on the brand name, shows the same fall without ads.",
                  "If the share fell, work through the Brand IS Monitor's fix plan: budget, then bids, then Quality Score."], worth, "medium")


def _cheaper_step(a, cur):
    return _step(0, "cheaper", "Check the cheaper clicks are not a lost share",
                 "Clicks held within the band and each cost less. That is usually good news (a rival stopped bidding, or Quality Score rose), and it only needs checking that the share did not fall with it.",
                 ["Open the Brand IS Monitor (%s) and confirm the brand share is still on target." % BRAND_IS_PAGE,
                  "If it is, nothing needs fixing. If a bid limit or target was lowered on purpose, the change history (Tools, then Change history) shows who did it."], None, "low")


def _defend_step(a, cur):
    sh, now, ex = a["share"], a["brand"], a["expected"]
    worth = None
    if sh["now"] > 0 and now["clicks"] and now["cpc"]:
        lost = now["clicks"] * (sh["usual"] / sh["now"] - 1)
        if lost >= 1:
            worth = "At last week's impression share the brand got %s clicks; at the usual %s it would have got about %s more, worth about %s at the brand's own cost per click: an estimate, since the searches won back would not all click." % (
                format(now["clicks"], ","), m.pct(sh["usual"]), format(round(lost), ","), m.money(lost * now["cpc"], cur))
    return _step(0, "defend", "Defend the brand name against a competitor",
                 "The impression share fell from %s to %s and the part lost to rank rose from %s to %s. That is a competitor bidding on the brand name, or a drop in the quality of the brand ads." % (
                     m.pct(sh["usual"]), m.pct(sh["now"]), m.pct(sh["usualLostRank"]), m.pct(sh["lostRank"])),
                 ["In Google Ads open the brand campaign, then Insights and reports, then Auction insights, and set last week against the weeks before. The names that appeared or whose overlap rate and outranking share rose are the competitors.",
                  "Open the campaign's Settings, then Bidding. With a target impression share, raise the target toward 90 to 100% and check the maximum CPC bid limit is high enough to win; with manual bids, raise the brand keywords' bids a little at a time.",
                  "Make the brand ad the strongest in the auction: the brand name in the first headline, sitelinks and callouts filled in, and the landing page loading fast and matching the ad.",
                  "If a competitor uses your brand name in its ad text, Google's trademark complaint form (in Google Ads Help, search for Trademarks policy) can have those ads stopped in the markets where you hold the mark. Keep screenshots of the ads with their date."],
                 worth, "high" if sh["drop"] >= 0.20 else "medium")


def _leak_step(a, cur):
    lk = a["leak"]
    names = ", ".join('"%s"' % c["name"] for c in lk["campaigns"][:3]) + (" and %d more" % (lk["campaignCount"] - 3) if lk["campaignCount"] > 3 else "")
    worth = None
    if lk["overpay"]:
        worth = "%s clicks cost %s in these campaigns and would have cost about %s in the brand campaign at its own price: about %s overpaid in %d days (about %s a month). An estimate, since sending the searches to the brand campaign also changes which of them click." % (
            format(lk["clicks"], ","), m.money(lk["cost"], cur), m.money(lk["clicks"] * lk["brandCpc"], cur), m.money(lk["overpay"], cur), lk["days"], m.money(lk["overpay"], cur))
    return _step(0, "leak", "Send the brand searches back to the brand campaign",
                 "%s of the last %d days' spend in %s went on searches holding %s%s." % (
                     m.money(lk["cost"], cur), lk["days"], names, ", ".join('"%s"' % t for t in lk["terms"]),
                     (" (%s of the brand campaigns' own spend)" % m.pct(lk["shareOfBrand"])) if lk["shareOfBrand"] else ""),
                 ["In Google Ads open each campaign named here, then Keywords, then Negative keywords, click +, choose the campaign, and add the brand words as exact-match negatives, like [%s]. Exact keeps the brand out without blocking searches that only share a word." % lk["terms"][0],
                  "Check the brand campaign has those words as keywords (exact and phrase match) so the searches have somewhere to go.",
                  "For a Performance Max campaign, negatives do not apply: use the campaign's Brand exclusions (in its settings under Brand exclusions) to keep the brand name out.",
                  "Check the Search terms report of those campaigns in a week: the brand searches should have dropped out, and the brand campaign's clicks risen by about as many."],
                 worth, "medium" if lk["overpay"] or (lk["shareOfBrand"] or 0) >= 0.10 else "low", [c["name"] for c in lk["campaigns"][:3]])


def _spike_step(a, cur):
    sp = a["spikes"][0]
    unconverted = sp["kind"] == "unconverted"
    return _step(0, "spike", "Find out what happened on %s %s" % (sp["weekday"], sp["date"]),
                 "The brand campaigns spent %s that day against a usual %s%s." % (m.money(sp["cost"], cur), m.money(sp["usual"], cur),
                                                                                  (": %s clicks against a usual %s and %s conversions" % (format(sp["clicks"], ","), format(sp["usualClicks"], ","), _n(sp["conversions"]))) if unconverted else ""),
                 ["In Google Ads set the date range to that one day and open the brand campaign, then Insights and reports, then Search terms: the terms that took the clicks, and any odd ones, show whether it was real demand.",
                  "Add the Invalid clicks column (Columns, Modify columns, Performance) for the day: Google credits invalid clicks back automatically, so a large number explains and softens the spend.",
                  "Open Tools, then Change history for the day before and that day: a budget, a bid or a keyword change made then is the likeliest cause.",
                  "If the clicks look like bots or one location, add an IP exclusion (the campaign's Settings, then IP exclusions) or exclude the location, and report the pattern to Google's support if the credit does not arrive."],
                 "That day spent about %s more than a usual day. If the clicks were invalid it comes back as a credit; if they were real it was demand. An estimate of the gap, not of the loss." % m.money(sp["cost"] - sp["usual"], cur),
                 "medium" if unconverted else "low")


def _n(v):
    return ("%d" % round(v)) if abs(v - round(v)) < 0.05 else ("%.1f" % v)


def _agreed_step(a, cur):
    lim = a["limits"]
    if lim["status"] == "over":
        daily = lim["max"] / 7.0
        return _step(0, "agreed", "Bring the weekly brand spend back inside the agreed maximum",
                     "Last week's %s is %s over the %s the team saved for this account." % (m.money(lim["cost"], cur), m.money(lim["by"], cur), m.money(lim["max"], cur)),
                     ["In Google Ads open Campaigns and set the daily budgets of the brand campaigns so that together they come to about %s a day (the weekly maximum divided by 7). Google can spend up to twice a daily budget on one day, so stay a little under." % m.money(daily, cur),
                      "If the cause is the price of a click rather than the number, put a maximum CPC bid limit on the strategy instead of cutting the budget, so impressions are not lost.",
                      "Check the figure again at the end of the week: the page compares each whole week with the limit."],
                     "Back inside the limit the week would have cost %s less." % m.money(lim["by"], cur), "high")
    return _step(0, "agreed", "Bring the weekly brand spend back up to the agreed minimum",
                 "Last week's %s is %s under the %s the team saved for this account." % (m.money(lim["cost"], cur), m.money(lim["by"], cur), m.money(lim["min"], cur)),
                 ["In Google Ads open the brand campaigns and check each is enabled, has a budget and is not limited by budget; read the Status column's reason.",
                  "Open the Brand IS Monitor (%s) for the account: if the brand's impression share is low, that is where the missing spend went." % BRAND_IS_PAGE,
                  "If the brand has fewer searches than the minimum can use (a small brand), tell the account manager the limit is above what the brand can spend."],
                 "%s of the agreed spend was not used." % m.money(lim["by"], cur), "high")


def _pace_step(a, cur):
    mo = a["month"]
    left = max(1, mo["daysInMonth"] - mo["days"])
    remaining = mo["budget"] - mo["spent"]
    if mo["status"] == "over":
        return _step(0, "pace", "Stop the brand spend for the rest of the month, or tell the client",
                     "%s has been spent this month against a %s budget, with %d days left." % (m.money(mo["spent"], cur), m.money(mo["budget"], cur), left),
                     ["Decide with the account manager whether the client agreed to go over. If not, pause the brand campaigns or set their daily budgets to the smallest amount that keeps them on, and say so to the client.",
                      "If the brand budget is too low for the demand, put the case to the client with the figures: spend a day, searches won, conversions."],
                     "At %s a day the rest of the month would add about %s." % (m.money(mo["perDay"], cur), m.money(mo["perDay"] * left, cur)), "high")
    per_day = remaining / float(left)
    return _step(0, "pace", "Slow the brand spend so the month ends on its budget",
                 "At %s a day the month ends near %s, over the %s budget; the budget is gone on %s." % (m.money(mo["perDay"], cur), m.money(mo["projected"], cur), m.money(mo["budget"], cur), mo["runsOutOn"]),
                 ["In Google Ads set the daily budgets of the brand campaigns so that together they come to about %s a day for the %d days left (what is left of the budget, %s, divided by the days). Google can spend up to twice a daily budget on one day, so stay a little under." % (
                     m.money(per_day, cur), left, m.money(remaining, cur)),
                  "If the rise is the price of a click, a maximum CPC bid limit does the same without cutting impressions.",
                  "Check again in a week: the page re-projects the month every day."],
                 "Left alone it ends about %s over the budget: an estimate that assumes each day goes on costing what the days so far have." % m.money(mo["projected"] - mo["budget"], cur), "medium")


def _nospend_step(a, cur):
    names = ", ".join('"%s"' % c["name"] for c in a["silent"] if c["status"] == "ENABLED")
    return _step(0, "nospend", "Find out why an enabled brand campaign is not spending",
                 "%s %s enabled but has no spend on any day of the last 8 weeks." % (names, "is" if len([c for c in a["silent"] if c["status"] == "ENABLED"]) == 1 else "are"),
                 ["In Google Ads open Campaigns and read the Status column of the campaign: the reason beside it (Limited by budget, Eligible (limited), Not eligible, Low search volume, No ads) says what is wrong.",
                  "Check its budget is above zero and the bid or the target is high enough to enter the auction for the brand keywords.",
                  "Open Ads: an ad that is Disapproved stops the campaign (the Disapproved Ads Tracker, %s, lists them)." % DISAPPROVED_PAGE,
                  "Check Billing: a declined payment pauses everything. If the brand is small enough that its keywords have no searches, Google shows Low search volume and nothing can be done but to broaden the keywords."],
                 None, "medium", [c["name"] for c in a["silent"] if c["status"] == "ENABLED"][:3])


def extra_steps(a, cur):
    """The steps the readings add, in the order they should be worked: [step...]."""
    out = []
    if a.get("limits") and a["limits"]["status"] != "ok":
        out.append(_agreed_step(a, cur))
    if a.get("month") and a["month"].get("budget") and a["month"]["status"] in ("over", "pace-over"):
        out.append(_pace_step(a, cur))
    if a.get("share") and a["share"]["signal"] == "competitor":
        out.append(_defend_step(a, cur))
    if a.get("leak") and a["leak"]["cost"] >= m.MIN_SWING:
        out.append(_leak_step(a, cur))
    if a.get("spikes"):
        out.append(_spike_step(a, cur))
    if any(c["status"] == "ENABLED" for c in a.get("silent") or []):
        out.append(_nospend_step(a, cur))
    return out


def build(a, currency="USD"):
    """{steps, note} for one analysed account (the dict analyse() builds before the plan and the findings are added). No steps for an account on its usual spend with nothing else to say."""
    st, p = a["status"], a.get("partial")
    cur = currency
    extras = extra_steps(a, cur)
    pre, post = [], []
    if st == "stopped":
        pre, post = [_running_step(a, cur, True)], [_changed_step(a)]
    elif st == "over":
        c = a["cause"]
        if c in ("clicks", "both"):
            pre.append(_clicks_step(a, cur))
        if c in ("cpc", "both"):
            pre.append(_cpc_step(a, cur))
        post = [_where_step(a, cur), _changed_step(a), _ceiling_step(a, cur)]
    elif st == "under":
        c = a["cause"]
        pre = [_running_step(a, cur, False), _cheaper_step(a, cur) if c == "cpc" else _fewer_clicks_step(a, cur)]
        post = [_where_step(a, cur), _changed_step(a)]
    elif p and p["status"] != "ok":
        post = [_where_step(a, cur), _changed_step(a, "low")]
    steps = [s for s in pre + extras + post if s]
    for i, s in enumerate(steps, 1):
        s["n"] = i
    note = None
    if steps and (st in ("over", "under", "stopped") or extras):
        note = "These steps are chosen from the figures read. The limits are the account's own history (30% either way unless changed) and what the team saved for it, not a budget the client set unless one is saved."
    return {"steps": steps, "note": note}
