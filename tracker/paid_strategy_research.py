"""Paid Marketing Agents: Strategy & Campaign Book, what is read before anything is written.

Every card at the top of the page is one piece of research here. Each returns the same shape,

    {"key", "state": ok | partial | failed | skipped, "note": words for the page, "took": seconds, "usd": cost beyond the model write-up,
     "data": {...}}

and never raises: a piece that cannot be read says so in its note and the strategy is written from the rest, naming what it did
not have. The functions take what they read through (fetcher, search, ask, post...) so tests run on fakes and nothing here needs a
network to be checked.

  website       the client's own pages, read politely (robots.txt is honoured, a few at a time, public addresses only)
  competitors   found with a web search when none are named, then each one's pages read the same way
  history       the Google Ads account: what was spent, what converted, which terms were wasted, devices, places, live ads
  keywords      Google Keyword Planner ideas with monthly searches, competition and bid ranges
  seasonality   the account's own yearly shape, from the Seasonality Analysis agent
  social        Social Media Intelligence: the latest finished run for the company, or a new one
  market        a web search for how people choose in this market and the advertising rules it must follow
"""
from __future__ import annotations

import codecs
import datetime as dt
import json
import logging
import re
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import paid_site_tech as site_tech, paid_strategy as ps

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; Position2-Strategy/1.0; +https://position2.com)"
ROBOT_NAME = "Position2-Strategy"
MAX_BYTES = 700_000
TIMEOUT_S = 12
PAGE_DEADLINE_S = 25         # one page, however slowly a site drips it out
CRAWL_DEADLINE_S = 90        # one site: no new page is asked for after this
CLIENT_PAGES = 14
RIVAL_PAGES = 4
CLIENT_TEXT = 2600
RIVAL_TEXT = 1500
MAX_SITEMAP_URLS = 400
MODEL = "claude-sonnet-5-5"
SEARCH_DEADLINE_S = 420.0      # a web search that has not answered in seven minutes is given up on (the shared default is forty)

# Where a page is worth reading first, by what its address or its link text says.
PRIORITY = [
    (("service", "treatment", "procedure", "solution", "product", "specialt", "therap", "program", "offering", "capabilit", "industr", "care"), 10),
    (("pric", "cost", "plan", "financ", "insur", "coverage", "special", "promo", "deal", "coupon", "offer", "rate", "quote"), 9),
    (("about", "team", "doctor", "provider", "physician", "story", "why", "mission", "who"), 7),
    (("location", "office", "clinic", "area", "contact", "visit", "directions", "near"), 6),
    (("review", "testimonial", "reputation", "result", "case", "gallery", "before", "success", "award"), 6),
    (("faq", "question", "how", "process", "started"), 4),
    (("blog", "news", "article", "resource", "press", "learn"), 1),
]
SKIP_PATH = re.compile(r"login|log-in|signin|sign-in|account|cart|checkout|privacy|terms|cookie|careers|jobs|wp-admin|wp-json|/feed|/tag/|/author/|"
                       r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|mp4|mov|css|js|xml|ico)(\?|$)|^mailto:|^tel:|^javascript:|^#", re.I)
CTA_WORDS = re.compile(r"^(book|schedule|call|request|get|start|contact|apply|buy|shop|order|sign up|signup|join|reserve|claim|talk|speak|"
                       r"find|try|download|see|view|learn|check|compare|ask|text|chat)\b", re.I)
OFFER_RE = re.compile(r"(\$\s?\d[\d,.]*|\b\d{1,3}\s?%|\bfree\b|\bcomplimentary\b|\bno cost\b|\bfinancing\b|\b\d+\s?months?\b|\bsave\b|\bdiscount\b|"
                      r"\bspecial\b|\bcoupon\b|\b0%\b|\bsame[- ]day\b|\bguarantee)", re.I)
NOT_COMPETITORS = {
    "yelp.com", "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "wikipedia.org", "healthgrades.com", "zocdoc.com",
    "yellowpages.com", "bbb.org", "angi.com", "angieslist.com", "indeed.com", "glassdoor.com", "reddit.com", "tiktok.com", "twitter.com",
    "x.com", "pinterest.com", "mapquest.com", "google.com", "maps.google.com", "tripadvisor.com", "thumbtack.com", "nextdoor.com",
    "webmd.com", "mayoclinic.org", "nih.gov", "forbes.com", "nytimes.com", "amazon.com", "ebay.com", "walmart.com", "trustpilot.com",
    "clutch.co", "g2.com", "capterra.com", "crunchbase.com", "apple.com", "medium.com", "quora.com", "bing.com", "yahoo.com",
}


def piece(key, state, note="", data=None, took=0.0, usd=0.0, spend=None):
    return {"key": key, "state": state, "note": note, "took": round(took, 1), "usd": round(usd, 4), "data": data or {}, "spend": spend or {}}


def skipped(key, why):
    return piece(key, "skipped", why)


def failed(key, why, took=0.0):
    return piece(key, "failed", why, took=took)


def _clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def decode(raw, content_type=""):
    """A page's bytes as text: the charset the server names, else the one the page names, else UTF-8. (requests would call a page with no charset ISO-8859-1,
    which turns every accent into mojibake, and a charset nobody has heard of raises.)"""
    named = re.search(r"charset=([\w.:-]+)", content_type or "", re.I)
    guess = [named.group(1)] if named else []
    if not named:
        meta = re.search(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", raw[:4096], re.I)
        if meta:
            guess.append(meta.group(1).decode("ascii", "ignore"))
    for enc in guess + ["utf-8"]:
        try:
            codecs.lookup(enc)
            return raw.decode(enc, errors="replace")
        except (LookupError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _bounded(fn, seconds):
    """fn() or a TimeoutError after `seconds`: a site that sends a byte a minute cannot hold a build."""
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(fn).result(timeout=seconds)
    except FuturesTimeout:
        raise TimeoutError("took longer than %d seconds" % seconds)
    finally:
        ex.shutdown(wait=False)


def _fetch(fetcher, url):
    """(status, final url, headers, html, bytes read) for one page."""
    def once():
        resp = fetcher(url, timeout=TIMEOUT_S, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        try:
            raw = resp.raw.read(MAX_BYTES + 1, decode_content=True) if hasattr(resp.raw, "read") else resp.content
            status, final, hdrs = resp.status_code, resp.url, dict(resp.headers)
        finally:
            resp.close()
        return status, final, hdrs, raw[:MAX_BYTES], len(raw)
    status, final, hdrs, raw, size = _bounded(once, PAGE_DEADLINE_S)
    return status, final, hdrs, decode(raw, hdrs.get("Content-Type") or hdrs.get("content-type") or ""), size


# ── One page ────────────────────────────────────────────────────────────────

def read_page(html, final_url, text_limit):
    """What one page says, boiled down: title, headings, the words, how it asks for the sale, what it offers, how it is rated."""
    from .paid_qa_web import PHONE, _Page
    p = _Page()
    try:
        p.feed(html)
        p.close()
    except Exception as e:                                  # noqa: BLE001 (broken HTML still has facts in it)
        log.info("strategy research: html parse stopped early: %s", e)
    text = " ".join(" ".join(p.text).split())
    ctas, seen = [], set()
    for _href, label in p.links:
        label = " ".join(label.split())
        if 2 <= len(label) <= 40 and CTA_WORDS.match(label) and label.lower() not in seen:
            seen.add(label.lower())
            ctas.append(label)
    offers, seen_offer = [], set()
    for m in OFFER_RE.finditer(text):
        lo, hi = max(0, m.start() - 50), min(len(text), m.end() + 70)
        snippet = text[lo:hi].strip()
        key = snippet[:40].lower()
        if key not in seen_offer:
            seen_offer.add(key)
            offers.append(snippet)
        if len(offers) >= 6:
            break
    blob = " ".join(p.inline)
    rating = re.search(r'"ratingValue"\s*:\s*"?([\d.]+)', blob)
    count = re.search(r'"(?:reviewCount|ratingCount)"\s*:\s*"?(\d+)', blob)
    tel = sorted({h.split(":", 1)[1].split("?")[0].strip() for h, _ in p.links if h.lower().startswith("tel:")})
    return {"url": final_url, "title": _clip(p.title, 160), "description": _clip(p.meta.get("description") or p.meta.get("og:description"), 300),
            "h1": [_clip(h, 140) for h in p.h1[:3]], "h2": [_clip(h, 120) for h in p.h2[:8]], "text": text[:text_limit], "chars": len(text),
            "ctas": ctas[:8], "offers": offers, "phones": (tel + sorted(set(PHONE.findall(text))))[:4],
            "forms": [{"fields": f["fields"][:8]} for f in p.forms[:3]],
            "rating": {"value": float(rating.group(1)), "count": int(count.group(1))} if rating and count else None,
            "links": [(h, " ".join(t.split())[:60]) for h, t in p.links[:300]], "scripts": len(p.scripts), "tech": site_tech.detect(p, final_url)}


def same_site(host, other):
    a, b = host.removeprefix("www."), (other or "").removeprefix("www.")
    return bool(a) and (a == b or b.endswith("." + a))


def rank_links(links, base_url, host, taken=()):
    """[(score, absolute url)] best first: same site only, pages worth reading before the rest, one address once."""
    seen, out = set(taken), []
    for href, label in links:
        href = (href or "").strip()
        if not href or SKIP_PATH.search(href):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https") or not same_site(host, parts.hostname):
            continue
        clean = urlunsplit((parts.scheme, parts.netloc, re.sub(r"/{2,}", "/", parts.path or "/"), "", ""))
        key = clean.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        words = re.findall(r"[a-z0-9]+", ("%s %s" % (parts.path, label)).lower())           # whole words (and their endings): "corporate" is not "rate"
        score = max([w for stems, w in PRIORITY if any(word.startswith(stem) for word in words for stem in stems)] or [2])
        depth = len([s for s in parts.path.split("/") if s])
        out.append((score - 0.3 * max(0, depth - 2), clean))
    out.sort(key=lambda x: -x[0])
    return out


def pick_pages(ranked, count, per_section=3):
    """The best `count`, but no more than `per_section` from one top-level folder so a long blog cannot take every place."""
    picked, by = [], {}
    for _score, url in ranked:
        sec = (urlsplit(url).path.strip("/").split("/") or [""])[0]
        if by.get(sec, 0) >= per_section:
            continue
        by[sec] = by.get(sec, 0) + 1
        picked.append(url)
        if len(picked) >= count:
            break
    return picked


def _sitemap_urls(fetcher, root, robots_sitemaps):
    """Addresses from the site's sitemap (one level of index followed). [] when there is none."""
    urls, todo, seen = [], list(robots_sitemaps or []) or [urljoin(root, "/sitemap.xml")], set()
    while todo and len(urls) < MAX_SITEMAP_URLS and len(seen) < 4:
        sm = todo.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            status, _final, _h, body, _n = _fetch(fetcher, sm)
        except Exception:                                   # noqa: BLE001 (no sitemap is normal)
            continue
        if status != 200:
            continue
        locs = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\s]+?)\s*(?:\]\]>)?\s*</loc>", body)
        if "<sitemapindex" in body[:2000]:
            todo = locs[:3] + todo
        else:
            urls.extend(locs)
    return urls[:MAX_SITEMAP_URLS]


def crawl(url, *, fetcher=None, max_pages=CLIENT_PAGES, text_limit=CLIENT_TEXT, workers=4):
    """Read a site: its home page, then the pages most worth reading. Returns
    {"ok", "reason", "site": {...}, "pages": [...]}; a site that cannot be read says why instead of raising."""
    if fetcher is None:
        from .event_intel_http import public_get as fetcher
    started = time.monotonic()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    out = {"ok": False, "reason": "", "site": {"url": url, "host": host.removeprefix("www."), "tried": 0, "blocked": [], "errors": []}, "pages": []}
    robots = urllib.robotparser.RobotFileParser()
    robots_ok = False
    try:
        status, _final, _h, body, _n = _fetch(fetcher, "%s://%s/robots.txt" % (parts.scheme, parts.netloc))
        if status == 200 and "<html" not in body[:400].lower():
            robots.parse(body.splitlines())
            robots_ok = True
    except Exception:                                       # noqa: BLE001 (no robots.txt means nothing is asked of us)
        pass

    def allowed(u):
        return not robots_ok or robots.can_fetch(ROBOT_NAME, u)           # a rule written for us wins over the one for everybody; with none, everybody's applies

    def get(u):
        out["site"]["tried"] += 1
        if time.monotonic() - started > CRAWL_DEADLINE_S and out["site"]["tried"] > 1:
            out["site"]["errors"].append("%s was not asked for: this site took too long" % _clip(u, 80))
            return None
        if not allowed(u):
            out["site"]["blocked"].append(u)
            return None
        try:
            status, final, hdrs, html, _n = _fetch(fetcher, u)
        except Exception as e:                              # noqa: BLE001 (private address, DNS, timeout, TLS)
            out["site"]["errors"].append("%s: %s" % (_clip(u, 80), "%s %s" % (type(e).__name__, _clip(e, 100))))
            return None
        if status >= 400:
            out["site"]["errors"].append("%s answered %d" % (_clip(u, 80), status))
            return None
        if "html" not in (hdrs.get("Content-Type") or hdrs.get("content-type") or "text/html").lower():
            return None
        pg = read_page(html, final, text_limit)
        pg["status"] = status
        return pg

    home = get(url)
    if home is None:
        why = "; ".join(out["site"]["errors"][:2]) or ("robots.txt asks crawlers to stay out" if out["site"]["blocked"] else "no page came back")
        out["reason"] = "The home page could not be read: %s." % why
        return out
    final_host = (urlsplit(home["url"]).hostname or host).lower()
    site_urls = _sitemap_urls(fetcher, "%s://%s/" % (parts.scheme, final_host), robots.site_maps() if robots_ok and hasattr(robots, "site_maps") else None)
    ranked = rank_links(home["links"], home["url"], final_host, taken=[home["url"].rstrip("/").lower()])
    seen = {u for _s, u in ranked}
    ranked += [r for r in rank_links([(u, "") for u in site_urls], home["url"], final_host, taken=[home["url"].rstrip("/").lower()]) if r[1] not in seen]
    ranked.sort(key=lambda x: -x[0])
    chosen = pick_pages(ranked, max(0, max_pages - 1))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(chosen) or 1))) as ex:
        rest = [pg for pg in ex.map(get, chosen) if pg]
    pages = [home] + rest
    for pg in pages:
        pg.pop("links", None)
    out["pages"] = pages
    out["site"].update(sitemapUrls=len(site_urls), robots=robots_ok, thin=home["chars"] < 300 and home["scripts"] > 5, took=round(time.monotonic() - started, 1))
    out["ok"] = True
    return out


def site_facts(crawled):
    """Facts across a site's pages: phones, how it asks for the sale, offers, rating, forms."""
    pages = crawled.get("pages") or []
    ctas, offers, phones = [], [], []
    for pg in pages:
        for c in pg.get("ctas") or []:
            if c.lower() not in [x.lower() for x in ctas]:
                ctas.append(c)
        for o in pg.get("offers") or []:
            if o not in offers:
                offers.append(o)
        for ph in pg.get("phones") or []:
            if ph not in phones:
                phones.append(ph)
    rating = next((pg["rating"] for pg in pages if pg.get("rating")), None)
    return {"pagesRead": len(pages), "ctas": ctas[:10], "offers": offers[:10], "phones": phones[:4], "rating": rating,
            "forms": sum(len(pg.get("forms") or []) for pg in pages), "thin": bool((crawled.get("site") or {}).get("thin"))}


def read_website(inputs, *, fetcher=None):
    started = time.monotonic()
    url = inputs.get("website")
    if not url:
        return skipped("website", "No website was given.")
    c = crawl(url, fetcher=fetcher)
    took = time.monotonic() - started
    if not c["ok"]:
        return failed("website", c["reason"], took)
    facts = site_facts(c)
    note = "Read %d page%s of %s." % (facts["pagesRead"], "" if facts["pagesRead"] == 1 else "s", c["site"]["host"])
    if facts["thin"]:
        note += " The site draws most of its words with scripts, so little text was readable."
    if c["site"]["blocked"]:
        note += " %d address%s left alone because robots.txt asks crawlers to stay out." % (len(c["site"]["blocked"]), "" if len(c["site"]["blocked"]) == 1 else "es")
    state = "partial" if c["site"]["errors"] and facts["pagesRead"] < 3 else "ok"
    return piece("website", state, note, {"site": c["site"], "pages": c["pages"], "facts": facts}, took)


# ── Competitors ─────────────────────────────────────────────────────────────

def registrable(host):
    """example.co.uk and www.example.com both reduce to a stable name for 'is this the same business'."""
    host = (host or "").lower().removeprefix("www.")
    bits = host.split(".")
    if len(bits) >= 3 and bits[-2] in ("co", "com", "org", "net", "gov", "ac") and len(bits[-1]) == 2:
        return ".".join(bits[-3:])
    return ".".join(bits[-2:]) if len(bits) >= 2 else host


def usable_competitor(url, client_host):
    """True when a competitor web address is a business site we may read: not the client, not a directory or a social network."""
    host = ps.host_of(url)
    if not host:
        return False
    reg = registrable(host)
    return reg != registrable(client_host) and reg not in NOT_COMPETITORS and host not in NOT_COMPETITORS


FIND_SYSTEM = """You find the direct competitors of one business, for a marketing agency, using web search. A direct competitor sells the same kind of thing to the same kind of customer in the same market. Reply with ONLY a JSON object, no prose before or after:
{"competitors":[{"name": str, "url": "https://their own website", "why": "one short sentence on why they compete"}]}
Rules: their own website only, never a directory, review site, marketplace, social network or news article. Not the client itself. Prefer businesses that advertise on Google for the same services. Between 4 and 8. Do not use em dashes."""


def find_competitors(inputs, *, ask=None, want=5):
    """Competitors from a web search. Returns (items, spend, note); items are {'name', 'url', 'why'}."""
    from . import claude_websearch as cw
    ask = ask or cw.ask
    where = inputs.get("locations") or "the same markets"
    user = ("Client: %s\nWebsite: %s\nIndustry: %s\nWhat they sell: %s\nWhere they serve customers: %s\nBrief: %s\n\nFind their direct competitors."
            % (inputs.get("name"), inputs.get("website") or "not given", inputs.get("industry") or "not given",
               inputs.get("services") or "not given", where, _clip(inputs.get("brief"), 700) or "not given"))
    res = ask(FIND_SYSTEM, user, max_uses=5, max_tokens=3500, model=MODEL, temperature=0.0, deadline=SEARCH_DEADLINE_S)
    spend = cw.spend_of(res)
    if (res or {}).get("error"):
        err = res["error"]
        return [], spend, "The web search for competitors did not complete (%s)." % (err.get("kind") if isinstance(err, dict) else err)
    found = cw.extract_json((res or {}).get("text") or "", require="competitors")
    if not found:
        return [], spend, "The web search for competitors came back in a form that could not be read."
    items, seen = [], set()
    for row in found.get("competitors") or []:
        if not isinstance(row, dict):
            continue
        try:
            url = ps.clean_url(row.get("url"), "competitor website")
        except ps.BadPayload:
            continue
        reg = registrable(ps.host_of(url))
        if not usable_competitor(url, ps.host_of(inputs.get("website"))) or reg in seen:
            continue
        seen.add(reg)
        items.append({"name": cw.strip_em_dash(_clip(row.get("name"), 80)) or ps.host_of(url), "url": url,
                      "why": cw.strip_em_dash(_clip(row.get("why"), 200))})
        if len(items) >= want:
            break
    return items, spend, ("" if items else "The web search did not find competitor websites to read.")


FIND_SITES_SYSTEM = """You find the official website of each business named, using web search. Reply with ONLY a JSON object, no prose before or after:
{"competitors":[{"name": str, "url": "https://their own website", "why": "one short sentence on what they are"}]}
Rules: the business's own website only, never a directory, review site, social network or news article. Leave a business out when you cannot find its own site. Do not use em dashes."""


def find_named(inputs, names, *, ask=None):
    """The websites of competitors that were named without an address. Returns (items, spend, note)."""
    from . import claude_websearch as cw
    ask = ask or cw.ask
    user = "Client: %s (%s). Find the websites of these businesses, which compete with it near %s:\n- %s" % (
        inputs.get("name"), inputs.get("industry") or inputs.get("website") or "", inputs.get("locations") or "its market", "\n- ".join(names[:6]))
    res = ask(FIND_SITES_SYSTEM, user, max_uses=4, max_tokens=2500, model=MODEL, temperature=0.0, deadline=SEARCH_DEADLINE_S)
    spend = cw.spend_of(res)
    found = None if (res or {}).get("error") else cw.extract_json((res or {}).get("text") or "", require="competitors")
    if not found:
        return [], spend, "The websites of %s could not be found." % ", ".join(names[:3])
    items = []
    for row in found.get("competitors") or []:
        try:
            url = ps.clean_url((row or {}).get("url"), "competitor website")
        except ps.BadPayload:
            continue
        items.append({"name": cw.strip_em_dash(_clip(row.get("name"), 80)) or ps.host_of(url), "url": url, "why": cw.strip_em_dash(_clip(row.get("why"), 200))})
    return items, spend, ("" if items else "The websites of %s could not be found." % ", ".join(names[:3]))


def read_competitors(inputs, *, fetcher=None, ask=None, want=5):
    """The competitors' websites: the ones named, or found by a web search, each read for a few pages."""
    from . import claude_websearch as cw
    started = time.monotonic()
    named = list(inputs.get("competitors") or [])
    spend, notes, found = {}, [], []
    targets = [{"name": c["name"], "url": c["url"], "why": "named by you", "via": "named"} for c in named if c.get("url")]
    if not named:
        found, spend, why = find_competitors(inputs, ask=ask, want=want)
        notes += [why] if why else []
        targets += [dict(c, via="web search") for c in found]
    nameless = [c["name"] for c in named if not c.get("url")]
    if nameless:
        more, more_spend, why = find_named(inputs, nameless, ask=ask)
        spend = cw.spend_sum(spend, more_spend)
        notes += [why] if why else []
        targets += [dict(c, via="web search") for c in more]
    client_host = ps.host_of(inputs.get("website"))
    seen, uniq = set(), []
    for t in targets:
        reg = registrable(ps.host_of(t["url"]))
        if reg in seen or not usable_competitor(t["url"], client_host):
            continue
        seen.add(reg)
        uniq.append(t)
    uniq = uniq[:max(want, len(named))]
    if not uniq:
        return piece("competitors", "failed", " ".join(notes) or "No competitor websites to read.", took=time.monotonic() - started,
                     usd=cw.spend_usd(spend), spend=spend)

    def one(target):
        crawled = crawl(target["url"], fetcher=fetcher, max_pages=RIVAL_PAGES, text_limit=RIVAL_TEXT, workers=3)
        row = {"name": target["name"], "url": target["url"], "why": target.get("why") or "", "via": target.get("via"), "ok": crawled["ok"],
               "reason": crawled["reason"], "pages": crawled["pages"], "facts": site_facts(crawled) if crawled["ok"] else {}}
        return row

    with ThreadPoolExecutor(max_workers=3) as ex:
        rows = list(ex.map(one, uniq))
    good = [r for r in rows if r["ok"]]
    for r in rows:
        if not r["ok"]:
            notes.append("%s could not be read: %s" % (r["name"], _clip(r["reason"], 120)))
    state = "ok" if len(good) == len(rows) else ("partial" if good else "failed")
    note = "Read %d of %d competitor website%s." % (len(good), len(rows), "" if len(rows) == 1 else "s")
    return piece("competitors", state, " ".join([note] + notes), {"competitors": rows, "found": bool(found)}, time.monotonic() - started,
                 cw.spend_usd(spend), spend)


# ── The Google Ads account ──────────────────────────────────────────────────

HISTORY_DAYS = 90
LEVELS = {"SEARCH": "Search", "PERFORMANCE_MAX": "Performance Max", "DEMAND_GEN": "Demand Gen", "DISPLAY": "Display", "VIDEO": "Video",
          "SHOPPING": "Shopping", "SMART": "Smart", "MULTI_CHANNEL": "App", "LOCAL_SERVICES": "Local Services", "LOCAL": "Local"}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _money(micros):
    return round(_f(micros) / 1e6, 2)


def _rate(num, den, nd=2):
    return round(num / den, nd) if den else None


def window(today=None, days=HISTORY_DAYS):
    end = (today or dt.date.today()) - dt.timedelta(days=1)
    return (end - dt.timedelta(days=days - 1)).isoformat(), end.isoformat()


def history_queries(start, end):
    between = "segments.date BETWEEN '%s' AND '%s'" % (start, end)
    return {
        "campaigns": ("SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, campaign.bidding_strategy_type, "
                      "campaign_budget.amount_micros, campaign.start_date_time, metrics.impressions, metrics.clicks, metrics.cost_micros, "
                      "metrics.conversions, metrics.conversions_value FROM campaign WHERE campaign.status != 'REMOVED' AND " + between),
        "actions": ("SELECT conversion_action.name, conversion_action.type, conversion_action.category, conversion_action.primary_for_goal "
                    "FROM conversion_action WHERE conversion_action.status = 'ENABLED'"),
        "keywords": ("SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, campaign.name, ad_group.name, "
                     "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, ad_group_criterion.quality_info.quality_score "
                     "FROM keyword_view WHERE %s AND ad_group_criterion.status = 'ENABLED' AND metrics.impressions > 0 "
                     "ORDER BY metrics.cost_micros DESC LIMIT 400" % between),
        "devices": ("SELECT segments.device, metrics.clicks, metrics.cost_micros, metrics.conversions FROM campaign WHERE %s" % between),
        "regions": ("SELECT segments.geo_target_region, metrics.clicks, metrics.cost_micros, metrics.conversions FROM user_location_view "
                    "WHERE %s ORDER BY metrics.cost_micros DESC LIMIT 40" % between),
        "ads": ("SELECT ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, ad_group_ad.ad.final_urls, "
                "campaign.name, metrics.clicks, metrics.conversions FROM ad_group_ad WHERE ad_group_ad.status = 'ENABLED' AND campaign.status = 'ENABLED' "
                "AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD' AND %s ORDER BY metrics.clicks DESC LIMIT 6" % between),
    }


def _parse_campaigns(rows):
    by = {}
    for r in rows:
        c, b, m = r.get("campaign") or {}, r.get("campaignBudget") or {}, r.get("metrics") or {}
        cid = str(c.get("id") or "")
        if not cid:
            continue
        row = by.setdefault(cid, {"id": cid, "name": c.get("name") or "", "status": c.get("status") or "", "type": LEVELS.get(c.get("advertisingChannelType"), c.get("advertisingChannelType") or ""),
                                  "bid": (c.get("biddingStrategyType") or "").replace("_", " ").title(), "daily": _money(b.get("amountMicros")),
                                  "started": (c.get("startDateTime") or "")[:10], "impr": 0, "clicks": 0, "cost": 0.0, "conv": 0.0, "value": 0.0})
        row["impr"] += int(_f(m.get("impressions")))
        row["clicks"] += int(_f(m.get("clicks")))
        row["cost"] += _money(m.get("costMicros"))
        row["conv"] += _f(m.get("conversions"))
        row["value"] += _f(m.get("conversionsValue"))
    out = []
    for row in by.values():
        row.update(cost=round(row["cost"], 2), conv=round(row["conv"], 1), value=round(row["value"], 2), cpa=_rate(row["cost"], row["conv"]),
                   cvr=_rate(row["conv"] * 100, row["clicks"], 1))
        out.append(row)
    out.sort(key=lambda r: (-r["cost"], r["name"]))
    return out


def _parse_keywords(rows):
    out = []
    for r in rows:
        k, m = (r.get("adGroupCriterion") or {}), (r.get("metrics") or {})
        kw = k.get("keyword") or {}
        if not kw.get("text"):
            continue
        out.append({"text": kw["text"], "match": (kw.get("matchType") or "").title(), "campaign": (r.get("campaign") or {}).get("name") or "",
                    "adGroup": (r.get("adGroup") or {}).get("name") or "", "impr": int(_f(m.get("impressions"))), "clicks": int(_f(m.get("clicks"))),
                    "cost": _money(m.get("costMicros")), "conv": round(_f(m.get("conversions")), 1),
                    "qs": (k.get("qualityInfo") or {}).get("qualityScore")})
    return out


def _parse_devices(rows):
    by = {}
    for r in rows:
        d, m = (r.get("segments") or {}).get("device") or "OTHER", r.get("metrics") or {}
        row = by.setdefault(d, {"device": d.title(), "clicks": 0, "cost": 0.0, "conv": 0.0})
        row["clicks"] += int(_f(m.get("clicks")))
        row["cost"] += _money(m.get("costMicros"))
        row["conv"] += _f(m.get("conversions"))
    out = [dict(r, cost=round(r["cost"], 2), conv=round(r["conv"], 1), cpa=_rate(r["cost"], r["conv"])) for r in by.values() if r["clicks"] or r["cost"]]
    out.sort(key=lambda r: -r["cost"])
    return out


def _parse_regions(rows, names):
    by = {}
    for r in rows:
        res, m = (r.get("segments") or {}).get("geoTargetRegion"), r.get("metrics") or {}
        if not res:
            continue
        row = by.setdefault(res, {"resource": res, "clicks": 0, "cost": 0.0, "conv": 0.0})
        row["clicks"] += int(_f(m.get("clicks")))
        row["cost"] += _money(m.get("costMicros"))
        row["conv"] += _f(m.get("conversions"))
    out = []
    for res, row in by.items():
        nm = names.get(res) or {}
        out.append({"name": nm.get("canonical") or nm.get("name") or res, "clicks": row["clicks"], "cost": round(row["cost"], 2),
                    "conv": round(row["conv"], 1), "cpa": _rate(row["cost"], row["conv"])})
    out.sort(key=lambda r: -r["cost"])
    return out[:15]


def _parse_ads(rows):
    out = []
    for r in rows:
        ad = (r.get("adGroupAd") or {}).get("ad") or {}
        rsa = ad.get("responsiveSearchAd") or {}
        heads = [h.get("text") for h in rsa.get("headlines") or [] if h.get("text")]
        if not heads:
            continue
        m = r.get("metrics") or {}
        out.append({"campaign": (r.get("campaign") or {}).get("name") or "", "headlines": heads[:15],
                    "descriptions": [d.get("text") for d in rsa.get("descriptions") or [] if d.get("text")][:4],
                    "urls": (ad.get("finalUrls") or [])[:2], "clicks": int(_f(m.get("clicks"))), "conv": round(_f(m.get("conversions")), 1)})
    return out[:4]


def read_history(account, *, search, geo_names=None, sqr_read=None, store=None, today=None, cached=None):
    """What the Google Ads account has done in the last 90 days. `search(customer_id, gaql)` reads Google Ads; `sqr_read(store, account,
    start, end)` is paid_sqr_data.read; `cached(key, ttl, make)` keeps a result so a second build costs no allowance."""
    started = time.monotonic()
    cid = account["id"]
    start, end = window(today)
    cached = cached or (lambda key, ttl, make: make())
    q = history_queries(start, end)
    notes = []

    def part(name, fn, default):
        try:
            return fn()
        except Exception as e:                              # noqa: BLE001 (one query failing must not lose the rest)
            log.info("strategy history: %s failed: %s", name, e)
            notes.append("%s could not be read (%s)." % (name.capitalize(), _clip(e, 100)))
            return default

    def run(name):
        return lambda: cached("strategy:%s:%s:%s" % (name, cid, end), 3600, lambda: search(cid, q[name]))

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {name: ex.submit(part, name, run(name), []) for name in q}
        sqr_f = ex.submit(part, "search terms", (lambda: sqr_read(store, {"id": cid, "name": account.get("name")}, start, end)), None) if sqr_read else None
        raw = {name: f.result() for name, f in futs.items()}
        sqr = sqr_f.result() if sqr_f else None
    campaigns = _parse_campaigns(raw["campaigns"])
    if not campaigns and "Campaigns could not be read" in " ".join(notes):
        return failed("history", "The campaigns of this account could not be read. " + " ".join(notes), time.monotonic() - started)
    kws = _parse_keywords(raw["keywords"])
    names = {}
    if geo_names:
        wanted = [(r.get("segments") or {}).get("geoTargetRegion") for r in raw["regions"]]
        names = part("place names", lambda: geo_names([w for w in wanted if w]), {})
    tot = {"impr": sum(c["impr"] for c in campaigns), "clicks": sum(c["clicks"] for c in campaigns), "cost": round(sum(c["cost"] for c in campaigns), 2),
           "conv": round(sum(c["conv"] for c in campaigns), 1), "value": round(sum(c["value"] for c in campaigns), 2)}
    tot.update(cpa=_rate(tot["cost"], tot["conv"]), cvr=_rate(tot["conv"] * 100, tot["clicks"], 1), cpc=_rate(tot["cost"], tot["clicks"]),
               ctr=_rate(tot["clicks"] * 100, tot["impr"], 1))
    best = sorted([k for k in kws if k["conv"] > 0], key=lambda k: (-k["conv"], k["cost"]))[:40]
    waste = sorted([k for k in kws if k["conv"] == 0 and k["cost"] > 0], key=lambda k: -k["cost"])[:15]
    terms = {"converting": [], "wasteful": [], "total": 0}
    keywords_known, negatives = 0, {"campaign": 0, "lists": 0}
    if sqr:
        rows = sqr.get("terms") or []
        terms["total"] = len(rows)
        terms["converting"] = [{"term": t["t"], "clicks": t["k"], "cost": t["x"], "conv": t["v"], "campaign": t["c"]}
                               for t in sorted([t for t in rows if t["v"] > 0], key=lambda t: (-t["v"], t["x"]))[:30]]
        terms["wasteful"] = [{"term": t["t"], "clicks": t["k"], "cost": t["x"], "campaign": t["c"]}
                             for t in sorted([t for t in rows if t["v"] == 0 and t["x"] > 0], key=lambda t: -t["x"])[:25]]
        keywords_known = len(sqr.get("keywords") or [])
        negatives = {"campaign": len(sqr.get("negatives") or []), "lists": len(sqr.get("lists") or [])}
        notes.extend(sqr.get("warnings") or [])
    actions = [{"name": (r.get("conversionAction") or {}).get("name"), "type": (r.get("conversionAction") or {}).get("type"),
                "category": (r.get("conversionAction") or {}).get("category"), "primary": bool((r.get("conversionAction") or {}).get("primaryForGoal"))}
               for r in raw["actions"]]
    data = {"account": {"id": cid, "name": account.get("name") or "", "currency": account.get("currency") or "USD"}, "window": {"start": start, "end": end},
            "totals": tot, "campaigns": campaigns[:40], "campaignCount": len(campaigns), "actions": actions[:20], "keywordsBest": best, "keywordsWasted": waste,
            "keywordCount": len(kws) or keywords_known, "terms": terms, "negatives": negatives, "devices": _parse_devices(raw["devices"]),
            "regions": _parse_regions(raw["regions"], names), "ads": _parse_ads(raw["ads"])}
    state = "partial" if notes else "ok"
    spent = "No spend in the last 90 days." if not tot["cost"] else "%d campaigns, %s spent and %s conversions in the last 90 days." % (
        len(campaigns), "{:,.0f}".format(tot["cost"]), "{:,.0f}".format(tot["conv"]))
    return piece("history", state, " ".join([spent] + notes), dict(data, notes=notes), time.monotonic() - started)


# ── Keyword Planner ─────────────────────────────────────────────────────────

COUNTRY_BY_CURRENCY = {"USD": 2840, "CAD": 2124, "GBP": 2826, "AUD": 2036, "NZD": 2554, "INR": 2356, "SGD": 2702}
LANGUAGE_ENGLISH = "languageConstants/1000"
MAX_SEEDS = 20
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


def split_places(text):
    """Places from the free-text locations: split on semicolons and new lines (a comma belongs inside 'Austin, TX')."""
    return [p.strip() for p in re.split(r"[;\n|]+", str(text or "")) if p.strip()][:6]


def resolve_places(places, *, post, country_code="US"):
    """[{'name', 'id', 'canonical', 'type'}] for the places Google knows, best match first. Never raises: an unknown place is left out."""
    out = []
    for place in places:
        try:
            d = post("/geoTargetConstants:suggest", {"locale": "en", "countryCode": country_code, "locationNames": {"names": [place]}})
        except Exception as e:                              # noqa: BLE001
            log.info("strategy keywords: place %r not resolved: %s", place, e)
            continue
        for s in (d.get("geoTargetConstantSuggestions") or [])[:1]:
            g = s.get("geoTargetConstant") or {}
            if g.get("id"):
                out.append({"name": place, "id": str(g["id"]), "canonical": g.get("canonicalName") or g.get("name") or place, "type": g.get("targetType") or ""})
    return out


def _idea(r):
    m = r.get("keywordIdeaMetrics") or {}
    monthly = {}
    for mm in m.get("monthlySearchVolumes") or []:
        try:
            monthly[mm["month"]] = monthly.get(mm["month"], 0) + int(mm.get("monthlySearches") or 0)
        except (KeyError, TypeError, ValueError):
            continue
    peak = max(monthly, key=monthly.get) if monthly else None
    low = min(monthly, key=monthly.get) if monthly else None
    return {"text": r.get("text") or "", "avg": int(_f(m.get("avgMonthlySearches"))), "competition": (m.get("competition") or "").title(),
            "index": int(_f(m.get("competitionIndex"))) if m.get("competitionIndex") is not None else None,
            "lowBid": _money(m.get("lowTopOfPageBidMicros")) or None, "highBid": _money(m.get("highTopOfPageBidMicros")) or None,
            "peak": peak.title() if peak else None, "low": low.title() if low else None}


def seeds_from(inputs, website_piece=None, history_piece=None):
    """Seed phrases for Keyword Planner: what the client says it sells, page titles and headings, and what already converted."""
    seeds = []

    def add(text):
        text = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9&' -]", " ", str(text or ""))).strip().lower()
        if 3 <= len(text) <= 60 and len(text.split()) <= 6 and text not in seeds:
            seeds.append(text)

    for part in re.split(r"[;,\n]+", inputs.get("services") or ""):
        add(part)
    if inputs.get("industry"):
        add(inputs["industry"])
    hist = (history_piece or {}).get("data") or {}
    for k in (hist.get("keywordsBest") or [])[:8]:
        add(k["text"])
    for pg in ((website_piece or {}).get("data") or {}).get("pages", [])[1:8]:
        for h in pg.get("h1") or []:
            add(h)
        add(re.split(r"[|\-\u2013\u2014:]", pg.get("title") or "")[0])
    return seeds[:MAX_SEEDS]


def read_keywords(inputs, *, post, planner_account, website_piece=None, history_piece=None, competitor_piece=None, currency=None):
    """Keyword Planner ideas for the client. `post(path, body)` is Google Ads' API call; `planner_account` is any account that may ask."""
    started = time.monotonic()
    if not planner_account:
        return skipped("keywords", "There is no Google Ads account to ask Keyword Planner through.")
    places = resolve_places(split_places(inputs.get("locations")), post=post) if inputs.get("locations") else []
    geo = ["geoTargetConstants/%s" % p["id"] for p in places] or (["geoTargetConstants/%d" % COUNTRY_BY_CURRENCY[currency]] if currency in COUNTRY_BY_CURRENCY else [])
    seeds = seeds_from(inputs, website_piece, history_piece)
    base = {"language": LANGUAGE_ENGLISH, "geoTargetConstants": geo, "keywordPlanNetwork": "GOOGLE_SEARCH", "pageSize": 120,
            "includeAdultKeywords": False}
    calls = []
    if seeds:
        calls.append(("seeds", dict(base, keywordSeed={"keywords": seeds})))
    client_pages = ((website_piece or {}).get("data") or {}).get("pages") or []
    if client_pages:
        calls.append(("site", dict(base, urlSeed={"url": client_pages[0]["url"]})))
    for r in ((competitor_piece or {}).get("data") or {}).get("competitors", [])[:2]:
        if r.get("ok") and r.get("pages"):
            calls.append(("rival:" + r["name"], dict(base, urlSeed={"url": r["pages"][0]["url"]})))
    if not calls:
        return skipped("keywords", "There was nothing to start Keyword Planner from: add the services, a website or an account.")
    ideas, notes, sources = {}, [], {}
    for label, body in calls:
        try:
            d = post("/customers/%s:generateKeywordIdeas" % planner_account, body)
        except Exception as e:                              # noqa: BLE001 (Keyword Planner has its own limits)
            notes.append("Keyword Planner did not answer for %s (%s)." % (label.split(":")[0], _clip(e, 110)))
            continue
        for r in d.get("results") or []:
            idea = _idea(r)
            if idea["text"] and idea["text"] not in ideas:
                ideas[idea["text"]] = idea
                sources[idea["text"]] = label
    if not ideas:
        return failed("keywords", " ".join(notes) or "Keyword Planner returned no ideas.", time.monotonic() - started)
    ranked = sorted(ideas.values(), key=lambda i: -i["avg"])
    for i in ranked:
        s = sources[i["text"]]
        i["from"] = "competitor" if s.startswith("rival:") else ("site" if s == "site" else "your terms")
    data = {"seeds": seeds, "places": places, "geo": geo, "ideas": ranked[:150], "total": len(ranked)}
    state = "partial" if notes else "ok"
    return piece("keywords", state, " ".join(["%d keyword ideas from %d request%s." % (len(ranked), len(calls) - len(notes), "" if len(calls) - len(notes) == 1 else "s")] + notes),
                 data, time.monotonic() - started)


# ── Seasonality ─────────────────────────────────────────────────────────────

def read_seasonality(account, *, report, months=24):
    """The shape of the year for an account, from the Seasonality Analysis agent. `report(accts, months)` is paid_seasonality.report."""
    started = time.monotonic()
    try:
        r = report([{"id": account["id"], "currency": account.get("currency") or "USD"}], months)
    except Exception as e:                                  # noqa: BLE001
        return failed("seasonality", "Seasonality could not be read (%s)." % _clip(e, 140), time.monotonic() - started)
    if r.get("empty"):
        return piece("seasonality", "partial", "The account has no spend to read a yearly shape from.", {"empty": True}, time.monotonic() - started)
    cov = r.get("coverage") or {}
    idx = r.get("index") or {}
    months_out = []
    for i in range(12):
        row = {"month": MONTHS[i].title()}
        for key in ("conv", "cost"):
            series = idx.get(key) or []
            row[key] = series[i]["index"] if i < len(series) else None
        months_out.append(row)
    data = {"yearly": bool(cov.get("yearly")), "monthsInARow": cov.get("monthsInARow"), "note": cov.get("note"), "months": months_out if cov.get("yearly") else [],
            "headlines": (r.get("headlines") or [])[:6], "upcoming": (r.get("upcoming") or [])[:4], "events": [
                {k: e.get(k) for k in ("name", "conv", "cost", "cpa") if k in e} for e in (r.get("events") or [])[:6]],
            "start": r.get("start"), "end": r.get("end")}
    state = "ok" if cov.get("yearly") else "partial"
    return piece("seasonality", state, cov.get("note") or "Read %d months of daily numbers." % (cov.get("monthsInARow") or 0), data, time.monotonic() - started)


# ── Social Media Intelligence ───────────────────────────────────────────────

SOCIAL_FRESH_DAYS = 45
SOCIAL_WAIT_S = 1200


def _norm_name(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _parsed_time(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def find_social_run(email, inputs, *, store, now=None):
    """The user's latest finished Social Media Intelligence run for this company that is recent enough, or None."""
    now = now or dt.datetime.now(dt.timezone.utc)
    name, host = _norm_name(inputs.get("name")), ps.host_of(inputs.get("website"))
    best = None
    for run in store.list_runs(email, limit=100) or []:
        if run.get("status") != "done":
            continue
        run_host = ps.host_of(run.get("company_url"))
        same = (host == run_host) if (host and run_host) else _norm_name(run.get("company_name")) == name        # two firms can share a name: the website is the tell
        when = _parsed_time(run.get("updated_at") or run.get("created_at"))
        if not same or not when:
            continue
        when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
        if (now - when).days > SOCIAL_FRESH_DAYS:
            continue
        if best is None or when > best[0]:
            best = (when, run)
    return best[1] if best else None


def start_social(email, inputs, *, store, pipeline, spawn):
    """Start a new Social Media Intelligence run for the client in the background. Returns the run id, or None."""
    run_id = store.save_run(email, inputs["name"], inputs.get("website") or None, None)
    if run_id is None:
        return None
    spawn(lambda: pipeline._sci_run_analysis_job(run_id, email, inputs["name"], inputs.get("website") or None))
    return run_id


def _engagement(post):
    m = post.get("metrics") or {}
    if not isinstance(m, dict):
        return 0
    keys = ("likes", "comments", "shares", "reactions", "views", "retweets", "replies", "reposts", "saves", "engagement")
    got = [v for k, v in m.items() if k in keys and isinstance(v, (int, float))]
    return sum(got) if got else sum(v for v in m.values() if isinstance(v, (int, float)))


def read_social(email, run_id, *, store, reused, wait_s=SOCIAL_WAIT_S, poll_s=10, sleep=time.sleep, clock=time.monotonic):
    """The findings of a Social Media Intelligence run, waiting for it if it is still going. Returns a research piece."""
    started = clock()
    waited = False
    run = None
    while True:
        run = store.get_run(run_id, email)
        if not run:
            return failed("social", "The Social Media Intelligence run could not be found.", clock() - started)
        run = store.resolve_stale_run(run)
        if run.get("status") in ("done", "error"):
            break
        waited = True
        if clock() - started >= wait_s:
            return piece("social", "partial", "The Social Media Intelligence run (#%s) is still working after %d minutes, so the strategy is written without it. "
                         "Build again once it has finished and its findings will be used." % (run_id, wait_s // 60),
                         {"runId": run_id, "reused": reused, "pending": True}, clock() - started)
        sleep(poll_s)
    if run.get("status") == "error":
        return failed("social", "The Social Media Intelligence run failed: %s" % _clip(run.get("error") or "no reason was given", 160), clock() - started)
    syn = run.get("synthesis") if isinstance(run.get("synthesis"), dict) else {}
    plats = store.get_platform_runs(run_id) or []
    posts = store.get_posts(run_id) or []
    by_plat = (syn.get("platforms") or {}) if isinstance(syn, dict) else {}
    out_p = []
    for p in plats:
        name = p.get("platform")
        s = by_plat.get(name) or {}
        out_p.append({"platform": name, "handle": p.get("handle") or "", "status": p.get("status") or "", "posts": p.get("post_count") or 0,
                      "lastPostAt": p.get("last_post_at"), "profileUrl": p.get("profile_url") or "",
                      "summary": [_clip(x, 200) for x in (s.get("summary") or [])][:6],
                      "messaging": [_clip(x, 220) for x in (s.get("messaging_and_strategy") or [])][:6]})
    cross = syn.get("cross_platform") or {}
    top = sorted(posts, key=_engagement, reverse=True)[:12]
    top_out = [{"platform": p.get("platform"), "type": p.get("post_type") or "", "caption": _clip(p.get("caption"), 240), "url": p.get("post_url") or "",
                "engagement": _engagement(p), "posted": (p.get("posted_at") or "")[:10]} for p in top]
    active = [p for p in out_p if p["posts"]]
    if not active and not cross:
        return piece("social", "partial", "The run found no organic social activity for this company.", {"runId": run_id, "reused": reused, "platforms": out_p},
                     clock() - started)
    note = "%s Social Media Intelligence run #%s: %d platform%s with posts." % ("Used the finished" if reused and not waited else "Ran and used", run_id, len(active),
                                                                         "" if len(active) == 1 else "s")
    data = {"runId": run_id, "reused": reused, "company": run.get("company_name"), "finished": run.get("updated_at"), "platforms": out_p,
            "crossPlatform": {"summary": [_clip(x, 220) for x in (cross.get("summary") or [])][:8],
                              "messaging": [_clip(x, 240) for x in (cross.get("messaging_and_strategy") or [])][:8]}, "topPosts": top_out}
    return piece("social", "ok", note, data, clock() - started)


# ── The market and its rules ────────────────────────────────────────────────

MARKET_SYSTEM = """You research a market for a Google Ads strategist, using web search. Reply with ONLY a JSON object, no prose before or after:
{"demand": [str], "decision_factors": [str], "trends": [str], "ad_policy": [{"rule": str, "applies_to": str}], "cost_signals": [str], "sources": [str]}
- demand: what drives people to search for this and when (seasons, events, life moments), each one short.
- decision_factors: what a buyer weighs when choosing between businesses here (price, speed, trust, location, insurance, reviews...).
- trends: what changed in the last two years that matters to advertising here.
- ad_policy: advertising rules this industry must follow on Google Ads and in the claims it can make (regulated categories, healthcare, financial, personalised advertising limits, restricted claims, required disclosures). Say plainly when none apply.
- cost_signals: what is known about typical cost per click or cost per lead here, with the place and year it is from. Say so when you only have a rough range.
- sources: the web addresses you relied on.
Every item must come from what you found, never from guesswork. One short sentence each. Do not use em dashes."""


def read_market(inputs, *, ask=None):
    from . import claude_websearch as cw
    ask = ask or cw.ask
    started = time.monotonic()
    user = ("Industry: %s\nWhat the client sells: %s\nWhere: %s\nGoal of the advertising: %s\nClient: %s (%s)\n\nResearch this market."
            % (inputs.get("industry") or "work it out from what is sold", inputs.get("services") or _clip(inputs.get("brief"), 400) or "not given",
               inputs.get("locations") or "not given", ps.GOALS.get(inputs.get("goal"), inputs.get("goal")), inputs.get("name"), inputs.get("website") or "no website"))
    res = ask(MARKET_SYSTEM, user, max_uses=6, max_tokens=4500, model=MODEL, temperature=0.0, deadline=SEARCH_DEADLINE_S)
    spend = cw.spend_of(res)
    usd = cw.spend_usd(spend)
    if (res or {}).get("error"):
        err = res["error"]
        return piece("market", "failed", "The market search did not complete (%s)." % (err.get("kind") if isinstance(err, dict) else err), took=time.monotonic() - started,
                     usd=usd, spend=spend)
    found = cw.extract_json((res or {}).get("text") or "", require="demand")
    if not isinstance(found, dict):
        return piece("market", "failed", "The market search came back in a form that could not be read.", took=time.monotonic() - started, usd=usd, spend=spend)

    def aslist(v):
        return v if isinstance(v, list) else ([v] if isinstance(v, str) else [])        # a reply with a string where a list belongs is one item, not its letters (or a failed card)

    def lines(key, n=8):
        return [cw.strip_em_dash(_clip(x, 260)) for x in aslist(found.get(key)) if isinstance(x, str) and x.strip()][:n]

    policy = []
    for r in aslist(found.get("ad_policy")):
        if isinstance(r, dict) and r.get("rule"):
            policy.append({"rule": cw.strip_em_dash(_clip(r.get("rule"), 280)), "appliesTo": cw.strip_em_dash(_clip(r.get("applies_to"), 120))})
    sources = []
    for s in aslist(found.get("sources")) + list((res or {}).get("result_urls") or []):
        try:
            u = ps.clean_url(s, "source")
        except ps.BadPayload:
            continue
        if u not in sources:
            sources.append(u)
    data = {"demand": lines("demand"), "decisionFactors": lines("decision_factors"), "trends": lines("trends"), "adPolicy": policy[:8],
            "costSignals": lines("cost_signals", 5), "sources": sources[:12]}
    if not any(data[k] for k in ("demand", "decisionFactors", "trends", "adPolicy", "costSignals")):
        return piece("market", "failed", "The market search found nothing it could stand behind.", took=time.monotonic() - started, usd=usd, spend=spend)
    return piece("market", "ok", "%d demand points, %d buying factors and %d advertising rules, from %d sources." % (
        len(data["demand"]), len(data["decisionFactors"]), len(data["adPolicy"]), len(sources)), data, time.monotonic() - started, usd, spend)


# ── For the page ────────────────────────────────────────────────────────────

def public(research):
    """The research as the page is shown it: statuses, notes and facts, without the page text the model was given."""
    out = {}
    for key, pc in (research or {}).items():
        if not isinstance(pc, dict):
            continue
        row = {k: v for k, v in pc.items() if k != "spend"}
        row["data"] = _without_page_text(pc.get("data") or {})
        out[key] = row
    return out


def _page(pg):
    return {k: v for k, v in pg.items() if k not in ("text", "scripts")} if isinstance(pg, dict) else pg


def _without_page_text(data):
    """The pages a site or a competitor was read from, minus the words on them (they are long and only the model needs them)."""
    data = dict(data)
    if isinstance(data.get("pages"), list):
        data["pages"] = [_page(p) for p in data["pages"]]
    if isinstance(data.get("competitors"), list):
        data["competitors"] = [dict(c, pages=[_page(p) for p in c.get("pages") or []]) if isinstance(c, dict) else c for c in data["competitors"]]
    return data


def corpus(research):
    """Everything the research read in words, for checking that a quoted fact is really on a page: the client's pages and the brief."""
    parts = []
    site = ((research or {}).get("website") or {}).get("data") or {}
    for pg in site.get("pages") or []:
        r = pg.get("rating") or {}
        parts.append(" ".join([pg.get("title") or "", pg.get("description") or "", " ".join(pg.get("h1") or []), " ".join(pg.get("h2") or []), pg.get("text") or "",
                               " ".join(pg.get("offers") or []), " ".join(pg.get("phones") or []),
                               ("rated %s stars from %s reviews" % (r.get("value"), r.get("count"))) if r else ""]))
    return "\n".join(parts)


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)
