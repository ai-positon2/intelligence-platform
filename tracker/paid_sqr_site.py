"""Paid Marketing Agents: Search Query Analysis, the words on the account's own website.

A search is judged against what the business itself says it sells. This reads the account's website and returns its vocabulary:

* where: the domains saved for the account's client in the Master Agent, else the site its ads sent the most money to (Google Ads landing pages
  in the range). With several saved domains, the one the ads paid most to reach is read.
* what: the homepage, the pages its menu links to (services, products, locations, pricing: not careers, legal, blog posts or log-ins), and the
  landing pages the ads paid most for, read in parallel within a time limit. robots.txt is obeyed.
* returned: every word the site uses with how many pages use it, its common two- and three-word phrases, and the names it gives its own pages
  (titles, descriptions, menu labels), which is where a business names what it offers. Careers, legal and log-in links are left out of all of it,
  so "jobs" is not a word of the site because its footer links to a careers page.

The page (static/js/sqr_core.js) uses this three ways: each search term is marked with the words of it the site never uses, the model is told the
site's phrases and, per term, those missing words, and a negative that would block something the site names as its own is held back for a person
to look at. A site that cannot be read is said so; the analysis then runs as before.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import re
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from . import paid_domains as dom
from . import paid_gads as g
from . import market_radar_site as site

log = logging.getLogger(__name__)

CACHE_V = "v1:"
TTL = 24 * 3600                 # a site's wording changes slowly; Refresh reads it again
TTL_FAILED = 30 * 60            # a site that could not be read is tried again sooner
MAX_LANDING = 8                 # landing pages, the costliest first
MAX_MENU = 10                   # pages the homepage links to
WORKERS = 6
BUDGET_S = 75                   # the whole read, so the request ends well inside the server's 120 s
MAX_WORDS = 8000
MAX_PHRASES = 3000
MAX_HEADINGS = 120
MAX_HEADING_CHARS = 80

# A link to one of these is not what the business sells: its label and its page stay out of the vocabulary.
NOT_OFFER = re.compile(r"career|\bjobs?\b|/jobs|employ|hiring|join[-\s]?(our|the)?[-\s]?team|privacy|terms|cookie|legal|impressum|accessibility|"
                       r"sitemap|login|log-in|sign[-\s]?in|sign[-\s]?up|register|account|cart|checkout|wishlist|investor|press|newsroom|"
                       r"/blog/.+|/news/.+|/tag/|/category/|/author/|/feed|wp-admin|wp-json|\?replytocom|mailto:|tel:|javascript:", re.I)
UI_LABEL = re.compile(r"^(skip to (main )?content|close( search)?|menu|search|home|see (more|all)|view (more|all|details)|read more|learn more|more|next|previous|"
                      r"back to top|toggle navigation|open menu)$", re.I)
TITLE_SPLIT = re.compile(r"\s+[|–—·:•-]\s+|\s*\|\s*")
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
STOP = frozenset((
    "a about above after again all also am an and any are as at be because been before being below between both but by can could did do does "
    "doing down during each few for from further had has have having he her here hers him his how i if in into is it its itself just let me "
    "more most my no nor not now of off on once only or other our ours out over own same she should so some such than that the their theirs them "
    "then there these they this those through to too under until up very was we were what when where which while who whom why will with would "
    "you your yours us get got may might must shall can't don't won't it's we're you're").split())

LANDING_GAQL = ("SELECT landing_page_view.unexpanded_final_url, metrics.cost_micros FROM landing_page_view WHERE segments.date BETWEEN '%s' AND '%s' "
                "AND metrics.cost_micros > 0 ORDER BY metrics.cost_micros DESC LIMIT 300")


# ── Where the site is ───────────────────────────────────────────────────────

def parse_landing(rows):
    """[(url, cost)] from landing_page_view rows: the same address in several rows summed, costliest first, query strings and fragments kept off."""
    cost = Counter()
    for r in rows or []:
        url = str(((r.get("landingPageView") or {}).get("unexpandedFinalUrl")) or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        url = url.split("#", 1)[0].split("?", 1)[0]
        cost[url] += int((r.get("metrics") or {}).get("costMicros") or 0) / 1e6
    return sorted(cost.items(), key=lambda kv: (-kv[1], kv[0]))


def _host(url):
    h = dom.host_of(str(url or "").lower())
    return h[4:] if h and h.startswith("www.") and h.count(".") >= 2 else h


def choose(domains, landing):
    """(domain, why, landing pages to read) for an account: of the client's saved domains, the one its ads paid most to reach (the first saved
    one when the ads reach none); with no saved domain, the site the ads paid most to reach. (None, why, []) when there is neither."""
    spend = Counter()
    for url, cost in landing:
        h = _host(url)
        if h:
            spend[h] += cost
    if domains:
        by = {d: sum(c for h, c in spend.items() if dom.covers(d, h)) for d in domains}
        best = max(domains, key=lambda d: (by[d], -domains.index(d)))
        why = "saved for this client in the Master Agent" + (" (the one of its %d domains the ads paid most to reach)" % len(domains) if len(domains) > 1 else "")
    elif spend:
        best = max(spend, key=lambda h: (spend[h], h))
        why = "the site this account's ads paid most to reach (no domain is saved for it in the Master Agent)"
    else:
        return None, "no domain is saved for this account in the Master Agent and its ads reached no landing page in this range", []
    pages = [u for u, _c in landing if _host(u) and dom.covers(best, _host(u))][:MAX_LANDING]
    return best, why, pages


# ── Reading it ──────────────────────────────────────────────────────────────

LINK = re.compile(r"([^\[\]]*)\[([^\]]*)\]")


def _clean_lines(text):
    """A page's text as lines of plain words, link targets dropped. A link to a careers, legal or log-in page is dropped with its label (and the
    text just before it), never the rest of its line: a menu is often one line, and dropping it whole for its Careers link dropped the menu."""
    out = []
    for line in str(text or "").splitlines():
        line = LINK.sub(lambda m: " " if NOT_OFFER.search(m.group(2)) else m.group(1) + " ", line)
        line = line.replace("\u2019", "'").lower()
        if line.strip():
            out.append(line)
    return out


def words_of(line):
    return [w[:-2] if w.endswith("'s") else w for w in WORD.findall(line)]


def menu_links(doc, home_url, rules):
    """(label, url) of the site's own pages the homepage links to, in the order it links them (the menu comes first), at most MAX_MENU: not the
    homepage itself, not files, not careers, legal, log-in or blog-post pages, and only where robots.txt lets us."""
    home_host = _host(home_url)
    out, seen = [], set()
    for href, label in doc.links:
        if len(out) >= MAX_MENU:
            break
        url = urljoin(home_url, href).split("#", 1)[0]
        if not url.startswith("http") or _host(url) != home_host:
            continue
        path = urlsplit(url).path or "/"
        key = path.rstrip("/").lower()
        if not key or key in seen or site.SKIP_PATH.search(path) or NOT_OFFER.search(url) or NOT_OFFER.search(label or "") or path.count("/") > 4:
            continue
        if not site.allowed(path, rules):
            continue
        seen.add(key)
        out.append((label, url.split("?", 1)[0]))
    return out


def headings_of(doc, home_url=None):
    """What a page calls itself and, for the homepage, what its menu calls the pages it links to: short lines, careers and legal left out."""
    out = []
    for part in TITLE_SPLIT.split(" ".join((doc.title or "").split())):
        out.append(part)
    desc = (doc.meta.get("description") or doc.meta.get("og:description") or "").strip()
    if desc:
        out.append(desc)
    if home_url:
        home_host = _host(home_url)
        for href, label in doc.links:
            url = urljoin(home_url, href)
            if label and _host(url) == home_host and not NOT_OFFER.search(url) and not NOT_OFFER.search(label) and len(label.split()) <= 6:
                out.append(label)
    out = [" ".join(x.split())[:MAX_HEADING_CHARS] for x in out if x]
    return [x for x in out if re.search(r"[A-Za-z]{2}", x) and not UI_LABEL.match(x)]       # not blank, an arrow or a button


def vocabulary(texts, headings):
    """{words: {word: pages using it}, phrases: [[phrase, pages using it]], headings: [...]} from the pages' texts and the pages' own names."""
    pages_of, gram_pages, gram_count = Counter(), Counter(), Counter()
    for text in texts:
        seen_w, seen_g = set(), set()
        for line in _clean_lines(text):
            ws = words_of(line)
            seen_w.update(w for w in ws if 2 <= len(w) <= 30 and not w.isdigit())
            for n in (2, 3):
                for i in range(len(ws) - n + 1):
                    gram = ws[i:i + n]
                    if gram[0] in STOP or gram[-1] in STOP or any(w.isdigit() and len(w) > 4 for w in gram):
                        continue
                    p = " ".join(gram)
                    gram_count[p] += 1
                    seen_g.add(p)
        pages_of.update(seen_w)
        gram_pages.update(seen_g)
    heads, seen = [], set()
    for h in headings:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            heads.append(h)
        for w in words_of(k):                         # what the site names itself is its vocabulary even when the name is only in a title
            if 2 <= len(w) <= 30 and not w.isdigit() and w not in pages_of:
                pages_of[w] = 1
    words = dict(sorted(pages_of.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_WORDS])
    phrases = [[p, gram_pages[p]] for p in sorted((p for p in gram_count if gram_count[p] >= 2), key=lambda p: (-gram_pages[p], -gram_count[p], p))][:MAX_PHRASES]
    return {"words": words, "phrases": phrases, "headings": heads[:MAX_HEADINGS]}


def _page(kind, page, url=None):
    return {"kind": kind, "url": page.get("final_url") or url or page.get("url"), "status": page.get("status"),
            "words": len((page.get("text") or "").split()) if page.get("status") == "ok" else 0, "note": page.get("note") or ""}


def crawl(domain, landing, *, fetch=None, choose_home=None, fetch_text=None, budget=BUDGET_S, clock=time.monotonic):
    """Read one site: its homepage, the pages its menu links to and the given landing pages. Never raises; a site that cannot be read comes back
    with status "failed" and why."""
    fetch = fetch or site.fetch
    choose_home = choose_home or site.choose_home
    fetch_text = fetch_text or site.fetch_text
    started = clock()
    out = {"domain": domain, "status": "failed", "pages": [], "words": {}, "phrases": [], "headings": [], "notes": [],
           "readAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        home, notes = choose_home("https://%s/" % domain)
        out["notes"] += notes
        home_url = home.get("final_url") or "https://%s/" % domain
        out["home"] = home_url
        out["pages"].append(_page("home", home, home_url))
        rules, _maps = site.parse_robots(fetch_text(urljoin(home_url, "/robots.txt"), limit=500_000))
        texts, heads, menu = [], [], []
        if home.get("status") == "ok":
            doc = site.parse_html(home.get("html") or "")
            texts.append(home.get("text") or "")
            heads += headings_of(doc, home_url)
            menu = menu_links(doc, home_url, rules)
        todo, seen = [], {urlsplit(home_url).path.rstrip("/").lower()}
        for kind, url in [("landing", u) for u in landing] + [("menu", u) for _label, u in menu]:
            key = urlsplit(url).path.rstrip("/").lower()
            if key in seen or not site.allowed(urlsplit(url).path or "/", rules):
                continue
            seen.add(key)
            todo.append((kind, url))
        left = max(min(5.0, budget), budget - (clock() - started))     # the menu and landing pages get what the homepage left, never nothing
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS)
        futs = {pool.submit(fetch, url): (kind, url) for kind, url in todo}
        done, late = concurrent.futures.wait(futs, timeout=left)
        pool.shutdown(wait=False, cancel_futures=True)
        for f, (kind, url) in futs.items():
            if f in done and f.exception() is None:
                page = f.result()
                out["pages"].append(_page(kind, page, url))
                if page.get("status") == "ok":
                    texts.append(page.get("text") or "")
                    heads += headings_of(site.parse_html(page.get("html") or ""))
            else:
                out["pages"].append({"kind": kind, "url": url, "status": "error", "words": 0,
                                     "note": "not read within the time limit" if f in late else "the reader broke on it"})
        if texts:
            out.update(vocabulary(texts, heads))
            out["status"] = "ok"
        else:
            out["notes"].append("the homepage could not be read (%s)" % (home.get("note") or home.get("status") or "no answer"))
    except Exception as e:                                   # a site that breaks the reader is a site we could not read, never a failed analysis
        log.exception("sqr site: reading %s broke", domain)
        out["notes"].append("the website reader broke on this site (%s)" % type(e).__name__)
    out["pagesRead"] = sum(1 for p in out["pages"] if p.get("status") == "ok")
    out["wordCount"] = len(out["words"])
    return out


# ── For one account ─────────────────────────────────────────────────────────

def master_domains(master, account_id):
    from . import paid_master
    eff = paid_master.effective(master or {}, str(account_id))
    return list((eff or {}).get("domains") or [])


def read(store, account, start, end, *, master=None, force=False, http=None, crawl_fn=None):
    """The website of one Google Ads account: {status ok|failed|none, domain, why, home, pages, words, phrases, headings, notes, warnings}.
    The landing pages come from Google Ads (cached like every other read); the site itself is cached a day, a failed read half an hour."""
    cid = account["id"]
    warnings = []
    try:
        landing = g._cached(store, CACHE_V + "sqr:landing:%s:%s:%s" % (cid, start, end), g._ttl(end),
                            lambda: parse_landing(g.search(cid, LANDING_GAQL % (start, end), store, http=http)), force=force)
    except g.AdsError as e:
        landing = []
        warnings.append("The landing pages could not be read from Google Ads (%s), so only the homepage and its menu are read." % str(e).rstrip("."))
    domain, why, pages = choose(master_domains(master, cid), [tuple(x) for x in landing])
    if not domain:
        return {"status": "none", "why": why, "warnings": warnings}
    key = CACHE_V + "sqr:site:%s:%s:%s" % (cid, domain, hashlib.sha1("|".join(pages).encode()).hexdigest()[:10])
    hit = None if force else (g._entry(store, key, TTL) or None)
    if hit and (hit[0].get("status") == "ok" or g._age_s(hit[1] or "") < TTL_FAILED):
        got = hit[0]
    else:
        got = (crawl_fn or crawl)(domain, pages)
        try:
            store.put_gads_cache(key, got)
            g._mem_put(key, got, g._now_iso())
        except Exception as e:
            log.warning("sqr site: cache write failed (%s)", e)
    return dict(got, why=why, warnings=warnings)
