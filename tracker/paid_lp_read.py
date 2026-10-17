"""Paid Marketing Agents: Landing Page & Relevancy Check, what Google Ads says about each landing page.

Four read-only queries over the last 30 days (a fifth part, the account record, is the caller's):

  pages      what each landing page took: impressions, clicks, spend, conversions and their value, split by mobile and desktop, and Google's own
             mobile speed score where it gives one (landing_page_view)
  ads        each live ad: its ad group and campaign, the headlines and descriptions of a responsive search ad, and the addresses it sends people to
  keywords   each live keyword with its Quality Score and the three parts Google rates (expected click rate, ad relevance, landing page experience),
             and the address it sends to when it has one of its own (an address on a keyword overrides its ad's)
  terms      what people actually searched, per ad group

A page is its host and path, plus the few parameters that choose which page a site shows (see ROUTING): the same page reached with other parameters is one page. An ad group belongs to the pages its ads and its
keywords send people to. Shopping and Performance Max traffic reaches a page with no keyword or ad text to compare, so such a page has no ad group
and the match checks do not apply to it.

A part that could not be read is a warning, its `ok` flag is False and what depends on it is empty (the checks then say they could not check); the
pages part is the point, so its failure is raised. Rows are compacted so a saved read stays small: the busiest KEEP_PAGES pages and the ad groups that send people to them.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from . import paid_gads_segments as gads

WINDOW = "LAST_30_DAYS"
KEEP_PAGES = 40                                   # pages whose evidence is kept, busiest first
MAX_KEYWORDS = 40                                 # per ad group, the ones that spent most
MAX_TERMS = 30                                    # per ad group, the ones with most clicks
MAX_ADS = 6                                       # per ad group, the ones that spent most
MAX_HEADLINES = 15
MAX_DESCRIPTIONS = 4
WORKERS = 4

_FILTER = "AND campaign.status = ENABLED AND ad_group.status = ENABLED"

PAGES_GAQL = (
    "SELECT landing_page_view.unexpanded_final_url, segments.device, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, "
    "metrics.conversions_value, metrics.speed_score FROM landing_page_view WHERE segments.date DURING " + WINDOW + " AND metrics.cost_micros > 0 "
    "ORDER BY metrics.cost_micros DESC LIMIT 3000")
ADS_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.advertising_channel_type, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, "
    "ad_group_ad.ad.final_urls, ad_group_ad.ad.final_mobile_urls, ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
    "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions FROM ad_group_ad WHERE ad_group_ad.status = ENABLED " + _FILTER +
    " AND segments.date DURING " + WINDOW + " ORDER BY metrics.cost_micros DESC LIMIT 3000")
KEYWORDS_GAQL = (
    "SELECT ad_group.id, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
    "ad_group_criterion.final_urls, ad_group_criterion.quality_info.quality_score, ad_group_criterion.quality_info.creative_quality_score, "
    "ad_group_criterion.quality_info.post_click_quality_score, ad_group_criterion.quality_info.search_predicted_ctr, "
    "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions FROM keyword_view WHERE ad_group_criterion.status = ENABLED "
    "AND ad_group_criterion.negative = FALSE " + _FILTER + " AND segments.date DURING " + WINDOW + " AND metrics.impressions > 0 "
    "ORDER BY metrics.cost_micros DESC LIMIT 4000")
TERMS_GAQL = (
    "SELECT ad_group.id, search_term_view.search_term, metrics.clicks, metrics.cost_micros, metrics.conversions FROM search_term_view "
    "WHERE segments.date DURING " + WINDOW + " AND metrics.clicks > 0 ORDER BY metrics.clicks DESC LIMIT 3000")

READS = [("pages", "the landing pages and what they took", PAGES_GAQL), ("ads", "the ads", ADS_GAQL), ("keywords", "the keywords and their Quality Score", KEYWORDS_GAQL),
         ("terms", "the search terms", TERMS_GAQL)]

DEVICES = {"MOBILE": "mobile", "DESKTOP": "desktop", "TABLET": "tablet"}
RATINGS = {"BELOW_AVERAGE": "below", "AVERAGE": "average", "ABOVE_AVERAGE": "above"}


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _money(micros):
    return round(_f(micros) / 1e6, 2)


_TEMPLATE = re.compile(r"\{[^{}]*\}")
# Query parameters that choose WHICH page a site shows (a location, a product, a post), so the same path with another value is another page. Tracking tags and
# parameters that only vary one page (variant, currency, sort, page number, search words) are not here: they never make a second landing page.
ROUTING = frozenset({
    "id", "p", "pid", "page_id", "post", "post_id", "product", "product_id", "sku", "item", "item_id", "cat", "category", "service", "treatment", "topic", "offer",
    "lp", "landing", "landingpage", "slug", "route", "view", "location", "location_title", "loc", "city", "state", "store", "branch", "office", "clinic"})
_PORTS = {"http": "80", "https": "443"}


def templated(url):
    """True when the host or path of the address is a {placeholder} Google fills in at click time ({_domain}, {_page}). Such an address is not one page, so it cannot be fetched."""
    p = urlsplit(str(url or "").strip())
    return bool(_TEMPLATE.search(p.netloc + p.path))


def clean(url):
    """The address as a visitor would reach it: the fragment dropped and every query parameter whose name or value is a {placeholder} left out (Google fills those in at
    click time, and an invented value can send a site to an error page or its home page). A placeholder in the host or path is kept as written."""
    p = urlsplit(str(url or "").strip())
    query = "&".join(seg for seg in p.query.split("&") if seg and not _TEMPLATE.search(unquote(seg)))
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def _routing(query):
    """[(name, value)] of the parameters in `query` that choose the page, sorted, in lower case."""
    return sorted((k.lower(), v.strip().lower()) for k, v in parse_qsl(query, keep_blank_values=False) if k.lower() in ROUTING and v.strip())


def page_id(url):
    """What makes a landing page one page: its host and path ("a.test/x"), with the parameters that choose the page when it has them ("a.test/ols?location_title=quincy").
    The same page reached by http or https, with or without www or a trailing slash, or with other parameters, is one page."""
    p = urlsplit(clean(url))
    host = p.netloc.lower().rsplit("@", 1)[-1]
    if host.endswith(":" + _PORTS.get(p.scheme.lower(), "")):
        host = host.rsplit(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    path = unquote(p.path).rstrip("/") or "/"
    pairs = _routing(p.query)
    return host + path + (("?" + "&".join("%s=%s" % kv for kv in pairs)) if pairs else "")


def label(url):
    """The page as a person would write it: the host as the ad has it, the path and the parameters that choose the page ("www.a.test/ols?location_title=quincy")."""
    p = urlsplit(clean(url))
    pairs = _routing(p.query)
    return p.netloc.lower().rsplit("@", 1)[-1] + (unquote(p.path).rstrip("/") or "/") + (("?" + "&".join("%s=%s" % kv for kv in pairs)) if pairs else "")


def _valid(url):
    u = clean(url)
    parts = urlsplit(u)
    return u if parts.scheme in ("http", "https") and parts.netloc else ""


# ── The pages ───────────────────────────────────────────────────────────────

def parse_pages(rows):
    """[{id, name, url, templated, impressions, clicks, cost, conversions, value, speed, devices}] by cost, one per page. `url` is the address of the variant that
    spent most and `name` how a person would write it; `templated` is True when the address has a {placeholder} in its host or path; `devices` is {mobile|desktop|tablet: {clicks, cost, conversions}}; `speed` is Google's 1 to 10 mobile speed score (None when it gave none)."""
    out = {}
    for r in rows:
        raw = _get(r, "landingPageView", "unexpandedFinalUrl", default="")
        url = _valid(raw)
        if not url:
            continue
        m = r.get("metrics") or {}
        cost = _money(m.get("costMicros"))
        pid = page_id(url)
        p = out.setdefault(pid, {"id": pid, "name": label(url), "templated": templated(url), "url": url, "impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0, "value": 0.0, "speed": None, "devices": {}, "_top": -1.0,
                                 "_speedCost": -1.0})
        p["impressions"] += _i(m.get("impressions"))
        p["clicks"] += _i(m.get("clicks"))
        p["cost"] = round(p["cost"] + cost, 2)
        p["conversions"] = round(p["conversions"] + _f(m.get("conversions")), 4)
        p["value"] = round(p["value"] + _f(m.get("conversionsValue")), 2)
        dev = DEVICES.get(_get(r, "segments", "device", default=""), "other")
        d = p["devices"].setdefault(dev, {"clicks": 0, "cost": 0.0, "conversions": 0.0})
        d["clicks"] += _i(m.get("clicks"))
        d["cost"] = round(d["cost"] + cost, 2)
        d["conversions"] = round(d["conversions"] + _f(m.get("conversions")), 4)
        if cost > p["_top"]:                                # the address shown is the variant that spent most
            p["_top"], p["url"], p["name"] = cost, url, label(url)
        score = _i(m.get("speedScore"), None)
        if score is not None and 1 <= score <= 10 and cost > p["_speedCost"]:
            p["_speedCost"], p["speed"] = cost, score
    rows_out = sorted(out.values(), key=lambda p: (-p["cost"], p["id"]))
    for p in rows_out:
        p.pop("_top", None)
        p.pop("_speedCost", None)
    return rows_out


# ── The ads, keywords and search terms, by ad group ─────────────────────────

def _texts(assets, limit):
    out = []
    for a in assets or []:
        t = " ".join(str((a or {}).get("text") or "").split())
        if t and t not in out:
            out.append(t)
    return out[:limit]


def parse_ads(rows):
    """{ad group id: {id, name, campaign, campaignType, cost, clicks, conversions, ads: [{headlines, descriptions, urls, cost}], urls}}."""
    groups = {}
    for r in rows:
        gid = str(_get(r, "adGroup", "id", default=""))
        if not gid:
            continue
        m = r.get("metrics") or {}
        ad = _get(r, "adGroupAd", "ad", default={}) or {}
        urls = []
        for u in list(ad.get("finalUrls") or []) + list(ad.get("finalMobileUrls") or []):
            u = _valid(u)
            if u and u not in urls:
                urls.append(u)
        rsa = ad.get("responsiveSearchAd") or {}
        g = groups.setdefault(gid, {"id": gid, "name": _get(r, "adGroup", "name", default=""), "campaign": _get(r, "campaign", "name", default=""),
                                    "campaignType": _get(r, "campaign", "advertisingChannelType", default=""), "cost": 0.0, "clicks": 0, "conversions": 0.0, "ads": [], "keywords": [],
                                    "terms": []})
        cost = _money(m.get("costMicros"))
        g["cost"] = round(g["cost"] + cost, 2)
        g["clicks"] += _i(m.get("clicks"))
        g["conversions"] = round(g["conversions"] + _f(m.get("conversions")), 4)
        if len(g["ads"]) < MAX_ADS:
            g["ads"].append({"headlines": _texts(rsa.get("headlines"), MAX_HEADLINES), "descriptions": _texts(rsa.get("descriptions"), MAX_DESCRIPTIONS),
                             "urls": urls[:3], "cost": cost, "type": ad.get("type", "")})
    return groups


def _rating(v):
    return RATINGS.get(str(v or ""))


def parse_keywords(rows):
    """{ad group id: [{text, match, cost, clicks, impressions, conversions, qs, expectedCtr, adRelevance, landingPage, url}]}, the biggest spenders first."""
    out = {}
    for r in rows:
        gid = str(_get(r, "adGroup", "id", default=""))
        c = r.get("adGroupCriterion") or {}
        text = " ".join(str(_get(c, "keyword", "text", default="")).split())
        if not gid or not text:
            continue
        m = r.get("metrics") or {}
        q = c.get("qualityInfo") or {}
        own = next((u for u in (_valid(x) for x in c.get("finalUrls") or []) if u), "")
        out.setdefault(gid, []).append({
            "text": text, "match": str(_get(c, "keyword", "matchType", default="")).lower(), "cost": _money(m.get("costMicros")), "clicks": _i(m.get("clicks")),
            "impressions": _i(m.get("impressions")), "conversions": round(_f(m.get("conversions")), 4),
            "qs": _i(q.get("qualityScore"), None) if 1 <= _i(q.get("qualityScore"), 0) <= 10 else None,
            "expectedCtr": _rating(q.get("searchPredictedCtr")), "adRelevance": _rating(q.get("creativeQualityScore")), "landingPage": _rating(q.get("postClickQualityScore")),
            "url": own})
    for gid in out:
        out[gid].sort(key=lambda k: (-k["cost"], -k["clicks"], k["text"]))
    return out


def parse_terms(rows):
    """{ad group id: [{text, cost, clicks, conversions}]}, most clicks first. A term searched under several match types is summed."""
    out = {}
    for r in rows:
        gid = str(_get(r, "adGroup", "id", default=""))
        text = " ".join(str(_get(r, "searchTermView", "searchTerm", default="")).split())
        if not gid or not text:
            continue
        m = r.get("metrics") or {}
        by = out.setdefault(gid, {})
        t = by.setdefault(text.lower(), {"text": text, "cost": 0.0, "clicks": 0, "conversions": 0.0})
        t["cost"] = round(t["cost"] + _money(m.get("costMicros")), 2)
        t["clicks"] += _i(m.get("clicks"))
        t["conversions"] = round(t["conversions"] + _f(m.get("conversions")), 4)
    return {gid: sorted(by.values(), key=lambda t: (-t["clicks"], -t["cost"], t["text"])) for gid, by in out.items()}


# ── Joining ─────────────────────────────────────────────────────────────────

def join(page_rows, groups, keywords, terms, keep=KEEP_PAGES):
    """Attaches each ad group's keywords and search terms, works out which pages it sends people to, and keeps only the busiest `keep` pages and the
    ad groups that reach them. Returns (pages with `adGroups` [ids], ad groups by id). A keyword with an address of its own is counted on that page, not
    on its ad group's other pages."""
    kept = page_rows[:keep]
    keep_ids = {p["id"] for p in kept}
    for g in groups.values():
        g["keywords"] = (keywords.get(g["id"]) or [])[:MAX_KEYWORDS]
        g["terms"] = (terms.get(g["id"]) or [])[:MAX_TERMS]
        reach = {}
        for ad in g["ads"]:
            for u in ad["urls"]:
                pid = page_id(u)
                reach[pid] = round(reach.get(pid, 0.0) + ad["cost"], 2)
        for k in g["keywords"]:
            if k["url"]:
                reach.setdefault(page_id(k["url"]), 0.0)
        g["pages"] = reach
    used = {}
    for p in kept:
        ids = [gid for gid, g in groups.items() if p["id"] in g["pages"]]
        ids.sort(key=lambda gid: (-groups[gid]["pages"][p["id"]], -groups[gid]["cost"], gid))
        p["adGroups"] = ids
        for gid in ids:
            used[gid] = groups[gid]
    for g in used.values():
        g["pages"] = {pid: c for pid, c in g["pages"].items() if pid in keep_ids}
    return kept, used


# ── The whole read ──────────────────────────────────────────────────────────

def read_account(account_id, *, search_fn=None, workers=WORKERS, keep=KEEP_PAGES):
    """{pages, pageCount, adGroups, ok, warnings}. The landing pages are the point, so a failure reading them is raised; every other part is read on its
    own and a failure is a warning. Read only."""
    search = search_fn or gads.search
    rows, errors = {}, {}

    def fetch(read):
        key, _what, gaql = read
        try:
            return key, search(account_id, gaql), None
        except gads.GadsError as e:
            return key, None, e
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for key, got, err in ex.map(fetch, READS):
            if err is not None:
                errors[key] = err
            else:
                rows[key] = got
    if "pages" in errors:
        raise errors["pages"]
    what = {key: w for key, w, _q in READS}
    warnings = ["%s could not be read (%s)." % (what[k][0].upper() + what[k][1:], e) for k, e in errors.items()]
    ok = {key: key in rows for key, _w, _q in READS}
    page_rows = parse_pages(rows["pages"])
    groups = parse_ads(rows["ads"]) if "ads" in rows else {}
    kept, used = join(page_rows, groups, parse_keywords(rows["keywords"]) if "keywords" in rows else {},
                      parse_terms(rows["terms"]) if "terms" in rows else {}, keep=keep)
    return {"pages": kept, "pageCount": len(page_rows), "pageCost": round(sum(p["cost"] for p in page_rows), 2), "adGroups": used, "ok": ok, "warnings": warnings}
