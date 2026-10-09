"""Market Radar, Phase 3: news about a competitor, from Google News RSS in
the competitor's own country edition. No model here.

What this module does and does not do:

* It asks Google News for the company's name in the edition of the
  country the company is in (a German chain in the German edition, in
  German), keeps the articles from the last 90 days whose HEADLINE names
  the company, and folds syndicated copies of one story into one item.
* It stores the articles as a snapshot. It does not decide what an article
  is about (an opening, a price rise, a lawsuit): that needs a reading of
  the article and belongs to the signal engine (Phase 6), which also
  merges an article with the sitemap change or job post that confirms it.
* Article links stay as Google's redirect links. Turning one into the
  publisher's address costs two requests (news_client._decode_google_news_url),
  so only the articles that survive triage will pay it. The publisher's
  own site comes free with every item (the feed's <source url=...>).

Google News answered every feed from Railway in Phase 0 (38 of 38, twice),
but that was 38 feeds, not hundreds a day. A breaker shared by one
collection stops asking after a run of failures, and every item a breaker
or a failure cost is reported as not read, never as "no news".
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import random
import re
import threading
import time
import unicodedata
import urllib.parse

DAYS = 90
FULL = 90                  # a feed this long may be cut at Google's 100, so ask again
BREAKER_AFTER = 5
GAP_S = 0.6                # between two requests to Google from one collection

# country -> (hl, gl, ceid). Countries not listed read the US English edition.
EDITIONS = {
    "US": ("en-US", "US", "US:en"), "GB": ("en-GB", "GB", "GB:en"),
    "IN": ("en-IN", "IN", "IN:en"), "AU": ("en-AU", "AU", "AU:en"),
    "CA": ("en-CA", "CA", "CA:en"), "IE": ("en-IE", "IE", "IE:en"),
    "NZ": ("en-NZ", "NZ", "NZ:en"), "SG": ("en-SG", "SG", "SG:en"),
    "ZA": ("en-ZA", "ZA", "ZA:en"), "AE": ("en-AE", "AE", "AE:en"),
    "DE": ("de", "DE", "DE:de"), "AT": ("de-AT", "AT", "AT:de"), "CH": ("de-CH", "CH", "CH:de"),
    "FR": ("fr", "FR", "FR:fr"), "BE": ("fr", "BE", "BE:fr"), "ES": ("es", "ES", "ES:es"),
    "MX": ("es-419", "MX", "MX:es-419"), "AR": ("es-419", "AR", "AR:es-419"),
    "CL": ("es-419", "CL", "CL:es-419"), "CO": ("es-419", "CO", "CO:es-419"),
    "BR": ("pt-BR", "BR", "BR:pt-419"), "PT": ("pt-PT", "PT", "PT:pt-150"),
    "IT": ("it", "IT", "IT:it"), "NL": ("nl", "NL", "NL:nl"), "SE": ("sv", "SE", "SE:sv"),
    "NO": ("no", "NO", "NO:no"), "DK": ("da", "DK", "DK:da"), "FI": ("fi", "FI", "FI:fi"),
    "PL": ("pl", "PL", "PL:pl"), "JP": ("ja", "JP", "JP:ja"), "KR": ("ko", "KR", "KR:ko"),
    "TR": ("tr", "TR", "TR:tr"),
}
# Words for a second, narrower query when the first may have been cut at
# 100 items: the moves a report is about. English editions only.
MOVES = ("opens OR opening OR launches OR launch OR expands OR acquires OR acquisition OR "
         "raises OR funding OR hiring OR layoffs OR closes OR closing OR price OR CEO")
LEGAL = re.compile(r"[,\s]+(inc|llc|ltd|limited|plc|gmbh|ag|sa|s\.a\.|ltda|corp|corporation|"
                   r"co|company|group|holdings|pvt|private|pty)\.?$", re.I)


class Breaker:
    """Shared by every news read in one collection: after BREAKER_AFTER
    failures in a row, stop asking Google and say so."""

    def __init__(self, after=BREAKER_AFTER, gap=GAP_S, sleep=time.sleep, clock=time.monotonic):
        self.after, self.gap, self.sleep, self.clock = after, gap, sleep, clock
        self.fails, self.open, self.requests = 0, False, 0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait_turn(self):
        with self._lock:
            pause = self._last + self.gap - self.clock()
            if pause > 0:
                self.sleep(pause + random.uniform(0, 0.3))
            self._last = self.clock()
            self.requests += 1

    def record(self, ok):
        with self._lock:
            self.fails = 0 if ok else self.fails + 1
            if self.fails >= self.after:
                self.open = True


def feed_url(query, country):
    hl, gl, ceid = EDITIONS.get((country or "").upper(), EDITIONS["US"])
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": hl, "gl": gl, "ceid": ceid})


def clean_name(name, domain):
    """The name a headline would use: legal suffixes off ("Aspen Dental
    Management, Inc." -> "Aspen Dental"). Falls back to the domain's label."""
    n = " ".join((name or "").split())
    for _ in range(2):
        n = LEGAL.sub("", n).strip(" ,.")
    return n or domain.split(".")[0]


def fold(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def names_company(title, name, domain):
    """True when the headline names the company: its name as whole words, or
    its domain label run together ("mydentist" for "{my}dentist")."""
    t = fold(title)
    n = fold(name)
    label = fold(domain.split(".")[0])
    if n and re.search(r"(^| )%s( |$)" % re.escape(n), t):
        return True
    return len(label) >= 5 and label in t.replace(" ", "")


def parse_items(xml):
    """[{"title", "publisher", "publisher_site", "date", "link"}] from a
    Google News RSS feed. The headline's trailing " - Publisher" is cut."""
    out = []
    for block in re.findall(r"<item>(.*?)</item>", xml or "", re.S):
        def tag(name):
            m = re.search(r"<%s[^>]*>(.*?)</%s>" % (name, name), block, re.S)
            return _unescape(m.group(1)) if m else ""
        src = re.search(r'<source url="([^"]*)"[^>]*>(.*?)</source>', block, re.S)
        publisher = _unescape(src.group(2)) if src else ""
        title = tag("title")
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        try:
            day = email.utils.parsedate_to_datetime(tag("pubDate")).strftime("%Y-%m-%d")
        except (TypeError, ValueError, IndexError):
            day = None
        out.append({"title": title.strip()[:300], "publisher": publisher[:120],
                    "publisher_site": src.group(1) if src else "", "date": day,
                    "link": tag("link")})
    return out


def _unescape(text):
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    return " ".join(html.unescape(text).split())


def _story_key(title):
    return hashlib.sha256(fold(title).encode()).hexdigest()[:16]


def read_news(ctx):
    entity, get, breaker = ctx["entity"], ctx["get"], ctx["news_breaker"]
    country = entity.get("country") or ctx.get("client_country") or "US"
    name = clean_name(entity.get("name"), entity["domain"])
    english = EDITIONS.get(country.upper(), EDITIONS["US"])[0].startswith("en")
    queries = ['"%s" when:%dd' % (name, DAYS)]
    stories, failures, cut = {}, [], False
    for i in range(2):
        if i >= len(queries):
            break
        if breaker.open:
            failures.append("Google News stopped answering earlier in this collection")
            break
        breaker.wait_turn()
        read = get(feed_url(queries[i], country), limit=3_000_000, timeout=12)
        ok = read["status"] == "ok" and "<rss" in read["body"][:500]
        breaker.record(ok)
        if not ok:
            failures.append("query %d: %s" % (i + 1, read["note"] or read["status"]))
            continue
        items = parse_items(read["body"])
        for it in items:
            if not it["title"] or not names_company(it["title"], name, entity["domain"]):
                continue
            key = _story_key(it["title"])
            if key in stories:
                stories[key]["copies"] += 1
                continue
            stories[key] = dict(it, id=key, copies=1)
        if i == 0 and len(items) >= FULL:
            if english:
                queries.append('"%s" (%s) when:%dd' % (name, MOVES, DAYS))
            else:
                cut = True
    if failures and not stories and len(failures) >= len(queries):
        return {"status": "failed", "note": "; ".join(failures), "payload": None, "items": 0,
                "complete": False}
    items = sorted(stories.values(), key=lambda s: s.get("date") or "", reverse=True)
    complete = not failures and not cut
    edition = EDITIONS.get(country.upper(), EDITIONS["US"])[2]
    note = "%d articles naming %s in the last %d days (%s edition)" % (
        len(items), name, DAYS, edition)
    if cut:
        note += "; the feed was full, so older articles may be missing"
    if failures:
        note += "; " + "; ".join(failures)
    return {"status": "ok" if items else "empty", "note": note,
            "payload": {"name": name, "edition": edition, "items": items,
                        "complete": complete},
            "items": len(items), "complete": complete}


def new_articles(prev, cur):
    old = {i["id"] for i in (prev or {}).get("items") or []}
    return [i for i in (cur or {}).get("items") or [] if i["id"] not in old]
