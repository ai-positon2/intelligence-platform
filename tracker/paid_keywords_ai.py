"""Paid Marketing Agents: Keyword Research, the judging.

Three questions to the model, each from the evidence and the answers before it:

  themes     the market grouped into a few buyer needs (the account's own campaigns offered as places for them)
  classify   for each keyword in a shortlist: is it a fit for this client, what is the searcher after, which theme, which match type, how important
             (asked in chunks of tracker/paid_keywords_data.CHUNK keywords, each chunk saved as it finishes)
  plan       the read in a few lines, quick wins, negative keywords to add, tests, questions for the client, risks

Every answer is a JSON schema answer of plain strings (no enums: a schema with many enums once made the API refuse it as too large), and is checked by
tracker/paid_keywords_check.py before it is kept. The model never does arithmetic and is never asked for a number: searches, competition and bids are Keyword
Planner's, copied by code (tracker/paid_keywords_data.py). The prompts' house rules, the schema builders and the model call are Strategy & Campaign Book's
(tracker/paid_strategy_ai.py).
"""
from __future__ import annotations

from . import paid_keywords as pk, paid_keywords_check as kck, paid_keywords_data as kd, paid_strategy_ai as ai
from .paid_strategy_ai import HOUSE, obj, rowspec, strs

MODEL = ai.MODEL

THEMES = obj(themes=rowspec("key", "name", "intent", "why", "existingCampaign", "priority"), assumptions=strs())
CLASSIFY = obj(rows=rowspec("kw", "fit", "intent", "theme", "match", "priority", "note"))
PLAN = obj(summary=strs(), quickWins=rowspec("what", "why"), negatives=rowspec("kw", "match", "why"), tests=rowspec("hypothesis", "how", "metric"),
           questions=rowspec("area", "question", "why", "urgency"), risks=rowspec("risk", "mitigation"), seasonal=rowspec("window", "action"))

NUMBERS = "The numbers (searches a month, competition, bids) are Google Keyword Planner's, given to you so you can weigh them. Never write a number of your own: do not estimate clicks, cost, conversions or revenue, and do not change one you were given."

THEMES_SYSTEM = HOUSE + """

You plan keyword research for a Google Search account. You are given the client, what is known about its business, the campaigns the account already has, what already converted, and a sample of the keyword ideas Google Keyword Planner found (the biggest ones). Group the market into themes a campaign could be built on.
- themes: between four and ten. Each is one buyer need or one thing the client sells, not a mix. `key` is short, lower case, letters and hyphens. `name` is two to four plain words. `intent` is the main thing the searchers want: transactional (ready to buy or book), commercial (comparing, price, best), local (near me, a place), informational (how, what, why), brand, competitor, support or other. `why` is one sentence: who searches this and why it matters to this client. `priority` is high, medium or low for what the client wants from advertising (demand, how well it fits what it sells, margin where known).
- `existingCampaign`: copy EXACTLY one of the account's campaign names if this theme belongs in it, else leave it empty. Never invent a campaign name.
- Include a theme named Brand for searches for the client's own name, and a theme named Competitors for searches for named rivals, only when the sample shows such searches.
- assumptions: what you had to assume.
""" + NUMBERS

CLASSIFY_SYSTEM = HOUSE + """

You judge keywords for a client's Google Search account. You are given a list of keyword objects. For EVERY one, return exactly one row, with `kw` copied exactly as given (only the keyword itself, nothing else from the object).
- fit: yes (someone looking for what the client sells, where it serves customers, who could become a customer: worth bidding on), maybe (could be right but depends on something, which you say in the note), no (not a customer: a different service or product, a place the client does not serve, free or do it yourself, jobs, students, only information with no buying in it, a rival's product the client does not sell, anything on the avoid list).
- intent: transactional, commercial, local, informational, brand, competitor, support, jobs or other.
- theme: the `key` of one of the themes you are given, or other.
- match: exact for a specific searcher who knows what they want, phrase for a service with its everyday modifiers, broad only for a wide head term and only when the account already has conversion data to steer it.
- priority: high (a clear buyer and real volume), medium, low.
- note: at most twelve words, the reason. Always give one for maybe and no.
A keyword the account already bids on (status bidding) or already matches (status covered) is still judged on its own merits: the status is information, not an answer.
Keywords that name a competitor are fit maybe at most (bidding on a rival is the client's decision, never yours) with intent competitor. Keywords that hold the client's own name are brand.
""" + NUMBERS

PLAN_SYSTEM = HOUSE + """

Now write the plan from the research and the judgement of each keyword. Never promise a result: a test is a hypothesis.
- summary: up to seven bullets a busy account manager reads in a minute: what the market looks like for this client, how much of it the account already covers, where the biggest gaps are, what is seasonal, what to do first, what to leave alone, what to ask the client.
- quickWins: the first moves (a theme to start, a converting search term to add as exact, a campaign to extend) with why. Say which theme or keyword; the numbers are in the evidence.
- negatives: negative keywords to add. For each, the smallest word or short phrase that makes a search irrelevant (never the whole search): `kw` the word or phrase, `match` phrase for a category (free, jobs, diy, course, a place the client does not serve) or exact only for a specific brand or address, `why`. Take them from the keywords judged no and from what the account's wasteful search terms show. Never a word that appears in a keyword judged yes, and not one the account already has.
- tests: hypotheses to test early (a match type, a theme, a seasonal start), how, and the metric.
- questions: what must be asked of the client, grouped by `area` (Offers and pricing, Locations and service area, Brand terms and competitors, Budget, Conversion tracking, Compliance), the question, why it matters, and urgency (before launch, first month, later). Do not ask what the evidence already answers.
- risks: what could go wrong (a rule that touches the service, a theme too wide, rival terms) and what to do.
- seasonal: windows where demand moves (from the patterns given) and what to do about each.
""" + NUMBERS


# ── The evidence ────────────────────────────────────────────────────────────

def _status_text(c):
    if c.get("status") == "bidding":
        return "the account bids on it"
    if c.get("status") == "covered":
        by = c.get("by") or {}
        return "already matched by %s (%s)" % (by.get("kw"), by.get("match"))
    return "new to the account"


def keyword_row(c):
    """One keyword as the model is shown it: an object, so `kw` is the whole of what it copies back."""
    row = {"kw": c["kw"], "searchesAMonth": c.get("avg"), "competition": c.get("comp") or None, "inTheAccount": _status_text(c)}
    if c.get("conv"):
        row["convertedAsASearchTerm"] = c["conv"].get("conv")
    if c.get("brand"):
        row["holdsTheClientsOwnName"] = True
    if c.get("rival"):
        row["namesACompetitor"] = c["rival"]
    if c.get("from"):
        row["foundFrom"] = c["from"][0]
    return row


def business_evidence(research):
    d = (((research or {}).get("business") or {}).get("data")) or {}
    return {k: d.get(k) for k in ("summary", "offerings", "searches", "vocabulary", "avoidWords", "compliance", "seasonality")} if d else None


def account_evidence(research):
    d = (((research or {}).get("account") or {}).get("data")) or {}
    if not d:
        return None
    return {"campaigns": d.get("campaigns"), "keywordCount": d.get("keywordCount"), "negativeCount": d.get("negativeCount"), "convertingSearchTerms": (d.get("terms") or {}).get("converting", [])[:25],
            "wastefulSearchTerms": (d.get("terms") or {}).get("wasteful", [])[:25]}


def site_headings(research):
    d = (((research or {}).get("website") or {}).get("data")) or {}
    return [{"url": p.get("url"), "title": p.get("title"), "h1": p.get("h1"), "h2": (p.get("h2") or [])[:6]} for p in (d.get("pages") or [])[:12]] or None


def write_themes(inputs, research, cands, call):
    sample = [{"kw": c["kw"], "searches": c.get("avg"), "status": c.get("status")} for c in sorted(cands, key=lambda c: -(c.get("avg") or 0))[:120]]
    user = ai._j({"client": ai.client_block(inputs), "business": business_evidence(research), "account": account_evidence(research), "siteHeadings": site_headings(research),
                  "keywordIdeaSample": sample, "notAvailable": ai.missing(research, inputs)})
    camps = [c["name"] for c in (((research or {}).get("account") or {}).get("data") or {}).get("campaigns") or []]
    out = kck.themes(ai._call_stage(call, "themes", THEMES_SYSTEM, user, THEMES, 4000), camps)
    out["campaignsKnown"] = bool(camps)                          # the themes were made knowing the account's campaigns, or without them
    return out


def write_classify(inputs, research, theme_list, chunk, call, idx=0):
    """One chunk: (judged, dropped). `chunk` is a list of candidates."""
    biz = business_evidence(research) or {}
    user = ai._j({"client": ai.client_block(inputs), "themes": [{"key": t["key"], "name": t["name"], "why": t["why"]} for t in theme_list], "avoid": pk.phrases(inputs.get("avoid")),
                  "competitorsNamed": [c.get("name") for c in inputs.get("competitors") or []], "offerings": [{"name": o.get("name"), "priority": o.get("priority")} for o in biz.get("offerings") or []],
                  "rulesThatTouchAds": biz.get("compliance"), "keywords": [keyword_row(c) for c in chunk]})
    wanted = {c["kw"] for c in chunk}
    return kck.classify(ai._call_stage(call, "classify", CLASSIFY_SYSTEM, user, CLASSIFY, 9000), wanted, theme_list)


def write_plan(inputs, research, assembled, call):
    rows = assembled["keywords"]
    yes_new = sorted((r for r in rows if r["fit"] == "yes" and r["status"] == "new"), key=lambda r: -(r["avg"] or 0))
    no = sorted((r for r in rows if r["fit"] == "no"), key=lambda r: -(r["avg"] or 0))
    account = (((research or {}).get("account") or {}).get("data")) or {}
    cov = assembled["coverage"]
    user = ai._j({"client": ai.client_block(inputs),
                  "coverage": {"keywordsJudged": cov["judged"], "keywordsThatFit": cov["yes"], "newKeywordsThatFit": cov["new"], "monthlySearchesOfTheNewKeywordsThatFit": cov["newSearches"],
                               "percentOfTheMarketsSearchesTheAccountAlreadyCovers": cov.get("poolPct"), "percentOfTheKeywordsJudgedAFitTheAccountAlreadyCovers": cov["pct"],
                               "meaning": "The market is every idea that shares a word with what the client sells."},
                  "themes": [{k: t.get(k) for k in ("name", "intent", "priority", "campaign", "existing", "yes", "maybe", "no", "new", "searches", "newSearches", "coverage", "avgLow", "avgHigh", "pattern", "top")}
                             for t in assembled["themes"]],
                  "topNewKeywords": [{"kw": r["kw"], "searches": r["avg"], "competition": r["comp"], "theme": r["theme"], "intent": r["intent"], "pattern": r["pattern"]} for r in yes_new[:30]],
                  "convertingTermsToAdd": [{"kw": r["kw"], "conversions": (r["conv"] or {}).get("conv"), "clicks": (r["conv"] or {}).get("clicks")} for r in rows if r.get("conv")][:25],
                  "judgedNo": [{"kw": r["kw"], "searches": r["avg"], "note": r["note"]} for r in no[:40]],
                  "competitorKeywords": [r["kw"] for r in rows if r["intent"] == "competitor"][:15],
                  "account": account_evidence(research), "existingNegatives": [n["t"] for n in (account.get("negatives") or [])[:60]], "business": business_evidence(research),
                  "notAvailable": ai.missing(research, inputs)})
    return kck.plan(ai._call_stage(call, "plan", PLAN_SYSTEM, user, PLAN, 9000), rows, account, allowed=kck.numbers_in(user))
