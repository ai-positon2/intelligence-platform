"""Market Radar, Phase 0: which free sources answer from THIS server's IP.

The plan for the Market Radar agent leans on free public sources, and every
one of them was first checked from a laptop on an office line. That proves
nothing about production: tracker/news_client.py already records that Google
blocks CI and datacenter IPs outright, and Railway is a datacenter. So this
runs the same checks from inside the deployed app and reports what came back.

Google News gets the most attention because the agent's whole news plan
rests on it:

  * blocked or not: a 503, a 403, a redirect to google.com/sorry or an
    "unusual traffic" page all mean blocked, and are told apart from a 429
    (rate limited) and from a feed that answered with no items (empty);
  * the user agent: news_client sends a bot user agent, so the same query is
    sent with it and with a browser one, because a block can depend on it;
  * country editions: the agent is global, so six editions are queried;
  * a burst: a real run reads about 60 feeds, and one good request says
    nothing about the 30th, so a run of sequential queries reports how many
    succeeded and where the first failure came;
  * article links: Google changed its link format and news_client's
    offline decoder recovers 0 of 52 links in a live feed (2026-10-08). The
    current method costs two more Google requests per article, so it is
    measured here too, from this IP, alongside the old decoder's hit rate.

Every other source in the plan gets one request. Nothing here writes
anything, holds a key or costs money. A check that could not run says so
(`not_checked`), so a missing result is never read as a pass.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

BOT_UA = "Position2-MarketRadar-probe/0.1 (+https://intelligence.position2.com)"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
TIMEOUT = 12            # seconds per request
MAX_READ = 512 * 1024   # bytes read per response; some job boards return megabytes
# Google's article page carries the decode signature near its END: measured at
# byte 595,767 of a 597,296-byte page (2026-10-08). Read it whole.
ARTICLE_PAGE_MAX_READ = 2 * 1024 * 1024
GDELT_GAP = 5.5         # GDELT asks for at most one request every 5 seconds

# One query per edition, in that market's language, about local openings: the
# kind of query the local radar will send.
EDITIONS = [
    ("US", "en-US", "US", "US:en", '"opens new" dental clinic'),
    ("UK", "en-GB", "GB", "GB:en", '"opens" dental practice'),
    ("India", "en-IN", "IN", "IN:en", '"opens new" clinic branch'),
    ("Germany", "de", "DE", "DE:de", "Zahnarztpraxis eröffnet"),
    ("Brazil", "pt-BR", "BR", "BR:pt-419", "clínica odontológica inaugura"),
    ("Japan", "ja", "JP", "JP:ja", "歯科 開院"),
]

# Sequential queries for the burst: competitor-style brand lookups, each a
# different URL so no cache can answer for Google.
BURST_QUERIES = [
    "Aspen Dental", "Heartland Dental", "Pacific Dental Services", "Smile Brands",
    "Western Dental", "Allbirds", "Warby Parker", "Glossier", "Gymshark", "Bombas",
    "Chipotle", "Sweetgreen", "Orangetheory Fitness", "Planet Fitness", "Starbucks",
    "Dutch Bros", "Crumbl Cookies", "European Wax Center", "Massage Envy", "Great Clips",
    "Sephora", "Ulta Beauty", "Lululemon", "Skims", "Ruggable",
    "Away luggage", "Casper mattress", "Brooklinen", "Hims & Hers", "Peloton",
]
BURST_GAP = 0.4         # seconds between burst requests: polite, not pathological
GOOGLE_BUDGET = 60      # seconds for the whole Google section; gunicorn kills at 120
DECODE_SAMPLE = 3       # article links put through the current decode method


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch(url, *, ua=BOT_UA, method="GET", data=None, headers=None,
           timeout=TIMEOUT, max_read=MAX_READ):
    """One request, never raising. Returns status, size, timing, final URL and
    the first `max_read` bytes of the body as text."""
    hdrs = {"User-Agent": ua, "Accept": "*/*"}
    hdrs.update(headers or {})
    started = time.monotonic()
    try:
        with requests.request(method, url, headers=hdrs, data=data, timeout=timeout,
                              stream=True, allow_redirects=True) as resp:
            body = b""
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) >= max_read:
                    break
            return {
                "status": resp.status_code,
                "bytes": len(body),
                "truncated": len(body) >= max_read,
                "ms": int((time.monotonic() - started) * 1000),
                "final_url": resp.url,
                "text": body.decode(resp.encoding or "utf-8", errors="replace"),
                "error": None,
            }
    except requests.Timeout:
        kind = "timeout"
    except requests.ConnectionError as e:
        kind = "connection: %s" % str(e)[:160]
    except Exception as e:  # anything else is still a result, not a crash
        kind = "%s: %s" % (type(e).__name__, str(e)[:160])
    return {"status": None, "bytes": 0, "truncated": False,
            "ms": int((time.monotonic() - started) * 1000),
            "final_url": url, "text": "", "error": kind}


def verdict(res, items=None):
    """Classify one fetch: ok | empty | blocked | rate_limited | timeout | error.

    `items` is how many records the caller parsed out of the body (None when
    the source has no item count to check). A 200 that parsed to zero items
    is `empty`, never `ok`: an answer with nothing in it is the shape a
    silent block often takes."""
    if res["error"] == "timeout":
        return "timeout"
    if res["error"]:
        return "error"
    status = res["status"]
    text = res["text"][:4000].lower()
    is_feed = "<rss" in text or "<?xml" in text
    # "unusual traffic" is Google's block page; a feed can carry the phrase in
    # a headline, so it only counts when the body is not a feed.
    if "/sorry/" in (res["final_url"] or "") or ("unusual traffic" in text and not is_feed):
        return "blocked"
    if status == 429:
        return "rate_limited"
    if status in (401, 403, 503):
        return "blocked"
    if status is None or status >= 400:
        return "error"
    if items is not None and items == 0:
        return "empty"
    return "ok"


def _row(name, purpose, res, items=None, note=""):
    v = verdict(res, items)
    detail = res["error"] or ""
    if v in ("blocked", "rate_limited", "error") and not detail:
        detail = re.sub(r"\s+", " ", res["text"][:200]).strip()
    return {"name": name, "purpose": purpose, "verdict": v, "status": res["status"],
            "ms": res["ms"], "bytes": res["bytes"], "items": items,
            "detail": detail, "note": note}


# -- Google News -------------------------------------------------------------

def gnews_url(query, hl="en-US", gl="US", ceid="US:en"):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": hl, "gl": gl, "ceid": ceid})


def rss_items(text):
    return re.findall(r"<item>(.*?)</item>", text, re.S)


def _item_links(text):
    return [m.group(1) for m in
            (re.search(r"<link>(.*?)</link>", it) for it in rss_items(text)) if m]


def decode_params(article_html):
    """The signature and timestamp Google's article page carries, which the
    batchexecute call needs. None when the page does not have them."""
    sg = re.search(r'data-n-a-sg="([^"]+)"', article_html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', article_html)
    if not (sg and ts):
        return None
    return sg.group(1), int(ts.group(1))


def batchexecute_body(article_id, ts, sig):
    inner = ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
               None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             article_id, ts, sig]
    freq = [[["Fbv4je", json.dumps(inner, separators=(",", ":")), None, "generic"]]]
    return "f.req=" + urllib.parse.quote(json.dumps(freq, separators=(",", ":")))


def decoded_url(batchexecute_text):
    m = re.search(r'\[\\"garturlres\\",\\"(.*?)\\"', batchexecute_text)
    return m.group(1) if m else None


def _decode_current(link):
    """Google's current link decode: fetch the article page for its signature,
    then ask batchexecute for the real URL. Two requests to Google."""
    m = re.search(r"/articles/([^?/]+)", link)
    if not m:
        return {"ok": False, "reason": "no article id in link", "ms": 0}
    aid = m.group(1)
    page = _fetch("https://news.google.com/rss/articles/" + aid, ua=BROWSER_UA,
                  max_read=ARTICLE_PAGE_MAX_READ)
    params = decode_params(page["text"])
    if not params:
        return {"ok": False, "ms": page["ms"],
                "reason": "article page gave no signature (%s, %s, %d bytes%s)" % (
                    verdict(page), page["status"], page["bytes"],
                    ", cut short" if page["truncated"] else "")}
    sig, ts = params
    post = _fetch("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                  ua=BROWSER_UA, method="POST", data=batchexecute_body(aid, ts, sig),
                  headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    url = decoded_url(post["text"])
    return {"ok": bool(url), "url": url, "ms": page["ms"] + post["ms"],
            "reason": None if url else "batchexecute gave no URL (%s, %s)" % (
                verdict(post), post["status"])}


def check_google_news(budget=GOOGLE_BUDGET):
    """Every Google check, inside `budget` seconds. A blocked Google can answer
    each request with a 12-second timeout, so without a budget this alone
    could outlast the web worker; checks it had no time for are reported as
    skipped, never as passed."""
    out = {"user_agent": [], "editions": [], "burst": {}, "links": {}}
    deadline = time.monotonic() + budget

    def out_of_time():
        return time.monotonic() > deadline

    # 1. Same query, bot vs browser user agent.
    us_url = gnews_url('"opens new" dental clinic when:30d')
    us_feed = None
    for label, ua in (("bot (what news_client sends today)", BOT_UA),
                      ("browser", BROWSER_UA)):
        res = _fetch(us_url, ua=ua)
        n = len(rss_items(res["text"]))
        out["user_agent"].append(_row("Google News, " + label, "blocking by user agent", res, n))
        if n and us_feed is None:
            us_feed = res["text"]

    # 2. Country editions.
    for country, hl, gl, ceid, query in EDITIONS:
        if out_of_time():
            out["editions"].append({"name": "Google News " + country, "purpose": ceid,
                                    "verdict": "skipped", "status": None, "ms": 0, "bytes": 0,
                                    "items": None, "detail": "time budget spent", "note": ""})
            continue
        res = _fetch(gnews_url(query, hl, gl, ceid), ua=BROWSER_UA)
        out["editions"].append(_row("Google News " + country, ceid, res,
                                    len(rss_items(res["text"]))))

    # 3. Burst of sequential queries.
    results = []
    for q in BURST_QUERIES:
        if out_of_time():
            break
        res = _fetch(gnews_url('"%s"' % q), ua=BROWSER_UA)
        results.append((q, verdict(res, len(rss_items(res["text"]))), res["status"], res["ms"]))
        time.sleep(BURST_GAP)
    first_bad = next((i for i, r in enumerate(results) if r[1] != "ok"), None)
    out["burst"] = {
        "planned": len(BURST_QUERIES),
        "requests": len(results),
        "ok": sum(1 for r in results if r[1] == "ok"),
        "first_failure_at": None if first_bad is None else first_bad + 1,
        "verdicts": {v: sum(1 for r in results if r[1] == v) for v in {r[1] for r in results}},
        "median_ms": sorted(r[3] for r in results)[len(results) // 2] if results else None,
        "failures": [{"query": q, "verdict": v, "status": s}
                     for q, v, s, _ in results if v != "ok"][:10],
    }

    # 4. Article links: old decoder hit rate, then the current method.
    if us_feed is None:
        out["links"] = {"not_checked": "no Google News feed answered with items, "
                                       "so there were no links to decode"}
        return out
    if out_of_time():
        out["links"] = {"not_checked": "time budget spent before link decoding"}
        return out
    links = _item_links(us_feed)
    try:
        from tracker.news_client import _decode_google_news_url
        old_hits = sum(1 for l in links if "news.google.com" not in _decode_google_news_url(l))
        old = {"links": len(links), "decoded": old_hits}
    except Exception as e:
        old = {"not_checked": "%s: %s" % (type(e).__name__, str(e)[:120])}
    sample = [_decode_current(l) for l in links[:DECODE_SAMPLE]]
    out["links"] = {
        "old_offline_decoder": old,
        "current_method": {
            "tried": len(sample),
            "decoded": sum(1 for s in sample if s["ok"]),
            "avg_ms": int(sum(s["ms"] for s in sample) / len(sample)) if sample else None,
            "examples": [s.get("url") or s.get("reason") for s in sample],
        },
    }
    return out


# -- Every other source in the plan ------------------------------------------

def _count_json(text, *path):
    try:
        data = json.loads(text)
        for key in path:
            data = data[key]
        return len(data)
    except Exception:
        return 0


def _one(name, purpose, url, counter=None, **kw):
    def run():
        res = _fetch(url, **kw)
        items = counter(res) if counter else None
        note = "body cut at %d KB for this check" % (MAX_READ // 1024) if res["truncated"] else ""
        return _row(name, purpose, res, items, note)
    return run


def _gdelt():
    # GDELT answered 429 twice in a row from the laptop. Wait out its stated
    # gap first so a 429 here means this IP, not the call before it.
    time.sleep(GDELT_GAP)
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query=%22dental%20clinic%22"
           "&mode=artlist&maxrecords=10&format=json&timespan=1month")
    res = _fetch(url)
    return _row("GDELT DOC API", "backup news source", res,
                _count_json(res["text"], "articles"))


def _overpass():
    query = ('[out:json][timeout:20];node["amenity"="dentist"]'
             "(37.30,-122.10,37.45,-121.95);out meta 5;")
    hosts = ("overpass-api.de", "overpass.private.coffee", "maps.mail.ru/osm/tools/overpass")

    def ask(host):
        res = _fetch("https://%s/api/interpreter" % host, method="POST",
                     data={"data": query}, headers={"Accept": "application/json"},
                     timeout=25)
        return _row("OpenStreetMap Overpass (%s)" % host.split("/")[0],
                    "local radar: new places nearby", res,
                    _count_json(res["text"], "elements"))

    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(pool.map(ask, hosts))


def _rss_count(res):
    return len(rss_items(res["text"]))


def other_sources():
    jobs = [
        _one("Bing News RSS", "backup news source",
             "https://www.bing.com/news/search?q=%22opens%22+%22dental%22&format=rss",
             _rss_count, ua=BROWSER_UA),
        _one("Greenhouse jobs API", "competitor hiring",
             "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs",
             lambda r: _count_json(r["text"], "jobs")),
        _one("Lever jobs API", "competitor hiring",
             "https://api.lever.co/v0/postings/palantir?mode=json&limit=5",
             lambda r: _count_json(r["text"])),
        _one("Ashby jobs API", "competitor hiring",
             "https://api.ashbyhq.com/posting-api/job-board/ramp"),
        _one("crt.sh certificate log", "new subdomains (B2B, later phase)",
             "https://crt.sh/?q=%25.position2.com&output=json"),
        _one("Overture Maps release list (S3)", "local radar: monthly new places",
             "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/",
             lambda r: len(re.findall(r"<Prefix>release/", r["text"]))),
        _one("Wayback Machine CDX", "first-run website history",
             "https://web.archive.org/cdx/search/cdx?url=aspendental.com/*&output=json&limit=5&from=2026",
             lambda r: max(0, _count_json(r["text"]) - 1), timeout=20),
        _one("SEC EDGAR full-text search", "filings (B2B, later phase)",
             "https://efts.sec.gov/LATEST/search-index?q=%22new%20locations%22&forms=8-K"),
        _one("Hacker News (Algolia)", "industry pulse for tech",
             "https://hn.algolia.com/api/v1/search_by_date?query=dental&tags=story&hitsPerPage=5",
             lambda r: _count_json(r["text"], "hits")),
        _one("Newly registered domains (whoisds)", "pre-launch signals",
             "https://www.whoisds.com/newly-registered-domains", ua=BROWSER_UA),
        _one("Nominatim geocoder", "geocoding addresses",
             "https://nominatim.openstreetmap.org/search?q=Merced%2C+CA&format=json&limit=1",
             lambda r: _count_json(r["text"])),
        _one("Federal Register API", "regulation (US country pack)",
             "https://www.federalregister.gov/api/v1/documents.json?per_page=5&conditions%5Bterm%5D=dental",
             lambda r: _count_json(r["text"], "results")),
        _one("Shopify products.json (allbirds.com)", "e-commerce catalog",
             "https://www.allbirds.com/products.json?limit=5",
             lambda r: _count_json(r["text"], "products")),
        _one("Competitor sitemap (aspendental.com)", "branch openings",
             "https://www.aspendental.com/dentist/server-sitemap-index.xml",
             lambda r: len(re.findall(r"<loc>", r["text"]))),
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(j) for j in jobs]
        gdelt = pool.submit(_gdelt)
        overpass = pool.submit(_overpass)
        rows = [f.result() for f in futures]
        rows.append(gdelt.result())
        rows.extend(overpass.result())
    return rows


def egress():
    """Where this server's requests come from, so a block can be tied to an
    address and a network rather than guessed at."""
    res = _fetch("https://ipinfo.io/json")
    try:
        info = json.loads(res["text"])
        return {k: info.get(k) for k in ("ip", "city", "region", "country", "org")}
    except Exception:
        return {"not_checked": res["error"] or "ipinfo answered %s" % res["status"]}


def probe():
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=3) as pool:
        ip = pool.submit(egress)
        gnews = pool.submit(check_google_news)
        rest = pool.submit(other_sources)
        result = {"ran_at": _now(), "egress": ip.result(),
                  "google_news": gnews.result(), "sources": rest.result()}
    rows = (result["google_news"]["user_agent"] + result["google_news"]["editions"]
            + result["sources"])
    result["summary"] = {
        "checks": len(rows),
        "ok": sum(1 for r in rows if r["verdict"] == "ok"),
        "not_ok": [{"name": r["name"], "verdict": r["verdict"], "status": r["status"]}
                   for r in rows if r["verdict"] != "ok"],
        "seconds": round(time.monotonic() - started, 1),
    }
    result["not_checked"] = [
        "Serper (no key yet)", "Google Places (no key yet)",
        "Foursquare Open Source Places (needs the Hugging Face access agreement)",
        "Reddit (already covered by the sci-reddit-check self-test)",
    ]
    return result
