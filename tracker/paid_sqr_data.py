"""Paid Marketing Agents: Search Query Analysis, what Google Ads says.

The agent used to take three CSV exports. Google Ads can say the same things directly, so this reads them:

  * the search terms report (search_term_view, plus Performance Max's campaign_search_term_view), with Google's own "added" or
    "excluded" mark on every term;
  * every keyword the account bids on, so a term is never suggested twice;
  * every negative keyword a campaign has, and the shared lists attached to a campaign. (An ad group's own negatives stop one ad group, so
    they never cover a suggestion for a campaign. Account-level negative keywords are not part of what the Google Ads API returns.)

The terms are read costliest first, up to MAX_TERM_ROWS rows (an account with more says so). Each piece is saved on its own: the terms
for as long as the range is settled, the account's keywords and negatives for an hour. A piece that cannot be read does not stop the
others; it comes back as a warning, so the page can say what it could not check instead of pretending the account has none.
"""
from __future__ import annotations

import logging
import re

from . import paid_gads as g

log = logging.getLogger(__name__)

MAX_TERM_ROWS = 20_000
MAX_KEYWORD_ROWS = 50_000
MAX_NEGATIVE_ROWS = 250_000
MAX_RANGE_DAYS = 92
TTL_CONTEXT = 3600
CACHE_V = "v1:"
STATUSES = {"ADDED", "EXCLUDED", "ADDED_EXCLUDED"}          # Google's mark on a term; anything else is "no mark"


def check_range(start, end):
    start, end = g.check_range(start, end, "the range")
    import datetime as dt
    if (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1 > MAX_RANGE_DAYS:
        raise ValueError("the range is longer than %d days" % MAX_RANGE_DAYS)
    return start, end


# ── Queries ─────────────────────────────────────────────────────────────────

def _between(start, end):
    return "segments.date BETWEEN '%s' AND '%s'" % (start, end)


def q_terms(start, end):
    return ("SELECT search_term_view.search_term, search_term_view.status, campaign.name, campaign.advertising_channel_type, ad_group.name, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions FROM search_term_view WHERE %s AND metrics.impressions > 0 "
            "ORDER BY metrics.cost_micros DESC, metrics.clicks DESC LIMIT %d" % (_between(start, end), MAX_TERM_ROWS + 1))


def q_pmax_terms(start, end):
    return ("SELECT campaign_search_term_view.search_term, campaign.name, campaign.advertising_channel_type, "
            "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions FROM campaign_search_term_view WHERE %s "
            "AND metrics.impressions > 0 ORDER BY metrics.cost_micros DESC, metrics.clicks DESC LIMIT %d" % (_between(start, end), MAX_TERM_ROWS + 1))


def q_keywords():
    return ("SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, campaign.name, ad_group.name FROM ad_group_criterion "
            "WHERE ad_group_criterion.type = 'KEYWORD' AND ad_group_criterion.negative = FALSE AND ad_group_criterion.status != 'REMOVED' "
            "AND campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED' LIMIT %d" % (MAX_KEYWORD_ROWS + 1))


def q_campaign_negatives():
    return ("SELECT campaign_criterion.keyword.text, campaign_criterion.keyword.match_type, campaign.name FROM campaign_criterion "
            "WHERE campaign_criterion.type = 'KEYWORD' AND campaign_criterion.negative = TRUE AND campaign.status != 'REMOVED' LIMIT %d" % (MAX_NEGATIVE_ROWS + 1))


def q_list_attachments():
    return ("SELECT shared_set.id, shared_set.name, campaign.name FROM campaign_shared_set WHERE shared_set.type = 'NEGATIVE_KEYWORDS' "
            "AND shared_set.status = 'ENABLED' AND campaign_shared_set.status = 'ENABLED' AND campaign.status != 'REMOVED'")


def q_list_keywords():
    return ("SELECT shared_set.id, shared_criterion.keyword.text, shared_criterion.keyword.match_type FROM shared_criterion "
            "WHERE shared_set.type = 'NEGATIVE_KEYWORDS' AND shared_set.status = 'ENABLED' AND shared_criterion.type = 'KEYWORD' LIMIT %d" % (MAX_NEGATIVE_ROWS + 1))


# ── Parsing ─────────────────────────────────────────────────────────────────

def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _row(term, status, camp, ctype, adgroup, m):
    return {"t": term, "s": status if status in STATUSES else "", "c": camp, "ct": ctype, "g": adgroup,
            "i": _i(m.get("impressions")), "k": _i(m.get("clicks")), "x": round(_i(m.get("costMicros")) / 1e6, 3),
            "v": round(g._f(m.get("conversions")), 3)}


def parse_terms(rows):
    out = []
    for r in rows:
        v, c, a, m = (r.get("searchTermView") or {}), (r.get("campaign") or {}), (r.get("adGroup") or {}), (r.get("metrics") or {})
        term = str(v.get("searchTerm") or "").strip()
        if term:
            out.append(_row(term, v.get("status") or "", c.get("name") or "", c.get("advertisingChannelType") or "", a.get("name") or "", m))
    return out


def parse_pmax_terms(rows):
    out = []
    for r in rows:
        v, c, m = (r.get("campaignSearchTermView") or {}), (r.get("campaign") or {}), (r.get("metrics") or {})
        term = str(v.get("searchTerm") or "").strip()
        if term:
            out.append(_row(term, "", c.get("name") or "", c.get("advertisingChannelType") or "PERFORMANCE_MAX", "", m))
    return out


def _kw(node):
    k = (node or {}).get("keyword") or {}
    return str(k.get("text") or "").strip(), str(k.get("matchType") or "")


def parse_keywords(rows):
    out = []
    for r in rows:
        text, match = _kw(r.get("adGroupCriterion"))
        if text:
            out.append({"t": text, "m": match, "c": (r.get("campaign") or {}).get("name") or "", "g": (r.get("adGroup") or {}).get("name") or ""})
    return out


def parse_campaign_negatives(rows):
    """A campaign's negative keywords, one entry per keyword and match type with every campaign that has it (a keyword blocked in two hundred
    campaigns is one entry, not two hundred)."""
    by = {}
    for r in rows:
        text, match = _kw(r.get("campaignCriterion"))
        camp = (r.get("campaign") or {}).get("name") or ""
        if text and camp:
            e = by.setdefault((text.lower(), match), {"t": text, "m": match, "c": set()})
            e["c"].add(camp)
    return [{"t": e["t"], "m": e["m"], "c": sorted(e["c"])} for e in by.values()]


def parse_lists(attachments, keywords):
    """The shared negative lists: [{n name, c campaigns it is attached to, k [[keyword, match], ...]}]. A list applies in every campaign it is
    attached to; one attached to none does nothing, and is left out."""
    where, names = {}, {}
    for r in attachments:
        ss = r.get("sharedSet") or {}
        sid = str(ss.get("id") or "")
        camp = (r.get("campaign") or {}).get("name") or ""
        if sid and camp:
            where.setdefault(sid, set()).add(camp)
            names[sid] = ss.get("name") or names.get(sid) or ""
    kws = {}
    for r in keywords:
        sid = str((r.get("sharedSet") or {}).get("id") or "")
        text, match = _kw(r.get("sharedCriterion"))
        if text and sid in where:
            kws.setdefault(sid, {}).setdefault((text.lower(), match), [text, match])
    return [{"n": names.get(sid, ""), "c": sorted(where[sid]), "k": list(k.values())} for sid, k in kws.items()]


def merge_terms(search_rows, pmax_rows):
    """Performance Max's terms join the search terms; one Google also lists under the search view is not counted twice."""
    seen = {(r["t"].lower(), r["c"]) for r in search_rows}
    return search_rows + [r for r in pmax_rows if (r["t"].lower(), r["c"]) not in seen]


# ── The read ────────────────────────────────────────────────────────────────

def _soft(label, fn, warnings, quiet_400=False):
    """Run one piece; on a Google error return [] and say what could not be read (a 400 on a field this API version lacks is only logged)."""
    try:
        return fn()
    except g.AdsError as e:
        if quiet_400 and getattr(e, "status", None) == 400:
            log.warning("sqr: %s not available (%s)", label, str(e)[:200])
            return []
        warnings.append("%s could not be read from Google Ads (%s)." % (label[0].upper() + label[1:], str(e).rstrip(".")))
        return []


def read(store, account, start, end, *, force=False, notes=None, stamps=None, http=None):
    """Everything the agent needs for one account and range:
    {terms, truncated, keywords, negatives, warnings}. `terms` are rows {t term, s Google's mark, c campaign, ct campaign type, g ad group,
    i impressions, k clicks, x cost, v conversions}."""
    cid = account["id"]
    start, end = check_range(start, end)
    warnings = []

    def piece(key, ttl, make):
        return g._cached(store, CACHE_V + key, ttl, make, notes, force=force, stamps=stamps)

    def terms_job():
        def make():
            rows = parse_terms(g.search(cid, q_terms(start, end), store, http=http))
            return {"rows": rows[:MAX_TERM_ROWS], "truncated": len(rows) > MAX_TERM_ROWS}
        return piece("sqr:terms:%s:%s:%s" % (cid, start, end), g._ttl(end), make)

    def pmax_job():
        def make():
            rows = parse_pmax_terms(g.search(cid, q_pmax_terms(start, end), store, http=http))
            return {"rows": rows[:MAX_TERM_ROWS], "truncated": len(rows) > MAX_TERM_ROWS}
        return _soft("Performance Max search terms", lambda: piece("sqr:pmax:%s:%s:%s" % (cid, start, end), g._ttl(end), make),
                     warnings, quiet_400=True) or {"rows": [], "truncated": False}

    def keywords_job():
        def make():
            rows = parse_keywords(g.search(cid, q_keywords(), store, http=http))
            return {"rows": rows[:MAX_KEYWORD_ROWS], "truncated": len(rows) > MAX_KEYWORD_ROWS}
        return _soft("the account's keywords", lambda: piece("sqr:keywords:%s" % cid, TTL_CONTEXT, make), warnings) or {"rows": [], "truncated": False, "failed": True}

    def negatives_job():
        def make():
            camp = g.search(cid, q_campaign_negatives(), store, http=http)
            att = g.search(cid, q_list_attachments(), store, http=http)
            lists = g.search(cid, q_list_keywords(), store, http=http)
            return {"rows": parse_campaign_negatives(camp), "lists": parse_lists(att, lists),
                    "truncated": len(camp) > MAX_NEGATIVE_ROWS or len(lists) > MAX_NEGATIVE_ROWS}
        return _soft("the account's negative keywords", lambda: piece("sqr:negatives:%s" % cid, TTL_CONTEXT, make), warnings) or \
            {"rows": [], "lists": [], "truncated": False, "failed": True}

    terms, pmax, kws, negs = g._par([terms_job, pmax_job, keywords_job, negatives_job])
    rows = merge_terms(terms["rows"], pmax["rows"])
    if terms["truncated"] or pmax["truncated"]:
        warnings.append("This range has more than %s search term rows of one kind (search or Performance Max), so only the %s that cost the most of that kind are "
                        "included. Pick a shorter range to see the rest." % (format(MAX_TERM_ROWS, ","), format(MAX_TERM_ROWS, ",")))
    if kws.get("failed"):
        warnings.append("Without the keyword list, a term the account already bids on can be suggested again.")
    if negs.get("failed"):
        warnings.append("Without the negative keyword list, a negative the account already has can be suggested again.")
    if kws.get("truncated") or negs.get("truncated"):
        warnings.append("The account has more than %s keywords or negatives; the rest were not compared." % format(MAX_KEYWORD_ROWS, ","))
    return {"terms": rows, "truncated": bool(terms["truncated"] or pmax["truncated"]), "keywords": kws["rows"], "negatives": negs["rows"],
            "lists": negs.get("lists", []), "warnings": warnings, "notes": NOTES}


NOTES = ["Account-level negative keywords are not available from the Google Ads API, so a suggestion can repeat one the account has only at account level."]


# ── Which rules judge which account ─────────────────────────────────────────

OIA_FACILITIES = {"ami", "cpic", "ft. jesse", "heartland", "holmdel-nj", "idi-fl", "nhi", "odc-al", "oic-culpeper", "smi", "ud", "wolf river"}


def suggest_ruleset(name):
    """(ruleset id, why) for an account name. The rules are written per client, so an account is matched by what its name says; one that is
    not recognised is judged by the model alone (the "generic" rules)."""
    n = str(name or "").strip().lower()
    if re.search(r"riccobene", n):
        return "riccobene", "Riccobene"
    if n in OIA_FACILITIES or re.search(r"outpatient imaging|\boia\b", n):
        return "oia", "an Outpatient Imaging Affiliates facility"
    if re.search(r"eventgroove", n):
        if re.search(r"fundrais", n):
            return "generic", "the Eventgroove rules treat the word 'fundraising' as not relevant, which would misjudge an account named for it, so the model alone judges it"
        return "eventgroove", "Eventgroove"
    if re.search(r"gentle dental|great hill|great lakes", n):
        return "gentledental_42north", "a 42 North practice"
    if re.search(r"mc\s?hale", n):
        return "mchale", "McHale Landscaping"
    if re.search(r"beta bionics", n):
        return "beta_bionics", "Beta Bionics"
    return "generic", "no rules are written for this account yet"
