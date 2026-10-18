"""Paid Marketing Agents: Landing Page & Relevancy Check, does the page match the ad.

The keywords people are bid on, the words they actually searched, the ads' headlines and the promises in the ads (a price, a discount, a free
offer) are held against what the page says: its title, its headings and its text. The matching is plain word matching
(tracker/paid_lp_match.py), so a finding can name the exact keywords and promises that were not found on the page. Two of Google's own ratings,
landing page experience and ad relevance, are read from Quality Score and counted by spend.

These checks apply to a page that Search ad groups send people to. A page reached only by Shopping or Performance Max has no keyword or ad
text to hold it against, and the checks say so. A page built by scripts, or one that could not be read, leaves them as "could not check".
Keywords and search terms are counted by what they spent, so one expensive keyword missing from the page weighs more than ten that cost little.
"""
from __future__ import annotations

from . import paid_lp_match as words
from . import paid_lp_model as m
from .paid_lp_checks_page import Ctx
from .paid_lp_model import make, not_applicable, plural, unknown
from .paid_lp_read import page_id

KEYWORDS_OK, KEYWORDS_FAIR = 0.75, 0.5             # share of keyword spend whose words are on the page
HEADLINE_OK, HEADLINE_FAIR = 0.5, 0.25             # share of keyword spend whose words are in the title or headings
TERMS_OK, TERMS_FAIR = 0.7, 0.45                   # share of search term spend whose words are on the page
TERMS_MIN = 3                                      # search terms needed to judge
ECHO_OK, ECHO_FAIR = 0.6, 0.3                      # share of ad group spend whose best headline the page's headings echo
RATING_FAIL, RATING_WARN = 0.3, 0.1                # share of rated keyword spend below average that fails, and that is worth a warning (less is noise)
CONTENT_OK, CONTENT_THIN = 150, 50                 # visible words
SHOWN = 8                                          # keywords or terms named in one finding

m.register([
    {"id": "match_meta_conversion", "section": "match", "title": "Meta rates the conversion rate average or better", "severity": "high",
     "why": "Meta compares each ad's conversion rate with the ads competing for the same people. Below average means the page converts fewer of the people the ad brings than other advertisers' pages do, and Meta charges more to show the ad.",
     "fix": "Fix what the other checks on this page find (match, speed, mobile, a short form) and Meta's rating follows in a few weeks."},
    {"id": "match_meta_quality", "section": "match", "title": "Meta rates the ad quality average or better", "severity": "medium",
     "why": "Meta's quality ranking compares how people judge an ad (what they hide, report or ignore) with the ads competing for the same people, and a low one raises what each person costs.",
     "fix": "Make the ad say what the page delivers, in plain words, with the same offer and the same picture of the service, and take out claims the page does not make."},
    {"id": "match_keywords_page", "section": "match", "title": "The keywords are on the page", "severity": "high",
     "why": "People searched for these words and clicked an ad written for them. A page that never uses them feels like the wrong place, and Google rates it down.",
     "fix": "Add the missing keywords, in the words people use, to the page's headline and body, or send those ad groups to a page that already uses them."},
    {"id": "match_keywords_headline", "section": "match", "title": "The keywords are in the page's headline", "severity": "medium",
     "why": "The headline is what a visitor reads first. When it does not repeat what they searched, they stop to wonder if they are in the right place.",
     "fix": "Rewrite the page's title and main headline so they say what the ad group's keywords say."},
    {"id": "match_terms", "section": "match", "title": "What people typed is on the page", "severity": "high",
     "why": "The search terms are the real wording of the people who clicked, and a page that does not use their words converts fewer of them.",
     "fix": "Use the most common search terms in the page's text, and add negative keywords for the ones the page should not answer."},
    {"id": "match_headline", "section": "match", "title": "The page's headline echoes the ad", "severity": "medium",
     "why": "A visitor who clicked an ad expects its headline's words on the page they land on.",
     "fix": "Write the page's main headline from the ad group's best ad headline, or write the ad from the page's."},
    {"id": "match_offers", "section": "match", "title": "The ad's offers are on the page", "severity": "high",
     "why": "An ad that promises a price, a discount or a free service and a page that does not mention it feels like bait, and visitors leave.",
     "fix": "Show every offer the ads make on the page, near the top and in the same words, or take it out of the ads."},
    {"id": "match_h1", "section": "match", "title": "The page has one clear main headline", "severity": "medium",
     "why": "The main headline (the H1) tells visitors and search engines what the page is. None, or a dozen, leaves them guessing.",
     "fix": "Give the page one H1 that says what is offered and where."},
    {"id": "match_lp_experience", "section": "match", "title": "Google rates the landing page experience average or better", "severity": "high",
     "why": "Landing page experience is one of the three parts of Quality Score. Below average raises the cost of every click and lowers where the ad shows.",
     "fix": "Fix what the other checks on this page find (match, speed, mobile) and Google's rating follows in a few weeks."},
    {"id": "match_ad_relevance", "section": "match", "title": "Google rates the ad relevance average or better", "severity": "medium",
     "why": "Ad relevance is one of the three parts of Quality Score and measures how closely the ad speaks to the keyword.",
     "fix": "Split the ad group so each holds keywords on one theme, and put the keyword's words in the ad headlines."},
    {"id": "match_content", "section": "match", "title": "The page has enough to read", "severity": "low",
     "why": "A page with a handful of words does not answer the questions a visitor has before they act.",
     "fix": "Add what a visitor needs to decide: what is offered, who it is for, the price or how it is worked out, and why to choose this business."},
])


# ── What the page is held against ───────────────────────────────────────────

def via_ads(group, pid):
    """True when one of the ad group's ads sends people to the page `pid`."""
    return any(page_id(u) == pid for ad in group["ads"] for u in ad["urls"])


def search_groups(c):
    """The ad groups that send people to this page and carry something to compare with (a keyword or an ad's words)."""
    pid = c.page["id"]
    out = []
    for g in c.groups:
        if g.get("campaignType") not in ("SEARCH", "", None):
            continue
        if not (g["keywords"] or any(ad["headlines"] for ad in g["ads"])):
            continue
        if via_ads(g, pid) or any(k["url"] and page_id(k["url"]) == pid for k in g["keywords"]):
            out.append(g)
    return out


def page_keywords(c, groups):
    """The keywords whose clicks land on this page: an address on the keyword decides, else its ad group's ads."""
    pid = c.page["id"]
    rows = []
    for g in groups:
        ads = via_ads(g, pid)
        for k in g["keywords"]:
            if (k["url"] and page_id(k["url"]) == pid) or (not k["url"] and ads):
                rows.append(k)
    return rows


def meta_groups(c):
    """The Meta ad sets whose ads send people to this page and carry words to compare with it."""
    pid = c.page["id"]
    return [g for g in c.groups if g.get("platform") == "meta" and via_ads(g, pid) and any(ad["headlines"] or ad["descriptions"] for ad in g["ads"])]


def ad_groups(c):
    """What the ads' words are held against: the Search ad groups (as search_groups) and the Meta ad sets that send people to the page."""
    return search_groups(c) + meta_groups(c)


def _no_groups(check_id, c, groups=None):
    """The finding when there is nothing to hold the page against, or None when there is. `groups` is the function that finds what the check holds the page against."""
    groups = groups or search_groups
    if not groups(c):
        ok = c.page.get("ok") or {}
        google = "google" in c.platforms
        if google and ok.get("ads") is False:
            return unknown(check_id, "the ads could not be read, so it is not known which keywords send people to this page")
        if "meta" in c.platforms and ok.get("meta:ads") is False and groups is ad_groups:
            return unknown(check_id, "the Meta ads could not be read, so it is not known what they say")
        if groups is ad_groups and "meta" in c.platforms:
            return not_applicable(check_id, "No Search ad group or Meta ad set with words to compare sends people to this page (Shopping and Performance Max have no keywords or ad text to compare it with).")
        return not_applicable(check_id, "No Search ad group sends people to this page (Shopping and Performance Max have no keywords or ad text to compare it with).")
    return None


def _preflight(check_id, c, groups=None):
    """A finding when the check cannot run (nothing to compare, the page unread or built by scripts), else None."""
    f = _no_groups(check_id, c, groups)
    if f:
        return f
    why = c.page_unread()
    return unknown(check_id, why) if why else None


def _names(rows, n=SHOWN):
    """The texts of the first `n` distinct keywords (the same keyword in two ad groups is named once)."""
    return list(dict.fromkeys(r["text"] for r in rows))[:n]


def _pct(x):
    return round(100 * x)


# ── The checks ──────────────────────────────────────────────────────────────

def _stats(c):
    groups = search_groups(c)
    return words.phrase_stats(page_keywords(c, groups), c.facts)


def check_keywords_page(c):
    f = _preflight("match_keywords_page", c)
    if f:
        return f
    if not (c.page.get("ok") or {}).get("keywords", True):
        return unknown("match_keywords_page", "the keywords could not be read")
    s = _stats(c)
    if not s["judged"]:
        return not_applicable("match_keywords_page", "The page's ad groups have no keyword with words to look for.")
    share = s["onPage"]
    said = "Keywords that took %d%% of the keyword spend are on the page (%s judged)." % (_pct(share), plural(s["judged"], "keyword"))
    if s["missing"]:
        said += " Missing: %s." % ", ".join('"%s"' % t for t in s["missing"][:3])
    res = "pass" if share >= KEYWORDS_OK else "warn" if share >= KEYWORDS_FAIR else "fail"
    return make("match_keywords_page", res, said, items=s["missing"] + s["partial"], count=len(s["missing"]) + len(s["partial"]))


def check_keywords_headline(c):
    f = _preflight("match_keywords_headline", c)
    if f:
        return f
    if not (c.page.get("ok") or {}).get("keywords", True):
        return unknown("match_keywords_headline", "the keywords could not be read")
    s = _stats(c)
    if not s["judged"]:
        return not_applicable("match_keywords_headline", "The page's ad groups have no keyword with words to look for.")
    share = s["inHeadline"]
    said = "Keywords that took %d%% of the keyword spend are in the page's title or headings." % _pct(share)
    res = "pass" if share >= HEADLINE_OK else "warn" if share >= HEADLINE_FAIR else "fail"
    return make("match_keywords_headline", res, said)


def check_terms(c):
    f = _preflight("match_terms", c)
    if f:
        return f
    if not (c.page.get("ok") or {}).get("terms", True):
        return unknown("match_terms", "the search terms could not be read")
    pid = c.page["id"]
    rows = [t for g in search_groups(c) if via_ads(g, pid) for t in g["terms"]]
    s = words.phrase_stats(rows, c.facts)
    if s["judged"] < TERMS_MIN:
        return not_applicable("match_terms", "Fewer than %d search terms with words to look for reached this page." % TERMS_MIN)
    share = s["onPage"]
    said = "Search terms that took %d%% of the search term spend are on the page (%s judged)." % (_pct(share), plural(s["judged"], "term"))
    if s["missing"]:
        said += " Missing: %s." % ", ".join('"%s"' % t for t in s["missing"][:3])
    res = "pass" if share >= TERMS_OK else "warn" if share >= TERMS_FAIR else "fail"
    return make("match_terms", res, said, items=s["missing"], count=len(s["missing"]))


def _group_echo(g, prom):
    heads = [h for ad in g["ads"] for h in ad["headlines"]]
    return words.best_echo(heads, prom)


def check_headline(c):
    f = _preflight("match_headline", c, ad_groups)
    if f:
        return f
    prom = words.vocab(words.prominent_text(c.facts))
    total = hit = 0.0
    misses = []
    judged = 0
    for g in ad_groups(c):
        share, head = _group_echo(g, prom)
        if not head:
            continue
        judged += 1
        w = max(float(g.get("cost") or 0.0), 0.01)
        total += w
        if share >= words.HEADLINE_ECHO:
            hit += w
        else:
            misses.append((w, '%s: "%s" (%d%% of its words are in the page\'s headings)' % (g["name"], head, _pct(share))))
    if not judged:
        return not_applicable("match_headline", "The page's ad groups have no responsive search ad headlines to compare." if "meta" not in c.platforms
                              else "The page's ad groups and ad sets have no ad headlines to compare.")
    share = hit / total
    misses.sort(key=lambda x: -x[0])
    if "meta" in c.platforms:
        said = "Ad groups and ad sets that took %d%% of the spend have a headline the page's title or headings echo (%d judged)." % (_pct(share), judged)
    else:
        said = "Ad groups that took %d%% of the spend have a headline the page's title or headings echo (%s judged)." % (_pct(share), plural(judged, "ad group"))
    res = "pass" if share >= ECHO_OK else "warn" if share >= ECHO_FAIR else "fail"
    return make("match_headline", res, said, items=[t for _w, t in misses], count=len(misses))


def check_offers(c):
    f = _preflight("match_offers", c, ad_groups)
    if f:
        return f
    pid = c.page["id"]
    texts = []
    for g in ad_groups(c):
        if via_ads(g, pid):
            for ad in g["ads"]:
                texts += ad["headlines"] + ad["descriptions"]
    offers = words.offers_in(texts)
    if not offers:
        return not_applicable("match_offers", "The ads promise no price, discount, free offer or round-the-clock service.")
    status = words.offer_status(offers, c.facts)
    missing = [o for o, ok in status if not ok]
    if not missing:
        return make("match_offers", "pass", "Every promise in the ads is on the page (%s)." % ", ".join(o["text"] for o in offers[:6]))
    hard = [o for o in missing if o["kind"] in ("price", "percent", "free")]
    said = "The ads promise %s that the page does not mention." % ", ".join(o["text"] for o in missing[:6])
    return make("match_offers", "fail" if hard else "warn", said, items=[o["text"] for o in missing], count=len(missing))


def check_h1(c):
    why = c.page_unread()
    if why:
        return unknown("match_h1", why)
    n, h1 = c.facts["h1Count"], c.facts["h1"]
    if n == 0 or not h1:
        return make("match_h1", "warn", "The page has no H1 (main headline).")
    if n > 2:
        return make("match_h1", "warn", "The page has %d H1 headings, so it has no single main message." % n)
    return make("match_h1", "pass", "The page's main headline is \"%s\"." % h1[0])


def _rating_check(check_id, key, label, c):
    f = _no_groups(check_id, c)
    if f:
        return f
    if not (c.page.get("ok") or {}).get("keywords", True):
        return unknown(check_id, "the keywords could not be read")
    rows = [k for k in page_keywords(c, search_groups(c)) if k.get(key)]
    if not rows:
        return unknown(check_id, "Google gave no %s rating for this page's keywords (it rates only keywords with enough impressions)" % label)
    total = sum(max(k["cost"], 0.01) for k in rows)
    below = [k for k in rows if k[key] == "below"]
    share = sum(max(k["cost"], 0.01) for k in below) / total
    said = "Google rates the %s below average for keywords that took %d%% of the rated keyword spend (%d of %d rated)." % (label, _pct(share), len(below), len(rows))
    if not below:
        return make(check_id, "pass", "Google rates the %s average or better for all %s rated." % (label, plural(len(rows), "keyword")))
    below.sort(key=lambda k: -k["cost"])
    if share < RATING_WARN:
        return make(check_id, "pass", said, items=_names(below), count=len(below))
    return make(check_id, "fail" if share >= RATING_FAIL else "warn", said, items=_names(below), count=len(below))


def _meta_rank_check(check_id, key, label, c):
    """Meta's own rating (quality or conversion rate) of the ads that send people to this page, by what each ad spent. Meta rates only ads with enough impressions."""
    ok = c.page.get("ok") or {}
    ads = [ad for g in meta_groups(c) for ad in g["ads"] if page_id_in(ad, c.page["id"])]
    if not ads:
        if ok.get("meta:ads") is False:
            return unknown(check_id, "the Meta ads could not be read")
        return not_applicable(check_id, "No Meta ad with words to compare sends people to this page.")
    if ok.get("meta:rankings") is False:
        return unknown(check_id, "Meta's ratings of the ads could not be read")
    rated = [ad for ad in ads if (ad.get("ranks") or {}).get(key)]
    if not rated:
        return unknown(check_id, "Meta gave no %s rating for the ads that send people to this page (it rates only ads with enough impressions)" % label)
    total = sum(max(ad["cost"], 0.01) for ad in rated)
    below = [ad for ad in rated if ad["ranks"][key] == "below"]
    share = sum(max(ad["cost"], 0.01) for ad in below) / total
    if not below:
        return make(check_id, "pass", "Meta rates the %s average or better for all %s rated." % (label, plural(len(rated), "ad")))
    below.sort(key=lambda ad: -ad["cost"])
    said = "Meta rates the %s below average for ads that took %d%% of the rated ad spend (%d of %d rated)." % (label, _pct(share), len(below), len(rated))
    names = list(dict.fromkeys(ad.get("name") or "an ad" for ad in below))[:SHOWN]
    if share < RATING_WARN:
        return make(check_id, "pass", said, items=names, count=len(below))
    return make(check_id, "fail" if share >= RATING_FAIL else "warn", said, items=names, count=len(below))


def page_id_in(ad, pid):
    return any(page_id(u) == pid for u in ad["urls"])


def check_meta_conversion(c):
    return _meta_rank_check("match_meta_conversion", "conversion", "conversion rate", c)


def check_meta_quality(c):
    return _meta_rank_check("match_meta_quality", "quality", "ad quality", c)


def check_lp_experience(c):
    return _rating_check("match_lp_experience", "landingPage", "landing page experience", c)


def check_ad_relevance(c):
    return _rating_check("match_ad_relevance", "adRelevance", "ad relevance", c)


def check_content(c):
    why = c.page_unread()
    if why:
        return unknown("match_content", why)
    n = c.facts["wordCount"]
    if n >= CONTENT_OK:
        return make("match_content", "pass", "The page has about %d words of text." % n)
    if n >= CONTENT_THIN:
        return make("match_content", "warn", "The page has only about %d words of text." % n)
    return make("match_content", "fail", "The page has only about %d words of text." % n)


CHECKS = [("match_meta_conversion", check_meta_conversion), ("match_meta_quality", check_meta_quality), ("match_keywords_page", check_keywords_page), ("match_keywords_headline", check_keywords_headline), ("match_terms", check_terms), ("match_headline", check_headline),
          ("match_offers", check_offers), ("match_h1", check_h1), ("match_lp_experience", check_lp_experience), ("match_ad_relevance", check_ad_relevance),
          ("match_content", check_content)]
